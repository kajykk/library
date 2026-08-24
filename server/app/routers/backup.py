"""zip 全量备份 / 恢复

备份：全部元数据（meta.json） + files/ + covers/ 原始文件
恢复：upsert 合并（保留 id），文件覆盖写入
"""

import json
import os
import tempfile
import uuid as uuid_mod
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..config import get_settings
from ..database import get_db
from ..models import (
    Annotation,
    Collection,
    Document,
    DocumentTag,
    Link,
    ReadingRecord,
    Tag,
)
from ..services import file_storage

router = APIRouter(prefix="/backup", tags=["backup"])

META_NAME = "meta.json"


def _row_to_dict(row) -> dict:
    out = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        if isinstance(value, uuid_mod.UUID):
            value = str(value)
        elif isinstance(value, datetime):
            value = value.isoformat()
        out[column.name] = value
    return out


@router.get("")
def create_backup(db: Session = Depends(get_db)):
    """生成 zip 备份（元数据 + 全部文件与封面）"""
    data = {
        "version": 1,
        "export_time": datetime.now().isoformat(),
        "collections": [_row_to_dict(r) for r in db.scalars(select(Collection))],
        "documents": [_row_to_dict(r) for r in db.scalars(select(Document))],
        "tags": [_row_to_dict(r) for r in db.scalars(select(Tag))],
        "document_tags": [_row_to_dict(r) for r in db.scalars(select(DocumentTag))],
        "annotations": [_row_to_dict(r) for r in db.scalars(select(Annotation))],
        "links": [_row_to_dict(r) for r in db.scalars(select(Link))],
        "reading_records": [_row_to_dict(r) for r in db.scalars(select(ReadingRecord))],
    }

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(META_NAME, json.dumps(data, ensure_ascii=False))
            for doc in data["documents"]:
                for rel in (doc.get("file_path"), doc.get("cover_path")):
                    if not rel:
                        continue
                    try:
                        zf.write(file_storage.abs_path(rel), rel)
                    except (OSError, ValueError, KeyError):
                        pass

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        cleanup = BackgroundTask(lambda: tmp_path.unlink(missing_ok=True))
        return FileResponse(
            tmp_path,
            media_type="application/zip",
            filename=f"knowledge_base_backup_{timestamp}.zip",
            background=cleanup,
        )
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _restore_value(column, value):
    if value is None:
        return None
    python_type = column.type.python_type
    if python_type is uuid_mod.UUID:
        return uuid_mod.UUID(value)
    if python_type is datetime:
        return datetime.fromisoformat(value)
    return value


@router.post("/restore", status_code=200)
async def restore_backup(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """从 zip 备份恢复（upsert 合并，保留原 id；文件覆盖写入）

    上传体分块写入临时文件并施加与书籍上传一致的大小上限，避免超大包一次性读入内存。
    """
    max_bytes = get_settings().upload_max_size_mb * 1024 * 1024
    fd, tmp_name = tempfile.mkstemp(suffix=".zip")
    tmp_path = Path(tmp_name)
    total = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Backup exceeds {get_settings().upload_max_size_mb} MB upload limit",
                    )
                out.write(chunk)

        try:
            zf = zipfile.ZipFile(tmp_path)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Invalid zip file")

        # Windows 下必须先关闭句柄才能在 finally 中删除临时文件
        try:
            if META_NAME not in zf.namelist():
                raise HTTPException(status_code=400, detail="meta.json missing in backup")

            meta = json.loads(zf.read(META_NAME))

            table_map = {
                "collections": Collection,
                "documents": Document,
                "tags": Tag,
                "document_tags": DocumentTag,
                "annotations": Annotation,
                "links": Link,
                "reading_records": ReadingRecord,
            }

            restored = {}
            for key, model in table_map.items():
                rows = meta.get(key) or []
                for row in rows:
                    obj = model(**{
                        c.name: _restore_value(c, row[c.name])
                        for c in model.__table__.columns
                        if c.name in row
                    })
                    db.merge(obj)
                restored[key] = len(rows)
            db.commit()

            files_restored = 0
            for entry in set(zf.namelist()):
                if entry.startswith(("files/", "covers/")) and not entry.endswith("/"):
                    file_storage.write_relative(entry, zf.read(entry))
                    files_restored += 1

            return {"restored": restored, "files_restored": files_restored}
        finally:
            zf.close()
    finally:
        tmp_path.unlink(missing_ok=True)

