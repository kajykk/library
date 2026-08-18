"""documents 搜索向量与 GIN 索引

Revision ID: 0003_documents_search_vector
Revises: 0002_last_read_at
Create Date: 2026-08-17
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003_documents_search_vector"
down_revision: Union[str, None] = "0002_last_read_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE documents
        ADD COLUMN IF NOT EXISTS search_vector tsvector
        GENERATED ALWAYS AS (
            to_tsvector('zh', coalesce(title, '') || ' ' || coalesce(author, '') || ' ' || coalesce(content, ''))
        ) STORED
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_documents_search_vector ON documents USING GIN (search_vector)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_documents_search_vector")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS search_vector")
