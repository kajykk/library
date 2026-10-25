"""书籍封面审计（只读）：报告无封面书籍、孤儿文件、重复封面、损坏引用。

统计口径为全库（含软删除文档）：软删除文档仍占用封面文件，若排除会把它们的
封面误报为孤儿。

用法：
    python server/scripts/audit_covers.py              # 人类可读报告
    python server/scripts/audit_covers.py --json       # JSON 输出（供后续自动化消费）
    python server/scripts/audit_covers.py --out report.md  # 写入文件
只读：不修改数据库、不删除/移动任何文件。
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_ROOT))

DB_PATH = SERVER_ROOT / "knowledge_base.db"
DATA_DIR = SERVER_ROOT / "data"
COVERS_DIR = DATA_DIR / "covers"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="书籍封面审计（只读）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--out", type=str, default=None, help="写入文件路径")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"数据库不存在: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    # 1. 基本统计（全库口径，含软删除）
    total_docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    docs_with_cover = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE cover_path IS NOT NULL AND cover_path != ''"
    ).fetchone()[0]
    docs_without_cover = total_docs - docs_with_cover
    soft_deleted_docs = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE deleted_at IS NOT NULL"
    ).fetchone()[0]

    # 2. 无封面书籍列表
    no_cover_docs = conn.execute(
        "SELECT id, title, author, format, type FROM documents "
        "WHERE cover_path IS NULL OR cover_path = '' ORDER BY title"
    ).fetchall()

    # 3. 封面文件引用完整性
    referenced_paths = [
        r["cover_path"]
        for r in conn.execute(
            "SELECT cover_path FROM documents WHERE cover_path IS NOT NULL AND cover_path != ''"
        ).fetchall()
    ]
    referenced_basename_list = [os.path.basename(p) for p in referenced_paths]
    referenced_basenames = set(referenced_basename_list)

    # 4. 磁盘文件
    disk_files = set()
    if COVERS_DIR.is_dir():
        disk_files = {f for f in os.listdir(COVERS_DIR) if os.path.isfile(COVERS_DIR / f)}
    orphans = disk_files - referenced_basenames
    missing_on_disk = referenced_basenames - disk_files

    # 5. 重复封面（同一文件被多本引用）
    cover_ref_count = Counter(referenced_basename_list)
    dup_covers = {k: v for k, v in cover_ref_count.items() if v > 1}

    # 6. 重复 sha256（同一文件内容被多本引用）
    sha_groups = defaultdict(list)
    for row in conn.execute(
        "SELECT id, title, sha256 FROM documents WHERE sha256 IS NOT NULL AND sha256 != ''"
    ).fetchall():
        sha_groups[row["sha256"]].append({"id": row["id"], "title": row["title"]})
    dup_sha = {k: v for k, v in sha_groups.items() if len(v) > 1}

    # 7. 封面文件大小分布
    cover_sizes = []
    for f in disk_files:
        try:
            cover_sizes.append(os.path.getsize(COVERS_DIR / f))
        except OSError:
            pass
    total_cover_bytes = sum(cover_sizes)
    avg_cover_bytes = total_cover_bytes // len(cover_sizes) if cover_sizes else 0

    # 8. 封面格式分布
    cover_exts = Counter(os.path.splitext(f)[1].lower() for f in disk_files)

    # 9. 无封面书籍按类型分布
    no_cover_by_type = Counter(r["type"] for r in no_cover_docs)

    # 10. 无封面书籍按格式分布
    no_cover_by_format = Counter(r["format"] for r in no_cover_docs)

    conn.close()

    report = {
        "summary": {
            "total_docs": total_docs,
            "soft_deleted_docs": soft_deleted_docs,
            "docs_with_cover": docs_with_cover,
            "docs_without_cover": docs_without_cover,
            "cover_coverage_pct": round(docs_with_cover / total_docs * 100, 1) if total_docs else 0,
            "distinct_cover_files_on_disk": len(disk_files),
            "referenced_cover_files": len(referenced_basenames),
            "orphan_cover_files": len(orphans),
            "missing_cover_files": len(missing_on_disk),
            "duplicate_cover_refs": len(dup_covers),
            "duplicate_sha256_groups": len(dup_sha),
            "total_cover_bytes": total_cover_bytes,
            "avg_cover_bytes": avg_cover_bytes,
        },
        "no_cover_by_type": dict(no_cover_by_type),
        "no_cover_by_format": dict(no_cover_by_format),
        "cover_extensions": dict(cover_exts),
        "orphan_files": sorted(orphans),
        "missing_files": sorted(missing_on_disk),
        "duplicate_cover_refs": {k: v for k, v in list(dup_covers.items())[:20]},
        "duplicate_sha_groups": len(dup_sha),
        "no_cover_docs": [
            {
                "id": r["id"],
                "title": r["title"],
                "author": r["author"],
                "format": r["format"],
                "type": r["type"],
            }
            for r in no_cover_docs[:50]
        ],
        "no_cover_docs_total": len(no_cover_docs),
    }

    if args.json:
        output = json.dumps(report, ensure_ascii=False, indent=2)
    else:
        lines = []
        lines.append("# 书籍封面审计报告（只读）")
        lines.append("")
        lines.append("## 概要")
        lines.append("")
        s = report["summary"]
        lines.append(f"- 总文档数（全库，含软删除）：{s['total_docs']}")
        lines.append(f"- 其中软删除文档：{s['soft_deleted_docs']}")
        lines.append(f"- 有封面：{s['docs_with_cover']}（{s['cover_coverage_pct']}%）")
        lines.append(f"- 无封面：{s['docs_without_cover']}")
        lines.append(f"- 磁盘封面文件数：{s['distinct_cover_files_on_disk']}")
        lines.append(f"- 被引用的封面文件数：{s['referenced_cover_files']}")
        lines.append(f"- 孤儿文件（磁盘有但无引用）：{s['orphan_cover_files']}")
        lines.append(f"- 损坏引用（引用了但磁盘无文件）：{s['missing_cover_files']}")
        lines.append(f"- 重复封面引用（同一文件被多本引用）：{s['duplicate_cover_refs']}")
        lines.append(f"- 重复 sha256 组（同一内容多本）：{s['duplicate_sha256_groups']}")
        lines.append(
            f"- 封面总大小：{s['total_cover_bytes']:,} bytes"
            f"（平均 {s['avg_cover_bytes']:,} bytes/文件）"
        )
        lines.append("")
        lines.append("## 无封面书籍分布")
        lines.append("")
        lines.append("### 按类型")
        for t, n in sorted(report["no_cover_by_type"].items(), key=lambda kv: kv[0] or ""):
            lines.append(f"- {t}: {n}")
        lines.append("")
        lines.append("### 按格式")
        for f, n in sorted(report["no_cover_by_format"].items(), key=lambda kv: kv[0] or ""):
            lines.append(f"- {f or '(无)'}: {n}")
        lines.append("")
        lines.append("## 封面文件格式分布")
        lines.append("")
        for ext, n in sorted(report["cover_extensions"].items()):
            lines.append(f"- {ext or '(无扩展名)'}: {n}")
        lines.append("")
        if report["orphan_files"]:
            lines.append("## 孤儿文件（可清理）")
            lines.append("")
            for f in report["orphan_files"]:
                lines.append(f"- {f}")
            lines.append("")
        if report["missing_files"]:
            lines.append("## 损坏引用（需修复）")
            lines.append("")
            for f in report["missing_files"]:
                lines.append(f"- {f}")
            lines.append("")
        if report["no_cover_docs"]:
            lines.append(f"## 无封面书籍（前 50 / 共 {report['no_cover_docs_total']}）")
            lines.append("")
            for d in report["no_cover_docs"]:
                lines.append(f"- [{d['type']}/{d['format']}] {d['title']} (作者: {d['author'] or '—'})")
            if report["no_cover_docs_total"] > 50:
                lines.append(f"\n... 还有 {report['no_cover_docs_total'] - 50} 本未列出")
            lines.append("")
        lines.append("---")
        lines.append("本报告由 `server/scripts/audit_covers.py` 生成，只读，未修改任何数据。")
        output = "\n".join(lines)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"报告已写入: {args.out}")
    else:
        print(output)


if __name__ == "__main__":
    main()
