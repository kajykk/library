"""documents 增加 last_read_at

Revision ID: 0002_last_read_at
Revises: 0001_initial
Create Date: 2026-08-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_last_read_at"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite 不支持 ADD COLUMN IF NOT EXISTS；用 inspector 检查后执行，双方言通用。
    # 0001 采用当前 metadata 建表（checkfirst），全新库可能已带该列，必须幂等。
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("documents")}
    if "last_read_at" in cols:
        return
    op.add_column(
        "documents",
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "last_read_at")
