"""批量导入：扫描指定目录下的 epub/pdf/mobi/azw3/txt，上传到本地后端

规则：
- 文件所在父目录名作为分类（Books/PDF/哲学心理学/xx.pdf → 分类"哲学心理学"）
- 幂等：已存在同名且同大小的文档则跳过
"""

import os
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000/api"
TOKEN = os.environ.get("KB_API_TOKEN", "change-me")
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("E:/library")
EXTS = {".epub", ".pdf", ".mobi", ".azw3", ".azw", ".txt"}

headers = {"X-API-Token": TOKEN}

with httpx.Client(base_url=BASE, headers=headers, timeout=300) as c:
    # 现有文档（按 标题+大小 去重）
    existing = {}
    for d in c.get("/documents").json():
        existing[(d["title"], d["file_size"])] = d["id"]

    # 分类列表
    collections = {col["name"]: col["id"] for col in c.get("/collections").json()}

    def ensure_collection(name: str) -> str:
        if name in collections:
            return collections[name]
        col = c.post("/collections", json={"name": name}).json()
        collections[name] = col["id"]
        return col["id"]

    files = [p for p in ROOT.rglob("*") if p.suffix.lower() in EXTS and p.is_file()]
    print(f"发现 {len(files)} 个书籍文件")

    ok, skip, fail = [], [], []
    start = time.time()
    for i, path in enumerate(files, 1):
        title_guess = path.stem
        size = path.stat().st_size
        # 幂等去重：标题（提取后的）可能与文件名不同，这里用文件名近似 + 大小匹配
        if (title_guess, size) in existing:
            skip.append(path.name)
            continue

        category = path.parent.name or "未分类"
        try:
            collection_id = ensure_collection(category)
            with open(path, "rb") as f:
                resp = c.post(
                    "/documents/upload",
                    files={"file": (path.name, f, "application/octet-stream")},
                    data={"collection_id": collection_id},
                )
            if resp.status_code == 201:
                doc = resp.json()
                existing[(doc["title"], doc["file_size"])] = doc["id"]
                ok.append((path.name, doc["title"]))
            else:
                fail.append((path.name, f"HTTP {resp.status_code}: {resp.text[:100]}"))
        except Exception as exc:
            fail.append((path.name, str(exc)[:150]))

        if i % 10 == 0 or i == len(files):
            print(f"[{i}/{len(files)}] 成功 {len(ok)} | 跳过 {len(skip)} | 失败 {len(fail)} | {time.time()-start:.0f}s")

    print("\n========== 导入完成 ==========")
    print(f"成功: {len(ok)}  跳过(已存在): {len(skip)}  失败: {len(fail)}  耗时: {time.time()-start:.0f}s")
    if fail:
        print("\n失败清单:")
        for name, err in fail:
            print(f"  - {name}: {err}")
