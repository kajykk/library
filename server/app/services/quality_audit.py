"""质量审计服务（B1-02）。

只读审计：计算文档质量问题并写入 document_quality 快照表，不修改 documents。
"""

import threading
import uuid
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, load_only

from ..config import get_settings
from ..database import SessionLocal
from ..models import Document, DocumentQuality, utcnow
from .file_storage import abs_path
from .job_state import JobState
from .sql_utils import LANGUAGE_ALIASES as _LANG_MAP
from .sql_utils import trimmed_content_length_expr

# 问题码（稳定字符串，前后端共用）
ISSUE_MISSING_FILE = "missing_file"
ISSUE_ORPHAN_COVER = "orphan_cover"
ISSUE_EMPTY_CONTENT = "empty_content"
ISSUE_SHORT_CONTENT = "short_content"
ISSUE_NO_COVER = "no_cover"
ISSUE_UNCLASSIFIED = "unclassified"
ISSUE_NEEDS_OCR = "needs_ocr"
ISSUE_DUPLICATE_SHA = "duplicate_sha"
ISSUE_LANGUAGE_VARIANT = "language_variant"

VALID_ISSUES = frozenset({
    ISSUE_MISSING_FILE, ISSUE_ORPHAN_COVER, ISSUE_EMPTY_CONTENT,
    ISSUE_SHORT_CONTENT, ISSUE_NO_COVER, ISSUE_UNCLASSIFIED,
    ISSUE_NEEDS_OCR, ISSUE_DUPLICATE_SHA, ISSUE_LANGUAGE_VARIANT,
})

_job = JobState("quality_audit")


def normalize_language(raw: str | None) -> str:
    """归一化语言码：en-US/eng/EN-US → en，zh-TW/zh-Hans → zh，空 → und。"""
    if not raw or not raw.strip():
        return "und"
    key = raw.strip().lower()
    return _LANG_MAP.get(key, key if key in ("zh", "en", "ja", "und", "fr", "de", "ru", "es") else "und")


def audit_one(db: Session, doc: Document, threshold: int) -> DocumentQuality:
    """审计单个文档并 upsert 到 document_quality。"""
    issues: list[str] = []

    # content_chars：SQL 里算，不把全文读进 Python
    length_expr = trimmed_content_length_expr(db)
    content_chars = db.execute(
        select(length_expr).where(Document.id == doc.id)
    ).scalar() or 0

    # file_exists
    scan_error: str | None = None
    file_exists = False
    if doc.file_path:
        try:
            file_exists = abs_path(doc.file_path).is_file()
        except Exception as exc:
            scan_error = str(exc)[:200]
            file_exists = False
        if not file_exists:
            issues.append(ISSUE_MISSING_FILE)

    # cover_exists
    cover_exists = False
    if doc.cover_path:
        try:
            cover_exists = abs_path(doc.cover_path).is_file()
        except Exception:
            cover_exists = False
        if not cover_exists:
            issues.append(ISSUE_ORPHAN_COVER)

    # 内容问题
    if content_chars == 0:
        issues.append(ISSUE_EMPTY_CONTENT)
    elif content_chars < threshold:
        issues.append(ISSUE_SHORT_CONTENT)

    # 无封面
    if not doc.cover_path:
        issues.append(ISSUE_NO_COVER)

    # 未分类
    if not doc.collection_id:
        issues.append(ISSUE_UNCLASSIFIED)

    # needs_ocr 启发式
    needs_ocr = (
        (doc.format or "").lower() == "pdf"
        and content_chars < threshold
        and file_exists
    )
    if needs_ocr:
        issues.append(ISSUE_NEEDS_OCR)

    # 重复（由调用方传入 duplicate_group_size）
    # 这里先占位，audit_all 会更新

    # 语言变体
    if doc.language and normalize_language(doc.language) not in ("zh", "en", "ja", "und"):
        issues.append(ISSUE_LANGUAGE_VARIANT)
    # 也标记原始值不在标准集合的情况
    if doc.language and doc.language.strip().lower() not in ("zh", "en", "ja", "und"):
        if ISSUE_LANGUAGE_VARIANT not in issues:
            issues.append(ISSUE_LANGUAGE_VARIANT)

    # upsert
    quality = db.query(DocumentQuality).filter_by(document_id=doc.id).first()
    if quality is None:
        quality = DocumentQuality(document_id=doc.id)
        db.add(quality)

    quality.content_chars = content_chars
    quality.file_exists = file_exists
    quality.cover_exists = cover_exists
    quality.needs_ocr = needs_ocr
    quality.issues = issues
    quality.scan_error = scan_error
    quality.checked_at = utcnow()
    return quality


def _get_duplicate_groups(db: Session) -> dict[str, int]:
    """返回 {sha256: count}，仅包含 count > 1 的组。"""
    rows = db.execute(
        select(Document.sha256, func.count(Document.id))
        .where(
            Document.deleted_at.is_(None),
            Document.sha256.isnot(None),
            Document.sha256 != "",
        )
        .group_by(Document.sha256)
        .having(func.count(Document.id) > 1)
    ).all()
    return {row[0]: row[1] for row in rows}


def _audit_batch(db: Session, docs: list[Document], threshold: int, dup_map: dict[str, int]) -> None:
    """审计一批文档并提交。"""
    for doc in docs:
        quality = audit_one(db, doc, threshold)
        if doc.sha256 and doc.sha256 in dup_map:
            quality.duplicate_group_size = dup_map[doc.sha256]
            if ISSUE_DUPLICATE_SHA not in quality.issues:
                quality.issues = [*quality.issues, ISSUE_DUPLICATE_SHA]
        else:
            quality.duplicate_group_size = 0
    db.commit()


_AUDIT_BATCH = 100

# 审计只需要这些列：不再 select 整行，避免把 content（每本最多 20 万字符）全部载入内存
_AUDIT_COLUMNS = (
    Document.id,
    Document.file_path,
    Document.cover_path,
    Document.format,
    Document.language,
    Document.collection_id,
    Document.sha256,
)


def _audit_conditions(ids: list[uuid.UUID] | None) -> list:
    conditions = [
        Document.deleted_at.is_(None),
        Document.type.in_(("book", "pdf")),
    ]
    if ids:
        conditions.append(Document.id.in_(ids))
    return conditions


def _count_audit_docs(db: Session, ids: list[uuid.UUID] | None) -> int:
    return db.scalar(
        select(func.count()).select_from(Document).where(*_audit_conditions(ids))
    ) or 0


def _load_audit_batch(
    db: Session, ids: list[uuid.UUID] | None, offset: int, limit: int
) -> list[Document]:
    return db.scalars(
        select(Document)
        .where(*_audit_conditions(ids))
        .options(load_only(*_AUDIT_COLUMNS))
        .order_by(Document.id)
        .offset(offset)
        .limit(limit)
    ).all()


def _run_audit(ids: list[uuid.UUID] | None) -> None:
    """后台审计任务：分批读取 + 分批提交，内存与批大小成正比。"""
    try:
        settings = get_settings()
        threshold = settings.quality_min_content_chars
        db = SessionLocal()
        try:
            dup_map = _get_duplicate_groups(db)
            total = _count_audit_docs(db, ids)
            _job.update(total=total, done=0)

            done = 0
            offset = 0
            while True:
                docs = _load_audit_batch(db, ids, offset, _AUDIT_BATCH)
                if not docs:
                    break
                _audit_batch(db, docs, threshold, dup_map)
                done += len(docs)
                _job.update(done=min(done, total))
                db.expunge_all()
                offset += _AUDIT_BATCH

            _job.finish()
        finally:
            db.close()
    except Exception as exc:
        _job.fail(str(exc)[:500])


def start_audit(ids: list[uuid.UUID] | None = None) -> bool:
    """启动审计任务（单飞）。已在运行返回 False。"""
    if not _job.start():
        return False
    t = threading.Thread(target=_run_audit, args=(ids,), daemon=True)
    t.start()
    return True


def status() -> dict:
    """返回审计任务状态。"""
    return _job.snapshot()


def scan_orphans(db: Session) -> dict:
    """扫描孤儿文件与缺失文件。

    引用集合 = 所有 documents.file_path + documents.cover_path（含软删除）。
    孤儿 = 磁盘上存在但无文档引用的文件。
    缺失 = 文档引用但磁盘不存在的文件。
    """
    settings = get_settings()
    data_root = Path(settings.data_dir).resolve()

    # 收集引用集合
    rows = db.execute(
        select(Document.file_path, Document.cover_path).where(
            Document.file_path.isnot(None) | Document.cover_path.isnot(None)
        )
    ).all()
    referenced: set[str] = set()
    for fp, cp in rows:
        if fp:
            referenced.add(fp.replace("\\", "/"))
        if cp:
            referenced.add(cp.replace("\\", "/"))

    orphan_files: list[dict] = []
    orphan_covers: list[dict] = []
    missing_files: list[dict] = []
    total_bytes = 0
    truncated = False

    # 扫描 data/files 和 data/covers
    max_files = 20_000
    scanned = 0

    for subdir, orphan_list in [("files", orphan_files), ("covers", orphan_covers)]:
        dir_path = data_root / subdir
        if not dir_path.is_dir():
            continue
        for f in sorted(dir_path.iterdir()):
            if not f.is_file() or f.is_symlink():
                continue
            scanned += 1
            if scanned > max_files:
                truncated = True
                break
            rel = f"{subdir}/{f.name}"
            if rel not in referenced:
                size = f.stat().st_size
                orphan_list.append({"rel_path": rel, "size": size})
                total_bytes += size

    # 检查缺失文件（文档引用但磁盘不存在）
    for fp, cp in rows:
        for path in (fp, cp):
            if not path:
                continue
            rel = path.replace("\\", "/")
            try:
                if not abs_path(path).is_file():
                    # 找到引用此路径的文档
                    doc = db.query(Document).filter(
                        (Document.file_path == path) | (Document.cover_path == path)
                    ).first()
                    if doc:
                        missing_files.append({
                            "document_id": str(doc.id),
                            "title": doc.title,
                            "rel_path": rel,
                        })
            except Exception:
                pass

    return {
        "orphan_files": orphan_files,
        "orphan_covers": orphan_covers,
        "missing_files": missing_files,
        "total_bytes": total_bytes,
        "truncated": truncated,
    }


def list_issues(db: Session, issue: str, limit: int, offset: int) -> tuple[int, list[dict]]:
    """按问题码列出文档。issue='all' 返回 issues 非空的文档。"""
    from sqlalchemy import cast, String as SAString

    base = (
        db.query(Document, DocumentQuality)
        .outerjoin(DocumentQuality, DocumentQuality.document_id == Document.id)
        .filter(
            Document.deleted_at.is_(None),
            Document.type.in_(("book", "pdf")),
        )
    )

    if issue == "all":
        # issues JSON 非空数组：不同方言处理不同
        base = base.filter(
            DocumentQuality.issues.isnot(None),
            cast(DocumentQuality.issues, SAString) != "[]",
            cast(DocumentQuality.issues, SAString) != "null",
        )
    else:
        # JSON 数组包含指定元素：用 LIKE 简单匹配（问题码是稳定 ASCII 字符串）
        base = base.filter(
            cast(DocumentQuality.issues, SAString).like(f'%"{issue}"%')
        )

    total = base.count()
    rows = base.order_by(Document.title).offset(offset).limit(limit).all()

    items = []
    for doc, quality in rows:
        items.append({
            "document_id": str(doc.id),
            "title": doc.title,
            "type": doc.type,
            "format": doc.format or "",
            "language": doc.language or "",
            "content_chars": quality.content_chars if quality else 0,
            "file_size": doc.file_size or 0,
            "cover_path": doc.cover_path,
            "collection_id": str(doc.collection_id) if doc.collection_id else None,
            "issues": (quality.issues if quality else []) or [],
            "checked_at": quality.checked_at.isoformat() if quality and quality.checked_at else None,
        })

    return total, items
