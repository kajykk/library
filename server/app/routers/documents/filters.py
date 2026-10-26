"""documents 列表/计数的共用过滤条件。"""

import uuid

from fastapi import HTTPException
from sqlalchemy import or_, cast, String as SAString

from ...models import Document, DocumentQuality, DocumentTag, Tag
from ...services.sql_utils import (
    escape_like,
    language_norm_expr,
    normalize_language_code,
    trimmed_content_length_expr,
)
from ...config import get_settings

# quality 筛选值（合法集合）
QUALITY_FILTERS = frozenset({
    "issues", "empty_content", "short_content", "no_cover", "unclassified",
    "language_variant", "needs_ocr", "missing_file", "duplicate", "not_audited",
})


def apply_quality_filter(stmt, db, quality: str | None):
    """按质量条件筛选文档。

    「实时」条件不依赖审计快照，便宜且始终准确。
    「快照」条件来自 document_quality 表，需要 outerjoin。
    两者在 issues 中是 OR 关系。
    """
    if not quality:
        return stmt

    if quality not in QUALITY_FILTERS:
        raise HTTPException(status_code=400, detail=f"不支持的 quality: {quality}")

    settings = get_settings()
    threshold = settings.quality_min_content_chars

    # 快照条件统一 outerjoin（只 join 一次）
    needs_snapshot = quality in ("needs_ocr", "missing_file", "duplicate", "not_audited", "issues")
    if needs_snapshot:
        stmt = stmt.outerjoin(
            DocumentQuality, DocumentQuality.document_id == Document.id
        )

    if quality == "issues":
        # 实时便宜条件 OR 快照昂贵条件
        length_expr = trimmed_content_length_expr(db)
        realtime = or_(
            length_expr == 0,
            (length_expr > 0) & (length_expr < threshold),
            (Document.cover_path.is_(None)) | (Document.cover_path == ""),
            Document.collection_id.is_(None),
            (Document.language.isnot(None)) & (~Document.language.in_(("zh", "en", "ja", "und", ""))),
        )
        snapshot = or_(
            DocumentQuality.file_exists.is_(False),
            DocumentQuality.needs_ocr.is_(True),
            DocumentQuality.duplicate_group_size > 1,
        )
        return stmt.where(or_(realtime, snapshot))

    if quality == "empty_content":
        length_expr = trimmed_content_length_expr(db)
        return stmt.where(length_expr == 0)

    if quality == "short_content":
        length_expr = trimmed_content_length_expr(db)
        return stmt.where((length_expr > 0) & (length_expr < threshold))

    if quality == "no_cover":
        return stmt.where((Document.cover_path.is_(None)) | (Document.cover_path == ""))

    if quality == "unclassified":
        return stmt.where(Document.collection_id.is_(None))

    if quality == "language_variant":
        return stmt.where(
            (Document.language.isnot(None)) & (~Document.language.in_(("zh", "en", "ja", "und", "")))
        )

    if quality == "needs_ocr":
        return stmt.where(DocumentQuality.needs_ocr.is_(True))

    if quality == "missing_file":
        return stmt.where(DocumentQuality.file_exists.is_(False))

    if quality == "duplicate":
        return stmt.where(DocumentQuality.duplicate_group_size > 1)

    if quality == "not_audited":
        return stmt.where(DocumentQuality.document_id.is_(None))

    return stmt


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
    db=None,
    include_deleted: bool = False,
    type: str | None = None,
    types: str | None = None,
    collection_id: uuid.UUID | None = None,
    tag: str | None = None,
    q: str | None = None,
    author: str | None = None,
    status: str | None = None,
    quality: str | None = None,
    format: str | None = None,
    language: str | None = None,
):
    """列表、计数与分面共用同一套过滤条件，避免两处条件漂移导致"显示 N 本、翻页却少几本"。"""
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
    # format 是文件格式（epub/pdf/…），与 type（文档类型）不是一回事；库里的 format 存小写
    if format:
        stmt = stmt.where(Document.format == format.strip().lower())
    # language 按归一化值匹配：language=en 要能命中 en-US、ENG
    if language:
        stmt = stmt.where(language_norm_expr(Document.language) == normalize_language_code(language))
    if quality:
        if db is None:
            raise HTTPException(status_code=500, detail="quality 筛选需要 db session")
        stmt = apply_quality_filter(stmt, db, quality)
    return apply_status_filter(stmt, status)
