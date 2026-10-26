"""入库共用逻辑（B2-01）：上传与目录导入共享格式校验/去重/元数据/正文提取。"""

import hashlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sqlalchemy.orm import Session

from ..models import Document, utcnow

SUPPORTED_FORMATS = {"epub", "pdf", "mobi", "azw3", "azw", "txt"}


class IngestDuplicate(Exception):
    def __init__(self, existing_id: uuid.UUID, existing_title: str):
        self.existing_id = existing_id
        self.existing_title = existing_title
        super().__init__(f"重复文档: {existing_title}")


class IngestError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class IngestOutcome:
    document_id: uuid.UUID | None
    status: Literal["imported", "skipped"]
    title: str | None = None
    reason: str | None = None
    existing_id: uuid.UUID | None = None


def validate_format(filename: str) -> str:
    """校验文件格式，返回小写扩展名。不支持则抛 IngestError。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SUPPORTED_FORMATS:
        raise IngestError("unsupported_format", f"不支持的文件格式: {ext or filename}")
    return ext


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def finalize_ingest(
    db: Session,
    *,
    doc_id: uuid.UUID,
    filename: str,
    fmt: str,
    rel_path: str,
    file_size: int,
    sha256: str,
    collection_id: uuid.UUID | None,
    source: str,
    source_path: str | None = None,
) -> IngestOutcome:
    """完成入库：去重检查 + 创建 Document 行。"""
    # SHA 去重
    if sha256:
        existing = db.query(Document).filter(
            Document.sha256 == sha256,
            Document.deleted_at.is_(None),
        ).first()
        if existing:
            return IngestOutcome(
                document_id=None,
                status="skipped",
                title=filename,
                reason="duplicate_sha256",
                existing_id=existing.id,
            )

    title = Path(filename).stem
    doc = Document(
        id=doc_id,
        type="book" if fmt != "pdf" else "pdf",
        title=title,
        content="",
        file_path=rel_path,
        file_size=file_size,
        format=fmt,
        sha256=sha256,
        collection_id=collection_id,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(doc)
    return IngestOutcome(document_id=doc_id, status="imported", title=title)
