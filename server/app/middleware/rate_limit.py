from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# 分级阈值：(max_requests, window_seconds)
# 单用户 NAS 部署，阈值偏宽松以避免误伤正常浏览；重操作（外部抓取/全库备份/
# 导出/迁移）才是真正需要拦的：它们要么出网、要么把整库读进内存。
READ_POLICY = (600, 60)
WRITE_POLICY = (120, 60)
HEAVY_POLICY = (20, 60)

# 重操作路径前缀：命中即用 HEAVY_POLICY（优先于读写分级）
_HEAVY_PATH_PREFIXES = (
    "/api/backup",
    "/api/export",
    "/api/migrate",
    "/api/search",
    "/api/stats",
    "/api/documents/metadata/search",  # 每次两个外部 HTTP
    "/api/documents/reindex-content",
)

# 需要比普通写更严格的具体路径（契约第 10 节）
_STRICT_PATH_POLICIES: dict[str, tuple[int, int]] = {
    "/api/documents/upload": (50, 60),
    "/api/clip": (10, 60),
    # CPU 密集：审计、扫描、导入执行、正文重抽取
    "/api/quality/audit": (2, 60),
    "/api/imports/scan": (2, 60),
    "/api/imports/execute": (2, 60),
    "/api/documents/reextract": (2, 60),
}


@dataclass
class _Bucket:
    timestamps: deque[float]


_BUCKETS: dict[str, _Bucket] = defaultdict(lambda: _Bucket(deque()))

# 桶总数上限：key 含 path，被扫描时可产生大量桶；超限后清理空桶防泄漏
_MAX_BUCKETS = 10_000


def _client_key(request: Request) -> str:
    client = request.client.host if request.client else "unknown"
    return f"{client}:{request.url.path}"


def _policy_for(request: Request) -> tuple[int, int] | None:
    """按路径挑分级策略；返回 None 表示不限流（如健康检查）。"""
    path = request.url.path
    if path == "/api/health":
        return None  # 容器 healthcheck 高频探活，不能限
    if path in _STRICT_PATH_POLICIES:
        return _STRICT_PATH_POLICIES[path]
    if any(path.startswith(prefix) for prefix in _HEAVY_PATH_PREFIXES):
        return HEAVY_POLICY
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return READ_POLICY
    return WRITE_POLICY


class RateLimitMiddleware(BaseHTTPMiddleware):
    """全局限流中间件：按路径/方法分级，替代此前"只在 upload/clip 手工调用"的散落实现。

    此前 README 把「限流」列为功能，但只有两个端点真的生效 —— 搜索、统计、备份生成、
    恢复、导出、迁移、在线元数据全部裸奔。这里统一拦截。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        policy = _policy_for(request)
        if policy is not None:
            try:
                enforce_rate_limit(request, max_requests=policy[0], window_seconds=policy[1])
            except HTTPException as exc:
                if exc.status_code != status.HTTP_429_TOO_MANY_REQUESTS:
                    raise
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": "Rate limit exceeded"},
                    headers={"Retry-After": str(policy[1])},
                )
        return await call_next(request)


def enforce_rate_limit(request: Request, max_requests: int, window_seconds: int) -> None:
    """Simple in-memory sliding-window limiter for single-user NAS deployment."""
    if (request.client and request.client.host in {"testclient", "testserver"}) or request.url.hostname == "testserver":
        return
    key = _client_key(request)
    now = time.monotonic()
    bucket = _BUCKETS[key]
    while bucket.timestamps and now - bucket.timestamps[0] > window_seconds:
        bucket.timestamps.popleft()
    if len(_BUCKETS) > _MAX_BUCKETS:
        for stale_key in [k for k, b in _BUCKETS.items() if not b.timestamps]:
            del _BUCKETS[stale_key]
    if len(bucket.timestamps) >= max_requests:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
        )
    bucket.timestamps.append(now)
