"""网页剪藏：抓取 URL 并用 readability 提取正文，存为 webclip 文档"""

import re
import uuid

from fastapi import APIRouter, Depends
from lxml import html as lxml_html
from pydantic import BaseModel
from readability import Document as ReadabilityDoc
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Document
from ..services import file_storage, url_safety
from ..services.tags import get_or_create_tag
from .documents import sync_mention_links

router = APIRouter(prefix="/clip", tags=["clip"])

UA = "Mozilla/5.0 (compatible; PersonalKnowledgeBase/0.1)"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


class ClipRequest(BaseModel):
    url: str
    title: str | None = None
    tags: list[str] = []


class ClipResult(BaseModel):
    id: str
    title: str
    url: str
    excerpt: str


def _fetch_page_html(url: str) -> tuple[str, str]:
    """抓取页面正文：SSRF 逐跳校验与体积上限统一由 url_safety.fetch_with_guard 负责。

    返回 (page_html, final_url)。
    """
    resp = url_safety.fetch_with_guard(
        url, max_bytes=MAX_RESPONSE_BYTES, headers={"User-Agent": UA}
    )
    charset = resp.charset or "utf-8"
    try:
        return resp.body.decode(charset, errors="replace"), resp.final_url
    except LookupError:  # 响应声明了未知编码（如 x-mac-cyrillic）
        return resp.body.decode("utf-8", errors="replace"), resp.final_url


@router.post("", response_model=ClipResult, status_code=201)
def create_clip(payload: ClipRequest, db: Session = Depends(get_db)):
    # 限流由全局 RateLimitMiddleware 按路径处理（/api/clip 10/60s），此处不重复计
    page_html, final_url = _fetch_page_html(payload.url)

    parsed = ReadabilityDoc(page_html)
    title = payload.title or parsed.short_title() or payload.url
    summary_html = parsed.summary(html_partial=True)

    tree = lxml_html.fragment_fromstring(summary_html, create_parent="div")
    text = re.sub(r"\s{2,}", " ", tree.text_content() or "").strip()

    doc_id = uuid.uuid4()
    rel_path = file_storage.save_file(doc_id, "html", page_html.encode("utf-8"))

    doc = Document(
        id=doc_id,
        type="webclip",
        title=title,
        source_url=payload.url,
        format="html",
        content=text[:100_000],
        file_path=rel_path,
        file_size=len(page_html.encode("utf-8")),
        meta={"excerpt": text[:200], "final_url": final_url},
    )
    db.add(doc)

    if payload.tags:
        db.flush()
        for name in payload.tags:
            doc.tags.append(get_or_create_tag(db, name))

    sync_mention_links(db, doc)
    db.commit()

    return ClipResult(id=str(doc.id), title=doc.title, url=payload.url, excerpt=text[:200])
