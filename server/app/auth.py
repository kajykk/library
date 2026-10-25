import hmac
import logging
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request, status

from .config import get_settings

logger = logging.getLogger("kb.auth")

# 失败锁定：此前 verify_token 没有失败计数，单个 token 可被无限次暴力枚举。
# 单用户自托管场景下按来源 IP 计数即可（与限流模块同样的内存滑动窗口）。
AUTH_MAX_FAILURES = 20
AUTH_WINDOW_SECONDS = 300

_FAILED: dict[str, deque[float]] = defaultdict(deque)


def _register_failure(client: str) -> None:
    now = time.monotonic()
    bucket = _FAILED[client]
    while bucket and now - bucket[0] > AUTH_WINDOW_SECONDS:
        bucket.popleft()
    bucket.append(now)
    if len(bucket) >= AUTH_MAX_FAILURES:
        logger.warning("来源 %s 认证失败次数过多，暂时锁定", client)


def _is_locked(client: str) -> bool:
    now = time.monotonic()
    bucket = _FAILED.get(client)
    if not bucket:
        return False
    while bucket and now - bucket[0] > AUTH_WINDOW_SECONDS:
        bucket.popleft()
    return len(bucket) >= AUTH_MAX_FAILURES


def verify_token(
    request: Request,
    x_api_token: str | None = Header(default=None, alias="X-API-Token"),
) -> None:
    """单用户 API Token 认证（X-API-Token 请求头），constant-time 比较 + 失败锁定"""
    settings = get_settings()
    client = request.client.host if request.client else "unknown"
    if _is_locked(client):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed authentication attempts",
        )
    if not x_api_token or not hmac.compare_digest(
        x_api_token.encode("utf-8"), settings.api_token.encode("utf-8")
    ):
        _register_failure(client)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
        )
    # 成功即清空该来源的失败记录
    _FAILED.pop(client, None)


def warn_insecure_token() -> None:
    """启动时检测不安全的默认 Token（在 lifespan 中调用）"""
    settings = get_settings()
    if settings.api_token == "change-me":
        logger.warning(
            "⚠️  INSECURE DEFAULT TOKEN DETECTED — rotate immediately "
            "(set KB_API_TOKEN environment variable to a random ≥32-char string)"
        )
    elif len(settings.api_token) < 32:
        logger.warning(
            "⚠️  API TOKEN is shorter than 32 characters — "
            "consider rotating to a stronger token"
        )
