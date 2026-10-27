"""B5-01 语义检索 spike：只读真实库副本，独立于应用依赖。

用法（在仓库根目录，使用 .spike-venv）：
    .spike-venv/Scripts/python.exe server/scripts/spike_semantic.py pool
    .spike-venv/Scripts/python.exe server/scripts/spike_semantic.py score

`pool` 读 spike-out/spike.db + sample.json，切片、嵌入、检索并把待人工判定的
候选写入 pool.json / pool.md；`score` 读人工标注 judgments.json 输出最终指标。
全部产物落在 server/scripts/spike-out/（已 gitignore），不写 knowledge_base.db。
"""

from __future__ import annotations

import argparse
import ctypes
import gc
import hashlib
import json
import os
import re
import sqlite3
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import numpy as np  # noqa: E402
from fastembed import TextEmbedding  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "server" / "scripts" / "spike-out"
DB = OUT / "spike.db"
SAMPLE = OUT / "sample.json"
MODELS_DIR = OUT / "models"

CHUNK_SIZE = 800
OVERLAP = 120
MAX_CHUNKS = 200
MIN_DOC_CHARS = 200
TOP_K = 5
RRF_K = 60

MODELS = {
    "bge": {
        "name": "BAAI/bge-small-zh-v1.5",
        "snapshot_prefix": "models--Qdrant--bge-small-zh-v1.5",
        "token_limit": 512,
        "dim": 512,
        "license": "MIT",
        "card": "https://huggingface.co/BAAI/bge-small-zh-v1.5",
    },
    "multi": {
        "name": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "snapshot_prefix": "models--Qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q",
        "token_limit": 128,
        "dim": 384,
        "license": "Apache-2.0",
        "card": "https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    },
}

# 15 条查询，覆盖概念/人名/短词/英文（B5-01 第 6 项）。
QUERIES = [
    ("q01", "弗洛伊德如何解释文明与本能的冲突？", ["弗洛伊德", "文明", "本能"], "概念+人名"),
    ("q02", "精神分析如何理解创伤？", ["精神分析", "创伤"], "概念"),
    ("q03", "权力为什么会让人腐败？", ["权力", "腐败"], "概念"),
    ("q04", "民粹主义为什么在民主国家兴起？", ["民粹", "民主"], "概念"),
    ("q05", "第一次世界大战为什么会爆发？", ["第一次世界大战", "战争", "刺杀"], "事件"),
    ("q06", "人工智能会取代哪些工作？", ["人工智能", "就业", "自动化"], "概念"),
    ("q07", "机器学习模型如何避免过拟合？", ["过拟合", "泛化", "正则"], "概念"),
    ("q08", "数字时代为什么让人更孤独？", ["孤独", "社交媒体"], "概念"),
    ("q09", "资本主义与民主之间有什么张力？", ["资本主义", "民主"], "概念"),
    ("q10", "六爻纳甲如何起卦和断卦？", ["六爻", "纳甲", "起卦"], "短词/术语"),
    ("q11", "国家权力的合法性来自哪里？", ["合法性", "国家", "权力"], "概念"),
    ("q12", "大模型对齐为什么需要 RLHF？", ["RLHF", "对齐", "人类反馈"], "短词/术语"),
    ("q13", "What is the hard problem of consciousness?", ["hard problem", "consciousness", "qualia"], "英文"),
    ("q14", "Why do introverts need quiet time?", ["introvert", "quiet", "stimulation"], "英文"),
    ("q15", "什么是政治哲学？", ["政治哲学", "政治", "哲学"], "短词"),
]

PUNCT = "。！？；.!?;"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def normalize_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u00a0\u3000]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(text: str) -> list[str]:
    """800 字窗口 + 120 字重叠；窗口尾部 200 字内优先在标点处收口。"""
    text = normalize_text(text)
    if len(text) < MIN_DOC_CHARS:
        return []
    chunks: list[str] = []
    start, n = 0, len(text)
    while start < n and len(chunks) < MAX_CHUNKS:
        end = min(start + CHUNK_SIZE, n)
        if end < n:
            window = text[start:end]
            cut = max(window.rfind(ch) for ch in PUNCT)
            if cut >= CHUNK_SIZE - 200:
                end = start + cut + 1
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        start = max(end - OVERLAP, start + 1)
    return chunks


def make_snippet(text: str, terms: list[str], width: int = 160) -> str:
    plain = re.sub(r"\s+", " ", text or "").strip()
    if not plain:
        return ""
    lowered = plain.lower()
    idx = -1
    for term in terms:
        pos = lowered.find(term.lower())
        if pos >= 0 and (idx < 0 or pos < idx):
            idx = pos
    if idx < 0:
        return plain[:width] + ("…" if len(plain) > width else "")
    start = max(0, idx - width // 3)
    end = min(len(plain), idx + len(terms[0]) + width)
    return ("…" if start else "") + plain[start:end] + ("…" if end < len(plain) else "")


def rss_mb() -> tuple[float, float]:
    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(Counters),
        ctypes.c_ulong,
    ]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    if not psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise OSError("GetProcessMemoryInfo 失败")
    return counters.WorkingSetSize / 1048576, counters.PeakWorkingSetSize / 1048576


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def snapshot_dir(prefix: str) -> Path:
    snaps = sorted((MODELS_DIR / prefix / "snapshots").glob("*"))
    if not snaps:
        raise SystemExit(f"模型缓存不存在: {prefix}")
    return snaps[-1]


def model_meta() -> dict:
    meta = {}
    for key, spec in MODELS.items():
        snap = snapshot_dir(spec["snapshot_prefix"])
        files = [p for p in snap.rglob("*") if p.is_file()]
        onnx = snap / "model_optimized.onnx"
        meta[key] = {
            **{k: spec[k] for k in ("name", "dim", "license", "card", "token_limit")},
            "snapshot": str(snap),
            "weights_bytes": sum(p.stat().st_size for p in files),
            "onnx_bytes": onnx.stat().st_size,
            "onnx_sha256": sha256_file(onnx),
            "files": {p.name: p.stat().st_size for p in files},
        }
    return meta


def load_sample_docs() -> list[dict]:
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    ids = [s["id"] for s in sample]
    placeholders = ",".join("?" * len(ids))
    db = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro&immutable=1", uri=True)
    rows = db.execute(
        f"SELECT id, title, author, format, language, content FROM documents "
        f"WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    db.close()
    by_id = {r[0]: r for r in rows}
    docs = []
    for item in sample:
        row = by_id.get(item["id"])
        if not row:
            continue
        docs.append(
            {
                "id": row[0],
                "title": row[1] or "",
                "author": row[2] or "",
                "format": (row[3] or "").lower(),
                "language": row[4] or "",
                "content": row[5] or "",
            }
        )
    return docs


def full_library_estimate() -> dict:
    """只读遍历全库，按 800/120/200 规则估算 chunk 下界（不必真正切分 139M 字符）。"""
    log("按窗口估算全库 chunk 数（只读扫描 139M 字符）…")
    db = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro&immutable=1", uri=True)
    total_docs = total_chars = total_chunks = skipped = capped = 0
    rows = db.execute(
        "SELECT format, content FROM documents "
        "WHERE deleted_at IS NULL AND length(coalesce(content,'')) > 0"
    )
    for fmt, content in rows:
        length = len(content or "")
        if length < MIN_DOC_CHARS:
            skipped += 1
            continue
        n = min((length + CHUNK_SIZE - OVERLAP - 1) // (CHUNK_SIZE - OVERLAP), MAX_CHUNKS)
        # 标点收口会略增 chunk 数；用真实切分函数在超长文档上抽样校准会太慢，
        # 这里按窗口下界估算并在报告中注明误差方向。
        total_docs += 1
        total_chars += length
        total_chunks += n
        if n >= MAX_CHUNKS:
            capped += 1
    db.close()
    return {
        "alive_with_content": total_docs,
        "skipped_short": skipped,
        "total_chars": total_chars,
        "estimated_chunks_lower_bound": total_chunks,
        "docs_capped_at_200": capped,
        "method": "ceil(len/680) capped at 200（标点收口只会增加，估算为下界）",
    }


def term_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _escape_fts(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def fts_search(db: sqlite3.Connection, query: str, keywords: list[str], mode: str,
               sample_ids: list[str] | None, limit: int = TOP_K) -> list[dict]:
    """mode=full 复刻 /api/search 的现状行为；mode=kw 是关键词重写后的最好情况。

    sample_ids=None 时查询全库（用于测真实 FTS 延迟）；否则只保留样本内文档。
    """
    terms = query.split() if mode == "full" else keywords
    sample_set = set(sample_ids) if sample_ids else None
    restrict = ""
    restrict_params: list = []
    if sample_ids:
        restrict = " AND d.id IN (" + ",".join("?" * len(sample_ids)) + ")"
        restrict_params = list(sample_ids)
    short = any(0 < len(t) < 3 for t in terms)
    use_fts = not short and len(query) >= 3
    if mode == "full":
        match = _escape_fts(query)
    else:
        match = " OR ".join(_escape_fts(t) for t in terms)
    fetch = limit if sample_set else limit
    if sample_set:
        fetch = max(limit * 8, 40)

    if use_fts:
        sql = f"""
            SELECT d.id, d.title, d.content, d.description
            FROM documents_fts f JOIN documents d ON d.id = f.doc_id
            WHERE documents_fts MATCH ? AND d.deleted_at IS NULL
              {restrict}
            ORDER BY bm25(documents_fts, 0.0, 5.0, 1.0, 1.0, 1.0)
            LIMIT ?
        """
        try:
            rows = db.execute(sql, [match, *restrict_params, fetch]).fetchall()
        except sqlite3.OperationalError:
            rows = []
    else:
        # 复刻 search.py 的 ILIKE 兜底：任一词命中任一字段即可，标题>作者>简介>正文。
        ors, params = [], []
        for term in terms:
            pat = f"%{term_like(term)}%"
            ors.append(
                "(d.title LIKE ? ESCAPE '\\' OR d.author LIKE ? ESCAPE '\\' "
                "OR d.description LIKE ? ESCAPE '\\' OR d.content LIKE ? ESCAPE '\\')"
            )
            params.extend([pat] * 4)
        title_case, title_params = [], []
        for term in terms:
            title_case.append("WHEN d.title LIKE ? ESCAPE '\\' THEN 0")
            title_params.append(f"%{term_like(term)}%")
        for term in terms:
            title_case.append("WHEN d.author LIKE ? ESCAPE '\\' THEN 1")
            title_params.append(f"%{term_like(term)}%")
        for term in terms:
            title_case.append("WHEN d.description LIKE ? ESCAPE '\\' THEN 2")
            title_params.append(f"%{term_like(term)}%")
        sql = f"""
            SELECT d.id, d.title, d.content, d.description
            FROM documents d
            WHERE d.deleted_at IS NULL {restrict}
              AND ({' OR '.join(ors)})
            ORDER BY CASE {' '.join(title_case)} ELSE 3 END, d.updated_at DESC
            LIMIT ?
        """
        rows = db.execute(sql, [*restrict_params, *params, *title_params, fetch]).fetchall()

    if sample_set:
        rows = [r for r in rows if r[0] in sample_set][:limit]
    out = []
    for doc_id, title, content, description in rows:
        body = description or ""
        for term in terms:
            if term.lower() in (content or "").lower():
                body = content or ""
                break
        out.append(
            {
                "doc_id": doc_id,
                "title": title or "",
                "snippet": make_snippet(body, terms),
            }
        )
    return out


def embed_model(key: str, chunks: list[dict], threads: int, batch_size: int) -> tuple[TextEmbedding, np.ndarray, dict]:
    spec = MODELS[key]
    log(f"加载 {spec['name']} …")
    t0 = time.perf_counter()
    model = TextEmbedding(
        model_name=spec["name"],
        cache_dir=str(MODELS_DIR),
        threads=threads,
        providers=["CPUExecutionProvider"],
    )
    load_s = time.perf_counter() - t0
    rss_loaded, _ = rss_mb()
    texts = [c["text"] for c in chunks]
    log(f"嵌入 {len(texts):,} 个 chunk（{spec['name']}，batch={batch_size}）…")
    t1 = time.perf_counter()
    vectors = list(model.passage_embed(texts, batch_size=batch_size))
    embed_s = time.perf_counter() - t1
    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    np.divide(matrix, np.maximum(norms, 1e-12), out=matrix)
    rss_now, peak = rss_mb()
    stats = {
        "load_s": round(load_s, 2),
        "embed_s": round(embed_s, 2),
        "batch_size": batch_size,
        "chunks_per_s": round(len(texts) / embed_s, 1) if embed_s else None,
        "chars_per_s": round(sum(len(t) for t in texts) / embed_s, 1) if embed_s else None,
        "matrix_bytes": int(matrix.nbytes),
        "rss_after_load_mb": round(rss_loaded, 1),
        "rss_after_embed_mb": round(rss_now, 1),
        "peak_rss_mb": round(peak, 1),
    }
    log(f"完成：{stats['embed_s']}s，{stats['chunks_per_s']} chunk/s，RSS {stats['rss_after_embed_mb']} MB")
    return model, matrix, stats


def token_truncation(chunks: list[dict], key: str) -> dict:
    from tokenizers import Tokenizer

    snap = snapshot_dir(MODELS[key]["snapshot_prefix"])
    tok = Tokenizer.from_file(str(snap / "tokenizer.json"))
    limit = MODELS[key]["token_limit"]
    lengths = [len(tok.encode(c["text"]).ids) for c in chunks]
    over = sum(1 for n in lengths if n > limit)
    return {
        "token_limit": limit,
        "chunks": len(lengths),
        "avg_tokens": round(statistics.mean(lengths), 1),
        "max_tokens": max(lengths),
        "over_limit": over,
        "over_limit_pct": round(100 * over / len(lengths), 1) if lengths else 0,
    }


def docs_with_chunks(chunks: list[dict]) -> list[str]:
    seen, out = set(), []
    for chunk in chunks:
        if chunk["doc_id"] not in seen:
            seen.add(chunk["doc_id"])
            out.append(chunk["doc_id"])
    return out


def rrf(rankings: list[list[str]], k: int = RRF_K) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return [d for d, _ in sorted(scores.items(), key=lambda x: (-x[1], x[0]))]


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((p / 100) * (len(ordered) - 1)))))
    return ordered[idx]


def phase_pool(args: argparse.Namespace) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    meta = model_meta()
    sample = load_sample_docs()
    if args.limit_docs:
        sample = sample[: args.limit_docs]
    queries = QUERIES[: args.limit_queries] if args.limit_queries else QUERIES
    log(f"样本 {len(sample)} 本，正文 {sum(len(d['content']) for d in sample):,} 字符")

    chunks: list[dict] = []
    per_doc = []
    for doc in sample:
        pieces = chunk_text(doc["content"])
        capped = len(pieces) >= MAX_CHUNKS
        per_doc.append(
            {
                "id": doc["id"],
                "title": doc["title"],
                "format": doc["format"],
                "chars": len(doc["content"]),
                "chunks": len(pieces),
                "capped": capped,
            }
        )
        for idx, text in enumerate(pieces):
            chunks.append(
                {
                    "uid": f"{doc['id']}:{idx}",
                    "doc_id": doc["id"],
                    "title": doc["title"],
                    "format": doc["format"],
                    "idx": idx,
                    "text": text,
                }
            )

    pdf_docs = [d for d in per_doc if d["format"] == "pdf"]
    chunk_stats = {
        "docs": len(per_doc),
        "docs_skipped_short": sum(1 for d in sample if len(d["content"]) < MIN_DOC_CHARS),
        "chunks": len(chunks),
        "avg_chunks_per_doc": round(statistics.mean([d["chunks"] for d in per_doc]), 1),
        "median_chunks_per_doc": statistics.median([d["chunks"] for d in per_doc]),
        "max_chunks_per_doc": max(d["chunks"] for d in per_doc),
        "docs_capped": sum(1 for d in per_doc if d["capped"]),
        "avg_chunk_chars": round(statistics.mean([len(c["text"]) for c in chunks]), 1),
        "max_chunk_chars": max(len(c["text"]) for c in chunks),
        "pdf": {
            "docs": len(pdf_docs),
            "avg_chunks": round(statistics.mean([d["chunks"] for d in pdf_docs]), 1) if pdf_docs else 0,
            "max_chunks": max((d["chunks"] for d in pdf_docs), default=0),
            "capped": sum(1 for d in pdf_docs if d["capped"]),
        },
        "per_doc": per_doc,
    }
    log(f"切出 {len(chunks):,} chunks，平均 {chunk_stats['avg_chunks_per_doc']} / 本")

    full = full_library_estimate() if not args.limit_docs else {
        "estimated_chunks_lower_bound": len(chunks),
        "total_chars": sum(len(d["content"]) for d in sample),
        "method": "limit-docs 冒烟模式，不做全库估算",
    }
    scale_chunks = full["estimated_chunks_lower_bound"] / max(1, len(chunks))
    scale_chars = full["total_chars"] / max(1, sum(len(d["content"]) for d in sample))
    log(f"全库估算 {full['estimated_chunks_lower_bound']:,} chunks，放大 {scale_chunks:.1f}×")

    db = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro&immutable=1", uri=True)
    sample_ids = [d["id"] for d in sample]
    doc_lookup = {d["id"]: d for d in sample}
    results: dict[str, dict] = {}
    latency: dict[str, dict] = {}
    for qid, query, keywords, kind in queries:
        results[qid] = {
            "query": query,
            "keywords": keywords,
            "kind": kind,
            "fts_full": fts_search(db, query, keywords, "full", sample_ids),
            "fts_kw": fts_search(db, query, keywords, "kw", sample_ids),
            "sem": {},
            "hybrid": {},
            "sem_docs": {},
        }

    baseline_rss, _ = rss_mb()
    for key in MODELS:
        log(f"=== 模型 {key} ===")
        rss_before, _ = rss_mb()
        model, matrix, embed_stats = embed_model(key, chunks, args.threads, args.batch_size)
        embed_stats["rss_before_load_mb"] = round(rss_before, 1)
        embed_stats["rss_delta_mb"] = round(embed_stats["rss_after_embed_mb"] - rss_before, 1)
        stats = {
            **embed_stats,
            "token_truncation": token_truncation(chunks, key),
            "index_total_s": round(embed_stats["load_s"] + embed_stats["embed_s"], 2),
        }

        for qid, query, keywords, kind in queries:
            entry = results[qid]
            qvec = np.asarray(list(model.query_embed([query]))[0], dtype=np.float32)
            qvec /= max(float(np.linalg.norm(qvec)), 1e-12)
            scores = matrix @ qvec
            top = np.argpartition(-scores, TOP_K)[:TOP_K]
            top = top[np.argsort(-scores[top])]
            sem_hits = []
            for rank, idx in enumerate(top, start=1):
                chunk = chunks[int(idx)]
                sem_hits.append(
                    {
                        "rank": rank,
                        "uid": chunk["uid"],
                        "doc_id": chunk["doc_id"],
                        "title": chunk["title"],
                        "idx": chunk["idx"],
                        "score": round(float(scores[idx]), 4),
                        "snippet": make_snippet(chunk["text"], keywords, width=180),
                    }
                )
            sem_doc_rank = docs_with_chunks(
                [chunks[int(i)] for i in np.argsort(-scores)]
            )
            fused_docs = rrf(
                [[d["doc_id"] for d in entry["fts_kw"]], sem_doc_rank[:TOP_K]]
            )
            chunk_lookup: dict[str, dict] = {}
            for i in np.argsort(-scores):
                chunk_lookup.setdefault(chunks[int(i)]["doc_id"], chunks[int(i)])
            hybrid = []
            for rank, doc_id in enumerate(fused_docs[:TOP_K], start=1):
                doc = doc_lookup[doc_id]
                best = chunk_lookup.get(doc_id)
                hybrid.append(
                    {
                        "rank": rank,
                        "doc_id": doc_id,
                        "title": doc["title"],
                        "snippet": make_snippet(best["text"] if best else doc["content"], keywords),
                    }
                )
            entry["sem"][key] = sem_hits
            entry["hybrid"][key] = hybrid
            entry["sem_docs"][key] = sem_doc_rank[:TOP_K]

            # 延迟：query 嵌入 + 点积；hybrid 再加 FTS + RRF。
            sem_times, hyb_times = [], []
            for _ in range(3):
                list(model.query_embed([query]))
            for _ in range(20):
                t0 = time.perf_counter()
                qv = np.asarray(list(model.query_embed([query]))[0], dtype=np.float32)
                qv /= max(float(np.linalg.norm(qv)), 1e-12)
                s = matrix @ qv
                np.argpartition(-s, TOP_K)[:TOP_K]
                sem_times.append((time.perf_counter() - t0) * 1000)
            for _ in range(10):
                t0 = time.perf_counter()
                fts_search(db, query, keywords, "kw", None)
                qv = np.asarray(list(model.query_embed([query]))[0], dtype=np.float32)
                qv /= max(float(np.linalg.norm(qv)), 1e-12)
                s = matrix @ qv
                idx = np.argpartition(-s, TOP_K)[:TOP_K]
                sem_rank = docs_with_chunks([chunks[int(i)] for i in idx])
                rrf([[d["doc_id"] for d in entry["fts_kw"]], sem_rank])
                hyb_times.append((time.perf_counter() - t0) * 1000)
            latency.setdefault(key, {})[qid] = {
                "sem_p50_ms": round(statistics.median(sem_times), 2),
                "sem_p95_ms": round(percentile(sem_times, 95), 2),
                "hybrid_p50_ms": round(statistics.median(hyb_times), 2),
                "hybrid_p95_ms": round(percentile(hyb_times, 95), 2),
            }

        rss_now, peak = rss_mb()
        stats["retrieval_rss_mb"] = round(rss_now, 1)
        stats["retrieval_peak_rss_mb"] = round(peak, 1)
        stats["projected_index_s"] = round(
            stats["embed_s"] * scale_chars + stats["load_s"], 1
        )
        stats["projected_vectors_bytes"] = int(matrix.nbytes * scale_chunks)
        results.setdefault("_stats", {})[key] = stats
        del model, matrix
        gc.collect()
        log(f"模型 {key} 完成，释放矩阵")

    db.close()

    payload = {
        "meta": {
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "db": str(DB),
            "sample": str(SAMPLE),
            "sample_docs": len(sample),
            "sample_chars": sum(len(d["content"]) for d in sample),
            "chunk_size": CHUNK_SIZE,
            "overlap": OVERLAP,
            "max_chunks": MAX_CHUNKS,
            "min_doc_chars": MIN_DOC_CHARS,
            "top_k": TOP_K,
            "rrf_k": RRF_K,
            "threads": args.threads,
            "cpu_count": os.cpu_count(),
            "baseline_rss_mb": round(baseline_rss, 1),
            "privacy": "HF_HUB_OFFLINE=1；模型已缓存，运行期无出站请求",
        },
        "models": meta,
        "chunking": chunk_stats,
        "full_library_estimate": full,
        "scales": {"chunks": round(scale_chunks, 2), "chars": round(scale_chars, 2)},
        "results": {qid: r for qid, r in results.items() if qid != "_stats"},
        "stats": results["_stats"],
        "latency": latency,
    }
    (OUT / "retrieval.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_pool_md(payload)
    log(f"写入 {OUT / 'retrieval.json'} 与 {OUT / 'pool.md'}")


def write_pool_md(payload: dict) -> None:
    lines = ["# B5-01 待人工判定候选池", ""]
    lines.append("判定规则：直接讨论查询主题=1；仅关键词命中/泛泛提及=0。")
    lines.append("semantic 判定到 chunk，FTS/hybrid 判定到文档。")
    lines.append("")
    for qid, query, _keywords, kind in QUERIES:
        if qid not in payload["results"]:
            continue
        entry = payload["results"][qid]
        lines.append(f"## {qid} {query}（{kind}）")
        lines.append("")
        lines.append("### FTS full（现状 API：整句精确短语）")
        if entry["fts_full"]:
            for hit in entry["fts_full"]:
                lines.append(f"- [doc] {hit['doc_id']} {hit['title'][:50]} — {hit['snippet']}")
        else:
            lines.append("- （无结果）")
        lines.append("")
        lines.append("### FTS kw（关键词 OR，最好情况）")
        if entry["fts_kw"]:
            for hit in entry["fts_kw"]:
                lines.append(f"- [doc] {hit['doc_id']} {hit['title'][:50]} — {hit['snippet']}")
        else:
            lines.append("- （无结果）")
        lines.append("")
        for key in MODELS:
            lines.append(f"### semantic {key} top-5")
            for hit in entry["sem"].get(key, []):
                lines.append(
                    f"- [chunk] {hit['uid']} 《{hit['title'][:36]}》 {hit['snippet']}"
                )
            lines.append("")
        for key in MODELS:
            lines.append(f"### hybrid {key} top-5（文档级）")
            for hit in entry["hybrid"].get(key, []):
                lines.append(f"- [doc] {hit['doc_id']} 《{hit['title'][:50]}》 {hit['snippet']}")
            lines.append("")
    (OUT / "pool.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def phase_score(args: argparse.Namespace) -> None:
    payload = json.loads((OUT / "retrieval.json").read_text(encoding="utf-8"))
    judgments = json.loads((OUT / "judgments.json").read_text(encoding="utf-8"))
    table, missing = [], []
    summary = {key: {"query_hits": 0, "precision": [], "not_worse": 0} for key in MODELS}
    fts_kw_hits = 0
    fts_full_hits = 0

    def jd(qid: str, doc_id: str) -> int:
        value = judgments.get(qid, {}).get("docs", {}).get(doc_id)
        if value is None:
            missing.append(f"{qid}/doc/{doc_id}")
            return 0
        return int(value)

    def jc(qid: str, uid: str) -> int:
        value = judgments.get(qid, {}).get("chunks", {}).get(uid)
        if value is None:
            missing.append(f"{qid}/chunk/{uid}")
            return 0
        return int(value)

    for qid, query, _keywords, kind in QUERIES:
        if qid not in payload["results"]:
            continue
        entry = payload["results"][qid]
        fts_full_count = sum(jd(qid, h["doc_id"]) for h in entry["fts_full"])
        fts_count = sum(jd(qid, h["doc_id"]) for h in entry["fts_kw"])
        fts_full_hits += 1 if fts_full_count else 0
        fts_kw_hits += 1 if fts_count else 0
        row = {
            "qid": qid,
            "query": query,
            "kind": kind,
            "fts_full": fts_full_count,
            "fts_kw": fts_count,
        }
        for key in MODELS:
            sem = entry["sem"][key]
            sem_count = sum(jc(qid, h["uid"]) for h in sem)
            sem_doc_count = len({h["doc_id"] for h in sem if jc(qid, h["uid"])})
            hyb_count = sum(jd(qid, h["doc_id"]) for h in entry["hybrid"][key])
            row[f"sem_{key}"] = sem_count
            row[f"sem_doc_{key}"] = sem_doc_count
            row[f"hybrid_{key}"] = hyb_count
            summary[key]["precision"].append(sem_count / TOP_K)
            if sem_count:
                summary[key]["query_hits"] += 1
            if hyb_count >= max(fts_count, sem_doc_count):
                summary[key]["not_worse"] += 1
        table.append(row)

    lines = [
        "# B5-01 相关性人工判定结果",
        "",
        "| 查询 | 类型 | FTS full | FTS kw | bge top5 | bge 文档级 | bge hybrid | multi top5 | multi 文档级 | multi hybrid |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in table:
        lines.append(
            f"| {row['qid']} {row['query']} | {row['kind']} | {row['fts_full']} | {row['fts_kw']} | "
            f"{row['sem_bge']} | {row['sem_doc_bge']} | {row['hybrid_bge']} | "
            f"{row['sem_multi']} | {row['sem_doc_multi']} | {row['hybrid_multi']} |"
        )
    lines += ["", "## 汇总", ""]
    lines.append(f"- FTS full 查询命中：{fts_full_hits}/15")
    lines.append(f"- FTS kw 查询命中：{fts_kw_hits}/15")
    for key in MODELS:
        s = summary[key]
        lines.append(
            f"- {key}: semantic 查询命中 {s['query_hits']}/15，"
            f"平均 top-5 精度 {statistics.mean(s['precision']):.2f}，"
            f"hybrid 不劣于单通道 {s['not_worse']}/15"
        )
    if missing:
        lines += ["", f"## 缺失判定（按 0 计，{len(missing)} 项）", ""]
        lines += [f"- {m}" for m in missing]
    report = "\n".join(lines) + "\n"
    (OUT / "relevance.md").write_text(report, encoding="utf-8")
    (OUT / "relevance.json").write_text(
        json.dumps(
            {"table": table, "missing": missing, "summary": {
                k: {"query_hits": v["query_hits"], "mean_precision": statistics.mean(v["precision"]),
                    "not_worse": v["not_worse"]} for k, v in summary.items()
            }}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    print(report)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["pool", "score"])
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--limit-docs", type=int, default=0, help="冒烟测试用")
    parser.add_argument("--limit-queries", type=int, default=0, help="冒烟测试用")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.phase == "pool":
        phase_pool(args)
    else:
        phase_score(args)


if __name__ == "__main__":
    main()
