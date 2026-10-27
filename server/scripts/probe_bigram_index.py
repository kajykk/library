"""R8b 前置测量（RW-807）：在库副本旁边建一套 bigram（2 字滑窗）索引，量体积/重建/延迟/召回。

全程只读源库（`mode=ro&immutable=1`），写入只发生在本脚本新建的两个独立索引库里：

    E:/zy/python/python.exe server/scripts/probe_bigram_index.py \\
        --db server/scripts/spike-out/spike.db \\
        --work-dir server/scripts/spike-out/bigram \\
        --out server/scripts/spike-out/bigram/measure.json

产出对照表（现状 trigram vs bigram 副本）：① 索引体积 ② 全库重建耗时与峰值内存
③ 2 字词查询 P50/P95（与现状 LIKE 全表扫描同题对比）④ ≥3 字短语召回正确性
（以源库 LIKE 为真值）。

连真实库必须显式加 `--i-know-this-is-the-real-db`，否则拒绝。两个索引库都用 `content=''`
（contentless：只存倒排不存正文），所以「文件字节 ≈ 索引字节」，两边口径一致可比。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import statistics
import sys
import threading
import time
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
REAL_DB = (SERVER / "knowledge_base.db").resolve()

_CJK = "㐀-䶿一-鿿豈-﫿"
_RUN_RE = re.compile(rf"[{_CJK}]+|[^\s{_CJK}]+")
_CJK_LEAD = re.compile(rf"[{_CJK}]")

# 2 字词：trigram 登记不了、只能全表 LIKE 的那一类
SHORT_TERMS = ["起卦", "断卦", "权力", "腐败", "本能", "冲突", "民主", "张力",
               "取代", "工作", "孤独", "创伤", "需要", "哲学"]
# ≥3 字短语：trigram 与 bigram 都能表达，用来验滑窗能否还原连续语义
LONG_TERMS = ["政治哲学", "机器学习", "弗洛伊德", "精神分析", "第一次世界大战", "人工智能"]

FTS_SPECS = {
    "bigram": ("documents_bigram_fts", "unicode61"),
    "trigram_ref": ("documents_trigram_fts", "trigram"),
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:16]


def connect_ro(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro&immutable=1", uri=True)


# ---------- 分词：CJK 走 2 字滑窗，其余保留原 token ----------

def bigram_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for run in _RUN_RE.findall(text or ""):
        if _CJK_LEAD.match(run[0]):
            if len(run) == 1:
                tokens.append(run)
            else:
                tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
        else:
            tokens.append(run)
    return tokens


def bigram_text(text: str) -> str:
    return " ".join(bigram_tokens(text))


def quote(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


def quote_phrase(text: str) -> str:
    """`机器学习模型` → `"机器 器学 学习 习模 模型"`（相邻 token 的短语查询）。"""
    return quote(" ".join(bigram_tokens(text)))


# ---------- 内存峰值采样（全进程 RSS，两次构建的读数会互相残留） ----------

class Sampler:
    def __init__(self):
        self.peak_kb = 0
        self._stop = threading.Event()
        try:
            import psutil
        except ImportError:
            self.proc = None
            return
        self.proc = psutil.Process()

    def start(self):
        if self.proc is None:
            return
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self._stop.is_set():
            self.peak_kb = max(self.peak_kb, self.proc.memory_info().rss // 1024)
            time.sleep(0.05)

    def stop(self) -> int | None:
        self._stop.set()
        return None if self.proc is None else self.peak_kb // 1024


# ---------- 建索引 ----------

def create_index_db(path: Path, table: str, tokenizer: str,
                    contentless: bool = True) -> sqlite3.Connection:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path.as_posix())
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = OFF")
    # contentless（content=''）只存倒排，体积小但 3.42 下不能删改；
    # contentless=False 是普通 fts5 表，正文再存一份，可支持逐行重建。
    tail = ", content=''" if contentless else ""
    conn.execute(
        f"CREATE VIRTUAL TABLE {table} USING fts5("
        f"title, author, description, content, tokenize = '{tokenizer}'{tail});"
    )
    conn.commit()
    return conn


def build(conn: sqlite3.Connection, table: str, source: sqlite3.Connection, expand):
    """全库重建。返回 (总秒, 展开秒, 行数, 展开后的正文字节)。"""
    started = time.perf_counter()
    expand_seconds = 0.0
    payload_bytes = 0
    rows = 0
    sql = (f"INSERT INTO {table}(rowid, title, author, description, content) "
           "VALUES (?, ?, ?, ?, ?)")
    batch: list[tuple] = []
    for rowid, title, author, description, content in source.execute(
        "SELECT rowid, title, author, description, content FROM documents "
        "WHERE deleted_at IS NULL ORDER BY rowid"
    ):
        t0 = time.perf_counter()
        parts = (expand(title or ""), expand(author or ""),
                 expand(description or ""), expand(content or ""))
        expand_seconds += time.perf_counter() - t0
        payload_bytes += sum(len(part.encode("utf-8")) for part in parts)
        batch.append((rowid, *parts))
        rows += 1
        if len(batch) >= 50:
            conn.executemany(sql, batch)
            batch.clear()
    if batch:
        conn.executemany(sql, batch)
    conn.commit()
    return (round(time.perf_counter() - started, 1), round(expand_seconds, 1),
            rows, payload_bytes)


def index_bytes(conn: sqlite3.Connection) -> int:
    """contentless 库里除文件头外全是索引：先把 WAL 落盘再按 page_size × page_count 计。"""
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    pages = conn.execute("PRAGMA page_count").fetchone()[0]
    return int(page_size) * int(pages)


# ---------- 查询测量 ----------

def timed(conn: sqlite3.Connection, table: str, match: str, runs: int):
    ts, ids = [], []
    sql = f"SELECT rowid FROM {table} WHERE {table} MATCH ? ORDER BY bm25({table})"
    for _ in range(runs):
        t0 = time.perf_counter()
        ids = [row[0] for row in conn.execute(sql, (match,))]
        ts.append((time.perf_counter() - t0) * 1000)
    return ts, len(ids)


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_ground_truth(source: sqlite3.Connection, term: str) -> int:
    pattern = f"%{escape_like(term)}%"
    row = source.execute(
        "SELECT count(*) FROM documents WHERE deleted_at IS NULL AND ("
        "title LIKE ? ESCAPE '\\' OR author LIKE ? ESCAPE '\\' "
        "OR description LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')",
        (pattern, pattern, pattern, pattern),
    ).fetchone()
    return int(row[0])


def like_scan_now(source: sqlite3.Connection, term: str, runs: int):
    """现状形状：2 字词只能带 LIMIT 的全表 LIKE 扫描（不排序，靠提前终止）。"""
    pattern = f"%{escape_like(term)}%"
    ts, rows = [], []
    for _ in range(runs):
        t0 = time.perf_counter()
        rows = source.execute(
            "SELECT id FROM documents WHERE deleted_at IS NULL AND ("
            "title LIKE ? ESCAPE '\\' OR author LIKE ? ESCAPE '\\' "
            "OR description LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\') LIMIT 20",
            (pattern, pattern, pattern, pattern)).fetchall()
        ts.append((time.perf_counter() - t0) * 1000)
    return ts, len(rows)


def stats(values: list[float]) -> dict:
    if not values:
        return {}
    ordered = sorted(values)
    p95 = ordered[max(0, min(len(ordered) - 1, int(round(0.95 * len(ordered))) - 1))]
    return {"p50": round(statistics.median(ordered), 2), "p95": round(p95, 2),
            "max": round(ordered[-1], 2)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, type=Path, help="只读源库（库副本，勿指真实库）")
    ap.add_argument("--work-dir", type=Path, required=True)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--skip-build", action="store_true", help="复用已建好的索引库，只测查询")
    ap.add_argument("--contentless", type=int, default=1, choices=(0, 1),
                    help="1 = content='' 只存倒排（默认，体积口径）；"
                         "0 = 普通 fts5 表（3.42 里唯一能逐行删改的形状，正文会再存一份）")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--i-know-this-is-the-real-db", action="store_true")
    args = ap.parse_args()

    db_path = args.db.resolve()
    if db_path == REAL_DB and not args.i_know_this_is_the_real_db:
        print(f"拒绝：{db_path} 是真实库；探针虽只读仍会改文件时间，请确认后再加参数", file=sys.stderr)
        return 2
    if not db_path.exists():
        print(f"库不存在：{db_path}", file=sys.stderr)
        return 2

    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    report: dict = {"db": db_path.name, "sha256_before": sha256(db_path),
                    "work_dir": str(work), "runs": args.runs,
                    "contentless": bool(args.contentless)}

    source = connect_ro(db_path)
    documents, content_chars = source.execute(
        "SELECT count(*), sum(length(coalesce(content,''))) FROM documents").fetchone()
    live = source.execute("SELECT count(*) FROM documents WHERE deleted_at IS NULL").fetchone()[0]
    report["corpus"] = {"documents": documents, "live_documents": live,
                        "content_chars": content_chars,
                        "source_file_bytes": index_bytes(source)}

    built: dict[str, dict] = {}
    conns: dict[str, sqlite3.Connection] = {}
    for kind, (table, tokenizer) in FTS_SPECS.items():
        path = work / (f"{kind}.db" if args.contentless else f"{kind}_full.db")
        if path.exists():
            conns[kind] = sqlite3.connect(path.as_posix())
            built[kind] = {"table": table, "reused": True,
                           "index_file_bytes": index_bytes(conns[kind])}
            continue
        if args.skip_build:
            built[kind] = {"table": table, "missing": True}
            continue
        conn = create_index_db(path, table, tokenizer, contentless=bool(args.contentless))
        expand = bigram_text if kind == "bigram" else (lambda t: t)
        sampler = Sampler()
        sampler.start()
        seconds, expand_s, rows, payload = build(conn, table, source, expand)
        built[kind] = {"table": table, "rebuild_seconds": seconds,
                       "expand_seconds": expand_s, "rows": rows,
                       "expanded_payload_bytes": payload,
                       "index_file_bytes": index_bytes(conn),
                       "peak_rss_mb_process": sampler.stop()}
        conns[kind] = conn
        print(json.dumps({"built": kind, **built[kind]}, ensure_ascii=False), file=sys.stderr)
    report["build"] = built

    queries: list[dict] = []
    for kind, conn in conns.items():
        table = FTS_SPECS[kind][0]
        for term in SHORT_TERMS + LONG_TERMS:
            match = quote_phrase(term) if kind == "bigram" else quote(term)
            ts, hits = timed(conn, table, match, args.runs)
            queries.append({"kind": kind, "term": term, "len": len(term),
                            "fts_ms": stats(ts), "fts_hits": hits})
    report["queries"] = queries

    truth = {term: like_ground_truth(source, term) for term in SHORT_TERMS + LONG_TERMS}
    report["like_ground_truth"] = truth
    hit_map = {(q["kind"], q["term"]): q["fts_hits"] for q in queries}
    report["recall_check"] = {
        kind: {term: {"fts_hits": hit_map[(kind, term)], "like_truth": truth[term],
                      "equal": hit_map[(kind, term)] == truth[term]}
               for term in SHORT_TERMS + LONG_TERMS if (kind, term) in hit_map}
        for kind in conns
    }

    current = []
    for term in SHORT_TERMS:
        ts, rows = like_scan_now(source, term, args.runs)
        current.append({"term": term, "like_scan_ms": stats(ts), "limit_hits": rows})
    report["current_like_scan"] = current

    big = {q["term"]: q["fts_ms"] for q in queries if q["kind"] == "bigram"}
    cur = {c["term"]: c["like_scan_ms"] for c in current}
    report["headline"] = {
        "short_terms": SHORT_TERMS,
        "bigram_p95_ms": {t: big[t]["p95"] for t in SHORT_TERMS if t in big},
        "current_like_p95_ms": {t: cur[t]["p95"] for t in SHORT_TERMS if t in cur},
        "bigram_p95_max_ms": max((big[t]["p95"] for t in SHORT_TERMS if t in big), default=None),
        "current_like_p95_max_ms": max((cur[t]["p95"] for t in SHORT_TERMS if t in cur), default=None),
        "index_bytes": {k: v.get("index_file_bytes") for k, v in built.items()},
        "all_short_recalled_exactly": all(
            report["recall_check"].get("bigram", {}).get(t, {}).get("equal", False)
            for t in SHORT_TERMS),
        "all_long_recalled_exactly": all(
            report["recall_check"].get("bigram", {}).get(t, {}).get("equal", False)
            for t in LONG_TERMS),
        "trigram_short_hits": {t: report["recall_check"].get("trigram_ref", {}).get(t, {})
                                                    .get("fts_hits") for t in SHORT_TERMS},
    }

    source.close()
    report["sha256_after"] = sha256(db_path)
    report["source_untouched"] = report["sha256_before"] == report["sha256_after"]

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"写入 {args.out}", file=sys.stderr)
    print(json.dumps(report["headline"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
