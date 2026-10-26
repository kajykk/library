"""重提取空正文（默认 dry-run，不写库）。

用法：
    python server/scripts/reextract_content.py              # 预览
    python server/scripts/reextract_content.py --json       # JSON 输出
    python server/scripts/reextract_content.py --apply      # 执行（自动备份 DB）

「空正文」口径与质量中心一致：length(trim(content)) = 0（见
app/services/sql_utils.py 的 trimmed_content_length_expr），因此首字符为
NUL 的乱码正文（SQLite length 记为 0、Python 视为非空）也会被重提取。
候选 = 未删除、有 file_path、上述长度为空、格式在提取白名单
（epub/pdf/txt/md/html/mobi/azw/azw3）内。
--apply 使用 app.services.content_extract.extract_text_from_file（单文件 5s
时间预算 + 熔断器），每 100 本提交一次；全部完成后写 operation log
（kind=reextract，undoable=false，before/after 只记成功文档的实际字数）。

默认库固定为 server/knowledge_base.db、数据目录 server/data；可用
KB_DATABASE_URL / KB_DATA_DIR 覆盖（临时库验收时必须同时指定，避免
文件路径与库错配）。--apply 前请停止 uvicorn 等写入进程。
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
os.environ.setdefault("KB_DATA_DIR", str(SERVER_ROOT / "data"))

from sqlalchemy.engine import make_url  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Document  # noqa: E402
from app.services import content_extract, file_storage  # noqa: E402
from app.services.operation_log import MAX_DOCS_PER_OP, record  # noqa: E402

BATCH = 100
BOOK_TYPES = ("book", "pdf")


def _db_path() -> Path:
    url = make_url(get_settings().database_url)
    if url.get_backend_name() != "sqlite":
        raise SystemExit(f"仅支持 sqlite 数据库，当前为 {url.get_backend_name()}")
    return Path(url.database).resolve()


def _read_rows(db_path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT id, title, type, format, file_path, content FROM documents "
            "WHERE deleted_at IS NULL "
            "AND LENGTH(TRIM(COALESCE(content, ''))) = 0 ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def _content_stats(db_path: Path) -> dict:
    """存活 book/pdf 的正文分布（与 quality_audit 的 trim 口径一致）。"""
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        total, empty, short, long_ = conn.execute(
            "SELECT COUNT(*), "
            "COALESCE(SUM(CASE WHEN LENGTH(TRIM(COALESCE(content, ''))) = 0 "
            "  THEN 1 ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN LENGTH(TRIM(COALESCE(content, ''))) BETWEEN 1 AND 499 "
            "  THEN 1 ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN LENGTH(TRIM(COALESCE(content, ''))) >= 500 "
            "  THEN 1 ELSE 0 END), 0) "
            "FROM documents WHERE deleted_at IS NULL AND type IN ('book', 'pdf')"
        ).fetchone()
    finally:
        conn.close()
    return {
        "alive_book_pdf": total,
        "empty": empty,
        "under_500": short,
        "at_least_500": long_,
        "coverage_pct": round(long_ / total * 100, 1) if total else 0.0,
    }


def _build_plan(rows: list[sqlite3.Row]) -> tuple[list[dict], dict, int]:
    candidates: list[dict] = []
    skipped: Counter[str] = Counter()
    empty_without_file = 0
    for row in rows:
        if not row["file_path"]:
            if (row["type"] or "") in BOOK_TYPES:
                empty_without_file += 1
            continue
        fmt = (row["format"] or "").lower()
        if fmt not in content_extract.TEXT_FORMATS:
            skipped[f"format:{fmt or '<none>'}"] += 1
            continue
        try:
            path = file_storage.abs_path(row["file_path"])
        except ValueError:
            skipped["illegal_path"] += 1
            continue
        if not path.is_file():
            skipped["file_missing"] += 1
            continue
        candidates.append(
            {
                "id": row["id"],
                "title": row["title"],
                "format": fmt,
                "path": str(path),
            }
        )
    return candidates, dict(skipped), empty_without_file


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


def _content_chars(value: str | None) -> int:
    """SQLite length(trim(content)) 的等价实现（NUL 视为终止符）。"""
    return len((value or "").split("\x00", 1)[0].strip(" "))


def _apply(plan: list[dict], verbose: bool) -> dict:
    db = SessionLocal()
    succeeded: list[uuid.UUID] = []
    before_docs: dict[str, dict] = {}
    after_docs: dict[str, dict] = {}
    failed_reasons: Counter[str] = Counter()
    failed_sample: list[dict] = []
    operation_ids: list[str] = []

    def fail(item: dict, reason: str) -> None:
        failed_reasons[reason] += 1
        if len(failed_sample) < 20:
            failed_sample.append({"title": item["title"], "reason": reason})

    try:
        for index, item in enumerate(plan, start=1):
            doc = db.get(Document, uuid.UUID(item["id"]))
            if doc is None or doc.deleted_at is not None:
                fail(item, "document_missing")
            elif _content_chars(doc.content) > 0:
                fail(item, "content_no_longer_empty")
            elif not Path(item["path"]).is_file():
                fail(item, "file_missing")
            else:
                old_chars = _content_chars(doc.content)
                try:
                    text = content_extract.extract_text_from_file(
                        item["format"], Path(item["path"])
                    )
                except Exception:
                    text = ""
                if text:
                    doc.content = text
                    succeeded.append(doc.id)
                    before_docs[str(doc.id)] = {"content_chars": old_chars}
                    after_docs[str(doc.id)] = {"content_chars": len(text)}
                else:
                    fail(item, "empty_result")
            if index % BATCH == 0:
                db.commit()
                db.expunge_all()
                if verbose:
                    print(
                        f"  已处理 {index}/{len(plan)}（成功 {len(succeeded)}）",
                        file=sys.stderr,
                    )
        db.commit()

        if succeeded:
            chunks = [
                succeeded[i : i + MAX_DOCS_PER_OP]
                for i in range(0, len(succeeded), MAX_DOCS_PER_OP)
            ]
            failed_total = len(plan) - len(succeeded)
            for part, chunk in enumerate(chunks, start=1):
                summary = (
                    f"重提取正文：{len(succeeded)} 本成功，{failed_total} 本失败/空"
                    "（脚本 reextract_content.py）"
                )
                if len(chunks) > 1:
                    summary = (
                        f"重提取正文：本批 {len(chunk)} 本成功（脚本 "
                        f"reextract_content.py，第 {part}/{len(chunks)} 批，"
                        f"共 {len(succeeded)} 本成功）"
                    )
                log = record(
                    db,
                    kind="reextract",
                    document_ids=chunk,
                    before_state={
                        "docs": {str(i): before_docs[str(i)] for i in chunk}
                    },
                    after_state={"docs": {str(i): after_docs[str(i)] for i in chunk}},
                    summary=summary,
                    undoable=False,
                )
                operation_ids.append(str(log.id))
            db.commit()
    finally:
        db.close()

    return {
        "attempted": len(plan),
        "success": len(succeeded),
        "failed": len(plan) - len(succeeded),
        "failed_reasons": _ordered(failed_reasons),
        "failed_sample": failed_sample,
        "operation_ids": operation_ids,
    }


def _print_report(report: dict) -> None:
    before = report["before"]
    print(f"# 重提取正文（{'APPLY' if report['mode'] == 'apply' else 'DRY-RUN'}）")
    print()
    print(f"数据库: {report['db']}")
    print(f"数据目录: {report['data_dir']}")
    print(
        "正文统计（存活 book/pdf）: "
        f"空 {before['empty']} / <500 {before['under_500']} / "
        f"≥500 {before['at_least_500']} / 覆盖率 {before['coverage_pct']}%"
    )
    print(f"空正文但无文件（不在重提取范围）: {report['empty_without_file']}")
    print(f"候选: {report['candidates']} {report['candidates_by_format']}")
    print(f"计划阶段跳过: {report['skipped_before_apply'] or '无'}")
    if report["sample"]:
        print("样例:")
        for item in report["sample"][:5]:
            print(f"  [{item['format']}] {item['title']} -> {item['path']}")
    print()

    if report["mode"] != "apply":
        print("仅预览。确认后加 --apply 执行（会先完整备份 DB）。")
        return
    if report["backup"] is None:
        print("没有候选，未备份也未写库。")
        return

    print(f"备份已创建: {report['backup']}")
    print(
        f"完成：尝试 {report['attempted']}，成功 {report['success']}，"
        f"失败/空 {report['failed']} {report['failed_reasons'] or ''}"
    )
    for op_id in report["operation_ids"]:
        print(f"operation log: {op_id}")
    after = report["after"]
    print(
        "变更后统计: "
        f"空 {after['empty']} / <500 {after['under_500']} / "
        f"≥500 {after['at_least_500']} / 覆盖率 {after['coverage_pct']}%"
    )
    print(f"前后差: {report['delta']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="重提取空正文（默认 dry-run）")
    parser.add_argument("--apply", action="store_true", help="执行写入（默认 dry-run）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    db_path = _db_path()
    if not db_path.is_file():
        print(f"数据库不存在: {db_path}", file=sys.stderr)
        return 1

    plan, skipped, empty_without_file = _build_plan(_read_rows(db_path))
    report: dict = {
        "mode": "apply" if args.apply else "dry-run",
        "db": str(db_path),
        "data_dir": str(Path(get_settings().data_dir).resolve()),
        "before": _content_stats(db_path),
        "empty_without_file": empty_without_file,
        "candidates": len(plan),
        "candidates_by_format": _ordered(Counter(item["format"] for item in plan)),
        "skipped_before_apply": skipped,
        "sample": plan[:20],
        "attempted": 0,
        "success": 0,
        "failed": 0,
        "failed_reasons": {},
        "failed_sample": [],
        "backup": None,
        "operation_ids": [],
        "after": None,
        "delta": None,
    }

    if args.apply and plan:
        try:
            report["backup"] = str(_backup_db(db_path))
        except Exception as exc:
            print(f"执行失败: {exc}", file=sys.stderr)
            return 1
        try:
            report.update(_apply(plan, verbose=not args.json))
        except Exception as exc:
            print(f"执行失败: {exc}", file=sys.stderr)
            return 1
        after = _content_stats(db_path)
        before = report["before"]
        report["after"] = after
        report["delta"] = {
            "empty": after["empty"] - before["empty"],
            "under_500": after["under_500"] - before["under_500"],
            "at_least_500": after["at_least_500"] - before["at_least_500"],
            "coverage_pp": round(after["coverage_pct"] - before["coverage_pct"], 1),
        }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
