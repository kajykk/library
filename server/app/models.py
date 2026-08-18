import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

# PostgreSQL 用原生 JSONB，SQLite（开发/测试）退化为 JSON
JSONVariant = JSON().with_variant(JSONB(), "postgresql")

DOCUMENT_TYPES = ("book", "pdf", "article", "note", "webclip")
ANNOTATION_TYPES = ("bookmark", "highlight", "note")
LINK_TYPES = ("mention", "cite", "relate")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("collections.id", ondelete="SET NULL"), nullable=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    documents: Mapped[list["Document"]] = relationship(back_populates="collection")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # DOCUMENT_TYPES
    title: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(Text, default="")
    publisher: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(32), default="zh")
    isbn: Mapped[str] = mapped_column(String(64), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 文件元信息（DB 只存路径，内容落盘）
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    format: Mapped[str | None] = mapped_column(String(16), nullable=True)  # epub/pdf/mobi/azw3/txt/md/html
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # 内容哈希，用于去重

    # note/article/webclip 正文；book 可存提取出的纯文本（供检索）
    content: Mapped[str] = mapped_column(Text, default="")

    meta: Mapped[dict] = mapped_column(JSONVariant, default=dict)  # 章节数、总页数等
    collection_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("collections.id", ondelete="SET NULL"), nullable=True
    )

    # 统一进度语义：0~1 浮点 + position 精确位置 {chapter,page,...}
    read_progress: Mapped[float] = mapped_column(Float, default=0.0)
    position: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    collection: Mapped[Collection | None] = relationship(back_populates="documents")
    tags: Mapped[list["Tag"]] = relationship(
        secondary="document_tags", lazy="selectin", order_by="Tag.name"
    )
    annotations: Mapped[list["Annotation"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)


class DocumentTag(Base):
    __tablename__ = "document_tags"

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


class Annotation(Base):
    __tablename__ = "annotations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # ANNOTATION_TYPES
    anchor: Mapped[dict] = mapped_column(JSONVariant, nullable=False)  # {chapter,page,cfi,text,...}
    quote: Mapped[str] = mapped_column(Text, default="")  # 划线摘录原文
    content: Mapped[str] = mapped_column(Text, default="")  # 用户笔记
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    document: Mapped[Document] = relationship(back_populates="annotations")


class Link(Base):
    __tablename__ = "links"
    __table_args__ = (UniqueConstraint("source_id", "target_id", "type", name="uq_link"),)

    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    type: Mapped[str] = mapped_column(String(16), primary_key=True)  # LINK_TYPES
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DocumentVersion(Base):
    """文档内容快照（保存时自动创建，用于版本历史/恢复）"""

    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReadingRecord(Base):
    __tablename__ = "reading_records"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    units_read: Mapped[int] = mapped_column(Integer, default=0)
