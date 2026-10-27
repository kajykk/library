"""sm2_lite 纯函数测试（RW-602：now 由调用方注入）。"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.review_scheduler import sm2_lite
from app.services.review_service import preview_from_state

BASE = datetime(2026, 1, 15, 8, 30, tzinfo=timezone.utc)


def _call(**overrides):
    params = {
        "interval_days": 10,
        "ease": 2.5,
        "repetitions": 3,
        "lapses": 1,
        "rating": 3,
        "now": BASE,
    }
    params.update(overrides)
    return sm2_lite(**params)


def test_now_is_caller_supplied_not_wall_clock():
    """到期时间必须从注入的 now 推出来，不能用 datetime.now()。"""
    _, _, _, _, due = _call(rating=3)
    assert due == BASE + timedelta(days=25)


def test_again_resets_repetitions_and_schedules_ten_minutes():
    interval, ease, repetitions, lapses, due = _call(rating=1)
    assert interval == 0
    assert ease == pytest.approx(2.3)
    assert repetitions == 0
    assert lapses == 2
    assert due == BASE + timedelta(minutes=10)


def test_again_ease_floor_is_1_3():
    _, ease, _, _, _ = _call(rating=1, ease=1.35)
    assert ease == pytest.approx(1.3)


def test_hard_grows_interval_by_1_2_and_reduces_ease():
    interval, ease, repetitions, lapses, due = _call(rating=2)
    assert interval == 12
    assert ease == pytest.approx(2.35)
    assert repetitions == 4
    assert lapses == 1
    assert due == BASE + timedelta(days=12)


def test_good_first_two_repetitions_use_fixed_steps():
    assert _call(rating=3, repetitions=0, interval_days=0)[0] == 1
    assert _call(rating=3, repetitions=1, interval_days=1)[0] == 3


def test_good_later_repetitions_multiply_by_ease():
    interval, ease, repetitions, _, _ = _call(rating=3, interval_days=10, ease=2.5)
    assert interval == 25
    assert ease == pytest.approx(2.5)
    assert repetitions == 4


def test_easy_first_repetition_is_four_days():
    interval, ease, repetitions, _, due = _call(rating=4, repetitions=0, interval_days=0)
    assert interval == 4
    assert ease == pytest.approx(2.65)
    assert repetitions == 1
    assert due == BASE + timedelta(days=4)


def test_easy_later_repetitions_get_1_3_bonus_and_ease_cap():
    interval, ease, _, _, _ = _call(rating=4, interval_days=10, ease=2.7)
    assert interval == 35
    assert ease == pytest.approx(2.8)


def test_preview_uses_same_injected_now():
    preview = preview_from_state(
        interval_days=10,
        ease=2.5,
        repetitions=3,
        lapses=0,
        now=BASE,
    )
    assert preview == {"again": "10 分钟", "hard": "12 天", "good": "25 天", "easy": "32 天"}
