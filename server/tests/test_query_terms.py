"""查询切词纯函数测试（RW-802）。

断言里出现的都是 2026-10-27 实测过的样本句（任务书 §1 的 15 条），
每条都对应一个 F-15 的具体失效形态，改规则时这些用例会先红。
"""

import pytest

from app.services.query_terms import (
    QueryPlan,
    build_query_plan,
    iter_recall_windows,
    with_recall_windows,
)


def _terms(q: str) -> tuple[str, ...]:
    return build_query_plan(q).terms


# ---------- 任务书 RW-802 的硬断言 ----------

def test_function_words_split_a_full_sentence_into_keywords():
    """「权力为什么会让人腐败？」现状是整句当一个词 → 0 命中（E1）。"""
    terms = _terms("权力为什么会让人腐败？")
    assert "权力" in terms
    assert "腐败" in terms


def test_four_char_term_is_not_broken_into_two_char_halves():
    """「政治哲学」必须整体保留：拆成 政治/哲学 会把 trigram 的精确命中丢掉。"""
    terms = _terms("什么是政治哲学？")
    assert terms == ("政治哲学",)


def test_long_compounds_survive_and_windows_recover_the_inner_keyword():
    """无词典时不机械切块（任务书 §7）；长词的召回靠滑窗降级。"""
    plan = build_query_plan("机器学习模型如何避免过拟合？")
    assert plan.terms == ("机器学习模型", "避免过拟合")
    windows = tuple(iter_recall_windows(plan))
    assert "机器学习" in windows
    assert "过拟合" in windows


def test_latin_acronym_survives_cjk_neighbors():
    assert "RLHF" in _terms("大模型对齐为什么需要 RLHF？")


@pytest.mark.parametrize("noise", ["?", "。", "   ", "", "\u3000", "，，"])
def test_punctuation_only_query_returns_empty_plan_without_raising(noise):
    plan = build_query_plan(noise)
    assert plan.terms == ()
    assert plan.used_fallback is False


# ---------- 规则细节 ----------

def test_latin_stopwords_do_not_eat_the_term_budget():
    """虚词不进 terms，名额留给真正有区分度的词。"""
    assert _terms("What is the hard problem of consciousness?") == (
        "hard", "problem", "consciousness",
    )


def test_term_within_word_characters_are_preserved():
    """分隔符表不含 - . +，所以 C++ / 1.0 / state-of-the-art 不会被切碎。"""
    assert "C++" in _terms("C++ 与 Rust 的所有权模型")
    assert "1.0" in _terms("React 1.0 的发布时间")


def test_punctuation_only_run_is_not_a_term():
    """`- . +` 为了保住 C++/1.0 不算分隔符，所以纯标点段必须靠「至少含一个字母或数字」兜住。"""
    assert _terms("...") == ()
    assert _terms("-- ++") == ()
    assert _terms("。。。") == ()


def test_latin_terms_dedup_case_insensitively_and_keep_first_form():
    assert _terms("Rust rust RUST ownership") == ("Rust", "ownership")


def test_content_bearing_char_is_not_a_word_boundary():
    """`人` 只允许在段首/段尾剥离，且原形 <5 字时不追加剥离形——
    否则「人工智能」会附带一个「工智能」噪声词。"""
    terms = _terms("人工智能")
    assert terms == ("人工智能",)
    assert "工智能" not in terms


def test_two_and_three_char_cjk_runs_become_terms_whole():
    assert _terms("弗洛伊德 文明 本能") == ("弗洛伊德", "文明", "本能")


def test_connector_char_splits_only_when_both_sides_keep_two_chars():
    """「起卦和断卦」两端都 ≥2 字 → 切成两个词；「人民」不能因为 `人` 被切。"""
    assert _terms("六爻纳甲如何起卦和断卦？") == ("六爻纳甲", "起卦", "断卦")
    assert _terms("人民的名义") == ("人民", "名义")


def test_terms_are_truncated_to_max_terms_in_appearance_order():
    terms = _terms("政治 哲学 权力 法律 自由 平等 正义 制度 国家 社会")
    assert len(terms) == 6
    assert terms[:3] == ("政治", "哲学", "权力")


def test_longer_term_from_the_same_segment_comes_first():
    """同段内长的先出，因为长词更该占 max_terms 名额。"""
    assert _terms("资本主义与民主之间有什么张力？") == ("资本主义", "民主", "张力")


def test_raw_is_stripped_but_kept_verbatim():
    plan = build_query_plan("  什么是政治哲学？  ")
    assert plan.raw == "什么是政治哲学？"


def test_single_char_terms_are_dropped():
    assert _terms("书 与 权力") == ("权力",)


# ---------- 滑窗降级 ----------

def test_windows_are_skipped_for_terms_that_are_already_short():
    plan = build_query_plan("国家权力的合法性来自哪里？")
    assert plan.terms == ("国家权力", "合法性")
    assert tuple(iter_recall_windows(plan)) == ()


def test_windows_can_be_forced_to_a_single_size():
    plan = build_query_plan("第一次世界大战为什么会爆发？")
    assert "第一次世界大战" in plan.terms
    assert set(iter_recall_windows(plan, size=3)) >= {"第一次", "世界大", "次世界"}
    assert all(len(w) == 3 for w in iter_recall_windows(plan, size=3))
    assert "第一次世" not in tuple(iter_recall_windows(plan, size=3))


def test_windows_exclude_terms_already_in_the_plan():
    plan = build_query_plan("机器学习模型如何避免过拟合？")
    assert not set(iter_recall_windows(plan)) & set(plan.terms)


def test_windows_are_lazy_and_deduped():
    """惰性产出：调用方凑满 limit 就可以停，不该一次算完整个列表。"""
    plan = build_query_plan("机器学习模型如何避免过拟合？")
    gen = iter_recall_windows(plan)
    first = [next(gen), next(gen)]
    assert len(set(first)) == 2


def test_with_recall_windows_appends_and_marks_fallback():
    plan = build_query_plan("机器学习模型如何避免过拟合？")
    merged = with_recall_windows(plan, ("机器学习", "过拟合"))
    assert merged.used_fallback is True
    assert merged.terms == plan.terms + ("机器学习", "过拟合")
    assert merged.raw == plan.raw


def test_with_recall_windows_drops_duplicates_of_existing_terms():
    plan = build_query_plan("政治哲学")
    merged = with_recall_windows(plan, ("政治哲学", "政治哲"))
    assert merged.terms == ("政治哲学", "政治哲")


def test_query_plan_is_frozen():
    plan = build_query_plan("政治哲学")
    assert isinstance(plan, QueryPlan)
    with pytest.raises(Exception):  # dataclass(frozen=True) → FrozenInstanceError
        plan.terms = ()  # type: ignore[misc]
