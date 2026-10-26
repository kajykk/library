"""电子书正文提取（EPUB / PDF / TXT / MD / HTML），存入 content 供全文检索索引"""

from __future__ import annotations

import concurrent.futures
import logging
import multiprocessing
import os
import re
import shutil
import time
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

TEXT_FORMATS = {"epub", "pdf", "txt", "md", "html", "mobi", "azw", "azw3"}

# 共享有界线程池：此前每次 _bounded 新建单线程 executor，超时被遗弃的任务
# （如 90s OCR）会随调用次数累积出大量游离线程。共享池复用线程并限制并发。
#
# 快/慢通道**分池**：此前两者共用一个 2 worker 的池，一个 90s 的 OCR 或两个卡住的
# PDF 解析就会占满全部 worker，此后所有普通提取（含上传主流程）排队等待 —— 超时
# 只是让调用方不再等，线程仍被占用。分池后慢任务无法饿死快任务。
_FAST_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="kb-extract"
)
_SLOW_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="kb-extract-slow"
)

# 可选的 PDF 进程隔离（KB_EXTRACT_PROCESS_ISOLATION=1）：线程无法取消，
# 卡死的 PDF 解析会永久占住 worker；进程池超时后重建，避免后续任务排队。
# spawn 必选：Windows 下 fork 不安全，且应用本身是多线程的。
_PROCESS_POOL: concurrent.futures.ProcessPoolExecutor | None = None

# 连续超时熔断：连续 N 次超时后该通道短期停用，让被放弃但仍占着 worker 的
# 任务有机会排空。冷却后放行一次探测；成功即清零，避免通道被永久禁用
# （旧实现按队列深度直接跳过，持续调用会让后续文件全部返回空）。
_CIRCUIT_BREAKER_THRESHOLD = 5
_CIRCUIT_BREAKER_COOLDOWN = 60.0
_consecutive_timeouts = {"fast": 0, "slow": 0}
_circuit_open_until = {"fast": 0.0, "slow": 0.0}


def _bounded(fn, timeout: float = EXTRACT_TIMEOUT, slow: bool = False) -> str:
    """共享线程池 + 时间预算执行提取；超时放弃该 future（任务线程自然结束），不阻塞后续文件。

    slow=True 走独立慢池（OCR / 大 PDF），与快通道互不抢占。
    """
    pool = _SLOW_POOL if slow else _FAST_POOL
    channel = "slow" if slow else "fast"

    now = time.monotonic()
    if _consecutive_timeouts[channel] >= _CIRCUIT_BREAKER_THRESHOLD:
        if now < _circuit_open_until[channel]:
            logger.warning(
                "提取熔断器触发（%s 通道连续 %s 次超时），跳过本次提取",
                channel,
                _consecutive_timeouts[channel],
            )
            return ""
        # 冷却结束：放行一次探测；探测仍超时会重新打开熔断
        _circuit_open_until[channel] = now + _CIRCUIT_BREAKER_COOLDOWN

    future = pool.submit(fn)
    try:
        result = future.result(timeout=timeout)
        _consecutive_timeouts[channel] = 0
        _circuit_open_until[channel] = 0.0
        return result
    except concurrent.futures.TimeoutError:
        logger.warning("正文提取超时（>%ss），已跳过", timeout)
        _consecutive_timeouts[channel] += 1
        if _consecutive_timeouts[channel] >= _CIRCUIT_BREAKER_THRESHOLD:
            _circuit_open_until[channel] = time.monotonic() + _CIRCUIT_BREAKER_COOLDOWN
        return ""
    except Exception as exc:
        logger.warning("正文提取失败: %s", exc, exc_info=True)
        _consecutive_timeouts[channel] = 0
        _circuit_open_until[channel] = 0.0
        return ""


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
                # 必须传 bytes：xhtml 带 <?xml encoding?> 声明时 lxml 拒绝 str 输入
                tree = lxml_html.fromstring(raw)
            except Exception:
                continue
            text = re.sub(r"\s+", " ", tree.text_content() or "").strip()
            if text:
                parts.append(text)
                total += len(text)
                if total >= MAX_CHARS:
                    break
        return "\n\n".join(parts)[:MAX_CHARS]


def _extract_mobi(path: Path) -> str:
    """MOBI/AZW 解包：mobi 库解出 html 后按 HTML 提取（Huffman/PalmDOC 压缩均支持）"""
    try:
        import mobi
    except ImportError:
        logger.info("MOBI 依赖未安装（pip install mobi），跳过: %s", path)
        return ""
    try:
        tmpdir, filepath = mobi.extract(str(path))
    except Exception as exc:
        logger.warning("MOBI 解包失败 %s: %s", path, exc)
        return ""
    try:
        out = Path(filepath)
        if out.suffix.lower() == ".epub":
            # mobi8/azw3 解出的是 EPUB 容器，按 EPUB 提取
            return _extract_epub(out)
        return _extract_html_bytes(out)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _extract(fmt: str, path: Path) -> str:
    if fmt == "pdf":
        return _extract_pdf(path, ocr=_ocr_available())
    if fmt == "epub":
        return _extract_epub(path)
    if fmt in ("mobi", "azw", "azw3"):
        return _extract_mobi(path)
    return _read_text(path)


def _extract_html_bytes(path: Path) -> str:
    raw = path.read_bytes()
    try:
        tree = lxml_html.fromstring(raw.decode("utf-8", "ignore"))
    except Exception:
        return ""
    return re.sub(r"\s+", " ", tree.text_content() or "").strip()[:MAX_CHARS]


def _get_process_pool() -> concurrent.futures.ProcessPoolExecutor:
    global _PROCESS_POOL
    if _PROCESS_POOL is None:
        _PROCESS_POOL = concurrent.futures.ProcessPoolExecutor(
            max_workers=1,
            mp_context=multiprocessing.get_context("spawn"),
        )
    return _PROCESS_POOL


def _reset_process_pool() -> None:
    global _PROCESS_POOL
    if _PROCESS_POOL is not None:
        _PROCESS_POOL.shutdown(wait=False, cancel_futures=True)
        _PROCESS_POOL = None


def _pdf_extract_worker(path_str: str, ocr: bool) -> str:
    """进程池入口：必须是模块级函数（可 pickle）。"""
    return _extract_pdf(Path(path_str), ocr=ocr)


def _extract_pdf_isolated(path: Path, ocr: bool, timeout_seconds: float) -> str:
    try:
        return _get_process_pool().submit(
            _pdf_extract_worker, str(path), ocr
        ).result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError:
        logger.warning("PDF 进程池提取超时（>%ss），已终止并跳过", timeout_seconds)
        _reset_process_pool()
        return ""
    except Exception as exc:
        logger.warning("PDF 进程池提取失败: %s", exc)
        return ""


def extract_text_from_file(fmt: str, path: Path) -> str:
    """提取正文。每个文件独立线程 + 时间预算，超时跳过；
    不等待被放弃的线程（shutdown(wait=False)），避免慢文件阻塞后续文件。

    PDF 文件先做内容嗅探：头部不是 %PDF 的「假 PDF」（如误存为 .pdf 的网页）
    按 HTML / 纯文本提取，可恢复大量本无法检索的文档。
    设置 KB_EXTRACT_PROCESS_ISOLATION=1 时，真实 PDF 改走单进程池（spawn）。

    公共签名保持不变；需要自定义预算时用 extract_text_from_file_with_budget。
    """
    return _extract_with_budget(fmt, path, timeout_seconds=None)


def extract_text_from_file_with_budget(
    fmt: str, path: Path, timeout_seconds: float = EXTRACT_TIMEOUT
) -> str:
    """带显式时间预算的提取入口（RW-401），行为与 extract_text_from_file 一致，
    但每个分支（含 OCR/进程隔离）统一使用 timeout_seconds 作为单文件上限。"""
    return _extract_with_budget(fmt, path, timeout_seconds=max(0.1, float(timeout_seconds)))


def _extract_with_budget(
    fmt: str, path: Path, timeout_seconds: float | None
) -> str:
    """timeout_seconds=None 时沿用各分支的历史默认预算，保证旧调用行为不变。"""

    def budget(default: float) -> float:
        return default if timeout_seconds is None else timeout_seconds

    if not path.is_file():
        return ""
    sniff = _head(path, 2048)
    if fmt == "pdf":
        if not sniff.startswith(b"%PDF"):
            if _looks_like_html(sniff):
                logger.info("假 PDF（实为 HTML）按 HTML 提取: %s", path)
                return _bounded(
                    lambda: _extract_html_bytes(path), timeout=budget(EXTRACT_TIMEOUT)
                )
            logger.info("假 PDF（头部不是 PDF 标记）按文本提取: %s", path)
            return _bounded(
                lambda: _read_text(path), timeout=budget(EXTRACT_TIMEOUT)
            )
        if os.getenv("KB_EXTRACT_PROCESS_ISOLATION", "0") == "1":
            ocr = _ocr_available()
            return _extract_pdf_isolated(
                path, ocr=ocr, timeout_seconds=budget(OCR_TIMEOUT if ocr else EXTRACT_TIMEOUT)
            )
        if os.getenv("KB_ENABLE_OCR") == "1" and _ocr_available():
            # OCR 走慢池：90s 预算的任务不能再占用快通道的 worker
            return _bounded(
                lambda: _extract_pdf(path, ocr=True),
                timeout=budget(OCR_TIMEOUT),
                slow=True,
            )
    return _bounded(lambda: _extract(fmt, path), timeout=budget(EXTRACT_TIMEOUT))
