"""扫描件 OCR 分类：RapidOCR(ONNX，无需 tesseract) 采样渲染页识别文本

仅被上传后的后台分类任务调用；依赖缺失时静默返回空串，不影响主流程。
采样策略与宿主机脚本 server/reclassify_ocr.py 一致：前 10 页 + 每 15 页一页。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger("kb.ocr_classify")

_WS_RE = re.compile(r"\s+")

OCR_MAX_PAGES = 40


def ocr_book_text(path: Path, fmt: str, max_pages: int = OCR_MAX_PAGES) -> str:
    """渲染 PDF/EPUB 采样页并 OCR，返回识别文本；失败返回空串"""
    try:
        import fitz  # pymupdf
        from rapidocr_onnxruntime import RapidOCR
        from PIL import Image
        import io
    except ImportError:
        logger.info("OCR 依赖未安装（pymupdf/rapidocr-onnxruntime/Pillow），跳过 OCR 分类: %s", path)
        return ""

    parts: list[str] = []
    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        logger.warning("OCR 打开失败 %s: %s", path, exc)
        return ""
    try:
        n = doc.page_count
        idx = list(range(min(10, n))) + list(range(10, n, 15))
        idx = idx[:max_pages]
        ocr = RapidOCR()
        for i in idx:
            try:
                pix = doc[i].get_pixmap(dpi=150)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                import numpy as np
                result, _ = ocr(np.array(img.convert("RGB")))
                if result:
                    parts.append(" ".join(line[1] for line in result))
            except Exception:
                continue
    finally:
        doc.close()
    text = _WS_RE.sub(" ", " ".join(parts)).strip()
    return text