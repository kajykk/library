"""Markdown 批量导出：全部笔记/文章/剪藏打包为 zip"""

import io
import re
import zipfile

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Document

router = APIRouter(prefix="/export", tags=["export"])


def _slug(title: str, index: int) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n]+', " ", title or "无标题").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)[:60]
    return f"{index:03d}-{cleaned}.md"


@router.get("/markdown")
def export_markdown(db: Session = Depends(get_db)):
    docs = list(
        db.scalars(
            select(Document)
            .where(
                Document.deleted_at.is_(None),
                Document.type.in_(["note", "article", "webclip"]),
            )
            .order_by(Document.created_at.desc())
        )
    )

    buffer = io.BytesIO()
    index_lines = ["# 知识库导出", "", f"导出时间：{__import__('datetime').datetime.now().isoformat(timespec='seconds')}", ""]
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, doc in enumerate(docs, start=1):
            filename = _slug(doc.title, i)
            tags_line = f"标签：{', '.join(t.name for t in doc.tags)}\n" if doc.tags else ""
            source_line = f"来源：{doc.source_url}\n" if doc.source_url else ""
            md = (
                f"# {doc.title}\n\n"
                f"{tags_line}{source_line}\n"
                f"{doc.content or ''}\n"
            )
            zf.writestr(filename, md)
            index_lines.append(f"- {filename} ({doc.type})")
        zf.writestr("index.md", "\n".join(index_lines))

    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="knowledge_base_export.zip"',
        },
    )
