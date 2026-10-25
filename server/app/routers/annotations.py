"""annotations 统一批注 API（bookmark / highlight / note）

另含两个知识流转端点：
- POST /{annotation_id}/to-note：标注一键转笔记（引用块 + 批注 + [[书名]] 双链）
- GET  /review：跨书籍标注流（每日回顾页）
"""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Annotation, Document
from ..schemas import AnnotationCreate, AnnotationOut, AnnotationUpdate, DocumentOut
from ..services.sql_utils import escape_like
from .documents import _snapshot_version, sync_mention_links

router = APIRouter(prefix="/annotations", tags=["annotations"])


class AnnotationSearchHit(BaseModel):
    id: UUID
    document_id: UUID
    document_title: str
    document_type: str
    type: str
    anchor: dict
    quote: str
    content: str
    created_at: datetime


@router.get("/search", response_model=list[AnnotationSearchHit])
def search_annotations(
    q: str = Query(default=""),
    limit: int = Query(default=20, le=50),
    db: Session = Depends(get_db),
):
    """跨书籍检索批注（带书名），用于笔记中插入书籍引用"""
    stmt = (
        select(Annotation, Document.title, Document.type)
        .join(Document, Document.id == Annotation.document_id)
        .where(Document.deleted_at.is_(None))
        .order_by(Annotation.created_at.desc())
        .limit(limit)
    )
    query = q.strip()
    if query:
        like = f"%{escape_like(query)}%"
        stmt = stmt.where(
            or_(
                Annotation.quote.ilike(like, escape="\\"),
                Annotation.content.ilike(like, escape="\\"),
            )
        )
    rows = db.execute(stmt).all()
    return [
        AnnotationSearchHit(
            id=ann.id,
            document_id=ann.document_id,
            document_title=doc_title,
            document_type=doc_type,
            type=ann.type,
            anchor=ann.anchor or {},
            quote=ann.quote or "",
            content=ann.content or "",
            created_at=ann.created_at,
        )
        for ann, doc_title, doc_type in rows
    ]


def _get_or_404(db: Session, annotation_id: UUID) -> Annotation:
    obj = db.get(Annotation, annotation_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Annotation not found")
    return obj


@router.get("", response_model=list[AnnotationOut])
def list_annotations(document_id: UUID | None = None, db: Session = Depends(get_db)):
    stmt = select(Annotation).order_by(Annotation.created_at.desc())
    if document_id:
        stmt = stmt.where(Annotation.document_id == document_id)
    return list(db.scalars(stmt))


@router.post("", response_model=AnnotationOut, status_code=201)
def create_annotation(payload: AnnotationCreate, db: Session = Depends(get_db)):
    document = db.get(Document, payload.document_id)
    if document is None or document.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Document not found")
    obj = Annotation(**payload.model_dump())
    db.add(obj)
    db.commit()
    return obj


@router.patch("/{annotation_id}", response_model=AnnotationOut)
def update_annotation(annotation_id: UUID, payload: AnnotationUpdate, db: Session = Depends(get_db)):
    obj = _get_or_404(db, annotation_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)
    db.commit()
    return obj


@router.delete("/{annotation_id}", status_code=204)
def delete_annotation(annotation_id: UUID, db: Session = Depends(get_db)):
    obj = _get_or_404(db, annotation_id)
    db.delete(obj)
    db.commit()


# ---------- 标注 → 知识流转 ----------

class AnnotationToNotePayload(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)


def _derive_note_title(book_title: str, quote: str, note: str) -> str:
    """从原文/批注推导笔记标题：《书名》摘录/批注：前 24 字"""
    excerpt = " ".join((quote or note).split())
    if not excerpt:
        # 纯书签（无原文无批注）：不加多余的"：摘录"尾巴
        return f"《{book_title}》书签"
    if len(excerpt) > 24:
        excerpt = excerpt[:24] + "…"
    kind = "摘录" if quote else "批注"
    return f"《{book_title}》{kind}：{excerpt}"


def _format_anchor_position(anchor: dict) -> str:
    """把 anchor 转成人类可读位置（章节优先，页码兜底）"""
    chapter = anchor.get("chapter")
    if isinstance(chapter, int) and chapter >= 0:
        return f"章节 {chapter}"
    page = anchor.get("page")
    if isinstance(page, int) and page > 0:
        return f"第 {page} 页"
    return ""


@router.post("/{annotation_id}/to-note", response_model=DocumentOut, status_code=201)
def annotation_to_note(
    annotation_id: UUID,
    payload: AnnotationToNotePayload | None = None,
    db: Session = Depends(get_db),
):
    """标注一键转笔记（对标 Readwise 批注流转）。

    生成 note 类型文档：引用块（划线原文）+ 用户批注 + [[书名]] wiki 双链 +
    来源位置；mention 链接同步建立，反向链接与图谱自动联通。
    """
    ann = _get_or_404(db, annotation_id)
    book = db.get(Document, ann.document_id)
    if book is None or book.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Document not found")

    quote = (ann.quote or "").strip()
    user_note = (ann.content or "").strip()
    book_title = (book.title or "").strip() or "未知书籍"

    lines: list[str] = []
    if quote:
        lines.extend(f"> {line}" for line in quote.splitlines())
        lines.append("")
    if user_note:
        lines.append(user_note)
        lines.append("")
    source = f"来源：[[{book_title}]]"
    position = _format_anchor_position(ann.anchor or {})
    if position:
        source += f" · {position}"
    lines.append(source)
    content = "\n".join(lines).strip()

    title = (payload.title if payload else None) or _derive_note_title(book_title, quote, user_note)

    note = Document(
        type="note",
        title=title,
        content=content,
        meta={
            "annotation_id": str(ann.id),
            "source_document_id": str(book.id),
            "source_anchor": ann.anchor or {},
            "workflow": "done",
        },
    )
    db.add(note)
    sync_mention_links(db, note)
    _snapshot_version(db, note, note.title, note.content or "")
    db.commit()
    db.refresh(note)
    return note


@router.get("/review", response_model=list[AnnotationSearchHit])
def review_annotations(
    since: datetime | None = None,
    type: str | None = Query(default=None, pattern="^(bookmark|highlight|note)$"),
    limit: int = Query(default=100, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """每日回顾：跨书籍标注流（按时间倒序），供 /review 页与每日笔记模板复用。

    since 传 ISO 日期过滤"今天新增"；SQLite 存的是 naive UTC，需先归一化。
    """
    stmt = (
        select(Annotation, Document.title, Document.type, Document.id)
        .join(Document, Document.id == Annotation.document_id)
        .where(Document.deleted_at.is_(None))
        .order_by(Annotation.created_at.desc())
    )
    if since is not None:
        if since.tzinfo is not None:
            since = since.astimezone(timezone.utc).replace(tzinfo=None)
        stmt = stmt.where(Annotation.created_at >= since)
    if type is not None:
        stmt = stmt.where(Annotation.type == type)
    rows = db.execute(stmt.offset(offset).limit(limit)).all()
    return [
        AnnotationSearchHit(
            id=ann.id,
            document_id=doc_id,
            document_title=doc_title,
            document_type=doc_type,
            type=ann.type,
            anchor=ann.anchor or {},
            quote=ann.quote or "",
            content=ann.content or "",
            created_at=ann.created_at,
        )
        for ann, doc_title, doc_type, doc_id in rows
    ]
