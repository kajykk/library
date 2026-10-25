"""documents 列表/计数的共用过滤条件。"""

import uuid

from fastapi import HTTPException
from sqlalchemy import or_

from ...models import Document, DocumentTag, Tag
from ...services.sql_utils import escape_like


def apply_types_filter(stmt, types: str | None):
    """types 支持逗号分隔（书架传 "book,pdf"），单值仍然兼容原有 type 参数"""
    if not types:
        return stmt
    values = [v.strip() for v in types.split(",") if v.strip()]
    if not values:
        return stmt
    return stmt.where(Document.type.in_(values))


def apply_status_filter(stmt, status: str | None):
    if not status or status == "all":
        return stmt
    if status == "reading":
        return stmt.where(Document.read_progress > 0, Document.read_progress < 1)
    if status == "unread":
        return stmt.where(or_(Document.read_progress.is_(None), Document.read_progress <= 0))
    if status == "done":
        return stmt.where(Document.read_progress >= 1)
    raise HTTPException(status_code=400, detail=f"不支持的 status: {status}")


def apply_document_filters(
    stmt,
    *,
    include_deleted: bool = False,
    type: str | None = None,
    types: str | None = None,
    collection_id: uuid.UUID | None = None,
    tag: str | None = None,
    q: str | None = None,
    author: str | None = None,
    status: str | None = None,
):
    """列表与计数共用同一套过滤条件，避免两处条件漂移导致"显示 N 本、翻页却少几本"。"""
    stmt = stmt.where(Document.deleted_at.is_(None) if not include_deleted else True)
    stmt = apply_types_filter(stmt, types)
    if type:
        stmt = stmt.where(Document.type == type)
    if collection_id:
        stmt = stmt.where(Document.collection_id == collection_id)
    if tag:
        stmt = stmt.join(DocumentTag, DocumentTag.document_id == Document.id).join(
            Tag, Tag.id == DocumentTag.tag_id
        ).where(Tag.name == tag)
    if q:
        like = f"%{escape_like(q)}%"
        stmt = stmt.where(
            or_(
                Document.title.ilike(like, escape="\\"),
                Document.author.ilike(like, escape="\\"),
                Document.description.ilike(like, escape="\\"),
                Document.content.ilike(like, escape="\\"),
            )
        )
    if author:
        stmt = stmt.where(Document.author.ilike(f"%{escape_like(author)}%", escape="\\"))
    return apply_status_filter(stmt, status)
