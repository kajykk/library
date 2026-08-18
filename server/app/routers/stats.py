"""统计聚合：文档总量/类型分布、阅读进度、阅读时长、近期趋势、阅读日历热力图、热门标签、作者聚合、健康面板"""

from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Document, DocumentTag, Link, ReadingRecord, Tag
from .documents import MENTION_PATTERN

router = APIRouter(prefix="/stats", tags=["stats"])


class DayStat(BaseModel):
    date: str
    minutes: float


class HeatmapDay(BaseModel):
    date: str
    minutes: float


class TagStat(BaseModel):
    name: str
    count: int


class StatsOut(BaseModel):
    total_documents: int
    by_type: dict[str, int]
    read_started: int
    completed: int
    total_reading_minutes: float
    recent_days: list[DayStat]
    heatmap: list[HeatmapDay]
    top_tags: list[TagStat]


def _build_day_map(records: list[ReadingRecord]) -> dict[str, float]:
    """按 UTC 日聚合阅读分钟数（跨 SQLite/PostgreSQL 一致）"""
    day_map: dict[str, float] = {}
    for record in records:
        start_time = record.start_time
        end_time = record.end_time
        if isinstance(start_time, datetime) and isinstance(end_time, datetime):
            date = start_time.astimezone(timezone.utc).strftime("%Y-%m-%d")
            minutes = max(0.0, (end_time - start_time).total_seconds() / 60)
        else:
            date = str(start_time)
            minutes = 0.0
        day_map[date] = day_map.get(date, 0.0) + minutes
    return day_map


@router.get("", response_model=StatsOut)
def get_stats(db: Session = Depends(get_db)):
    total_documents = db.scalar(
        select(func.count()).select_from(Document).where(Document.deleted_at.is_(None))
    ) or 0

    by_type_rows = db.execute(
        select(Document.type, func.count()).where(Document.deleted_at.is_(None)).group_by(Document.type)
    ).all()
    by_type = {doc_type: count for doc_type, count in by_type_rows}

    read_started = db.scalar(
        select(func.count()).select_from(Document).where(
            Document.deleted_at.is_(None), Document.type.in_(["book", "pdf"]), Document.read_progress > 0
        )
    ) or 0
    completed = db.scalar(
        select(func.count()).select_from(Document).where(
            Document.deleted_at.is_(None), Document.type.in_(["book", "pdf"]), Document.read_progress >= 1.0
        )
    ) or 0

    records = list(db.scalars(select(ReadingRecord)))
    day_map = _build_day_map(records)
    total_reading_minutes = sum(day_map.values())
    recent_days = [
        DayStat(date=date, minutes=round(minutes, 1))
        for date, minutes in sorted(day_map.items(), key=lambda kv: kv[0], reverse=True)[:7]
    ]

    # 阅读日历热力图：最近 365 天（含零阅读日，前端直接渲染）
    today = datetime.now(timezone.utc).date()
    heatmap = [
        HeatmapDay(
            date=(today - timedelta(days=offset)).strftime("%Y-%m-%d"),
            minutes=round(day_map.get((today - timedelta(days=offset)).strftime("%Y-%m-%d"), 0.0), 1),
        )
        for offset in range(364, -1, -1)
    ]

    tag_rows = db.execute(
        select(Tag.name, func.count(DocumentTag.document_id).label("cnt"))
        .join(DocumentTag, DocumentTag.tag_id == Tag.id)
        .group_by(Tag.name)
        .order_by(func.count(DocumentTag.document_id).desc())
        .limit(10)
    ).all()
    top_tags = [TagStat(name=name, count=cnt) for name, cnt in tag_rows]

    return StatsOut(
        total_documents=total_documents,
        by_type=by_type,
        read_started=read_started,
        completed=completed,
        total_reading_minutes=round(float(total_reading_minutes), 1),
        recent_days=recent_days,
        heatmap=heatmap,
        top_tags=top_tags,
    )


class AuthorStat(BaseModel):
    name: str
    count: int


@router.get("/authors", response_model=list[AuthorStat])
def get_authors(db: Session = Depends(get_db)):
    """作者聚合：按作者统计文档数（书籍+笔记），用于属性化检索"""
    rows = db.execute(
        select(Document.author, func.count())
        .where(
            Document.deleted_at.is_(None),
            Document.author.isnot(None),
            Document.author != "",
            Document.author != "未知作者",
        )
        .group_by(Document.author)
        .order_by(func.count().desc(), Document.author)
        .limit(30)
    ).all()
    return [AuthorStat(name=name, count=count) for name, count in rows]


class HealthItem(BaseModel):
    id: str
    title: str
    type: str


class PlaceholderMention(BaseModel):
    mention: str
    count: int


class HealthOut(BaseModel):
    orphans: list[HealthItem]
    placeholders: list[PlaceholderMention]
    unread: list[HealthItem]


@router.get("/health", response_model=HealthOut)
def get_health(db: Session = Depends(get_db)):
    """知识库健康面板：孤立文档、占位提及（[[x]] 无目标）、未开始阅读的书"""
    docs = list(
        db.scalars(
            select(Document)
            .where(Document.deleted_at.is_(None))
            .order_by(Document.updated_at.desc())
        )
    )
    linked = set(db.scalars(select(Link.source_id))) | set(db.scalars(select(Link.target_id)))
    orphans = [
        HealthItem(id=str(d.id), title=d.title, type=d.type)
        for d in docs
        if d.id not in linked
    ][:50]

    titles = {d.title.strip() for d in docs if d.title.strip()}
    counter: Counter[str] = Counter()
    for d in docs:
        mentions = set(MENTION_PATTERN.findall(d.content or ""))
        for mention in mentions:
            clean = mention.strip()
            if clean and clean not in titles:
                counter[clean] += 1
    placeholders = [
        PlaceholderMention(mention=name, count=count)
        for name, count in counter.most_common(50)
    ]

    unread = [
        HealthItem(id=str(d.id), title=d.title, type=d.type)
        for d in docs
        if d.type in ("book", "pdf") and (d.read_progress or 0) <= 0
    ][:50]

    return HealthOut(orphans=orphans, placeholders=placeholders, unread=unread)
