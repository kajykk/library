"""library_indexes 书架查询索引（B3）

Revision ID: 0009_library_indexes
Revises: 0008_operation_logs
Create Date: 2026-10-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_library_indexes"
down_revision: Union[str, None] = "0008_operation_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEXES = [
    ("ix_documents_alive_type_created", ["deleted_at", "type", "created_at"]),
    ("ix_documents_alive_type_collection", ["deleted_at", "type", "collection_id", "created_at"]),
    ("ix_documents_alive_type_author", ["deleted_at", "type", "author"]),
    ("ix_documents_alive_type_last_read", ["deleted_at", "type", "last_read_at"]),
    ("ix_documents_alive_type_progress", ["deleted_at", "type", "read_progress"]),
    ("ix_documents_alive_updated", ["deleted_at", "updated_at"]),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {i["name"] for i in inspector.get_indexes("documents")}
    for name, cols in _INDEXES:
        if name not in existing:
            op.create_index(name, "documents", cols)


def downgrade() -> None:
    for name, _ in reversed(_INDEXES):
        op.drop_index(name, table_name="documents")
