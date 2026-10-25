"""WAL checkpoint（默认 dry-run，不写库）。

用法：
    python server/scripts/wal_checkpoint.py              # 预览 WAL 状态
    python server/scripts/wal_checkpoint.py --json       # JSON 输出
    python server/scripts/wal_checkpoint.py --apply      # 执行 checkpoint（自动备份）

dry-run 只读打开数据库，不做 checkpoint；--apply 前请停止 uvicorn 等连接，
脚本会先备份 knowledge_base.db 及 -wal/-shm，再执行 PRAGMA wal_checkpoint(TRUNCATE)。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = SERVER_ROOT / "knowledge_base.db"


def _file_state() -> dict[str, dict[str, object]]:
    state: dict[str, dict[str, object]] = {}
    for label, suffix in (("main", ""), ("wal", "-wal"), ("shm", "-shm")):
        path = Path(str(DB_PATH) + suffix)
        state[label] = {
            "path": str(path),
            "exists": path.exists(),
            "size": path.stat().st_size if path.exists() else 0,
            "mtime_ns": path.stat().st_mtime_ns if path.exists() else None,
        }
    return state


def _read_info() -> dict[str, object]:
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    try:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        autocheckpoint = conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0]
    finally:
        conn.close()
    return {
        "journal_mode": journal_mode,
        "page_size": page_size,
        "wal_autocheckpoint": autocheckpoint,
    }


def _estimate_frames(wal_size: int, page_size: int) -> int:
    # WAL 头 32 字节，每帧 = page_size + 24 字节帧头
    if wal_size < 32 or page_size <= 0:
        return 0
    return (wal_size - 32) // (page_size + 24)


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


def main() -> int:
    parser = argparse.ArgumentParser(description="WAL checkpoint（默认 dry-run）")
    parser.add_argument("--apply", action="store_true", help="执行 checkpoint（默认 dry-run）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"数据库不存在: {DB_PATH}", file=sys.stderr)
        return 1

    before = _file_state()
    info = _read_info()
    wal_size = int(before["wal"]["size"])
    report: dict[str, object] = {
        "mode": "apply" if args.apply else "dry-run",
        "db": str(DB_PATH),
        "before": before,
        **info,
        "wal_frames_estimate": _estimate_frames(wal_size, int(info["page_size"])),
        "applied": False,
        "backup": None,
        "checkpoint_result": None,
        "after": None,
        "db_mtime_changed": False,
    }

    if not args.apply:
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        print("# WAL checkpoint（DRY-RUN）")
        print()
        print(f"数据库: {DB_PATH}")
        for label, title in (("main", "主库"), ("wal", "WAL"), ("shm", "SHM")):
            item = before[label]
            size = int(item["size"])
            print(f"  {title} ({label}): {size:,} bytes")
        print(f"  journal_mode: {info['journal_mode']}")
        print(f"  page_size: {info['page_size']} bytes")
        print(f"  wal_autocheckpoint: {info['wal_autocheckpoint']} pages")
        print(
            f"  WAL 待合并估算: {wal_size:,} bytes / "
            f"{report['wal_frames_estimate']} frames"
        )
        print()
        print("仅预览。确认后加 --apply 执行（会先备份 DB，需停止写入进程）。")
        return 0

    try:
        if not args.json:
            print("请确认 uvicorn 等写入进程已停止；开始备份数据库 ...")
        backup = _backup_db()
        report["backup"] = str(backup)
        conn = sqlite3.connect(str(DB_PATH), timeout=10, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        finally:
            conn.close()
        report["checkpoint_result"] = {
            "busy": result[0],
            "log_frames": result[1],
            "checkpointed_frames": result[2],
        }
        after = _file_state()
        report["after"] = after
        report["applied"] = True
        report["db_mtime_changed"] = (
            after["main"]["mtime_ns"] != before["main"]["mtime_ns"]
        )
    except Exception as exc:
        print(f"执行失败: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"备份已创建: {report['backup']}")
    result = report["checkpoint_result"]
    assert isinstance(result, dict)
    print(
        "checkpoint 结果: "
        f"busy={result['busy']}, log_frames={result['log_frames']}, "
        f"checkpointed={result['checkpointed_frames']}"
    )
    if result["busy"]:
        print("警告: 仍有连接占用，部分帧可能未合并；请停止写入进程后重试。")
    print("## checkpoint 后文件大小")
    after = report["after"]
    assert isinstance(after, dict)
    for label, title in (("main", "主库"), ("wal", "WAL"), ("shm", "SHM")):
        item = after[label]
        print(f"  {title} ({label}): {int(item['size']):,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
