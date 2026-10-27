"""`/api/search` 与 `/api/search/summary` 的功能测试（RW-806，任务书 §3 的 14 条必覆盖项）。

30 例，逐条标注口径（把本文件跑在 R8a 之前的 `search.py` 上得到 12 红，见任务书 §5 的 RW-806 槽）：

- `[红→绿]`：旧实现跑红，且红的原因是行为差异。
- `[回归锁定]`：旧实现也绿，锁住既有正确行为。
- `[新约束]`：本批才存在的形状/边界，旧模块连被断言的函数都还没有（那几条红是 AttributeError，
  不是行为差异——不当成红→绿宣称）。
"""

import pathlib
import sys

SERVER_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_ROOT))

import pytest
from sqlalchemy import event
from sqlalchemy.dialects import sqlite

from app.database import engine
from app.routers import search as search_mod

API = "/api"

_LONG_PAD = "乙" * 520

_SEEDS = {
    # ① 整句查询的目标文档：含「弗洛伊德」「认为文明」「本能升华」三个 ≥3 字词与「结果」这个 2 字词
    "sentence": {
        "type": "note",
        "title": "R8 整句检索笔记",
        "content": "弗洛伊德认为文明建立在压抑本能的基础上，本能升华是处理这种冲突的主要出口，它不是零和游戏的结果。",
    },
    # ② 纯 2 字词的目标
    "short": {
        "type": "note",
        "title": "R8 两字词笔记",
        "content": "限额之外的两字词只走 LIKE：熵减协作、湮没测试都写在这里。",
    },
    # ③ 混合长短词的第二目标（复盘 = 2 字词）
    "mixed": {
        "type": "article",
        "title": "R8 混合词文章",
        "content": "每天复盘十分钟，弗洛伊德的例子放这里没用。",
    },
    # ④⑤ 拉丁词与大小写：RLHF 只出现在正文末尾，靠大小写折叠的窗口锚定才取得到
    "latin": {
        "type": "note",
        "title": "R8 大小写笔记",
        "content": f"{_LONG_PAD} RLHF 全称 Reinforcement Learning from Human Feedback，用于大模型对齐。",
    },
    # ⑮ 滑窗降级：整词「熵减协作机制」不在库里，4 字窗口「熵减协作」在
    "window": {
        "type": "note",
        "title": "R8 滑窗笔记",
        "content": "本文只写四分片段：熵减协作。其余内容不含查询整词。",
    },
    # ⑭ 候选集一致性：唯一四字词，恰好 3 篇（2 note + 1 article）
    "count_a": {"type": "note", "title": "R8 计数甲", "content": "限额测试 第一篇。"},
    "count_b": {"type": "note", "title": "R8 计数乙", "content": "限额测试 第二篇。"},
    "count_c": {"type": "article", "title": "R8 计数丙", "content": "限额测试 第三篇。"},
    # ⑯ 字段优先级：标题命中应排在正文命中之前
    "title_hit": {"type": "note", "title": "R8 排序：湮没测试在标题", "content": "正文里没有那个词。"},
    "body_hit": {"type": "note", "title": "R8 排序：正文命中", "content": "湮没测试 只出现在正文很靠后的位置。"},
}


@pytest.fixture(scope="module")
def seeded(client, auth_headers):
    out = {}
    for key, payload in _SEEDS.items():
        resp = client.post(f"{API}/documents", headers=auth_headers, json=payload)
        assert resp.status_code == 201, resp.text
        out[key] = resp.json()
    return out


@pytest.fixture
def captured_sql():
    """抓本次请求真实下发的 SQL：E3/E4/E15 的形状约束只能这样锁。"""
    statements = []

    @event.listens_for(engine, "before_cursor_execute")
    def _listen(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    yield statements
    event.remove(engine, "before_cursor_execute", _listen)


def _strip_marks(snippet: str) -> str:
    return snippet.replace("<mark>", "").replace("</mark>", "")


class TestWholeSentenceQuery:
    """① 整句中文查询不再打空（RW-803 的红线）[红→绿]"""

    def test_sentence_query_hits(self, client, auth_headers, seeded):
        """[红→绿] 旧路径把整句当一个短语，0 命中"""
        resp = client.get(
            f"{API}/search",
            headers=auth_headers,
            params={"q": "弗洛伊德为什么认为文明是本能升华的结果"},
        )
        assert resp.status_code == 200
        hits = resp.json()
        assert hits, "整句查询必须切词后返回结果，不能 0 命中"
        assert hits[0]["title"] == seeded["sentence"]["title"]

    def test_sentence_query_snippet_marks_a_keyword(self, client, auth_headers, seeded):
        hits = client.get(
            f"{API}/search",
            headers=auth_headers,
            params={"q": "弗洛伊德为什么认为文明是本能升华的结果"},
        ).json()
        snippet = next(h["snippet"] for h in hits if h["title"] == seeded["sentence"]["title"])
        assert "<mark>" in snippet and "</mark>" in snippet
        assert any(word in snippet for word in ("弗洛伊德", "认为文明", "本能升华"))


class TestTermRouting:
    """②③④ 词长路由：≥3 字进 FTS，2 字进 LIKE，混合词两路并收"""

    def test_two_char_query(self, client, auth_headers, seeded, captured_sql):
        """② 纯 2 字词：trigram 不登记，只能 LIKE 命中 [回归锁定]"""
        hits = client.get(f"{API}/search", headers=auth_headers, params={"q": "熵减"}).json()
        assert any(h["title"] == seeded["short"]["title"] for h in hits)
        assert not any("MATCH" in s.upper() for s in captured_sql)

    def test_two_char_pair_query(self, client, auth_headers, seeded):
        """两个 2 字词合并成一条扫描，都要命中 [回归锁定]"""
        hits = client.get(f"{API}/search", headers=auth_headers, params={"q": "熵减 湮没"}).json()
        titles = {h["title"] for h in hits}
        assert seeded["short"]["title"] in titles
        assert seeded["title_hit"]["title"] in titles

    def test_mixed_long_and_short_terms(self, client, auth_headers, seeded):
        hits = client.get(f"{API}/search", headers=auth_headers, params={"q": "弗洛伊德 复盘"}).json()
        titles = {h["title"] for h in hits}
        assert seeded["sentence"]["title"] in titles      # 长词走 FTS
        assert seeded["mixed"]["title"] in titles         # 短词走 LIKE 填充

    def test_latin_acronym_query(self, client, auth_headers, seeded):
        hits = client.get(f"{API}/search", headers=auth_headers, params={"q": "对齐 RLHF"}).json()
        assert any(h["title"] == seeded["latin"]["title"] for h in hits)

    def test_mixed_cjk_latin_query(self, client, auth_headers, seeded):
        hits = client.get(
            f"{API}/search", headers=auth_headers, params={"q": "Reinforcement Learning"}
        ).json()
        assert any(h["title"] == seeded["latin"]["title"] for h in hits)


class TestCaseFold:
    """⑤ 大小写不敏感只对含 ASCII 字母的词成立"""

    def test_lowercase_query_reaches_upright_case_in_body(self, client, auth_headers, seeded):
        """[回归锁定] trigram 本身折叠 ASCII 大小写，旧路径同样能命中。

        RLHF 在 520 字之后：窗口若没做大小写折叠就锚不到它，snippet 里不会出现。
        """
        hits = client.get(f"{API}/search", headers=auth_headers, params={"q": "rlhf"}).json()
        hit = next(h for h in hits if h["title"] == seeded["latin"]["title"])
        assert "RLHF" in _strip_marks(hit["snippet"])
        assert "<mark>RLHF</mark>" in hit["snippet"]

    def test_pure_cjk_predicate_has_no_lower(self):
        """[新约束] E3：纯 CJK 词不再套 lower()"""
        sql = str(search_mod._like_predicate(["熵减协作"]).compile(dialect=sqlite.dialect()))
        assert "lower(" not in sql, "纯 CJK 词套 lower() 等于对 200k 字正文多一次全量拷贝（E3）"

    def test_ascii_bearing_predicate_uses_lower(self):
        """[新约束] 含 ASCII 的词仍折叠大小写"""
        sql = str(search_mod._like_predicate(["RLHF"]).compile(dialect=sqlite.dialect()))
        assert "lower(" in sql


class TestSoftDeleteAndType:
    """⑥⑦ 软删除排除与 type 过滤 [回归锁定]"""

    def test_soft_deleted_document_excluded(self, client, auth_headers):
        created = client.post(
            f"{API}/documents",
            headers=auth_headers,
            json={"type": "note", "title": "R8 待删笔记", "content": "湮没测试 只在这里出现一次。"},
        ).json()
        assert created["title"] in [
            h["title"]
            for h in client.get(f"{API}/search", headers=auth_headers, params={"q": "湮没测试"}).json()
        ]

        assert client.delete(f"{API}/documents/{created['id']}", headers=auth_headers).status_code == 204
        titles = [
            h["title"]
            for h in client.get(f"{API}/search", headers=auth_headers, params={"q": "湮没测试"}).json()
        ]
        assert created["title"] not in titles

    def test_type_filter(self, client, auth_headers, seeded):
        notes = client.get(
            f"{API}/search", headers=auth_headers, params={"q": "限额测试", "type": "note"}
        ).json()
        assert notes and all(h["type"] == "note" for h in notes)

        articles = client.get(
            f"{API}/search", headers=auth_headers, params={"q": "限额测试", "type": "article"}
        ).json()
        assert articles and all(h["type"] == "article" for h in articles)

        note_titles = {h["title"] for h in notes}
        assert not note_titles & {h["title"] for h in articles}


class TestParamValidation:
    """⑧⑨ limit 与空查询的边界"""

    def test_limit_clamps_result_count(self, client, auth_headers, seeded):
        hits = client.get(f"{API}/search", headers=auth_headers, params={"q": "限额测试", "limit": 2}).json()
        assert len(hits) == 2
        assert len(client.get(f"{API}/search", headers=auth_headers, params={"q": "限额测试"}).json()) == 3

    def test_limit_above_100_is_422(self, client, auth_headers):
        assert client.get(
            f"{API}/search", headers=auth_headers, params={"q": "限额测试", "limit": 101}
        ).status_code == 422

    def test_limit_below_1_is_422(self, client, auth_headers):
        """负数 limit 在 SQLite 里等于「不限」，必须挡住 [新约束]"""
        assert client.get(
            f"{API}/search", headers=auth_headers, params={"q": "限额测试", "limit": 0}
        ).status_code == 422
        assert client.get(
            f"{API}/search", headers=auth_headers, params={"q": "限额测试", "limit": -5}
        ).status_code == 422

    def test_blank_and_punctuation_only_queries(self, client, auth_headers):
        assert client.get(f"{API}/search", headers=auth_headers, params={"q": ""}).status_code == 422
        for q in ("   ", "，。！？", "；——、", "..."):
            assert client.get(f"{API}/search", headers=auth_headers, params={"q": q}).json() == [], q


class TestFtsDegradation:
    """⑩⑬ FTS 不可用与同步异常时的 LIKE 回退"""

    def test_fts_unavailable_still_returns_hits(self, client, auth_headers, seeded, monkeypatch):
        monkeypatch.setattr(search_mod, "fts_available", lambda: False)
        hits = client.get(
            f"{API}/search", headers=auth_headers, params={"q": "弗洛伊德认为文明"}
        ).json()
        assert any(h["title"] == seeded["sentence"]["title"] for h in hits)

    def test_fts_failure_falls_back_to_exactly_one_like_scan(
        self, client, auth_headers, seeded, monkeypatch
    ):
        """[新约束] 旧模块没有 `_like_hits`/`_fts_rank_ids`，在旧代码上的红是 AttributeError 而非行为差异"""
        calls = []
        real_like = search_mod._like_hits

        def spy(db, terms, doc_type, exclude, fetch):
            calls.append(list(terms))
            return real_like(db, terms, doc_type, exclude, fetch)

        def boom(db, term, fetch):
            raise RuntimeError("触发器同步异常")

        monkeypatch.setattr(search_mod, "_like_hits", spy)
        monkeypatch.setattr(search_mod, "_fts_rank_ids", boom)

        hits = client.get(
            f"{API}/search", headers=auth_headers, params={"q": "弗洛伊德 本能升华 熵减协作"}
        ).json()
        assert any(h["title"] == seeded["sentence"]["title"] for h in hits)
        assert len(calls) == 1, f"整条链只允许一次 LIKE 回退，实际 {calls}"
        assert sorted(calls[0]) == sorted(["弗洛伊德", "本能升华", "熵减协作"])

    def test_recall_window_fallback_when_all_terms_miss(
        self, client, auth_headers, seeded, captured_sql
    ):
        """整词打空 → 4 字滑窗补位 [红→绿]（旧路径整句一个短语，直接 0 命中）"""
        hits = client.get(
            f"{API}/search", headers=auth_headers, params={"q": "熵减协作机制"}
        ).json()
        assert any(h["title"] == seeded["window"]["title"] for h in hits)
        assert any("熵减协作" in _strip_marks(h["snippet"]) for h in hits)


class TestQueryShape:
    """E3/E4/E15 的 SQL 形状锁定：LIKE 扫描不带 ORDER BY、FTS 排序不带 JOIN [红→绿]"""

    def test_like_scan_has_no_order_by(self, client, auth_headers, captured_sql):
        client.get(f"{API}/search", headers=auth_headers, params={"q": "限额测试 熵减"})
        like_sql = [s for s in captured_sql if "LIKE" in s.upper()]
        assert like_sql, "2 字词应下发一条 LIKE 扫描"
        assert all("ORDER BY" not in s.upper() for s in like_sql), \
            "ORDER BY 会强制扫完全表，LIMIT 提前终止就没了（E4：10 ms → 670 ms）"

    def test_fts_ranking_query_has_no_join(self, client, auth_headers, captured_sql):
        client.get(f"{API}/search", headers=auth_headers, params={"q": "本能升华"})
        fts_sql = [s for s in captured_sql if "MATCH" in s.upper()]
        assert fts_sql
        assert all("JOIN" not in s.upper() for s in fts_sql), \
            "JOIN 让每个匹配行都回表取列（E15：20 ms → 60–180 ms）"


class TestSummaryEndpoint:
    """⑪⑭ summary 的形状、一致性，以及与 hits 共用同一条链"""

    def test_summary_shape(self, client, auth_headers, seeded):
        body = client.get(f"{API}/search/summary", headers=auth_headers, params={"q": "限额测试"}).json()
        assert set(body) == {"query", "total", "by_type", "suggestions"}
        assert body["query"] == "限额测试"
        assert isinstance(body["total"], int)
        assert isinstance(body["suggestions"], list)
        assert sum(body["by_type"].values()) == body["total"]

    def test_summary_counts_match_hits(self, client, auth_headers, seeded):
        """同一 q：候选集口径必须一致 [回归锁定]（四字词单条查询旧路径两侧都给 3，没暴露分裂；
        真正暴露分裂的是下面的滑窗降级用例）"""
        hits = client.get(f"{API}/search", headers=auth_headers, params={"q": "限额测试"}).json()
        body = client.get(f"{API}/search/summary", headers=auth_headers, params={"q": "限额测试"}).json()
        assert body["total"] == len(hits) == 3
        assert body["by_type"] == {"note": 2, "article": 1}

    def test_summary_and_hits_share_the_window_fallback(self, client, auth_headers, seeded):
        """summary 也必须走滑窗降级，否则两个端点口径分裂 [红→绿]"""
        hits = client.get(f"{API}/search", headers=auth_headers, params={"q": "熵减协作机制"}).json()
        body = client.get(f"{API}/search/summary", headers=auth_headers, params={"q": "熵减协作机制"}).json()
        assert hits and body["total"] == len(hits)

    def test_summary_of_no_match_query(self, client, auth_headers):
        body = client.get(
            f"{API}/search/summary", headers=auth_headers, params={"q": "鲸吞鼹鼠岩层表"}
        ).json()
        assert body["total"] == 0
        assert body["by_type"] == {}

    def test_summary_blank_query(self, client, auth_headers):
        assert client.get(
            f"{API}/search/summary", headers=auth_headers, params={"q": "  "}
        ).json() == {"query": "", "total": 0, "by_type": {}, "suggestions": []}


class TestRankingAndSnippet:
    """⑫⑯ 排序优先级与 snippet 上限"""

    def test_metadata_hit_ranks_above_content_hit(self, client, auth_headers, seeded):
        hits = client.get(f"{API}/search", headers=auth_headers, params={"q": "湮没测试"}).json()
        titles = [h["title"] for h in hits]
        assert titles.index(seeded["title_hit"]["title"]) < titles.index(seeded["body_hit"]["title"])

    def test_snippet_marks_and_respects_cap(self, client, auth_headers, seeded):
        hits = client.get(f"{API}/search", headers=auth_headers, params={"q": "对齐 RLHF"}).json()
        hit = next(h for h in hits if h["title"] == seeded["latin"]["title"])
        snippet = hit["snippet"]
        assert "<mark>" in snippet and snippet.count("<mark>") == snippet.count("</mark>")
        assert len(snippet) <= 220

    def test_clip_is_hard_cap(self):
        """[新约束] snippet 的 220 字硬上限由 `_clip` 保证（窗口宽度下路径够不到，故直接单测）"""
        clipped = search_mod._clip("字" * 1000)
        assert len(clipped) <= 220
        assert clipped.endswith("…")

    def test_clip_closes_open_mark(self):
        """[新约束] 截断不能留下未闭合的 `<mark>`"""
        clipped = search_mod._clip("前" * 210 + "<mark>命中" + "后" * 100)
        assert clipped.count("<mark>") == clipped.count("</mark>")
