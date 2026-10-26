"""operation_logs 批量操作审计表（B2）

Revision ID: 0008_operation_logs
Revises: 0007_document_quality
Create Date: 2026-10-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_operation_logs"
down_revision: Union[str, None] = "0007_document_quality"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "operation_logs" in inspector.get_table_names():
        return

    op.create_table(
        "operation_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="applied"),
        sa.Column("document_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("before_state", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("after_state", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("summary", sa.String(500), nullable=False, server_default=""),
        sa.Column("undoable", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_operation_logs_kind", "operation_logs", ["kind"])
    op.create_index("ix_operation_logs_created_at", "operation_logs", ["created_at"])
    op.create_index("ix_operation_logs_status", "operation_logs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_operation_logs_status", table_name="operation_logs")
    op.drop_index("ix_operation_logs_created_at", table_name="operation_logs")
    op.drop_index("ix_operation_logs_kind", table_name="operation_logs")
    op.drop_table("operation_logs")
