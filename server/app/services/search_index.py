"""SQLite FTS5 全文索引（trigram 分词，适合中文子串检索）。

表结构与触发器随应用启动自动创建（auto_create_tables），
生产环境同时提供 Alembic 迁移（0004）。PostgreSQL 走 search_vector/zhparser 分支，
不启用本模块。
"""

from __future__ import annotations

import logging

from sqlalchemy.engine import Engine
from sqlalchemy.exc import DatabaseError

logger = logging.getLogger("kb.search_index")

# doc_id UNINDEXED + title/author/description/content 四个可检索列
FTS_TABLE = "documents_fts"

_CREATE_SQL = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
    doc_id UNINDEXED,
    title,
    author,
    description,
    content,
    tokenize = 'trigram'
);
"""

_TRIGGER_STATEMENTS = [
    f"""
    CREATE TRIGGER IF NOT EXISTS documents_fts_ai AFTER INSERT ON documents BEGIN
        INSERT INTO {FTS_TABLE}(doc_id, title, author, description, content)
        VALUES (new.id, coalesce(new.title, ''), coalesce(new.author, ''),
                coalesce(new.description, ''), coalesce(new.content, ''));
    END;
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS documents_fts_ad AFTER DELETE ON documents BEGIN
        DELETE FROM {FTS_TABLE} WHERE doc_id = old.id;
    END;
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS documents_fts_au AFTER UPDATE ON documents BEGIN
        DELETE FROM {FTS_TABLE} WHERE doc_id = old.id;
        INSERT INTO {FTS_TABLE}(doc_id, title, author, description, content)
        VALUES (new.id, coalesce(new.title, ''), coalesce(new.author, ''),
                coalesce(new.description, ''), coalesce(new.content, ''));
    END;
    """,
]

_REINDEX_STATEMENTS = [
    f"DELETE FROM {FTS_TABLE};",
    f"""
    INSERT INTO {FTS_TABLE}(doc_id, title, author, description, content)
    SELECT id, coalesce(title, ''), coalesce(author, ''),
           coalesce(description, ''), coalesce(content, '')
    FROM documents;
    """,
]

_available: bool | None = None


def ensure_fts_index(engine: Engine) -> bool:
    """创建 FTS5 表/触发器并重建索引。返回是否可用。"""
    global _available
    if _available is not None:
        return _available

    if engine.dialect.name != "sqlite":
        _available = False
        return False

    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("SELECT fts5(?)", ("initialize",))
            conn.exec_driver_sql(_CREATE_SQL)
            for trigger_sql in _TRIGGER_STATEMENTS:
                conn.exec_driver_sql(trigger_sql)
            stale = conn.exec_driver_sql(
                "SELECT (SELECT count(*) FROM documents) - (SELECT count(*) FROM documents_fts)"
            ).scalar()
            if stale != 0:
                for reindex_sql in _REINDEX_STATEMENTS:
                    conn.exec_driver_sql(reindex_sql)
        _available = True
        logger.info("FTS5 trigram 全文索引已就绪")
    except DatabaseError as exc:
        _available = False
        logger.warning("FTS5 不可用（%s），检索回退 ILIKE", exc)
    return _available


def fts_available() -> bool:
    return _available is True
