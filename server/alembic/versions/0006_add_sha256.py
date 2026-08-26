"""documents.sha256 内容哈希（去重）

Revision ID: 0006_add_sha256
Revises: 0005_document_versions
Create Date: 2026-08-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_add_sha256"
down_revision: Union[str, None] = "0005_document_versions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 0001 用当前 metadata 建表时可能已带该列/索引，必须幂等
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("documents")}
    if "sha256" in cols:
        return
    op.add_column("documents", sa.Column("sha256", sa.String(64), nullable=True))
    idx_names = {i["name"] for i in inspector.get_indexes("documents")}
    if "ix_documents_sha256" not in idx_names:
        op.create_index("ix_documents_sha256", "documents", ["sha256"])


def downgrade() -> None:
    op.drop_index("ix_documents_sha256", table_name="documents")
    op.drop_column("documents", "sha256")
