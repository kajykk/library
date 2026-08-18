import logging
from contextlib import asynccontextmanager

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
        return {"status": "ok"}

    return app


app = create_app()
