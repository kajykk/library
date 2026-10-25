"""SQL 查询辅助：LIKE/ILIKE 模式转义。"""


def escape_like(value: str) -> str:
    """转义 LIKE 通配符，使用户输入按字面匹配（需配合 escape="\\\\"）。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
