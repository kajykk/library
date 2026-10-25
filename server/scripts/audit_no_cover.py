"""无封面补全审计（只读，默认不复用/下载/写库）。

用法：
    python server/scripts/audit_no_cover.py                 # 审计无封面书籍
    python server/scripts/audit_no_cover.py --json          # JSON 输出
    python server/scripts/audit_no_cover.py --try-extract   # 探测 EPUB 内嵌封面（只读）

范围：
    全库 531 本无封面（含 9 本软删除中的 6 本）；可操作目标是存活 525 本。
--try-extract 只读取 EPUB zip 目录与 OPF，不写文件；PDF 封面需要 PyMuPDF 渲染，
本脚本仅报告候选数量，不实际提取。
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import sqlite3
import sys
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = SERVER_ROOT / "knowledge_base.db"
DATA_DIR = Path(os.getenv("KB_DATA_DIR") or (SERVER_ROOT / "data")).resolve()
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def _connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _resolve_data_path(rel_path: str | None) -> Path | None:
    if not rel_path:
        return None
    path = (DATA_DIR / rel_path).resolve()
    if not path.is_relative_to(DATA_DIR):
        return None
    return path


def _probe_epub(path: Path) -> dict[str, object]:
    """只读探测 EPUB 封面：优先 OPF cover-image，其次首张图片。

    返回 {kind, size, ext} 或 {error}。不写出任何文件。
    """
    try:
        with zipfile.ZipFile(path) as zf:
            opf_path = None
            try:
                container = zf.read("META-INF/container.xml").decode(
                    "utf-8", "ignore"
                )
                root = ET.fromstring(container)
                for node in root.iter():
                    if _localname(node.tag) == "rootfile":
                        opf_path = node.get("full-path")
                        break
            except (KeyError, ET.ParseError):
                opf_path = None

            if opf_path:
                try:
                    opf_root = ET.fromstring(
                        zf.read(opf_path).decode("utf-8", "ignore")
                    )
                except (KeyError, ET.ParseError):
                    opf_root = None
                if opf_root is not None:
                    opf_dir = (
                        opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
                    )
                    cover_href = None
                    by_id: dict[str, str] = {}
                    for node in opf_root.iter():
                        if _localname(node.tag) != "item":
                            continue
                        href = node.get("href")
                        media = (node.get("media-type") or "").lower()
                        if not href or "image" not in media:
                            continue
                        node_id = (node.get("id") or "").lower()
                        by_id[node_id] = href
                        if "cover-image" in (node.get("properties") or ""):
                            cover_href = href
                    if not cover_href:
                        # EPUB2 常见写法: <meta name="cover" content="cover-id"/>
                        for node in opf_root.iter():
                            if _localname(node.tag) == "meta" and (
                                node.get("name") or ""
                            ).lower() == "cover":
                                cover_href = by_id.get(
                                    (node.get("content") or "").lower()
                                )
                                if cover_href:
                                    break
                    if not cover_href:
                        for node in opf_root.iter():
                            if _localname(node.tag) != "item":
                                continue
                            href = node.get("href")
                            media = (node.get("media-type") or "").lower()
                            if href and "image" in media and "cover" in (
                                (node.get("id") or "").lower()
                            ):
                                cover_href = href
                                break
                    if cover_href:
                        member = posixpath.normpath(
                            posixpath.join(opf_dir, urllib.parse.unquote(cover_href))
                        ).lstrip("/")
                        try:
                            info = zf.getinfo(member)
                        except KeyError:
                            info = None
                        if info is not None and info.file_size > 0:
                            return {
                                "kind": "opf_cover",
                                "size": info.file_size,
                                "ext": posixpath.splitext(member)[1].lstrip(".").lower(),
                            }

            # 回退：第一张图片（仅报告候选，可能不是真正封面）
            image_members = [
                info
                for info in zf.infolist()
                if posixpath.splitext(info.filename)[1].lower() in IMAGE_EXTS
                and info.file_size > 0
            ]
            if image_members:
                info = max(image_members, key=lambda item: item.file_size)
                return {
                    "kind": "fallback_image",
                    "size": info.file_size,
                    "ext": posixpath.splitext(info.filename)[1].lstrip(".").lower(),
                }
            return {"error": "no_image_member"}
    except (OSError, zipfile.BadZipFile) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _format_key(value: object) -> str:
    return str(value) if value else "<NULL>"


def main() -> int:
    parser = argparse.ArgumentParser(description="无封面补全审计（只读）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument(
        "--try-extract",
        action="store_true",
        help="探测 EPUB 内嵌封面（只读，仅报告）",
    )
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"数据库不存在: {DB_PATH}", file=sys.stderr)
        return 1

    conn = _connect_ro()
    try:
        all_no_cover = conn.execute(
            "SELECT id, title, author, format, type, file_path, isbn, language, "
            "deleted_at "
            "FROM documents "
            "WHERE cover_path IS NULL OR cover_path = '' "
            "ORDER BY title"
        ).fetchall()
    finally:
        conn.close()

    alive = [dict(row) for row in all_no_cover if row["deleted_at"] is None]
    deleted = len(all_no_cover) - len(alive)
    by_format_all = Counter(_format_key(row["format"]) for row in all_no_cover)
    by_format_alive = Counter(_format_key(row["format"]) for row in alive)
    by_type_all = Counter(str(row["type"] or "<NULL>") for row in all_no_cover)
    by_type_alive = Counter(str(row["type"] or "<NULL>") for row in alive)
    has_isbn_alive = sum(1 for row in alive if (row["isbn"] or "").strip())
    has_author_alive = sum(1 for row in alive if (row["author"] or "").strip())

    extractable: list[dict[str, object]] = []
    probe_errors: list[dict[str, object]] = []
    pdf_candidates = 0
    if args.try_extract:
        for row in alive:
            fmt = (row["format"] or "").lower()
            path = _resolve_data_path(row["file_path"])
            if fmt == "pdf" and path is not None and path.is_file():
                pdf_candidates += 1
            if fmt != "epub":
                continue
            if path is None or not path.is_file():
                probe_errors.append(
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "reason": "file_missing",
                    }
                )
                continue
            result = _probe_epub(path)
            if "error" in result:
                probe_errors.append(
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "reason": result["error"],
                    }
                )
            else:
                extractable.append(
                    {
                        "id": row["id"],
                        "title": row["title"],
                        **result,
                    }
                )

    report: dict[str, object] = {
        "mode": "dry-run",
        "db": str(DB_PATH),
        "data_dir": str(DATA_DIR),
        "total_no_cover": len(all_no_cover),
        "alive_no_cover": len(alive),
        "deleted_no_cover": deleted,
        "by_format": dict(sorted(by_format_all.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_format_alive": dict(
            sorted(by_format_alive.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "by_type": dict(sorted(by_type_all.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_type_alive": dict(
            sorted(by_type_alive.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "has_isbn_alive": has_isbn_alive,
        "has_author_alive": has_author_alive,
        "try_extract": args.try_extract,
        "extractable_embedded": len(extractable) if args.try_extract else None,
        "extractable_opf_cover": sum(
            1 for item in extractable if item.get("kind") == "opf_cover"
        )
        if args.try_extract
        else None,
        "extractable_fallback_image": sum(
            1 for item in extractable if item.get("kind") == "fallback_image"
        )
        if args.try_extract
        else None,
        "probe_errors": probe_errors[:50] if args.try_extract else None,
        "pdf_probe": "not_attempted"
        if args.try_extract
        else None,
        "pdf_candidates": pdf_candidates if args.try_extract else None,
        "extractable_details": extractable[:50] if args.try_extract else None,
        "no_cover_details": [
            {
                "id": row["id"],
                "title": row["title"],
                "author": row["author"],
                "format": row["format"],
                "type": row["type"],
                "isbn": row["isbn"],
                "language": row["language"],
            }
            for row in alive[:50]
        ],
        "no_cover_details_total": len(alive),
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("# 无封面补全审计（只读）")
    print()
    print(f"数据库: {DB_PATH}")
    print(
        f"全库无封面: {len(all_no_cover)}"
        f"（未删除 {len(alive)}；软删除 {deleted}）"
    )
    print()
    print("## 按格式（全库 / 存活）")
    for fmt in sorted(by_format_all, key=lambda key: (-by_format_all[key], key)):
        print(f"  {fmt}: {by_format_all[fmt]} / {by_format_alive.get(fmt, 0)}")
    print()
    print("## 按类型（全库 / 存活）")
    for kind in sorted(by_type_all, key=lambda key: (-by_type_all[key], key)):
        print(f"  {kind}: {by_type_all[kind]} / {by_type_alive.get(kind, 0)}")
    print()
    print(f"存活且有 ISBN（可查元数据 API）: {has_isbn_alive}")
    print(f"存活且有作者（可查元数据 API）: {has_author_alive}")
    print()
    if args.try_extract:
        print(
            f"EPUB 内嵌封面候选: {len(extractable)}"
            f"（OPF cover: {report['extractable_opf_cover']}；"
            f"回退图片: {report['extractable_fallback_image']}）"
        )
        print(f"PDF 文件存在、可做首页渲染的候选: {pdf_candidates}（本脚本不提取）")
        if extractable:
            print()
            print("## 可内嵌提取（前 20）")
            for item in extractable[:20]:
                print(
                    f"  [{item['kind']}] {str(item['title'])[:50]} "
                    f"({int(item['size']):,} bytes, .{item['ext']})"
                )
        if probe_errors:
            print()
            print("## EPUB 探测失败（前 20）")
            for item in probe_errors[:20]:
                print(f"  {str(item['title'])[:50]} — {item['reason']}")
    else:
        print("提示：加 --try-extract 探测 EPUB 内嵌封面（只读，仅报告）。")
    print()
    print("## 补全策略建议")
    print("1. 先处理 EPUB 内嵌封面候选（零网络依赖），仍需单独授权再写入。")
    print("2. PDF 用 PyMuPDF 渲染首页作为封面；TXT 无内嵌封面。")
    print("3. 有 ISBN/作者的记录可用 metadata_fetch 查 Open Library / Google Books。")
    print("4. 无法补全的记录保留现状，不强制。")
    print()
    print("注意：本脚本只审计和报告，不下载、不写入、不修改任何文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
