"""上传文件的元数据与封面提取（EPUB / PDF / MOBI / AZW3 / TXT）。

EPUB：zipfile + ElementTree 解析 container.xml → OPF（题名/作者/出版社/ISBN/封面）
PDF：pypdf 读取文档信息（题名/作者/主题）
MOBI/AZW3：解析 PDB 头 + EXTH 记录（题名/作者/出版社/ISBN）
TXT：仅以文件名作为题名
"""

from __future__ import annotations

import io
import logging
import struct
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import IO

from pypdf import PdfReader

logger = logging.getLogger("kb.metadata")

_BLOCKED_EXTS = {"exe", "bat", "cmd", "sh", "js", "jar", "com", "msi"}


def extract_metadata(filename: str, fmt: str, data: bytes) -> dict:
    """返回 {title, author, publisher, language, isbn, description, cover: bytes|None, cover_ext}"""
    return _extract_from_bytes(filename, fmt, data)


def extract_metadata_from_file(filename: str, fmt: str, file_path: Path) -> dict:
    """同 extract_metadata，但从文件路径读取（避免全量载入内存）"""
    base_title = _strip_ext(filename)
    meta = _default_meta(base_title)

    try:
        if fmt == "epub":
            with zipfile.ZipFile(file_path) as zf:
                _extract_epub_from_zip(zf, meta)
        elif fmt == "pdf":
            with open(file_path, "rb") as fh:
                _extract_pdf(fh, meta)
        elif fmt in ("mobi", "azw", "azw3"):
            with open(file_path, "rb") as fh:
                _extract_mobi(fh, meta)
    except Exception as exc:  # 元数据提取失败不应阻断上传
        logger.warning("Metadata extraction failed for %s: %s", filename, exc, exc_info=True)
        meta["extract_error"] = str(exc)

    return meta


def _default_meta(base_title: str) -> dict:
    return {
        "title": base_title,
        "author": "",
        "publisher": "",
        "language": "",
        "isbn": "",
        "description": "",
        "cover": None,
        "cover_ext": "jpg",
    }


def _extract_from_bytes(filename: str, fmt: str, data: bytes) -> dict:
    base_title = _strip_ext(filename)
    meta = _default_meta(base_title)

    try:
        if fmt == "epub":
            _extract_epub(data, meta)
        elif fmt == "pdf":
            _extract_pdf(io.BytesIO(data), meta)
        elif fmt in ("mobi", "azw", "azw3"):
            _extract_mobi(io.BytesIO(data), meta)
    except Exception as exc:
        logger.warning("Metadata extraction failed for %s: %s", filename, exc, exc_info=True)
        meta["extract_error"] = str(exc)

    return meta


def _strip_ext(filename: str) -> str:
    name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name.strip() or "未命名文档"


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _element_text(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _extract_epub(data: bytes, meta: dict) -> None:
    _extract_epub_from_zip(zipfile.ZipFile(io.BytesIO(data)), meta)


def _extract_epub_from_zip(zf: zipfile.ZipFile, meta: dict) -> None:
    with zf:
        container = zf.read("META-INF/container.xml").decode("utf-8", "ignore")
        container_root = ET.fromstring(container)
        opf_path = None
        for rootfile in container_root.iter():
            if _localname(rootfile.tag) == "rootfile":
                opf_path = rootfile.get("full-path")
                break
        if not opf_path:
            return

        opf_root = ET.fromstring(zf.read(opf_path).decode("utf-8", "ignore"))
        opf_dir = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""

        # 元数据（dc:xxx）
        for elem in opf_root.iter():
            name = _localname(elem.tag)
            if name == "title":
                text = _element_text(elem)
                if text:
                    meta["title"] = text
            elif name == "creator":
                if _element_text(elem):
                    meta["author"] = _element_text(elem)
            elif name == "publisher":
                if _element_text(elem):
                    meta["publisher"] = _element_text(elem)
            elif name == "language":
                if _element_text(elem):
                    meta["language"] = _element_text(elem)
            elif name == "description":
                if _element_text(elem):
                    meta["description"] = _element_text(elem)
            elif name == "identifier":
                text = _element_text(elem)
                scheme = ""
                for attr_key, attr_val in elem.attrib.items():
                    if _localname(attr_key) == "scheme":
                        scheme = (attr_val or "").upper()
                if text and (scheme == "ISBN" or text.upper().startswith("ISBN")):
                    meta["isbn"] = text

        # 封面：优先 properties="cover-image"，其次 id 含 cover
        cover_href = None
        for elem in opf_root.iter():
            if _localname(elem.tag) != "item":
                continue
            props = elem.get("properties") or ""
            item_id = (elem.get("id") or "").lower()
            href = elem.get("href")
            media = (elem.get("media-type") or "").lower()
            if not href or "image" not in media:
                continue
            if "cover-image" in props:
                cover_href = href
                break
            if "cover" in item_id:
                cover_href = href

        if cover_href:
            path = cover_href.lstrip("/") if cover_href.startswith("/") else opf_dir + cover_href
            try:
                meta["cover"] = zf.read(path)
                ext = cover_href.rsplit(".", 1)[-1].lower() if "." in cover_href else "jpg"
                meta["cover_ext"] = "png" if ext == "png" else "jpg"
            except KeyError:
                pass


def _extract_pdf(fh: IO[bytes], meta: dict) -> None:
    reader = PdfReader(fh)
    info = reader.metadata
    if info is None:
        return
    title = (info.title or "").strip()
    if title:
        meta["title"] = title
    author = (info.author or "").strip()
    if author:
        meta["author"] = author
    subject = (info.subject or "").strip()
    if subject:
        meta["description"] = subject


def _extract_mobi(fh: IO[bytes], meta: dict) -> None:
    data = fh.read()
    # PDB 头：0~32 字节为文档名
    if len(data) < 100:
        return
    pdb_name = data[0:32].split(b"\x00", 1)[0].decode("latin-1", "ignore").strip()
    if pdb_name:
        meta["title"] = pdb_name

    num_records = struct.unpack_from(">H", data, 76)[0]
    if num_records < 1:
        return
    record0_offset = struct.unpack_from(">I", data, 78)[0]
    if data[record0_offset + 16 : record0_offset + 20] != b"MOBI":
        return

    # MOBI 头部长度（自 +16 起）
    mobi_header_len = struct.unpack_from(">I", data, record0_offset + 20)[0]
    exth_flags = struct.unpack_from(">I", data, record0_offset + 128)[0]
    if not exth_flags & 0x40:
        return

    exth_start = record0_offset + 16 + mobi_header_len
    if data[exth_start : exth_start + 4] != b"EXTH":
        return

    count = struct.unpack_from(">I", data, exth_start + 8)[0]
    offset = exth_start + 12
    for _ in range(count):
        if offset + 8 > len(data):
            break
        rec_type, rec_len = struct.unpack_from(">II", data, offset)
        if rec_len < 8 or offset + rec_len > len(data):
            break
        payload = data[offset + 8 : offset + rec_len]
        if rec_type == 100:
            meta["author"] = payload.decode("utf-8", "ignore")
        elif rec_type == 101:
            meta["publisher"] = payload.decode("utf-8", "ignore")
        elif rec_type == 103:
            meta["description"] = payload.decode("utf-8", "ignore")
        elif rec_type == 104:
            meta["isbn"] = payload.decode("utf-8", "ignore")
        elif rec_type == 503:
            updated_title = payload.decode("utf-8", "ignore").strip()
            if updated_title:
                meta["title"] = updated_title
        offset += rec_len
