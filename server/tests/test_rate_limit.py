"""限流中间件单元测试。

此前限流为测试盲区：documents/clip 均启用了 enforce_rate_limit，
但整个测试套件没有任何 429 断言。这里直接对中间件做单元级验证。
"""

from collections import deque

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.middleware import rate_limit
from app.middleware.rate_limit import enforce_rate_limit


def _request(path: str, host: str = "203.0.113.7") -> Request:
    scope = {
        "type": "http",
        "scheme": "http",
        "server": ("server", 80),
        "client": (host, 51234),
        "method": "GET",
        "path": path,
        "headers": [],
        "query_string": b"",
    }
    return Request(scope)


@pytest.fixture(autouse=True)
def _clean_buckets():
    rate_limit._BUCKETS.clear()
    yield
    rate_limit._BUCKETS.clear()


def test_exceeding_max_requests_raises_429():
    req = _request("/api/documents/upload")
    for _ in range(3):
        enforce_rate_limit(req, max_requests=3, window_seconds=60)
    with pytest.raises(HTTPException) as exc_info:
        enforce_rate_limit(req, max_requests=3, window_seconds=60)
    assert exc_info.value.status_code == 429


def test_buckets_isolated_by_path():
    enforce_rate_limit(_request("/a"), max_requests=1, window_seconds=60)
    # 不同路径独立计数：不应触发 429
    enforce_rate_limit(_request("/b"), max_requests=1, window_seconds=60)
    with pytest.raises(HTTPException):
        enforce_rate_limit(_request("/a"), max_requests=1, window_seconds=60)


def test_window_expiry_allows_again(monkeypatch):
    clock = {"now": 1000.0}

    class FakeTime:
        @staticmethod
        def monotonic():
            return clock["now"]

    monkeypatch.setattr(rate_limit, "time", FakeTime)
    req = _request("/c")
    enforce_rate_limit(req, max_requests=1, window_seconds=10)
    with pytest.raises(HTTPException):
        enforce_rate_limit(req, max_requests=1, window_seconds=10)
    clock["now"] += 11  # 越过窗口：旧记录滑出，重新放行
    enforce_rate_limit(req, max_requests=1, window_seconds=10)


def test_empty_buckets_pruned_when_over_cap(monkeypatch):
    monkeypatch.setattr(rate_limit, "_MAX_BUCKETS", 2)
    # 预置一个空桶（模拟已过期的历史路径）
    rate_limit._BUCKETS["203.0.113.7:/stale"] = rate_limit._Bucket(deque())

    enforce_rate_limit(_request("/x"), max_requests=5, window_seconds=60)
    enforce_rate_limit(_request("/y"), max_requests=5, window_seconds=60)

    assert all(k != "203.0.113.7:/stale" for k in rate_limit._BUCKETS)


def test_testclient_bypassed():
    # TestClient 场景不参与限流（与现有测试套件兼容的前提）
    req = _request("/z", host="testclient")
    for _ in range(10):
        enforce_rate_limit(req, max_requests=1, window_seconds=60)
