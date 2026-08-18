"""SQLite FTS5 trigram 全文索引（documents_fts + 同步触发器）

Revision ID: 0004_documents_fts
Revises: 0003_documents_search_vector
Create Date: 2026-08-18
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004_documents_fts"
down_revision: Union[str, None] = "0003_documents_search_vector"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    op.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
            doc_id UNINDEXED,
            title,
            author,
            description,
            content,
            tokenize = 'trigram'
        )
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS documents_fts_ai AFTER INSERT ON documents BEGIN
            INSERT INTO documents_fts(doc_id, title, author, description, content)
            VALUES (new.id, coalesce(new.title, ''), coalesce(new.author, ''),
                    coalesce(new.description, ''), coalesce(new.content, ''));
        END;
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS documents_fts_ad AFTER DELETE ON documents BEGIN
            DELETE FROM documents_fts WHERE doc_id = old.id;
        END;
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS documents_fts_au AFTER UPDATE ON documents BEGIN
            DELETE FROM documents_fts WHERE doc_id = old.id;
            INSERT INTO documents_fts(doc_id, title, author, description, content)
            VALUES (new.id, coalesce(new.title, ''), coalesce(new.author, ''),
                    coalesce(new.description, ''), coalesce(new.content, ''));
        END;
        """
    )
    op.execute("DELETE FROM documents_fts")
    op.execute(
        """
        INSERT INTO documents_fts(doc_id, title, author, description, content)
        SELECT id, coalesce(title, ''), coalesce(author, ''),
               coalesce(description, ''), coalesce(content, '')
        FROM documents;
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    op.execute("DROP TRIGGER IF EXISTS documents_fts_ai")
    op.execute("DROP TRIGGER IF EXISTS documents_fts_ad")
    op.execute("DROP TRIGGER IF EXISTS documents_fts_au")
    op.execute("DROP TABLE IF EXISTS documents_fts")
