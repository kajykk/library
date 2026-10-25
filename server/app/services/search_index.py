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
    CREATE TRIGGER IF NOT EXISTS documents_fts_au AFTER UPDATE OF title, author, description, content ON documents BEGIN
        DELETE FROM {FTS_TABLE} WHERE doc_id = old.id;
        INSERT INTO {FTS_TABLE}(doc_id, title, author, description, content)
        VALUES (new.id, coalesce(new.title, ''), coalesce(new.author, ''),
                coalesce(new.description, ''), coalesce(new.content, ''));
    END;
    """,
]
# 旧版本部署的 documents_fts_au 是"任意列 UPDATE 全量重建"，阅读进度心跳会
# 触发写放大；此处先删旧触发器再按新定义重建（幂等）。
_TRIGGER_DROP_STATEMENTS = ["DROP TRIGGER IF EXISTS documents_fts_au;"]

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


def _setup(engine: Engine) -> bool:
    """创建 FTS5 虚拟表与触发器（幂等）。返回 FTS 是否可用。"""
    global _available
    if engine.dialect.name != "sqlite":
        # PostgreSQL 走 search_vector/zhparser 分支，本模块不参与
        _available = False
        return False
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("SELECT fts5(?)", ("initialize",))
            conn.exec_driver_sql(_CREATE_SQL)
            for drop_sql in _TRIGGER_DROP_STATEMENTS:
                conn.exec_driver_sql(drop_sql)
            for trigger_sql in _TRIGGER_STATEMENTS:
                conn.exec_driver_sql(trigger_sql)
    except DatabaseError as exc:
        _available = False
        logger.warning("FTS5 不可用（%s），检索回退 ILIKE", exc)
        return False
    _available = True
    logger.info("FTS5 trigram 全文索引已就绪")
    return True


def ensure_fts_index(engine: Engine, reindex: bool = True) -> bool:
    """确保 FTS5 表/触发器存在，并在落后时回填。返回是否可用。

    reindex=False 用于启动路径：只建结构不回填，避免大库阻塞启动
    （落后量由 fts_stale_count 探测，回填转后台任务）。
    """
    if _available is None:
        _setup(engine)
    if _available and reindex and fts_stale_count(engine) != 0:
        reindex_fts(engine)
    return bool(_available)


def fts_stale_count(engine: Engine) -> int:
    """documents 与 FTS 表的行数差（>0 表示索引落后）。不可用或探测失败返回 0。"""
    if not fts_available():
        return 0
    try:
        with engine.begin() as conn:
            diff = conn.exec_driver_sql(
                "SELECT (SELECT count(*) FROM documents) "
                "- (SELECT count(*) FROM documents_fts)"
            ).scalar()
        return int(diff or 0)
    except DatabaseError as exc:
        logger.warning("FTS 落后量探测失败：%s", exc)
        return 0


def reindex_fts(engine: Engine) -> int:
    """全量回填 FTS 索引（同步，大库耗时；供后台任务/脚本调用）。返回索引总行数。"""
    if not fts_available():
        return 0
    try:
        with engine.begin() as conn:
            for reindex_sql in _REINDEX_STATEMENTS:
                conn.exec_driver_sql(reindex_sql)
            total = conn.exec_driver_sql(f"SELECT count(*) FROM {FTS_TABLE}").scalar()
        logger.info("FTS5 回填完成，共 %s 条", total)
        return int(total or 0)
    except DatabaseError as exc:
        logger.warning("FTS5 回填失败：%s", exc)
        return 0


def fts_available() -> bool:
    return _available is True
