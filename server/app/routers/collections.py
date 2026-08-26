"""collections 分类 CRUD"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Collection
from ..schemas import CollectionCreate, CollectionOut, CollectionUpdate

router = APIRouter(prefix="/collections", tags=["collections"])


def _get_or_404(db: Session, collection_id: UUID) -> Collection:
    obj = db.get(Collection, collection_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return obj


@router.get("", response_model=list[CollectionOut])
def list_collections(db: Session = Depends(get_db)):
    return list(db.scalars(select(Collection).order_by(Collection.sort_order, Collection.name)))


@router.post("", response_model=CollectionOut, status_code=201)
def create_collection(payload: CollectionCreate, db: Session = Depends(get_db)):
    exists = db.scalar(select(Collection).where(Collection.name == payload.name))
    if exists is not None:
        raise HTTPException(status_code=409, detail=f"Collection '{payload.name}' already exists")
    obj = Collection(**payload.model_dump())
    db.add(obj)
    try:
        db.commit()
    except IntegrityError:
        # check-then-insert 竞态兜底：并发同名创建时唯一约束冲突 → 409 而非 500
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Collection '{payload.name}' already exists") from None
    return obj


@router.get("/{collection_id}", response_model=CollectionOut)
def get_collection(collection_id: UUID, db: Session = Depends(get_db)):
    return _get_or_404(db, collection_id)


@router.patch("/{collection_id}", response_model=CollectionOut)
def update_collection(collection_id: UUID, payload: CollectionUpdate, db: Session = Depends(get_db)):
    obj = _get_or_404(db, collection_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)

    # 父级环检测：parent_id 指向自身或子孙会使树遍历死循环
    new_parent_id = getattr(obj, "parent_id", None)
    if new_parent_id is not None:
        if new_parent_id == obj.id:
            raise HTTPException(status_code=400, detail="Collection cannot be its own parent")
        visited = {obj.id}
        cursor_id: UUID | None = new_parent_id
        while cursor_id is not None:
            if cursor_id in visited:
                raise HTTPException(
                    status_code=400,
                    detail="parent_id would create a cycle in the collection tree",
                )
            visited.add(cursor_id)
            parent = db.get(Collection, cursor_id)
            cursor_id = parent.parent_id if parent else None

    db.commit()
    return obj


@router.delete("/{collection_id}", status_code=204)
def delete_collection(collection_id: UUID, db: Session = Depends(get_db)):
    obj = _get_or_404(db, collection_id)
    db.delete(obj)  # 文档 collection_id 为 SET NULL（依赖 foreign_keys=ON PRAGMA）
    db.commit()
