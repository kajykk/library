"""在线元数据抓取：Google Books + Open Library（均免 key），对标 BookLore/Calibre。

同步 httpx 实现：调用方（documents 路由）经 run_in_threadpool 下放线程池。
单源失败/超时静默降级（返回空列表），不阻塞编辑主流程。
"""

import logging
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger("kb.metadata_fetch")

TIMEOUT_SECONDS = 8.0

GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
OPEN_LIBRARY_URL = "https://openlibrary.org/search.json"


def search_online(query: str, limit: int = 6) -> list[dict]:
    """查询两个元数据源并归一化合并（单源失败不影响另一源）"""
    results: list[dict] = []
    for fetcher in (_search_google_books, _search_open_library):
        try:
            results.extend(fetcher(query, limit))
        except Exception as exc:  # noqa: BLE001 —— 任一元数据源故障都不应中断搜索
            logger.warning("元数据源查询失败: %s", exc)
    return results


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def _search_google_books(query: str, limit: int) -> list[dict]:
    params = {
        "q": query,
        "maxResults": max(1, min(10, limit)),
        "printType": "books",
    }
    with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True) as client:
        resp = client.get(GOOGLE_BOOKS_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    items: list[dict] = []
    for entry in data.get("items", [])[:limit]:
        vol = entry.get("volumeInfo", {})
        isbns = [
            i.get("identifier", "")
            for i in vol.get("industryIdentifiers", [])
            if i.get("type") in ("ISBN_13", "ISBN_10")
        ]
        image = vol.get("imageLinks", {}).get("thumbnail")
        if image:
            image = image.replace("http://", "https://")
        items.append(
            {
                "source": "google_books",
                "title": _clean(vol.get("title")),
                "author": _clean(", ".join(vol.get("authors", []) or [])),
                "publisher": _clean(vol.get("publisher")),
                "isbn": isbns[0] if isbns else "",
                "description": _clean(vol.get("description")),
                "language": _clean(vol.get("language")),
                "year": _clean(str(vol.get("publishedDate", ""))[:4]),
                "cover_url": image or None,
            }
        )
    return items


def _search_open_library(query: str, limit: int) -> list[dict]:
    params = {
        "q": quote_plus(query),
        "limit": max(1, min(10, limit)),
        "fields": "key,title,author_name,publisher,isbn,cover_i,first_publish_year,language",
    }
    with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True) as client:
        resp = client.get(OPEN_LIBRARY_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    items: list[dict] = []
    for doc in data.get("docs", [])[:limit]:
        cover_i = doc.get("cover_i")
        publishers = doc.get("publisher") or []
        items.append(
            {
                "source": "open_library",
                "title": _clean(doc.get("title")),
                "author": _clean(", ".join((doc.get("author_name") or [])[:3])),
                "publisher": _clean(", ".join(publishers[:2])) if publishers else "",
                "isbn": (doc.get("isbn") or [""])[0],
                "description": "",
                "language": (doc.get("language") or [""])[0],
                "year": _clean(str(doc.get("first_publish_year") or "")),
                "cover_url": f"https://covers.openlibrary.org/b/id/{cover_i}-L.jpg" if cover_i else None,
            }
        )
    return items


def download_cover(url: str) -> tuple[bytes, str]:
    """下载封面图片，返回 (bytes, ext)。SSRF 校验由调用方完成。"""
    with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        data = resp.content

    if not content_type.startswith("image/"):
        raise ValueError(f"Unexpected content type: {content_type}")
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("Cover image exceeds 8 MB limit")
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(content_type, "jpg")
    return data, ext
