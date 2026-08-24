"""API 集成测试：认证、上传/Range、CRUD、软删除、标签、图谱、迁移"""

import io
import uuid
import zipfile

API = "/api"

_COUNTER = [0]


def _txt_file(name="测试书.txt", content=None):
    """默认生成唯一内容，避免 SHA-256 去重误伤跨测试重复上传"""
    _COUNTER[0] += 1
    if content is None:
        content = f"hello knowledge base #{_COUNTER[0]}\n" * 10
    return name, io.BytesIO(content.encode("utf-8"))


def _unique_marker():
    _COUNTER[0] += 1
    return f"test #{_COUNTER[0]}"


def _epub_bytes(title: str = "EPUB 测试书", author: str = "张三") -> bytes:
    """构造最小合法 EPUB（zip + container.xml + OPF）"""
    opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" xmlns:opf="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <!-- {marker} -->
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title>
    <dc:creator>{author}</dc:creator>
    <dc:language>zh</dc:language>
    <dc:identifier opf:scheme="ISBN">9787111000000</dc:identifier>
  </metadata>
  <manifest>
    <item id="cover" href="cover.jpg" media-type="image/jpeg"/>
  </manifest>
  <spine/>
</package>""".format(marker=_unique_marker(), title=title, author=author)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/cover.jpg", b"\xff\xd8\xff\xe0fakejpgdata")
    return buf.getvalue()


def _epub_with_chapter_bytes(
    title: str = "正文书",
    chapter_text: str = "认知负荷理论正文内容",
    xml_decl: str = '<?xml version="1.0"?>',
) -> bytes:
    opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <!-- {marker} -->
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title>
  </metadata>
  <manifest>
    <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="ch1"/></spine>
</package>""".format(marker=_unique_marker(), title=title)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr(
            "OEBPS/chapter1.xhtml",
            f'{xml_decl}<html xmlns="http://www.w3.org/1999/xhtml">'
            f"<body><h1>第一章</h1><p>{chapter_text}</p></body></html>",
        )
    return buf.getvalue()


# ---------- 系统 ----------

def test_health_no_auth(client):
    resp = client.get(f"{API}/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # 自检项：数据库 / FTS 索引 / 数据目录 / 协作目录
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["fulltext_index"] == "ok"
    assert body["checks"]["data_dir"] == "ok"
    assert body["checks"]["collab_dir"] == "ok"


def test_requires_token(client):
    assert client.get(f"{API}/documents").status_code == 401
    assert client.get(f"{API}/documents", headers={"X-API-Token": "wrong"}).status_code == 401


# ---------- 上传 / 元数据 / 文件流 ----------

def test_upload_txt_and_range(client, auth_headers):
    name, fh = _txt_file()
    resp = client.post(
        f"{API}/documents/upload", headers=auth_headers, files={"file": (name, fh, "text/plain")}
    )
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    assert doc["title"] == "测试书"
    assert doc["type"] == "book"
    assert doc["format"] == "txt"
    assert doc["file_size"] > 0

    # 全量下载
    full = client.get(f"{API}/documents/{doc['id']}/file", headers=auth_headers)
    assert full.status_code == 200
    assert len(full.content) == doc["file_size"]
    assert full.headers["accept-ranges"] == "bytes"

    # Range 分段
    partial = client.get(
        f"{API}/documents/{doc['id']}/file", headers={**auth_headers, "Range": "bytes=0-4"}
    )
    assert partial.status_code == 206
    assert partial.content == b"hello"
    assert partial.headers["content-range"] == f"bytes 0-4/{doc['file_size']}"
    assert partial.headers["content-length"] == "5"

    # 越界 Range → 416
    bad = client.get(
        f"{API}/documents/{doc['id']}/file", headers={**auth_headers, "Range": f"bytes=999999-"}
    )
    assert bad.status_code == 416


def test_upload_epub_metadata_and_cover(client, auth_headers):
    data = _epub_bytes()
    resp = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": ("我的EPUB.epub", io.BytesIO(data), "application/epub+zip")},
    )
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    assert doc["title"] == "EPUB 测试书"
    assert doc["author"] == "张三"
    assert doc["isbn"] == "9787111000000"
    assert doc["type"] == "book" and doc["format"] == "epub"

    cover = client.get(f"{API}/documents/{doc['id']}/cover", headers=auth_headers)
    assert cover.status_code == 200
    assert cover.content.startswith(b"\xff\xd8")


# ---------- 导入自动分类（标题 → 正文词频 → OCR 后台） ----------

def _ensure_collection(client, auth_headers, name):
    r = client.post(f"{API}/collections", headers=auth_headers, json={"name": name})
    if r.status_code == 201:
        return r.json()["id"]
    if r.status_code == 409:
        return next(c["id"] for c in client.get(f"{API}/collections", headers=auth_headers).json() if c["name"] == name)
    raise AssertionError(f"create collection failed: {r.status_code}")


def test_upload_auto_classify_title_and_content(client, auth_headers):
    psy_id = _ensure_collection(client, auth_headers, "心理学")

    # 第一层：标题关键词命中
    name, fh = _txt_file("心理学入门笔记.txt")
    r1 = client.post(
        f"{API}/documents/upload", headers=auth_headers, files={"file": (name, fh, "text/plain")}
    )
    assert r1.status_code == 201, r1.text
    assert r1.json()["collection_id"] == psy_id

    # 第二层：标题无关键词、正文词频命中（≥阈值）
    body = "情绪管理 焦虑 正念 冥想 认知行为 潜意识 " * 10
    name2, fh2 = _txt_file("无名之书.txt", body)
    r2 = client.post(
        f"{API}/documents/upload", headers=auth_headers, files={"file": (name2, fh2, "text/plain")}
    )
    assert r2.status_code == 201, r2.text
    assert r2.json()["collection_id"] == psy_id

    # 两层都未命中 → 不归类
    name3, fh3 = _txt_file("无主题之书.txt", "这是完全没有主题特征词汇的普通内容 " * 5)
    r3 = client.post(
        f"{API}/documents/upload", headers=auth_headers, files={"file": (name3, fh3, "text/plain")}
    )
    assert r3.status_code == 201, r3.text
    assert r3.json()["collection_id"] is None


def test_upload_auto_classify_ocr_background(client, auth_headers, monkeypatch):
    from pypdf import PdfWriter

    import app.routers.documents as docmod
    import app.services.ocr_classify as ocr_mod

    psy_id = _ensure_collection(client, auth_headers, "心理学")

    monkeypatch.setattr(docmod.settings, "auto_classify_ocr", True)
    monkeypatch.setattr(
        ocr_mod, "ocr_book_text",
        lambda path, fmt: "情绪 焦虑 正念 冥想 认知行为 潜意识 心理 人格 " * 30,
    )

    def _blank_pdf(marker: str):
        w = PdfWriter()
        w.add_blank_page(width=200, height=200)
        w.add_metadata({"/Title": marker})
        buf = io.BytesIO()
        w.write(buf)
        return buf.getvalue()

    # 扫描件（无文本层）→ 后台 OCR 归类
    r = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": ("扫描版.pdf", io.BytesIO(_blank_pdf("scan-1")), "application/pdf")},
    )
    assert r.status_code == 201, r.text
    assert r.json()["collection_id"] is None  # 响应先返回，后台任务随后写入
    after = client.get(f"{API}/documents/{r.json()['id']}", headers=auth_headers).json()
    assert after["collection_id"] == psy_id

    # OCR 文本无主题特征 → 保持未归类
    monkeypatch.setattr(ocr_mod, "ocr_book_text", lambda path, fmt: "仅有零散图文没有主题词汇 ")
    r2 = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": ("扫描版2.pdf", io.BytesIO(_blank_pdf("scan-2")), "application/pdf")},
    )
    assert r2.status_code == 201, r2.text
    after2 = client.get(f"{API}/documents/{r2.json()['id']}", headers=auth_headers).json()
    assert after2["collection_id"] is None


def test_epub_xml_encoding_declaration_extraction(client, auth_headers):
    # xhtml 带 <?xml encoding?> 声明时 lxml 拒绝 str 输入，必须传 bytes 提取
    buf = _epub_with_chapter_bytes(
        "带编码声明书", "海德格尔的存在主义与形而上学思想", '<?xml version="1.0" encoding="utf-8"?>'
    )
    r = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": ("带编码声明书.epub", io.BytesIO(buf), "application/epub+zip")},
    )
    assert r.status_code == 201, r.text
    got = client.get(f"{API}/documents/{r.json()['id']}", headers=auth_headers).json()
    assert "海德格尔" in (got.get("content") or "")


def test_auto_classify_series_name_no_false_positive(client, auth_headers):
    # 书名含"人文与社会译丛"（丛书名）不应因"社会"一词误入经济政治
    name, fh = _txt_file("技术与时间（修订合卷本）（人文与社会译丛）.txt", "无主题的普通内容文字 " * 5)
    r = client.post(
        f"{API}/documents/upload", headers=auth_headers, files={"file": (name, fh, "text/plain")}
    )
    assert r.status_code == 201, r.text
    assert r.json()["collection_id"] is None


def test_auto_classify_philosophy_content(client, auth_headers):
    # 正文主题词频命中 → 归入哲学
    phi_id = _ensure_collection(client, auth_headers, "哲学")
    buf = _epub_with_chapter_bytes(
        "技术与时间（人文与社会译丛）", "存在主义 形而上学 海德格尔 哲学 思想 自由 " * 10
    )
    r = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": ("哲学书.epub", io.BytesIO(buf), "application/epub+zip")},
    )
    assert r.status_code == 201, r.text
    assert r.json()["collection_id"] == phi_id


def test_upload_mobi_auto_classify(client, auth_headers, monkeypatch):
    # mobi 属于 TEXT_FORMATS：解包出正文后走正文词频分类
    lit_id = _ensure_collection(client, auth_headers, "文学小说")

    import app.services.content_extract as ce

    monkeypatch.setattr(
        ce, "_extract_mobi",
        lambda path: "<html><body>陀思妥耶夫斯基的小说 小说 小说 小说 文学 " * 20 + "</body></html>",
    )
    r = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": ("赌徒.mobi", io.BytesIO(b"\x00fake-mobi"), "application/x-mobipocket-ebook")},
    )
    assert r.status_code == 201, r.text
    assert r.json()["format"] == "mobi"
    assert r.json()["collection_id"] == lit_id
    got = client.get(f"{API}/documents/{r.json()['id']}", headers=auth_headers).json()
    assert "陀思妥耶夫斯基" in (got.get("content") or "")


def test_upload_azw3_extract_and_classify(client, auth_headers, monkeypatch):
    # azw3(KF8) 必须走 _extract_mobi（解出的是 EPUB 容器），而非二进制直读
    sci_id = _ensure_collection(client, auth_headers, "科学")

    import app.services.content_extract as ce

    monkeypatch.setattr(
        ce, "_extract_mobi",
        lambda path: "<html><body>量子宇宙 宇宙 宇宙 宇宙 物理学 物理 " * 20 + "</body></html>",
    )
    r = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": ("无中生有的宇宙.azw3", io.BytesIO(b"\x00fake-azw3"), "application/vnd.amazon.ebook")},
    )
    assert r.status_code == 201, r.text
    assert r.json()["format"] == "azw3"
    assert r.json()["collection_id"] == sci_id
    got = client.get(f"{API}/documents/{r.json()['id']}", headers=auth_headers).json()
    assert "量子宇宙" in (got.get("content") or "")


def test_auto_classify_content_margin_guard(client, auth_headers):
    # 最高分与次高分接近（如小说里满是对话/情绪词）→ 拒绝归类，不强行猜测
    name, fh = _txt_file("界限之书.txt", "小说 说话 情绪 小说 聊天 心理 小说 人格 小说 " * 10)
    r = client.post(
        f"{API}/documents/upload", headers=auth_headers, files={"file": (name, fh, "text/plain")}
    )
    assert r.status_code == 201, r.text
    assert r.json()["collection_id"] is None


# ---------- 笔记类文档 / 标签 / 检索 ----------

def test_note_crud_tags_and_search(client, auth_headers):
    resp = client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={"type": "note", "title": "读书笔记A", "content": "关于认知负荷的思考", "tags": ["心理学", "阅读"]},
    )
    assert resp.status_code == 201
    note = resp.json()
    assert sorted(t["name"] for t in note["tags"]) == ["心理学", "阅读"]

    # 标签过滤
    by_tag = client.get(f"{API}/documents", headers=auth_headers, params={"tag": "心理学"})
    assert any(d["id"] == note["id"] for d in by_tag.json())

    # 模糊检索
    by_q = client.get(f"{API}/documents", headers=auth_headers, params={"q": "认知负荷"})
    assert any(d["id"] == note["id"] for d in by_q.json())

    # 更新
    patched = client.patch(
        f"{API}/documents/{note['id']}",
        headers=auth_headers,
        json={
            "content": "更新后的内容",
            "read_progress": 0.5,
            "last_read_at": "2026-08-17T10:00:00+00:00",
        },
    )
    assert patched.status_code == 200
    assert patched.json()["read_progress"] == 0.5
    assert patched.json()["last_read_at"].startswith("2026-08-17T10:00:00")

    # 覆盖标签
    set_tags = client.put(
        f"{API}/documents/{note['id']}/tags", headers=auth_headers, json={"tags": ["新标签"]}
    )
    assert [t["name"] for t in set_tags.json()["tags"]] == ["新标签"]


# ---------- collections ----------

def test_collections_flow(client, auth_headers):
    created = client.post(
        f"{API}/collections", headers=auth_headers, json={"name": "技术类", "sort_order": 1}
    )
    assert created.status_code == 201
    coll = created.json()

    # 重名 → 409
    dup = client.post(f"{API}/collections", headers=auth_headers, json={"name": "技术类"})
    assert dup.status_code == 409

    # 归属文档
    name, fh = _txt_file("归类的书.txt")
    doc = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": (name, fh, "text/plain")},
        data={"collection_id": coll["id"]},
    ).json()
    assert doc["collection_id"] == coll["id"]

    # 删除分类 → 文档 collection_id 置空（SET NULL），文档仍在
    deleted = client.delete(f"{API}/collections/{coll['id']}", headers=auth_headers)
    assert deleted.status_code == 204
    after = client.get(f"{API}/documents/{doc['id']}", headers=auth_headers).json()
    assert after["collection_id"] is None


# ---------- annotations / links / graph ----------

def test_annotations_links_graph(client, auth_headers):
    name, fh = _txt_file("批注用书.txt")
    doc = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": (name, fh, "text/plain")},
    ).json()

    ann = client.post(
        f"{API}/annotations",
        headers=auth_headers,
        json={
            "document_id": doc["id"],
            "type": "highlight",
            "anchor": {"chapter": 2, "text": "关键句子"},
            "quote": "关键句子",
            "content": "这段重要",
            "color": "yellow",
        },
    )
    assert ann.status_code == 201
    ann_id = ann.json()["id"]

    listing = client.get(
        f"{API}/annotations", headers=auth_headers, params={"document_id": doc["id"]}
    )
    assert any(a["id"] == ann_id for a in listing.json())

    patched = client.patch(
        f"{API}/annotations/{ann_id}", headers=auth_headers, json={"content": "改过的笔记"}
    )
    assert patched.json()["content"] == "改过的笔记"

    # 笔记文档 + 双链 + 图谱
    note = client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={"type": "note", "title": "书评"},
    ).json()
    link = client.post(
        f"{API}/links",
        headers=auth_headers,
        json={"source_id": note["id"], "target_id": doc["id"], "type": "cite"},
    )
    assert link.status_code == 201

    graph = client.get(f"{API}/links/graph", headers=auth_headers).json()
    assert any(e["source"] == note["id"] and e["type"] == "cite" for e in graph["edges"])
    assert any(n["id"] == note["id"] for n in graph["nodes"])


# ---------- 软删除 / 硬删除 ----------

def test_soft_and_hard_delete(client, auth_headers):
    name, fh = _txt_file("待删除.txt")
    doc = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": (name, fh, "text/plain")},
    ).json()

    # 软删除：列表不可见，include_deleted 可见
    assert client.delete(f"{API}/documents/{doc['id']}", headers=auth_headers).status_code == 204
    assert client.get(f"{API}/documents/{doc['id']}", headers=auth_headers).status_code == 404
    with_deleted = client.get(
        f"{API}/documents", headers=auth_headers, params={"include_deleted": True}
    ).json()
    assert any(d["id"] == doc["id"] and d["deleted_at"] for d in with_deleted)

    # 硬删除
    assert (
        client.delete(f"{API}/documents/{doc['id']}?hard=true", headers=auth_headers).status_code == 204
    )
    gone = client.get(
        f"{API}/documents", headers=auth_headers, params={"include_deleted": True}
    ).json()
    assert not any(d["id"] == doc["id"] for d in gone)


# ---------- 迁移（IndexedDB → 后端）----------

def test_migrate_meta_and_files(client, auth_headers):
    payload = {
        "categories": [
            {"id": "cat-1", "name": "历史", "parentId": None, "sortOrder": 0},
        ],
        "books": [
            {
                "id": "book-1",
                "title": "万历十五年",
                "author": "黄仁宇",
                "publisher": "三联",
                "category": "历史",
                "format": "txt",
                "addedAt": 1700000000000,
                "lastReadAt": 1700100000000,
                "readProgress": 55,
                "lastPosition": 12,
                "totalPages": 100,
                "fileSize": 1024,
                "tags": ["明史", "经典"],
            },
            {
                "id": "book-2",
                "title": "一份PDF",
                "category": "历史",
                "format": "pdf",
                "readProgress": 0,
            },
        ],
        "bookmarks": [
            {"id": "bm-1", "bookId": "book-1", "page": 12, "note": "第一章", "createdAt": 1700050000000}
        ],
        "notes": [
            {"id": "n-1", "bookId": "book-1", "page": 13, "content": "重要观点", "createdAt": 1700060000000, "updatedAt": 1700060000000}
        ],
        "readingRecords": [
            {"id": "r-1", "bookId": "book-1", "startTime": 1700050000000, "endTime": 1700053000000, "pagesRead": 3}
        ],
    }
    result = client.post(f"{API}/migrate/meta", headers=auth_headers, json=payload)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["collections"] == 1
    assert body["annotations"] == 2
    assert body["reading_records"] == 1
    mapping = {d["old_id"]: d["new_id"] for d in body["documents"]}
    assert set(mapping) == {"book-1", "book-2"}

    doc_id = mapping["book-1"]
    doc = client.get(f"{API}/documents/{doc_id}", headers=auth_headers).json()
    assert doc["title"] == "万历十五年"
    assert doc["read_progress"] == 0.55  # 55% → 0~1
    assert doc["position"] == {"page": 12}
    assert doc["meta"] == {"totalPages": 100}
    assert sorted(t["name"] for t in doc["tags"]) == ["明史", "经典"]
    pdf_doc = client.get(f"{API}/documents/{mapping['book-2']}", headers=auth_headers).json()
    assert pdf_doc["type"] == "pdf"

    # 上传迁移文件 → 可下载
    up = client.post(
        f"{API}/migrate/file",
        headers=auth_headers,
        data={"doc_id": doc_id},
        files={"file": ("whatever.txt", io.BytesIO(("万历十五年正文" * 20).encode("utf-8")), "text/plain")},
    )
    assert up.status_code == 204
    downloaded = client.get(f"{API}/documents/{doc_id}/file", headers=auth_headers)
    assert downloaded.status_code == 200
    assert downloaded.content.decode("utf-8").startswith("万历十五年正文")

    # 封面
    cover_up = client.post(
        f"{API}/migrate/cover",
        headers=auth_headers,
        data={"doc_id": doc_id},
        files={"file": ("cover.jpg", io.BytesIO(b"\xff\xd8\xff\xe0cover"), "image/jpeg")},
    )
    assert cover_up.status_code == 204
    assert client.get(f"{API}/documents/{doc_id}/cover", headers=auth_headers).status_code == 200

    # 迁移产生的批注
    anns = client.get(
        f"{API}/annotations", headers=auth_headers, params={"document_id": doc_id}
    ).json()
    types = sorted(a["type"] for a in anns)
    assert types == ["bookmark", "note"]


# ---------- 检索 / 统计 ----------

def test_search_and_stats(client, auth_headers):
    client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={
            "type": "note",
            "title": "深度工作笔记",
            "content": "认知负荷理论认为工作记忆容量有限，深度工作需要减少干扰。",
            "tags": ["心理学"],
        },
    )

    hits = client.get(f"{API}/search", headers=auth_headers, params={"q": "认知负荷"}).json()
    assert any("深度工作笔记" == h["title"] for h in hits)
    hit = next(h for h in hits if h["title"] == "深度工作笔记")
    assert "认知负荷" in hit["snippet"]

    # type 过滤
    notes_only = client.get(
        f"{API}/search", headers=auth_headers, params={"q": "认知负荷", "type": "note"}
    ).json()
    assert notes_only and all(h["type"] == "note" for h in notes_only)

    # 空查询 → 422
    assert client.get(f"{API}/search", headers=auth_headers, params={"q": ""}).status_code == 422

    stats = client.get(f"{API}/stats", headers=auth_headers).json()
    assert stats["total_documents"] > 0
    assert stats["by_type"].get("note", 0) >= 1
    assert any(t["name"] == "心理学" for t in stats["top_tags"])
    assert stats["total_reading_minutes"] >= 0
    assert isinstance(stats["recent_days"], list)


def test_search_index_sync(client, auth_headers):
    note = client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={"type": "note", "title": "索引同步笔记", "content": "初始内容没有任何特色"},
    ).json()

    def hit_titles(q):
        return [h["title"] for h in client.get(f"{API}/search", headers=auth_headers, params={"q": q}).json()]

    assert "索引同步笔记" not in hit_titles("氟利昂制冷剂")

    # 更新内容后 FTS 索引同步
    client.patch(
        f"{API}/documents/{note['id']}",
        headers=auth_headers,
        json={"content": "讨论氟利昂制冷剂对臭氧层的影响"},
    )
    assert "索引同步笔记" in hit_titles("氟利昂制冷剂")

    # 软删除后不再命中
    client.delete(f"{API}/documents/{note['id']}", headers=auth_headers)
    assert "索引同步笔记" not in hit_titles("氟利昂制冷剂")

    # 短查询走 ILIKE 兜底
    short = client.get(f"{API}/search", headers=auth_headers, params={"q": "氟利昂"}).json()
    assert all("氟利昂" in (h["title"] + h["snippet"]).replace("<mark>", "").replace("</mark>", "") for h in short)


def test_search_two_char_query_ilike_fallback(client, auth_headers):
    # trigram FTS 需要 >= 3 字符，2 字查询必须走 ILIKE 兜底且能命中正文
    client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={"type": "note", "title": "两字查询笔记", "content": "精进思维：持续改善每日习惯"},
    )

    hits = client.get(f"{API}/search", headers=auth_headers, params={"q": "精进"}).json()
    assert any(h["title"] == "两字查询笔记" for h in hits)

    # 多词短查询（每词 2 字）也应命中
    hits2 = client.get(f"{API}/search", headers=auth_headers, params={"q": "持续 改善"}).json()
    assert any(h["title"] == "两字查询笔记" for h in hits2)


# ---------- 版本历史 ----------

def test_document_versions_and_restore(client, auth_headers):
    note = client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={"type": "note", "title": "版本测试笔记", "content": "第一版内容"},
    ).json()
    assert note["id"]

    versions = client.get(f"{API}/documents/{note['id']}/versions", headers=auth_headers).json()
    assert len(versions) == 1
    assert versions[0]["title"] == "版本测试笔记"

    # 立即更新（间隔 < 30s）不产生新版本
    client.patch(
        f"{API}/documents/{note['id']}",
        headers=auth_headers,
        json={"content": "几乎同时的修改"},
    )
    versions = client.get(f"{API}/documents/{note['id']}/versions", headers=auth_headers).json()
    assert len(versions) == 1

    # 恢复到初始版本（当前内容 → 快照 → 恢复 v1）
    v1 = versions[0]["id"]
    restored = client.post(
        f"{API}/documents/{note['id']}/versions/{v1}/restore", headers=auth_headers
    )
    assert restored.status_code == 200
    assert restored.json()["content"] == "第一版内容"

    # 恢复动作本身产生新快照（可撤销）
    versions_after = client.get(f"{API}/documents/{note['id']}/versions", headers=auth_headers).json()
    assert len(versions_after) >= 2

    # 详情接口
    detail = client.get(
        f"{API}/documents/{note['id']}/versions/{v1}", headers=auth_headers
    ).json()
    assert detail["content"] == "第一版内容"

    # 删除文档 → 版本级联清理
    client.delete(f"{API}/documents/{note['id']}", headers=auth_headers)
    assert (
        client.get(f"{API}/documents/{note['id']}/versions", headers=auth_headers).status_code == 404
    )


# ---------- 标注搜索（书籍引用）----------

def test_annotation_search_across_documents(client, auth_headers):
    name, fh = _txt_file("标注源书.txt")
    book = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": (name, fh, "text/plain")},
    ).json()

    client.post(
        f"{API}/annotations",
        headers=auth_headers,
        json={
            "document_id": book["id"],
            "type": "highlight",
            "anchor": {"chapter": 1},
            "quote": "知识就是力量",
            "content": "经典名言",
        },
    )

    hits = client.get(
        f"{API}/annotations/search", headers=auth_headers, params={"q": "力量"}
    ).json()
    assert len(hits) == 1
    assert hits[0]["document_title"] == "标注源书"
    assert hits[0]["quote"] == "知识就是力量"

    no_hit = client.get(
        f"{API}/annotations/search", headers=auth_headers, params={"q": "不存在的内容"}
    ).json()
    assert no_hit == []


# ---------- 健康面板 / 作者聚合 ----------

def test_health_panel_and_authors(client, auth_headers):
    orphan = client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={"type": "note", "title": "孤独的笔记", "content": "没有任何链接"},
    ).json()

    linked_target = client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={"type": "note", "title": "链接目标", "content": "正文"},
    ).json()
    client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={"type": "note", "title": "链接源", "content": "参见[[链接目标]]"},
    )
    client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={"type": "note", "title": "占位源", "content": "参见[[并不存在的书]]"},
    )

    health = client.get(f"{API}/stats/health", headers=auth_headers).json()
    assert any(h["id"] == orphan["id"] for h in health["orphans"])
    assert not any(h["id"] == linked_target["id"] for h in health["orphans"])
    assert any(p["mention"] == "并不存在的书" for p in health["placeholders"])
    assert isinstance(health["unread"], list)

    # 作者聚合
    client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={"type": "note", "title": "作者笔记", "author": "张三"},
    )
    client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={"type": "note", "title": "作者笔记2", "author": "张三"},
    )
    authors = client.get(f"{API}/stats/authors", headers=auth_headers).json()
    assert any(a["name"] == "张三" and a["count"] >= 2 for a in authors)


# ---------- 电子书正文提取 / 全文检索 ----------

def test_epub_content_extraction_and_search(client, auth_headers):
    data = _epub_with_chapter_bytes(chapter_text="氟利昂制冷剂对环境的影响研究")
    resp = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": ("正文提取测试.epub", io.BytesIO(data), "application/epub+zip")},
    )
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    assert "氟利昂制冷剂对环境的影响研究" in doc["content"]

    hits = client.get(f"{API}/search", headers=auth_headers, params={"q": "氟利昂制冷剂"}).json()
    assert any(h["id"] == doc["id"] for h in hits)

    # 覆盖已有书籍正文：先清空再重索引
    client.patch(
        f"{API}/documents/{doc['id']}", headers=auth_headers, json={"content": ""}
    )
    reindexed = client.post(f"{API}/documents/reindex-content?sync=true", headers=auth_headers)
    assert reindexed.status_code == 200
    assert reindexed.json()["updated"] >= 1
    after = client.get(f"{API}/documents/{doc['id']}", headers=auth_headers).json()
    assert "氟利昂制冷剂" in after["content"]


# ---------- 假 PDF 嗅探回退 ----------

def test_fake_pdf_sniffing_fallback(client, auth_headers):
    # webclip 误存为 .pdf：非 %PDF 头、实为 HTML，应按 HTML 提取
    html_bytes = "<html><body><h1>网页标题</h1><p>这是一段网页正文内容</p></body></html>".encode("utf-8")
    resp = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": ("网页存档.pdf", io.BytesIO(html_bytes), "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    assert "网页正文内容" in doc["content"]

    # 非 PDF 头的纯文本也应按文本提取
    text_bytes = "纯文本伪装成 PDF 的内容".encode("utf-8")
    resp = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": ("文本伪装.pdf", io.BytesIO(text_bytes), "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    assert "纯文本伪装" in resp.json()["content"]


# ---------- SHA-256 去重 ----------

def test_upload_deduplication_by_sha256(client, auth_headers):
    content = "去重测试正文\n" * 20
    resp1 = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": ("去重测试.txt", io.BytesIO(content.encode("utf-8")), "text/plain")},
    )
    assert resp1.status_code == 201, resp1.text
    first = resp1.json()

    # 同内容再次上传 → 409 + 已存在条目信息
    resp2 = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": ("副本.txt", io.BytesIO(content.encode("utf-8")), "text/plain")},
    )
    assert resp2.status_code == 409, resp2.text
    detail = resp2.json()["detail"]
    assert detail["code"] == "duplicate"
    assert detail["existing_id"] == first["id"]

    # 不同内容 → 正常入库
    resp3 = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": ("新内容.txt", io.BytesIO("完全不同的内容".encode("utf-8")), "text/plain")},
    )
    assert resp3.status_code == 201, resp3.text


# ---------- 后台重索引任务 ----------

def test_reindex_job_async_and_status(client, auth_headers, monkeypatch):
    import time

    import app.services.reindex_job as rj

    # 不真正跑库，替换线程入口为短暂占位
    def fake_run(session_factory):
        time.sleep(0.15)
        rj._set(running=False, finished_at="t")

    monkeypatch.setattr(rj, "_run", fake_run)

    resp = client.post(f"{API}/documents/reindex-content", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"

    st = client.get(f"{API}/documents/reindex-status", headers=auth_headers).json()
    assert st["running"] is True
    assert st["started_at"] is not None

    # 运行中再次触发 → 409
    again = client.post(f"{API}/documents/reindex-content", headers=auth_headers)
    assert again.status_code == 409

    time.sleep(0.4)
    st = client.get(f"{API}/documents/reindex-status", headers=auth_headers).json()
    assert st["running"] is False
    assert st["finished_at"] is not None


# ---------- 重命名安全重构 ----------

def test_rename_safe_refactor(client, auth_headers):
    target = client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={"type": "note", "title": "旧书名", "content": "正文"},
    ).json()
    ref_note = client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={"type": "note", "title": "引用者", "content": "参见[[旧书名]]的讨论"},
    ).json()

    renamed = client.patch(
        f"{API}/documents/{target['id']}",
        headers=auth_headers,
        json={"title": "新书名"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "新书名"

    updated_ref = client.get(f"{API}/documents/{ref_note['id']}", headers=auth_headers).json()
    assert "[[新书名]]" in updated_ref["content"]
    assert "[[旧书名]]" not in updated_ref["content"]

    links = client.get(
        f"{API}/links", headers=auth_headers, params={"source_id": ref_note["id"]}
    ).json()
    assert any(l["target_id"] == target["id"] and l["type"] == "mention" for l in links)

    # 占位提及应消失
    health = client.get(f"{API}/stats/health", headers=auth_headers).json()
    assert not any(p["mention"] == "旧书名" for p in health["placeholders"])


# ---------- Markdown 批量导出 ----------

def test_markdown_export(client, auth_headers):
    note = client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={"type": "note", "title": "导出笔记", "content": "## 内容\n正文文字", "tags": ["导出"]},
    ).json()

    resp = client.get(f"{API}/export/markdown", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert "index.md" in names
    md_files = [n for n in names if n.endswith(".md") and n != "index.md"]
    assert any("导出笔记" in n for n in md_files)
    content = zf.read(next(n for n in md_files if "导出笔记" in n)).decode("utf-8")
    assert "# 导出笔记" in content
    assert "正文文字" in content
    assert "标签：导出" in content


# ---------- 实时协作（Yjs WebSocket）----------

def test_collab_websocket_sync(client, auth_headers):
    from pycrdt import (
        Doc,
        Text,
        YSyncMessageType,
        create_sync_message,
        create_update_message,
        handle_sync_message,
    )

    # 授权校验（单元级；集成级拒绝会导致 TestClient 等待 accept 挂起）
    from app.collab import _reject_unauthorized

    assert (
        _reject_unauthorized({}, {"path": "/ws/collab/doc-1", "query_string": b"token=bad", "headers": []})
        is True
    )
    assert (
        _reject_unauthorized(
            {}, {"path": "/ws/collab/doc-12345678", "query_string": b"token=test-token", "headers": []}
        )
        is False
    )
    assert (
        _reject_unauthorized(
            {}, {"path": "/ws/collab/../../etc/passwd", "query_string": b"token=test-token", "headers": []}
        )
        is True
    )

    def sync_exchange(ws, doc: Doc) -> None:
        """完整 Yjs 同步握手：反复交换 SYNC 消息直到收到 STEP2"""
        ws.send_bytes(create_sync_message(doc))
        for _ in range(10):
            msg = ws.receive_bytes()
            if msg[0] != 0:  # 跳过 awareness 等
                continue
            if msg[1] == YSyncMessageType.SYNC_STEP1 and len(msg) <= 2:
                # 服务器空状态向量（pycrdt 0.14 无法解码空 state），忽略
                continue
            reply = handle_sync_message(msg[1:], doc)
            if reply is not None:
                ws.send_bytes(reply)
            if msg[1] == YSyncMessageType.SYNC_STEP2:
                return
        raise AssertionError("sync handshake did not finish")

    doc_a = Doc()
    doc_a["content"] = Text("协作测试内容")
    with client.websocket_connect("/ws/collab/doc-12345678?token=test-token") as ws_a:
        ws_a.send_bytes(create_sync_message(doc_a))
        for _ in range(10):
            msg = ws_a.receive_bytes()
            if msg[0] != 0:
                continue
            if msg[1] == YSyncMessageType.SYNC_STEP1:
                # 服务器请求我方更新 → 回复 STEP2
                ws_a.send_bytes(create_update_message(doc_a.get_update()))
                break

    # 第二个客户端同步到内容（服务端持久化 + 广播）
    doc_b = Doc()
    with client.websocket_connect("/ws/collab/doc-12345678?token=test-token") as ws_b:
        sync_exchange(ws_b, doc_b)
        assert str(doc_b.get("content", type=Text)) == "协作测试内容"

    # 冷启动恢复：从持久化存储回放更新到全新文档
    import time

    time.sleep(0.3)  # 等待异步落盘完成
    from app.collab import _apply_stored_updates
    from app.config import get_settings
    from pathlib import Path

    doc_cold = Doc()
    _apply_stored_updates(Path(get_settings().data_dir) / "collab.db", "doc-12345678", doc_cold)
    assert str(doc_cold.get("content", type=Text)) == "协作测试内容"


# ---------- 双链 / 反向链接 ----------

def test_mention_links_and_backlinks(client, auth_headers):
    # 目标文档（书）
    name, fh = _txt_file("被引用的书.txt")
    target = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": (name, fh, "text/plain")},
    ).json()

    # 笔记 A 通过 [[标题]] 引用书
    note_a = client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={"type": "note", "title": "引用笔记A", "content": "读[[被引用的书]]的感想"},
    ).json()

    links = client.get(
        f"{API}/links", headers=auth_headers, params={"source_id": note_a["id"]}
    ).json()
    assert any(l["target_id"] == target["id"] and l["type"] == "mention" for l in links)

    backlinks = client.get(f"{API}/documents/{target['id']}/backlinks", headers=auth_headers).json()
    assert any(b["id"] == note_a["id"] for b in backlinks)
    backlink = next(b for b in backlinks if b["id"] == note_a["id"])
    assert "被引用的书" in backlink["snippet"]

    # 未链接提及：正文包含标题但无 [[链接]]
    unlinked_note = client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={"type": "note", "title": "散记", "content": "昨天又翻了一遍被引用的书，很有启发"},
    ).json()
    unlinked = client.get(
        f"{API}/documents/{target['id']}/unlinked-mentions", headers=auth_headers
    ).json()
    assert any(m["id"] == unlinked_note["id"] for m in unlinked)
    mention = next(m for m in unlinked if m["id"] == unlinked_note["id"])
    assert "<mark>被引用的书</mark>" in mention["snippet"]

    # 建立链接后不再出现在未链接列表
    client.post(
        f"{API}/links",
        headers=auth_headers,
        json={"source_id": unlinked_note["id"], "target_id": target["id"], "type": "mention"},
    )
    unlinked_after = client.get(
        f"{API}/documents/{target['id']}/unlinked-mentions", headers=auth_headers
    ).json()
    assert not any(m["id"] == unlinked_note["id"] for m in unlinked_after)

    # 移除双链 → 链接清理
    client.patch(
        f"{API}/documents/{note_a['id']}",
        headers=auth_headers,
        json={"content": "没有链接了"},
    )
    links_after = client.get(
        f"{API}/links", headers=auth_headers, params={"source_id": note_a["id"]}
    ).json()
    assert not any(l["type"] == "mention" for l in links_after)

    # 图谱包含节点与边
    graph = client.get(f"{API}/links/graph", headers=auth_headers).json()
    assert any(n["id"] == target["id"] for n in graph["nodes"])


# ---------- zip 备份 / 恢复 ----------

def test_backup_and_restore_roundtrip(client, auth_headers):
    # 造数据：一本书（带文件）+ 一篇笔记
    name, fh = _txt_file("备份测试书.txt", "备份正文内容" * 10)
    doc = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": (name, fh, "text/plain")},
    ).json()

    backup = client.get(f"{API}/backup", headers=auth_headers)
    assert backup.status_code == 200
    assert backup.headers["content-type"] == "application/zip"

    import io
    import zipfile

    zf = zipfile.ZipFile(io.BytesIO(backup.content))
    names = zf.namelist()
    assert "meta.json" in names
    assert any(n.startswith("files/") for n in names)
    import json as json_mod

    meta = json_mod.loads(zf.read("meta.json"))
    assert any(d["id"] == doc["id"] for d in meta["documents"])

    # 硬删除该书模拟灾难，再从备份恢复
    client.delete(f"{API}/documents/{doc['id']}?hard=true", headers=auth_headers)
    assert client.get(f"{API}/documents/{doc['id']}", headers=auth_headers).status_code == 404

    restore = client.post(
        f"{API}/backup/restore",
        headers=auth_headers,
        files={"file": ("backup.zip", io.BytesIO(backup.content), "application/zip")},
    )
    assert restore.status_code == 200, restore.text
    assert restore.json()["restored"]["documents"] >= 1

    restored_doc = client.get(f"{API}/documents/{doc['id']}", headers=auth_headers).json()
    assert restored_doc["title"] == doc["title"]
    file_resp = client.get(f"{API}/documents/{doc['id']}/file", headers=auth_headers)
    assert file_resp.status_code == 200
    assert b"\xe5\xa4\x87\xe4\xbb\xbd" in file_resp.content  # "备份" UTF-8


# ---------- 网页剪藏（mock 抓取）----------

def test_clip(client, auth_headers, monkeypatch):
    fake_html = """
    <html><head><title>深度工作指南</title></head>
    <body><nav>导航</nav><article><h1>深度工作指南</h1>
    <p>深度工作是在无干扰状态下专注进行的专业活动，是产出高质量成果的关键。</p>
    </article><footer>页脚</footer></body></html>
    """

    import contextlib

    class FakeStreamResp:
        charset_encoding = "utf-8"
        headers = {}
        url = "https://example.com/deep-work"

        def __init__(self):
            self._body = fake_html.encode("utf-8")

        @property
        def status_code(self):
            return 200

        @property
        def is_redirect(self):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield self._body

    import app.routers.clip as clip_module

    @contextlib.contextmanager
    def fake_stream(method, url, **kwargs):
        yield FakeStreamResp()

    monkeypatch.setattr(clip_module.httpx, "stream", fake_stream)

    result = client.post(
        f"{API}/clip", headers=auth_headers, json={"url": "https://example.com/deep-work", "tags": ["方法论"]}
    )
    assert result.status_code == 201, result.text
    body = result.json()
    assert "深度工作" in body["title"] or "深度工作" in body["excerpt"]
    assert body["url"] == "https://example.com/deep-work"

    # 文档可检索、有标签
    doc = client.get(f"{API}/documents/{body['id']}", headers=auth_headers).json()
    assert doc["type"] == "webclip"
    assert any(t["name"] == "方法论" for t in doc["tags"])
    hits = client.get(f"{API}/search", headers=auth_headers, params={"q": "无干扰"}).json()
    assert any(h["id"] == body["id"] for h in hits)


def test_clip_url_ssrf_guard(client, auth_headers, monkeypatch):
    # 默认拦截本机地址，防止 SSRF
    blocked = client.post(
        f"{API}/clip",
        headers=auth_headers,
        json={"url": "http://localhost:8111/api/health"},
    )
    assert blocked.status_code == 400
    assert "Blocked" in blocked.text

    # KB_ALLOW_LOCAL_CLIP=1 仅测试通道放行本机
    import app.services.url_safety as url_safety

    from app.config import get_settings

    monkeypatch.setattr(
        get_settings(), "allow_local_clip", True
    )
    ok = client.post(
        f"{API}/clip",
        headers=auth_headers,
        json={"url": "http://127.0.0.1:1/health"},
    )
    assert ok.status_code in (201, 502)  # 放行后到达抓取阶段（连接失败或成功均可，取决于端口）

# ---------- 文档洞察（幽灵端点落地） ----------

def test_document_insight(client, auth_headers):
    note = client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={
            "type": "note",
            "title": "洞察测试笔记",
            "content": (
                "机器学习是人工智能的核心方向。机器学习模型需要大量数据训练。"
                "深度学习是机器学习的一个重要分支。深度学习显著推动了图像识别进步。"
                "强化学习被广泛用于序贯决策问题。机器学习的应用范围日益扩大。"
            ),
        },
    ).json()
    resp = client.get(f"{API}/documents/{note['id']}/insight", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]
    assert "机器学习" in body["summary"]
    assert isinstance(body["suggested_tags"], list)
    assert any("学习" in t for t in body["suggested_tags"])

    missing = client.get(f"{API}/documents/{uuid.uuid4()}/insight", headers=auth_headers)
    assert missing.status_code == 404


def test_document_insight_empty_content(client, auth_headers):
    note = client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={"type": "note", "title": f"zzemptyinsight{_COUNTER[0]}", "content": ""},
    ).json()
    body = client.get(f"{API}/documents/{note['id']}/insight", headers=auth_headers).json()
    # 正文与描述均为空 → 摘要为空；标签仅可能来自纯 ASCII 标题，允许为空或非空列表
    assert body["summary"] == ""
    assert isinstance(body["suggested_tags"], list)


# ---------- 搜索摘要（幽灵端点落地） ----------

def test_search_summary_endpoint(client, auth_headers):
    marker = f"zzqxsummary{_COUNTER[0]}{_unique_marker().replace(' ', '').replace('#', '')}"
    client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={
            "type": "note",
            "title": f"摘要统计笔记 {marker}",
            "content": f"包含独特标记 {marker} 的正文，讨论认知负荷与工作记忆的关系。",
        },
    )
    resp = client.get(f"{API}/search/summary", headers=auth_headers, params={"q": marker})
    assert resp.status_code == 200
    body = resp.json()
    assert marker in body["query"]
    assert body["total"] >= 1
    assert body["by_type"].get("note", 0) >= 1
    assert isinstance(body["suggestions"], list)

    # 短词走 ILIKE 兜底路径同样可用
    resp2 = client.get(f"{API}/search/summary", headers=auth_headers, params={"q": "摘要"})
    assert resp2.status_code == 200
    assert resp2.json()["total"] >= 1


# ---------- 列表轻量化 + 分页（include_content/limit/offset） ----------

def test_list_documents_lightweight_and_pagination(client, auth_headers):
    marker = f"轻量列表 {_unique_marker()}"
    created = []
    for i in range(3):
        d = client.post(
            f"{API}/documents",
            headers=auth_headers,
            json={
                "type": "note",
                "title": f"{marker} 第{i}篇",
                "content": "这是一段用于截断验证的较长正文内容。" * 30 + f"结尾编号{i}",
            },
        ).json()
        created.append(d)

    listed = client.get(
        f"{API}/documents",
        headers=auth_headers,
        params={"q": marker, "limit": "2", "offset": "0"},
    ).json()
    assert len(listed) == 2
    # 默认轻量模式：content 为截断摘要（≤201 字符且带省略号）
    for item in listed:
        assert len(item["content"]) <= 201
        assert item["content"].endswith("…")

    # offset 翻页
    page2 = client.get(
        f"{API}/documents",
        headers=auth_headers,
        params={"q": marker, "limit": "2", "offset": "2"},
    ).json()
    assert len(page2) == 1

    # include_content=true 时返回全文（向后兼容）
    full = client.get(
        f"{API}/documents",
        headers=auth_headers,
        params={"q": marker, "include_content": "true"},
    ).json()
    assert any("结尾编号0" in d["content"] for d in full)

    # detail 接口始终返回全文（向后兼容）
    detail = client.get(f"{API}/documents/{created[0]['id']}", headers=auth_headers).json()
    assert "结尾编号0" in detail["content"]


# ---------- 上传白名单 / 文件下载响应头 / 备份大小上限 ----------

def test_upload_rejects_disallowed_extension(client, auth_headers):
    resp = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": ("evil.exe", io.BytesIO(b"MZ" + b"\x00" * 32), "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert "Unsupported file format" in resp.text


def test_document_file_download_security_headers(client, auth_headers):
    name, fh = _txt_file("响应头测试书.txt")
    doc = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": (name, fh, "text/plain")},
    ).json()

    full_resp = client.get(f"{API}/documents/{doc['id']}/file", headers=auth_headers)
    assert full_resp.status_code == 200
    assert full_resp.headers["x-content-type-options"] == "nosniff"
    assert full_resp.headers["content-disposition"].startswith("attachment")

    range_resp = client.get(
        f"{API}/documents/{doc['id']}/file",
        headers={**auth_headers, "Range": "bytes=0-3"},
    )
    assert range_resp.status_code == 206
    assert range_resp.headers["x-content-type-options"] == "nosniff"
    assert range_resp.headers["content-disposition"].startswith("attachment")


def test_backup_restore_size_limit(client, auth_headers, monkeypatch):
    from app.config import get_settings

    settings_obj = get_settings()
    monkeypatch.setattr(settings_obj, "upload_max_size_mb", 1)

    oversized = b"PK\x03\x04" + b"x" * (1024 * 1024 + 128)
    resp = client.post(
        f"{API}/backup/restore",
        headers=auth_headers,
        files={"file": ("backup.zip", io.BytesIO(oversized), "application/zip")},
    )
    assert resp.status_code == 413
    assert "upload limit" in resp.text
