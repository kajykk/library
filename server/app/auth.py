import hmac
import logging

from fastapi import Header, HTTPException, status

from .config import get_settings

logger = logging.getLogger("kb.auth")


def verify_token(x_api_token: str | None = Header(default=None, alias="X-API-Token")) -> None:
    """单用户 API Token 认证（X-API-Token 请求头），constant-time 比较"""
    settings = get_settings()
    if not x_api_token or not hmac.compare_digest(
        x_api_token.encode("utf-8"), settings.api_token.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
        )


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
