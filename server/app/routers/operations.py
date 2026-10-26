"""操作日志 API（B2-05）：查看操作历史与撤销。"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
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
    document_ids: list[str]
    summary: str
    undoable: bool
    created_at: str | None
    undone_at: str | None


class UndoResponse(BaseModel):
    status: str
    restored: int
    skipped: list[dict]


def _to_out(log: OperationLog) -> OperationOut:
    return OperationOut(
        id=str(log.id),
        kind=log.kind,
        status=log.status,
        document_ids=[str(i) for i in (log.document_ids or [])],
        summary=log.summary,
        undoable=log.undoable,
        created_at=log.created_at.isoformat() if log.created_at else None,
        undone_at=log.undone_at.isoformat() if log.undone_at else None,
    )


@router.get("", response_model=list[OperationOut])
def list_operations(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    logs = db.execute(
        select(OperationLog).order_by(OperationLog.created_at.desc()).offset(offset).limit(limit)
    ).scalars().all()
    return [_to_out(log) for log in logs]


@router.get("/{operation_id}", response_model=OperationOut)
def get_operation(operation_id: uuid.UUID, db: Session = Depends(get_db)):
    log = db.get(OperationLog, operation_id)
    if log is None:
        raise HTTPException(status_code=404, detail="操作不存在")
    return _to_out(log)


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
        raise HTTPException(status_code=400, detail="该操作不可撤销")
    db.commit()
    return UndoResponse(**result)
