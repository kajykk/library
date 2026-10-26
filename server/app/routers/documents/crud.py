"""文档 CRUD、列表与重索引路由。"""

import re
import uuid

from fastapi import Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer, selectinload

from ...database import get_db
from ...models import Document, utcnow
from ...schemas import DocumentCreate, DocumentOut, DocumentUpdate
from ...services import file_storage, reindex_job
from ...services.sql_utils import escape_like
from ...services.tags import get_or_create_tag
from ._shared import get_doc_or_404, router
from .filters import apply_document_filters
from .insight import sync_mention_links
from .versions import _snapshot_version

# 列表接口的规模上限：此前 limit 默认 None = 返回全表，调用方（书架/命令面板/双链补全）
# 全部不带 limit，等于任何一次列表都要把整库的 content 读进内存。
LIST_DEFAULT_LIMIT = 200
LIST_HARD_LIMIT = 1000

SUMMARY_PREVIEW_CHARS = 200


# 摘要只需前 N 个字符（空白压平后取 SUMMARY_PREVIEW_CHARS 字），按此长度取前缀即可
CONTENT_PREVIEW_FETCH_CHARS = SUMMARY_PREVIEW_CHARS * 4


def summarize_content(content: str | None, limit: int = SUMMARY_PREVIEW_CHARS) -> str:
    """列表场景的正文摘要：压平空白后截断，避免大书库一次性传输全文"""
    plain = re.sub(r"\s+", " ", content or "").strip()
    if len(plain) <= limit:
        return plain
    return plain[:limit] + "…"


@router.get("", response_model=list[DocumentOut])
def list_documents(
    type: str | None = None,
    types: str | None = None,
    collection_id: uuid.UUID | None = None,
    tag: str | None = None,
    q: str | None = None,
    author: str | None = None,
    status: str | None = None,
    quality: str | None = None,
    format: str | None = None,
    language: str | None = None,
    sort: str = "created_at_desc",
    include_deleted: bool = False,
    include_content: bool = False,
    limit: int | None = Query(default=None, ge=1, le=LIST_HARD_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(Document).options(selectinload(Document.tags))
    if not include_content:
        # 轻量模式下正文不进 ORM：每行数百 KB 的全文被读出是列表的主要内存开销
        stmt = stmt.options(defer(Document.content))
    stmt = apply_document_filters(
        stmt,
        db=db,
        include_deleted=include_deleted,
        type=type,
        types=types,
        collection_id=collection_id,
        tag=tag,
        q=q,
        author=author,
        status=status,
        quality=quality,
        format=format,
        language=language,
    )
    # 排序（B3-01）：末位加 Document.id tiebreaker，避免 offset 分页重复/漏项
    sort_map = {
        "created_at_desc": [Document.created_at.desc(), Document.id],
        "created_at_asc": [Document.created_at.asc(), Document.id],
        "title_asc": [Document.title.asc(), Document.id],
        "title_desc": [Document.title.desc(), Document.id],
        "last_read_desc": [Document.last_read_at.desc().nullslast(), Document.id],
        "updated_desc": [Document.updated_at.desc(), Document.id],
    }
    order_cols = sort_map.get(sort, sort_map["created_at_desc"])
    stmt = stmt.order_by(*order_cols).offset(offset)
    # 未显式传 limit 时按默认页大小截断；需要全量的调用方（书架等）显式传 limit=LIST_HARD_LIMIT
    stmt = stmt.limit(limit if limit is not None else LIST_DEFAULT_LIMIT)
    docs = list(db.scalars(stmt))
    if not include_content:
        # 轻量模式（默认）：content 只返回截断摘要；全文走 GET /documents/{id} 详情接口。
        # 摘要只需开头若干字符，故单独一次批量查询取前缀，而不是把全文拉进内存。
        # 注意顺序：必须先 expunge 再写回 content —— 在受管实例上改属性会被 autoflush
        # 判定为用户修改，把 200 字摘要 UPDATE 回库，覆盖掉正文（不可逆）。
        ids = [d.id for d in docs]
        prefixes: dict = {}
        if ids:
            prefixes = dict(
                db.execute(
                    select(
                        Document.id,
                        func.substr(Document.content, 1, CONTENT_PREVIEW_FETCH_CHARS),
                    ).where(Document.id.in_(ids))
                ).all()
            )
        db.expunge_all()
        for d in docs:
            d.content = summarize_content(prefixes.get(d.id) or "")
    return docs


@router.post("", response_model=DocumentOut, status_code=201)
def create_document(payload: DocumentCreate, db: Session = Depends(get_db)):
    """创建无文件的文档（笔记/文章/剪藏）"""
    data = payload.model_dump(exclude={"tags"})
    doc = Document(**data)
    for name in payload.tags:
        doc.tags.append(get_or_create_tag(db, name))
    db.add(doc)
    sync_mention_links(db, doc)
    _snapshot_version(db, doc, doc.title, doc.content or "")
    db.commit()
    return doc


@router.post("/reindex-content")
def reindex_document_content(sync: bool = False, db: Session = Depends(get_db)):
    """为已有书籍补充正文全文索引（升级后一次性执行）。

    sync=true 同步执行并返回统计（测试/脚本用）；默认后台执行，返回任务已启动。
    """
    if sync:
        return reindex_job.run_sync(db)
    if not reindex_job.start():
        raise HTTPException(status_code=409, detail="重索引任务已在运行")
    return {"status": "started"}


@router.get("/reindex-status")
def reindex_status():
    """后台重索引任务进度（设置页轮询用）"""
    return reindex_job.status()


@router.get("/{doc_id}", response_model=DocumentOut)
def get_document(doc_id: uuid.UUID, db: Session = Depends(get_db)):
    return get_doc_or_404(db, doc_id)


@router.patch("/{doc_id}", response_model=DocumentOut)
def update_document(doc_id: uuid.UUID, payload: DocumentUpdate, db: Session = Depends(get_db)):
    doc = get_doc_or_404(db, doc_id)
    previous_title = doc.title
    previous_content = doc.content or ""
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(doc, field, value)
    if payload.content is not None or payload.title is not None:
        sync_mention_links(db, doc)
        _snapshot_version(db, doc, previous_title, previous_content)
    # 重命名安全：其他文档中的 [[旧标题]] 引用同步更新
    if payload.title is not None and payload.title != previous_title and previous_title:
        old_ref = f"[[{previous_title}]]"
        new_ref = f"[[{doc.title}]]"
        refs = db.scalars(
            select(Document).where(
                Document.deleted_at.is_(None),
                Document.id != doc.id,
                Document.content.ilike(f"%{escape_like(old_ref)}%", escape="\\"),
            )
        ).all()
        for ref in refs:
            # ilike 命中不区分大小写，替换也必须忽略大小写（仅改标题大小写的场景此前会漏更新）；
            # 用函数替换器避免标题中的 \ 序列被解释为反向引用
            ref.content = re.sub(
                re.escape(old_ref),
                lambda _m: new_ref,
                ref.content or "",
                flags=re.IGNORECASE,
            )
            sync_mention_links(db, ref)
    db.commit()
    return doc


@router.delete("/{doc_id}", status_code=204)
def delete_document(doc_id: uuid.UUID, hard: bool = False, db: Session = Depends(get_db)):
    # 硬删除允许作用于已软删除的文档（彻底清理）
    doc = get_doc_or_404(db, doc_id, include_deleted=hard)
    file_path, cover_path = doc.file_path, doc.cover_path
    if hard:
        db.delete(doc)
        db.commit()
        # 先落库再删文件：commit 失败时记录仍在、可重试；文件删除失败仅留孤儿磁盘文件
        for p in (file_path, cover_path):
            if p:
                file_storage.delete(p)
    else:
        doc.deleted_at = utcnow()
        db.commit()
