"""查询侧关键词切分：把用户的整句/混合输入切成可检索的词。

入库侧的 `text_analysis.extract_keywords` 是「2 字滑窗 + 频次」，直接拿来当查询词会
产出「什么/如何/为什/伊德」这类噪声（R8 任务书 E13），所以查询侧单独一条路径。

设计约束（任务书 §1 的实测决定）：
- 无词典可用（G2 已按 R7 决议关闭，不装分词依赖），所以**不做机械切块**：
  单字虚词只在「两侧都能剩 ≥2 字」时当边界，否则只在段首/段尾剥离，且**剥离前后两种
  形态都留作词**——这样「人工智能」不会被 `人` 切成「工智能」而丢掉原形，
  「会让人腐败」能同时给出「腐败」。真正的召回上限由 ≥3 字滑窗降级（E4′/R8b）承担。
- ≥3 字的词走 FTS（trigram 能登记），2 字词只能走 LIKE：路由在 routers/search.py，
  这里只负责切词。
纯函数、零依赖、零 IO。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

# 显式分隔符表（不用 \W：词内的 - . + 要保留，如 RLHF、C++、1.0）
_SEPARATORS = (
    " \t\r\n ，、。！？；：「」『』【】（）《》〈〉“”‘’—…·～％＊＂＇＃＄＆（）－＝／＼｜"
    "!?\"'(),:;<>[]{}#$%&*~`|^@/"
)
_SEPARATOR_RE = re.compile(f"[{re.escape(_SEPARATORS)}]")

_CJK = "㐀-䶿一-鿿豈-﫿"  # CJK 统一表意文字 + 扩展 A + 兼容表意文字
_CJK_RE = re.compile(f"[{_CJK}]")

# 段内边界用的单字虚词：刻意不含 人/民/有/它/不/大/上/中/国 这类高频实语素
_FUNCTION_CHARS = set("的了是在和与及或之而就也被把对从很这那其们为吗呢吧啊于以会让")
# 段首/段尾剥离用的扩展表（只在剥离后仍 ≥2 字时才剥，且原形一并保留）
_EDGE_STRIP_CHARS = _FUNCTION_CHARS | {"人", "使", "系", "等"}

# 多字虚词/疑问框架：先于单字表整段切除，无歧义。应用时按长度降序，避免短项先吃掉长项
_MULTI_FUNCTION = tuple(sorted((
    "为什么", "为何", "什么是", "是什么", "什么样", "有什么", "怎么样", "怎样", "怎么",
    "哪些", "哪个", "哪里", "何处", "何时", "多少", "是否", "能否",
    "可以", "而且", "并且", "或者", "但是", "因为", "所以", "如果", "虽然", "然后",
    "以及", "之间", "来自", "属于", "有一个", "一个", "这个", "那个", "如何",
), key=len, reverse=True))

# 查询侧停用词：只用于拉丁词段（中文的噪声由 _MULTI_FUNCTION/_FUNCTION_CHARS 处理）
_LATIN_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "do", "does",
    "did", "have", "has", "had", "of", "to", "in", "on", "at", "by", "for", "with",
    "about", "into", "from", "as", "and", "or", "but", "if", "so", "not", "no", "yes",
    "can", "will", "would", "should", "could", "may", "might", "must", "it", "its",
    "this", "that", "these", "those", "they", "them", "their", "we", "our", "you",
    "your", "i", "he", "she", "his", "her", "who", "whom", "whose", "which", "what",
    "when", "where", "why", "how", "than", "then", "there", "here", "very", "more",
    "most", "some", "any", "each", "other", "such", "also", "just", "like", "between",
}

_MIN_TERM_LEN = 2
_WINDOW_SIZES = (4, 3)


@dataclass(frozen=True)
class QueryPlan:
    raw: str
    terms: tuple[str, ...]      # 有序、去重、最多 max_terms 个
    used_fallback: bool         # 是否追加了「长段滑窗」降级词


def _strip_edges(piece: str) -> tuple[str, set[str]]:
    """反复剥离段首/段尾的虚词，但只在剥离后仍 ≥2 字时接受；返回（剥后形态，剥掉的字符）。"""
    result = piece
    removed: set[str] = set()
    changed = True
    while changed and len(result) > _MIN_TERM_LEN:
        changed = False
        if result[0] in _EDGE_STRIP_CHARS and len(result[1:]) >= _MIN_TERM_LEN:
            removed.add(result[0])
            result = result[1:]
            changed = True
            continue
        if result[-1] in _EDGE_STRIP_CHARS and len(result[:-1]) >= _MIN_TERM_LEN:
            removed.add(result[-1])
            result = result[:-1]
            changed = True
    return result, removed


def _divide(piece: str) -> list[str]:
    """递归：每层只接受「两侧都剩 ≥2 字」的最左单字虚词边界。"""
    if len(piece) < 4:
        return [piece]
    for i, char in enumerate(piece):
        if char in _FUNCTION_CHARS and i >= _MIN_TERM_LEN and len(piece) - i - 1 >= _MIN_TERM_LEN:
            return _divide(piece[:i]) + _divide(piece[i + 1:])
    return [piece]


def _pieces_from_run(run: str) -> list[str]:
    """≥4 字的 CJK 连续段 → 词列表（原形 + 虚词剥离形）。

    剥离形只有在「原形 ≥5 字」或「剥掉的全是无歧义虚词」时才追加：
    「会让人腐败」要给出「腐败」，但「人工智能」不能因为 `人` 就附带一个「工智能」噪声词。
    """
    pieces = [run]
    for phrase in _MULTI_FUNCTION:
        pieces = [p for piece in pieces for p in piece.split(phrase) if p] or pieces
    terms: list[str] = []
    for piece in (p for divided in (_divide(piece) for piece in pieces) for p in divided):
        if len(piece) < _MIN_TERM_LEN:
            continue
        terms.append(piece)
        stripped, removed = _strip_edges(piece)
        if stripped == piece:
            continue
        if len(piece) >= 5 or not removed - _FUNCTION_CHARS:
            terms.append(stripped)
    return terms


def build_query_plan(q: str, max_terms: int = 6) -> QueryPlan:
    """按 RW-802 规则生成查询词；不检索、不碰数据库。"""
    raw = q.strip()
    if not raw:
        return QueryPlan(raw="", terms=(), used_fallback=False)

    collected: list[str] = []
    for segment in (s for s in _SEPARATOR_RE.split(raw) if s):
        segment_terms: list[str] = []
        for run in _iter_runs(segment):
            if _CJK_RE.match(run[0]):
                if len(run) <= 3:
                    if len(run) >= _MIN_TERM_LEN:
                        segment_terms.append(run)
                else:
                    segment_terms.extend(_pieces_from_run(run))
            elif (
                len(run) >= _MIN_TERM_LEN
                and any(char.isalnum() for char in run)
                and run.lower() not in _LATIN_STOPWORDS
            ):
                segment_terms.append(run)
        # 同一空格段内长的先出（长词更该占 max_terms 名额）
        segment_terms.sort(key=len, reverse=True)
        collected.extend(segment_terms)

    return QueryPlan(raw=raw, terms=_dedupe_truncate(collected, max_terms), used_fallback=False)


def _iter_runs(segment: str) -> list[str]:
    """空格段拆成 CJK / 非 CJK 连续串的交替序列，保持出现顺序（"RLHF对齐" → RLHF, 对齐）。"""
    runs: list[str] = []
    pos = 0
    while pos < len(segment):
        end = pos
        is_cjk = bool(_CJK_RE.match(segment[pos]))
        while end < len(segment) and bool(_CJK_RE.match(segment[end])) == is_cjk:
            end += 1
        runs.append(segment[pos:end])
        pos = end
    return runs


def _dedupe_truncate(terms: list[str], max_terms: int) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        key = term.lower()
        if term in seen or (not _CJK_RE.match(term[0]) and key in seen):
            continue
        seen.add(term)
        if not _CJK_RE.match(term[0]):
            seen.add(key)
        out.append(term)
        if len(out) >= max_terms:
            break
    return tuple(out)


def iter_recall_windows(plan: QueryPlan, size: int = 0) -> Iterator[str]:
    """名额不足时的降级词：把计划里 ≥5 字的长词按 4 字与 3 字滑窗拆开，按出现顺序惰性产出。

    只从**已切好的长词**取窗，不从原文取——原文里带着虚词，滑出来是「什么会让」这种噪声
    （R8 任务书 E13 的教训）。4 字窗优先于 3 字窗（更精确），惰性产出是为了让调用方
    「凑满 limit 就停」，不要一次发一堆 FTS 查询（E8：每条 0–83 ms）。`size` > 0 时只用该窗长。
    """
    existing = set(plan.terms)
    taken: set[str] = set()
    sizes = (size,) if size else _WINDOW_SIZES
    for term in plan.terms:
        if len(term) < max(size or 0, 5) or not _CJK_RE.match(term[0]):
            continue
        for i in range(len(term) - min(sizes) + 1):
            for s in sizes:
                if i + s > len(term):
                    continue
                window = term[i:i + s]
                if window in existing or window in taken:
                    continue
                taken.add(window)
                yield window


def with_recall_windows(plan: QueryPlan, windows: tuple[str, ...]) -> QueryPlan:
    """把降级词并入计划，并把 `used_fallback` 置为 True。"""
    return QueryPlan(raw=plan.raw, terms=_dedupe_truncate(list(plan.terms) + list(windows), 10_000),
                     used_fallback=True)
