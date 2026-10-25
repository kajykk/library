"""文档洞察：反链、未链接提及、关键词/摘要。"""

import uuid

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ...database import get_db
from ...models import Document, Link
from ...services.sql_utils import escape_like
from ...services.text_analysis import _context_snippet, extract_keywords, one_line_summary
from ._shared import MENTION_PATTERN, get_doc_or_404, router


class Backlink(BaseModel):
    id: str
    title: str
    type: str
    snippet: str = ""


class DocumentInsight(BaseModel):
    summary: str
    suggested_tags: list[str]


def sync_mention_links(db: Session, doc: Document) -> int:
    """解析文档 content 中的 [[标题]]，按标题匹配其他文档并重建 mention 链接。返回新建链接数。"""
    db.flush()  # 确保新文档的 id（Python 侧默认值）已生成
    db.execute(delete(Link).where(Link.source_id == doc.id, Link.type == "mention"))
    if not doc.content:
        return 0

    titles = {t.strip() for t in MENTION_PATTERN.findall(doc.content)}
    titles.discard("")
    if not titles:
        return 0

    # 一次 IN 批量查询替代逐标题 SELECT（此前为 N+1，长文多链接时是查询风暴）
    targets = db.scalars(
        select(Document).where(
            Document.title.in_(titles),
            Document.id != doc.id,
            Document.deleted_at.is_(None),
        )
    ).all()
    for target in targets:
        db.add(Link(source_id=doc.id, target_id=target.id, type="mention"))
    return len(targets)


@router.get("/{doc_id}/backlinks", response_model=list[Backlink])
def get_backlinks(doc_id: uuid.UUID, db: Session = Depends(get_db)):
    """反向链接：引用了本文档（[[标题]]）的其他文档，附上下文摘要"""
    doc = get_doc_or_404(db, doc_id)
    rows = db.execute(
        select(Document)
        .join(Link, Link.source_id == Document.id)
        .where(Link.target_id == doc.id, Document.deleted_at.is_(None))
        .order_by(Document.updated_at.desc())
    ).scalars().all()
    return [
        Backlink(
            id=str(r.id),
            title=r.title,
            type=r.type,
            snippet=_context_snippet(r.content or r.description or "", doc.title),
        )
        for r in rows
    ]


@router.get("/{doc_id}/unlinked-mentions", response_model=list[Backlink])
def get_unlinked_mentions(doc_id: uuid.UUID, db: Session = Depends(get_db)):
    """未链接提及：正文包含本文档标题但尚未建立 [[链接]] 的其他文档"""
    doc = get_doc_or_404(db, doc_id)
    title = doc.title.strip()
    if len(title) < 2:
        return []
    linked_ids = set(
        db.scalars(select(Link.source_id).where(Link.target_id == doc.id))
    )
    candidates = db.scalars(
        select(Document)
        .where(
            Document.deleted_at.is_(None),
            Document.id != doc.id,
            Document.content.ilike(f"%{escape_like(title)}%", escape="\\"),
        )
        .order_by(Document.updated_at.desc())
        .limit(50)
    ).all()
    return [
        Backlink(
            id=str(c.id),
            title=c.title,
            type=c.type,
            snippet=_context_snippet(c.content or "", title),
        )
        for c in candidates
        if c.id not in linked_ids
    ]


@router.get("/{doc_id}/insight", response_model=DocumentInsight)
def get_document_insight(doc_id: uuid.UUID, db: Session = Depends(get_db)):
    """文档洞察：基于正文词频的 top 关键词标签 + 一句话摘要（供前端"智能洞察"卡片）"""
    doc = get_doc_or_404(db, doc_id)
    body = doc.content or ""
    return DocumentInsight(
        summary=one_line_summary(body or doc.description or ""),
        suggested_tags=extract_keywords(f"{doc.title} {body}"),
    )
