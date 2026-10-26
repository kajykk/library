"""阅读队列与间隔复习 API（B4-03/04）。"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Annotation, AnnotationReview, Document, ReadingQueueItem, utcnow
from ..services.review_scheduler import sm2_lite, as_utc_naive, from_utc_naive

router = APIRouter(tags=["review"])


# ---- 请求/响应 ----

class QueueAddRequest(BaseModel):
    document_id: str
    priority: int = 100
    note: str = ""


class QueueUpdateRequest(BaseModel):
    priority: int | None = None
    note: str | None = None
    status: str | None = None


class QueueReorderRequest(BaseModel):
    items: list[dict]  # [{id, sort_order}]


class ReviewRateRequest(BaseModel):
    rating: int  # 1-4


class ReviewSnoozeRequest(BaseModel):
    days: int = 1


class QueueItemOut(BaseModel):
    id: str
    document_id: str
    title: str
    priority: int
    sort_order: int
    status: str
    note: str
    created_at: str | None


class ReviewItemOut(BaseModel):
    annotation_id: str
    document_id: str
    document_title: str
    quote: str
    content: str
    due_at: str | None
    interval_days: int
    ease: float
    repetitions: int
    lapses: int


# ---- 阅读队列 ----

@router.get("/queue", response_model=list[QueueItemOut])
def list_queue(
    status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    stmt = (
        select(ReadingQueueItem, Document.title)
        .join(Document, Document.id == ReadingQueueItem.document_id)
        .order_by(ReadingQueueItem.priority, ReadingQueueItem.sort_order, ReadingQueueItem.created_at)
    )
    if status:
        stmt = stmt.where(ReadingQueueItem.status == status)
    rows = db.execute(stmt).all()
    return [
        QueueItemOut(
            id=str(item.id), document_id=str(item.document_id), title=title,
            priority=item.priority, sort_order=item.sort_order,
            status=item.status, note=item.note,
            created_at=item.created_at.isoformat() if item.created_at else None,
        )
        for item, title in rows
    ]


@router.post("/queue", response_model=QueueItemOut)
def add_to_queue(req: QueueAddRequest, db: Session = Depends(get_db)):
    doc_id = uuid.UUID(req.document_id)
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    existing = db.query(ReadingQueueItem).filter_by(document_id=doc_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="已在队列中")

    item = ReadingQueueItem(
        id=uuid.uuid4(), document_id=doc_id,
        priority=max(0, min(1000, req.priority)),
        note=req.note[:500], status="active",
        created_at=utcnow(), updated_at=utcnow(),
    )
    db.add(item)
    db.commit()
    return QueueItemOut(
        id=str(item.id), document_id=str(item.document_id), title=doc.title,
        priority=item.priority, sort_order=item.sort_order, status=item.status,
        note=item.note, created_at=item.created_at.isoformat(),
    )


@router.put("/queue/{item_id}")
def update_queue_item(item_id: uuid.UUID, req: QueueUpdateRequest, db: Session = Depends(get_db)):
    item = db.get(ReadingQueueItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="队列项不存在")
    if req.priority is not None:
        item.priority = max(0, min(1000, req.priority))
    if req.note is not None:
        item.note = req.note[:500]
    if req.status is not None:
        if req.status not in ("active", "paused", "done"):
            raise HTTPException(status_code=400, detail="无效状态")
        item.status = req.status
    item.updated_at = utcnow()
    db.commit()
    return {"status": "updated"}


@router.delete("/queue/{item_id}")
def remove_from_queue(item_id: uuid.UUID, db: Session = Depends(get_db)):
    item = db.get(ReadingQueueItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="队列项不存在")
    db.delete(item)
    db.commit()
    return {"status": "deleted"}


@router.post("/queue/reorder")
def reorder_queue(req: QueueReorderRequest, db: Session = Depends(get_db)):
    for entry in req.items:
        item = db.get(ReadingQueueItem, uuid.UUID(entry["id"]))
        if item:
            item.sort_order = int(entry["sort_order"])
            item.updated_at = utcnow()
    db.commit()
    return {"status": "reordered"}


# ---- 复习 ----

@router.get("/reviews/due", response_model=list[ReviewItemOut])
def list_due_reviews(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    now = utcnow()
    rows = db.execute(
        select(AnnotationReview, Annotation, Document.title)
        .join(Annotation, Annotation.id == AnnotationReview.annotation_id)
        .join(Document, Document.id == Annotation.document_id)
        .where(
            AnnotationReview.status == "active",
            AnnotationReview.due_at <= now,
            Document.deleted_at.is_(None),
        )
        .order_by(AnnotationReview.due_at)
        .limit(limit)
    ).all()

    return [
        ReviewItemOut(
            annotation_id=str(r.annotation_id),
            document_id=str(ann.document_id),
            document_title=title,
            quote=ann.quote or "",
            content=ann.content or "",
            due_at=r.due_at.isoformat() if r.due_at else None,
            interval_days=r.interval_days,
            ease=r.ease,
            repetitions=r.repetitions,
            lapses=r.lapses,
        )
        for r, ann, title in rows
    ]


@router.post("/reviews/{annotation_id}/rate")
def rate_review(
    annotation_id: uuid.UUID,
    req: ReviewRateRequest,
    db: Session = Depends(get_db),
):
    if not (1 <= req.rating <= 4):
        raise HTTPException(status_code=400, detail="评分范围 1-4")

    review = db.get(AnnotationReview, annotation_id)
    if review is None:
        raise HTTPException(status_code=404, detail="复习项不存在")

    interval, ease, reps, lapses, due = sm2_lite(
        interval_days=review.interval_days,
        ease=review.ease,
        repetitions=review.repetitions,
        lapses=review.lapses,
        rating=req.rating,
    )
    review.interval_days = interval
    review.ease = ease
    review.repetitions = reps
    review.lapses = lapses
    review.due_at = due
    review.last_rated_at = utcnow()
    review.updated_at = utcnow()
    db.commit()

    return {
        "next_due_at": due.isoformat(),
        "interval_days": interval,
        "ease": ease,
    }


@router.post("/reviews/{annotation_id}/snooze")
def snooze_review(
    annotation_id: uuid.UUID,
    req: ReviewSnoozeRequest,
    db: Session = Depends(get_db),
):
    review = db.get(AnnotationReview, annotation_id)
    if review is None:
        raise HTTPException(status_code=404, detail="复习项不存在")
    from datetime import timedelta
    review.due_at = utcnow() + timedelta(days=max(1, req.days))
    review.updated_at = utcnow()
    db.commit()
    return {"next_due_at": review.due_at.isoformat()}


@router.get("/reviews/stats")
def review_stats(db: Session = Depends(get_db)):
    now = utcnow()
    total = db.query(AnnotationReview).filter_by(status="active").count()
    due = db.query(AnnotationReview).filter(
        AnnotationReview.status == "active",
        AnnotationReview.due_at <= now,
    ).count()
    return {"total": total, "due": due}
