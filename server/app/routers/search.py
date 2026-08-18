"""全文检索：SQLite FTS5 trigram（中文子串）+ ILIKE 兜底；PostgreSQL 用 zhparser 中文分词"""

import re
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func, literal_column, or_, select, text
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Document
from ..services.search_index import fts_available

router = APIRouter(prefix="/search", tags=["search"])

MARK_START = "<mark>"
MARK_END = "</mark>"


class SearchHit(BaseModel):
    id: str
    title: str
    type: str
    snippet: str


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

    if fts_available() and len(q) >= 3:
        try:
            return _search_fts(db, q, type, limit)
        except Exception:
            # 触发器同步异常时兜底 ILIKE
            pass
    return _search_ilike(db, q, type, limit)
