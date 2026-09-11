"""Markdown 批量导出：全部笔记/文章/剪藏打包为 zip；标注导出（MD zip + TSV）"""

import io
import re
import zipfile
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Annotation, Document

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
    index_lines = ["# 知识库导出", "", f"导出时间：{datetime.now().isoformat(timespec='seconds')}", ""]
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


# ---------- 标注导出（按书分组 Markdown zip / Anki 可导入 TSV） ----------


def _annotation_md_lines(ann: Annotation) -> list[str]:
    lines: list[str] = []
    quote = (ann.quote or "").strip()
    note = (ann.content or "").strip()
    type_label = {"highlight": "划线", "note": "批注", "bookmark": "书签"}.get(ann.type, ann.type)
    lines.append(f"### {type_label} · {ann.created_at:%Y-%m-%d %H:%M}")
    lines.append("")
    if quote:
        lines.extend(f"> {line}" for line in quote.splitlines())
        lines.append("")
    if note:
        lines.append(note)
        lines.append("")
    return lines


@router.get("/annotations")
def export_annotations(db: Session = Depends(get_db)):
    """全部标注导出：按书分组打包 Markdown zip（划线/批注/书签）"""
    rows = db.execute(
        select(Annotation, Document.title)
        .join(Document, Document.id == Annotation.document_id)
        .where(Document.deleted_at.is_(None))
        .order_by(Document.title, Annotation.created_at)
    ).all()
    groups: dict[str, list[Annotation]] = {}
    for ann, title in rows:
        groups.setdefault((title or "无标题").strip() or "无标题", []).append(ann)

    buffer = io.BytesIO()
    index_lines = ["# 标注导出", "", f"导出时间：{datetime.now().isoformat(timespec='seconds')}", ""]
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, (title, annotations) in enumerate(sorted(groups.items()), start=1):
            lines: list[str] = []
            for ann in annotations:
                lines.extend(_annotation_md_lines(ann))
            body = "\n".join(lines).strip()
            md = f"# 《{title}》标注\n\n共 {len(annotations)} 条\n\n{body}\n"
            zf.writestr(_slug(f"《{title}》标注", i), md)
            index_lines.append(f"- {title}（{len(annotations)} 条）")
        zf.writestr("index.md", "\n".join(index_lines))

    buffer.seek(0)
    date_tag = datetime.now().strftime("%Y%m%d")
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="annotations_export_{date_tag}.zip"',
        },
    )


@router.get("/annotations.tsv")
def export_annotations_tsv(db: Session = Depends(get_db)):
    """标注 TSV 导出（Anki/表格可导入）：front=原文/内容，back=批注+出处

    utf-8-sig 带 BOM，Excel 直接打开不乱码。
    """
    rows = db.execute(
        select(Annotation, Document.title)
        .join(Document, Document.id == Annotation.document_id)
        .where(Document.deleted_at.is_(None))
        .order_by(Document.title, Annotation.created_at)
    ).all()

    def tsv_cell(value: str) -> str:
        return (value or "").replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")

    lines = ["front\tback\ttype\tbook"]
    for ann, title in rows:
        front = (ann.quote or "").strip() or (ann.content or "").strip()
        back_parts = []
        if ann.quote and ann.content:
            back_parts.append((ann.content or "").strip())
        back_parts.append(f"— 《{title or "无标题"}》")
        lines.append(
            f"{tsv_cell(front)}\t{tsv_cell(chr(10).join(back_parts))}\t{ann.type}\t{tsv_cell(title or '')}"
        )

    payload = "\ufeff" + "\n".join(lines) + "\n"
    date_tag = datetime.now().strftime("%Y%m%d")
    return StreamingResponse(
        iter([payload.encode("utf-8")]),
        media_type="text/tab-separated-values; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="annotations_{date_tag}.tsv"',
        },
    )
