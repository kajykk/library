"""全文检索：SQLite FTS5 trigram（中文子串）+ ILIKE 兜底；PostgreSQL 用 zhparser 中文分词"""

import re
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, false, func, literal_column, or_, select, text
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Document
from ..services.search_index import fts_available
from .documents import extract_keywords

router = APIRouter(prefix="/search", tags=["search"])

MARK_START = "<mark>"
MARK_END = "</mark>"


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


def _escape_fts_query(q: str) -> str:
    return '"' + q.replace('"', '""') + '"'


def _strip_newlines(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _highlight_snippet(text_value: str, terms: list[str], width: int = 80) -> str:
    source = _strip_newlines(text_value)
    if not source:
        return ""
    lowered = source.lower()
    best: tuple[int, int] | None = None
    for term in terms:
        idx = lowered.find(term.lower())
        if idx < 0:
            continue
        start = max(0, idx - width // 3)
        end = min(len(source), idx + len(term) + width)
        if best is None or start < best[0]:
            best = (start, end, idx)
    if best is None:
        return source[:width] + ("…" if len(source) > width else "")
    start, end, idx = best
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(source) else ""
    match_end = idx + next(
        (len(t) for t in terms if source.lower()[idx : idx + len(t)].lower() == t.lower()),
        len(terms[0]),
    )
    segment = (
        source[start:idx] + MARK_START + source[idx:match_end] + MARK_END + source[match_end:end]
    )
    return prefix + segment + suffix


def _search_fts(db: Session, q: str, doc_type: str | None, limit: int) -> list[SearchHit]:
    terms = q.split()
    match_expr = " AND ".join(_escape_fts_query(t) for t in terms)
    type_clause = "AND d.type = :doc_type" if doc_type else ""
    sql = f"""
        SELECT d.id, d.title, d.type, d.content, d.description
        FROM documents_fts f
        JOIN documents d ON d.id = f.doc_id
        WHERE documents_fts MATCH :match
          AND d.deleted_at IS NULL
          {type_clause}
        ORDER BY bm25(documents_fts, 0.0, 5.0, 1.0, 1.0, 1.0)
        LIMIT :limit
    """
    params = {"match": match_expr, "limit": limit}
    if doc_type:
        params["doc_type"] = doc_type
    rows = db.execute(text(sql), params).all()
    hits: list[SearchHit] = []
    for doc_id, title, doc_type_val, content, description in rows:
        body = description or ""
        for term in terms:
            if term.lower() in (content or "").lower():
                body = content or ""
                break
        hits.append(
            SearchHit(
                id=str(uuid.UUID(str(doc_id))),
                title=title,
                type=doc_type_val,
                snippet=_highlight_snippet(body, terms),
            )
        )
    return hits


def _search_ilike(db: Session, q: str, doc_type: str | None, limit: int) -> list[SearchHit]:
    terms = [t for t in q.split() if t]

    def any_field(field, ts: list[str]):
        return or_(*[field.ilike(f"%{t}%") for t in ts])

    stmt = select(Document).where(Document.deleted_at.is_(None)).limit(limit)
    if doc_type:
        stmt = stmt.where(Document.type == doc_type)
    stmt = stmt.where(
        or_(
            any_field(Document.title, terms),
            any_field(Document.author, terms),
            any_field(Document.description, terms),
            any_field(Document.content, terms),
        )
    )
    stmt = stmt.order_by(
        case(
            (any_field(Document.title, terms), 0),
            (any_field(Document.author, terms), 1),
            (any_field(Document.description, terms), 2),
            else_=3,
        ),
        Document.updated_at.desc(),
    )

    docs = db.scalars(stmt).all()
    hits: list[SearchHit] = []
    for doc in docs:
        body = doc.description or ""
        for term in terms:
            if term.lower() in (doc.content or "").lower():
                body = doc.content or ""
                break
        hits.append(
            SearchHit(
                id=str(doc.id),
                title=doc.title,
                type=doc.type,
                snippet=_highlight_snippet(body, terms),
            )
        )
    return hits


@router.get("", response_model=list[SearchHit])
def search(
    q: str = Query(min_length=1),
    type: str | None = None,
    limit: int = Query(default=20, le=100),
    db: Session = Depends(get_db),
):
    q = q.strip()
    if not q:
        return []

    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        zh = literal_column("'zh'")
        tsquery = func.plainto_tsquery(zh, q)
        stmt = select(Document).where(
            Document.deleted_at.is_(None),
            literal_column("search_vector").op("@@")(tsquery),
        ).limit(limit)
        if type:
            stmt = stmt.where(Document.type == type)
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

    # trigram 索引只含 >= 3 字符的 token：只要任一词短于 3 字，FTS 会静默漏配
    #（如 "持续 改善"），此时整体回退 ILIKE
    if fts_available():
        terms = q.split()
        short_term = any(0 < len(t) < 3 for t in terms)
        if not short_term and len(q) >= 3:
            try:
                return _search_fts(db, q, type, limit)
            except Exception:
                # 触发器同步异常时兜底 ILIKE
                pass
    return _search_ilike(db, q, type, limit)


def _fts_usable(q: str, terms: list[str]) -> bool:
    """与 search 主路径一致的 FTS 可用性判定（短词回退 ILIKE）"""
    if not fts_available():
        return False
    if any(0 < len(t) < 3 for t in terms):
        return False
    return len(q) >= 3


_FTS_COUNT_SQL = """
    SELECT COUNT(*)
    FROM documents_fts f
    JOIN documents d ON d.id = f.doc_id
    WHERE documents_fts MATCH :match AND d.deleted_at IS NULL {type_clause}
"""

_FTS_BY_TYPE_SQL = """
    SELECT d.type AS doc_type, COUNT(*)
    FROM documents_fts f
    JOIN documents d ON d.id = f.doc_id
    WHERE documents_fts MATCH :match AND d.deleted_at IS NULL {type_clause}
    GROUP BY d.type
"""

_FTS_TITLES_SQL = """
    SELECT d.title
    FROM documents_fts f
    JOIN documents d ON d.id = f.doc_id
    WHERE documents_fts MATCH :match AND d.deleted_at IS NULL {type_clause}
    ORDER BY bm25(documents_fts, 0.0, 5.0, 1.0, 1.0, 1.0)
    LIMIT 30
"""


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

    dialect = db.get_bind().dialect.name
    terms = [t for t in q.split() if t]

    if dialect == "postgresql":
        zh = literal_column("'zh'")
        tsquery = func.plainto_tsquery(zh, q)
        cond = literal_column("search_vector").op("@@")(tsquery)
        base_where = [Document.deleted_at.is_(None), cond]
        if type:
            base_where.append(Document.type == type)
        sub = select(Document).where(*base_where).subquery()
        total = db.scalar(select(func.count()).select_from(sub)) or 0
        by_type = dict(
            db.execute(
                select(sub.c.type, func.count()).group_by(sub.c.type)
            ).all()
        )
        titles = [
            row[0]
            for row in db.execute(select(sub.c.title).limit(30)).all()
        ]
    elif _fts_usable(q, terms):
        match_expr = " AND ".join(_escape_fts_query(t) for t in terms)
        type_clause = "AND d.type = :doc_type" if type else ""
        params: dict = {"match": match_expr}
        if type:
            params["doc_type"] = type
        try:
            total = db.execute(
                text(_FTS_COUNT_SQL.format(type_clause=type_clause)), params
            ).scalar() or 0
            by_type = {
                row[0]: row[1]
                for row in db.execute(
                    text(_FTS_BY_TYPE_SQL.format(type_clause=type_clause)), params
                ).all()
                if row[0]
            }
            titles = [
                row[0]
                for row in db.execute(
                    text(_FTS_TITLES_SQL.format(type_clause=type_clause)), params
                ).all()
            ]
        except Exception:
            # FTS 同步异常时兜底 ILIKE 统计
            total, by_type, titles = _ilike_stats(db, q, terms, type)
    else:
        total, by_type, titles = _ilike_stats(db, q, terms, type)

    suggestions = _related_terms(titles, terms)
    return SearchSummary(query=q, total=int(total), by_type={k: int(v) for k, v in by_type.items()}, suggestions=suggestions)


def _ilike_stats(db: Session, q: str, terms: list[str], doc_type: str | None):
    def any_field(field):
        return or_(*[field.ilike(f"%{t}%") for t in terms]) if terms else false()

    cond = or_(
        any_field(Document.title),
        any_field(Document.author),
        any_field(Document.description),
        any_field(Document.content),
    )
    where = [Document.deleted_at.is_(None), cond]
    if doc_type:
        where.append(Document.type == doc_type)
    sub = select(Document).where(*where).subquery()
    total = db.scalar(select(func.count()).select_from(sub)) or 0
    by_type = dict(db.execute(select(sub.c.type, func.count()).group_by(sub.c.type)).all())
    titles = [row[0] for row in db.execute(select(sub.c.title).limit(30)).all()]
    return total, by_type, titles


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
