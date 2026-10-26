"""筛选结果 id 列表（契约第 2 节）：供书架「全选筛选结果」使用。

GET /documents/ids 会被 crud 的 /{doc_id} 抢先匹配，必须注册在 crud 之前。
"""

import uuid

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...database import get_db
from ...models import Document
from ._shared import router
from .filters import apply_document_filters

IDS_DEFAULT_MAX = 5000
IDS_HARD_MAX = 5000


class DocumentIdsOut(BaseModel):
    ids: list[str]
    total: int
    truncated: bool


@router.get("/ids", response_model=DocumentIdsOut)
def get_document_ids(
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
    max_ids: int = Query(default=IDS_DEFAULT_MAX, ge=1, le=IDS_HARD_MAX, alias="max"),
    db: Session = Depends(get_db),
):
    filters = dict(
        db=db,
        type=type,
        types=types,
        collection_id=collection_id,
        tag=tag,
        q=q,
        author=author,
        status=status,
        quality=quality,
        format=format,
        language=language,
    )
    total = db.scalar(
        apply_document_filters(select(func.count()).select_from(Document), **filters)
    ) or 0
    stmt = (
        apply_document_filters(select(Document.id), **filters)
        .order_by(Document.created_at.desc(), Document.id.desc())
        .limit(max_ids)
    )
    ids = [str(i) for i in db.scalars(stmt).all()]
    return DocumentIdsOut(ids=ids, total=total, truncated=total > len(ids))
