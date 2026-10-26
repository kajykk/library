"""SQL 查询辅助：LIKE/ILIKE 模式转义、跨方言长度表达式、语言码归一化。"""

from sqlalchemy import case, func
from sqlalchemy.orm import Session

# 语言码别名 → 规范码。SQL 分组侧与查询参数侧共用这一份，避免两处口径漂移。
LANGUAGE_ALIASES = {
    "en-us": "en", "en-gb": "en", "eng": "en",
    "zh-tw": "zh", "zh-hk": "zh", "zh-hans": "zh", "zh-hant": "zh", "cht": "zh", "chs": "zh",
    "ja-jp": "ja", "jpn": "ja",
}

# 空/NULL 语言码归到这个值，与 quality_audit 的写入侧保持一致
UNDETERMINED_LANGUAGE = "und"


def escape_like(value: str) -> str:
    """转义 LIKE 通配符，使用户输入按字面匹配（需配合 escape="\\\\"）。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def normalize_language_code(value: str | None) -> str:
    """归一化用户传来的语言码：`en-US`/`ENG` → `en`，空 → `und`。

    未知码保留小写原值（`pt` 仍是 `pt`）——否则按未收录语言筛选会误命中所有未标注的书。
    """
    key = (value or "").strip().lower()
    if not key:
        return UNDETERMINED_LANGUAGE
    return LANGUAGE_ALIASES.get(key, key)


def language_norm_expr(col):
    """SQL 侧的语言码归一化表达式，口径与 normalize_language_code 一致。

    分面聚合必须按归一化值分组，否则 `en-US`/`ENG`/`en` 会裂成三个桶。
    """
    lowered = func.lower(func.trim(func.coalesce(col, "")))
    whens = [(lowered == "", UNDETERMINED_LANGUAGE)] + [
        (lowered == alias, target) for alias, target in LANGUAGE_ALIASES.items()
    ]
    return case(*whens, else_=lowered)


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
