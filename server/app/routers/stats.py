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


def _to_utc(dt: datetime) -> datetime:
    """统一到 UTC。

    SQLite 返回 naive datetime（库里存的就是 UTC，见 annotations.py 的写入侧说明），
    PostgreSQL 返回 aware datetime。此前对两者一律 astimezone(utc)，而 astimezone 作用
    在 naive 值上会**按系统本地时区解释**它 —— 在 UTC+8 机器上每条记录都被当成
    本地时间再换算，日期整体偏移 8 小时。naive 必须按 UTC 原样标注，不能换算。
    """
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _build_day_map(records: list[ReadingRecord]) -> dict[str, float]:
    """按 UTC 日聚合阅读分钟数（跨 SQLite/PostgreSQL 一致）"""
    day_map: dict[str, float] = {}
    for record in records:
        start_time = record.start_time
        end_time = record.end_time
        if isinstance(start_time, datetime) and isinstance(end_time, datetime):
            date = _to_utc(start_time).strftime("%Y-%m-%d")
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


# 占位提及扫描的分页批大小：此前一次性 select(Document) 把全库（含每篇正文）
# 载入内存后再在 Python 里跑正则。health 是前端统计面板会周期性拉取的端点。
_HEALTH_SCAN_BATCH = 200
_HEALTH_ITEM_LIMIT = 50


@router.get("/health", response_model=HealthOut)
def get_health(db: Session = Depends(get_db)):
    """知识库健康面板：孤立文档、占位提及（[[x]] 无目标）、未开始阅读的书。

    三项各自在 SQL 侧收敛，不再全表载入 ORM 对象：
    - 孤立/未读：只取 id/title/type 三列并 limit，正文不进内存；
    - 占位提及：必须扫正文，但先用 LIKE '%[[%' 在数据库侧预筛，再按批分页读取。
    """
    alive = Document.deleted_at.is_(None)

    linked_sources = select(Link.source_id)
    linked_targets = select(Link.target_id)
    orphan_rows = db.execute(
        select(Document.id, Document.title, Document.type)
        .where(alive, Document.id.notin_(linked_sources), Document.id.notin_(linked_targets))
        .order_by(Document.updated_at.desc())
        .limit(_HEALTH_ITEM_LIMIT)
    ).all()
    orphans = [
        HealthItem(id=str(row[0]), title=row[1], type=row[2]) for row in orphan_rows
    ]

    unread_rows = db.execute(
        select(Document.id, Document.title, Document.type)
        .where(alive, Document.type.in_(("book", "pdf")), Document.read_progress <= 0)
        .order_by(Document.updated_at.desc())
        .limit(_HEALTH_ITEM_LIMIT)
    ).all()
    unread = [HealthItem(id=str(row[0]), title=row[1], type=row[2]) for row in unread_rows]

    titles = {
        t.strip()
        for (t,) in db.execute(select(Document.title).where(alive)).all()
        if t and t.strip()
    }

    counter: Counter[str] = Counter()
    offset = 0
    while True:
        rows = db.execute(
            select(Document.id, Document.content)
            .where(alive, Document.content.like("%[[%"))
            .order_by(Document.id)  # 稳定排序，避免分页漂移
            .offset(offset)
            .limit(_HEALTH_SCAN_BATCH)
        ).all()
        if not rows:
            break
        for _doc_id, content in rows:
            for mention in set(MENTION_PATTERN.findall(content or "")):
                clean = mention.strip()
                if clean and clean not in titles:
                    counter[clean] += 1
        offset += len(rows)

    placeholders = [
        PlaceholderMention(mention=name, count=count)
        for name, count in counter.most_common(_HEALTH_ITEM_LIMIT)
    ]

    return HealthOut(orphans=orphans, placeholders=placeholders, unread=unread)
