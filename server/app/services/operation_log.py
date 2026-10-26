"""操作日志与撤销服务（B2-05）。

所有真实数据写入都记录 operation log，支持撤销。
"""

import uuid
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Document, OperationLog, Tag, utcnow
from .orphan_quarantine import restore_from_manifest

MAX_DOCS_PER_OP = 500


class UndoResult(TypedDict):
    status: str
    restored: int
    skipped: list[dict]


def record(
    db: Session,
    *,
    kind: str,
    document_ids: list[uuid.UUID],
    before_state: dict,
    after_state: dict,
    summary: str,
    undoable: bool,
) -> OperationLog:
    """记录操作日志。只 add 不 commit，由调用方在事务中 commit。"""
    unique_ids = list(dict.fromkeys(str(i) for i in document_ids))
    if len(unique_ids) > MAX_DOCS_PER_OP:
        raise ValueError(f"document_ids 超过 {MAX_DOCS_PER_OP} 上限")

    log = OperationLog(
        id=uuid.uuid4(),
        kind=kind,
        status="applied",
        document_ids=unique_ids,
        before_state=before_state,
        after_state=after_state,
        summary=summary[:500],
        undoable=undoable,
        created_at=utcnow(),
    )
    db.add(log)
    return log


def undo(db: Session, operation_id: uuid.UUID) -> UndoResult:
    """撤销一次操作。按 kind 分派到具体恢复逻辑。

    返回状态统一为契约第 4 节的 undone / partial / not_undoable / already_undone。
    """
    log = db.get(OperationLog, operation_id)
    if log is None:
        return {"status": "not_found", "restored": 0, "skipped": []}
    if log.status == "undone":
        return {"status": "already_undone", "restored": 0, "skipped": []}
    if not log.undoable:
        return {"status": "not_undoable", "restored": 0, "skipped": []}

    handler = _UNDO_HANDLERS.get(log.kind)
    if handler is None:
        return {"status": "not_undoable", "restored": 0, "skipped": []}

    result = handler(db, log)
    if result["status"] == "undone":
        log.status = "undone"
        log.undone_at = utcnow()
    return result


def _settle(restored: int, skipped: list[dict]) -> UndoResult:
    """按契约口径收口：有跳过即 partial，全成即可 undone。"""
    status = "partial" if skipped else "undone"
    return {"status": status, "restored": restored, "skipped": skipped}


def _current_field_value(doc: Document, field: str):
    if field == "tags":
        return sorted(tag.name for tag in doc.tags)
    value = getattr(doc, field)
    return str(value) if value is not None else None


def _restore_field(db: Session, doc: Document, field: str, value):
    if field == "tags":
        names = list(value or [])
        tags = db.scalars(select(Tag).where(Tag.name.in_(names))).all() if names else []
        by_name = {tag.name: tag for tag in tags}
        doc.tags = [by_name[name] for name in names if name in by_name]
        return
    if field == "collection_id":
        # before_state 里存的是 JSON 字符串，Uuid 列只接受 uuid.UUID
        doc.collection_id = uuid.UUID(value) if value else None
        return
    setattr(doc, field, value)


def _undo_batch_update(db: Session, log: OperationLog) -> UndoResult:
    """批量修改撤销：比对 after_state，一致才恢复 before_state。"""
    restored = 0
    skipped: list[dict] = []
    after = log.after_state.get("docs", {})
    before = log.before_state.get("docs", {})

    for doc_id_str, before_fields in before.items():
        doc = db.get(Document, uuid.UUID(doc_id_str))
        if doc is None:
            skipped.append({"document_id": doc_id_str, "reason": "document_not_found"})
            continue
        after_fields = after.get(doc_id_str, {})
        # 检查文档当前值是否仍等于 after_state（未被后续修改）
        modified = False
        for field, expected in after_fields.items():
            if _current_field_value(doc, field) != expected:
                modified = True
                break
        if modified:
            skipped.append({"document_id": doc_id_str, "reason": "modified_after_operation"})
            continue
        # 恢复 before_state
        for field, value in before_fields.items():
            _restore_field(db, doc, field, value)
        restored += 1

    return _settle(restored, skipped)


def _undo_batch_soft_delete(db: Session, log: OperationLog) -> UndoResult:
    """软删除撤销：恢复 deleted_at=null。"""
    restored = 0
    skipped: list[dict] = []
    for doc_id_str in log.document_ids:
        doc = db.get(Document, uuid.UUID(doc_id_str))
        if doc is None:
            skipped.append({"document_id": doc_id_str, "reason": "document_not_found"})
            continue
        if doc.deleted_at is None:
            skipped.append({"document_id": doc_id_str, "reason": "not_deleted"})
            continue
        doc.deleted_at = None
        restored += 1
    return _settle(restored, skipped)


def _undo_directory_import(db: Session, log: OperationLog) -> UndoResult:
    """导入撤销：软删除仍存活的导入文档。"""
    restored = 0
    skipped: list[dict] = []
    for doc_id_str in log.document_ids:
        doc = db.get(Document, uuid.UUID(doc_id_str))
        if doc is None:
            skipped.append({"document_id": doc_id_str, "reason": "document_not_found"})
            continue
        if doc.deleted_at is not None:
            skipped.append({"document_id": doc_id_str, "reason": "already_deleted"})
            continue
        doc.deleted_at = utcnow()
        restored += 1
    return _settle(restored, skipped)


def _undo_cover_upload(db: Session, log: OperationLog) -> UndoResult:
    """封面撤销：恢复旧 cover_path。"""
    restored = 0
    skipped: list[dict] = []
    before = log.before_state.get("docs", {})
    for doc_id_str, fields in before.items():
        doc = db.get(Document, uuid.UUID(doc_id_str))
        if doc is None:
            skipped.append({"document_id": doc_id_str, "reason": "document_not_found"})
            continue
        doc.cover_path = fields.get("cover_path")
        restored += 1
    return _settle(restored, skipped)


def _undo_language_normalize(db: Session, log: OperationLog) -> UndoResult:
    """语言归一化撤销：同 batch_update。"""
    return _undo_batch_update(db, log)


def _undo_orphan_quarantine(db: Session, log: OperationLog) -> UndoResult:
    """孤儿隔离撤销：按 manifest 把文件真正移回原相对路径。"""
    quarantine_dir = (log.after_state or {}).get("quarantine_dir")
    if not quarantine_dir:
        return {"status": "not_undoable", "restored": 0, "skipped": []}
    outcome = restore_from_manifest(quarantine_dir)
    if not outcome["manifest_found"]:
        # manifest 丢了就再没有依据判断哪些文件该回哪儿：保持日志 applied，不要伪装成已撤销
        return {"status": "not_undoable", "restored": 0, "skipped": outcome["skipped"]}
    return _settle(outcome["restored"], outcome["skipped"])


_UNDO_HANDLERS = {
    "batch_update": _undo_batch_update,
    "batch_soft_delete": _undo_batch_soft_delete,
    "directory_import": _undo_directory_import,
    "cover_upload": _undo_cover_upload,
    "language_normalize": _undo_language_normalize,
    "orphan_quarantine": _undo_orphan_quarantine,
    "reextract": lambda db, log: {"status": "not_undoable", "restored": 0, "skipped": []},
}
