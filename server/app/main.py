import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import verify_token, warn_insecure_token
from .collab import build_collab_asgi
from .config import get_settings
from .database import Base, engine
from .routers import (
    annotations,
    backup,
    clip,
    collections,
    documents,
    export,
    links,
    migrate,
    reading_records,
    search,
    stats,
    tags,
)
from .services.search_index import ensure_fts_index

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    warn_insecure_token()
    if settings.auto_create_tables:
        Base.metadata.create_all(bind=engine)
        ensure_fts_index(engine)
    async with collab_server:
        yield


collab_server, collab_asgi = build_collab_asgi(get_settings().data_dir)


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Personal Knowledge Base API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["X-API-Token", "Content-Type", "Accept"],
    )

    app.include_router(collections.router, prefix="/api", dependencies=[Depends(verify_token)])
    app.include_router(documents.router, prefix="/api", dependencies=[Depends(verify_token)])
    app.include_router(annotations.router, prefix="/api", dependencies=[Depends(verify_token)])
    app.include_router(tags.router, prefix="/api", dependencies=[Depends(verify_token)])
    app.include_router(links.router, prefix="/api", dependencies=[Depends(verify_token)])
    app.include_router(reading_records.router, prefix="/api", dependencies=[Depends(verify_token)])
    app.include_router(migrate.router, prefix="/api", dependencies=[Depends(verify_token)])
    app.include_router(search.router, prefix="/api", dependencies=[Depends(verify_token)])
    app.include_router(stats.router, prefix="/api", dependencies=[Depends(verify_token)])
    app.include_router(backup.router, prefix="/api", dependencies=[Depends(verify_token)])
    app.include_router(clip.router, prefix="/api", dependencies=[Depends(verify_token)])
    app.include_router(export.router, prefix="/api", dependencies=[Depends(verify_token)])

    app.mount("/ws/collab", collab_asgi)

    @app.get("/api/health", tags=["system"])
    def health():
        """系统自检：数据库、FTS 索引、数据目录、协作库可写性（供启动探活与设置页状态卡）"""
        from sqlalchemy import text

        from .database import SessionLocal
        from .services.search_index import FTS_TABLE

        settings = get_settings()

        result: dict[str, object] = {"status": "ok", "checks": {}}

        # 数据库 + FTS 虚拟表
        try:
            db = SessionLocal()
            try:
                db.execute(text("SELECT 1"))
                result["checks"]["database"] = "ok"
                try:
                    db.execute(text(f"SELECT rowid FROM {FTS_TABLE} LIMIT 1"))
                    result["checks"]["fulltext_index"] = "ok"
                except Exception:
                    result["checks"]["fulltext_index"] = "missing"
                    result["status"] = "degraded"
            finally:
                db.close()
        except Exception as exc:
            result["checks"]["database"] = f"error: {exc}"
            result["status"] = "error"

        # 数据目录可写
        try:
            data_dir = Path(settings.data_dir)
            data_dir.mkdir(parents=True, exist_ok=True)
            probe = data_dir / ".health-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            result["checks"]["data_dir"] = "ok"
        except Exception as exc:
            result["checks"]["data_dir"] = f"error: {exc}"
            result["status"] = "error"

        # 协作库可写（与主库同目录，y-websocket 使用）
        try:
            db_path = Path(settings.database_url.replace("sqlite:///", ""))
            collab_probe = db_path.parent / f"{db_path.stem}.collab-probe"
            collab_probe.write_text("ok", encoding="utf-8")
            collab_probe.unlink(missing_ok=True)
            result["checks"]["collab_dir"] = "ok"
        except Exception as exc:
            result["checks"]["collab_dir"] = f"error: {exc}"
            result["status"] = "error"

        return result

    return app


app = create_app()
