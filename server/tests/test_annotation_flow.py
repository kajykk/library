"""标注知识流转与在线元数据端点测试：

- POST /annotations/{id}/to-note：引用块 + 批注 + [[书名]] 双链 + 反链联通
- GET  /annotations/review：跨书籍标注流 + since 过滤
- GET  /export/annotations(.tsv)：按书分组 MD zip / TSV
- GET  /documents/metadata/search 与 PATCH /documents/{id}/metadata
"""

import io
import zipfile

API = "/api"

_COUNTER = [0]


def _unique(marker: str = "flow") -> str:
    _COUNTER[0] += 1
    return f"{marker}-{_COUNTER[0]}"


def _txt_file(name=None, content=None):
    name = name or f"{_unique()}.txt"
    if content is None:
        content = f"标注流转测试正文 {_unique()}\n" * 8
    return name, io.BytesIO(content.encode("utf-8"))


def _create_book(client, auth_headers, title=None) -> dict:
    name, fh = _txt_file(f"{title or _unique()}.txt")
    resp = client.post(
        f"{API}/documents/upload", headers=auth_headers, files={"file": (name, fh, "text/plain")}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_annotation(client, auth_headers, doc_id, quote, content="", atype="highlight"):
    resp = client.post(
        f"{API}/annotations",
        headers=auth_headers,
        json={
            "document_id": doc_id,
            "type": atype,
            "anchor": {"chapter": 0, "page": 1},
            "quote": quote,
            "content": content,
            "color": "yellow",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------- 标注 → 笔记 ----------


def test_annotation_to_note_creates_linked_note(client, auth_headers):
    book = _create_book(client, auth_headers, "源书A")
    quote = "这是一段值得记录的原文摘录"
    ann = _create_annotation(client, auth_headers, book["id"], quote, "我的批注")

    resp = client.post(f"{API}/annotations/{ann['id']}/to-note", headers=auth_headers)
    assert resp.status_code == 201, resp.text
    note = resp.json()
    assert note["type"] == "note"
    assert "这是一段值得记录的原文摘录" in note["content"]
    assert "我的批注" in note["content"]
    assert f"[[{book['title']}]]" in note["content"]
    assert "章节 0" in note["content"]
    assert note["meta"]["annotation_id"] == ann["id"]
    assert note["meta"]["source_document_id"] == book["id"]
    assert note["meta"]["workflow"] == "done"
    assert note["title"].startswith("《")

    # 反向链接：源书应看到这条笔记
    backlinks = client.get(f"{API}/documents/{book['id']}/backlinks", headers=auth_headers).json()
    assert any(b["id"] == note["id"] for b in backlinks)

    # 图中有 note -> book 的 mention 边
    graph = client.get(f"{API}/links/graph", headers=auth_headers).json()
    assert any(e["source"] == note["id"] and e["target"] == book["id"] and e["type"] == "mention" for e in graph["edges"])


def test_annotation_to_note_custom_title_and_note_without_quote(client, auth_headers):
    book = _create_book(client, auth_headers, "源书B")
    ann = _create_annotation(client, auth_headers, book["id"], "", "纯批注内容", atype="note")

    resp = client.post(
        f"{API}/annotations/{ann['id']}/to-note",
        headers=auth_headers,
        json={"title": "自定义标题"},
    )
    assert resp.status_code == 201, resp.text
    note = resp.json()
    assert note["title"] == "自定义标题"
    assert "纯批注内容" in note["content"]
    assert f"[[{book['title']}]]" in note["content"]


def test_annotation_to_note_unknown_annotation_404(client, auth_headers):
    resp = client.post(
        f"{API}/annotations/00000000-0000-0000-0000-000000000000/to-note", headers=auth_headers
    )
    assert resp.status_code == 404


def test_annotation_to_note_bookmark_without_body(client, auth_headers):
    """纯书签（无原文无批注）转笔记：标题为《书》书签，内容仅含来源双链"""
    book = _create_book(client, auth_headers, "书签书")
    ann = _create_annotation(client, auth_headers, book["id"], "", "", atype="bookmark")

    resp = client.post(f"{API}/annotations/{ann['id']}/to-note", headers=auth_headers)
    assert resp.status_code == 201, resp.text
    note = resp.json()
    assert note["title"] == f"《{book['title']}》书签"
    assert f"[[{book['title']}]]" in note["content"]


# ---------- 每日回顾 ----------


def test_review_annotations_stream_and_since_filter(client, auth_headers):
    book_a = _create_book(client, auth_headers, "回顾书A")
    book_b = _create_book(client, auth_headers, "回顾书B")
    _create_annotation(client, auth_headers, book_a["id"], "回顾A划线", "A批注")
    _create_annotation(client, auth_headers, book_b["id"], "回顾B划线", "", atype="bookmark")

    resp = client.get(f"{API}/annotations/review", headers=auth_headers)
    assert resp.status_code == 200
    hits = resp.json()
    assert len(hits) >= 2
    titles = {h["document_title"] for h in hits}
    assert book_a["title"] in titles and book_b["title"] in titles

    # type 过滤
    only_bookmark = client.get(
        f"{API}/annotations/review?type=bookmark", headers=auth_headers
    ).json()
    assert all(h["type"] == "bookmark" for h in only_bookmark)
    assert any(h["document_title"] == book_b["title"] for h in only_bookmark)

    # since 过滤（未来时间 → 空）
    future = client.get(
        f"{API}/annotations/review?since=2999-01-01T00:00:00Z", headers=auth_headers
    ).json()
    assert future == []


# ---------- 标注导出 ----------


def test_export_annotations_zip_grouped_by_book(client, auth_headers):
    book = _create_book(client, auth_headers, "导出书")
    _create_annotation(client, auth_headers, book["id"], "导出划线内容", "导出批注")

    resp = client.get(f"{API}/export/annotations", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        assert any(".md" in n and n != "index.md" for n in names)
        md = "\n".join(zf.read(n).decode("utf-8") for n in names)
        assert "导出划线内容" in md
        assert "导出批注" in md
        assert book["title"] in md


def test_export_annotations_tsv(client, auth_headers):
    book = _create_book(client, auth_headers, "TSV书")
    _create_annotation(client, auth_headers, book["id"], "TSV划线", "TSV批注")

    resp = client.get(f"{API}/export/annotations.tsv", headers=auth_headers)
    assert resp.status_code == 200
    assert "text/tab-separated-values" in resp.headers["content-type"]
    body = resp.content.decode("utf-8-sig")
    assert body.startswith("front\tback\ttype\tbook")
    assert "TSV划线" in body
    assert "TSV批注" in body
    assert book["title"] in body


# ---------- 在线元数据 ----------


def test_metadata_search_and_apply(client, auth_headers, monkeypatch):
    import app.services.metadata_fetch as meta_mod

    def fake_search(query, limit=6):
        return [
            {
                "source": "google_books",
                "title": "在线书名",
                "author": "在线作者",
                "publisher": "在线出版社",
                "isbn": "9787111000000",
                "description": "在线简介",
                "language": "zh",
                "year": "2024",
                "cover_url": None,
            }
        ]

    monkeypatch.setattr(meta_mod, "search_online", fake_search)

    # 搜索
    resp = client.get(f"{API}/documents/metadata/search?q=%E5%9C%A8%E7%BA%BF%E4%B9%A6", headers=auth_headers)
    assert resp.status_code == 200
    candidates = resp.json()
    assert len(candidates) == 1
    assert candidates[0]["title"] == "在线书名"

    # 应用元数据（不含封面）
    book = _create_book(client, auth_headers, "待补全书")
    patch = client.patch(
        f"{API}/documents/{book['id']}/metadata",
        headers=auth_headers,
        json={
            "title": "在线书名",
            "author": "在线作者",
            "publisher": "在线出版社",
            "isbn": "9787111000000",
            "description": "在线简介",
            "language": "zh",
            "cover_url": None,
        },
    )
    assert patch.status_code == 200, patch.text
    doc = patch.json()
    assert doc["title"] == "在线书名"
    assert doc["author"] == "在线作者"
    assert doc["publisher"] == "在线出版社"
    assert doc["isbn"] == "9787111000000"


def test_metadata_apply_with_cover_download(client, auth_headers, monkeypatch):
    import app.services.metadata_fetch as meta_mod

    monkeypatch.setattr(
        meta_mod, "download_cover", lambda url: (b"\xff\xd8\xff\xe0fakejpeg", "jpg")
    )
    book = _create_book(client, auth_headers, "封面书")
    patch = client.patch(
        f"{API}/documents/{book['id']}/metadata",
        headers=auth_headers,
        json={"title": "新标题", "cover_url": "https://example.com/cover.jpg"},
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["cover_path"] is not None
    cover = client.get(f"{API}/documents/{book['id']}/cover", headers=auth_headers)
    assert cover.status_code == 200
    assert cover.content.startswith(b"\xff\xd8")


def test_metadata_search_requires_query(client, auth_headers):
    resp = client.get(f"{API}/documents/metadata/search", headers=auth_headers)
    assert resp.status_code == 422
