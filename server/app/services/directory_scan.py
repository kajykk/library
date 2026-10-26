"""目录扫描服务（B2-02）：白名单目录扫描，分类坏文件/重复/可导入文件。

白名单是唯一的路径来源：扫描结果只回传 `dir_id` + `rel_path`，导入时由服务端
重新解析并校验包含关系。客户端永远拿不到、也传不回「服务器上的任意绝对路径」。
"""

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings

SUPPORTED_EXTS = {".epub", ".pdf", ".mobi", ".azw3", ".azw", ".txt"}

# 坏文件判定：0 字节或已知错误页大小
BAD_FILE_SIZES = {0, 143}  # 143 bytes = 常见 HTTP 错误页


class ImportPathError(ValueError):
    """路径不合法（不在白名单内、绝对路径、穿越或符号链接逃逸）。"""


@dataclass
class ScanResult:
    total: int = 0
    importable: list[dict] = field(default_factory=list)
    bad_files: list[dict] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)
    unsupported: list[dict] = field(default_factory=list)
    truncated: bool = False


def _norm(path: str | Path) -> str:
    """解析软链接/`..`/大小写（Windows）后的规范路径字符串。"""
    return os.path.normcase(os.path.realpath(str(path)))


def get_import_dirs() -> list[Path]:
    """白名单导入目录（settings.import_dirs，逗号分隔；未配置时为空）。"""
    raw = get_settings().import_dirs
    if not raw:
        return []
    return [Path(p.strip()) for p in raw.split(",") if p.strip()]


def make_dir_id(path: str | Path) -> str:
    """对白名单目录的稳定 id：客户端只回传它，不回传路径。"""
    return hashlib.sha256(_norm(path).encode("utf-8")).hexdigest()[:12]


def list_import_dirs() -> list[dict]:
    """GET /imports/dirs 的数据源；未配置白名单时返回空数组。"""
    out = []
    for d in get_import_dirs():
        real = _norm(d)
        p = Path(real)
        out.append({
            "id": make_dir_id(d),
            "label": p.name or real,
            "path": real,
            "exists": p.is_dir(),
            "readable": p.is_dir() and os.access(real, os.R_OK),
        })
    return out


def resolve_import_dir(id_or_path: str) -> Path | None:
    """dir_id → 白名单目录；id 不存在或白名单为空时返回 None。"""
    for d in get_import_dirs():
        if make_dir_id(d) == id_or_path:
            return d
    return None


def is_within_whitelist(path: str | Path) -> bool:
    """realpath 后是否仍落在某个白名单目录内（含目录自身）。"""
    real = _norm(path)
    for root in get_import_dirs():
        root_real = _norm(root)
        if real == root_real or Path(real).is_relative_to(Path(root_real)):
            return True
    return False


def validate_rel_path(rel_path: str) -> str:
    """相对路径的形状校验：拒绝空、绝对路径与 `..`，返回正斜杠规范形式。

    不依赖白名单，所以在碰到任何文件系统之前就用它把整批请求体挡掉。
    """
    unified = (rel_path or "").replace("\\", "/")
    if unified.startswith("/"):
        # POSIX 绝对路径与 UNC（\\server\share）归一化后都是前导 /
        raise ImportPathError("absolute_path")
    parts = [p for p in unified.split("/") if p and p != "."]
    if not parts:
        raise ImportPathError("empty_rel_path")
    if any(p == ".." for p in parts):
        raise ImportPathError("path_traversal")
    if len(parts[0]) == 2 and parts[0][1] == ":":
        raise ImportPathError("absolute_path")
    return "/".join(parts)


def resolve_import_path(dir_id: str, rel_path: str) -> Path:
    """把 (dir_id, rel_path) 解析成服务器上的真实文件路径，并做包含校验。

    形状不合法（绝对路径、`..`）由 validate_rel_path 拒绝；解析后逃出白名单的路径
    （符号链接逃逸走的是这一条，因为 realpath 会把链接展开到目标）在这一步拒绝。
    """
    root = resolve_import_dir(dir_id)
    if root is None:
        raise ImportPathError("dir_not_allowed")

    parts = validate_rel_path(rel_path).split("/")
    target = root.joinpath(*parts)
    root_real = _norm(root)
    target_real = _norm(target)
    if target_real != root_real and not Path(target_real).is_relative_to(Path(root_real)):
        raise ImportPathError("escapes_whitelist")
    return Path(target_real)


def scan_directory(dir_path: Path, dir_id: str, max_files: int | None = None) -> ScanResult:
    """扫描单个白名单目录，分类文件。不修改任何文件。

    只回传 `rel_path`（相对该目录、正斜杠），绝对路径不出服务器。
    """
    result = ScanResult()
    if max_files is None:
        max_files = get_settings().import_max_files
    seen_hashes: dict[str, str] = {}  # size+name -> path (简易去重)
    scanned = 0

    root_real = _norm(dir_path)
    if not Path(root_real).is_dir():
        return result

    # os.walk(followlinks=False) + 显式剔除链接目录：白名单里放一个指向 C:\\ 的软链
    # 就能把整块盘扫进结果，rglob 在 3.12 及以前会跟着链接走下去。
    for dirpath, dirnames, filenames in os.walk(root_real, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not os.path.islink(os.path.join(dirpath, d)))
        for name in sorted(filenames):
            f = Path(dirpath) / name
            if f.is_symlink():
                continue
            scanned += 1
            if scanned > max_files:
                result.truncated = True
                return result

            rel = os.path.relpath(str(f), root_real).replace("\\", "/")
            result.total += 1
            ext = f.suffix.lower()
            size = f.stat().st_size
            item = {"dir_id": dir_id, "rel_path": rel, "name": name, "size": size}

            # 坏文件判定
            if size in BAD_FILE_SIZES:
                result.bad_files.append({**item, "reason": "empty_or_error_page"})
                continue

            # 不支持格式
            if ext not in SUPPORTED_EXTS:
                result.unsupported.append({**item, "ext": ext})
                continue

            # 简易重复检测（同名同大小）
            dedup_key = f"{name}:{size}"
            if dedup_key in seen_hashes:
                result.duplicates.append({**item, "duplicate_of": seen_hashes[dedup_key]})
                continue
            seen_hashes[dedup_key] = rel

            result.importable.append({**item, "ext": ext.lstrip(".")})

    return result


def scan_all_import_dirs() -> dict:
    """扫描所有白名单目录，返回合并结果；未配置时各项为空。"""
    dirs = get_import_dirs()
    budget = get_settings().import_max_files
    combined = ScanResult()
    dir_results = {}

    for d in dirs:
        did = make_dir_id(d)
        r = scan_directory(d, did, max_files=budget // max(len(dirs), 1))
        dir_results[did] = {
            "label": Path(_norm(d)).name or did,
            "total": r.total,
            "importable": len(r.importable),
            "bad_files": len(r.bad_files),
            "duplicates": len(r.duplicates),
            "unsupported": len(r.unsupported),
        }
        combined.total += r.total
        combined.importable.extend(r.importable)
        combined.bad_files.extend(r.bad_files)
        combined.duplicates.extend(r.duplicates)
        combined.unsupported.extend(r.unsupported)
        if r.truncated:
            combined.truncated = True

    return {
        "total": combined.total,
        "importable": combined.importable[:500],  # 最多返回 500 条预览
        "bad_files": combined.bad_files,
        "duplicates": combined.duplicates,
        "unsupported": combined.unsupported,
        "truncated": combined.truncated,
        "directories": dir_results,
    }
