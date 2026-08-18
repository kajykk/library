"""IndexedDB → 后端 一键迁移端点

流程：
    1) POST /api/migrate/meta   一次性提交元数据（books/categories/bookmarks/notes/records）
    2) POST /api/migrate/file   逐本上传书籍原始文件（multipart）
    3) POST /api/migrate/cover  逐本上传封面（multipart，可选）
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Annotation, Collection, Document, ReadingRecord
from ..schemas import MigrateMetaPayload, MigrateMetaResult
from ..services import file_storage
from .documents import _get_or_create_tag, get_doc_or_404

router = APIRouter(prefix="/migrate", tags=["migrate"])


def _ms_to_dt(ms: int | None) -> datetime | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


@router.post("/meta", response_model=MigrateMetaResult)
def migrate_meta(payload: MigrateMetaPayload, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    id_map: dict[str, uuid.UUID] = {}
    tag_count = 0

    # 1) 分类：按名称 get_or_create，保留排序
    for cat in sorted(payload.categories, key=lambda c: c.sortOrder):
        _ = db.scalar(select(Collection).where(Collection.name == cat.name))
        if _ is None:
            db.add(Collection(name=cat.name, sort_order=cat.sortOrder))
    db.flush()

    # 2) 书籍 → documents
    for book in payload.books:
        doc_id = uuid.uuid4()
        id_map[book.id] = doc_id
        collection = db.scalar(select(Collection).where(Collection.name == book.category))
        doc = Document(
            id=doc_id,
            type="pdf" if book.format == "pdf" else "book",
            title=book.title,
            author=book.author,
            publisher=book.publisher,
            language=book.language or "zh",
            isbn=book.isbn,
            description=book.description,
            format=book.format or None,
            file_size=book.fileSize,
            collection_id=collection.id if collection else None,
            # 旧 readProgress 为 0~100 百分比 → 统一为 0~1
            read_progress=min(1.0, max(0.0, book.readProgress / 100)),
            position={"page": book.lastPosition} if book.lastPosition is not None else None,
            last_read_at=_ms_to_dt(book.lastReadAt),
            meta={"totalPages": book.totalPages} if book.totalPages else {},
            created_at=_ms_to_dt(book.addedAt) or now,
            updated_at=_ms_to_dt(book.lastReadAt) or now,
        )
        for tag_name in book.tags:
            doc.tags.append(_get_or_create_tag(db, tag_name))
            tag_count += 1
        db.add(doc)
    db.flush()

    # 3) 书签/笔记 → annotations
    annotation_count = 0
    for bm in payload.bookmarks:
        new_doc_id = id_map.get(bm.bookId)
        if not new_doc_id:
            continue
        db.add(
            Annotation(
                document_id=new_doc_id,
                type="bookmark",
                anchor={"page": bm.page},
                content=bm.note,
                created_at=_ms_to_dt(bm.createdAt) or now,
            )
        )
        annotation_count += 1
    for note in payload.notes:
        new_doc_id = id_map.get(note.bookId)
        if not new_doc_id:
            continue
        db.add(
            Annotation(
                document_id=new_doc_id,
                type="note",
                anchor={"page": note.page},
                content=note.content,
                created_at=_ms_to_dt(note.createdAt) or now,
                updated_at=_ms_to_dt(note.updatedAt) or now,
            )
        )
        annotation_count += 1

    # 4) 阅读记录
    record_count = 0
    for rec in payload.readingRecords:
        new_doc_id = id_map.get(rec.bookId)
        if not new_doc_id:
            continue
        db.add(
            ReadingRecord(
                document_id=new_doc_id,
                start_time=_ms_to_dt(rec.startTime) or now,
                end_time=_ms_to_dt(rec.endTime) or now,
                units_read=rec.pagesRead,
            )
        )
        record_count += 1

    db.commit()
    return MigrateMetaResult(
        collections=len(payload.categories),
        documents=[{"old_id": old, "new_id": str(new)} for old, new in id_map.items()],
        annotations=annotation_count,
        reading_records=record_count,
        tags=tag_count,
    )


@router.post("/file", status_code=204)
async def migrate_file(
    doc_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    doc = get_doc_or_404(db, doc_id)
    data = await file.read()
    fmt = doc.format or "bin"
    file_storage.delete(doc.file_path)  # 幂等：覆盖旧文件
    doc.file_path = file_storage.save_file(doc.id, fmt, data)
    doc.file_size = len(data)
    db.commit()


@router.post("/cover", status_code=204)
async def migrate_cover(
    doc_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    doc = get_doc_or_404(db, doc_id)
    data = await file.read()
    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    file_storage.delete(doc.cover_path)
    doc.cover_path = file_storage.save_cover(doc.id, data, ext)
    db.commit()
