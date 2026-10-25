"""文档原始文件（Range 流式）与封面路由。"""

import re
import uuid
from pathlib import Path

from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from ...database import get_db
from ...models import Document
from ...services import file_storage
from ._shared import get_doc_or_404, router

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

RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")

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
