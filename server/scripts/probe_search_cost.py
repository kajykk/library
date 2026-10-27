"""R8 搜索成本探针（只读）。

复现 docs/roadmap/R8-chinese-search.md 第 1 节的实测依据，并在 R8a 改动后重测同一批查询。

    # 库副本（推荐，来自 R7 spike 的字节副本）
    server/.venv/Scripts/python.exe server/scripts/probe_search_cost.py \
        --db server/scripts/spike-out/spike.db --mode baseline
    server/.venv/Scripts/python.exe server/scripts/probe_search_cost.py \
        --db server/scripts/spike-out/spike.db --mode after

全程 `mode=ro&immutable=1`，不建表、不建触发器、不改 journal mode。
连真实库必须显式加 `--i-know-this-is-the-real-db`，否则拒绝。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
REAL_DB = (SERVER / "knowledge_base.db").resolve()
LIMIT = 20
META = ("title", "author", "description")

# (qid, 用户真实输入形态的整句, 手工空格切好的关键词——R7 spike 用的同一批)
SAMPLES = [
    ("q01", "弗洛伊德如何解释文明与本能的冲突？", ["弗洛伊德", "文明", "本能"]),
    ("q02", "精神分析如何理解创伤？", ["精神分析", "创伤"]),
    ("q03", "权力为什么会让人腐败？", ["权力", "腐败"]),
    ("q04", "民粹主义为什么在民主国家兴起？", ["民粹", "民主"]),
    ("q05", "第一次世界大战为什么会爆发？", ["第一次世界大战", "战争", "刺杀"]),
    ("q06", "人工智能会取代哪些工作？", ["人工智能", "就业", "自动化"]),
    ("q07", "机器学习模型如何避免过拟合？", ["过拟合", "泛化", "正则"]),
    ("q08", "数字时代为什么让人更孤独？", ["孤独", "社交媒体"]),
    ("q09", "资本主义与民主之间有什么张力？", ["资本主义", "民主"]),
    ("q10", "六爻纳甲如何起卦和断卦？", ["六爻", "纳甲", "起卦"]),
    ("q11", "国家权力的合法性来自哪里？", ["合法性", "国家", "权力"]),
    ("q12", "大模型对齐为什么需要 RLHF？", ["RLHF", "对齐", "人类反馈"]),
    ("q13", "What is the hard problem of consciousness?",
     ["hard problem", "consciousness", "qualia"]),
    ("q14", "Why do introverts need quiet time?", ["introvert", "quiet", "stimulation"]),
    ("q15", "什么是政治哲学？", ["政治哲学", "政治", "哲学"]),
]


def connect_ro(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro&immutable=1", uri=True
    )


def escape_term(t: str) -> str:
    return t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def quote(t: str) -> str:
    return '"' + t.replace('"', '""') + '"'


def timed(fn, runs: int):
    ts, n = [], 0
    for _ in range(runs):
        t0 = time.perf_counter()
        n = fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return round(statistics.median(ts), 1), n


# ---------- 基线侧：现状 SQL 形状的三种写法与两种 FTS 连接词 ----------

def lower_like(db, terms, cols, order=True):
    """现状：SQLAlchemy ilike() 在 SQLite 展开成 lower(col) LIKE lower(?)，
    并在 ORDER BY CASE 里把三个 LIKE 表达式再求值一遍。"""
    ors, params, case, case_params = [], [], [], []
    for t in terms:
        pat = f"%{escape_term(t)}%"
        ors.append("(" + " OR ".join(f"lower(d.{c}) LIKE lower(?) ESCAPE '\\'" for c in cols) + ")")
        params += [pat] * len(cols)
    if order:
        for c in META:
            for t in terms:
                case.append(f"WHEN lower(d.{c}) LIKE lower(?) ESCAPE '\\' THEN 0")
                case_params.append(f"%{escape_term(t)}%")
    sql = (f"SELECT d.id FROM documents d WHERE d.deleted_at IS NULL AND ({' OR '.join(ors)})"
           + (f" ORDER BY CASE {' '.join(case)} ELSE 3 END, d.updated_at DESC" if order else "")
           + f" LIMIT {LIMIT}")
    return timed(lambda: len(db.execute(sql, [*params, *case_params]).fetchall()), 3)


def like(db, terms, cols, order=True):
    """大小写敏感 LIKE：CJK 词与 lower() 版同结果。"""
    ors, params, case, case_params = [], [], [], []
    for t in terms:
        pat = f"%{escape_term(t)}%"
        ors.append("(" + " OR ".join(f"d.{c} LIKE ? ESCAPE '\\'" for c in cols) + ")")
        params += [pat] * len(cols)
    if order:
        for c in META:
            for t in terms:
                case.append(f"WHEN d.{c} LIKE ? ESCAPE '\\' THEN 0")
                case_params.append(f"%{escape_term(t)}%")
    sql = (f"SELECT d.id FROM documents d WHERE d.deleted_at IS NULL AND ({' OR '.join(ors)})"
           + (f" ORDER BY CASE {' '.join(case)} ELSE 3 END, d.updated_at DESC" if order else "")
           + f" LIMIT {LIMIT}")
    return timed(lambda: len(db.execute(sql, [*params, *case_params]).fetchall()), 3)


def like_total(db, terms, cols):
    ors, params = [], []
    for t in terms:
        pat = f"%{escape_term(t)}%"
        ors.append("(" + " OR ".join(f"d.{c} LIKE ? ESCAPE '\\'" for c in cols) + ")")
        params += [pat] * len(cols)
    sql = f"SELECT d.id FROM documents d WHERE d.deleted_at IS NULL AND ({' OR '.join(ors)})"
    return len(db.execute(sql, params).fetchall())


def fts(db, terms, op):
    """整句短语 / 多词 AND / 多词 OR 三种 MATCH 形态的耗时与命中数。"""
    match = f" {op} ".join(quote(t) for t in terms)
    sql = ("SELECT d.id, bm25(documents_fts, 0.0, 5.0, 1.0, 1.0, 1.0) sc"
           " FROM documents_fts f JOIN documents d ON d.id = f.doc_id"
           " WHERE documents_fts MATCH ? AND d.deleted_at IS NULL"
           f" ORDER BY sc LIMIT {LIMIT}")
    return timed(lambda: len(db.execute(sql, (match,)).fetchall()), 3)


def fts_prefilter(db, terms):
    """≥3 字词先 FTS 取候选，短词只在候选集内 LIKE 精筛。"""
    longs = [t for t in terms if len(t) >= 3]
    shorts = [t for t in terms if len(t) < 3]
    if not longs:
        return None

    def once():
        match = " AND ".join(quote(t) for t in longs)
        cand = [r[0] for r in db.execute(
            "SELECT d.id FROM documents_fts f JOIN documents d ON d.id = f.doc_id"
            " WHERE documents_fts MATCH ? AND d.deleted_at IS NULL", (match,)).fetchall()]
        if not shorts or not cand:
            return len(cand)
        ph = ",".join("?" * len(cand))
        ors, params = [], []
        for t in shorts:
            ors.append(f"(d.title LIKE ? ESCAPE '\\' OR d.content LIKE ? ESCAPE '\\')"
                       f" AND d.id IN ({ph})")
            params += [f"%{escape_term(t)}%", f"%{escape_term(t)}%", *cand]
        return len(db.execute(
            f"SELECT d.id FROM documents d WHERE d.deleted_at IS NULL AND ({' OR '.join(ors)})",
            params).fetchall())

    return timed(once, 3)


def run_baseline(db, runs):
    rows = []
    for qid, sentence, terms in SAMPLES:
        raw = [t for t in sentence.split() if t]
        whole = raw if len(raw) == 1 else []
        row = {"qid": qid, "query": sentence, "terms": terms}
        # E1：整句当一个 term 走 FTS 短语
        row["e1_whole_sentence_fts"] = fts(db, whole, "OR") if whole else None
        row["e1b_terms_fts_and"] = fts(db, terms, "AND")
        # E8：高频词 OR 的单次 MATCH
        row["e8_terms_fts_or"] = fts(db, terms, "OR")
        # E2/E3/E4：现状 lower() vs LIKE
        row["e2_current_lower_like"] = lower_like(db, terms, (*META, "content"), order=True)
        row["e3_like_no_lower"] = like(db, terms, (*META, "content"), order=True)
        row["e4_like_no_order"] = like(db, terms, (*META, "content"), order=False)
        # E5：列拆分成本
        row["e5_meta_only"] = like(db, terms, META, order=False)
        row["e5_content_only"] = like(db, terms, ("content",), order=False)
        # E7：FTS 候选精筛
        row["e7_fts_prefilter"] = fts_prefilter(db, terms)
        # E10：覆盖差（不限量，看真实命中分布）
        row["e10_total"] = {"all_cols": like_total(db, terms, (*META, "content")),
                            "meta_only": like_total(db, terms, META)}
        rows.append(row)
    return rows


# ---------- 改动后：直接调应用层函数，量同一批查询 ----------

def run_after(db_path: Path, runs: int):
    import os

    os.environ["KB_DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["KB_AUTO_CREATE_TABLES"] = "0"
    sys.path.insert(0, str(SERVER))
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.services import search_index as si
    from app.routers.search import search as search_endpoint
    from app.routers.search import search_summary as summary_endpoint

    try:
        from app.services.query_terms import build_query_plan
    except ImportError:
        build_query_plan = None

    # 只读副本上不能建表/删触发器，直接标记 FTS 可用（表与数据都在副本里）
    si._available = True
    engine = create_engine(
        f"sqlite:///file:{db_path.as_posix()}?mode=ro&immutable=1&uri=true")
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = Session()

    rows = []
    for qid, sentence, _curated in SAMPLES:
        plan = build_query_plan(sentence).terms if build_query_plan else None

        def call_search():
            hits = search_endpoint(q=sentence, type=None, limit=LIMIT, db=db)
            return len(hits), [hit.title for hit in hits[:5]]

        def call_summary():
            return summary_endpoint(q=sentence, type=None, db=db).total

        s_ms, (s_hits, s_top) = timed(call_search, runs)
        sum_ms, sum_total = timed(call_summary, runs)
        rows.append({"qid": qid, "query": sentence, "plan_terms": list(plan) if plan else None,
                     "search_ms": s_ms, "search_hits": s_hits, "search_top_titles": s_top,
                     "summary_ms": sum_ms, "summary_total": sum_total})
    db.close()
    engine.dispose()
    return rows


def summarize(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return None
    vals = sorted(vals)
    p95 = vals[min(len(vals) - 1, int(round(0.95 * len(vals) + 0.4999)) - 1)]
    return {"n": len(vals), "median": round(statistics.median(vals), 1), "p95": p95,
            "max": max(vals)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--mode", choices=["baseline", "after"], default="baseline")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--i-know-this-is-the-real-db", action="store_true")
    args = ap.parse_args()

    db_path = args.db.resolve()
    if db_path == REAL_DB and not args.i_know_this_is_the_real_db:
        print(f"拒绝：{db_path} 是真实库。只读探针仍可能改变 WAL/文件时间，"
              f"要连它请显式加 --i-know-this-is-the-real-db", file=sys.stderr)
        return 2
    if not db_path.exists():
        print(f"库不存在：{db_path}", file=sys.stderr)
        return 2

    if args.mode == "baseline":
        db = connect_ro(db_path)
        rows = run_baseline(db, args.runs)
        db.close()
    else:
        rows = run_after(db_path, args.runs)

    print(f"db={db_path.name} mode={args.mode} runs={args.runs}")
    for r in rows:
        print(json.dumps(r, ensure_ascii=False))
    print("summary = " + json.dumps({
        "search_ms": summarize(rows, "search_ms"),
        "summary_ms": summarize(rows, "summary_ms"),
        "empty_hits": [r["qid"] for r in rows if r.get("search_hits") == 0],
    }, ensure_ascii=False))

    if args.out:
        args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
