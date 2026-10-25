"""文档上传（元数据提取、去重、自动分类）与后台 OCR 分类。"""

import logging
import uuid

from fastapi import BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ...database import SessionLocal, get_db
from ...models import Collection, Document
from ...schemas import DocumentOut
from ...services import auto_classify, content_extract, file_storage, metadata_extract, ocr_classify
from ._shared import router, settings

# 上传格式白名单：拒绝可执行/未知扩展名，防止任意文件落盘与浏览器内容嗅探
UPLOAD_FORMAT_WHITELIST = {"epub", "pdf", "mobi", "azw", "azw3", "txt", "md"}


@router.post("/upload", response_model=DocumentOut, status_code=201)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    collection_id: uuid.UUID | None = Form(default=None),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
):
    """上传书籍/PDF：流式写入磁盘 + 后端提取元数据与封面

    SHA-256 / 元数据解析 / 正文提取均为秒级同步重活，必须经
    run_in_threadpool 下放线程池，否则会阻塞事件循环、冻结全站请求。

    限流已由全局 RateLimitMiddleware 按路径处理（/api/documents/upload 50/60s），
    此处不再手工调用 —— 否则同一请求计两次。
    """
    max_bytes = settings.upload_max_size_mb * 1024 * 1024

    filename = file.filename or "unnamed"
    fmt = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if fmt not in UPLOAD_FORMAT_WHITELIST:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format: .{fmt or '(none)'}；allowed: {', '.join(sorted(UPLOAD_FORMAT_WHITELIST))}",
        )
    doc_id = uuid.uuid4()

    try:
        rel_path, file_size = await file_storage.stream_file_to_disk(
            file, doc_id, fmt or "bin", max_bytes
        )
    except ValueError:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {settings.upload_max_size_mb} MB upload limit",
        )

    abs_file = file_storage.abs_path(rel_path)

    # SHA-256 去重：同内容文件已在库（含回收站外）则拒绝并提示已有条目
    sha256 = await run_in_threadpool(file_storage.sha256_of, rel_path)
    existing = db.scalar(
        select(Document).where(
            Document.sha256 == sha256,
            Document.deleted_at.is_(None),
        )
    )
    if existing:
        file_storage.delete(rel_path)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "duplicate",
                "message": f"已存在相同文件《{existing.title}》",
                "existing_id": str(existing.id),
                "existing_title": existing.title,
            },
        )

    meta = await run_in_threadpool(
        metadata_extract.extract_metadata_from_file, filename, fmt, abs_file
    )

    text_content = (
        await run_in_threadpool(content_extract.extract_text_from_file, fmt, abs_file)
        if fmt in content_extract.TEXT_FORMATS
        else ""
    )

    if collection_id is None:
        # 三级自动分类：标题/作者关键词 → 已提取正文词频 → （后台）OCR 扫描件
        target = await run_in_threadpool(
            auto_classify.classify_document,
            meta["title"] or filename,
            meta["author"] or "",
            text_content,
        )
        if target:
            col = db.scalar(select(Collection).where(Collection.name == target))
            if col:
                collection_id = col.id
        elif settings.auto_classify_ocr and fmt in ("pdf", "epub"):
            background_tasks.add_task(classify_ocr_background, str(doc_id), fmt, rel_path)

    doc = Document(
        id=doc_id,
        type="pdf" if fmt == "pdf" else "book",
        title=meta["title"] or filename,
        author=meta["author"],
        publisher=meta["publisher"],
        language=meta["language"] or "zh",
        isbn=meta["isbn"],
        description=meta["description"],
        file_path=rel_path,
        file_size=file_size,
        format=fmt or None,
        content=text_content,
        sha256=sha256,
        collection_id=collection_id,
    )
    if meta.get("cover"):
        doc.cover_path = file_storage.save_cover(doc_id, meta["cover"], meta.get("cover_ext", "jpg"))

    db.add(doc)
    db.commit()
    return doc


def classify_ocr_background(doc_id: str, fmt: str, rel_path: str) -> None:
    """后台 OCR 分类：扫描件采样页 OCR → 词频打分 → 若无分类则写入。

    上传响应已返回；独立会话执行，失败仅记日志不影响主流程。
    """
    logger = logging.getLogger("kb.ocr_classify")
    try:
        with SessionLocal() as db:
            doc = db.get(Document, uuid.UUID(doc_id))
            if not doc or doc.deleted_at or doc.collection_id is not None:
                return
            path = file_storage.abs_path(doc.file_path or rel_path)
            if not path.is_file():
                return
            text = ocr_classify.ocr_book_text(path, fmt)
            if len(text) < 500:
                logger.info("OCR 分类无有效文本，跳过: %s", doc.title)
                return
            target = auto_classify.classify_document(doc.title or "", doc.author or "", text)
            if not target:
                logger.info("OCR 分类无主题特征，跳过: %s", doc.title)
                return
            col = db.scalar(select(Collection).where(Collection.name == target))
            if col:
                doc.collection_id = col.id
                db.commit()
                logger.info("OCR 后台分类: 《%s》 -> %s", doc.title, target)
    except Exception as exc:
        logging.getLogger("kb.ocr_classify").warning("OCR 后台分类失败: %s", exc, exc_info=True)
