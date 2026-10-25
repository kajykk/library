"""语言字段归一化（默认 dry-run，不写库）。

用法：
    python server/scripts/normalize_language.py              # 预览
    python server/scripts/normalize_language.py --json       # JSON 输出
    python server/scripts/normalize_language.py --apply      # 执行（自动备份 DB）

--apply 前请停止 uvicorn 等写入进程。备份为 knowledge_base.db.bak.<timestamp>
（含 -wal/-shm），写入按 id + 原值做条件更新，避免覆盖并发修改。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = SERVER_ROOT / "knowledge_base.db"

LANG_MAP = {
    "zh": "zh",
    "zh-CN": "zh",
    "zh-TW": "zh",
    "zh-Hans": "zh",
    "zh-Hant": "zh",
    "zh-cn": "zh",
    "zh-tw": "zh",
    "zh-Hans-CN": "zh",
    "简体中文": "zh",
    "cn": "zh",
    "en": "en",
    "en-US": "en",
    "en-GB": "en",
    "en-us": "en",
    "EN": "en",
    "EN-US": "en",
    "eng": "en",
    "ja": "ja",
    "und": "und",
    "": "und",
}


def _display(value: str | None) -> str:
    if value is None:
        return "<NULL>"
    if value == "":
        return "<EMPTY>"
    return value


def _ordered(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


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


def _distribution(conn: sqlite3.Connection) -> Counter[str]:
    return Counter(_display(row[0]) for row in conn.execute("SELECT language FROM documents"))


def main() -> int:
    parser = argparse.ArgumentParser(description="语言字段归一化（默认 dry-run）")
    parser.add_argument("--apply", action="store_true", help="执行写入（默认 dry-run）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"数据库不存在: {DB_PATH}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, language, deleted_at FROM documents").fetchall()
    conn.close()

    before = Counter()
    after = Counter()
    transitions: Counter[tuple[str, str]] = Counter()
    unmapped: Counter[str] = Counter()
    plan: list[dict[str, object]] = []

    for row in rows:
        raw = row["language"]
        before[_display(raw)] += 1
        key = "" if raw is None else raw
        if key in LANG_MAP:
            target = LANG_MAP[key]
            after[target] += 1
            if target != raw:
                transitions[(_display(raw), target)] += 1
                plan.append(
                    {
                        "id": row["id"],
                        "raw": raw,
                        "target": target,
                        "deleted": row["deleted_at"] is not None,
                    }
                )
        else:
            unmapped[_display(raw)] += 1
            after[_display(raw)] += 1

    transition_rows = [
        {"from": frm, "to": to, "count": count}
        for (frm, to), count in sorted(transitions.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    report: dict[str, object] = {
        "mode": "apply" if args.apply else "dry-run",
        "db": str(DB_PATH),
        "total_docs": len(rows),
        "alive_docs": sum(1 for row in rows if row["deleted_at"] is None),
        "before": _ordered(before),
        "after_preview": _ordered(after),
        "changes": len(plan),
        "changes_alive": sum(1 for item in plan if not item["deleted"]),
        "transitions": transition_rows,
        "unmapped": _ordered(unmapped),
        "sample_changes": [
            {"id": item["id"], "from": _display(item["raw"]), "to": item["target"]}
            for item in plan[:30]
        ],
        "applied": False,
        "updated": 0,
        "backup": None,
        "after_actual": None,
    }

    backup_path: Path | None = None
    updated = 0
    skipped = 0
    if args.apply and plan:
        try:
            if not args.json:
                print("请确认 uvicorn 等写入进程已停止；开始备份数据库 ...")
            backup_path = _backup_db()
            report["backup"] = str(backup_path)
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            try:
                with conn:
                    for item in plan:
                        cur = conn.execute(
                            "UPDATE documents SET language = ? "
                            "WHERE id = ? AND language IS ?",
                            (item["target"], item["id"], item["raw"]),
                        )
                        if cur.rowcount:
                            updated += 1
                        else:
                            skipped += 1
            finally:
                conn.close()
            conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
            try:
                actual = _distribution(conn)
            finally:
                conn.close()
        except Exception as exc:  # 写入前失败时备份仍在，便于人工恢复
            print(f"执行失败: {exc}", file=sys.stderr)
            return 1
        report["applied"] = True
        report["updated"] = updated
        report["skipped_stale"] = skipped
        report["after_actual"] = _ordered(actual)
        report["after_matches_preview"] = actual == after

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"# 语言字段归一化（{'APPLY' if args.apply else 'DRY-RUN'}）")
    print()
    print(f"数据库: {DB_PATH}")
    print(f"文档: {len(rows)}（未删除 {report['alive_docs']}）")
    print()
    print("## 变更前分布")
    for key, count in _ordered(before).items():
        print(f"  {key}: {count}")
    print()
    print(f"## 将变更 {len(plan)} 条（未删除 {report['changes_alive']}）")
    for item in transition_rows:
        print(f"  {item['from']} -> {item['to']}: {item['count']}")
    print()
    print(f"## 未映射值（保持原值）: {_ordered(unmapped) or '无'}")
    print()
    print("## 变更后预览")
    for key, count in _ordered(after).items():
        print(f"  {key}: {count}")
    print()

    if not args.apply:
        print("仅预览。确认后加 --apply 执行（会先完整备份 DB）。")
        return 0
    if not plan:
        print("没有需要变更的记录，未创建备份。")
        return 0

    print(f"备份已创建: {backup_path}")
    print(f"完成：更新 {updated} 条，跳过 {skipped} 条（原值已变化）。")
    if isinstance(report["after_actual"], dict):
        print("## 变更后实际分布")
        for key, count in report["after_actual"].items():
            print(f"  {key}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
