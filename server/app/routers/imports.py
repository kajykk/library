"""导入与批量治理 API（B2-03/06/08/09）：目录扫描、导入、封面、语言归一化、孤儿隔离。

批量修改/软删除、筛选结果 id 与重提取已迁到 documents 包（`documents/batch.py`、
`documents/ids.py`、`documents/reextract.py`），否则会被 documents 的 `/{doc_id}` 抢先匹配。
"""

import math
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import Document
from ..schemas import DocumentOut
from ..services import file_storage
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
from ..services.operation_log import MAX_DOCS_PER_OP, record
from ..services.orphan_quarantine import QuarantineError
from ..services.orphan_quarantine import quarantine as quarantine_files
from .documents import get_doc_or_404

router = APIRouter(tags=["imports"])

MAX_COVER_BYTES = 8 * 1024 * 1024


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


class NormalizeLanguageRequest(BaseModel):
    dry_run: bool = False
    ids: list[str] | None = None
    confirm: bool = False


class NormalizeLanguageResponse(BaseModel):
    dry_run: bool
    total: int
    changes: list[dict]
    unchanged: int
    batches: int


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


# ---- 语言归一化 ----

def _normalize_language_value(raw: str | None) -> str:
    """契约第 5 节：en*/zh*/ja* → 对应两字母码，空 → und，其他值保持不变。

    不能用 quality_audit.normalize_language —— 它把未知码（如 pt）吞成 und，
    会让「按语言归一化」变成「抹掉未收录语言」。
    """
    value = (raw or "").strip()
    if not value:
        return "und"
    lower = value.lower()
    for prefix in ("en", "zh", "ja"):
        if lower.startswith(prefix):
            return prefix
    return value


def _parse_ids(ids: list[str]) -> list[uuid.UUID]:
    try:
        return [uuid.UUID(i) for i in ids]
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="ids 含非法 UUID")


@router.post("/documents/normalize-language", response_model=NormalizeLanguageResponse)
def normalize_languages(req: NormalizeLanguageRequest, db: Session = Depends(get_db)):
    if not req.dry_run and not req.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm: true 或 dry_run: true")

    stmt = select(Document.id, Document.title, Document.language).where(
        Document.deleted_at.is_(None)
    )
    if req.ids is not None:
        stmt = stmt.where(Document.id.in_(_parse_ids(req.ids)))
    else:
        # 省略 ids = 全部存活 book/pdf；显式传 ids 时不限制类型
        stmt = stmt.where(Document.type.in_(("book", "pdf")))

    rows = db.execute(stmt.order_by(Document.created_at, Document.id)).all()
    changes: list[dict] = []
    for doc_id, title, language in rows:
        normalized = _normalize_language_value(language)
        if normalized != language:
            changes.append(
                {
                    "document_id": str(doc_id),
                    "title": title,
                    "from": language,
                    "to": normalized,
                }
            )

    batches = math.ceil(len(changes) / MAX_DOCS_PER_OP) if changes else 0
    if req.dry_run:
        return NormalizeLanguageResponse(
            dry_run=True,
            total=len(rows),
            changes=changes,
            unchanged=len(rows) - len(changes),
            batches=batches,
        )

    for start in range(0, len(changes), MAX_DOCS_PER_OP):
        chunk = changes[start : start + MAX_DOCS_PER_OP]
        for change in chunk:
            doc = db.get(Document, uuid.UUID(change["document_id"]))
            doc.language = change["to"]
        record(
            db,
            kind="language_normalize",
            document_ids=[uuid.UUID(c["document_id"]) for c in chunk],
            before_state={"docs": {c["document_id"]: {"language": c["from"]} for c in chunk}},
            after_state={"docs": {c["document_id"]: {"language": c["to"]} for c in chunk}},
            summary=f"语言归一化：{len(chunk)} 本",
            undoable=True,
        )
    db.commit()

    return NormalizeLanguageResponse(
        dry_run=False,
        total=len(rows),
        changes=changes,
        unchanged=len(rows) - len(changes),
        batches=batches,
    )


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

def _sniff_cover_ext(data: bytes) -> str | None:
    """按魔数判断封面类型，不信任扩展名与 Content-Type。"""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


@router.put("/documents/{document_id}/cover", response_model=DocumentOut)
async def upload_cover(
    document_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    doc = get_doc_or_404(db, document_id)

    buf = bytearray()
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > MAX_COVER_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"封面超过 {MAX_COVER_BYTES // (1024 * 1024)} MB 上限",
            )

    ext = _sniff_cover_ext(bytes(buf))
    if ext is None:
        raise HTTPException(
            status_code=400, detail="不支持的图片格式（仅 JPEG/PNG/WebP/GIF）"
        )

    old_cover = doc.cover_path
    rel_path = file_storage.save_cover(document_id, bytes(buf), ext)
    doc.cover_path = rel_path

    record(
        db,
        kind="cover_upload",
        document_ids=[document_id],
        before_state={"docs": {str(document_id): {"cover_path": old_cover}}},
        after_state={"docs": {str(document_id): {"cover_path": rel_path}}},
        summary=f"补封面：{doc.title}",
        undoable=True,
    )
    db.commit()
    return doc
