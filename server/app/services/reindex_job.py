"""后台全文重索引任务：避免同步请求长时间占用连接（真实库可能需要十几分钟）。

POST /api/documents/reindex-content 启动后台线程，GET /api/documents/reindex-status 查询进度。
线程内使用独立 DB Session；每 20 个文档提交一次，失败不影响已处理部分。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import Document
from . import content_extract, file_storage

logger = logging.getLogger("kb.reindex_job")

_lock = threading.Lock()

_state: dict = {
    "running": False,
    "total": 0,
    "done": 0,
    "updated": 0,
    "skipped": 0,
    "empty": 0,
    "sha_backfilled": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
}


def status() -> dict:
    with _lock:
        return dict(_state)


def _set(**kwargs) -> None:
    with _lock:
        _state.update(kwargs)


def start() -> bool:
    """启动后台任务；已在运行则返回 False"""
    with _lock:
        if _state["running"]:
            return False
        _state.update(
            running=True,
            total=0,
            done=0,
            updated=0,
            skipped=0,
            empty=0,
            sha_backfilled=0,
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at=None,
            error=None,
        )
    threading.Thread(target=_run, args=(SessionLocal,), daemon=True).start()
    return True


def run_sync(db: Session) -> dict:
    """同步执行（测试/脚本用）：复用调用方 Session"""
    stats = _do_run(db)
    return {
        "processed": stats["total"],
        "updated": stats["updated"],
        "skipped": stats["skipped"],
        "empty": stats["empty"],
    }


def _run(session_factory) -> None:
    try:
        session = session_factory()
        try:
            stats = _do_run(session)
        finally:
            session.close()
        _set(running=False, finished_at=datetime.now(timezone.utc).isoformat())
        logger.info(
            "重索引完成: processed=%s updated=%s skipped=%s empty=%s sha_backfilled=%s",
            stats["total"],
            stats["updated"],
            stats["skipped"],
            stats["empty"],
            stats["sha_backfilled"],
        )
    except Exception as exc:
        logger.exception("重索引任务失败")
        _set(running=False, error=str(exc), finished_at=datetime.now(timezone.utc).isoformat())


# 分页批大小：此前一次性 select(Document) 把全部待处理文档（含数百 KB 正文）
# 载入内存。改为按批读取 + 批后 expunge，内存占用与批大小成正比而非与库大小成正比。
_REINDEX_BATCH = 100


def _do_run(db: Session) -> dict:
    base_where = (
        Document.deleted_at.is_(None),
        Document.file_path.isnot(None),
    )
    total = db.scalar(
        select(func.count()).select_from(Document).where(*base_where)
    ) or 0
    _set(total=total)
    done = updated = skipped = empty = sha_backfilled = 0

    offset = 0
    while True:
        docs = db.scalars(
            select(Document)
            .where(*base_where)
            .order_by(Document.id)  # 稳定排序，分页不漂移
            .offset(offset)
            .limit(_REINDEX_BATCH)
        ).all()
        if not docs:
            break
        for doc in docs:
            fmt = doc.format or ""
            if fmt in content_extract.TEXT_FORMATS and not doc.content:
                path = file_storage.abs_path(doc.file_path)
                text = content_extract.extract_text_from_file(fmt, path)
                if text and text != (doc.content or ""):
                    doc.content = text
                    updated += 1
                else:
                    empty += 1
            else:
                skipped += 1

            # 顺带为旧文档补齐内容哈希（去重用）
            if not doc.sha256 and doc.file_path:
                try:
                    doc.sha256 = file_storage.sha256_of(doc.file_path)
                    sha_backfilled += 1
                except Exception:
                    pass

            done += 1
        db.commit()
        # 释放本批 ORM 对象（正文已写库，无需再驻留内存）
        db.expunge_all()
        offset += _REINDEX_BATCH
        _set(done=done, updated=updated, skipped=skipped, empty=empty, sha_backfilled=sha_backfilled)

    db.commit()
    _set(done=done, updated=updated, skipped=skipped, empty=empty, sha_backfilled=sha_backfilled)
    return {
        "total": total,
        "updated": updated,
        "skipped": skipped,
        "empty": empty,
        "sha_backfilled": sha_backfilled,
    }
