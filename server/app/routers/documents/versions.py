"""文档版本历史路由。"""

import re
import uuid
from datetime import timezone

from fastapi import Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ...database import get_db
from ...models import Document, DocumentVersion, utcnow
from ...schemas import DocumentOut, DocumentVersionOut, DocumentVersionSummary
from ._shared import get_doc_or_404, router
from .insight import sync_mention_links

VERSION_MIN_INTERVAL_SECONDS = 30
VERSION_MAX_PER_DOC = 100


def _snapshot_version(db: Session, doc: Document, title: str, content: str, *, force: bool = False) -> None:
    """保存前快照上一状态：与最新版本内容相同或间隔不足 30 秒则跳过，每文档最多保留 100 版。

    force=True（版本恢复等需要完整撤销链的场景）跳过间隔限制，但仍保留
    内容去重与数量裁剪。
    """
    latest = db.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == doc.id)
        .order_by(DocumentVersion.created_at.desc())
        .limit(1)
    )
    if latest is not None:
        if latest.title == title and latest.content == content:
            return
        if not force:
            created = latest.created_at
            if created.tzinfo is not None:
                created = created.astimezone(timezone.utc).replace(tzinfo=None)
            if (utcnow().replace(tzinfo=None) - created).total_seconds() < VERSION_MIN_INTERVAL_SECONDS:
                return
    db.add(DocumentVersion(document_id=doc.id, title=title, content=content or ""))
    db.flush()
    stale_ids = db.scalars(
        select(DocumentVersion.id)
        .where(DocumentVersion.document_id == doc.id)
        .order_by(DocumentVersion.created_at.desc())
        .offset(VERSION_MAX_PER_DOC)
    ).all()
    if stale_ids:
        db.execute(delete(DocumentVersion).where(DocumentVersion.id.in_(stale_ids)))


@router.get("/{doc_id}/versions", response_model=list[DocumentVersionSummary])
def list_document_versions(doc_id: uuid.UUID, db: Session = Depends(get_db)):
    get_doc_or_404(db, doc_id)
    rows = db.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == doc_id)
        .order_by(DocumentVersion.created_at.desc())
    ).all()
    return [
        DocumentVersionSummary(
            id=v.id,
            title=v.title,
            preview=re.sub(r"\s+", " ", v.content or "")[:80],
            created_at=v.created_at,
        )
        for v in rows
    ]


@router.get("/{doc_id}/versions/{version_id}", response_model=DocumentVersionOut)
def get_document_version(doc_id: uuid.UUID, version_id: uuid.UUID, db: Session = Depends(get_db)):
    get_doc_or_404(db, doc_id)
    version = db.get(DocumentVersion, version_id)
    if version is None or version.document_id != doc_id:
        raise HTTPException(status_code=404, detail="Version not found")
    return version


@router.post("/{doc_id}/versions/{version_id}/restore", response_model=DocumentOut)
def restore_document_version(doc_id: uuid.UUID, version_id: uuid.UUID, db: Session = Depends(get_db)):
    doc = get_doc_or_404(db, doc_id)
    version = db.get(DocumentVersion, version_id)
    if version is None or version.document_id != doc_id:
        raise HTTPException(status_code=404, detail="Version not found")
    # 先快照当前内容（复用统一快照逻辑：内容去重 + 100 版裁剪；force 跳过 30s
    # 间隔以保留完整撤销链）。此前裸 db.add 绕过该逻辑，反复恢复会无限堆积版本。
    _snapshot_version(db, doc, doc.title, doc.content or "", force=True)
    doc.title = version.title
    doc.content = version.content
    sync_mention_links(db, doc)
    db.commit()
    return doc
