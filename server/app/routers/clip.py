"""网页剪藏：抓取 URL 并用 readability 提取正文，存为 webclip 文档"""

import re
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from lxml import html as lxml_html
from pydantic import BaseModel
from readability import Document as ReadabilityDoc
from sqlalchemy.orm import Session

from ..database import get_db
from ..middleware.rate_limit import enforce_rate_limit
from ..models import Document
from ..services import file_storage, url_safety
from .documents import _get_or_create_tag, sync_mention_links

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


@router.post("", response_model=ClipResult, status_code=201)
def create_clip(payload: ClipRequest, request: Request, db: Session = Depends(get_db)):
    enforce_rate_limit(request, max_requests=10, window_seconds=60)
    url_safety.validate_url(payload.url)

    try:
        resp = httpx.get(
            payload.url,
            timeout=httpx.Timeout(10.0),
            follow_redirects=True,
            headers={"User-Agent": UA},
        )
        resp.raise_for_status()
        page_html = resp.text
        if len(page_html.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise HTTPException(status_code=413, detail="Remote response exceeds 5 MB limit")
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"抓取失败：{exc}")

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
        meta={"excerpt": text[:200]},
    )
    db.add(doc)

    if payload.tags:
        db.flush()
        for name in payload.tags:
            doc.tags.append(_get_or_create_tag(db, name))

    sync_mention_links(db, doc)
    db.commit()

    return ClipResult(id=str(doc.id), title=doc.title, url=payload.url, excerpt=text[:200])
