"""运行时冒烟测试：针对已启动的 uvicorn (127.0.0.1:8000)"""

import httpx

BASE = "http://127.0.0.1:8000/api"
H = {"X-API-Token": "dev-token-123"}

with httpx.Client(base_url=BASE, headers=H, timeout=10) as c:
    content = "冒烟测试内容。".encode("utf-8") * 50
    files = {"file": ("冒烟测试书.txt", content, "text/plain")}
    r = c.post("/documents/upload", files=files)
    assert r.status_code == 201, r.text
    doc = r.json()
    print("upload:", doc["title"], doc["type"], doc["format"], doc["file_size"])

    r = c.get("/documents")
    assert r.status_code == 200 and any(d["id"] == doc["id"] for d in r.json())
    print("list: ok")

    r = c.get(f"/documents/{doc['id']}/file", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206, r.status_code
    assert r.headers["content-length"] == "10"
    print("range 206: ok,", r.headers["content-range"])

    r = c.patch(f"/documents/{doc['id']}", json={"read_progress": 0.42, "position": {"page": 5}, "last_read_at": "2026-08-17T12:00:00+00:00"})
    assert r.status_code == 200 and abs(r.json()["read_progress"] - 0.42) < 1e-9
    print("progress patch: ok")

    r = c.delete(f"/documents/{doc['id']}")
    assert r.status_code == 204
    r = c.get(f"/documents/{doc['id']}")
    assert r.status_code == 404
    print("soft delete: ok")
    print("SMOKE ALL OK")
