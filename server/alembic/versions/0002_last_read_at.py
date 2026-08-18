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
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS last_read_at TIMESTAMP WITH TIME ZONE")


def downgrade() -> None:
    op.drop_column("documents", "last_read_at")
