"""电子书正文提取（EPUB / PDF / TXT / MD / HTML），存入 content 供全文检索索引"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import re
import shutil
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from lxml import html as lxml_html
from pypdf import PdfReader

logger = logging.getLogger("kb.content_extract")

MAX_CHARS = 200_000
EXTRACT_TIMEOUT = 5  # 单个文件提取时间预算（秒），超时跳过，防止扫描版 PDF 拖垮全库重索引
OCR_TIMEOUT = 90  # OCR 通道独立预算（分钟级）
OCR_MAX_PAGES = 20

TEXT_FORMATS = {"epub", "pdf", "txt", "md", "html"}


def _bounded(fn, timeout: float = EXTRACT_TIMEOUT) -> str:
    """独立线程 + 时间预算执行提取；超时放弃线程（wait=False），不阻塞后续文件。"""
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        logger.warning("正文提取超时（>%ss），已跳过", timeout)
        return ""
    except Exception as exc:
        logger.warning("正文提取失败: %s", exc, exc_info=True)
        return ""
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _head(path: Path, n: int) -> bytes:
    with open(path, "rb") as f:
        return f.read(n)


def _looks_like_html(sniff: bytes) -> bool:
    lowered = sniff.lower()
    return any(marker in lowered for marker in (b"<html", b"<!doctype", b"<body", b"<head"))


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)[:MAX_CHARS]
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", "ignore")[:MAX_CHARS]


def _extract_pdf(path: Path, ocr: bool = False) -> str:
    reader = PdfReader(str(path))
    parts: list[str] = []
    total = 0
    for page in reader.pages:
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            continue
        if text:
            parts.append(text)
            total += len(text)
            if total >= MAX_CHARS:
                break
    if not parts and ocr:
        ocr_text = _ocr_scanned_pdf(path)
        if ocr_text:
            parts.append(ocr_text)
    return "\n\n".join(parts)[:MAX_CHARS]


def _ocr_available() -> bool:
    return bool(os.getenv("KB_ENABLE_OCR") == "1" and shutil.which("tesseract"))


def _ocr_scanned_pdf(path: Path, max_pages: int = OCR_MAX_PAGES) -> str:
    """扫描版 PDF 兜底 OCR：需 tesseract 二进制 + pymupdf/pytesseract/Pillow。
    未安装或未开启（KB_ENABLE_OCR=1）时静默跳过。"""
    try:
        import fitz  # pymupdf
        import pytesseract
        from PIL import Image
    except ImportError:
        logger.info("OCR 依赖未安装（pymupdf/pytesseract/Pillow），跳过扫描版 OCR: %s", path)
        return ""
    if not _ocr_available():
        return ""
    try:
        doc = fitz.open(str(path))
        parts: list[str] = []
        for i in range(min(doc.page_count, max_pages)):
            pix = doc[i].get_pixmap(dpi=200)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            text = pytesseract.image_to_string(img, lang=os.getenv("KB_OCR_LANG", "chi_sim+eng"))
            text = re.sub(r"\s+", " ", text or "").strip()
            if text:
                parts.append(text)
            if len("".join(parts)) >= MAX_CHARS:
                break
        doc.close()
        return "\n\n".join(parts)[:MAX_CHARS]
    except Exception as exc:
        logger.warning("OCR 失败 %s: %s", path, exc, exc_info=True)
        return ""


def _extract_epub(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        try:
            container = ET.fromstring(zf.read("META-INF/container.xml").decode("utf-8", "ignore"))
        except (KeyError, ET.ParseError):
            return ""
        opf_path = None
        for rootfile in container.iter():
            if _localname(rootfile.tag) == "rootfile":
                opf_path = rootfile.get("full-path")
                break
        if not opf_path:
            return ""
        try:
            opf = ET.fromstring(zf.read(opf_path).decode("utf-8", "ignore"))
        except (KeyError, ET.ParseError):
            return ""

        opf_dir = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
        manifest: dict[str, str] = {}
        for item in opf.iter():
            if _localname(item.tag) != "item":
                continue
            item_id = item.get("id")
            href = item.get("href")
            if item_id and href:
                manifest[item_id] = href
        spine_ids = [
            r.get("idref")
            for r in opf.iter()
            if _localname(r.tag) == "itemref" and r.get("idref")
        ]

        parts: list[str] = []
        total = 0
        for spine_id in spine_ids:
            href = manifest.get(spine_id)
            if not href:
                continue
            file_path = href.lstrip("/") if href.startswith("/") else opf_dir + href
            try:
                raw = zf.read(file_path)
            except KeyError:
                continue
            try:
                tree = lxml_html.fromstring(raw.decode("utf-8", "ignore"))
            except Exception:
                continue
            text = re.sub(r"\s+", " ", tree.text_content() or "").strip()
            if text:
                parts.append(text)
                total += len(text)
                if total >= MAX_CHARS:
                    break
        return "\n\n".join(parts)[:MAX_CHARS]


def _extract(fmt: str, path: Path) -> str:
    if fmt == "pdf":
        return _extract_pdf(path, ocr=_ocr_available())
    if fmt == "epub":
        return _extract_epub(path)
    return _read_text(path)


def _extract_html_bytes(path: Path) -> str:
    raw = path.read_bytes()
    try:
        tree = lxml_html.fromstring(raw.decode("utf-8", "ignore"))
    except Exception:
        return ""
    return re.sub(r"\s+", " ", tree.text_content() or "").strip()[:MAX_CHARS]


def extract_text_from_file(fmt: str, path: Path) -> str:
    """提取正文。每个文件独立线程 + 时间预算，超时跳过；
    不等待被放弃的线程（shutdown(wait=False)），避免慢文件阻塞后续文件。

    PDF 文件先做内容嗅探：头部不是 %PDF 的「假 PDF」（如误存为 .pdf 的网页）
    按 HTML / 纯文本提取，可恢复大量本无法检索的文档。
    """
    if not path.is_file():
        return ""
    sniff = _head(path, 2048)
    if fmt == "pdf":
        if not sniff.startswith(b"%PDF"):
            if _looks_like_html(sniff):
                logger.info("假 PDF（实为 HTML）按 HTML 提取: %s", path)
                return _bounded(lambda: _extract_html_bytes(path))
            logger.info("假 PDF（头部不是 PDF 标记）按文本提取: %s", path)
            return _bounded(lambda: _read_text(path))
        if os.getenv("KB_ENABLE_OCR") == "1" and _ocr_available():
            return _bounded(lambda: _extract_pdf(path, ocr=True), timeout=OCR_TIMEOUT)
    return _bounded(lambda: _extract(fmt, path))
