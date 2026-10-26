"""后台重提取正文任务（RW-401，契约第 5 节）。

POST /api/documents/reextract 启动后台线程；GET /api/documents/reextract-status
返回 JobState 快照，另加 updated/skipped/empty/failed/current_title。

写入规则：
- 提取结果为空时绝不覆盖旧正文（empty）。
- overwrite=only_if_longer 时只有新文本更长才写；always 时非空且不同即写。
- 每 20 本提交一次；成功文档写一条 kind=reextract、undoable=false 的日志，
  before/after 只记成功文档的实际字数（与 scripts/reextract_content.py 同构）。
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import Document
from . import content_extract, file_storage
from .job_state import JobState
from .operation_log import record

logger = logging.getLogger("kb.reextract_job")

BATCH_SIZE = 20
OVERWRITE_MODES = ("only_if_longer", "always")

_job = JobState("reextract")
_lock = threading.Lock()
_extras: dict = {
    "updated": 0,
    "skipped": 0,
    "empty": 0,
    "failed": 0,
    "current_title": None,
}


def _fresh_extras() -> dict:
    return {"updated": 0, "skipped": 0, "empty": 0, "failed": 0, "current_title": None}


def status() -> dict:
    with _lock:
        extras = dict(_extras)
        snapshot = _job.snapshot()
    return {**snapshot, **extras}


def _set(**fields) -> None:
    with _lock:
        _extras.update(fields)


def start(
    ids: list[uuid.UUID],
    timeout_seconds: float = 30,
    overwrite: str = "only_if_longer",
) -> bool:
    """启动后台任务；已在运行返回 False（单飞）。"""
    with _lock:
        if not _job.start(total=len(ids)):
            return False
        _extras.clear()
        _extras.update(_fresh_extras())
    threading.Thread(
        target=_run,
        args=(SessionLocal, list(ids), timeout_seconds, overwrite),
        daemon=True,
    ).start()
    return True


def _content_chars(value: str | None) -> int:
    """与质量中心/脚本一致的 trim 口径（NUL 视为终止符）。"""
    return len((value or "").split("\x00", 1)[0].strip(" "))


def _process_doc(
    doc: Document,
    timeout_seconds: float,
    overwrite: str,
    before_docs: dict,
    after_docs: dict,
) -> str:
    """处理单本，返回 updated / skipped / empty / failed 之一。"""
    fmt = (doc.format or "").lower()
    if not doc.file_path or fmt not in content_extract.TEXT_FORMATS:
        return "skipped"

    try:
        path = file_storage.abs_path(doc.file_path)
        if not path.is_file():
            return "failed"
        text = content_extract.extract_text_from_file_with_budget(
            fmt, path, timeout_seconds
        )
    except Exception as exc:
        logger.warning("重提取失败 %s: %s", doc.id, exc)
        return "failed"

    if not text:
        return "empty"
    old_chars = _content_chars(doc.content)
    if overwrite == "only_if_longer":
        if len(text) <= old_chars:
            return "skipped"
    elif text == (doc.content or ""):
        return "skipped"

    before_docs[str(doc.id)] = {"content_chars": old_chars}
    doc.content = text
    after_docs[str(doc.id)] = {"content_chars": len(text)}
    return "updated"


def _execute(
    db: Session,
    ids: list[uuid.UUID],
    timeout_seconds: float,
    overwrite: str,
    progress: Callable[[int, dict, str], None],
) -> dict:
    stats = {"updated": 0, "skipped": 0, "empty": 0, "failed": 0}
    before_docs: dict[str, dict] = {}
    after_docs: dict[str, dict] = {}
    done = 0

    for start in range(0, len(ids), BATCH_SIZE):
        chunk = ids[start : start + BATCH_SIZE]
        docs = {
            doc.id: doc
            for doc in db.scalars(
                select(Document).where(
                    Document.id.in_(chunk), Document.deleted_at.is_(None)
                )
            ).all()
        }
        for doc_id in chunk:
            done += 1
            doc = docs.get(doc_id)
            title = doc.title if doc is not None else str(doc_id)
            if doc is None:
                stats["failed"] += 1
            else:
                stats[_process_doc(doc, timeout_seconds, overwrite, before_docs, after_docs)] += 1
            progress(done, stats, title)
        db.commit()
        db.expunge_all()

    if stats["updated"]:
        record(
            db,
            kind="reextract",
            document_ids=[uuid.UUID(i) for i in after_docs],
            before_state={"docs": before_docs},
            after_state={"docs": after_docs},
            summary=(
                f"重提取正文：{stats['updated']} 本更新，{stats['skipped']} 本跳过，"
                f"{stats['empty']} 本空结果，{stats['failed']} 本失败（API reextract）"
            ),
            undoable=False,
        )
        db.commit()

    return stats


def _progress(done: int, stats: dict, title: str) -> None:
    with _lock:
        _extras.update(
            updated=stats["updated"],
            skipped=stats["skipped"],
            empty=stats["empty"],
            failed=stats["failed"],
            current_title=title,
        )
    _job.update(done=done)


def _run(
    session_factory,
    ids: list[uuid.UUID],
    timeout_seconds: float,
    overwrite: str,
) -> None:
    db = session_factory()
    try:
        stats = _execute(db, ids, timeout_seconds, overwrite, _progress)
        _job.update(done=len(ids))
        _job.finish()
        logger.info(
            "重提取完成: updated=%s skipped=%s empty=%s failed=%s",
            stats["updated"],
            stats["skipped"],
            stats["empty"],
            stats["failed"],
        )
    except Exception as exc:
        logger.exception("重提取任务失败")
        db.rollback()
        _job.fail(str(exc)[:500])
    finally:
        db.close()
        _set(current_title=None)
