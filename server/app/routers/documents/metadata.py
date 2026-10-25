"""在线书籍元数据：候选搜索与应用。"""

import uuid

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...database import get_db
from ...schemas import DocumentOut
from ...services import file_storage, metadata_fetch, url_safety
from ._shared import get_doc_or_404, router


class MetadataCandidate(BaseModel):
    source: str
    title: str
    author: str
    publisher: str
    isbn: str
    description: str
    language: str
    year: str
    cover_url: str | None


@router.get("/metadata/search", response_model=list[MetadataCandidate])
def search_metadata_online(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=6, ge=1, le=12),
):
    """在线搜索书籍元数据候选（编辑弹窗"联网搜索"用）；同步端点由线程池执行"""
    return metadata_fetch.search_online(q, limit)


class MetadataApply(BaseModel):
    title: str | None = None
    author: str | None = None
    publisher: str | None = None
    isbn: str | None = None
    description: str | None = None
    language: str | None = None
    cover_url: str | None = None


@router.patch("/{doc_id}/metadata", response_model=DocumentOut)
def apply_metadata_online(doc_id: uuid.UUID, payload: MetadataApply, db: Session = Depends(get_db)):
    """应用在线元数据候选到指定文档；带 cover_url 时下载封面落盘"""
    doc = get_doc_or_404(db, doc_id)
    for field, value in payload.model_dump(exclude_unset=True, exclude={"cover_url"}).items():
        if value:  # 空串不覆盖本地已填字段
            setattr(doc, field, value)

    if payload.cover_url:
        url_safety.validate_url(payload.cover_url)
        data, ext = metadata_fetch.download_cover(payload.cover_url)
        doc.cover_path = file_storage.save_cover(doc.id, data, ext)

    db.commit()
    db.refresh(doc)
    return doc
