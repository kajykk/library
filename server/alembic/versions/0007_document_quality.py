"""document_quality 质量快照表（B1）

Revision ID: 0007_document_quality
Revises: 0006_add_sha256
Create Date: 2026-10-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_document_quality"
down_revision: Union[str, None] = "0006_add_sha256"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "document_quality" in inspector.get_table_names():
        return

    op.create_table(
        "document_quality",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("content_chars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("file_exists", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("cover_exists", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("needs_ocr", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("duplicate_group_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("issues", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("scan_error", sa.Text(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("document_id"),
    )
    op.create_index("ix_document_quality_needs_ocr", "document_quality", ["needs_ocr"])
    op.create_index("ix_document_quality_duplicate_group_size", "document_quality", ["duplicate_group_size"])
    op.create_index("ix_document_quality_checked_at", "document_quality", ["checked_at"])


def downgrade() -> None:
    op.drop_index("ix_document_quality_checked_at", table_name="document_quality")
    op.drop_index("ix_document_quality_duplicate_group_size", table_name="document_quality")
    op.drop_index("ix_document_quality_needs_ocr", table_name="document_quality")
    op.drop_table("document_quality")
