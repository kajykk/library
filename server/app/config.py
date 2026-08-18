from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """通过 KB_ 前缀环境变量覆盖，例如 KB_DATABASE_URL / KB_API_TOKEN"""

    model_config = SettingsConfigDict(env_prefix="KB_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./knowledge_base.db"
    data_dir: str = "./data"
    api_token: str = "change-me"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    upload_max_size_mb: int = 500
    # 开发环境直接建表；生产走 Alembic 时置 0
    auto_create_tables: bool = True
    # 仅测试/e2e 使用：允许剪藏本机地址（默认关闭，SSRF 防护仍保留 http/https + 主机名校验）
    allow_local_clip: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
