"""数据巡检：检查知识库的一致性问题，输出报告。

用法（在 server 目录下）：
    python scripts/audit_db.py [--db sqlite:///./knowledge_base.db] [--data-dir ./data]

检查项：
  1. 文档统计（总数 / 按类型 / 软删除）
  2. 缺失文件：file_path 指向的文件不存在
  3. 孤立文件：data/files 下未被任何文档引用的文件
  4. 无正文书籍：content 为空（按格式统计）
  5. 重复 sha256：内容哈希相同的文档（去重冲突候选）
  6. 协作孤儿房间：collab.db 中找不到对应文档的房间

退出码 0 表示无异常项，非 0 表示至少一项异常。
"""

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, func, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import Document  # noqa: E402


def parse_args():
    p = argparse.ArgumentParser(description="知识库数据巡检")
    p.add_argument("--db", default=None, help="数据库连接串（默认取 KB_DATABASE_URL 或 ./knowledge_base.db）")
    p.add_argument("--data-dir", default=None, help="数据目录（默认取 KB_DATA_DIR 或 ./data）")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    data_dir = Path(args.data_dir or "./data").resolve()
    engine = create_engine(args.db or "sqlite:///./knowledge_base.db")

    with engine.connect() as conn:
        if conn.dialect.has_table(conn, "documents") is False:
            print("数据库为空或未迁移（无 documents 表），请先 alembic upgrade head")
            return 1

    problems = 0

    with Session(engine) as db:
        total = db.scalar(select(func.count()).select_from(Document))
        deleted = db.scalar(
            select(func.count()).select_from(Document).where(Document.deleted_at.isnot(None))
        )
        by_type = dict(
            db.execute(
                select(Document.type, func.count()).group_by(Document.type)
            ).all()
        )
        print(f"== 文档统计 ==  总数 {total}（软删除 {deleted}）")
        print("  按类型:", ", ".join(f"{t}={c}" for t, c in sorted(by_type.items())))

        # 2. 缺失文件
        missing = []
        rows = db.execute(
            select(Document.id, Document.title, Document.file_path).where(
                Document.file_path.isnot(None), Document.deleted_at.is_(None)
            )
        ).all()
        for doc_id, title, rel in rows:
            if not rel:
                continue
            if not (data_dir / rel).is_file():
                missing.append((str(doc_id)[:8], title, rel))
        if missing:
            problems += 1
            print(f"\n== 缺失文件 ==  {len(missing)} 条")
            for doc_id, title, rel in missing[:20]:
                print(f"  - {doc_id} 《{title}》 -> {rel}")
            if len(missing) > 20:
                print(f"  ... 还有 {len(missing) - 20} 条")
        else:
            print("\n== 缺失文件 ==  无")

        # 3. 孤立文件
        referenced_names = {Path(rel).name for _, _, rel in rows if rel}
        files_dir = data_dir / "files"
        if files_dir.is_dir():
            orphans = [p for p in files_dir.iterdir() if p.is_file() and p.name not in referenced_names]
            if orphans:
                problems += 1
                print(f"\n== 孤立文件 ==  {len(orphans)} 个未被引用")
                for p in sorted(orphans)[:20]:
                    print(f"  - {p.name}（{p.stat().st_size / 1024:.0f} KB）")
                if len(orphans) > 20:
                    print(f"  ... 还有 {len(orphans) - 20} 个")
            else:
                print("\n== 孤立文件 ==  无")
        else:
            print("\n== 孤立文件 ==  data/files 目录不存在（跳过）")

        # 4. 无正文书籍（笔记/剪藏可为空，不计入异常）
        empty = db.execute(
            select(Document.type, Document.format, func.count())
            .where(Document.deleted_at.is_(None), func.length(func.coalesce(Document.content, "")) == 0)
            .group_by(Document.type, Document.format)
        ).all()
        if empty:
            book_empty = sum(c for t, _, c in empty if t in ("book", "pdf"))
            if book_empty:
                problems += 1
            print(f"\n== 无正文 ==  {sum(c for _, _, c in empty)} 条（书籍 {book_empty}）")
            for t, fmt, c in empty:
                print(f"  - {t} / {fmt}: {c}")
        else:
            print("\n== 无正文 ==  无")

        # 5. 重复 sha256
        dups = db.execute(
            select(Document.sha256, func.count(), func.group_concat(Document.title, " | "))
            .where(Document.sha256.isnot(None), Document.deleted_at.is_(None))
            .group_by(Document.sha256)
            .having(func.count() > 1)
        ).all()
        if dups:
            problems += 1
            print(f"\n== 重复 sha256 ==  {len(dups)} 组")
            for sha, c, titles in dups[:20]:
                print(f"  - {sha[:12]}… × {c}: {titles[:200]}")
        else:
            print("\n== 重复 sha256 ==  无")

    # 6. 协作孤儿房间
    collab_db = data_dir / "collab.db"
    if collab_db.is_file():
        with sqlite3.connect(collab_db) as conn:
            room_rows = conn.execute(
                "SELECT DISTINCT path FROM (SELECT path FROM yupdates UNION SELECT path FROM ycheckpoints)"
            ).fetchall()
        room_ids = {r[0] for r in room_rows}
        with Session(engine) as db:
            known = set(
                db.scalars(select(Document.id)).all()
            )
            known_ids = {f"doc-{uid}" for uid in known}
        orphans = sorted(room_ids - known_ids)
        if orphans:
            problems += 1
            print(f"\n== 协作孤儿房间 ==  {len(orphans)} 个（collab.db 中无对应文档）")
            for o in orphans[:20]:
                print(f"  - {o}")
        else:
            print("\n== 协作孤儿房间 ==  无")
    else:
        print("\n== 协作孤儿房间 ==  collab.db 不存在（跳过）")

    print(f"\n{'发现异常，请检查上方报告' if problems else '巡检通过'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
