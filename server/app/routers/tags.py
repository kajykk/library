"""tags 标签"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DocumentTag, Tag
from ..schemas import TagOut

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=list[TagOut])
def list_tags(db: Session = Depends(get_db)):
    return list(db.scalars(select(Tag).order_by(Tag.name)))


@router.delete("/{tag_id}", status_code=204)
def delete_tag(tag_id: UUID, db: Session = Depends(get_db)):
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    db.execute(delete(DocumentTag).where(DocumentTag.tag_id == tag_id))
    db.delete(tag)
    db.commit()
