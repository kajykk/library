"""按书名/作者关键词自动归类（默认 dry-run，不写库）。

用法：
    python server/scripts/auto_classify.py              # 预览
    python server/scripts/auto_classify.py --json       # JSON 输出
    python server/scripts/auto_classify.py --apply      # 执行（自动备份 DB）

候选 = 未删除、type 为 book/pdf、collection_id 为空。规则来自
app/services/auto_classify.py 的 classify(title, author)：只有命中且目标分类
已存在于 collections 时才归类；不自动创建分类，未命中/分类缺失一律跳过。

--apply 写 operation log（kind=batch_update，undoable=true，before/after 记
collection_id），可通过 POST /api/operations/{id}/undo 撤销。
默认库固定为 server/knowledge_base.db，可用 KB_DATABASE_URL 覆盖（临时库
验收用）。--apply 前请停止 uvicorn 等写入进程。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parent.parent
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

os.environ.setdefault(
    "KB_DATABASE_URL", f"sqlite:///{(SERVER_ROOT / 'knowledge_base.db').as_posix()}"
)

from sqlalchemy.engine import make_url  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Document  # noqa: E402
from app.services.auto_classify import classify  # noqa: E402
from app.services.operation_log import MAX_DOCS_PER_OP, record  # noqa: E402

BOOK_TYPES = ("book", "pdf")


def _db_path() -> Path:
    url = make_url(get_settings().database_url)
    if url.get_backend_name() != "sqlite":
        raise SystemExit(f"仅支持 sqlite 数据库，当前为 {url.get_backend_name()}")
    return Path(url.database).resolve()


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _read_rows(db_path: Path) -> list[sqlite3.Row]:
    conn = _connect_ro(db_path)
    try:
        return conn.execute(
            "SELECT id, title, author FROM documents "
            "WHERE deleted_at IS NULL AND type IN ('book', 'pdf') "
            "AND collection_id IS NULL ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def _read_collections(db_path: Path) -> dict[str, uuid.UUID]:
    conn = _connect_ro(db_path)
    try:
        return {
            row["name"]: uuid.UUID(row["id"])
            for row in conn.execute("SELECT id, name FROM collections")
        }
    finally:
        conn.close()


def _uncategorized_count(db_path: Path) -> int:
    conn = _connect_ro(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM documents WHERE deleted_at IS NULL "
            "AND type IN ('book', 'pdf') AND collection_id IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()


def _build_plan(
    rows: list[sqlite3.Row], collections: dict[str, uuid.UUID]
) -> tuple[list[dict], list[dict], Counter[str]]:
    planned: list[dict] = []
    unmatched: list[dict] = []
    skipped: Counter[str] = Counter()
    for row in rows:
        target = classify(row["title"] or "", row["author"] or "")
        if target is None:
            skipped["no_rule"] += 1
            if len(unmatched) < 20:
                unmatched.append({"title": row["title"]})
            continue
        collection_id = collections.get(target)
        if collection_id is None:
            skipped["collection_missing"] += 1
            if len(unmatched) < 20:
                unmatched.append({"title": row["title"], "target": target})
            continue
        planned.append(
            {
                "id": row["id"],
                "title": row["title"],
                "collection": target,
                "collection_id": str(collection_id),
            }
        )
    return planned, unmatched, skipped


def _backup_db(db_path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    base = db_path.with_name(f"{db_path.name}.bak.{ts}")
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(db_path) + suffix)
        if src.exists():
            shutil.copy2(src, Path(str(base) + suffix))
    if not base.exists() or base.stat().st_size == 0:
        raise RuntimeError(f"数据库备份失败: {base}")
    return base


def _ordered(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


def _apply(plan: list[dict]) -> dict:
    db = SessionLocal()
    changed: list[uuid.UUID] = []
    before_docs: dict[str, dict] = {}
    after_docs: dict[str, dict] = {}
    skipped: Counter[str] = Counter()
    operation_ids: list[str] = []
    try:
        for item in plan:
            doc = db.get(Document, uuid.UUID(item["id"]))
            if doc is None or doc.deleted_at is not None:
                skipped["document_missing"] += 1
            elif doc.collection_id is not None:
                skipped["already_classified"] += 1
            else:
                doc.collection_id = uuid.UUID(item["collection_id"])
                changed.append(doc.id)
                before_docs[str(doc.id)] = {"collection_id": None}
                after_docs[str(doc.id)] = {"collection_id": item["collection_id"]}
        db.commit()

        if changed:
            chunks = [
                changed[i : i + MAX_DOCS_PER_OP]
                for i in range(0, len(changed), MAX_DOCS_PER_OP)
            ]
            for part, chunk in enumerate(chunks, start=1):
                summary = f"自动分类：{len(changed)} 本（脚本 auto_classify.py）"
                if len(chunks) > 1:
                    summary = (
                        f"自动分类：本批 {len(chunk)} 本（脚本 auto_classify.py，"
                        f"第 {part}/{len(chunks)} 批，共 {len(changed)} 本）"
                    )
                log = record(
                    db,
                    kind="batch_update",
                    document_ids=chunk,
                    before_state={
                        "docs": {str(i): before_docs[str(i)] for i in chunk}
                    },
                    after_state={"docs": {str(i): after_docs[str(i)] for i in chunk}},
                    summary=summary,
                    undoable=True,
                )
                operation_ids.append(str(log.id))
            db.commit()
    finally:
        db.close()

    return {
        "attempted": len(plan),
        "assigned": len(changed),
        "skipped_after_plan": _ordered(skipped),
        "operation_ids": operation_ids,
    }


def _print_report(report: dict) -> None:
    print(f"# 自动分类（{'APPLY' if report['mode'] == 'apply' else 'DRY-RUN'}）")
    print()
    print(f"数据库: {report['db']}")
    print(f"未分类（存活 book/pdf）: {report['before_uncategorized']}")
    print(f"可自动归类: {report['candidates']} {report['by_collection']}")
    print(f"计划阶段跳过: {report['skipped_before_apply'] or '无'}")
    if report["unmatched_sample"]:
        print("未归类样例:")
        for item in report["unmatched_sample"][:10]:
            suffix = f"（规则命中「{item['target']}」但分类不存在）" if item.get("target") else ""
            print(f"  {item['title']}{suffix}")
    print()

    if report["mode"] != "apply":
        print("仅预览。确认后加 --apply 执行（会先完整备份 DB）。")
        return
    if report["backup"] is None:
        print("没有可自动归类的文档，未备份也未写库。")
        return

    print(f"备份已创建: {report['backup']}")
    print(
        f"完成：尝试 {report['attempted']}，归类 {report['assigned']}，"
        f"执行阶段跳过 {report['skipped_after_plan'] or '无'}"
    )
    for op_id in report["operation_ids"]:
        print(f"operation log: {op_id}")
    print(
        f"变更后未分类（存活 book/pdf）: {report['after_uncategorized']}"
        f"（-{report['before_uncategorized'] - report['after_uncategorized']}）"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="按规则自动归类（默认 dry-run）")
    parser.add_argument("--apply", action="store_true", help="执行写入（默认 dry-run）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    db_path = _db_path()
    if not db_path.is_file():
        print(f"数据库不存在: {db_path}", file=sys.stderr)
        return 1

    collections = _read_collections(db_path)
    plan, unmatched, skipped = _build_plan(_read_rows(db_path), collections)
    report: dict = {
        "mode": "apply" if args.apply else "dry-run",
        "db": str(db_path),
        "before_uncategorized": _uncategorized_count(db_path),
        "candidates": len(plan),
        "by_collection": _ordered(Counter(item["collection"] for item in plan)),
        "skipped_before_apply": dict(skipped),
        "unmatched_sample": unmatched,
        "sample": plan[:20],
        "attempted": 0,
        "assigned": 0,
        "skipped_after_plan": {},
        "backup": None,
        "operation_ids": [],
        "after_uncategorized": None,
    }

    if args.apply and plan:
        try:
            report["backup"] = str(_backup_db(db_path))
        except Exception as exc:
            print(f"执行失败: {exc}", file=sys.stderr)
            return 1
        try:
            report.update(_apply(plan))
        except Exception as exc:
            print(f"执行失败: {exc}", file=sys.stderr)
            return 1
        report["after_uncategorized"] = _uncategorized_count(db_path)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
