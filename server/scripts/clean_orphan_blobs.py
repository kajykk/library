"""孤儿 blob 清理（默认 dry-run，只报告，不删文件）。

识别 data/covers/ 与 data/files/ 中未被任何 document 引用的文件。
--apply 只把孤儿移动到 data/_orphan_quarantine/<timestamp>/，绝不删除；
移动前按 P3 约束备份 DB，并在隔离目录写入 manifest.json。

用法：
    python server/scripts/clean_orphan_blobs.py              # 预览
    python server/scripts/clean_orphan_blobs.py --json       # JSON 输出
    python server/scripts/clean_orphan_blobs.py --apply      # 隔离孤儿（不删除）
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = SERVER_ROOT / "knowledge_base.db"
DATA_DIR = Path(os.getenv("KB_DATA_DIR") or (SERVER_ROOT / "data")).resolve()
SCAN_DIRS = ("covers", "files")


def _connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _backup_db() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    base = DB_PATH.with_name(f"{DB_PATH.name}.bak.{ts}")
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(DB_PATH) + suffix)
        if src.exists():
            shutil.copy2(src, Path(str(base) + suffix))
    if not base.exists() or base.stat().st_size == 0:
        raise RuntimeError(f"数据库备份失败: {base}")
    return base


def _references() -> tuple[dict[str, set[str]], int]:
    """按 covers/files 目录收集引用；无法识别目录的路径保守地计入两边。"""
    refs: dict[str, set[str]] = {name: set() for name in SCAN_DIRS}
    unscoped = 0
    conn = _connect_ro()
    try:
        rows = conn.execute(
            "SELECT file_path, cover_path FROM documents"
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        for value in (row["file_path"], row["cover_path"]):
            if not value:
                continue
            rel = str(value).replace("\\", "/")
            base = os.path.basename(rel)
            top = rel.split("/", 1)[0] if "/" in rel else ""
            if top in refs:
                refs[top].add(base)
            else:
                unscoped += 1
                for name in SCAN_DIRS:
                    refs[name].add(base)
    return refs, unscoped


def _scan(
    refs: dict[str, set[str]],
) -> tuple[dict[str, int], list[dict[str, object]], list[dict[str, object]]]:
    disk: dict[str, int] = {}
    orphans: list[dict[str, object]] = []
    missing: list[dict[str, object]] = []
    for name in SCAN_DIRS:
        directory = DATA_DIR / name
        files = (
            [entry for entry in directory.iterdir() if entry.is_file()]
            if directory.is_dir()
            else []
        )
        disk[name] = len(files)
        disk_names = {entry.name for entry in files}
        for entry in files:
            if entry.name not in refs[name]:
                stat = entry.stat()
                orphans.append(
                    {
                        "dir": name,
                        "file": entry.name,
                        "size": stat.st_size,
                        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(
                            timespec="seconds"
                        ),
                        "path": str(entry),
                    }
                )
        for base in sorted(refs[name] - disk_names):
            missing.append({"dir": name, "file": base})
    orphans.sort(key=lambda item: (str(item["dir"]), str(item["file"])))
    return disk, orphans, missing


def _state() -> dict[str, object]:
    return {
        "main_size": DB_PATH.stat().st_size if DB_PATH.exists() else 0,
        "main_mtime_ns": DB_PATH.stat().st_mtime_ns if DB_PATH.exists() else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="孤儿 blob 清理（默认 dry-run）")
    parser.add_argument("--apply", action="store_true", help="隔离孤儿（默认 dry-run）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"数据库不存在: {DB_PATH}", file=sys.stderr)
        return 1

    refs, unscoped = _references()
    disk, orphans, missing = _scan(refs)
    orphan_bytes = sum(int(item["size"]) for item in orphans)
    before = _state()
    report: dict[str, object] = {
        "mode": "apply" if args.apply else "dry-run",
        "db": str(DB_PATH),
        "data_dir": str(DATA_DIR),
        "referenced_by_dir": {name: len(refs[name]) for name in SCAN_DIRS},
        "referenced_count": sum(len(refs[name]) for name in SCAN_DIRS),
        "unscoped_references": unscoped,
        "disk_by_dir": disk,
        "orphan_count": len(orphans),
        "orphan_bytes": orphan_bytes,
        "orphans": orphans,
        "missing_count": len(missing),
        "missing": missing,
        "applied": False,
        "backup": None,
        "quarantine": None,
        "moved": 0,
        "skipped_existing": 0,
        "remaining_orphans": None,
        "db_state_before": before,
        "db_state_after": None,
    }

    if not args.apply:
        report["db_state_after"] = _state()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        print("# 孤儿 blob 清理（DRY-RUN）")
        print()
        print(f"数据目录: {DATA_DIR}")
        print(f"被引用文件: {report['referenced_count']} "
              f"({', '.join(f'{k}={v}' for k, v in report['referenced_by_dir'].items())})")
        print(
            f"磁盘文件: {sum(disk.values())} "
            f"({', '.join(f'{k}={v}' for k, v in disk.items())})"
        )
        print(f"孤儿文件: {len(orphans)}（{orphan_bytes:,} bytes）")
        print(f"损坏引用（引用但磁盘缺失）: {len(missing)}")
        print()
        if orphans:
            for item in orphans:
                print(
                    f"  {item['dir']}/{item['file']} "
                    f"({int(item['size']):,} bytes, mtime={item['mtime']})"
                )
            print()
            print("仅预览。确认后加 --apply 执行隔离（移动到 _orphan_quarantine，不删除）。")
        else:
            print("  无孤儿文件。")
        if missing:
            print()
            print("## 损坏引用")
            for item in missing:
                print(f"  {item['dir']}/{item['file']}")
        return 0

    if not orphans:
        report["applied"] = True
        report["remaining_orphans"] = 0
        report["db_state_after"] = _state()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        print("# 孤儿 blob 清理（APPLY）")
        print("无孤儿文件；未创建隔离目录、未备份、未移动任何文件。")
        return 0

    manifest: list[dict[str, object]] = []
    quarantine: Path | None = None
    skipped = 0
    backup: Path | None = None
    try:
        if not args.json:
            print("开始备份数据库 ...")
        backup = _backup_db()
        report["backup"] = str(backup)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        quarantine = DATA_DIR / "_orphan_quarantine" / ts
        quarantine.mkdir(parents=True, exist_ok=True)
        report["quarantine"] = str(quarantine)
        moved = 0
        for item in orphans:
            src = Path(str(item["path"]))
            dest = quarantine / str(item["dir"]) / str(item["file"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                skipped += 1
                continue
            shutil.move(str(src), str(dest))
            moved += 1
            manifest.append(
                {
                    **item,
                    "quarantine_path": str(dest),
                }
            )
        report["moved"] = moved
        report["skipped_existing"] = skipped
        _, remaining, _ = _scan(refs)
        report["remaining_orphans"] = len(remaining)
        report["db_state_after"] = _state()
        report["applied"] = True
    except Exception as exc:
        print(f"执行失败: {exc}", file=sys.stderr)
        return 1
    finally:
        if quarantine is not None:
            try:
                (quarantine / "manifest.json").write_text(
                    json.dumps(
                        {
                            "created_at": datetime.now().isoformat(timespec="seconds"),
                            "db_backup": str(backup) if backup else None,
                            "moved": manifest,
                            "skipped_existing": skipped,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except OSError as exc:
                print(f"写入隔离清单失败: {exc}", file=sys.stderr)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f"备份已创建: {report['backup']}")
    print(f"已隔离 {moved} 个文件到 {report['quarantine']}（未删除）。")
    if report["skipped_existing"]:
        print(f"跳过 {report['skipped_existing']} 个目标已存在的文件。")
    print(f"剩余孤儿: {report['remaining_orphans']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
