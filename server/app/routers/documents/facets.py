"""书架分面聚合：GET /documents/facets。

必须是静态路径，且在包内先于 crud 的 /{doc_id} 注册，否则 "facets" 会被当成文档 id。

每一维用「除自身以外」的全部当前筛选统计（types/q/status/quality 永远参与），
否则选中 format=pdf 后其他格式计数归零，用户没法一键切走。
"""

import uuid

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...database import get_db
from ...models import Collection, Document, DocumentTag, Tag
from ...services.sql_utils import language_norm_expr
from ._shared import router
from .filters import apply_document_filters
from .stats import BOOK_TYPES

# 每维候选上限；多取一条用来判断是否截断
FACET_LIMIT = 100


class FacetValue(BaseModel):
    value: str
    count: int


class FacetCategory(BaseModel):
    collection_id: str
    name: str
    count: int


class FacetTruncated(BaseModel):
    tags: bool
    authors: bool


class FacetsOut(BaseModel):
    total: int
    formats: list[FacetValue]
    languages: list[FacetValue]
    tags: list[FacetValue]
    authors: list[FacetValue]
    categories: list[FacetCategory]
    uncategorized: int
    truncated: FacetTruncated


def _apply(db: Session, stmt, filters: dict, exclude: str | None = None):
    """在 stmt 上套用「除 exclude 之外」的筛选，并强制只统计存活 book/pdf。"""
    params = dict(filters)
    if exclude is not None:
        params[exclude] = None
    return apply_document_filters(stmt, db=db, **params).where(Document.type.in_(BOOK_TYPES))


def _agg(*cols):
    """聚合查询一律锚定 documents 表：让 tags/collections 这些附加表只通过显式 join 进来。"""
    return select(*cols).select_from(Document)


def _count_expr(distinct: bool = False):
    col = DocumentTag.document_id if distinct else Document.id
    return func.count(col.distinct() if distinct else col)


def _ranked(stmt, value_col, count_col):
    """每维统一口径：count 降序、值升序，保证同样输入得到同样顺序。"""
    return stmt.group_by(value_col).order_by(count_col.desc(), value_col.asc())


@router.get("/facets", response_model=FacetsOut)
def get_facets(
    types: str | None = None,
    collection_id: uuid.UUID | None = None,
    tag: str | None = None,
    q: str | None = None,
    author: str | None = None,
    status: str | None = None,
    quality: str | None = None,
    format: str | None = None,
    language: str | None = None,
    db: Session = Depends(get_db),
):
    """格式/语言/标签/作者/分类五个维度的分面计数，参数与列表、计数端点一致。"""
    filters = {
        "types": types,
        "collection_id": collection_id,
        "tag": tag,
        "q": q,
        "author": author,
        "status": status,
        "quality": quality,
        "format": format,
        "language": language,
    }

    docs = _count_expr()
    total = db.scalar(_apply(db, _agg(docs), filters)) or 0

    # 格式：库中 format 允许为 NULL，归到空串桶，保证各维计数之和与 total 可核对
    fmt_col = func.coalesce(Document.format, "")
    formats = [
        FacetValue(value=str(value), count=int(count))
        for value, count in db.execute(
            _ranked(_apply(db, _agg(fmt_col, docs), filters, exclude="format"), fmt_col, docs).limit(
                FACET_LIMIT
            )
        ).all()
    ]

    # 语言：按归一化值分组，en-US / ENG / en 合成一个桶
    lang_col = language_norm_expr(Document.language)
    languages = [
        FacetValue(value=str(value), count=int(count))
        for value, count in db.execute(
            _ranked(
                _apply(db, _agg(lang_col, docs), filters, exclude="language"), lang_col, docs
            ).limit(FACET_LIMIT)
        ).all()
    ]

    # 标签：多标签文档 join 会放大行数，必须 COUNT(DISTINCT document_id)
    tag_cnt = _count_expr(distinct=True)
    tag_rows = db.execute(
        _ranked(
            _apply(db, _agg(Tag.name, tag_cnt), filters, exclude="tag")
            .join(DocumentTag, DocumentTag.document_id == Document.id)
            .join(Tag, Tag.id == DocumentTag.tag_id)
            .where(func.trim(Tag.name) != ""),
            Tag.name,
            tag_cnt,
        ).limit(FACET_LIMIT + 1)
    ).all()
    tags_truncated = len(tag_rows) > FACET_LIMIT
    tags = [FacetValue(value=str(name), count=int(count)) for name, count in tag_rows[:FACET_LIMIT]]

    # 作者：排除空与纯空白
    author_col = func.trim(func.coalesce(Document.author, ""))
    author_rows = db.execute(
        _ranked(
            _apply(db, _agg(author_col, docs), filters, exclude="author").where(author_col != ""),
            author_col,
            docs,
        ).limit(FACET_LIMIT + 1)
    ).all()
    authors_truncated = len(author_rows) > FACET_LIMIT
    authors = [
        FacetValue(value=str(name), count=int(count)) for name, count in author_rows[:FACET_LIMIT]
    ]

    # 分类：未分类不进列表，只进 uncategorized
    category_rows = db.execute(
        _apply(
            db,
            _agg(Document.collection_id, Collection.name, docs),
            filters,
            exclude="collection_id",
        )
        .join(Collection, Collection.id == Document.collection_id, isouter=True)
        .where(Document.collection_id.isnot(None))
        .group_by(Document.collection_id, Collection.name)
        .order_by(docs.desc(), Collection.name.asc())
        .limit(FACET_LIMIT)
    ).all()
    categories = [
        FacetCategory(collection_id=str(cid), name=str(name or ""), count=int(count))
        for cid, name, count in category_rows
    ]

    uncategorized = (
        db.scalar(
            _apply(
                db,
                _agg(docs),
                filters,
                exclude="collection_id",
            ).where(Document.collection_id.is_(None))
        )
        or 0
    )

    return FacetsOut(
        total=total,
        formats=formats,
        languages=languages,
        tags=tags,
        authors=authors,
        categories=categories,
        uncategorized=uncategorized,
        truncated=FacetTruncated(tags=tags_truncated, authors=authors_truncated),
    )
