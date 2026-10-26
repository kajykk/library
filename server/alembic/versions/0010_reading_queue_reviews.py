"""reading_queue_items + annotation_reviews（B4）

Revision ID: 0010_reading_queue_reviews
Revises: 0009_library_indexes
Create Date: 2026-10-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_reading_queue_reviews"
down_revision: Union[str, None] = "0009_library_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "reading_queue_items" not in tables:
        op.create_table(
            "reading_queue_items",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("document_id", sa.Uuid(), nullable=False),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("document_id"),
        )
        op.create_index("ix_reading_queue_status_priority", "reading_queue_items", ["status", "priority", "sort_order", "created_at"])
        op.create_index("ix_reading_queue_document_id", "reading_queue_items", ["document_id"])

    if "annotation_reviews" not in tables:
        op.create_table(
            "annotation_reviews",
            sa.Column("annotation_id", sa.Uuid(), nullable=False),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("interval_days", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("ease", sa.Float(), nullable=False, server_default="2.5"),
            sa.Column("repetitions", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("lapses", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            sa.Column("last_rated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["annotation_id"], ["annotations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("annotation_id"),
        )
        op.create_index("ix_annotation_reviews_status_due", "annotation_reviews", ["status", "due_at"])
        op.create_index("ix_annotation_reviews_due_at", "annotation_reviews", ["due_at"])

    # 结构化回填：阅读队列（在读的书）
    op.execute("""
        INSERT OR IGNORE INTO reading_queue_items (id, document_id, priority, sort_order, status, note, created_at, updated_at)
        SELECT lower(hex(randomblob(16))), d.id, 100, 0, 'active', '', datetime('now'), datetime('now')
        FROM documents d
        WHERE d.deleted_at IS NULL
          AND d.type IN ('book','pdf')
          AND d.read_progress > 0
          AND d.read_progress < 1
          AND NOT EXISTS (SELECT 1 FROM reading_queue_items q WHERE q.document_id = d.id)
    """)

    # 结构化回填：复习行（highlight/note 标注，按 created_at 打散 14 天）
    op.execute("""
        INSERT OR IGNORE INTO annotation_reviews (annotation_id, due_at, interval_days, ease, repetitions, lapses, status, last_rated_at, created_at, updated_at)
        SELECT a.id,
               datetime('now', '+' || (1 + (ROW_NUMBER() OVER (ORDER BY a.created_at) - 1) % 14) || ' days'),
               0, 2.5, 0, 0, 'active', NULL, datetime('now'), datetime('now')
        FROM annotations a
        JOIN documents d ON d.id = a.document_id
        WHERE d.deleted_at IS NULL
          AND a.type IN ('highlight','note')
          AND NOT EXISTS (SELECT 1 FROM annotation_reviews r WHERE r.annotation_id = a.id)
    """)


def downgrade() -> None:
    op.drop_index("ix_annotation_reviews_due_at", table_name="annotation_reviews")
    op.drop_index("ix_annotation_reviews_status_due", table_name="annotation_reviews")
    op.drop_table("annotation_reviews")
    op.drop_index("ix_reading_queue_document_id", table_name="reading_queue_items")
    op.drop_index("ix_reading_queue_status_priority", table_name="reading_queue_items")
    op.drop_table("reading_queue_items")
