"""无正文补全审计（只读，默认仅审计，不写 DB、不写文件）。

用法：
    python server/scripts/audit_no_content.py                 # 审计 624 本文件型无正文
    python server/scripts/audit_no_content.py --json          # JSON 输出
    python server/scripts/audit_no_content.py --try-extract   # 重新提取（只读，仅报告）
    python server/scripts/audit_no_content.py --try-extract --limit 20  # 抽样

范围（与设置页“全文索引维护”的候选一致）：
    存活、正文为空、format 属于 TEXT_FORMATS、且有 file_path 的文档，共 624 本。
本脚本强制关闭 OCR（KB_ENABLE_OCR=0），只验证文本层提取；扫描版 PDF 的 OCR
补全需要额外授权与计算资源，不在本批执行。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = SERVER_ROOT / "knowledge_base.db"
DATA_DIR = Path(os.getenv("KB_DATA_DIR") or (SERVER_ROOT / "data")).resolve()
sys.path.insert(0, str(SERVER_ROOT))

# 本批只审计文本层；避免环境里 KB_ENABLE_OCR=1 时触发 90s/本的 OCR 慢通道
OCR_ENV_ORIGINAL = os.getenv("KB_ENABLE_OCR")
os.environ["KB_ENABLE_OCR"] = "0"

from app.services.content_extract import TEXT_FORMATS  # noqa: E402

MIN_TEXT_CHARS = 50
MOBI_AVAILABLE = importlib.util.find_spec("mobi") is not None
# 每个文件独立子进程 + 硬超时：content_extract 的共享快池在 PDF 超时后线程仍被占用，
# 持续调用会让队列积压并直接跳过后续文件，污染审计结果。子进程隔离后互不影响。
CHILD_TIMEOUT = 8
EXTRACT_CHILD_CODE = r"""
import json, os, sys
from pathlib import Path
os.environ["KB_ENABLE_OCR"] = "0"
sys.path.insert(0, os.getcwd())
try:
    from app.services.content_extract import extract_text_from_file
    payload = {"text": extract_text_from_file(sys.argv[1], Path(sys.argv[2])) or ""}
except Exception as exc:
    payload = {"error": f"{type(exc).__name__}: {exc}"}
sys.stdout.write(json.dumps(payload, ensure_ascii=False))
sys.stdout.flush()
os._exit(0)
"""


def _stderr_tail(value: object, limit: int = 240) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return str(value).strip()[-limit:]


def _terminate_tree(proc: subprocess.Popen[str]) -> None:
    """Windows venv 的 python.exe 是启动器，kill 它不会杀死真正解析 PDF 的子进程；
    必须用 taskkill /T 结束整棵树，并关闭管道避免 communicate 继续等待。"""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            proc.kill()
    else:
        proc.kill()
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _extract_in_child(fmt: str, path: Path) -> dict[str, object]:
    began = time.monotonic()
    proc = subprocess.Popen(
        [sys.executable, "-c", EXTRACT_CHILD_CODE, fmt, str(path)],
        cwd=str(SERVER_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout_text, stderr = proc.communicate(timeout=CHILD_TIMEOUT)
    except subprocess.TimeoutExpired:
        _terminate_tree(proc)
        return {
            "text": "",
            "reason": "timeout",
            "elapsed_s": round(time.monotonic() - began, 3),
            "stderr_tail": "",
        }
    elapsed = round(time.monotonic() - began, 3)
    stdout = (stdout_text or "").strip()
    try:
        payload = json.loads(stdout.splitlines()[-1]) if stdout else {}
    except json.JSONDecodeError:
        payload = {}
    text = str(payload.get("text") or "")
    if "error" in payload:
        reason = f"error: {payload['error']}"
    elif "正文提取超时" in stderr:
        reason = "timeout"
    elif "提取队列积压" in stderr:
        reason = "queue_backlog"
    elif "正文提取失败" in stderr:
        reason = "error"
    elif not text.strip():
        reason = "empty_text"
    else:
        reason = "too_short"
    return {
        "text": text,
        "reason": reason,
        "elapsed_s": elapsed,
        "stderr_tail": _stderr_tail(stderr),
        "child_exit": proc.returncode,
    }


def _connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _resolve_data_path(rel_path: str | None) -> Path | None:
    if not rel_path:
        return None
    path = (DATA_DIR / rel_path).resolve()
    if not path.is_relative_to(DATA_DIR):
        return None
    return path


def _format_key(value: object) -> str:
    return str(value) if value else "<NULL>"


def main() -> int:
    parser = argparse.ArgumentParser(description="无正文补全审计（只读）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument(
        "--try-extract",
        action="store_true",
        help="重新提取正文（只读，不写 DB）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="仅抽样前 N 本（0=全部，默认全部）",
    )
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"数据库不存在: {DB_PATH}", file=sys.stderr)
        return 1

    conn = _connect_ro()
    try:
        blank_rows = conn.execute(
            "SELECT id, title, author, format, type, file_path, deleted_at "
            "FROM documents "
            "WHERE TRIM(COALESCE(content, '')) = '' "
            "ORDER BY title"
        ).fetchall()
    finally:
        conn.close()

    all_blank = [dict(row) for row in blank_rows]
    alive_blank = [row for row in all_blank if row["deleted_at"] is None]
    target = [
        row
        for row in alive_blank
        if (row["format"] or "").lower() in TEXT_FORMATS and (row["file_path"] or "")
    ]
    if args.limit and args.limit > 0:
        target = target[: args.limit]

    by_format_all = Counter(_format_key(row["format"]) for row in all_blank)
    by_format_alive = Counter(_format_key(row["format"]) for row in alive_blank)
    by_format_target = Counter(_format_key(row["format"]) for row in target)

    extractable: list[dict[str, object]] = []
    still_empty: list[dict[str, object]] = []
    started_at = time.monotonic()

    if args.try_extract:
        total = len(target)
        for index, row in enumerate(target, 1):
            fmt = (row["format"] or "").lower()
            path = _resolve_data_path(row["file_path"])
            elapsed = 0.0
            if fmt in {"mobi", "azw", "azw3"} and not MOBI_AVAILABLE:
                still_empty.append(
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "format": fmt,
                        "reason": "dependency_missing:mobi",
                    }
                )
            elif path is None or not path.is_file():
                still_empty.append(
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "format": fmt,
                        "reason": "file_missing",
                    }
                )
            else:
                result = _extract_in_child(fmt, path)
                text = str(result["text"])
                reason = str(result["reason"])
                elapsed = float(result["elapsed_s"])
                stripped_len = len(text.strip())
                if stripped_len > MIN_TEXT_CHARS:
                    extractable.append(
                        {
                            "id": row["id"],
                            "title": row["title"],
                            "format": fmt,
                            "extracted_len": stripped_len,
                            "elapsed_s": elapsed,
                        }
                    )
                else:
                    item = {
                        "id": row["id"],
                        "title": row["title"],
                        "format": fmt,
                        "reason": reason,
                        "extracted_len": stripped_len,
                        "elapsed_s": elapsed,
                    }
                    if result.get("stderr_tail"):
                        item["stderr_tail"] = result["stderr_tail"]
                    still_empty.append(item)
            if index % 25 == 0 or index == total:
                print(
                    f"[{index}/{total}] 可提取 {len(extractable)} / "
                    f"仍为空 {len(still_empty)}",
                    file=sys.stderr,
                    flush=True,
                )

    elapsed_total = round(time.monotonic() - started_at, 2)
    reason_counts = Counter(str(item["reason"]) for item in still_empty)
    report: dict[str, object] = {
        "mode": "dry-run",
        "db": str(DB_PATH),
        "data_dir": str(DATA_DIR),
        "total_no_content": len(target),
        "all_blank_total": len(all_blank),
        "alive_blank_total": len(alive_blank),
        "by_format": dict(
            sorted(by_format_target.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "by_format_all": dict(
            sorted(by_format_all.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "by_format_alive": dict(
            sorted(by_format_alive.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "try_extract": args.try_extract,
        "limited": bool(args.limit),
        "limit": args.limit,
        "extractable": len(extractable) if args.try_extract else None,
        "still_empty": len(still_empty) if args.try_extract else None,
        "still_empty_reasons": dict(reason_counts) if args.try_extract else None,
        "extractable_details": extractable[:50] if args.try_extract else None,
        "still_empty_details": still_empty[:50] if args.try_extract else None,
        "elapsed_seconds": elapsed_total if args.try_extract else None,
        "extraction_mode": "subprocess" if args.try_extract else None,
        "child_timeout_seconds": CHILD_TIMEOUT if args.try_extract else None,
        "ocr_forced_off": True,
        "ocr_env_original": OCR_ENV_ORIGINAL,
        "mobi_available": MOBI_AVAILABLE,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("# 无正文补全审计（只读）")
    print()
    print(f"数据库: {DB_PATH}")
    print(
        f"全库空正文: {len(all_blank)}；存活空正文: {len(alive_blank)}；"
        f"文件型可提取候选: {len(target)}"
    )
    print()
    print("## 按格式（可提取候选）")
    for fmt in sorted(by_format_target, key=lambda key: (-by_format_target[key], key)):
        print(f"  {fmt}: {by_format_target[fmt]}")
    print()
    print("## 按格式（全库 / 存活）")
    for fmt in sorted(by_format_all, key=lambda key: (-by_format_all[key], key)):
        print(f"  {fmt}: {by_format_all[fmt]} / {by_format_alive.get(fmt, 0)}")
    print()
    if args.try_extract:
        print(
            f"可重新提取: {len(extractable)}；仍为空: {len(still_empty)}"
            f"（耗时 {elapsed_total:.1f}s，OCR 强制关闭，mobi 依赖"
            f"{'可用' if MOBI_AVAILABLE else '缺失'}）"
        )
        print()
        if extractable:
            print("## 可提取（前 20）")
            for item in extractable[:20]:
                print(
                    f"  [{item['format']}] {str(item['title'])[:50]} "
                    f"({int(item['extracted_len']):,} chars, {item['elapsed_s']}s)"
                )
        if still_empty:
            print()
            print("## 仍为空（前 20）")
            for item in still_empty[:20]:
                print(
                    f"  [{item['format']}] {str(item['title'])[:50]} "
                    f"— {item['reason']}"
                )
            print()
            print("## 仍为空原因汇总")
            for reason, count in reason_counts.most_common():
                print(f"  {reason}: {count}")
    else:
        print("提示：加 --try-extract 重新提取正文（只读，仅报告；可加 --limit 抽样）。")
    print()
    print("## 补全策略建议")
    print("1. 可提取项通过设置页“全文索引维护”写入 content（会更新 FTS），需单独授权。")
    print("2. timeout 的 PDF 多为扫描版；安装 OCR 依赖后按 ocr_classify 慢通道处理。")
    print("3. mobi/azw3 需先安装 mobi 依赖（当前缺失时解包返回空）。")
    print("4. 143B/0B 文件（P3-3 可疑重复组）建议重新下载，而不是反复提取。")
    print()
    print("注意：本脚本只审计和报告，不写 DB、不写文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
