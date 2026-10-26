"""目录扫描服务（B2-02）：白名单目录扫描，分类坏文件/重复/可导入文件。"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings

SUPPORTED_EXTS = {".epub", ".pdf", ".mobi", ".azw3", ".azw", ".txt"}

# 坏文件判定：0 字节或已知错误页大小
BAD_FILE_SIZES = {0, 143}  # 143 bytes = 常见 HTTP 错误页


@dataclass
class ScanResult:
    total: int = 0
    importable: list[dict] = field(default_factory=list)
    bad_files: list[dict] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)
    unsupported: list[dict] = field(default_factory=list)
    truncated: bool = False


def get_import_dirs() -> list[Path]:
    """获取白名单导入目录（KB_IMPORT_DIRS 环境变量，逗号分隔）。"""
    raw = os.environ.get("KB_IMPORT_DIRS", "")
    if not raw:
        return []
    return [Path(p.strip()) for p in raw.split(",") if p.strip()]


def scan_directory(dir_path: Path, max_files: int = 20000) -> ScanResult:
    """扫描目录，分类文件。不修改任何文件。"""
    result = ScanResult()
    seen_hashes: dict[str, str] = {}  # size+name -> path (简易去重)
    scanned = 0

    if not dir_path.is_dir():
        return result

    for f in sorted(dir_path.rglob("*")):
        if not f.is_file() or f.is_symlink():
            continue
        scanned += 1
        if scanned > max_files:
            result.truncated = True
            break

        result.total += 1
        ext = f.suffix.lower()
        size = f.stat().st_size

        # 坏文件判定
        if size in BAD_FILE_SIZES:
            result.bad_files.append({"path": str(f), "size": size, "reason": "empty_or_error_page"})
            continue

        # 不支持格式
        if ext not in SUPPORTED_EXTS:
            result.unsupported.append({"path": str(f), "size": size, "ext": ext})
            continue

        # 简易重复检测（同名同大小）
        dedup_key = f"{f.name}:{size}"
        if dedup_key in seen_hashes:
            result.duplicates.append({
                "path": str(f),
                "size": size,
                "duplicate_of": seen_hashes[dedup_key],
            })
            continue
        seen_hashes[dedup_key] = str(f)

        result.importable.append({
            "path": str(f),
            "name": f.name,
            "size": size,
            "ext": ext.lstrip("."),
        })

    return result


def scan_all_import_dirs(max_files: int = 20000) -> dict:
    """扫描所有白名单目录，返回合并结果。"""
    dirs = get_import_dirs()
    combined = ScanResult()
    dir_results = {}

    for d in dirs:
        r = scan_directory(d, max_files=max_files // max(len(dirs), 1))
        dir_results[str(d)] = {
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
