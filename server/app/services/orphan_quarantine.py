"""孤儿文件隔离（RW-103）：只移动勾选的文件，保留目录结构，按 manifest 真正可撤销。

三条硬性口径：
1. 只处理 `files/` 顶层扫描出来的孤儿（`covers/` 暂不隔离）。
2. 执行时重新计算孤儿集合，客户端提交的旧列表不作数。
3. 移动不是删除，每一次移动都先进 manifest，撤销按 manifest 逐条移回。
"""

import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from sqlalchemy.orm import Session

from ..config import get_settings
from .file_storage import abs_path, sha256_of
from .quality_audit import scan_orphans

MAX_ITEMS = 500
# 5 GB：单次隔离的体积上限，超了整批拒绝而不是移到一半停下
MAX_TOTAL_BYTES = 5 * 1024 * 1024 * 1024
MANIFEST_NAME = "manifest.json"


class QuarantineError(Exception):
    """整批拒绝（对应 400）：路径形状非法、超限、目标不可用。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class ManifestItem(TypedDict, total=False):
    rel_path: str
    size: int
    sha256: str
    moved_to: str
    moved_at: str
    status: str
    reason: str


class RestoreResult(TypedDict):
    restored: int
    skipped: list[dict]
    manifest_found: bool


def _root() -> Path:
    return Path(get_settings().data_dir).resolve()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _safe_abs(rel_path: str) -> Path | None:
    """manifest 里的字符串来自磁盘，不可信：逃逸 data 目录的直接判失败，不抛给调用方。"""
    try:
        return abs_path(rel_path)
    except ValueError:
        return None


def _is_symlink(rel_path: str) -> bool:
    """符号链接必须在 resolve 之前发现：abs_path 会把链接解析成目标，届时已看不出它是链接。"""
    return (_root() / rel_path).is_symlink()


def _check_rel_path(rel_path: str) -> str:
    unified = (rel_path or "").replace("\\", "/")
    if unified.startswith("/"):
        # POSIX 绝对路径与 UNC（\\server\share）归一化后都是前导 /
        raise QuarantineError("absolute_path", f"不允许绝对路径: {rel_path}")
    parts = [p for p in unified.split("/") if p and p != "."]
    if not parts:
        raise QuarantineError("empty_rel_path", "rel_paths 不能为空")
    if any(p == ".." for p in parts):
        raise QuarantineError("path_traversal", f"不允许 ..: {rel_path}")
    if parts[0] != "files":
        raise QuarantineError("only_files_dir", f"只允许 files/ 下的相对路径: {rel_path}")
    return "/".join(parts)


def _reserve_dir(parent: Path, stamp: str) -> Path:
    """带 manifest 的目录属于上一次运行，不能被同秒的第二次运行覆盖；没有 manifest 的空目录可以复用。"""
    candidate = parent / stamp
    n = 1
    while (candidate / MANIFEST_NAME).exists():
        n += 1
        candidate = parent / f"{stamp}-{n}"
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def _write_manifest(path: Path, manifest: dict) -> None:
    """先写临时文件再原子 rename：崩溃不能留下半份 manifest，否则撤销无从依据。"""
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def build_plan(db: Session, rel_paths: list[str]) -> list[ManifestItem]:
    """校验勾选 + 重新计算孤儿集合，返回带 planned/skipped 状态的计划；不写任何文件。"""
    unique = list(dict.fromkeys(rel_paths))
    if not unique:
        raise QuarantineError("empty_selection", "rel_paths 不能为空")
    if len(unique) > MAX_ITEMS:
        raise QuarantineError("too_many_items", f"单次最多 {MAX_ITEMS} 个文件")

    checked = [_check_rel_path(p) for p in unique]
    current_orphans = {o["rel_path"] for o in scan_orphans(db)["orphan_files"]}

    plan: list[ManifestItem] = []
    total_bytes = 0
    for rel in checked:
        if _is_symlink(rel):
            raise QuarantineError("symlink", f"不允许符号链接: {rel}")
        src = _safe_abs(rel)
        if src is None:
            raise QuarantineError("escapes_data_dir", f"路径逃逸 data 目录: {rel}")
        if not src.is_file():
            plan.append({"rel_path": rel, "status": "skipped", "reason": "file_not_found"})
            continue
        if rel not in current_orphans:
            # 客户端提交的是旧扫描结果，或该文件仍被文档（含软删除文档）引用
            plan.append({"rel_path": rel, "status": "skipped", "reason": "not_orphan"})
            continue
        size = src.stat().st_size
        total_bytes += size
        plan.append({"rel_path": rel, "size": size, "status": "planned"})

    if total_bytes > MAX_TOTAL_BYTES:
        raise QuarantineError(
            "too_much_data",
            f"单次最多 {MAX_TOTAL_BYTES} 字节，本次 {total_bytes} 字节",
        )
    return plan


def quarantine(db: Session, rel_paths: list[str]) -> dict:
    """执行隔离：manifest 先落盘，再逐个 shutil.move；单个失败不中断整批。"""
    plan = build_plan(db, rel_paths)
    planned = [item for item in plan if item["status"] == "planned"]
    if not planned:
        return _outcome(plan, quarantine_dir=None)

    data_root = _root()
    qdir = _reserve_dir(data_root / "quarantine", _timestamp())
    qdir_rel = qdir.relative_to(data_root).as_posix()

    for item in planned:
        # sha256 必须在移动前取：文件一旦进了隔离目录，撤销时就没法再证明「移回去的是同一个」
        try:
            item["sha256"] = sha256_of(item["rel_path"])
        except OSError as exc:
            item["status"] = "failed"
            item["reason"] = f"hash_failed: {str(exc)[:120]}"
            continue
        # 保留整条相对路径（quarantine/<ts>/files/xxx.pdf），禁止压平成 <ts>/xxx.pdf
        item["moved_to"] = f"{qdir_rel}/{item['rel_path']}"

    manifest = {"created_at": _now_iso(), "quarantine_dir": qdir_rel, "items": plan}
    manifest_path = qdir / MANIFEST_NAME
    _write_manifest(manifest_path, manifest)

    for item in planned:
        if item["status"] != "planned":
            continue
        dst = _safe_abs(item["moved_to"])
        src = _safe_abs(item["rel_path"])
        if dst is None or src is None:
            item["status"] = "failed"
            item["reason"] = "illegal_path"
            continue
        if dst.exists():
            item["status"] = "skipped"
            item["reason"] = "destination_exists"
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            item["status"] = "moved"
            item["moved_at"] = _now_iso()
        except Exception as exc:
            item["status"] = "failed"
            item["reason"] = f"move_failed: {str(exc)[:120]}"

    manifest["completed_at"] = _now_iso()
    _write_manifest(manifest_path, manifest)

    return _outcome(plan, quarantine_dir=qdir_rel)


def _outcome(plan: list[ManifestItem], *, quarantine_dir: str | None) -> dict:
    moved = [item for item in plan if item["status"] == "moved"]
    skipped = [item for item in plan if item["status"] == "skipped"]
    failed = [item for item in plan if item["status"] == "failed"]
    return {
        "moved": len(moved),
        "skipped": len(skipped),
        "failed": len(failed),
        "quarantine_dir": quarantine_dir,
        "results": [
            {
                "rel_path": item["rel_path"],
                "status": item["status"],
                "reason": item.get("reason"),
                "moved_to": item.get("moved_to"),
                "size": item.get("size"),
            }
            for item in plan
        ],
        # 移动映射写进 operation log 的 after_state，日志本身就是可核对的清单
        "moves": {item["rel_path"]: item.get("moved_to") for item in moved},
    }


def restore_from_manifest(quarantine_dir_rel: str) -> RestoreResult:
    """按 manifest 把文件真正移回原相对路径；原位置已被占用则跳过，绝不覆盖。"""
    empty: RestoreResult = {"restored": 0, "skipped": [], "manifest_found": False}
    mpath = _safe_abs(f"{quarantine_dir_rel}/{MANIFEST_NAME}")
    if mpath is None or not mpath.is_file():
        return empty

    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {
            "restored": 0,
            "skipped": [{"rel_path": str(mpath), "reason": f"manifest_unreadable: {str(exc)[:120]}"}],
            "manifest_found": True,
        }

    items = manifest.get("items", [])
    moved_items = [item for item in items if item.get("status") == "moved"]
    restored = 0
    skipped: list[dict] = []

    for item in moved_items:
        rel = item.get("rel_path")
        moved_to = item.get("moved_to")
        if not rel or not moved_to:
            skipped.append({"rel_path": rel or "?", "reason": "manifest_incomplete"})
            continue
        src = _safe_abs(moved_to)
        if src is None or not src.is_file():
            skipped.append({"rel_path": rel, "reason": "missing_from_quarantine"})
            continue
        target = _safe_abs(rel)
        if target is None:
            skipped.append({"rel_path": rel, "reason": "illegal_path"})
            continue
        if target.exists():
            skipped.append({"rel_path": rel, "reason": "original_exists"})
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(target))
            item["status"] = "restored"
            item["restored_at"] = _now_iso()
            restored += 1
        except Exception as exc:
            skipped.append({"rel_path": rel, "reason": f"move_failed: {str(exc)[:120]}"})

    manifest["undone_at"] = _now_iso()
    _write_manifest(mpath, manifest)
    return {"restored": restored, "skipped": skipped, "manifest_found": True}
