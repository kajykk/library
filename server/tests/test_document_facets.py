"""分面聚合接口测试（RW-101 / B3-02）。

RD-01 的直接教训：facets 合进来时没有一条测试命中该端点，`NameError` 一路活到人工复核。
本文件第一条用例就是「调用返回 200」，其余用例覆盖契约里的自排除、归一化与去重口径。
"""

import uuid

import pytest
from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.models import Collection, Document, Tag, utcnow
from app.routers.documents import facets as facets_module

Base.metadata.create_all(engine)

# 只命中本文件造的样本，隔离同一 session 库里其他测试的数据
MARK = "fct7k"
HIST = f"{MARK}-历史"
PERSON = f"{MARK}-人物"
COLL_A = f"{MARK}-A"
COLL_B = f"{MARK}-B"


def _new(title, **kw):
    kw.setdefault("type", "book")
    kw.setdefault("author", "")
    kw.setdefault("language", "zh")
    return Document(id=uuid.uuid4(), title=f"{title}-{MARK}", content=f"{MARK} 正文", **kw)


@pytest.fixture(scope="module")
def facet_data():
    """5 本存活 book/pdf + 1 本软删 + 1 本 note（后两者都不该进分面）。"""
    with SessionLocal() as db:
        tag_hist = Tag(id=uuid.uuid4(), name=HIST)
        tag_person = Tag(id=uuid.uuid4(), name=PERSON)
        coll_a = Collection(id=uuid.uuid4(), name=COLL_A)
        coll_b = Collection(id=uuid.uuid4(), name=COLL_B)
        db.add_all([tag_hist, tag_person, coll_a, coll_b])

        d1 = _new("d1", format="pdf", language="en-US", author="张三", collection_id=coll_a.id)
        d1.tags = [tag_hist, tag_person]
        d2 = _new("d2", type="pdf", format="pdf", language="ENG", author="张三",
                  collection_id=coll_a.id)
        d2.tags = [tag_hist]
        d3 = _new("d3", format="epub", author="李四", collection_id=coll_b.id)
        d3.tags = [tag_hist]
        d4 = _new("d4", format="txt", language="zh-TW")
        d5 = _new("d5", format="epub", language="", author="   ")
        gone = _new("gone", format="pdf", language="en", collection_id=coll_a.id,
                    deleted_at=utcnow())
        note = _new("note", type="note", format="epub", collection_id=coll_a.id)
        db.add_all([d1, d2, d3, d4, d5, gone, note])
        db.commit()
        ids = {d.id for d in (d1, d2, d3, d4, d5, gone, note)}
        yield {"coll_a": str(coll_a.id), "coll_b": str(coll_b.id)}

        for row in db.scalars(select(Document).where(Document.id.in_(ids))).all():
            db.delete(row)
        for row in db.scalars(select(Tag).where(Tag.id.in_([tag_hist.id, tag_person.id]))).all():
            db.delete(row)
        for row in db.scalars(
            select(Collection).where(Collection.id.in_([coll_a.id, coll_b.id]))
        ).all():
            db.delete(row)
        db.commit()


def _facets(client, **params):
    resp = client.get("/api/documents/facets", params={"q": MARK, **params},
                      headers={"X-API-Token": "test-token"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _buckets(body, key):
    return {item["value"]: item["count"] for item in body[key]}


def _cats(body):
    return {item["name"]: item["count"] for item in body["categories"]}


class TestFacetsSmoke:
    def test_returns_200_without_params(self, client):
        """RD-01 回归：旧实现每次调用都 500（sa_func 未定义），这条用例当时不存在。"""
        resp = client.get("/api/documents/facets", headers={"X-API-Token": "test-token"})
        assert resp.status_code == 200
        body = resp.json()
        for key in ("total", "formats", "languages", "tags", "authors",
                    "categories", "uncategorized", "truncated"):
            assert key in body
        assert isinstance(body["truncated"]["tags"], bool)

    def test_counts_are_scoped_to_alive_book_pdf(self, client, facet_data):
        # gone（软删）与 note（非书架类型）都不计入
        assert _facets(client)["total"] == 5


class TestFacetDimensions:
    def test_formats_languages_authors_and_categories(self, client, facet_data):
        body = _facets(client)
        assert _buckets(body, "formats") == {"pdf": 2, "epub": 2, "txt": 1}
        assert sum(_buckets(body, "formats").values()) == body["total"]
        # en-US / ENG 归到 en，zh-TW 归到 zh，空值归到 und
        assert _buckets(body, "languages") == {"en": 2, "zh": 2, "und": 1}
        assert sum(_buckets(body, "languages").values()) == body["total"]
        assert body["uncategorized"] == 2
        # 空作者与纯空白作者都不进桶
        assert _buckets(body, "authors") == {"张三": 2, "李四": 1}
        assert _cats(body) == {COLL_A: 2, COLL_B: 1}
        assert body["categories"][0]["collection_id"] == facet_data["coll_a"]
        assert body["uncategorized"] + sum(_cats(body).values()) == body["total"]

    def test_multi_tag_document_counted_once(self, client, facet_data):
        """d1 同时有两个标签：join 会放大行数，tags 维必须 COUNT(DISTINCT document_id)。"""
        assert _buckets(_facets(client), "tags") == {HIST: 3, PERSON: 1}

    def test_truncated_flags(self, client, facet_data, monkeypatch):
        monkeypatch.setattr(facets_module, "FACET_LIMIT", 1)
        body = _facets(client)
        assert len(body["tags"]) == 1 and body["truncated"]["tags"] is True
        assert len(body["authors"]) == 1 and body["truncated"]["authors"] is True


class TestSelfExclusion:
    """每一维用「除自身以外」的筛选统计，否则选中后其他候选计数归零、用户切不走。"""

    def test_format_facet_excludes_format_filter(self, client, facet_data):
        body = _facets(client, format="pdf")
        assert body["total"] == 2
        assert _buckets(body, "formats") == {"pdf": 2, "epub": 2, "txt": 1}

    def test_language_facet_excludes_language_filter(self, client, facet_data):
        body = _facets(client, language="en")
        # language=en 要命中 en-US 与 ENG 两本
        assert body["total"] == 2
        assert _buckets(body, "languages") == {"en": 2, "zh": 2, "und": 1}

    def test_tag_facet_excludes_tag_filter(self, client, facet_data):
        body = _facets(client, tag=HIST)
        assert body["total"] == 3
        assert _buckets(body, "tags") == {HIST: 3, PERSON: 1}

    def test_category_facet_excludes_collection_filter(self, client, facet_data):
        body = _facets(client, collection_id=facet_data["coll_a"])
        assert body["total"] == 2
        assert _cats(body) == {COLL_A: 2, COLL_B: 1}
        assert body["uncategorized"] == 2

    def test_author_facet_excludes_author_filter(self, client, facet_data):
        body = _facets(client, author="张三")
        assert body["total"] == 2
        assert _buckets(body, "authors") == {"张三": 2, "李四": 1}

    def test_types_always_participates(self, client, facet_data):
        """types/q/status/quality 永远参与：types=note 与书架作用域求交后应为空。"""
        body = _facets(client, types="note")
        assert body["total"] == 0
        assert body["formats"] == []


class TestConsistencyWithCountAndStats:
    @pytest.mark.parametrize("params", [{}, {"format": "epub"}, {"language": "zh"}])
    def test_total_matches_count_endpoint(self, client, facet_data, params):
        body = _facets(client, **params)
        resp = client.get("/api/documents/count", params={"q": MARK, "types": "book,pdf", **params},
                          headers={"X-API-Token": "test-token"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["count"] == body["total"]

    def test_uncategorized_matches_library_stats(self, client):
        """无筛选时 facets.uncategorized 必须与书架侧栏同名字段一致。"""
        headers = {"X-API-Token": "test-token"}
        body = client.get("/api/documents/facets", headers=headers).json()
        stats = client.get("/api/documents/stats", headers=headers).json()
        assert body["uncategorized"] == stats["uncategorized"]

    def test_invalid_status_rejected(self, client, facet_data):
        resp = client.get("/api/documents/facets", params={"q": MARK, "status": "bogus"},
                          headers={"X-API-Token": "test-token"})
        assert resp.status_code == 400
