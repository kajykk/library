"""书架统计与文档计数路由。"""

import uuid

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ...database import get_db
from ...models import Document
from ._shared import router
from .filters import apply_document_filters

# 书架只认这两种 type：笔记（type=note）不进书架，也不计入书架统计
BOOK_TYPES = ("book", "pdf")


class LibraryStats(BaseModel):
    """书架侧栏计数：一次聚合取全，避免前端为了算分类数量去拉整库列表"""

    total: int
    reading: int
    unread: int
    done: int
    # key 为 collection_id；无分类的书籍单独记在 uncategorized
    by_collection: dict[str, int]
    uncategorized: int


class CountOut(BaseModel):
    count: int


@router.get("/stats", response_model=LibraryStats)
def library_stats(db: Session = Depends(get_db)):
    """书架侧栏计数：总数 / 智能书架分桶 / 各分类数量，全部按 type in (book, pdf) 统计"""
    alive = [
        Document.deleted_at.is_(None),
        Document.type.in_(BOOK_TYPES),
    ]

    by_collection: dict[str, int] = {}
    uncategorized = 0
    for collection_id, count in db.execute(
        select(Document.collection_id, func.count()).where(*alive).group_by(Document.collection_id)
    ).all():
        if collection_id is None:
            uncategorized += count
        else:
            by_collection[str(collection_id)] = count

    def count_where(*conditions) -> int:
        return db.scalar(select(func.count()).select_from(Document).where(*alive, *conditions)) or 0

    status_columns = (
        count_where(Document.read_progress > 0, Document.read_progress < 1),
        count_where(or_(Document.read_progress.is_(None), Document.read_progress <= 0)),
        count_where(Document.read_progress >= 1),
    )
    total = sum(by_collection.values()) + uncategorized
    return LibraryStats(
        total=total,
        reading=status_columns[0],
        unread=status_columns[1],
        done=status_columns[2],
        by_collection=by_collection,
        uncategorized=uncategorized,
    )


@router.get("/count", response_model=CountOut)
def count_documents(
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
    include_deleted: bool = False,
    db: Session = Depends(get_db),
):
    """与列表同参的计数：书架用它显示"共 N 本"和剩余量，不必把整库拉下来数"""
    stmt = apply_document_filters(
        select(func.count()).select_from(Document),
        db=db,
        include_deleted=include_deleted,
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
    return CountOut(count=db.scalar(stmt) or 0)
