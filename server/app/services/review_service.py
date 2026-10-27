"""复习行与间隔预览的共用逻辑（B4）。

`ensure_review_row` 同时供 rate 端点（缺行自动建）与标注创建挂钩使用。
"""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..models import Annotation, AnnotationReview, Document, utcnow
from .review_scheduler import sm2_lite

# 契约第 7 节的四档评分 → sm2_lite 的数值档位
RATING_VALUES = {"again": 1, "hard": 2, "good": 3, "easy": 4}

REVIEWABLE_ANNOTATION_TYPES = ("highlight", "note")


def ensure_review_row(db: Session, annotation: Annotation) -> AnnotationReview | None:
    """取复习行；不存在则按初始状态建一条（0010 之前或迁移后新增的标注）。

    书签与已软删除文档的标注不参与复习，返回 None。幂等：已有行原样返回。
    """
    if annotation.type not in REVIEWABLE_ANNOTATION_TYPES:
        return None
    document = db.get(Document, annotation.document_id)
    if document is None or document.deleted_at is not None:
        return None

    review = db.get(AnnotationReview, annotation.id)
    if review is not None:
        return review
    now = utcnow()
    review = AnnotationReview(
        annotation_id=annotation.id,
        due_at=now,
        interval_days=0,
        ease=2.5,
        repetitions=0,
        lapses=0,
        status="active",
        created_at=now,
        updated_at=now,
    )
    db.add(review)
    db.flush()
    return review


def format_interval(delta: timedelta) -> str:
    minutes = round(delta.total_seconds() / 60)
    if minutes < 24 * 60:
        return f"{max(1, minutes)} 分钟"
    return f"{max(1, round(minutes / (24 * 60)))} 天"


def preview_from_state(
    *,
    interval_days: int,
    ease: float,
    repetitions: int,
    lapses: int,
    now: datetime | None = None,
) -> dict[str, str]:
    """按四档评分各算一次下次到期，返回「再次/困难/良好/简单」的间隔文案。"""
    base = now or utcnow()
    preview: dict[str, str] = {}
    for name, value in RATING_VALUES.items():
        _, _, _, _, due = sm2_lite(
            interval_days=interval_days,
            ease=ease,
            repetitions=repetitions,
            lapses=lapses,
            rating=value,
            now=base,
        )
        preview[name] = format_interval(due - base)
    return preview
