"""重复 sha256 去重（默认 dry-run，仅软删除，不删文件）。

用法：
    python server/scripts/dedup_sha256.py                       # 预览
    python server/scripts/dedup_sha256.py --json                # JSON 输出
    python server/scripts/dedup_sha256.py --strategy oldest     # best(默认)/oldest/newest
    python server/scripts/dedup_sha256.py --apply               # 执行软删除（自动备份）

保留策略 best：有封面 > 有正文 > 创建时间最早 > id 稳定排序。
只对存活文档去重（deleted_at IS NULL），每组保留 1 本，其余写入 deleted_at；
不硬删除、不删除任何文件。--apply 前请停止 uvicorn 等写入进程。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = SERVER_ROOT / "knowledge_base.db"
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
SUSPICIOUS_MAX_BYTES = 1024

DOC_QUERY = """
    SELECT id, title, format, type, created_at, deleted_at, sha256, file_size,
           CASE WHEN COALESCE(cover_path, '') <> '' THEN 1 ELSE 0 END AS has_cover,
           CASE WHEN TRIM(COALESCE(content, '')) <> '' THEN 1 ELSE 0 END AS has_content
    FROM documents
    WHERE sha256 = ?
"""


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


def _title_rank(doc: dict[str, object]) -> int:
    """best 策略的平局保护：优先有意义的标题，避免保留纯时间戳标题。"""
    title = str(doc["title"] or "").strip()
    if not title:
        return 2
    if title.isdigit() and len(title) >= 10:
        return 1
    return 0


def _pick_keeper(docs: list[dict[str, object]], strategy: str) -> dict[str, object]:
    if strategy == "oldest":
        return min(docs, key=lambda d: (d["created_at"] or "", d["id"]))
    if strategy == "newest":
        return max(docs, key=lambda d: (d["created_at"] or "", d["id"]))
    return min(
        docs,
        key=lambda d: (
            0 if d["has_cover"] else 1,
            0 if d["has_content"] else 1,
            _title_rank(d),
            d["created_at"] or "",
            d["id"],
        ),
    )


def _suspicious_reason(sha: str, docs: list[dict[str, object]]) -> str | None:
    """识别“同哈希但文件本身是错误页/空文件”的假重复，默认不软删除。"""
    sizes = [int(doc["file_size"] or 0) for doc in docs]
    if sha == EMPTY_SHA256 or (sizes and max(sizes) == 0):
        return "empty_file"
    if sizes and max(sizes) <= SUSPICIOUS_MAX_BYTES:
        return "tiny_error_page"
    return None


def _brief(doc: dict[str, object]) -> dict[str, object]:
    return {
        "id": doc["id"],
        "title": doc["title"],
        "format": doc["format"],
        "type": doc["type"],
        "created_at": doc["created_at"],
        "has_cover": bool(doc["has_cover"]),
        "has_content": bool(doc["has_content"]),
    }


def _build_plan(
    strategy: str, include_suspicious: bool
) -> tuple[list[dict[str, object]], dict[str, object]]:
    conn = _connect_ro()
    try:
        sha_rows = conn.execute(
            "SELECT sha256 FROM documents "
            "WHERE COALESCE(sha256, '') <> '' "
            "GROUP BY sha256 HAVING COUNT(*) > 1 "
            "ORDER BY COUNT(*) DESC, sha256"
        ).fetchall()
        groups: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        for sha_row in sha_rows:
            sha = sha_row["sha256"]
            docs = [dict(row) for row in conn.execute(DOC_QUERY, (sha,)).fetchall()]
            alive = [doc for doc in docs if doc["deleted_at"] is None]
            if len(alive) <= 1:
                skipped.append(
                    {
                        "sha256": sha,
                        "total": len(docs),
                        "alive": len(alive),
                        "reason": "already_deduped" if alive else "all_deleted",
                    }
                )
                continue
            keeper = _pick_keeper(alive, strategy)
            to_delete = [doc for doc in alive if doc["id"] != keeper["id"]]
            titles = sorted({str(doc["title"] or "") for doc in docs})
            reason = _suspicious_reason(sha, alive)
            groups.append(
                {
                    "sha256": sha,
                    "total": len(docs),
                    "alive": len(alive),
                    "keeper": _brief(keeper),
                    "to_delete": [_brief(doc) for doc in to_delete],
                    "title_variants": len(titles),
                    "titles": titles[:6],
                    "file_size": int(alive[0]["file_size"] or 0),
                    "suspicious_reason": reason,
                    "planned": reason is None or include_suspicious,
                }
            )
        planned = [group for group in groups if group["planned"]]
        total_detected = sum(len(group["to_delete"]) for group in groups)
        total_planned = sum(len(group["to_delete"]) for group in planned)
        summary = {
            "groups_total": len(sha_rows),
            "groups_actionable": len(groups),
            "groups_planned": len(planned),
            "groups_suspicious": len(groups) - len(planned),
            "groups_skipped": len(skipped),
            "skipped": skipped,
            "total_to_soft_delete": total_planned,
            "total_to_soft_delete_detected": total_detected,
            "total_to_soft_delete_suspicious": total_detected - total_planned,
            "include_suspicious": include_suspicious,
        }
        return groups, summary
    finally:
        conn.close()


def _count_actionable(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT sha256 FROM documents "
        "WHERE deleted_at IS NULL AND COALESCE(sha256, '') <> '' "
        "GROUP BY sha256 HAVING COUNT(*) > 1"
    ).fetchall()
    return len(rows)


def _human_print(
    groups: list[dict[str, object]], summary: dict[str, object], strategy: str, apply: bool
) -> None:
    print(f"# 重复 sha256 去重（{'APPLY' if apply else 'DRY-RUN'}）")
    print()
    print(f"策略: {strategy}")
    print(
        f"重复组: {summary['groups_total']}（检测到可处理 {summary['groups_actionable']}；"
        f"默认计划 {summary['groups_planned']}；可疑跳过 "
        f"{summary['groups_suspicious']}；已去重/全删 {summary['groups_skipped']}）"
    )
    print(
        f"计划软删除: {summary['total_to_soft_delete']} 本"
        f"（检测到的全部候选 {summary['total_to_soft_delete_detected']} 本；"
        f"可疑组 {summary['total_to_soft_delete_suspicious']} 本默认不动）"
    )
    if summary["include_suspicious"]:
        print("警告: 已启用 --include-suspicious，可疑组也会进入计划。")
    print()

    suspicious = [group for group in groups if not group["planned"]]
    if suspicious:
        print("## 可疑组（默认不处理，需人工确认）")
        for group in suspicious:
            keeper = group["keeper"]
            print(
                f"## {str(group['sha256'])[:16]}... "
                f"({group['alive']} alive / {group['total']} total, "
                f"{group['file_size']} bytes, {group['suspicious_reason']})"
            )
            for doc in [keeper, *group["to_delete"]]:
                print(
                    f"  [涉及] [{doc['format']}] {str(doc['title'])[:58]} "
                    f"(cover={doc['has_cover']}, content={doc['has_content']})"
                )
        print(
            "  这些文件是同哈希的空文件/极小错误页（如 143 bytes 的 410 Gone），"
            "不应按重复去重；建议重新下载或标记源文件损坏。"
        )
        print()

    planned = [group for group in groups if group["planned"]]
    print("## 计划处理的重复组")
    for group in planned:
        keeper = group["keeper"]
        print(
            f"## {str(group['sha256'])[:16]}... "
            f"({group['alive']} alive / {group['total']} total, "
            f"标题写法 {group['title_variants']} 种)"
        )
        print(
            f"  保留: [{keeper['format']}] {str(keeper['title'])[:58]} "
            f"(cover={keeper['has_cover']}, content={keeper['has_content']})"
        )
        for doc in group["to_delete"]:
            print(
                f"  软删: [{doc['format']}] {str(doc['title'])[:58]} "
                f"(cover={doc['has_cover']}, content={doc['has_content']})"
            )
        print()
    if summary["skipped"]:
        print("## 跳过的重复组（已去重/全部已删）")
        for item in summary["skipped"]:
            print(
                f"  {str(item['sha256'])[:16]}... "
                f"({item['alive']} alive / {item['total']} total, {item['reason']})"
            )
        print()
    if not apply:
        print("仅预览。确认后加 --apply 执行软删除（会先完整备份 DB）。")
        if suspicious:
            print("可疑组需人工确认后，才能用 --include-suspicious 纳入。")


def main() -> int:
    parser = argparse.ArgumentParser(description="重复 sha256 去重（默认 dry-run）")
    parser.add_argument("--apply", action="store_true", help="执行软删除（默认 dry-run）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument(
        "--strategy",
        choices=["best", "oldest", "newest"],
        default="best",
        help="保留策略（默认 best：有封面>有正文>有意义标题>最早）",
    )
    parser.add_argument(
        "--include-suspicious",
        action="store_true",
        help="把空文件/极小错误页组也纳入计划（默认跳过，需人工确认）",
    )
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"数据库不存在: {DB_PATH}", file=sys.stderr)
        return 1

    groups, summary = _build_plan(args.strategy, args.include_suspicious)
    report: dict[str, object] = {
        "mode": "apply" if args.apply else "dry-run",
        "db": str(DB_PATH),
        "strategy": args.strategy,
        "include_suspicious": args.include_suspicious,
        "groups": summary["groups_actionable"],
        "groups_total": summary["groups_total"],
        "groups_planned": summary["groups_planned"],
        "groups_suspicious": summary["groups_suspicious"],
        "groups_skipped": summary["groups_skipped"],
        "skipped": summary["skipped"],
        "total_to_soft_delete": summary["total_to_soft_delete"],
        "total_to_soft_delete_detected": summary["total_to_soft_delete_detected"],
        "total_to_soft_delete_suspicious": summary["total_to_soft_delete_suspicious"],
        "details": groups,
        "applied": False,
        "updated": 0,
        "backup": None,
        "actionable_after": None,
    }

    planned_groups = [group for group in groups if group["planned"]]
    if args.apply and not planned_groups:
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        _human_print(groups, summary, args.strategy, apply=True)
        print("没有可安全处理的重复组，未创建备份、未写库。")
        return 0
    if args.apply and planned_groups:
        try:
            if not args.json:
                print("请确认 uvicorn 等写入进程已停止；开始备份数据库 ...")
            backup = _backup_db()
            report["backup"] = str(backup)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            updated = 0
            stale = 0
            try:
                with conn:
                    for group in planned_groups:
                        for doc in group["to_delete"]:
                            cur = conn.execute(
                                "UPDATE documents SET deleted_at = ? "
                                "WHERE id = ? AND deleted_at IS NULL AND sha256 = ?",
                                (now, doc["id"], group["sha256"]),
                            )
                            if cur.rowcount:
                                updated += 1
                            else:
                                stale += 1
            finally:
                conn.close()
            conn = _connect_ro()
            try:
                after = _count_actionable(conn)
            finally:
                conn.close()
        except Exception as exc:
            print(f"执行失败: {exc}", file=sys.stderr)
            return 1
        report["applied"] = True
        report["updated"] = updated
        report["skipped_stale"] = stale
        report["actionable_after"] = after

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if not args.apply:
        _human_print(groups, summary, args.strategy, apply=False)
        return 0

    print(f"备份已创建: {report['backup']}")
    print(f"完成：软删除 {report['updated']} 本，跳过 {report.get('skipped_stale', 0)} 本。")
    print(f"剩余可处理重复组: {report['actionable_after']}")
    suspicious_count = summary["groups_suspicious"]
    if suspicious_count:
        print(
            f"注意: {suspicious_count} 个可疑组（空文件/极小错误页）未处理，"
            "需人工确认后使用 --include-suspicious。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
