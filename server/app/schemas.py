import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .models import ANNOTATION_TYPES, DOCUMENT_TYPES, LINK_TYPES


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- Collections ----------
class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: uuid.UUID | None = None
    sort_order: int = 0


class CollectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: uuid.UUID | None = None
    sort_order: int | None = None


class CollectionOut(ORMModel):
    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None
    sort_order: int
    created_at: datetime


# ---------- Tags ----------
class TagOut(ORMModel):
    id: uuid.UUID
    name: str


class TagsSet(BaseModel):
    tags: list[str]


# ---------- Documents ----------
class DocumentCreate(BaseModel):
    type: str = Field(pattern="^(note|article|webclip)$")
    title: str = Field(min_length=1)
    content: str = ""
    author: str = ""
    publisher: str = ""
    source_url: str | None = None
    collection_id: uuid.UUID | None = None
    format: str | None = None
    tags: list[str] = []


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    author: str | None = None
    publisher: str | None = None
    language: str | None = None
    isbn: str | None = None
    description: str | None = None
    source_url: str | None = None
    content: str | None = None
    collection_id: uuid.UUID | None = None
    read_progress: float | None = Field(default=None, ge=0, le=1)
    position: dict | None = None
    last_read_at: datetime | None = None
    meta: dict | None = None


class DocumentOut(ORMModel):
    id: uuid.UUID
    type: str
    title: str
    author: str
    publisher: str
    language: str
    isbn: str
    description: str
    source_url: str | None
    file_path: str | None
    cover_path: str | None
    file_size: int
    format: str | None
    content: str
    meta: dict
    collection_id: uuid.UUID | None
    read_progress: float
    position: dict | None
    last_read_at: datetime | None
    tags: list[TagOut]
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


# ---------- Annotations ----------
class AnnotationCreate(BaseModel):
    document_id: uuid.UUID
    type: str = Field(pattern="^(bookmark|highlight|note)$")
    anchor: dict
    quote: str = ""
    content: str = ""
    color: str | None = None


class AnnotationUpdate(BaseModel):
    quote: str | None = None
    content: str | None = None
    color: str | None = None
    anchor: dict | None = None


class AnnotationOut(ORMModel):
    id: uuid.UUID
    document_id: uuid.UUID
    type: str
    anchor: dict
    quote: str
    content: str
    color: str | None
    created_at: datetime
    updated_at: datetime


# ---------- Links ----------
class LinkCreate(BaseModel):
    source_id: uuid.UUID
    target_id: uuid.UUID
    type: str = Field(default="mention", pattern="^(mention|cite|relate)$")


class LinkOut(ORMModel):
    source_id: uuid.UUID
    target_id: uuid.UUID
    type: str
    created_at: datetime


class GraphNode(ORMModel):
    id: uuid.UUID
    title: str
    type: str


class GraphEdge(BaseModel):
    source: uuid.UUID
    target: uuid.UUID
    type: str


class GraphOut(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


# ---------- 文档版本历史 ----------
class DocumentVersionSummary(BaseModel):
    id: uuid.UUID
    title: str
    preview: str
    created_at: datetime


class DocumentVersionOut(ORMModel):
    id: uuid.UUID
    document_id: uuid.UUID
    title: str
    content: str
    created_at: datetime


# ---------- ReadingRecords ----------
class ReadingRecordCreate(BaseModel):
    document_id: uuid.UUID
    start_time: datetime
    end_time: datetime
    units_read: int = 0


class ReadingRecordOut(ORMModel):
    id: uuid.UUID
    document_id: uuid.UUID
    start_time: datetime
    end_time: datetime
    units_read: int


# ---------- 迁移（IndexedDB → 后端）----------
class MigrateCategory(BaseModel):
    id: str
    name: str
    parentId: str | None = None
    sortOrder: int = 0


class MigrateBook(BaseModel):
    id: str
    title: str
    author: str = "未知作者"
    publisher: str = ""
    category: str = ""
    format: str = ""
    description: str = ""
    addedAt: int = 0
    lastReadAt: int | None = None
    readProgress: int = 0  # 旧数据为 0~100 百分比
    lastPosition: int | None = None
    totalPages: int = 0
    fileSize: int = 0
    language: str = "zh"
    isbn: str = ""
    tags: list[str] = []


class MigrateBookmark(BaseModel):
    id: str
    bookId: str
    page: int = 0
    note: str = ""
    createdAt: int = 0


class MigrateNote(BaseModel):
    id: str
    bookId: str
    page: int = 0
    content: str = ""
    createdAt: int = 0
    updatedAt: int = 0


class MigrateReadingRecord(BaseModel):
    id: str
    bookId: str
    startTime: int = 0
    endTime: int = 0
    pagesRead: int = 0


class MigrateMetaPayload(BaseModel):
    books: list[MigrateBook] = []
    categories: list[MigrateCategory] = []
    bookmarks: list[MigrateBookmark] = []
    notes: list[MigrateNote] = []
    readingRecords: list[MigrateReadingRecord] = []


class MigrateMetaResult(BaseModel):
    collections: int
    documents: list[dict]  # [{old_id, new_id}]
    annotations: int
    reading_records: int
    tags: int


__all__ = [
    "ANNOTATION_TYPES",
    "DOCUMENT_TYPES",
    "LINK_TYPES",
]
