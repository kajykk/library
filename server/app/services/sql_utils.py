"""SQL 查询辅助：LIKE/ILIKE 模式转义、跨方言长度表达式。"""

from sqlalchemy import func
from sqlalchemy.orm import Session


def escape_like(value: str) -> str:
    """转义 LIKE 通配符，使用户输入按字面匹配（需配合 escape="\\\\"）。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def trimmed_content_length_expr(db: Session):
    """跨方言的「去空白后长度」表达式。

    SQLite: length(trim(coalesce(content,'')))
    PostgreSQL: length(btrim(coalesce(content,'')))
    """
    from app.models import Document

    coalesced = func.coalesce(Document.content, "")
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        return func.length(func.btrim(coalesced))
    return func.length(func.trim(coalesced))
