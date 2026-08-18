"""阶段3冒烟：双链同步 / backlinks / graph"""

import httpx

BASE = "http://127.0.0.1:8000/api"
H = {"X-API-Token": "dev-token-123"}

with httpx.Client(base_url=BASE, headers=H, timeout=10) as c:
    book = c.post(
        "/documents/upload",
        files={"file": ("深度工作.txt", ("深度工作正文。" * 30).encode("utf-8"), "text/plain")},
    ).json()

    note = c.post(
        "/documents",
        json={"type": "note", "title": "阅读计划", "content": "本周精读[[深度工作]]并输出笔记"},
    ).json()

    # 双链自动建立
    links = c.get("/links", params={"source_id": note["id"]}).json()
    assert any(l["target_id"] == book["id"] and l["type"] == "mention" for l in links), links
    print("mention link ok:", links)

    # 反向链接
    backlinks = c.get(f"/documents/{book['id']}/backlinks").json()
    assert any(b["title"] == "阅读计划" for b in backlinks), backlinks
    print("backlinks ok:", backlinks)

    # 图谱
    graph = c.get("/links/graph").json()
    assert len(graph["nodes"]) >= 2 and len(graph["edges"]) >= 1
    print(f"graph ok: {len(graph['nodes'])} nodes / {len(graph['edges'])} edges")

    # 清理
    c.delete(f"/documents/{note['id']}")
    c.delete(f"/documents/{book['id']}?hard=true")
    print("PHASE3 SMOKE OK")
