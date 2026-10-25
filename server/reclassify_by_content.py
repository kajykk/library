"""按内容自动归类：对混杂分类的书重新提取元数据（含简介），标题+作者+简介匹配

用法：
    python server/reclassify_by_content.py                 # 预览（简介模式）
    python server/reclassify_by_content.py --apply         # 执行
    python server/reclassify_by_content.py --fulltext      # 预览（正文模式：提取正文前40KB，词频打分）
    python server/reclassify_by_content.py --fulltext --apply
"""

import os
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from app.services.auto_classify import classify, classify_by_content  # noqa: E402
from app.services.metadata_extract import extract_metadata_from_file  # noqa: E402

BASE = "http://127.0.0.1:8000/api"
TOKEN = os.environ.get("KB_API_TOKEN", "change-me")
headers = {"X-API-Token": TOKEN}
DATA_DIR = Path(os.environ.get("KB_DATA_DIR", str(Path(__file__).parent / "data")))

SRC_COLLECTIONS = {"book", "其他", "电子书籍", "library", "未分类", "文档", "Documents"}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

MAX_SAMPLE = 40000


def extract_text_sample(fmt: str, path: Path, max_chars: int = MAX_SAMPLE) -> str:
    """提取正文前 max_chars 字符；扫描件/失败返回空串"""
    out = []
    total = 0

    def push(text: str):
        nonlocal total
        need = max_chars - total
        if need <= 0:
            return False
        t = _WS_RE.sub(" ", text)
        out.append(t[:need])
        total += len(t[:need])
        return total >= max_chars

    try:
        if fmt == "epub":
            import zipfile
            with zipfile.ZipFile(path) as zf:
                names = sorted(n for n in zf.namelist()
                               if n.lower().endswith((".html", ".xhtml", ".htm")))
                for name in names:
                    raw = zf.read(name).decode("utf-8", "ignore")
                    if push(_TAG_RE.sub(" ", raw)):
                        break
        elif fmt == "pdf":
            from pypdf import PdfReader
            with open(path, "rb") as fh:
                reader = PdfReader(fh)
                for i in range(min(len(reader.pages), 20)):
                    try:
                        t = reader.pages[i].extract_text() or ""
                    except Exception:
                        continue
                    if push(t):
                        break
        elif fmt == "txt":
            push(path.read_text("utf-8", "ignore"))
        elif fmt == "mobi":
            # 优先用 mobi 库解包（Huffman/PalmDOC 压缩），失败退回 latin-1 直读
            try:
                from app.services.content_extract import _extract_mobi
                t = _extract_mobi(path)
                if t:
                    push(t)
            except Exception:
                raw = path.read_bytes()
                txt = _TAG_RE.sub(" ", raw.decode("latin-1", "ignore"))
                push(txt)
    except Exception:
        return ""

    return " ".join(out)


def main():
    apply = "--apply" in sys.argv
    fulltext = "--fulltext" in sys.argv
    mode = "正文词频" if fulltext else "简介"
    with httpx.Client(base_url=BASE, headers=headers, timeout=120) as c:
        cols = c.get("/collections").json()
        col_id_by_name = {col["name"]: col["id"] for col in cols}
        col_name_by_id = {col["id"]: col["name"] for col in cols}

        docs = c.get("/documents").json()
        candidates = [
            d for d in docs
            if (col_name_by_id.get(d.get("collection_id")) or "未分类") in SRC_COLLECTIONS
        ]
        print(f"混杂分类 {len(candidates)} 本，{mode}模式...")

        plan = []  # (doc, target)
        skipped = []
        for i, d in enumerate(candidates, 1):
            rel = d.get("file_path") or ""
            path = DATA_DIR / rel if rel else None
            if not path or not path.is_file():
                skipped.append((d["title"], "文件缺失"))
                continue
            fmt = (d.get("format") or rel.rsplit(".", 1)[-1] if rel else "") or ""
            meta = extract_metadata_from_file(rel.rsplit("/", 1)[-1], fmt, path)
            title = meta.get("title") or d["title"]
            author = meta.get("author") or d.get("author") or ""
            if fulltext:
                sample = extract_text_sample(fmt, path)
                extra = sample[:60]
                target = classify_by_content(title, author, sample)
            else:
                extra = meta.get("description") or ""
                target = classify(title, author, extra)
            if target and target in col_id_by_name:
                plan.append((d, target, title, author, extra[:60]))
            else:
                skipped.append((d["title"], "无主题特征"))

            if i % 50 == 0 or i == len(candidates):
                print(f"  [{i}/{len(candidates)}] 命中 {len(plan)} | 未命中 {len(skipped)}")

        print(f"\n== 将归类 {len(plan)} 本 ==")
        from collections import Counter
        for name, n in Counter(t for _, t, *_ in plan).most_common():
            print(f"  -> {name}: {n}")

        print("\n== 明细（前 80）==")
        for d, t, title, author, extra in plan[:80]:
            print(f"  [{t}] {title[:45]} | {extra[:35]}")

        if not apply:
            print("\n仅预览。确认后加 --apply 执行")
            return

        ok, fail = 0, 0
        for d, t, *_ in plan:
            r = c.patch(f"/documents/{d['id']}", json={"collection_id": col_id_by_name[t]})
            if r.status_code == 200:
                ok += 1
            else:
                fail += 1
                print(f"  失败 {d['title'][:40]}: HTTP {r.status_code}")
        print(f"\n完成：归类 {ok} 本，失败 {fail} 本")


if __name__ == "__main__":
    main()