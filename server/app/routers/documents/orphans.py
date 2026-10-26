"""`GET /documents/orphans` 契约别名（与 `/quality/orphans` 同源）。

单段静态路由必须注册在 crud 的 /{doc_id} 之前。
"""

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...database import get_db
from ...services.quality_audit import scan_orphans
from ._shared import router


class OrphanFileOut(BaseModel):
    rel_path: str
    size: int


class MissingFileOut(BaseModel):
    document_id: str
    title: str
    rel_path: str


class OrphansOut(BaseModel):
    orphan_files: list[OrphanFileOut]
    orphan_covers: list[OrphanFileOut]
    missing_files: list[MissingFileOut]
    total_bytes: int
    truncated: bool


@router.get("/orphans", response_model=OrphansOut)
def list_document_orphans(db: Session = Depends(get_db)):
    return OrphansOut(**scan_orphans(db))
