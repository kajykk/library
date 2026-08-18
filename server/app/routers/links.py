"""links 双向链接 + 知识图谱数据"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Document, Link
from ..schemas import GraphEdge, GraphNode, GraphOut, LinkCreate, LinkOut

router = APIRouter(prefix="/links", tags=["links"])


@router.get("", response_model=list[LinkOut])
def list_links(source_id: UUID | None = None, target_id: UUID | None = None, db: Session = Depends(get_db)):
    stmt = select(Link)
    if source_id:
        stmt = stmt.where(Link.source_id == source_id)
    if target_id:
        stmt = stmt.where(Link.target_id == target_id)
    return list(db.scalars(stmt))


@router.post("", response_model=LinkOut, status_code=201)
def create_link(payload: LinkCreate, db: Session = Depends(get_db)):
    if payload.source_id == payload.target_id:
        raise HTTPException(status_code=400, detail="Self link is not allowed")
    for doc_id in (payload.source_id, payload.target_id):
        if db.get(Document, doc_id) is None:
            raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    existing = db.scalar(
        select(Link).where(
            Link.source_id == payload.source_id,
            Link.target_id == payload.target_id,
            Link.type == payload.type,
        )
    )
    if existing:
        return existing
    obj = Link(**payload.model_dump())
    db.add(obj)
    db.commit()
    return obj


@router.delete("", status_code=204)
def delete_link(source_id: UUID, target_id: UUID, type: str, db: Session = Depends(get_db)):
    obj = db.scalar(
        select(Link).where(
            Link.source_id == source_id,
            Link.target_id == target_id,
            Link.type == type,
        )
    )
    if obj is None:
        raise HTTPException(status_code=404, detail="Link not found")
    db.delete(obj)
    db.commit()


@router.get("/graph", response_model=GraphOut)
def get_graph(db: Session = Depends(get_db)):
    """图谱数据：全部未删除文档为节点，links 为边"""
    docs = list(db.scalars(select(Document).where(Document.deleted_at.is_(None))))
    links = list(db.scalars(select(Link)))
    return GraphOut(
        nodes=[GraphNode(id=d.id, title=d.title, type=d.type) for d in docs],
        edges=[GraphEdge(source=l.source_id, target=l.target_id, type=l.type) for l in links],
    )
