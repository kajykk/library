from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _build_engine():
    settings = get_settings()
    kwargs: dict = {}
    if settings.database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(settings.database_url, **kwargs)


engine = _build_engine()


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _record):
    """SQLite 连接级 PRAGMA：外键强制 / WAL / 写锁等待。

    缺少 foreign_keys=ON 时所有 ondelete/cascade 的 DB 级联均不生效，
    会产生悬挂引用与孤儿行；WAL + busy_timeout 缓解后台重索引与
    请求并发写时的 "database is locked"。仅对 SQLite 生效。
    """
    if not engine.dialect.name == "sqlite":
        return
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
