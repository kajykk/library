"""重提取正文（RW-401，契约第 5 节）：后台任务 + 状态查询。

GET /documents/reextract-status 是单段静态路由，必须注册在 crud 的 /{doc_id} 之前。
"""

import uuid
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...database import get_db
from ...models import Document
from ...services import reextract_job
from ._shared import router

MAX_IDS = 500


class ReextractRequest(BaseModel):
    ids: list[str]
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    overwrite: Literal["only_if_longer", "always"] = "only_if_longer"
    confirm: bool = False


class ReextractStartResponse(BaseModel):
    status: str
    total: int


class ReextractStatusResponse(BaseModel):
    name: str
    running: bool
    total: int
    done: int
    updated: int
    skipped: int
    empty: int
    failed: int
    current_title: str | None
    error: str | None
    started_at: str | None
    finished_at: str | None


def _parse_ids(ids: list[str]) -> list[uuid.UUID]:
    if not ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    if len(ids) > MAX_IDS:
        raise HTTPException(status_code=400, detail=f"单次最多 {MAX_IDS} 个文档")
    try:
        parsed = [uuid.UUID(i) for i in ids]
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="ids 含非法 UUID")
    return list(dict.fromkeys(parsed))


@router.post("/reextract", response_model=ReextractStartResponse, status_code=202)
def start_reextract(req: ReextractRequest, db: Session = Depends(get_db)):
    if not req.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm: true")
    doc_ids = _parse_ids(req.ids)

    found = set(
        db.scalars(
            select(Document.id).where(
                Document.id.in_(doc_ids), Document.deleted_at.is_(None)
            )
        ).all()
    )
    missing = [str(i) for i in doc_ids if i not in found]
    if missing:
        raise HTTPException(
            status_code=400,
            detail={"message": "部分文档不存在或已删除", "missing_ids": missing},
        )

    if not reextract_job.start(doc_ids, req.timeout_seconds, req.overwrite):
        raise HTTPException(status_code=409, detail="重提取任务已在运行")
    return ReextractStartResponse(status="started", total=len(doc_ids))


@router.get("/reextract-status", response_model=ReextractStatusResponse)
def get_reextract_status():
    return ReextractStatusResponse(**reextract_job.status())
