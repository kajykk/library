"""SM-2 lite 间隔复习调度器（B4-02）。纯函数，不依赖 IO。"""

from datetime import datetime, timedelta, timezone


def as_utc_naive(dt: datetime | None) -> datetime | None:
    """tz-aware → naive UTC（SQLite 落库的是 naive UTC 文本）。"""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def from_utc_naive(dt: datetime | None) -> datetime | None:
    """naive UTC → tz-aware UTC。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def sm2_lite(
    *,
    interval_days: int,
    ease: float,
    repetitions: int,
    lapses: int,
    rating: int,  # 1=重来 2=困难 3=一般 4=简单
    now: datetime,
) -> tuple[int, float, int, int, datetime]:
    """计算下一次复习参数。

    返回 (interval_days, ease, repetitions, lapses, due_at)。
    `now` 由调用方注入，保证与落库时间、预览基准一致。
    """
    if rating < 2:
        # 重来
        return 0, max(1.3, ease - 0.2), 0, lapses + 1, now + timedelta(minutes=10)

    if rating == 2:
        # 困难
        new_interval = max(1, int(interval_days * 1.2))
        new_ease = max(1.3, ease - 0.15)
    elif rating == 3:
        # 一般
        if repetitions == 0:
            new_interval = 1
        elif repetitions == 1:
            new_interval = 3
        else:
            new_interval = max(1, int(interval_days * ease))
        new_ease = ease
    else:
        # 简单
        if repetitions == 0:
            new_interval = 4
        else:
            new_interval = max(1, int(interval_days * ease * 1.3))
        new_ease = min(2.8, ease + 0.15)

    due = now + timedelta(days=new_interval)
    return new_interval, new_ease, repetitions + 1, lapses, due
