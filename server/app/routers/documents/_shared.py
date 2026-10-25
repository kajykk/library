"""documents 包共享原语：路由、配置、文档查询。

集中在这里是为了让 crud / versions / insight 等子模块保持单向依赖，
避免包内循环 import。
"""

import re
import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ...config import get_settings
from ...models import Document

router = APIRouter(prefix="/documents", tags=["documents"])

settings = get_settings()

# [[标题]] 双链语法（Obsidian 风格）
MENTION_PATTERN = re.compile(r"\[\[([^\[\]\n]+?)\]\]")


def get_doc_or_404(db: Session, doc_id: uuid.UUID, include_deleted: bool = False) -> Document:
    doc = db.get(Document, doc_id, options=(selectinload(Document.tags),))
    if doc is None or (doc.deleted_at is not None and not include_deleted):
        raise HTTPException(status_code=404, detail="Document not found")
    return doc
