"""documents 统一文档 API：CRUD、上传（后端元数据提取）、Range 流式取文件、封面、标签、双链同步、反链"""

import re
import uuid
from datetime import timezone
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, defer, selectinload
from starlette.concurrency import run_in_threadpool

from ..config import get_settings
from ..database import SessionLocal, get_db
from ..models import Collection, Document, DocumentTag, DocumentVersion, Link, Tag, utcnow
from ..schemas import (
    DocumentCreate,
    DocumentOut,
    DocumentUpdate,
    DocumentVersionOut,
    DocumentVersionSummary,
    TagsSet,
)
from ..services import (
    auto_classify,
    content_extract,
    file_storage,
    metadata_extract,
    metadata_fetch,
    ocr_classify,
    reindex_job,
    url_safety,
)
from ..services.sql_utils import escape_like

router = APIRouter(prefix="/documents", tags=["documents"])

settings = get_settings()

MIME_BY_FORMAT = {
    "epub": "application/epub+zip",
    "pdf": "application/pdf",
    "mobi": "application/x-mobipocket-ebook",
    "azw": "application/vnd.amazon.ebook",
    "azw3": "application/vnd.amazon.mobi8-ebook",
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
}

# 上传格式白名单：拒绝可执行/未知扩展名，防止任意文件落盘与浏览器内容嗅探
UPLOAD_FORMAT_WHITELIST = {"epub", "pdf", "mobi", "azw", "azw3", "txt", "md"}

RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")

# 列表接口的规模上限：此前 limit 默认 None = 返回全表，调用方（书架/命令面板/双链补全）
# 全部不带 limit，等于任何一次列表都要把整库的 content 读进内存。
LIST_DEFAULT_LIMIT = 200
LIST_HARD_LIMIT = 1000

# 书架只认这两种 type：笔记（type=note）不进书架，也不计入书架统计
BOOK_TYPES = ("book", "pdf")

# 阅读状态分桶：与前端智能书架（在读/未读/读完）保持一致。
# 未读含 read_progress 为 NULL 的旧数据；在修为 0 < p < 1；读完为 p >= 1。


class LibraryStats(BaseModel):
    """书架侧栏计数：一次聚合取全，避免前端为了算分类数量去拉整库列表"""

    total: int
    reading: int
    unread: int
    done: int
    # key 为 collection_id；无分类的书籍单独记在 uncategorized
    by_collection: dict[str, int]
    uncategorized: int


class CountOut(BaseModel):
    count: int


def apply_types_filter(stmt, types: str | None):
    """types 支持逗号分隔（书架传 "book,pdf"），单值仍然兼容原有 type 参数"""
    if not types:
        return stmt
    values = [v.strip() for v in types.split(",") if v.strip()]
    if not values:
        return stmt
    return stmt.where(Document.type.in_(values))


def apply_status_filter(stmt, status: str | None):
    if not status or status == "all":
        return stmt
    if status == "reading":
        return stmt.where(Document.read_progress > 0, Document.read_progress < 1)
    if status == "unread":
        return stmt.where(or_(Document.read_progress.is_(None), Document.read_progress <= 0))
    if status == "done":
        return stmt.where(Document.read_progress >= 1)
    raise HTTPException(status_code=400, detail=f"不支持的 status: {status}")


def apply_document_filters(
    stmt,
    *,
    include_deleted: bool = False,
    type: str | None = None,
    types: str | None = None,
    collection_id: uuid.UUID | None = None,
    tag: str | None = None,
    q: str | None = None,
    author: str | None = None,
    status: str | None = None,
):
    """列表与计数共用同一套过滤条件，避免两处条件漂移导致"显示 N 本、翻页却少几本"。"""
    stmt = stmt.where(Document.deleted_at.is_(None) if not include_deleted else True)
    stmt = apply_types_filter(stmt, types)
    if type:
        stmt = stmt.where(Document.type == type)
    if collection_id:
        stmt = stmt.where(Document.collection_id == collection_id)
    if tag:
        stmt = stmt.join(DocumentTag, DocumentTag.document_id == Document.id).join(
            Tag, Tag.id == DocumentTag.tag_id
        ).where(Tag.name == tag)
    if q:
        like = f"%{escape_like(q)}%"
        stmt = stmt.where(
            or_(
                Document.title.ilike(like, escape="\\"),
                Document.author.ilike(like, escape="\\"),
                Document.description.ilike(like, escape="\\"),
                Document.content.ilike(like, escape="\\"),
            )
        )
    if author:
        stmt = stmt.where(Document.author.ilike(f"%{escape_like(author)}%", escape="\\"))
    return apply_status_filter(stmt, status)


# Content-Disposition 的 ascii 回退名白名单：标题是用户可控字段，
# 只剔双引号挡不住 \r\n（响应头注入，取决于 h11/uvicorn 是否拒绝控制字符：
# 拒绝则 500，不拒绝则头注入，两种都不可接受），故按白名单逐字符收敛。
_ASCII_FILENAME_RE = re.compile(r"[^A-Za-z0-9._ -]")


def file_download_headers(doc: Document, extra: dict | None = None) -> dict:
    """文件下载响应安全头：强制 attachment 下载 + 禁止 MIME 嗅探"""
    from urllib.parse import quote

    ext = doc.format or ""
    raw_name = f"{doc.title or 'document'}{'.' + ext if ext else ''}"
    # filename*=UTF-8''... 里的 quote() 已把 CRLF 编码为 %0D%0A，那一段本身安全；
    # 危险的是 ascii 回退名，必须白名单化。
    ascii_fallback = (
        _ASCII_FILENAME_RE.sub("_", raw_name.encode("ascii", "ignore").decode("ascii")).strip()
        or "document"
    )
    headers = {
        "Content-Disposition": f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(raw_name)}",
        "X-Content-Type-Options": "nosniff",
    }
    if extra:
        headers.update(extra)
    return headers

# [[标题]] 双链语法（Obsidian 风格）
MENTION_PATTERN = re.compile(r"\[\[([^\[\]\n]+?)\]\]")


def sync_mention_links(db: Session, doc: Document) -> int:
    """解析文档 content 中的 [[标题]]，按标题匹配其他文档并重建 mention 链接。返回新建链接数。"""
    db.flush()  # 确保新文档的 id（Python 侧默认值）已生成
    db.execute(delete(Link).where(Link.source_id == doc.id, Link.type == "mention"))
    if not doc.content:
        return 0

    titles = {t.strip() for t in MENTION_PATTERN.findall(doc.content)}
    titles.discard("")
    if not titles:
        return 0

    # 一次 IN 批量查询替代逐标题 SELECT（此前为 N+1，长文多链接时是查询风暴）
    targets = db.scalars(
        select(Document).where(
            Document.title.in_(titles),
            Document.id != doc.id,
            Document.deleted_at.is_(None),
        )
    ).all()
    for target in targets:
        db.add(Link(source_id=doc.id, target_id=target.id, type="mention"))
    return len(targets)


class Backlink(BaseModel):
    id: str
    title: str
    type: str
    snippet: str = ""


class DocumentInsight(BaseModel):
    summary: str
    suggested_tags: list[str]


# ---------- 文档洞察：词频关键词 + 一句话摘要 ----------

_LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]+")
_CJK_BIGRAM_RE = re.compile(r"[\u4e00-\u9fff]")

_KEYWORD_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "are", "was", "were",
    "have", "has", "had", "not", "but", "you", "your", "its", "their", "they",
    "them", "our", "can", "will", "would", "should", "could", "into", "about",
    "these", "those", "been", "being", "does", "doing",
    "这些", "那些", "我们", "你们", "他们", "一个", "这个", "那个", "以及",
    "但是", "然后", "因此", "所以", "如果", "虽然", "可以", "没有", "就是",
    "还有", "进行", "通过", "对于", "关于", "出现", "或者", "并且",
}

_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")


def extract_keywords(text: str, top_n: int = 5) -> list[str]:
    """极简关键词提取：拉丁词 + 中文 2 字滑窗词频统计（无外部依赖）"""
    counts: dict[str, int] = {}
    for word in _LATIN_WORD_RE.findall(text):
        w = word.lower()
        counts[w] = counts.get(w, 0) + 1
    cjk_runs = re.findall(r"[\u4e00-\u9fff]+", text)
    for run in cjk_runs:
        for i in range(len(run) - 1):
            bigram = run[i : i + 2]
            counts[bigram] = counts.get(bigram, 0) + 1
    filtered = [
        (w, c)
        for w, c in counts.items()
        if w not in _KEYWORD_STOPWORDS and len(w) >= 2 and not w.isdigit()
    ]
    # 中文滑窗会互相重叠：同源 bigram 只保留频次最高者附近即可，简单按频次排序去重输出
    filtered.sort(key=lambda wc: (-wc[1], wc[0]))
    picked: list[str] = []
    for w, _c in filtered:
        if any(w in p or p in w for p in picked):
            continue
        picked.append(w)
        if len(picked) >= top_n:
            break
    return picked


def one_line_summary(text: str, max_chars: int = 100) -> str:
    """取第一句非空句子作为一句话摘要，过长截断"""
    plain = re.sub(r"\s+", " ", text or "").strip()
    if not plain:
        return ""
    for part in _SENTENCE_SPLIT_RE.split(plain):
        part = part.strip()
        if len(part) >= 8:
            return part[:max_chars] + ("…" if len(part) > max_chars else "")
    return plain[:max_chars] + ("…" if len(plain) > max_chars else "")


def _context_snippet(content: str, keyword: str, width: int = 60) -> str:
    if not content:
        return ""
    plain = re.sub(r"\s+", " ", content).strip()
    idx = plain.lower().find(keyword.lower())
    if idx < 0:
        return plain[:width] + ("…" if len(plain) > width else "")
    start = max(0, idx - width // 2)
    end = min(len(plain), idx + len(keyword) + width // 2)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(plain) else ""
    return (
        prefix
        + plain[start:idx]
        + "<mark>"
        + plain[idx : idx + len(keyword)]
        + "</mark>"
        + plain[idx + len(keyword) : end]
        + suffix
    )


def get_doc_or_404(db: Session, doc_id: uuid.UUID, include_deleted: bool = False) -> Document:
    doc = db.get(Document, doc_id, options=(selectinload(Document.tags),))
    if doc is None or (doc.deleted_at is not None and not include_deleted):
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


SUMMARY_PREVIEW_CHARS = 200


# 摘要只需前 N 个字符（空白压平后取 SUMMARY_PREVIEW_CHARS 字），按此长度取前缀即可
CONTENT_PREVIEW_FETCH_CHARS = SUMMARY_PREVIEW_CHARS * 4


def summarize_content(content: str | None, limit: int = SUMMARY_PREVIEW_CHARS) -> str:
    """列表场景的正文摘要：压平空白后截断，避免大书库一次性传输全文"""
    plain = re.sub(r"\s+", " ", content or "").strip()
    if len(plain) <= limit:
        return plain
    return plain[:limit] + "…"


@router.get("/stats", response_model=LibraryStats)
def library_stats(db: Session = Depends(get_db)):
    """书架侧栏计数：总数 / 智能书架分桶 / 各分类数量，全部按 type in (book, pdf) 统计"""
    alive = [
        Document.deleted_at.is_(None),
        Document.type.in_(BOOK_TYPES),
    ]

    by_collection: dict[str, int] = {}
    uncategorized = 0
    for collection_id, count in db.execute(
        select(Document.collection_id, func.count()).where(*alive).group_by(Document.collection_id)
    ).all():
        if collection_id is None:
            uncategorized += count
        else:
            by_collection[str(collection_id)] = count

    def count_where(*conditions) -> int:
        return db.scalar(select(func.count()).select_from(Document).where(*alive, *conditions)) or 0

    status_columns = (
        count_where(Document.read_progress > 0, Document.read_progress < 1),
        count_where(or_(Document.read_progress.is_(None), Document.read_progress <= 0)),
        count_where(Document.read_progress >= 1),
    )
    total = sum(by_collection.values()) + uncategorized
    return LibraryStats(
        total=total,
        reading=status_columns[0],
        unread=status_columns[1],
        done=status_columns[2],
        by_collection=by_collection,
        uncategorized=uncategorized,
    )


@router.get("/count", response_model=CountOut)
def count_documents(
    type: str | None = None,
    types: str | None = None,
    collection_id: uuid.UUID | None = None,
    tag: str | None = None,
    q: str | None = None,
    author: str | None = None,
    status: str | None = None,
    include_deleted: bool = False,
    db: Session = Depends(get_db),
):
    """与列表同参的计数：书架用它显示"共 N 本"和剩余量，不必把整库拉下来数"""
    stmt = apply_document_filters(
        select(func.count()).select_from(Document),
        include_deleted=include_deleted,
        type=type,
        types=types,
        collection_id=collection_id,
        tag=tag,
        q=q,
        author=author,
        status=status,
    )
    return CountOut(count=db.scalar(stmt) or 0)


@router.get("", response_model=list[DocumentOut])
def list_documents(
    type: str | None = None,
    types: str | None = None,
    collection_id: uuid.UUID | None = None,
    tag: str | None = None,
    q: str | None = None,
    author: str | None = None,
    status: str | None = None,
    include_deleted: bool = False,
    include_content: bool = False,
    limit: int | None = Query(default=None, ge=1, le=LIST_HARD_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(Document).options(selectinload(Document.tags))
    if not include_content:
        # 轻量模式下正文不进 ORM：每行数百 KB 的全文被读出是列表的主要内存开销
        stmt = stmt.options(defer(Document.content))
    stmt = apply_document_filters(
        stmt,
        include_deleted=include_deleted,
        type=type,
        types=types,
        collection_id=collection_id,
        tag=tag,
        q=q,
        author=author,
        status=status,
    )
    stmt = stmt.order_by(Document.created_at.desc()).offset(offset)
    # 未显式传 limit 时按默认页大小截断；需要全量的调用方（书架等）显式传 limit=LIST_HARD_LIMIT
    stmt = stmt.limit(limit if limit is not None else LIST_DEFAULT_LIMIT)
    docs = list(db.scalars(stmt))
    if not include_content:
        # 轻量模式（默认）：content 只返回截断摘要；全文走 GET /documents/{id} 详情接口。
        # 摘要只需开头若干字符，故单独一次批量查询取前缀，而不是把全文拉进内存。
        # 注意顺序：必须先 expunge 再写回 content —— 在受管实例上改属性会被 autoflush
        # 判定为用户修改，把 200 字摘要 UPDATE 回库，覆盖掉正文（不可逆）。
        ids = [d.id for d in docs]
        prefixes: dict = {}
        if ids:
            prefixes = dict(
                db.execute(
                    select(
                        Document.id,
                        func.substr(Document.content, 1, CONTENT_PREVIEW_FETCH_CHARS),
                    ).where(Document.id.in_(ids))
                ).all()
            )
        db.expunge_all()
        for d in docs:
            d.content = summarize_content(prefixes.get(d.id) or "")
    return docs


@router.post("", response_model=DocumentOut, status_code=201)
def create_document(payload: DocumentCreate, db: Session = Depends(get_db)):
    """创建无文件的文档（笔记/文章/剪藏）"""
    data = payload.model_dump(exclude={"tags"})
    doc = Document(**data)
    for name in payload.tags:
        doc.tags.append(_get_or_create_tag(db, name))
    db.add(doc)
    sync_mention_links(db, doc)
    _snapshot_version(db, doc, doc.title, doc.content or "")
    db.commit()
    return doc


@router.post("/reindex-content")
def reindex_document_content(sync: bool = False, db: Session = Depends(get_db)):
    """为已有书籍补充正文全文索引（升级后一次性执行）。

    sync=true 同步执行并返回统计（测试/脚本用）；默认后台执行，返回任务已启动。
    """
    if sync:
        return reindex_job.run_sync(db)
    if not reindex_job.start():
        raise HTTPException(status_code=409, detail="重索引任务已在运行")
    return {"status": "started"}


@router.get("/reindex-status")
def reindex_status():
    """后台重索引任务进度（设置页轮询用）"""
    return reindex_job.status()


@router.get("/{doc_id}", response_model=DocumentOut)
def get_document(doc_id: uuid.UUID, db: Session = Depends(get_db)):
    return get_doc_or_404(db, doc_id)


@router.patch("/{doc_id}", response_model=DocumentOut)
def update_document(doc_id: uuid.UUID, payload: DocumentUpdate, db: Session = Depends(get_db)):
    doc = get_doc_or_404(db, doc_id)
    previous_title = doc.title
    previous_content = doc.content or ""
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(doc, field, value)
    if payload.content is not None or payload.title is not None:
        sync_mention_links(db, doc)
        _snapshot_version(db, doc, previous_title, previous_content)
    # 重命名安全：其他文档中的 [[旧标题]] 引用同步更新
    if payload.title is not None and payload.title != previous_title and previous_title:
        old_ref = f"[[{previous_title}]]"
        new_ref = f"[[{doc.title}]]"
        refs = db.scalars(
            select(Document).where(
                Document.deleted_at.is_(None),
                Document.id != doc.id,
                Document.content.ilike(f"%{escape_like(old_ref)}%", escape="\\"),
            )
        ).all()
        for ref in refs:
            # ilike 命中不区分大小写，替换也必须忽略大小写（仅改标题大小写的场景此前会漏更新）；
            # 用函数替换器避免标题中的 \ 序列被解释为反向引用
            ref.content = re.sub(
                re.escape(old_ref),
                lambda _m: new_ref,
                ref.content or "",
                flags=re.IGNORECASE,
            )
            sync_mention_links(db, ref)
    db.commit()
    return doc


@router.get("/{doc_id}/backlinks", response_model=list[Backlink])
def get_backlinks(doc_id: uuid.UUID, db: Session = Depends(get_db)):
    """反向链接：引用了本文档（[[标题]]）的其他文档，附上下文摘要"""
    doc = get_doc_or_404(db, doc_id)
    rows = db.execute(
        select(Document)
        .join(Link, Link.source_id == Document.id)
        .where(Link.target_id == doc.id, Document.deleted_at.is_(None))
        .order_by(Document.updated_at.desc())
    ).scalars().all()
    return [
        Backlink(
            id=str(r.id),
            title=r.title,
            type=r.type,
            snippet=_context_snippet(r.content or r.description or "", doc.title),
        )
        for r in rows
    ]


@router.get("/{doc_id}/unlinked-mentions", response_model=list[Backlink])
def get_unlinked_mentions(doc_id: uuid.UUID, db: Session = Depends(get_db)):
    """未链接提及：正文包含本文档标题但尚未建立 [[链接]] 的其他文档"""
    doc = get_doc_or_404(db, doc_id)
    title = doc.title.strip()
    if len(title) < 2:
        return []
    linked_ids = set(
        db.scalars(select(Link.source_id).where(Link.target_id == doc.id))
    )
    candidates = db.scalars(
        select(Document)
        .where(
            Document.deleted_at.is_(None),
            Document.id != doc.id,
            Document.content.ilike(f"%{escape_like(title)}%", escape="\\"),
        )
        .order_by(Document.updated_at.desc())
        .limit(50)
    ).all()
    return [
        Backlink(
            id=str(c.id),
            title=c.title,
            type=c.type,
            snippet=_context_snippet(c.content or "", title),
        )
        for c in candidates
        if c.id not in linked_ids
    ]


@router.get("/{doc_id}/insight", response_model=DocumentInsight)
def get_document_insight(doc_id: uuid.UUID, db: Session = Depends(get_db)):
    """文档洞察：基于正文词频的 top 关键词标签 + 一句话摘要（供前端"智能洞察"卡片）"""
    doc = get_doc_or_404(db, doc_id)
    body = doc.content or ""
    return DocumentInsight(
        summary=one_line_summary(body or doc.description or ""),
        suggested_tags=extract_keywords(f"{doc.title} {body}"),
    )


@router.delete("/{doc_id}", status_code=204)
def delete_document(doc_id: uuid.UUID, hard: bool = False, db: Session = Depends(get_db)):
    # 硬删除允许作用于已软删除的文档（彻底清理）
    doc = get_doc_or_404(db, doc_id, include_deleted=hard)
    file_path, cover_path = doc.file_path, doc.cover_path
    if hard:
        db.delete(doc)
        db.commit()
        # 先落库再删文件：commit 失败时记录仍在、可重试；文件删除失败仅留孤儿磁盘文件
        for p in (file_path, cover_path):
            if p:
                file_storage.delete(p)
    else:
        doc.deleted_at = utcnow()
        db.commit()


@router.post("/upload", response_model=DocumentOut, status_code=201)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    collection_id: uuid.UUID | None = Form(default=None),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
):
    """上传书籍/PDF：流式写入磁盘 + 后端提取元数据与封面

    SHA-256 / 元数据解析 / 正文提取均为秒级同步重活，必须经
    run_in_threadpool 下放线程池，否则会阻塞事件循环、冻结全站请求。

    限流已由全局 RateLimitMiddleware 按路径处理（/api/documents/upload 50/60s），
    此处不再手工调用 —— 否则同一请求计两次。
    """
    max_bytes = settings.upload_max_size_mb * 1024 * 1024

    filename = file.filename or "unnamed"
    fmt = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if fmt not in UPLOAD_FORMAT_WHITELIST:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format: .{fmt or '(none)'}；allowed: {', '.join(sorted(UPLOAD_FORMAT_WHITELIST))}",
        )
    doc_id = uuid.uuid4()

    try:
        rel_path, file_size = await file_storage.stream_file_to_disk(
            file, doc_id, fmt or "bin", max_bytes
        )
    except ValueError:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {settings.upload_max_size_mb} MB upload limit",
        )

    abs_file = file_storage.abs_path(rel_path)

    # SHA-256 去重：同内容文件已在库（含回收站外）则拒绝并提示已有条目
    sha256 = await run_in_threadpool(file_storage.sha256_of, rel_path)
    existing = db.scalar(
        select(Document).where(
            Document.sha256 == sha256,
            Document.deleted_at.is_(None),
        )
    )
    if existing:
        file_storage.delete(rel_path)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "duplicate",
                "message": f"已存在相同文件《{existing.title}》",
                "existing_id": str(existing.id),
                "existing_title": existing.title,
            },
        )

    meta = await run_in_threadpool(
        metadata_extract.extract_metadata_from_file, filename, fmt, abs_file
    )

    text_content = (
        await run_in_threadpool(content_extract.extract_text_from_file, fmt, abs_file)
        if fmt in content_extract.TEXT_FORMATS
        else ""
    )

    if collection_id is None:
        # 三级自动分类：标题/作者关键词 → 已提取正文词频 → （后台）OCR 扫描件
        target = await run_in_threadpool(
            auto_classify.classify_document,
            meta["title"] or filename,
            meta["author"] or "",
            text_content,
        )
        if target:
            col = db.scalar(select(Collection).where(Collection.name == target))
            if col:
                collection_id = col.id
        elif settings.auto_classify_ocr and fmt in ("pdf", "epub"):
            background_tasks.add_task(classify_ocr_background, str(doc_id), fmt, rel_path)

    doc = Document(
        id=doc_id,
        type="pdf" if fmt == "pdf" else "book",
        title=meta["title"] or filename,
        author=meta["author"],
        publisher=meta["publisher"],
        language=meta["language"] or "zh",
        isbn=meta["isbn"],
        description=meta["description"],
        file_path=rel_path,
        file_size=file_size,
        format=fmt or None,
        content=text_content,
        sha256=sha256,
        collection_id=collection_id,
    )
    if meta.get("cover"):
        doc.cover_path = file_storage.save_cover(doc_id, meta["cover"], meta.get("cover_ext", "jpg"))

    db.add(doc)
    db.commit()
    return doc


def classify_ocr_background(doc_id: str, fmt: str, rel_path: str) -> None:
    """后台 OCR 分类：扫描件采样页 OCR → 词频打分 → 若无分类则写入。

    上传响应已返回；独立会话执行，失败仅记日志不影响主流程。
    """
    import logging

    logger = logging.getLogger("kb.ocr_classify")
    try:
        with SessionLocal() as db:
            doc = db.get(Document, uuid.UUID(doc_id))
            if not doc or doc.deleted_at or doc.collection_id is not None:
                return
            path = file_storage.abs_path(doc.file_path or rel_path)
            if not path.is_file():
                return
            text = ocr_classify.ocr_book_text(path, fmt)
            if len(text) < 500:
                logger.info("OCR 分类无有效文本，跳过: %s", doc.title)
                return
            target = auto_classify.classify_document(doc.title or "", doc.author or "", text)
            if not target:
                logger.info("OCR 分类无主题特征，跳过: %s", doc.title)
                return
            col = db.scalar(select(Collection).where(Collection.name == target))
            if col:
                doc.collection_id = col.id
                db.commit()
                logger.info("OCR 后台分类: 《%s》 -> %s", doc.title, target)
    except Exception as exc:
        logging.getLogger("kb.ocr_classify").warning("OCR 后台分类失败: %s", exc, exc_info=True)


# Range 分段读取的分块大小：64KB 兼顾系统调用次数与内存占用
FILE_CHUNK_SIZE = 64 * 1024


def _iter_file_range(path: Path, start: int, length: int, chunk_size: int = FILE_CHUNK_SIZE):
    """按 [start, start+length) 区间分块读文件，避免整段进内存。"""
    remaining = length
    with open(path, "rb") as f:
        f.seek(start)
        while remaining > 0:
            chunk = f.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@router.get("/{doc_id}/file")
def get_document_file(doc_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    """流式返回原始文件，支持 Range 分段请求（206）"""
    doc = get_doc_or_404(db, doc_id)
    if not doc.file_path:
        raise HTTPException(status_code=404, detail="Document has no file")

    path: Path = file_storage.abs_path(doc.file_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File missing on disk")

    size = path.stat().st_size
    media_type = MIME_BY_FORMAT.get(doc.format or "", "application/octet-stream")
    range_header = request.headers.get("range")

    if range_header:
        match = RANGE_PATTERN.match(range_header.strip())
        if match:
            start_s, end_s = match.groups()
            # suffix-range：bytes=-N 语义为"最后 N 字节"（此前被误解析为 0..N-1，
            # 个别下载工具会拿到损坏数据段）
            if not start_s and end_s:
                suffix = int(end_s)
                if suffix <= 0:
                    return Response(
                        status_code=416,
                        headers={"Content-Range": f"bytes */{size}"},
                    )
                start = max(0, size - suffix)
                end = size - 1
            else:
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else size - 1
                end = min(end, size - 1)
            if start > end or start >= size:
                return Response(
                    status_code=416,
                    headers={"Content-Range": f"bytes */{size}"},
                )
            length = end - start + 1
            # 分块流式返回：此前是 f.read(length) 一次性读入，bytes=0- 时 length 就是
            # 整个文件大小（500MB 的 PDF 会整本进内存，两三个并发即打爆进程）。
            # 非 Range 路径用的 FileResponse 本来就是流式的，这里对齐它的行为。
            return StreamingResponse(
                _iter_file_range(path, start, length),
                status_code=206,
                media_type=media_type,
                headers=file_download_headers(
                    doc,
                    {
                        "Content-Range": f"bytes {start}-{end}/{size}",
                        "Content-Length": str(length),
                        "Accept-Ranges": "bytes",
                    },
                ),
            )

    return FileResponse(
        path,
        media_type=media_type,
        headers=file_download_headers(doc, {"Accept-Ranges": "bytes"}),
    )


@router.get("/{doc_id}/cover")
def get_document_cover(doc_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    doc = get_doc_or_404(db, doc_id)
    if not doc.cover_path:
        raise HTTPException(status_code=404, detail="No cover")
    path = file_storage.abs_path(doc.cover_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Cover missing on disk")
    media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    etag = file_storage.file_etag(doc.cover_path)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return FileResponse(
        path,
        media_type=media,
        headers={
            "Cache-Control": "public, max-age=86400",
            "ETag": etag,
        },
    )


@router.put("/{doc_id}/tags", response_model=DocumentOut)
def set_document_tags(doc_id: uuid.UUID, payload: TagsSet, db: Session = Depends(get_db)):
    doc = get_doc_or_404(db, doc_id)
    doc.tags = [_get_or_create_tag(db, name) for name in dict.fromkeys(payload.tags)]
    db.commit()
    return doc


def _get_or_create_tag(db: Session, name: str) -> Tag:
    name = name.strip()
    tag = db.scalar(select(Tag).where(Tag.name == name))
    if tag is None:
        tag = Tag(name=name)
        db.add(tag)
        db.flush()
    return tag


# ---------- 文档版本历史 ----------

VERSION_MIN_INTERVAL_SECONDS = 30
VERSION_MAX_PER_DOC = 100


def _snapshot_version(db: Session, doc: Document, title: str, content: str, *, force: bool = False) -> None:
    """保存前快照上一状态：与最新版本内容相同或间隔不足 30 秒则跳过，每文档最多保留 100 版。

    force=True（版本恢复等需要完整撤销链的场景）跳过间隔限制，但仍保留
    内容去重与数量裁剪。
    """
    latest = db.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == doc.id)
        .order_by(DocumentVersion.created_at.desc())
        .limit(1)
    )
    if latest is not None:
        if latest.title == title and latest.content == content:
            return
        if not force:
            created = latest.created_at
            if created.tzinfo is not None:
                created = created.astimezone(timezone.utc).replace(tzinfo=None)
            if (utcnow().replace(tzinfo=None) - created).total_seconds() < VERSION_MIN_INTERVAL_SECONDS:
                return
    db.add(DocumentVersion(document_id=doc.id, title=title, content=content or ""))
    db.flush()
    stale_ids = db.scalars(
        select(DocumentVersion.id)
        .where(DocumentVersion.document_id == doc.id)
        .order_by(DocumentVersion.created_at.desc())
        .offset(VERSION_MAX_PER_DOC)
    ).all()
    if stale_ids:
        db.execute(delete(DocumentVersion).where(DocumentVersion.id.in_(stale_ids)))


@router.get("/{doc_id}/versions", response_model=list[DocumentVersionSummary])
def list_document_versions(doc_id: uuid.UUID, db: Session = Depends(get_db)):
    get_doc_or_404(db, doc_id)
    rows = db.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == doc_id)
        .order_by(DocumentVersion.created_at.desc())
    ).all()
    return [
        DocumentVersionSummary(
            id=v.id,
            title=v.title,
            preview=re.sub(r"\s+", " ", v.content or "")[:80],
            created_at=v.created_at,
        )
        for v in rows
    ]


@router.get("/{doc_id}/versions/{version_id}", response_model=DocumentVersionOut)
def get_document_version(doc_id: uuid.UUID, version_id: uuid.UUID, db: Session = Depends(get_db)):
    get_doc_or_404(db, doc_id)
    version = db.get(DocumentVersion, version_id)
    if version is None or version.document_id != doc_id:
        raise HTTPException(status_code=404, detail="Version not found")
    return version


@router.post("/{doc_id}/versions/{version_id}/restore", response_model=DocumentOut)
def restore_document_version(doc_id: uuid.UUID, version_id: uuid.UUID, db: Session = Depends(get_db)):
    doc = get_doc_or_404(db, doc_id)
    version = db.get(DocumentVersion, version_id)
    if version is None or version.document_id != doc_id:
        raise HTTPException(status_code=404, detail="Version not found")
    # 先快照当前内容（复用统一快照逻辑：内容去重 + 100 版裁剪；force 跳过 30s
    # 间隔以保留完整撤销链）。此前裸 db.add 绕过该逻辑，反复恢复会无限堆积版本。
    _snapshot_version(db, doc, doc.title, doc.content or "", force=True)
    doc.title = version.title
    doc.content = version.content
    sync_mention_links(db, doc)
    db.commit()
    return doc


# ---------- 在线元数据（对标 BookLore/Calibre：Google Books + Open Library） ----------


class MetadataCandidate(BaseModel):
    source: str
    title: str
    author: str
    publisher: str
    isbn: str
    description: str
    language: str
    year: str
    cover_url: str | None


@router.get("/metadata/search", response_model=list[MetadataCandidate])
def search_metadata_online(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=6, ge=1, le=12),
):
    """在线搜索书籍元数据候选（编辑弹窗"联网搜索"用）；同步端点由线程池执行"""
    return metadata_fetch.search_online(q, limit)


class MetadataApply(BaseModel):
    title: str | None = None
    author: str | None = None
    publisher: str | None = None
    isbn: str | None = None
    description: str | None = None
    language: str | None = None
    cover_url: str | None = None


@router.patch("/{doc_id}/metadata", response_model=DocumentOut)
def apply_metadata_online(doc_id: uuid.UUID, payload: MetadataApply, db: Session = Depends(get_db)):
    """应用在线元数据候选到指定文档；带 cover_url 时下载封面落盘"""
    doc = get_doc_or_404(db, doc_id)
    for field, value in payload.model_dump(exclude_unset=True, exclude={"cover_url"}).items():
        if value:  # 空串不覆盖本地已填字段
            setattr(doc, field, value)

    if payload.cover_url:
        url_safety.validate_url(payload.cover_url)
        data, ext = metadata_fetch.download_cover(payload.cover_url)
        doc.cover_path = file_storage.save_cover(doc.id, data, ext)

    db.commit()
    db.refresh(doc)
    return doc
