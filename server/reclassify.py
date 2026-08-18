"""按书名/作者关键词自动归类书籍（仅处理混杂分类里的书）

用法：
    python server/reclassify.py           # 预览分类计划
    python server/reclassify.py --apply   # 执行

规则：标题+作者命中关键词 → 归入对应分类（规则按优先级从上到下，
先命中的生效）；未命中的留在原分类。
"""

import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from app.services.auto_classify import RULES, classify

BASE = "http://127.0.0.1:8000/api"
TOKEN = os.environ.get("KB_API_TOKEN", "change-me")
headers = {"X-API-Token": TOKEN}

# 只处理这些"混杂"分类里的书；主题明确的分类（考研资料/学术论文等）不动
SRC_COLLECTIONS = {"book", "其他", "电子书籍", "library", "未分类", "文档", "Documents"}


def main():
    apply = "--apply" in sys.argv
    with httpx.Client(base_url=BASE, headers=headers, timeout=120) as c:
        cols = c.get("/collections").json()
        col_by_name = {col["name"]: col for col in cols}
        col_id_by_name = {col["name"]: col["id"] for col in cols}
        col_name_by_id = {col["id"]: col["name"] for col in cols}

        missing = [t for t, _ in RULES if t not in col_id_by_name]
        if missing:
            print(f"警告：目标分类不存在，将跳过：{missing}")

        docs = c.get("/documents").json()
        plan = []  # (doc, target)
        untouched = []
        for d in docs:
            src = col_name_by_id.get(d.get("collection_id")) or "未分类"
            if src not in SRC_COLLECTIONS:
                continue
            target = classify(d["title"] or "", d.get("author") or "")
            if target and target in col_id_by_name and col_name_by_id.get(d["collection_id"]) != target:
                plan.append((d, target))
            else:
                untouched.append(d)

        print(f"源分类 {sorted(SRC_COLLECTIONS)} 中共 {len(plan) + len(untouched)} 本")
        print(f"将归类 {len(plan)} 本，保持不动 {len(untouched)} 本\n")

        from collections import Counter
        target_count = Counter(t for _, t in plan)
        print("== 归类分布 ==")
        for name, n in target_count.most_common():
            print(f"  -> {name}: {n}")
        print("\n== 明细（前 60）==")
        for d, t in plan[:60]:
            print(f"  [{t}] {d['title'][:50]}")

        if not apply:
            print("\n仅预览。确认后加 --apply 执行")
            return

        ok, fail = 0, 0
        for d, t in plan:
            r = c.patch(f"/documents/{d['id']}", json={"collection_id": col_id_by_name[t]})
            if r.status_code == 200:
                ok += 1
            else:
                fail += 1
                print(f"  失败 {d['title'][:40]}: HTTP {r.status_code}")
        print(f"\n完成：归类 {ok} 本，失败 {fail} 本")


if __name__ == "__main__":
    main()
