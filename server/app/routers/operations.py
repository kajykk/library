"""操作日志 API（B2-05）：查看操作历史与撤销。"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import OperationLog
from ..services.operation_log import undo

router = APIRouter(prefix="/operations", tags=["operations"])


class UndoRequest(BaseModel):
    confirm: bool = False


class OperationOut(BaseModel):
    id: str
    kind: str
    status: str
    summary: str
    document_count: int
    undoable: bool
    created_at: str | None
    undone_at: str | None


class OperationDetailOut(OperationOut):
    document_ids: list[str]
    before_state: dict
    after_state: dict


class OperationListOut(BaseModel):
    total: int
    items: list[OperationOut]


class UndoResponse(BaseModel):
    status: str
    restored: int
    skipped: list[dict]


def _to_out(log: OperationLog) -> OperationOut:
    return OperationOut(
        id=str(log.id),
        kind=log.kind,
        status=log.status,
        summary=log.summary,
        document_count=len(log.document_ids or []),
        undoable=log.undoable,
        created_at=log.created_at.isoformat() if log.created_at else None,
        undone_at=log.undone_at.isoformat() if log.undone_at else None,
    )


def _to_detail(log: OperationLog) -> OperationDetailOut:
    return OperationDetailOut(
        **_to_out(log).model_dump(),
        document_ids=[str(i) for i in (log.document_ids or [])],
        before_state=log.before_state or {},
        after_state=log.after_state or {},
    )


@router.get("", response_model=OperationListOut)
def list_operations(
    kind: str | None = None,
    status: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    conditions = []
    if kind:
        conditions.append(OperationLog.kind == kind)
    if status:
        conditions.append(OperationLog.status == status)

    total = db.scalar(
        select(func.count()).select_from(OperationLog).where(*conditions)
    ) or 0
    logs = db.scalars(
        select(OperationLog)
        .where(*conditions)
        .order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return OperationListOut(total=total, items=[_to_out(log) for log in logs])


@router.get("/{operation_id}", response_model=OperationDetailOut)
def get_operation(operation_id: uuid.UUID, db: Session = Depends(get_db)):
    log = db.get(OperationLog, operation_id)
    if log is None:
        raise HTTPException(status_code=404, detail="操作不存在")
    return _to_detail(log)


@router.post("/{operation_id}/undo", response_model=UndoResponse)
def undo_operation(
    operation_id: uuid.UUID,
    req: UndoRequest,
    db: Session = Depends(get_db),
):
    if not req.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm: true")
    result = undo(db, operation_id)
    if result["status"] == "not_found":
        raise HTTPException(status_code=404, detail="操作不存在")
    if result["status"] == "already_undone":
        raise HTTPException(status_code=400, detail="操作已撤销")
    if result["status"] == "not_undoable":
        raise HTTPException(status_code=400, detail="operation is not undoable")
    db.commit()
    return UndoResponse(**result)
