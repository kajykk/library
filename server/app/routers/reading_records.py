"""reading_records 阅读时长记录"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Document, ReadingRecord
from ..schemas import ReadingRecordCreate, ReadingRecordOut

router = APIRouter(prefix="/reading-records", tags=["reading-records"])


@router.get("", response_model=list[ReadingRecordOut])
def list_reading_records(document_id: UUID | None = None, db: Session = Depends(get_db)):
    stmt = select(ReadingRecord).order_by(ReadingRecord.start_time.desc())
    if document_id:
        stmt = stmt.where(ReadingRecord.document_id == document_id)
    return list(db.scalars(stmt))


@router.post("", response_model=ReadingRecordOut, status_code=201)
def create_reading_record(payload: ReadingRecordCreate, db: Session = Depends(get_db)):
    document = db.get(Document, payload.document_id)
    if document is None or document.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Document not found")
    obj = ReadingRecord(**payload.model_dump())
    db.add(obj)
    db.commit()
    return obj


@router.delete("/{record_id}", status_code=204)
def delete_reading_record(record_id: UUID, db: Session = Depends(get_db)):
    obj = db.get(ReadingRecord, record_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Record not found")
    db.delete(obj)
    db.commit()
