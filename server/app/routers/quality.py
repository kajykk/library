"""质量中心 API（B1-03）：只读审计结果 + 触发审计任务。"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import Document, DocumentQuality
from ..services.quality_audit import (
    VALID_ISSUES,
    list_issues,
    scan_orphans,
    start_audit,
    status,
)

router = APIRouter(prefix="/quality", tags=["quality"])


class AuditRequest(BaseModel):
    ids: list[str] | None = None
    full: bool = False


class AuditResponse(BaseModel):
    status: str


class AuditStatusResponse(BaseModel):
    name: str
    running: bool
    total: int
    done: int
    error: str | None
    started_at: str | None
    finished_at: str | None


class IssueItem(BaseModel):
    document_id: str
    title: str
    type: str
    format: str
    language: str
    content_chars: int
    file_size: int
    cover_path: str | None
    collection_id: str | None
    issues: list[str]
    checked_at: str | None


class IssueListResponse(BaseModel):
    total: int
    items: list[IssueItem]


class QualitySummaryResponse(BaseModel):
    total: int
    audited: int
    stale: bool
    last_audited_at: str | None
    min_content_chars: int
    counts: dict[str, int]
    duplicate_groups: int
    orphan_files: int
    orphan_covers: int


class OrphanFile(BaseModel):
    rel_path: str
    size: int


class MissingFile(BaseModel):
    document_id: str
    title: str
    rel_path: str


class OrphansResponse(BaseModel):
    orphan_files: list[OrphanFile]
    orphan_covers: list[OrphanFile]
    missing_files: list[MissingFile]
    total_bytes: int


@router.get("/summary", response_model=QualitySummaryResponse)
def get_quality_summary(db: Session = Depends(get_db)):
    settings = get_settings()
    threshold = settings.quality_min_content_chars

    # 总数：存活 book/pdf
    total = db.execute(
        select(func.count(Document.id)).where(
            Document.deleted_at.is_(None),
            Document.type.in_(("book", "pdf")),
        )
    ).scalar() or 0

    # 已审计：有质量行的存活文档
    audited = db.execute(
        select(func.count(DocumentQuality.document_id))
        .join(Document, Document.id == DocumentQuality.document_id)
        .where(
            Document.deleted_at.is_(None),
            Document.type.in_(("book", "pdf")),
        )
    ).scalar() or 0

    # last_audited_at
    last_audited = db.execute(
        select(func.max(DocumentQuality.checked_at))
    ).scalar()

    # stale：有文档 updated_at > last_audited 或未审计
    if last_audited:
        stale_count = db.execute(
            select(func.count(Document.id))
            .outerjoin(DocumentQuality, DocumentQuality.document_id == Document.id)
            .where(
                Document.deleted_at.is_(None),
                Document.type.in_(("book", "pdf")),
                (DocumentQuality.document_id.is_(None)) | (Document.updated_at > last_audited),
            )
        ).scalar() or 0
        stale = stale_count > 0
    else:
        stale = total > 0

    # 计数：基于 document_quality 快照 + 实时条件
    def _count_quality(col_condition) -> int:
        return db.execute(
            select(func.count(DocumentQuality.document_id))
            .join(Document, Document.id == DocumentQuality.document_id)
            .where(
                Document.deleted_at.is_(None),
                Document.type.in_(("book", "pdf")),
                col_condition,
            )
        ).scalar() or 0

    # 实时条件（不需要质量行）
    def _count_realtime(condition) -> int:
        return db.execute(
            select(func.count(Document.id)).where(
                Document.deleted_at.is_(None),
                Document.type.in_(("book", "pdf")),
                condition,
            )
        ).scalar() or 0

    from ..services.sql_utils import trimmed_content_length_expr
    length_expr = trimmed_content_length_expr(db)

    counts = {
        "empty_content": _count_realtime(length_expr == 0),
        "short_content": _count_realtime((length_expr > 0) & (length_expr < threshold)),
        "no_cover": _count_realtime((Document.cover_path.is_(None)) | (Document.cover_path == "")),
        "unclassified": _count_realtime(Document.collection_id.is_(None)),
        "needs_ocr": _count_quality(DocumentQuality.needs_ocr.is_(True)),
        "missing_file": _count_quality(DocumentQuality.file_exists.is_(False)),
        "duplicate": _count_quality(DocumentQuality.duplicate_group_size > 1),
        "language_variant": _count_realtime(
            (Document.language.isnot(None)) & (~Document.language.in_(("zh", "en", "ja", "und", "")))
        ),
        "orphan_cover": _count_quality(DocumentQuality.cover_exists.is_(False)),
    }

    # duplicate_groups
    duplicate_groups = db.execute(
        select(func.count(func.distinct(Document.sha256)))
        .select_from(Document)
        .where(
            Document.deleted_at.is_(None),
            Document.sha256.isnot(None),
            Document.sha256 != "",
        )
        .group_by(Document.sha256)
        .having(func.count(Document.id) > 1)
        .subquery()
    ).scalar() if False else len(db.execute(
        select(Document.sha256)
        .where(
            Document.deleted_at.is_(None),
            Document.sha256.isnot(None),
            Document.sha256 != "",
        )
        .group_by(Document.sha256)
        .having(func.count(Document.id) > 1)
    ).all())

    return QualitySummaryResponse(
        total=total,
        audited=audited,
        stale=stale,
        last_audited_at=last_audited.isoformat() if last_audited else None,
        min_content_chars=threshold,
        counts=counts,
        duplicate_groups=duplicate_groups,
        orphan_files=0,
        orphan_covers=0,
    )


@router.get("/issues", response_model=IssueListResponse)
def get_quality_issues(
    issue: str = Query(..., description="问题码或 all"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    if issue != "all" and issue not in VALID_ISSUES:
        raise HTTPException(status_code=400, detail=f"未知问题码: {issue}")
    total, items = list_issues(db, issue, limit, offset)
    return IssueListResponse(total=total, items=[IssueItem(**item) for item in items])


@router.get("/orphans", response_model=OrphansResponse)
def get_quality_orphans(db: Session = Depends(get_db)):
    result = scan_orphans(db)
    return OrphansResponse(**result)


@router.post("/audit", response_model=AuditResponse, status_code=202)
def post_quality_audit(req: AuditRequest):
    if req.full and req.ids:
        raise HTTPException(status_code=400, detail="full=true 与 ids 不能同时提供")
    if req.ids and len(req.ids) > 500:
        raise HTTPException(status_code=400, detail="ids 最多 500 个")

    ids = None
    if req.ids and not req.full:
        try:
            ids = [uuid.UUID(i) for i in req.ids]
        except ValueError:
            raise HTTPException(status_code=400, detail="无效的 UUID")

    started = start_audit(ids)
    if not started:
        raise HTTPException(status_code=409, detail="审计任务已在运行")
    return AuditResponse(status="started")


@router.get("/audit-status", response_model=AuditStatusResponse)
def get_audit_status():
    return AuditStatusResponse(**status())
