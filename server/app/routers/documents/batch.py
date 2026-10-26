"""批量修改与批量软删除（契约第 4 节）。

PATCH /documents/batch 与 PATCH /documents/{doc_id} 同前缀冲突，必须注册在 crud 之前。
"""

import uuid

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ...database import get_db
from ...models import Collection, Document, utcnow
from ...services.operation_log import record
from ...services.tags import get_or_create_tag
from ._shared import router

MAX_BATCH = 500


class BatchPatch(BaseModel):
    collection_id: uuid.UUID | None = None
    language: str | None = None
    add_tags: list[str] | None = None
    remove_tags: list[str] | None = None


class BatchUpdateRequest(BaseModel):
    ids: list[str]
    patch: BatchPatch
    confirm: bool = False


class BatchDeleteRequest(BaseModel):
    ids: list[str]
    confirm: bool = False


class BatchUpdateResponse(BaseModel):
    updated: int
    operation_id: str


class BatchDeleteResponse(BaseModel):
    deleted: int
    operation_id: str


def _parse_ids(ids: list[str]) -> list[uuid.UUID]:
    if not ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    if len(ids) > MAX_BATCH:
        raise HTTPException(status_code=400, detail=f"单次最多 {MAX_BATCH} 个文档")
    try:
        return [uuid.UUID(i) for i in ids]
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="ids 含非法 UUID")


def _tag_names(doc: Document) -> list[str]:
    return sorted(tag.name for tag in doc.tags)


@router.patch("/batch", response_model=BatchUpdateResponse)
def batch_update(req: BatchUpdateRequest, db: Session = Depends(get_db)):
    if not req.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm: true")
    doc_ids = _parse_ids(req.ids)
    patch = req.patch
    if not patch.model_fields_set:
        raise HTTPException(status_code=400, detail="patch 至少包含一个字段")

    found = set(
        db.scalars(
            select(Document.id).where(
                Document.id.in_(doc_ids), Document.deleted_at.is_(None)
            )
        ).all()
    )
    missing = [str(i) for i in doc_ids if i not in found]
    if missing:
        raise HTTPException(
            status_code=400,
            detail={"message": "部分文档不存在或已删除", "missing_ids": missing},
        )

    if "collection_id" in patch.model_fields_set and patch.collection_id is not None:
        if db.get(Collection, patch.collection_id) is None:
            raise HTTPException(status_code=400, detail="collection_id 不存在")

    docs = db.scalars(
        select(Document)
        .options(selectinload(Document.tags))
        .where(Document.id.in_(doc_ids))
    ).all()
    by_id = {doc.id: doc for doc in docs}

    before_state: dict = {"docs": {}}
    after_state: dict = {"docs": {}}
    add_names = [n.strip() for n in (patch.add_tags or []) if n.strip()]
    remove_names = {n.strip() for n in (patch.remove_tags or []) if n.strip()}

    for doc_id in doc_ids:
        doc = by_id[doc_id]
        key = str(doc_id)
        before_state["docs"][key] = {
            "collection_id": str(doc.collection_id) if doc.collection_id else None,
            "language": doc.language,
            "tags": _tag_names(doc),
        }

        if "collection_id" in patch.model_fields_set:
            doc.collection_id = patch.collection_id
        if "language" in patch.model_fields_set:
            doc.language = patch.language
        if add_names:
            existing = {tag.name for tag in doc.tags}
            for name in add_names:
                if name not in existing:
                    doc.tags.append(get_or_create_tag(db, name))
                    existing.add(name)
        if remove_names:
            doc.tags = [tag for tag in doc.tags if tag.name not in remove_names]

        after_state["docs"][key] = {
            "collection_id": str(doc.collection_id) if doc.collection_id else None,
            "language": doc.language,
            "tags": _tag_names(doc),
        }

    parts = []
    if "collection_id" in patch.model_fields_set:
        parts.append("分类置空" if patch.collection_id is None else "改分类")
    if "language" in patch.model_fields_set:
        parts.append(f"改语言→{patch.language}")
    if add_names:
        parts.append(f"加标签+{len(add_names)}")
    if remove_names:
        parts.append(f"去标签-{len(remove_names)}")

    log = record(
        db,
        kind="batch_update",
        document_ids=doc_ids,
        before_state=before_state,
        after_state=after_state,
        summary=f"批量修改（{'、'.join(parts)}）：{len(doc_ids)} 本",
        undoable=True,
    )
    db.commit()
    return BatchUpdateResponse(updated=len(doc_ids), operation_id=str(log.id))


@router.post("/batch/delete", response_model=BatchDeleteResponse)
def batch_soft_delete(req: BatchDeleteRequest, db: Session = Depends(get_db)):
    if not req.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm: true")
    doc_ids = _parse_ids(req.ids)

    alive = db.scalars(
        select(Document).where(
            Document.id.in_(doc_ids), Document.deleted_at.is_(None)
        )
    ).all()
    now = utcnow()
    for doc in alive:
        doc.deleted_at = now

    log = record(
        db,
        kind="batch_soft_delete",
        document_ids=[doc.id for doc in alive],
        before_state={},
        after_state={"docs": {str(doc.id): {"deleted": True} for doc in alive}},
        summary=f"批量软删除：{len(alive)} 本",
        undoable=True,
    )
    db.commit()
    return BatchDeleteResponse(deleted=len(alive), operation_id=str(log.id))
