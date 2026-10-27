"""限流中间件单元测试。

此前限流为测试盲区：documents/clip 均启用了 enforce_rate_limit，
但整个测试套件没有任何 429 断言。这里直接对中间件做单元级验证。

2026-10-04 补充：限流改为全局中间件后，还要验证"非 TestClient 客户端真的被拦"
—— 否则中间件注册了但被 bypass 逻辑放过，等于没做。
"""

import json
from collections import deque

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.middleware import rate_limit
from app.middleware.rate_limit import (
    HEAVY_POLICY,
    READ_POLICY,
    WRITE_POLICY,
    RateLimitMiddleware,
    _policy_for,
    enforce_rate_limit,
)


def _request(path: str, host: str = "203.0.113.7", method: str = "GET") -> Request:
    scope = {
        "type": "http",
        "scheme": "http",
        "server": ("server", 80),
        "client": (host, 51234),
        "method": method,
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


# ---------- 全局中间件（2026-10-04 新增） ----------

def _dispatch(path: str, method: str = "GET", host: str = "203.0.113.7"):
    """直接驱动中间件：host 用真实客户端地址，绕开 TestClient 的 bypass 分支。

    （TestClient 无法自定义 client 地址，而 bypass 正是"中间件注册了却没生效"的
    典型失效方式，所以这里必须在非 testclient 语义下验证。）
    """
    import asyncio

    async def call_next(_request):
        return JSONResponse({"ok": True})

    middleware = RateLimitMiddleware(lambda scope, receive, send: None)
    return asyncio.run(
        middleware.dispatch(_request(path, host=host, method=method), call_next)
    )


@pytest.fixture()
def _clean():
    rate_limit._BUCKETS.clear()
    yield
    rate_limit._BUCKETS.clear()


def test_middleware_returns_429_for_heavy_path(_clean):
    """真实客户端打重操作路径，超限必须返回 429 且带 Retry-After。"""
    max_requests, window = HEAVY_POLICY
    for _ in range(max_requests):
        assert _dispatch("/api/stats").status_code == 200
    resp = _dispatch("/api/stats")
    assert resp.status_code == 429
    assert json.loads(resp.body)["detail"] == "Rate limit exceeded"
    assert resp.headers["retry-after"] == str(window)


def test_middleware_does_not_limit_health_probe(_clean):
    """/api/health 是容器探活端点，不能被限流。"""
    for _ in range(50):
        assert _dispatch("/api/health").status_code == 200


def test_middleware_counters_are_independent_per_path(_clean):
    max_requests, _window = HEAVY_POLICY
    for _ in range(max_requests):
        assert _dispatch("/api/stats").status_code == 200
    assert _dispatch("/api/stats").status_code == 429
    # 不同路径/方法各自独立计数：重操作桶打满不影响普通读与写
    assert _dispatch("/api/documents", method="GET").status_code == 200
    assert _dispatch("/api/documents", method="POST").status_code == 200


def test_middleware_bypasses_testclient(_clean):
    """testclient 放行必须保留：否则整个现有测试套件会被自己的限流打断。"""
    for _ in range(50):
        assert _dispatch("/api/stats", host="testclient").status_code == 200


def test_policy_for_classifies_paths_and_methods():
    assert _policy_for(_request("/api/health")) is None
    assert _policy_for(_request("/api/search", method="GET")) == HEAVY_POLICY
    assert _policy_for(_request("/api/backup")) == HEAVY_POLICY
    assert _policy_for(_request("/api/export/markdown")) == HEAVY_POLICY
    assert _policy_for(_request("/api/documents", method="GET")) == READ_POLICY
    assert _policy_for(_request("/api/documents", method="POST")) == WRITE_POLICY
    assert _policy_for(_request("/api/documents/upload", method="POST")) == (50, 60)
    assert _policy_for(_request("/api/clip", method="POST")) == (10, 60)


# CPU 密集端点：契约第 10 节要求 2/60s
STRICT_HEAVY_PATHS = (
    ("/api/quality/audit", "POST"),
    ("/api/imports/scan", "GET"),
    ("/api/imports/execute", "POST"),
    ("/api/documents/reextract", "POST"),
)


@pytest.mark.parametrize("path,method", STRICT_HEAVY_PATHS)
def test_policy_for_cpu_heavy_paths_is_2_per_minute(path, method):
    assert _policy_for(_request(path, method=method)) == (2, 60)


@pytest.mark.parametrize("path,method", STRICT_HEAVY_PATHS)
def test_middleware_limits_cpu_heavy_paths(_clean, path, method):
    """第三次请求必须 429；即使 GET /imports/scan 也不能落到宽松的读策略。"""
    assert _dispatch(path, method=method).status_code == 200
    assert _dispatch(path, method=method).status_code == 200
    resp = _dispatch(path, method=method)
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "60"
