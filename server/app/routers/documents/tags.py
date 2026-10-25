"""文档标签路由。"""

import uuid

from fastapi import Depends
from sqlalchemy.orm import Session

from ...database import get_db
from ...schemas import DocumentOut, TagsSet
from ...services.tags import get_or_create_tag
from ._shared import get_doc_or_404, router


@router.put("/{doc_id}/tags", response_model=DocumentOut)
def set_document_tags(doc_id: uuid.UUID, payload: TagsSet, db: Session = Depends(get_db)):
    doc = get_doc_or_404(db, doc_id)
    doc.tags = [get_or_create_tag(db, name) for name in dict.fromkeys(payload.tags)]
    db.commit()
    return doc
