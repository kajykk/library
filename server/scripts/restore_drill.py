"""恢复演练（只读验证，不写入真实数据）。

从备份目录恢复到临时位置，按 manifest.json 的 sha256 校验完整性。
不修改任何真实数据；默认使用系统临时目录，可用 --target 指定其他磁盘。

用法：
    python server/scripts/restore_drill.py <backup_dir>              # 复制+校验
    python server/scripts/restore_drill.py <backup_dir> --verify     # 仅校验（不复制）
    python server/scripts/restore_drill.py <backup_dir> --target E:/kb-restore-drill
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_path(root: Path, rel: str) -> Path:
    full = (root / rel).resolve()
    if not full.is_relative_to(root.resolve()):
        raise ValueError(f"非法清单路径: {rel}")
    return full


def main() -> int:
    parser = argparse.ArgumentParser(description="恢复演练")
    parser.add_argument("backup_dir", type=str, help="备份目录路径")
    parser.add_argument("--verify", action="store_true", help="仅校验，不复制")
    parser.add_argument(
        "--target",
        type=str,
        default=None,
        help="复制目标目录（默认系统临时目录；空间不足时指定其他磁盘）",
    )
    args = parser.parse_args()

    backup_dir = Path(args.backup_dir).resolve()
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"manifest.json 不存在: {manifest_path}", file=sys.stderr)
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files", [])

    print(f"# 恢复演练（{'仅校验' if args.verify else '复制+校验'}）")
    print(f"备份: {backup_dir}")
    print(f"备份时间: {manifest.get('timestamp')}")
    print(f"模式: {manifest.get('mode')}")
    print(f"文件数: {len(files)}")
    print()

    if args.verify:
        ok = True
        for entry in files:
            try:
                full = _safe_path(backup_dir, entry["path"])
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
                print(f"  MISMATCH: {entry['path']}")
                ok = False
            else:
                print(f"  OK: {entry['path']}")
        print(f"\n校验结果: {'全部通过' if ok else '发现异常'}（{len(files)} 个文件）")
        return 0 if ok else 1

    if args.target:
        tmp_dir = Path(args.target).resolve()
        if tmp_dir.exists() and any(tmp_dir.iterdir()):
            print(f"目标目录非空，拒绝覆盖: {tmp_dir}", file=sys.stderr)
            return 1
        tmp_dir.mkdir(parents=True, exist_ok=True)
    else:
        tmp_dir = Path(tempfile.mkdtemp(prefix="kb-restore-drill-"))
    print(f"恢复目标: {tmp_dir}")
    print()

    ok = True
    restored = 0
    for entry in files:
        try:
            src = _safe_path(backup_dir, entry["path"])
            dest = _safe_path(tmp_dir, entry["path"])
        except ValueError as exc:
            print(f"  INVALID: {exc}")
            ok = False
            continue
        if not src.is_file():
            print(f"  MISSING: {entry['path']}")
            ok = False
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        actual = sha256_file(dest)
        if actual != entry["sha256"]:
            print(f"  MISMATCH: {entry['path']}")
            ok = False
        else:
            restored += 1

    print(f"\n恢复文件: {restored}/{len(files)}")
    print(f"校验结果: {'全部通过' if ok else '发现异常'}")
    print(f"临时目录: {tmp_dir}")
    print("（演练完成，可手动删除临时目录）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
