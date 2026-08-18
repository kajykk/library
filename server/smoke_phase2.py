"""阶段2冒烟：search / stats / annotations 高亮链路"""

import httpx

BASE = "http://127.0.0.1:8000/api"
H = {"X-API-Token": "dev-token-123"}

with httpx.Client(base_url=BASE, headers=H, timeout=10) as c:
    # 造一条笔记
    note = c.post(
        "/documents",
        json={"type": "note", "title": "阶段2冒烟笔记", "content": "知识管理是个人成长的核心能力。", "tags": ["冒烟"]},
    ).json()

    hits = c.get("/search", params={"q": "知识管理"}).json()
    assert any(h["id"] == note["id"] for h in hits), hits
    print("search hit:", hits[0]["title"], "|", hits[0]["snippet"][:30])

    stats = c.get("/stats").json()
    assert stats["total_documents"] >= 1 and "note" in stats["by_type"]
    assert any(t["name"] == "冒烟" for t in stats["top_tags"])
    print("stats ok: total =", stats["total_documents"], "| top_tags =", stats["top_tags"])

    # 划线批注链路
    hl = c.post(
        "/annotations",
        json={
            "document_id": note["id"],
            "type": "highlight",
            "anchor": {"chapter": 2, "page": 5},
            "quote": "知识管理",
            "content": "重点",
            "color": "yellow",
        },
    )
    assert hl.status_code == 201, hl.text
    anns = c.get("/annotations", params={"document_id": note["id"]}).json()
    assert any(a["type"] == "highlight" for a in anns)
    print("highlight ok:", anns[0]["quote"])

    # 清理
    c.delete(f"/documents/{note['id']}")
    print("PHASE2 SMOKE OK")
