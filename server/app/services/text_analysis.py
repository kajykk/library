"""纯文本分析：关键词提取、一句话摘要、关键词上下文片段。"""

import re

_LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]+")
_CJK_BIGRAM_RE = re.compile(r"[\u4e00-\u9fff]")

_KEYWORD_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "are", "was", "were",
    "have", "has", "had", "not", "but", "you", "your", "its", "their", "they",
    "them", "our", "can", "will", "would", "should", "could", "into", "about",
    "these", "those", "been", "being", "does", "doing",
    "这些", "那些", "我们", "你们", "他们", "一个", "这个", "那个", "以及",
    "但是", "然后", "因此", "所以", "如果", "虽然", "可以", "没有", "就是",
    "还有", "进行", "通过", "对于", "关于", "出现", "或者", "并且",
}

_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")


def extract_keywords(text: str, top_n: int = 5) -> list[str]:
    """极简关键词提取：拉丁词 + 中文 2 字滑窗词频统计（无外部依赖）"""
    counts: dict[str, int] = {}
    for word in _LATIN_WORD_RE.findall(text):
        w = word.lower()
        counts[w] = counts.get(w, 0) + 1
    cjk_runs = re.findall(r"[\u4e00-\u9fff]+", text)
    for run in cjk_runs:
        for i in range(len(run) - 1):
            bigram = run[i : i + 2]
            counts[bigram] = counts.get(bigram, 0) + 1
    filtered = [
        (w, c)
        for w, c in counts.items()
        if w not in _KEYWORD_STOPWORDS and len(w) >= 2 and not w.isdigit()
    ]
    # 中文滑窗会互相重叠：同源 bigram 只保留频次最高者附近即可，简单按频次排序去重输出
    filtered.sort(key=lambda wc: (-wc[1], wc[0]))
    picked: list[str] = []
    for w, _c in filtered:
        if any(w in p or p in w for p in picked):
            continue
        picked.append(w)
        if len(picked) >= top_n:
            break
    return picked


def one_line_summary(text: str, max_chars: int = 100) -> str:
    """取第一句非空句子作为一句话摘要，过长截断"""
    plain = re.sub(r"\s+", " ", text or "").strip()
    if not plain:
        return ""
    for part in _SENTENCE_SPLIT_RE.split(plain):
        part = part.strip()
        if len(part) >= 8:
            return part[:max_chars] + ("…" if len(part) > max_chars else "")
    return plain[:max_chars] + ("…" if len(plain) > max_chars else "")


def _context_snippet(content: str, keyword: str, width: int = 60) -> str:
    if not content:
        return ""
    plain = re.sub(r"\s+", " ", content).strip()
    idx = plain.lower().find(keyword.lower())
    if idx < 0:
        return plain[:width] + ("…" if len(plain) > width else "")
    start = max(0, idx - width // 2)
    end = min(len(plain), idx + len(keyword) + width // 2)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(plain) else ""
    return (
        prefix
        + plain[start:idx]
        + "<mark>"
        + plain[idx : idx + len(keyword)]
        + "</mark>"
        + plain[idx + len(keyword) : end]
        + suffix
    )
