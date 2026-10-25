"""增量备份（默认 dry-run）。

以 mtime_ns + size 检测变更。每次备份都是完整快照：未变更的数据文件硬链接自
上一份备份（同卷零拷贝），新增/修改文件复制并计算 sha256；SQLite 数据库文件
每次都实拷贝，避免硬链接让源库后续写入污染历史备份。manifest.json 记录每个
文件的 sha256 / size / mtime_ns，可独立校验与恢复。

用法：
    python server/scripts/incremental_backup.py                    # dry-run：报告将要备份的文件
    python server/scripts/incremental_backup.py --apply            # 执行增量备份
    python server/scripts/incremental_backup.py --apply --full     # 全量备份（忽略上次记录）
    python server/scripts/incremental_backup.py --verify <path>    # 校验备份完整性
    python server/scripts/incremental_backup.py --json             # JSON 输出
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = SERVER_ROOT / "data"
DB_PATH = SERVER_ROOT / "knowledge_base.db"
BACKUP_ROOT = SERVER_ROOT / "backups"
MANIFEST_NAME = "manifest.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel(path: Path) -> str:
    return str(path.relative_to(SERVER_ROOT)).replace("\\", "/")


def _is_db_file(path: Path) -> bool:
    """SQLite 主库/侧车文件必须实拷贝，硬链接会被源库写入连带修改。"""
    return path.name.lower().endswith((".db", ".db-wal", ".db-shm"))


def _backup_path(backup_dir: Path, rel: str) -> Path:
    full = (backup_dir / rel).resolve()
    if not full.is_relative_to(backup_dir.resolve()):
        raise ValueError(f"非法清单路径: {rel}")
    return full


def load_last_manifest() -> dict:
    """加载上次备份的 manifest，用于增量检测。"""
    if not BACKUP_ROOT.exists():
        return {}
    manifests = sorted(BACKUP_ROOT.glob(f"*/{MANIFEST_NAME}"))
    if not manifests:
        return {}
    try:
        return json.loads(manifests[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def collect_files() -> list[Path]:
    """收集 data/ 下所有文件和 knowledge_base.db 主库/侧车文件。"""
    files: list[Path] = []
    if DATA_DIR.exists():
        for root, dirs, fnames in os.walk(DATA_DIR):
            dirs[:] = sorted(d for d in dirs if d != "_orphan_quarantine")
            for fn in sorted(fnames):
                full = Path(root) / fn
                if full.is_file():
                    files.append(full)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(DB_PATH) + suffix)
        if p.exists():
            files.append(p)
    return files


def _build_plan(full: bool) -> tuple[list[dict], list[dict], dict]:
    all_files = collect_files()
    last_manifest = {} if full else load_last_manifest()
    prev_files = {f["path"]: f for f in last_manifest.get("files", [])}

    to_backup: list[dict] = []
    unchanged: list[dict] = []
    for path in all_files:
        rel = _rel(path)
        st = path.stat()
        prev = prev_files.get(rel)
        if (
            not _is_db_file(path)
            and prev
            and prev.get("mtime_ns") == st.st_mtime_ns
            and prev.get("size") == st.st_size
        ):
            unchanged.append(
                {
                    "path": rel,
                    "full": path,
                    "size": st.st_size,
                    "mtime_ns": st.st_mtime_ns,
                    "prev": prev,
                }
            )
        else:
            to_backup.append(
                {
                    "path": rel,
                    "full": path,
                    "size": st.st_size,
                    "mtime_ns": st.st_mtime_ns,
                }
            )
    return to_backup, unchanged, last_manifest


def verify_backup(backup_dir: Path) -> int:
    manifest_path = backup_dir / MANIFEST_NAME
    if not manifest_path.exists():
        print(f"manifest.json 不存在: {manifest_path}", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files", [])
    ok = True
    for entry in files:
        try:
            full = _backup_path(backup_dir, entry["path"])
        except ValueError as exc:
            print(f"  INVALID: {exc}")
            ok = False
            continue
        if not full.is_file():
            print(f"  MISSING: {entry['path']}")
            ok = False
            continue
        if full.stat().st_size != entry["size"]:
            print(f"  SIZE MISMATCH: {entry['path']}")
            ok = False
            continue
        actual = sha256_file(full)
        if actual != entry["sha256"]:
            print(
                f"  MISMATCH: {entry['path']} "
                f"(expected {entry['sha256'][:12]}..., got {actual[:12]}...)"
            )
            ok = False
        else:
            print(f"  OK: {entry['path']}")
    print(f"\n校验结果: {'全部通过' if ok else '发现异常'}（{len(files)} 个文件）")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="增量备份")
    parser.add_argument("--apply", action="store_true", help="执行备份（默认 dry-run）")
    parser.add_argument("--full", action="store_true", help="全量备份（忽略上次记录）")
    parser.add_argument("--verify", type=str, default=None, help="校验指定备份目录")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    if args.verify:
        return verify_backup(Path(args.verify))

    to_backup, unchanged, last_manifest = _build_plan(args.full)
    to_backup_bytes = sum(e["size"] for e in to_backup)
    unchanged_bytes = sum(e["size"] for e in unchanged)

    if args.json:
        print(
            json.dumps(
                {
                    "mode": "full" if args.full else "incremental",
                    "base_manifest": last_manifest.get("timestamp"),
                    "total_files": len(to_backup) + len(unchanged),
                    "to_backup": len(to_backup),
                    "unchanged": len(unchanged),
                    "to_backup_bytes": to_backup_bytes,
                    "unchanged_bytes": unchanged_bytes,
                    "files": [{"path": e["path"], "size": e["size"]} for e in to_backup],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print("# 增量备份（{}）".format("APPLY" if args.apply else "DRY-RUN"))
    print(f"模式: {'全量' if args.full else '增量'}")
    print(f"上次备份: {last_manifest.get('timestamp') or '无'}")
    print(f"总文件: {len(to_backup) + len(unchanged)}")
    print(
        f"需备份: {len(to_backup)}"
        f"（{to_backup_bytes:,} bytes = {to_backup_bytes / 1024 / 1024 / 1024:.2f} GiB）"
    )
    print(f"未变更: {len(unchanged)}（{unchanged_bytes:,} bytes，硬链接复用）")
    print()
    if to_backup:
        for e in to_backup[:20]:
            print(f"  {e['path']} ({e['size']:,} bytes)")
        if len(to_backup) > 20:
            print(f"  ... 还有 {len(to_backup) - 20} 个文件")

    if not args.apply:
        print("\n仅预览。确认后加 --apply 执行备份。")
        return 0

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUP_ROOT / ts
    suffix = 1
    while backup_dir.exists():
        suffix += 1
        backup_dir = BACKUP_ROOT / f"{ts}_{suffix}"
    backup_dir.mkdir(parents=True)

    prev_ts = last_manifest.get("timestamp")
    linked = 0
    copied = 0
    manifest_files: list[dict] = []

    total = len(to_backup) + len(unchanged)
    done = 0
    for e in to_backup:
        dest = _backup_path(backup_dir, e["path"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(e["full"], dest)
        manifest_files.append(
            {
                "path": e["path"],
                "size": e["size"],
                "mtime_ns": e["mtime_ns"],
                "sha256": sha256_file(dest),
            }
        )
        copied += 1
        done += 1
        if done % 100 == 0:
            print(f"[{done}/{total}] 已处理", file=sys.stderr, flush=True)

    for e in unchanged:
        dest = _backup_path(backup_dir, e["path"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        prev_path = BACKUP_ROOT / str(prev_ts) / e["path"] if prev_ts else None
        hardlinked = False
        if prev_path and prev_path.is_file() and prev_path.stat().st_size == e["size"]:
            try:
                os.link(prev_path, dest)
                hardlinked = True
                linked += 1
            except OSError:
                hardlinked = False
        if hardlinked:
            sha = e["prev"]["sha256"]
        else:
            shutil.copy2(e["full"], dest)
            sha = sha256_file(dest)
            copied += 1
        manifest_files.append(
            {
                "path": e["path"],
                "size": e["size"],
                "mtime_ns": e["mtime_ns"],
                "sha256": sha,
            }
        )
        done += 1
        if done % 100 == 0:
            print(f"[{done}/{total}] 已处理", file=sys.stderr, flush=True)

    manifest_files.sort(key=lambda e: e["path"])
    manifest = {
        "version": 1,
        "timestamp": backup_dir.name,
        "mode": "full" if args.full else "incremental",
        "base_manifest": prev_ts,
        "file_count": len(manifest_files),
        "total_bytes": sum(e["size"] for e in manifest_files),
        "linked_files": linked,
        "copied_files": copied,
        "files": manifest_files,
    }
    (backup_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n备份完成: {backup_dir}")
    print(f"  文件数: {len(manifest_files)}")
    print(f"  总大小: {manifest['total_bytes']:,} bytes")
    print(f"  实拷贝: {copied}；硬链接复用: {linked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
