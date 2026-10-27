"""全文检索：SQLite FTS5 trigram（≥3 字词）+ 短词单次 LIKE 扫描；PostgreSQL 用 zhparser 中文分词。

R8a 的形状（任务书 docs/roadmap/R8-chinese-search.md §3 RW-803/804/805）：

- 查询先切成词（`query_terms.build_query_plan`），不再存在「整句当一个短语」的路径——
  trigram 要求逐字连续，整句必然打空。
- ≥3 字的词**逐个**发一条 FTS 查询：跨词 AND 会打空，跨词 OR 遇到高频词会退化（实测 934 ms）。
- <3 字的词只能 LIKE（trigram 不登记 2 字 token），合并成**一条**扫描，并且**不写 SQL 侧
  `ORDER BY CASE`**：排序会强制取回全部匹配行，`LIMIT` 的提前终止就失效了（实测 10 ms → 670 ms）。
- 正文只在 SQL 侧取 480 字窗口，不再整行 ORM 载入。
- `/search` 与 `/search/summary` 共用同一套词路由（`_route`）与谓词构造器（`_like_predicate`）。
"""

import logging
import re
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from itertools import islice

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import column, func, literal_column, or_, select, table, text
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Document
from ..services.query_terms import (
    QueryPlan,
    build_query_plan,
    iter_recall_windows,
    with_recall_windows,
)
from ..services.search_index import fts_available
from ..services.sql_utils import escape_like
from .documents import extract_keywords

router = APIRouter(prefix="/search", tags=["search"])

MARK_START = "<mark>"
MARK_END = "</mark>"

_WINDOW_PAD = 60          # SQL 侧窗口在命中点前留的字数
_WINDOW_CHARS = 480       # SQL 侧最多取回的正文字数（20 行 × 480 = 9,600，远低于 64,000 预算）
_SNIPPET_MAX_CHARS = 220  # 响应里 snippet 的硬上限（含省略号）
_RECALL_WIDTH = 80
_MAX_RECALL_WINDOWS = 6   # 打空时补位的滑窗词上限
_TITLE_SAMPLE = 30        # summary 相关词的取样标题数
_BM25_WEIGHTS = "0.0, 5.0, 1.0, 1.0, 1.0"
_FIELD_CONTENT = 3        # _field_rank 的兜底档：正文命中（title/author/description 依次是 0/1/2）
# FTS 排序不带 JOIN，所以软删/类型过滤后可能少给名额：先多取，最多取到 _FTS_FETCH_MAX
_FTS_FETCH_SLACK = 20
_FTS_FETCH_MAX = 4000

# FTS5 影子表（触发器维护）；MATCH 子句在本文件只出现在 _FTS_MATCH 这一处
_FTS_TABLE = table("documents_fts", column("doc_id"))
_FTS_MATCH = "documents_fts MATCH :match"


class SearchHit(BaseModel):
    id: str
    title: str
    type: str
    snippet: str


class SearchSummary(BaseModel):
    query: str
    total: int
    by_type: dict[str, int]
    suggestions: list[str]


@dataclass(frozen=True)
class _Candidate:
    doc_id: uuid.UUID
    title: str
    type: str
    author: str | None
    updated_at: datetime | None
    description: str | None
    window: str
    matched_primary: bool
    hit_terms: int
    best_score: float


def _escape_fts_query(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _strip_newlines(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _needs_case_insensitive(term: str) -> bool:
    """含 ASCII 字母的词保持大小写不敏感；纯 CJK 词用敏感 LIKE（实测省 30%）。"""
    return any(char.isascii() and char.isalpha() for char in term)


def _like_expr(field, term: str):
    pattern = f"%{escape_like(term)}%"
    if _needs_case_insensitive(term):
        return field.ilike(pattern, escape="\\")
    return field.like(pattern, escape="\\")


def _like_predicate(terms: list[str]):
    """一个词一组列谓词，列序 title→author→description→content：
    SQLite 的 OR 逐行短路，元数据先命中的行根本不读正文。"""
    if not terms:
        return None
    fields = (Document.title, Document.author, Document.description, Document.content)
    return or_(*[or_(*[_like_expr(f, term) for f in fields]) for term in terms])


def _content_window_expr(term: str):
    """命中点前 60 字起的 480 字窗口；`instr` 找不到时退化为正文开头。

    纯 CJK 词不套 `lower()`：CJK 大小写折叠是恒等变换，套上去等于对 200k 字正文
    多做一次全量拷贝（实测每词多 20–30 ms）。
    """
    content = func.coalesce(Document.content, "")
    needle = func.lower(term) if _needs_case_insensitive(term) else term
    haystack = func.lower(content) if _needs_case_insensitive(term) else content
    start = func.max(1, func.instr(haystack, needle) - _WINDOW_PAD)
    return func.substr(content, start, _WINDOW_CHARS)


def _type_filter(doc_type: str | None) -> list:
    return [Document.type == doc_type] if doc_type else []


def _as_uuid(value) -> uuid.UUID:
    """documents_fts.doc_id 是影子表里的裸列（SQLite 存 32 位十六进制），
    不像 Document.id 那样有 Uuid 结果处理器 → 不回表前必须自己转成 UUID，
    否则主键映射永远 miss，命中会被静默丢光。"""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _fts_rank_ids(db: Session, term: str, fetch: int) -> list[tuple[uuid.UUID, float]]:
    """FTS5 排序**不带 JOIN**：join 让每个匹配行都回一次 documents 表取列，高频词实测
    143–218 ms；只取 id+score 再按主键回表是 22–26 ms（任务书 E15）。软删/类型过滤留到回表那一步。"""
    score = literal_column(f"bm25(documents_fts, {_BM25_WEIGHTS})").label("score")
    stmt = (
        select(_FTS_TABLE.c.doc_id, score)
        .where(text(_FTS_MATCH))
        .order_by(text("score"))
        .limit(fetch)
    )
    rows = db.execute(stmt, {"match": _escape_fts_query(term)}).all()
    return [(_as_uuid(row[0]), float(row[1])) for row in rows]


def _docs_by_ids(db: Session, ids: list[uuid.UUID], doc_type: str | None,
                 window_term: str) -> dict[uuid.UUID, tuple]:
    if not ids:
        return {}
    stmt = select(
        Document.id, Document.title, Document.type, Document.author, Document.updated_at,
        Document.description, _content_window_expr(window_term).label("window"),
    ).where(Document.id.in_(ids), Document.deleted_at.is_(None), *_type_filter(doc_type))
    return {row.id: row for row in db.execute(stmt).all()}


def _fts_hits(db: Session, term: str, doc_type: str | None, limit: int) -> list[_Candidate]:
    """先按 bm25 取一批 doc_id，再按主键回表过滤/取列；名额不够就加倍重取。"""
    fetch = max(limit * 2, limit + _FTS_FETCH_SLACK)
    while True:
        ranked = _fts_rank_ids(db, term, fetch)
        docs = _docs_by_ids(db, [doc_id for doc_id, _ in ranked], doc_type, term)
        hits = [
            _Candidate(
                doc_id=doc_id, title=docs[doc_id].title, type=docs[doc_id].type,
                author=docs[doc_id].author, updated_at=docs[doc_id].updated_at,
                description=docs[doc_id].description, window=docs[doc_id].window or "",
                matched_primary=True, hit_terms=1, best_score=score,
            )
            for doc_id, score in ranked
            if doc_id in docs
        ]
        if len(hits) >= limit or len(ranked) < fetch or fetch >= _FTS_FETCH_MAX:
            return hits[:limit]
        fetch = min(fetch * 4, _FTS_FETCH_MAX)


def _like_hits(db: Session, terms: list[str], doc_type: str | None, exclude: set[uuid.UUID],
               fetch: int) -> list[_Candidate]:
    """短词填充：一条不带 ORDER BY 的扫描，靠 LIMIT 提前终止。"""
    if fetch <= 0 or not terms:
        return []
    window_term = terms[0]
    stmt = (
        select(
            Document.id, Document.title, Document.type, Document.author, Document.updated_at,
            Document.description, _content_window_expr(window_term).label("window"),
        )
        .where(Document.deleted_at.is_(None), _like_predicate(terms), *_type_filter(doc_type))
        .limit(fetch)
    )
    if exclude:
        stmt = stmt.where(~Document.id.in_(exclude))
    rows = db.execute(stmt).all()
    return [
        _Candidate(
            doc_id=row.id, title=row.title, type=row.type, author=row.author,
            updated_at=row.updated_at, description=row.description, window=row.window or "",
            matched_primary=True,
            hit_terms=sum(1 for t in terms if _row_contains(t, row)),
            best_score=float("inf"),
        )
        for row in rows
    ]


def _row_contains(term: str, row) -> bool:
    values = (row.title, row.author, row.description, row.window)
    return any(_term_in(term, value or "") for value in values)


def _term_in(term: str, value: str) -> bool:
    return term.lower() in value.lower() if _needs_case_insensitive(term) else term in value


def _field_rank(candidate: _Candidate, terms: list[str]) -> int:
    """字段优先级在 Python 侧算：行数 ≤ limit，与全表无关（取代 SQL 侧的 ORDER BY CASE）。"""
    values = (candidate.title, candidate.author, candidate.description, candidate.window)
    for rank, value in enumerate(values):
        if any(_term_in(term, value or "") for term in terms):
            return rank
    return _FIELD_CONTENT


def _sort_key(candidate: _Candidate, terms: list[str]):
    stamp = candidate.updated_at.timestamp() if candidate.updated_at else 0.0
    return (
        0 if candidate.matched_primary else 1,
        -candidate.hit_terms,
        _field_rank(candidate, terms),
        candidate.best_score,
        -stamp,
        str(candidate.doc_id),
    )


def _dedupe(candidates: Iterable[_Candidate]) -> list[_Candidate]:
    """同一文档被多个词命中时合并：命中词数累加，bm25 取最好的一档。"""
    merged: dict[uuid.UUID, _Candidate] = {}
    for item in candidates:
        current = merged.get(item.doc_id)
        if current is None:
            merged[item.doc_id] = item
            continue
        merged[item.doc_id] = _Candidate(
            doc_id=current.doc_id, title=current.title, type=current.type,
            author=current.author, updated_at=current.updated_at,
            description=current.description, window=current.window,
            matched_primary=current.matched_primary or item.matched_primary,
            hit_terms=current.hit_terms + item.hit_terms,
            best_score=min(current.best_score, item.best_score),
        )
    return list(merged.values())


def _route(terms: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """trigram 只登记 ≥3 字符的 token：3 字以上走 FTS，2 字词只能 LIKE。"""
    return [t for t in terms if len(t) >= 3], [t for t in terms if len(t) < 3]


def _recall_windows(plan: QueryPlan) -> tuple[str, ...]:
    return tuple(islice(iter_recall_windows(plan), _MAX_RECALL_WINDOWS))


def _finalize(candidates: list[_Candidate], effective: QueryPlan, limit: int
              ) -> tuple[list[_Candidate], QueryPlan]:
    if not candidates:
        return [], effective
    terms = list(effective.terms)
    candidates.sort(key=lambda c: _sort_key(c, terms))
    return candidates[:limit], effective


def _collect(db: Session, plan: QueryPlan, doc_type: str | None, limit: int
             ) -> tuple[list[_Candidate], QueryPlan]:
    """一条链产出候选集；第二个返回值是实际生效的词（打空时含滑窗降级词）。"""
    indexable, short = _route(plan.terms)

    if not fts_available():
        # FTS 不可用（触发器没建起来）：整条链退化成一次 LIKE 扫描
        return _finalize(_like_hits(db, list(plan.terms), doc_type, set(), limit), plan, limit)

    pool: list[_Candidate] = []
    for term in indexable:
        try:
            pool.extend(_fts_hits(db, term, doc_type, limit))
        except Exception as exc:
            # 触发器同步异常时兜底 LIKE——必须留痕，否则 FTS 损坏无人知晓
            logging.getLogger("kb.search").warning("FTS 查询失败，退化 LIKE: %s", exc, exc_info=True)
            return _finalize(_like_hits(db, list(plan.terms), doc_type, set(), limit), plan, limit)

    candidates = _dedupe(pool)
    if short and len(candidates) < limit:
        exclude = {c.doc_id for c in candidates}
        candidates = _dedupe(
            candidates + _like_hits(db, short, doc_type, exclude, limit - len(candidates))
        )

    effective = plan
    if not candidates:
        windows = _recall_windows(plan)
        if windows:
            effective = with_recall_windows(plan, windows)
            retried = _dedupe(
                [c for term in windows for c in _fts_hits(db, term, doc_type, limit)]
            )
            if short and len(retried) < limit:
                retried = _dedupe(retried + _like_hits(
                    db, short, doc_type, {c.doc_id for c in retried}, limit - len(retried)
                ))
            candidates = retried

    return _finalize(candidates, effective, limit)


def _snippet_for(candidate: _Candidate, terms: list[str]) -> str:
    if any(_term_in(term, candidate.window) for term in terms):
        body = candidate.window
    elif any(_term_in(term, candidate.description or "") for term in terms):
        body = candidate.description or ""
    else:
        body = candidate.window or candidate.description or ""
    return _highlight_snippet(body, terms)


def _highlight_snippet(text_value: str, terms: list[str], width: int = _RECALL_WIDTH) -> str:
    source = _strip_newlines(text_value)
    if not source:
        return ""
    lowered = source.lower()
    best: tuple[int, int, int] | None = None
    for term in terms:
        idx = lowered.find(term.lower())
        if idx < 0:
            continue
        start = max(0, idx - width // 3)
        end = min(len(source), idx + len(term) + width)
        if best is None or start < best[0]:
            best = (start, end, idx)
    if best is None:
        return _clip(source[:width] + ("…" if len(source) > width else ""))
    start, end, idx = best
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(source) else ""
    match_end = idx + next(
        (len(t) for t in terms if source.lower()[idx:idx + len(t)].lower() == t.lower()),
        len(terms[0]),
    )
    segment = (
        source[start:idx] + MARK_START + source[idx:match_end] + MARK_END + source[match_end:end]
    )
    return _clip(prefix + segment + suffix)


def _clip(value: str) -> str:
    """snippet 的字符硬上限（含省略号与 <mark> 标签）。"""
    if len(value) <= _SNIPPET_MAX_CHARS:
        return value
    room = _SNIPPET_MAX_CHARS - len("…") - len(MARK_END)
    head = value[:room]
    if head.count(MARK_START) > head.count(MARK_END):
        head += MARK_END
    return head + "…"


def _to_hit(candidate: _Candidate, terms: list[str]) -> SearchHit:
    return SearchHit(
        id=str(candidate.doc_id),
        title=candidate.title,
        type=candidate.type,
        snippet=_snippet_for(candidate, terms),
    )


@router.get("", response_model=list[SearchHit])
def search(
    q: str = Query(min_length=1),
    type: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    q = q.strip()
    if not q:
        return []

    if db.get_bind().dialect.name == "postgresql":
        return _search_postgresql(db, q, type, limit)

    plan = build_query_plan(q)
    if not plan.terms:
        return []
    candidates, effective = _collect(db, plan, type, limit)
    return [_to_hit(c, list(effective.terms)) for c in candidates]


def _search_postgresql(db: Session, q: str, doc_type: str | None, limit: int) -> list[SearchHit]:
    zh = literal_column("'zh'")
    tsquery = func.plainto_tsquery(zh, q)
    stmt = select(Document).where(
        Document.deleted_at.is_(None),
        literal_column("search_vector").op("@@")(tsquery),
    ).limit(limit)
    if doc_type:
        stmt = stmt.where(Document.type == doc_type)
    stmt = stmt.add_columns(
        func.ts_headline(
            zh,
            func.coalesce(Document.content, ""),
            tsquery,
            "StartSel=<mark>,StopSel=</mark>,MaxFragments=2,FragmentSize=40",
        ).label("headline")
    )
    rows = db.execute(stmt).all()
    return [
        SearchHit(id=str(doc.id), title=doc.title, type=doc.type, snippet=headline)
        for doc, headline in rows
    ]


@router.get("/summary", response_model=SearchSummary)
def search_summary(
    q: str = Query(min_length=1),
    type: str | None = None,
    db: Session = Depends(get_db),
):
    """搜索摘要：总命中数、按类型分布与相关词（供前端统计条，不返回逐条 snippet）"""
    q = q.strip()
    if not q:
        return SearchSummary(query=q, total=0, by_type={}, suggestions=[])

    if db.get_bind().dialect.name == "postgresql":
        total, by_type, titles = _postgresql_stats(db, q, type)
        query_terms = [t for t in q.split() if t]
    else:
        plan = build_query_plan(q)
        total, by_type, titles = _sqlite_stats(db, plan, type) if plan.terms else (0, {}, [])
        query_terms = list(plan.terms)

    return SearchSummary(
        query=q,
        total=total,
        by_type={k: int(v) for k, v in by_type.items()},
        suggestions=_related_terms(titles, query_terms),
    )


def _sqlite_stats(db: Session, plan: QueryPlan, doc_type: str | None):
    """与 `_collect` 同一套词路由与谓词，只是不加 LIMIT——`total` 因此仍是精确计数。

    短词那一条必然扫完整表（任务书 E4′），这是 RD-J 的成本所在。
    """
    indexable, short = _route(plan.terms)
    docs: dict[uuid.UUID, str] = {}
    titles: list[str] = []

    def add(rows) -> None:
        for row in rows:
            if row.id in docs:
                continue
            docs[row.id] = row.type
            if len(titles) < _TITLE_SAMPLE:
                titles.append(row.title)

    if fts_available():
        try:
            for term in indexable:
                add(_fts_docs(db, term, doc_type))
        except Exception as exc:
            logging.getLogger("kb.search").warning("FTS 统计失败，退化 LIKE 计数: %s", exc, exc_info=True)
            docs, titles = {}, []
            indexable, short = [], list(plan.terms)
    else:
        indexable, short = [], list(plan.terms)

    if short:
        stmt = (
            select(Document.id, Document.type, Document.title)
            .where(Document.deleted_at.is_(None), _like_predicate(short), *_type_filter(doc_type))
        )
        add(db.execute(stmt).all())

    # 与 _collect 同一个触发条件：整条链打空才补滑窗词，这样 hits 与 total 口径一致
    if not docs and indexable:
        for term in _recall_windows(plan):
            add(_fts_docs(db, term, doc_type))

    return len(docs), dict(Counter(docs.values())), titles


def _fts_match_ids(db: Session, term: str) -> list[uuid.UUID]:
    """一个词的全部匹配 doc_id（不限量、不 JOIN）：计数走它，取列另走主键回表。"""
    stmt = select(_FTS_TABLE.c.doc_id).where(text(_FTS_MATCH))
    return [_as_uuid(row[0]) for row in db.execute(stmt, {"match": _escape_fts_query(term)}).all()]


def _docs_meta(db: Session, ids: list[uuid.UUID], doc_type: str | None) -> list[tuple]:
    """按主键分批回表，只取计数需要的三列（同样避开 JOIN 排序）。"""
    out: list[tuple] = []
    for i in range(0, len(ids), 500):
        stmt = (
            select(Document.id, Document.type, Document.title)
            .where(Document.id.in_(ids[i:i + 500]), Document.deleted_at.is_(None),
                   *_type_filter(doc_type))
        )
        out.extend(db.execute(stmt).all())
    return out


def _fts_docs(db: Session, term: str, doc_type: str | None):
    return _docs_meta(db, _fts_match_ids(db, term), doc_type)


def _postgresql_stats(db: Session, q: str, doc_type: str | None):
    zh = literal_column("'zh'")
    tsquery = func.plainto_tsquery(zh, q)
    base_where = [Document.deleted_at.is_(None), literal_column("search_vector").op("@@")(tsquery)]
    if doc_type:
        base_where.append(Document.type == doc_type)
    sub = select(Document.id, Document.type, Document.title).where(*base_where).subquery()
    total = db.scalar(select(func.count()).select_from(sub)) or 0
    by_type = dict(db.execute(select(sub.c.type, func.count()).group_by(sub.c.type)).all())
    titles = [row[0] for row in db.execute(select(sub.c.title).limit(_TITLE_SAMPLE)).all()]
    return int(total), by_type, titles


def _related_terms(titles: list[str], query_terms: list[str], top_n: int = 5) -> list[str]:
    """相关词：命中文档标题中的高频关键词，排除与查询词重叠者"""
    candidates = extract_keywords(" ".join(titles), top_n=top_n * 4)
    lowered = [t.lower() for t in query_terms]
    out: list[str] = []
    for word in candidates:
        if any(word in t or t in word for t in lowered):
            continue
        out.append(word)
        if len(out) >= top_n:
            break
    return out
