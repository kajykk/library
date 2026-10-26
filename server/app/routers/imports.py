"""导入与批量治理 API（B2-03/04/06/07/08/09）：目录扫描、导入、批量修改/软删除、封面、重提取、语言归一化、孤儿隔离。"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func as sa_func
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import Document, DocumentQuality, utcnow
from ..services.directory_scan import (
    ImportPathError,
    list_import_dirs,
    resolve_import_dir,
    resolve_import_path,
    scan_all_import_dirs,
    validate_rel_path,
)
from ..services.ingest import finalize_ingest, validate_format, IngestError
from ..services.file_storage import import_file
from ..services.operation_log import record
from ..services.orphan_quarantine import QuarantineError
from ..services.orphan_quarantine import quarantine as quarantine_files
from ..services.quality_audit import normalize_language

router = APIRouter(tags=["imports"])


# ---- 请求/响应模型 ----

class ScanResponse(BaseModel):
    total: int
    importable: list[dict]
    bad_files: list[dict]
    duplicates: list[dict]
    unsupported: list[dict]
    truncated: bool
    directories: dict


class ImportRequest(BaseModel):
    dir_id: str
    rel_paths: list[str]
    confirm: bool = False


class ImportResponse(BaseModel):
    imported: int
    skipped: int
    failed: int
    results: list[dict]


class BatchUpdateRequest(BaseModel):
    ids: list[str]
    collection_id: str | None = None
    language: str | None = None
    tags: list[str] | None = None
    confirm: bool = False


class BatchDeleteRequest(BaseModel):
    ids: list[str]
    confirm: bool = False


class BatchIdsRequest(BaseModel):
    quality: str | None = None
    types: str | None = None
    status: str | None = None


class CoverUploadRequest(BaseModel):
    document_id: str
    cover_base64: str
    confirm: bool = False


class ReextractRequest(BaseModel):
    ids: list[str]
    confirm: bool = False


class NormalizeLanguageRequest(BaseModel):
    ids: list[str] | None = None
    confirm: bool = False


class OrphanQuarantineRequest(BaseModel):
    rel_paths: list[str]
    confirm: bool = False


class OrphanQuarantineResponse(BaseModel):
    moved: int
    skipped: int
    failed: int
    operation_id: str | None = None
    quarantine_dir: str | None = None
    results: list[dict]


class BatchResponse(BaseModel):
    status: str
    affected: int
    operation_id: str | None = None


# ---- 目录扫描 ----

@router.get("/imports/dirs")
def get_import_dirs():
    """白名单目录列表：客户端只拿到 dir_id，服务器路径不作为后续请求的输入。"""
    return {"dirs": list_import_dirs()}


@router.get("/imports/scan", response_model=ScanResponse)
def scan_import_dirs():
    return ScanResponse(**scan_all_import_dirs())


# ---- 执行导入 ----

@router.post("/imports/execute", response_model=ImportResponse)
def execute_import(req: ImportRequest, db: Session = Depends(get_db)):
    if not req.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm: true")
    if len(req.rel_paths) > 500:
        raise HTTPException(status_code=400, detail="单次最多 500 个文件")
    if not req.rel_paths:
        raise HTTPException(status_code=400, detail="rel_paths 不能为空")
    # 白名单是唯一的路径来源：未配置 KB_IMPORT_DIRS 或 dir_id 不认识就直接拒绝，
    # 绝不退化成「客户端传什么路径就导什么」——那等于把服务器任意可读文件搬进书库。
    if resolve_import_dir(req.dir_id) is None:
        raise HTTPException(status_code=400, detail="dir_id 不在导入白名单中")
    # 形状不合法（绝对路径 / `..` / 空）整批拒绝：这是越权尝试，不是「部分文件缺失」
    for rel_path in req.rel_paths:
        try:
            validate_rel_path(rel_path)
        except ImportPathError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"rel_paths 只能是白名单目录内的相对路径（{exc}）",
            )

    settings = get_settings()
    max_size = settings.upload_max_size_mb * 1024 * 1024
    results = []
    imported = skipped = failed = 0
    doc_ids = []

    for rel_path in req.rel_paths:
        item = {"dir_id": req.dir_id, "rel_path": rel_path}
        try:
            src = resolve_import_path(req.dir_id, rel_path)
        except ImportPathError as exc:
            failed += 1
            results.append({**item, "status": "failed", "reason": str(exc)})
            continue

        if not src.is_file():
            results.append({**item, "status": "failed", "reason": "file_not_found"})
            failed += 1
            continue
        try:
            fmt = validate_format(src.name)
            doc_id = uuid.uuid4()
            rel_file, size, sha256 = import_file(src, doc_id, fmt, max_size)
            outcome = finalize_ingest(
                db,
                doc_id=doc_id,
                filename=src.name,
                fmt=fmt,
                rel_path=rel_file,
                file_size=size,
                sha256=sha256,
                collection_id=None,
                source="directory",
                source_path=str(src),
            )
            if outcome.status == "imported":
                imported += 1
                doc_ids.append(str(doc_id))
                results.append({**item, "status": "imported", "document_id": str(doc_id)})
            else:
                skipped += 1
                results.append({**item, "status": "skipped", "reason": outcome.reason})
        except IngestError as e:
            failed += 1
            results.append({**item, "status": "failed", "reason": e.message})
        except Exception as e:
            failed += 1
            results.append({**item, "status": "failed", "reason": str(e)[:200]})

    # 写操作日志
    if doc_ids:
        record(
            db,
            kind="directory_import",
            document_ids=[uuid.UUID(d) for d in doc_ids],
            before_state={},
            after_state={"docs": {d: {"created": True} for d in doc_ids}},
            summary=f"目录导入：{imported} 本",
            undoable=True,
        )
    db.commit()

    return ImportResponse(imported=imported, skipped=skipped, failed=failed, results=results)


# ---- 批量获取筛选结果 id ----

@router.post("/documents/ids")
def get_document_ids(req: BatchIdsRequest, db: Session = Depends(get_db)):
    from .documents.filters import apply_quality_filter

    stmt = select(Document.id).where(Document.deleted_at.is_(None))
    if req.types:
        values = [v.strip() for v in req.types.split(",") if v.strip()]
        stmt = stmt.where(Document.type.in_(values))
    if req.quality:
        stmt = apply_quality_filter(stmt, db, req.quality)
    ids = [str(r[0]) for r in db.execute(stmt.limit(500)).all()]
    return {"ids": ids, "total": len(ids)}


# ---- 批量修改 ----

@router.post("/documents/batch", response_model=BatchResponse)
def batch_update(req: BatchUpdateRequest, db: Session = Depends(get_db)):
    if not req.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm: true")
    if len(req.ids) > 500:
        raise HTTPException(status_code=400, detail="单次最多 500 个文档")

    before_state = {"docs": {}}
    after_state = {"docs": {}}
    affected = 0

    for id_str in req.ids:
        doc = db.get(Document, uuid.UUID(id_str))
        if doc is None or doc.deleted_at is not None:
            continue
        before_state["docs"][id_str] = {
            "collection_id": str(doc.collection_id) if doc.collection_id else None,
            "language": doc.language,
        }
        if req.collection_id:
            doc.collection_id = uuid.UUID(req.collection_id)
        if req.language:
            doc.language = req.language
        after_state["docs"][id_str] = {
            "collection_id": str(doc.collection_id) if doc.collection_id else None,
            "language": doc.language,
        }
        affected += 1

    summary_parts = []
    if req.collection_id:
        summary_parts.append("改分类")
    if req.language:
        summary_parts.append(f"改语言→{req.language}")

    log = record(
        db,
        kind="batch_update",
        document_ids=[uuid.UUID(i) for i in req.ids],
        before_state=before_state,
        after_state=after_state,
        summary=f"批量修改（{'、'.join(summary_parts)}）：{affected} 本",
        undoable=True,
    )
    db.commit()
    return BatchResponse(status="applied", affected=affected, operation_id=str(log.id))


# ---- 批量软删除 ----

@router.post("/documents/batch/delete", response_model=BatchResponse)
def batch_soft_delete(req: BatchDeleteRequest, db: Session = Depends(get_db)):
    if not req.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm: true")
    if len(req.ids) > 500:
        raise HTTPException(status_code=400, detail="单次最多 500 个文档")

    affected = 0
    for id_str in req.ids:
        doc = db.get(Document, uuid.UUID(id_str))
        if doc is None or doc.deleted_at is not None:
            continue
        doc.deleted_at = utcnow()
        affected += 1

    log = record(
        db,
        kind="batch_soft_delete",
        document_ids=[uuid.UUID(i) for i in req.ids],
        before_state={},
        after_state={"docs": {i: {"deleted": True} for i in req.ids}},
        summary=f"批量软删除：{affected} 本",
        undoable=True,
    )
    db.commit()
    return BatchResponse(status="applied", affected=affected, operation_id=str(log.id))


# ---- 语言归一化 ----

@router.post("/documents/normalize-language", response_model=BatchResponse)
def normalize_languages(req: NormalizeLanguageRequest, db: Session = Depends(get_db)):
    if not req.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm: true")

    if req.ids:
        docs = db.query(Document).filter(
            Document.id.in_([uuid.UUID(i) for i in req.ids]),
            Document.deleted_at.is_(None),
        ).all()
    else:
        docs = db.query(Document).filter(Document.deleted_at.is_(None)).all()

    before_state = {"docs": {}}
    after_state = {"docs": {}}
    affected = 0

    for doc in docs:
        normalized = normalize_language(doc.language)
        if normalized != doc.language:
            before_state["docs"][str(doc.id)] = {"language": doc.language}
            doc.language = normalized
            after_state["docs"][str(doc.id)] = {"language": normalized}
            affected += 1

    log = record(
        db,
        kind="language_normalize",
        document_ids=[uuid.UUID(i) for i in before_state["docs"].keys()],
        before_state=before_state,
        after_state=after_state,
        summary=f"语言归一化：{affected} 本",
        undoable=True,
    )
    db.commit()
    return BatchResponse(status="applied", affected=affected, operation_id=str(log.id))


# ---- 重提取 ----

@router.post("/documents/reextract", response_model=BatchResponse)
def reextract_documents(req: ReextractRequest, db: Session = Depends(get_db)):
    if not req.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm: true")
    if len(req.ids) > 500:
        raise HTTPException(status_code=400, detail="单次最多 500 个文档")

    # 简化实现：只记录操作日志，实际重提取由后台任务处理
    affected = 0
    for id_str in req.ids:
        doc = db.get(Document, uuid.UUID(id_str))
        if doc is not None and doc.deleted_at is None:
            affected += 1

    log = record(
        db,
        kind="reextract",
        document_ids=[uuid.UUID(i) for i in req.ids],
        before_state={"docs": {i: {"content_chars": 0} for i in req.ids}},
        after_state={},
        summary=f"重提取：{affected} 本",
        undoable=False,
    )
    db.commit()
    return BatchResponse(status="applied", affected=affected, operation_id=str(log.id))


# ---- 孤儿隔离 ----

@router.post("/documents/orphans/quarantine", response_model=OrphanQuarantineResponse)
def quarantine_orphans(
    req: OrphanQuarantineRequest, db: Session = Depends(get_db)
):
    """只隔离勾选的文件；候选清单来自只读的 `GET /api/quality/orphans`。"""
    if not req.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm: true")
    try:
        outcome = quarantine_files(db, req.rel_paths)
    except QuarantineError as exc:
        raise HTTPException(status_code=400, detail=exc.message)

    operation_id = None
    if outcome["moved"]:
        log = record(
            db,
            kind="orphan_quarantine",
            document_ids=[],
            before_state={"rel_paths": [r["rel_path"] for r in outcome["results"]]},
            after_state={
                "quarantine_dir": outcome["quarantine_dir"],
                "moves": outcome["moves"],
                "moved": outcome["moved"],
            },
            summary=f"孤儿隔离：{outcome['moved']} 个文件",
            undoable=True,
        )
        operation_id = str(log.id)
    db.commit()

    return OrphanQuarantineResponse(
        moved=outcome["moved"],
        skipped=outcome["skipped"],
        failed=outcome["failed"],
        operation_id=operation_id,
        quarantine_dir=outcome["quarantine_dir"],
        results=outcome["results"],
    )


# ---- 补封面 ----

@router.put("/documents/{document_id}/cover", response_model=BatchResponse)
def upload_cover(
    document_id: uuid.UUID,
    req: CoverUploadRequest,
    db: Session = Depends(get_db),
):
    import base64

    if not req.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm: true")

    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 魔数校验
    try:
        raw = base64.b64decode(req.cover_base64)
    except Exception:
        raise HTTPException(status_code=400, detail="无效的 base64 数据")

    if raw[:3] == b"\xff\xd8\xff":
        ext = "jpg"
    elif raw[:8] == b"\x89PNG\r\n\x1a\n":
        ext = "png"
    else:
        raise HTTPException(status_code=400, detail="不支持的图片格式（仅 jpg/png）")

    from ..services.file_storage import write_relative

    old_cover = doc.cover_path
    rel_path = f"covers/{document_id}.{ext}"
    write_relative(rel_path, raw)
    doc.cover_path = rel_path

    log = record(
        db,
        kind="cover_upload",
        document_ids=[document_id],
        before_state={"docs": {str(document_id): {"cover_path": old_cover}}},
        after_state={"docs": {str(document_id): {"cover_path": rel_path}}},
        summary=f"补封面：{doc.title}",
        undoable=True,
    )
    db.commit()
    return BatchResponse(status="applied", affected=1, operation_id=str(log.id))
