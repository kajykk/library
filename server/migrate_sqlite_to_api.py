"""旧 SQLite（knowledge_base.db）→ 后端 API 一次性迁移

用法：
    $env:KB_API_TOKEN = "change-me"
    python server/migrate_sqlite_to_api.py

流程：
    1) 读取旧库元数据（分类/书籍/阅读进度）
    2) POST /api/migrate/meta 一次性提交元数据
    3) 逐本 POST /api/migrate/file 上传原始文件
    4) 逐本 POST /api/migrate/cover 上传封面
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000/api"
TOKEN = os.environ.get("KB_API_TOKEN", "change-me")
SQLITE = Path(__file__).parent / "knowledge_base.db"
DATA_DIR = Path(__file__).parent / "data"

headers = {"X-API-Token": TOKEN}


def to_ms(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(s).timestamp() * 1000)
    except ValueError:
        return None


def sanitize(s: str | None) -> str:
    if not s:
        return ""
    return "".join(ch for ch in s if ch >= " " and ch != "\x7f") or ""


def dedup_books(books):
    """按 (title, file_size) 去重，每组保留第一条；返回 (保留列表, 被跳过列表)"""
    seen = {}
    removed = []
    for b in books:
        key = (b["title"], b["fileSize"])
        if key in seen:
            removed.append(b)
        else:
            seen[key] = b
    return list(seen.values()), removed


def load_old_db():
    con = sqlite3.connect(str(SQLITE))
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    categories = [
        {"id": r["id"], "name": r["name"], "sortOrder": r["sort_order"] or 0}
        for r in cur.execute("SELECT id, name, sort_order FROM collections ORDER BY sort_order, name")
    ]
    name_to_id = {r["name"]: r["id"] for r in cur.execute("SELECT id, name FROM collections")}

    rows = cur.execute(
        "SELECT * FROM documents WHERE file_path IS NOT NULL AND file_path != ''"
    ).fetchall()

    books = []
    file_map = {}
    cover_map = {}
    skipped = []
    for r in rows:
        fmt = (r["format"] or "").lower()
        cat_id = r["collection_id"]
        cat_name = None
        for name, cid in name_to_id.items():
            if cid == cat_id:
                cat_name = name
                break
        cat_name = cat_name or "未分类"

        position = None
        if r["position"]:
            try:
                pos = json.loads(r["position"])
                position = pos.get("page")
            except (json.JSONDecodeError, AttributeError):
                pass

        books.append({
            "id": r["id"],
            "title": sanitize(r["title"]) or "未命名",
            "author": sanitize(r["author"]),
            "publisher": sanitize(r["publisher"]),
            "language": sanitize(r["language"]) or "zh",
            "isbn": sanitize(r["isbn"]),
            "description": sanitize(r["description"]),
            "format": fmt,
            "fileSize": r["file_size"] or 0,
            "category": cat_name,
            "tags": [],
            # 旧库 read_progress 为 0~1，meta 接口会 /100，故 ×100 传回
            "readProgress": int(round((r["read_progress"] or 0) * 100)),
            "lastPosition": position,
            "lastReadAt": to_ms(r["last_read_at"]),
            "addedAt": to_ms(r["created_at"]),
            "totalPages": 0,
        })

        f = DATA_DIR / r["file_path"]
        if f.is_file():
            file_map[r["id"]] = f
        else:
            skipped.append((r["id"], r["title"], "文件缺失: " + str(r["file_path"])))

        if r["cover_path"]:
            c = DATA_DIR / r["cover_path"]
            if c.is_file():
                cover_map[r["id"]] = c

    notes = [
        {"id": r["id"], "title": r["title"], "content": r["content"] or ""}
        for r in cur.execute(
            "SELECT * FROM documents WHERE file_path IS NULL OR file_path = ''"
        )
    ]
    con.close()
    return categories, books, file_map, cover_map, skipped, notes


def clean_api_duplicates(c: httpx.Client, dry_run: bool = False):
    """验证并清理 API 中 (标题+文件大小) 完全相同的重复文档"""
    docs = c.get("/documents").json()
    print(f"API 共 {len(docs)} 个文档，按 (标题, 大小) 分组验证...")

    groups: dict[tuple, list[dict]] = {}
    for d in docs:
        groups.setdefault((d["title"], d.get("file_size") or 0), []).append(d)

    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    to_delete = []
    for (title, size), items in dup_groups.items():
        items.sort(key=lambda d: d.get("created_at") or "")
        keep, remove = items[0], items[1:]
        print(f"  x{len(items)}  {title[:50]} ({size} B) -> 保留 {keep['id'][:8]}..., 删除 {len(remove)} 条")
        to_delete.extend(remove)

    print(f"\n重复组: {len(dup_groups)} | 待删除: {len(to_delete)}")
    if not to_delete:
        print("无重复，无需清理")
        return

    if dry_run:
        print("--dry-run：仅验证，未执行删除")
        return

    ok, fail = 0, 0
    for d in to_delete:
        r = c.delete(f"/documents/{d['id']}", params={"hard": True})
        if r.status_code == 204:
            ok += 1
        else:
            fail += 1
            print(f"  删除失败 {d['id']}: HTTP {r.status_code}")
    print(f"清理完成：删除 {ok} 条（含文件），失败 {fail} 条")


def main():
    force = "--force" in sys.argv
    no_dedup = "--no-dedup" in sys.argv
    clean_mode = "--clean-api-dups" in sys.argv
    dry_run = "--dry-run" in sys.argv

    with httpx.Client(base_url=BASE, headers=headers, timeout=httpx.Timeout(900.0, connect=30.0)) as c:
        if clean_mode:
            clean_api_duplicates(c, dry_run=dry_run)
            return

        existing = c.get("/documents").json()
        if existing and not force:
            print(f"API 已有 {len(existing)} 个文档，为避免重复插入请确认后加 --force 重跑")
            sys.exit(1)

        print("读取旧库...")
        categories, books, file_map, cover_map, skipped, notes = load_old_db()
        print(f"分类 {len(categories)} 个 | 书籍 {len(books)} 本 | 封面 {len(cover_map)} 张 | 缺失文件 {len(skipped)} | 笔记 {len(notes)} 条")

        if no_dedup:
            print("--no-dedup：不做去重，忠实 1:1 迁移")
        else:
            books, dup_removed = dedup_books(books)
            if dup_removed:
                print(f"去重：跳过 {len(dup_removed)} 本（标题+大小相同的重复书）")
                for b in dup_removed[:20]:
                    print(f"  [重复] {b['title'][:60]} ({b['fileSize']} B)")
                for b in dup_removed:
                    file_map.pop(b["id"], None)
                    cover_map.pop(b["id"], None)

        for s in skipped:
            print(f"  [缺失] {s[1]} ({s[2]})")
        for n in notes:
            print(f"  [笔记-不迁移] {n['title']}")

        # 1) 元数据
        print("提交元数据...")
        t0 = time.time()
        payload = {"categories": categories, "books": books, "bookmarks": [], "notes": [], "readingRecords": []}
        resp = c.post("/migrate/meta", json=payload)
        resp.raise_for_status()
        result = resp.json()
        id_map = {item["old_id"]: item["new_id"] for item in result["documents"]}
        print(f"  分类 {result['collections']} | 文档 {len(id_map)} | 耗时 {time.time()-t0:.1f}s")

        # 2) 文件
        ok, fail = [], []
        total = len(file_map)
        for i, (old_id, path) in enumerate(file_map.items(), 1):
            new_id = id_map.get(old_id)
            if not new_id:
                fail.append((path.name, "未映射到新文档"))
                continue
            try:
                with open(path, "rb") as f:
                    r = c.post(
                        "/migrate/file",
                        data={"doc_id": new_id},
                        files={"file": (path.name, f, "application/octet-stream")},
                    )
                if r.status_code == 204:
                    ok.append(path.name)
                else:
                    fail.append((path.name, f"HTTP {r.status_code}: {r.text[:80]}"))
            except Exception as exc:
                fail.append((path.name, str(exc)[:120]))
            if i % 20 == 0 or i == total:
                print(f"  [文件 {i}/{total}] 成功 {len(ok)} | 失败 {len(fail)} | {time.time()-t0:.0f}s")

        # 3) 封面
        cok, cfail = [], []
        total_c = len(cover_map)
        for i, (old_id, path) in enumerate(cover_map.items(), 1):
            new_id = id_map.get(old_id)
            if not new_id:
                continue
            try:
                with open(path, "rb") as f:
                    r = c.post(
                        "/migrate/cover",
                        data={"doc_id": new_id},
                        files={"file": (path.name, f, "image/jpeg")},
                    )
                if r.status_code == 204:
                    cok.append(path.name)
                else:
                    cfail.append((path.name, f"HTTP {r.status_code}"))
            except Exception as exc:
                cfail.append((path.name, str(exc)[:120]))
            if i % 50 == 0 or i == total_c:
                print(f"  [封面 {i}/{total_c}] 成功 {len(cok)} | 失败 {len(cfail)}")

        print("\n========== 迁移完成 ==========")
        print(f"文档: {len(id_map)} | 文件: {len(ok)} 成功 / {len(fail)} 失败 | 封面: {len(cok)} 成功 / {len(cfail)} 失败 | 总耗时 {time.time()-t0:.0f}s")
        if fail:
            print("文件失败清单（前 20）:")
            for name, err in fail[:20]:
                print(f"  - {name}: {err}")


if __name__ == "__main__":
    main()
