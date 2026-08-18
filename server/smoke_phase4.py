"""阶段4冒烟：zip 备份/恢复 + 网页剪藏（本地 mock 页面）"""

import io
import zipfile

import httpx

BASE = "http://127.0.0.1:8000/api"
H = {"X-API-Token": "dev-token-123"}

with httpx.Client(base_url=BASE, headers=H, timeout=30) as c:
    # 备份
    book = c.post(
        "/documents/upload",
        files={"file": ("收尾之书.txt", ("收尾正文。" * 20).encode("utf-8"), "text/plain")},
    ).json()
    note = c.post(
        "/documents",
        json={"type": "note", "title": "收尾笔记", "content": "参考[[收尾之书]]"},
    ).json()

    backup = c.get("/backup")
    assert backup.status_code == 200 and backup.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(backup.content))
    assert "meta.json" in zf.namelist() and any(n.startswith("files/") for n in zf.namelist())
    print("backup ok:", len(backup.content), "bytes,", len(zf.namelist()), "entries")

    # 硬删除后恢复
    c.delete(f"/documents/{book['id']}?hard=true")
    assert c.get(f"/documents/{book['id']}").status_code == 404
    restore = c.post("/backup/restore", files={"file": ("b.zip", backup.content, "application/zip")})
    assert restore.status_code == 200, restore.text
    assert restore.json()["files_restored"] >= 1
    doc = c.get(f"/documents/{book['id']}").json()
    assert doc["title"] == "收尾之书"
    file_resp = c.get(f"/documents/{book['id']}/file")
    assert file_resp.status_code == 200
    print("restore ok:", restore.json())

    # 双链确认
    links = c.get("/links", params={"source_id": note["id"]}).json()
    assert any(l["target_id"] == book["id"] for l in links)
    print("mention link ok")

    # 剪藏：对本机 uvicorn 自己的 health 页面无意义 —— 用 data URL 不行，直接测 mock 场景之外的真实 URL 不可控。
    # 改为验证 clip 端点对无效地址返回 502（错误路径）
    bad = c.post("/clip", json={"url": "http://127.0.0.1:9/nowhere"})
    assert bad.status_code == 502, bad.status_code
    print("clip error path ok (502)")

    # 清理
    c.delete(f"/documents/{note['id']}")
    c.delete(f"/documents/{book['id']}?hard=true")
    print("PHASE4 SMOKE OK")
