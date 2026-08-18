"""文件落盘存储：DB 只存相对路径，内容保存在 data 目录下。

目录结构：
    {data_dir}/files/{doc_id}.{ext}
    {data_dir}/covers/{doc_id}.{ext}
"""

import hashlib
import re
import uuid
from pathlib import Path

from fastapi import UploadFile

from ..config import get_settings

SAFE_EXT = re.compile(r"^[a-zA-Z0-9]{1,8}$")
CHUNK_SIZE = 1024 * 1024


def _data_root() -> Path:
    return Path(get_settings().data_dir).resolve()


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def save_file(doc_id: uuid.UUID, fmt: str, data: bytes) -> str:
    ext = fmt.lower().lstrip(".") or "bin"
    if not SAFE_EXT.match(ext):
        ext = "bin"
    files_dir = _data_root() / "files"
    _ensure_dir(files_dir)
    target = files_dir / f"{doc_id}.{ext}"
    target.write_bytes(data)
    return str(target.relative_to(_data_root())).replace("\\", "/")


async def stream_file_to_disk(
    file: UploadFile, doc_id: uuid.UUID, fmt: str, max_size_bytes: int
) -> tuple[str, int]:
    """Stream an UploadFile to disk in chunks. Returns (relative_path, total_bytes).

    Raises ValueError if total bytes exceed max_size_bytes.
    """
    ext = fmt.lower().lstrip(".") or "bin"
    if not SAFE_EXT.match(ext):
        ext = "bin"
    files_dir = _data_root() / "files"
    _ensure_dir(files_dir)
    target = files_dir / f"{doc_id}.{ext}"
    total = 0
    with open(target, "wb") as f:
        while True:
            chunk = await file.read(CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_size_bytes:
                f.close()
                target.unlink(missing_ok=True)
                raise ValueError(f"File exceeds {max_size_bytes} limit")
            f.write(chunk)
    return str(target.relative_to(_data_root())).replace("\\", "/"), total


def save_cover(doc_id: uuid.UUID, data: bytes, ext: str = "jpg") -> str:
    ext = ext.lower().lstrip(".") or "jpg"
    if not SAFE_EXT.match(ext):
        ext = "jpg"
    covers_dir = _data_root() / "covers"
    _ensure_dir(covers_dir)
    target = covers_dir / f"{doc_id}.{ext}"
    target.write_bytes(data)
    return str(target.relative_to(_data_root())).replace("\\", "/")


def abs_path(rel_path: str) -> Path:
    """相对路径 → 绝对路径，校验不允许逃逸 data 目录"""
    root = _data_root()
    target = (root / rel_path).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"Illegal path: {rel_path}")
    return target


def write_relative(rel_path: str, data: bytes) -> None:
    """按备份内相对路径写文件（带逃逸校验）"""
    target = abs_path(rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def file_etag(rel_path: str) -> str:
    """Simple ETag based on file size + mtime."""
    path = abs_path(rel_path)
    stat = path.stat()
    return f'"{stat.st_size:x}-{int(stat.st_mtime):x}"'


def sha256_of(rel_path: str) -> str:
    """文件内容 SHA-256（去重用），分块读取避免大文件占内存"""
    path = abs_path(rel_path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def delete(rel_path: str | None) -> None:
    if not rel_path:
        return
    try:
        abs_path(rel_path).unlink(missing_ok=True)
    except OSError:
        pass
