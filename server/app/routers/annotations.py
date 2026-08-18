"""annotations 统一批注 API（bookmark / highlight / note）"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Annotation, Document
from ..schemas import AnnotationCreate, AnnotationOut, AnnotationUpdate

router = APIRouter(prefix="/annotations", tags=["annotations"])


class AnnotationSearchHit(BaseModel):
    id: UUID
    document_id: UUID
    document_title: str
    document_type: str
    type: str
    anchor: dict
    quote: str
    content: str
    created_at: datetime


@router.get("/search", response_model=list[AnnotationSearchHit])
def search_annotations(
    q: str = Query(default=""),
    limit: int = Query(default=20, le=50),
    db: Session = Depends(get_db),
):
    """跨书籍检索批注（带书名），用于笔记中插入书籍引用"""
    stmt = (
        select(Annotation, Document.title, Document.type)
        .join(Document, Document.id == Annotation.document_id)
        .where(Document.deleted_at.is_(None))
        .order_by(Annotation.created_at.desc())
        .limit(limit)
    )
    query = q.strip()
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(Annotation.quote.ilike(like), Annotation.content.ilike(like))
        )
    rows = db.execute(stmt).all()
    return [
        AnnotationSearchHit(
            id=ann.id,
            document_id=ann.document_id,
            document_title=doc_title,
            document_type=doc_type,
            type=ann.type,
            anchor=ann.anchor or {},
            quote=ann.quote or "",
            content=ann.content or "",
            created_at=ann.created_at,
        )
        for ann, doc_title, doc_type in rows
    ]


def _get_or_404(db: Session, annotation_id: UUID) -> Annotation:
    obj = db.get(Annotation, annotation_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Annotation not found")
    return obj


@router.get("", response_model=list[AnnotationOut])
def list_annotations(document_id: UUID | None = None, db: Session = Depends(get_db)):
    stmt = select(Annotation).order_by(Annotation.created_at.desc())
    if document_id:
        stmt = stmt.where(Annotation.document_id == document_id)
    return list(db.scalars(stmt))


@router.post("", response_model=AnnotationOut, status_code=201)
def create_annotation(payload: AnnotationCreate, db: Session = Depends(get_db)):
    document = db.get(Document, payload.document_id)
    if document is None or document.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Document not found")
    obj = Annotation(**payload.model_dump())
    db.add(obj)
    db.commit()
    return obj


@router.patch("/{annotation_id}", response_model=AnnotationOut)
def update_annotation(annotation_id: UUID, payload: AnnotationUpdate, db: Session = Depends(get_db)):
    obj = _get_or_404(db, annotation_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)
    db.commit()
    return obj


@router.delete("/{annotation_id}", status_code=204)
def delete_annotation(annotation_id: UUID, db: Session = Depends(get_db)):
    obj = _get_or_404(db, annotation_id)
    db.delete(obj)
    db.commit()
