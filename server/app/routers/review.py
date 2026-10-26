"""阅读队列与间隔复习 API（契约第 6/7 节）。"""

import uuid
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Annotation, AnnotationReview, Document, ReadingQueueItem, utcnow
from ..services.review_scheduler import sm2_lite
from ..services.review_service import (
    RATING_VALUES,
    ensure_review_row,
    preview_from_state,
)

router = APIRouter(tags=["review"])

QUEUE_STATUSES = ("active", "paused", "done")


# ---- 请求/响应 ----

class QueueAddRequest(BaseModel):
    document_id: uuid.UUID
    priority: int = 100
    note: str = ""


class QueueUpdateRequest(BaseModel):
    priority: int | None = None
    status: str | None = None
    note: str | None = None


class QueueReorderRequest(BaseModel):
    ordered_ids: list[uuid.UUID]


class QueueDocumentOut(BaseModel):
    id: str
    title: str
    author: str
    format: str | None
    cover_path: str | None
    read_progress: float
    last_read_at: str | None


class QueueItemOut(BaseModel):
    id: str
    document_id: str
    priority: int
    sort_order: int
    status: str
    note: str
    created_at: str | None
    updated_at: str | None
    document: QueueDocumentOut


class QueueListOut(BaseModel):
    total: int
    items: list[QueueItemOut]


class QueueCreateResult(BaseModel):
    created: bool
    item: QueueItemOut | None = None
    skipped: str | None = None


class QueueReorderResult(BaseModel):
    items: list[QueueItemOut]


class ReviewRateRequest(BaseModel):
    rating: Literal["again", "hard", "good", "easy"]


class ReviewSnoozeRequest(BaseModel):
    days: int


class ReviewCardOut(BaseModel):
    annotation_id: str
    document_id: str
    document_title: str
    document_type: str
    type: str
    anchor: dict
    quote: str
    content: str
    created_at: str | None
    due_at: str | None
    interval_days: int
    repetitions: int
    ease: float
    preview: dict[str, str]


class DueReviewsOut(BaseModel):
    total: int
    cards: list[ReviewCardOut]


class ScheduleOut(BaseModel):
    annotation_id: str
    due_at: str | None
    interval_days: int
    ease: float
    repetitions: int
    lapses: int
    next_preview: dict[str, str]


class ReviewStatsOut(BaseModel):
    tz_offset_minutes: int
    due_now: int
    due_today: int
    new_today: int
    reviewed_today: int
    upcoming_7d: int


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _queue_out(item: ReadingQueueItem, doc: Document) -> QueueItemOut:
    return QueueItemOut(
        id=str(item.id),
        document_id=str(item.document_id),
        priority=item.priority,
        sort_order=item.sort_order,
        status=item.status,
        note=item.note,
        created_at=_iso(item.created_at),
        updated_at=_iso(item.updated_at),
        document=QueueDocumentOut(
            id=str(doc.id),
            title=doc.title,
            author=doc.author or "",
            format=doc.format,
            cover_path=doc.cover_path,
            read_progress=doc.read_progress or 0.0,
            last_read_at=_iso(doc.last_read_at),
        ),
    )


def _queue_order():
    return (
        ReadingQueueItem.priority,
        ReadingQueueItem.sort_order,
        ReadingQueueItem.created_at,
        ReadingQueueItem.id,
    )


def _active_queue_rows(db: Session) -> list[tuple[ReadingQueueItem, Document]]:
    return db.execute(
        select(ReadingQueueItem, Document)
        .join(Document, Document.id == ReadingQueueItem.document_id)
        .where(
            ReadingQueueItem.status == "active",
            Document.deleted_at.is_(None),
        )
        .order_by(*_queue_order())
    ).all()


# ---- 阅读队列 ----

@router.get("/reading-queue", response_model=QueueListOut)
def list_queue(
    status: str = Query(default="active"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    if status != "all" and status not in QUEUE_STATUSES:
        raise HTTPException(status_code=400, detail=f"不支持的 status: {status}")
    conditions = [Document.deleted_at.is_(None)]
    if status != "all":
        conditions.append(ReadingQueueItem.status == status)

    total = db.scalar(
        select(func.count())
        .select_from(ReadingQueueItem)
        .join(Document, Document.id == ReadingQueueItem.document_id)
        .where(*conditions)
    ) or 0
    rows = db.execute(
        select(ReadingQueueItem, Document)
        .join(Document, Document.id == ReadingQueueItem.document_id)
        .where(*conditions)
        .order_by(*_queue_order())
        .offset(offset)
        .limit(limit)
    ).all()
    return QueueListOut(total=total, items=[_queue_out(item, doc) for item, doc in rows])


@router.post("/reading-queue", response_model=QueueCreateResult, response_model_exclude_none=True)
def add_to_queue(req: QueueAddRequest, response: Response, db: Session = Depends(get_db)):
    doc = db.get(Document, req.document_id)
    if doc is None or doc.deleted_at is not None:
        raise HTTPException(status_code=404, detail="文档不存在")

    existing = db.query(ReadingQueueItem).filter_by(document_id=req.document_id).first()
    if existing is not None:
        response.status_code = 200
        return QueueCreateResult(created=False, item=_queue_out(existing, doc))

    item = ReadingQueueItem(
        id=uuid.uuid4(),
        document_id=req.document_id,
        priority=max(0, min(1000, req.priority)),
        sort_order=0,
        status="active",
        note=req.note[:500],
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(item)
    db.commit()
    response.status_code = 201
    return QueueCreateResult(created=True, item=_queue_out(item, doc))


@router.patch("/reading-queue/{item_id}", response_model=QueueItemOut)
def update_queue_item(
    item_id: uuid.UUID, req: QueueUpdateRequest, db: Session = Depends(get_db)
):
    item = db.get(ReadingQueueItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="队列项不存在")

    fields = req.model_fields_set
    if not fields:
        raise HTTPException(status_code=400, detail="至少提供一个字段")
    if "priority" in fields and req.priority is not None:
        item.priority = max(0, min(1000, req.priority))
    if "note" in fields and req.note is not None:
        item.note = req.note[:500]
    if "status" in fields:
        if req.status not in QUEUE_STATUSES:
            raise HTTPException(status_code=400, detail="无效状态")
        item.status = req.status
    item.updated_at = utcnow()
    db.commit()

    doc = db.get(Document, item.document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return _queue_out(item, doc)


@router.delete("/reading-queue/{item_id}", status_code=204)
def remove_from_queue(item_id: uuid.UUID, db: Session = Depends(get_db)):
    item = db.get(ReadingQueueItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="队列项不存在")
    db.delete(item)
    db.commit()
    return Response(status_code=204)


@router.post("/reading-queue/reorder", response_model=QueueReorderResult)
def reorder_queue(req: QueueReorderRequest, db: Session = Depends(get_db)):
    if not req.ordered_ids:
        raise HTTPException(status_code=400, detail="ordered_ids 不能为空")
    if len(req.ordered_ids) > 500:
        raise HTTPException(status_code=400, detail="单次最多 500 项")
    if len(set(req.ordered_ids)) != len(req.ordered_ids):
        raise HTTPException(status_code=400, detail="ordered_ids 含重复项")

    items = db.scalars(
        select(ReadingQueueItem).where(ReadingQueueItem.id.in_(req.ordered_ids))
    ).all()
    by_id = {item.id: item for item in items}
    missing = [str(i) for i in req.ordered_ids if i not in by_id]
    if missing:
        raise HTTPException(
            status_code=400,
            detail={"message": "部分队列项不存在", "missing_ids": missing},
        )
    inactive = [
        str(item.id) for item in items if item.status != "active"
    ]
    if inactive:
        raise HTTPException(
            status_code=400,
            detail={"message": "只能重排 active 队列项", "inactive_ids": inactive},
        )

    now = utcnow()
    for index, item_id in enumerate(req.ordered_ids):
        item = by_id[item_id]
        item.sort_order = index
        item.updated_at = now
    db.commit()

    return QueueReorderResult(
        items=[_queue_out(item, doc) for item, doc in _active_queue_rows(db)]
    )


@router.post(
    "/reading-queue/auto-add",
    response_model=QueueCreateResult,
    response_model_exclude_none=True,
)
def auto_add_to_queue(
    req: QueueAddRequest, response: Response, db: Session = Depends(get_db)
):
    """阅读进度上报时的幂等自动入队；重复/不适用一律 200。"""
    response.status_code = 200
    doc = db.get(Document, req.document_id)
    if (
        doc is None
        or doc.deleted_at is not None
        or doc.type not in ("book", "pdf")
        or (doc.read_progress or 0) >= 1
    ):
        return QueueCreateResult(created=False, skipped="not_applicable")

    existing = db.query(ReadingQueueItem).filter_by(document_id=req.document_id).first()
    if existing is not None:
        return QueueCreateResult(created=False, skipped="exists")

    item = ReadingQueueItem(
        id=uuid.uuid4(),
        document_id=req.document_id,
        priority=100,
        sort_order=0,
        status="active",
        note="",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        # 并发重复：document_id UNIQUE 兜底，回查后按 exists 返回而不是 500
        db.rollback()
        existing = db.query(ReadingQueueItem).filter_by(
            document_id=req.document_id
        ).first()
        if existing is not None:
            return QueueCreateResult(created=False, skipped="exists")
        raise HTTPException(status_code=409, detail="队列项冲突，请重试")

    response.status_code = 201
    return QueueCreateResult(created=True, item=_queue_out(item, doc))


@router.get("/reading-queue/next", response_model=QueueItemOut | None)
def next_queue_item(db: Session = Depends(get_db)):
    row = db.execute(
        select(ReadingQueueItem, Document)
        .join(Document, Document.id == ReadingQueueItem.document_id)
        .where(
            ReadingQueueItem.status == "active",
            Document.deleted_at.is_(None),
        )
        .order_by(*_queue_order())
        .limit(1)
    ).first()
    if row is None:
        return Response(status_code=204)
    return _queue_out(row[0], row[1])


# ---- 复习 ----

def _due_conditions(now, document_id: uuid.UUID | None):
    conditions = [
        AnnotationReview.status == "active",
        AnnotationReview.due_at <= now,
        Document.deleted_at.is_(None),
        Annotation.type.in_(("highlight", "note")),
    ]
    if document_id is not None:
        conditions.append(Annotation.document_id == document_id)
    return conditions


def _card_out(review, annotation, document, now) -> ReviewCardOut:
    return ReviewCardOut(
        annotation_id=str(review.annotation_id),
        document_id=str(annotation.document_id),
        document_title=document.title,
        document_type=document.type,
        type=annotation.type,
        anchor=annotation.anchor or {},
        quote=annotation.quote or "",
        content=annotation.content or "",
        created_at=_iso(annotation.created_at),
        due_at=_iso(review.due_at),
        interval_days=review.interval_days,
        repetitions=review.repetitions,
        ease=review.ease,
        preview=preview_from_state(
            interval_days=review.interval_days,
            ease=review.ease,
            repetitions=review.repetitions,
            lapses=review.lapses,
            now=now,
        ),
    )


@router.get("/reviews/due", response_model=DueReviewsOut)
def list_due_reviews(
    limit: int = Query(default=20, ge=1, le=100),
    new_limit: int = Query(default=20, ge=1, le=100),
    document_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
):
    now = utcnow()
    conditions = _due_conditions(now, document_id)
    base = (
        select(AnnotationReview, Annotation, Document)
        .join(Annotation, Annotation.id == AnnotationReview.annotation_id)
        .join(Document, Document.id == Annotation.document_id)
        .where(*conditions)
    )
    total = db.scalar(
        select(func.count())
        .select_from(AnnotationReview)
        .join(Annotation, Annotation.id == AnnotationReview.annotation_id)
        .join(Document, Document.id == Annotation.document_id)
        .where(*conditions)
    ) or 0

    # 新卡优先：interval_days=0 按创建时间升序，单独受 new_limit 限制
    new_rows = db.execute(
        base.where(AnnotationReview.interval_days == 0)
        .order_by(AnnotationReview.created_at.asc(), AnnotationReview.annotation_id.asc())
        .limit(new_limit)
    ).all()
    old_rows = db.execute(
        base.where(AnnotationReview.interval_days > 0)
        .order_by(AnnotationReview.due_at.asc(), AnnotationReview.annotation_id.asc())
        .limit(limit)
    ).all()

    cards = [
        _card_out(review, annotation, document, now)
        for review, annotation, document in [*new_rows, *old_rows]
    ]
    return DueReviewsOut(total=total, cards=cards)


def _schedule_out(review: AnnotationReview, now) -> ScheduleOut:
    return ScheduleOut(
        annotation_id=str(review.annotation_id),
        due_at=_iso(review.due_at),
        interval_days=review.interval_days,
        ease=review.ease,
        repetitions=review.repetitions,
        lapses=review.lapses,
        next_preview=preview_from_state(
            interval_days=review.interval_days,
            ease=review.ease,
            repetitions=review.repetitions,
            lapses=review.lapses,
            now=now,
        ),
    )


@router.post("/reviews/{annotation_id}/rate", response_model=ScheduleOut)
def rate_review(
    annotation_id: uuid.UUID,
    req: ReviewRateRequest,
    db: Session = Depends(get_db),
):
    annotation = db.get(Annotation, annotation_id)
    if annotation is None:
        raise HTTPException(status_code=404, detail="标注不存在")
    document = db.get(Document, annotation.document_id)
    if document is None or document.deleted_at is not None:
        raise HTTPException(status_code=404, detail="文档不存在")

    review = ensure_review_row(db, annotation)
    interval, ease, repetitions, lapses, due = sm2_lite(
        interval_days=review.interval_days,
        ease=review.ease,
        repetitions=review.repetitions,
        lapses=review.lapses,
        rating=RATING_VALUES[req.rating],
    )
    now = utcnow()
    review.interval_days = interval
    review.ease = ease
    review.repetitions = repetitions
    review.lapses = lapses
    review.due_at = due
    review.last_rated_at = now
    review.updated_at = now
    db.commit()
    return _schedule_out(review, now)


@router.post("/reviews/{annotation_id}/snooze", response_model=ScheduleOut)
def snooze_review(
    annotation_id: uuid.UUID,
    req: ReviewSnoozeRequest,
    db: Session = Depends(get_db),
):
    if req.days not in (1, 3, 7):
        raise HTTPException(status_code=400, detail="days 只能是 1/3/7")
    review = db.get(AnnotationReview, annotation_id)
    if review is None:
        raise HTTPException(status_code=404, detail="复习项不存在")

    now = utcnow()
    review.due_at = now + timedelta(days=req.days)
    review.updated_at = now
    db.commit()
    return _schedule_out(review, now)


def _local_day_condition(
    db: Session,
    column,
    offset_minutes: int,
    local_day: str,
    start_utc,
    end_utc,
):
    """本地日界：SQLite 用 `strftime(col, '+480 minutes')`（单位必须是 minutes）。

    PostgreSQL 用等价的 UTC 区间比较，避免在列上套函数导致索引失效。
    """
    if db.get_bind().dialect.name == "sqlite":
        return func.strftime("%Y-%m-%d", column, f"{offset_minutes:+d} minutes") == local_day
    return and_(column >= start_utc, column < end_utc)


@router.get("/reviews/stats", response_model=ReviewStatsOut)
def review_stats(
    tz_offset_minutes: int = Query(default=0, ge=-840, le=840),
    db: Session = Depends(get_db),
):
    now = utcnow()
    offset = timedelta(minutes=tz_offset_minutes)
    local_now = now + offset
    local_day = local_now.date().isoformat()
    day_start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_utc = day_start_local - offset
    day_end_utc = day_start_utc + timedelta(days=1)

    def count_reviews(*conditions) -> int:
        return db.scalar(
            select(func.count()).select_from(AnnotationReview).where(*conditions)
        ) or 0

    due_now = count_reviews(
        AnnotationReview.status == "active",
        AnnotationReview.due_at <= now,
    )
    due_today = count_reviews(
        AnnotationReview.status == "active",
        AnnotationReview.due_at > now,
        AnnotationReview.due_at <= day_end_utc,
    )
    upcoming_7d = count_reviews(
        AnnotationReview.status == "active",
        AnnotationReview.due_at > now,
        AnnotationReview.due_at <= now + timedelta(days=7),
    )
    new_today = db.scalar(
        select(func.count())
        .select_from(Annotation)
        .where(
            Annotation.type.in_(("highlight", "note")),
            _local_day_condition(
                db, Annotation.created_at, tz_offset_minutes, local_day,
                day_start_utc, day_end_utc,
            ),
        )
    ) or 0
    reviewed_today = count_reviews(
        _local_day_condition(
            db, AnnotationReview.last_rated_at, tz_offset_minutes, local_day,
            day_start_utc, day_end_utc,
        )
    )

    return ReviewStatsOut(
        tz_offset_minutes=tz_offset_minutes,
        due_now=due_now,
        due_today=due_today,
        new_today=new_today,
        reviewed_today=reviewed_today,
        upcoming_7d=upcoming_7d,
    )
