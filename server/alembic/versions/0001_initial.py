"""初始建表：documents / collections / annotations / tags / document_tags / links / reading_records

采用 Base.metadata 驱动建表，确保与 ORM 模型完全一致；
后续迁移请使用 autogenerate 生成增量 revision。

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-17
"""
from typing import Sequence, Union

from alembic import op

from app.database import Base
from app import models  # noqa: F401

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
