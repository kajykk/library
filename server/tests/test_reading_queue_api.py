"""阅读队列 API 契约测试（RW-302 / RW-601）。

覆盖：入队/去重/范围夹取、排序与分页、状态机、重排校验、
auto-add 的适用性判定、next 与删除。此前这些端点没有一条集成测试。
"""

import io
import uuid

import pytest

from app.database import SessionLocal
from app.models import ReadingQueueItem

API = "/api"
_COUNTER = [0]


@pytest.fixture(autouse=True)
def _empty_queue():
    """队列是全局资源：每个用例前后清空，断言不受其他用例残留影响。"""
    def clear():
        with SessionLocal() as db:
            db.query(ReadingQueueItem).delete()
            db.commit()

    clear()
    yield
    clear()


def _book(client, auth, title, progress=0.3):
    _COUNTER[0] += 1
    content = f"{title} 正文 #{_COUNTER[0]}\n" * 10
    resp = client.post(
        f"{API}/documents/upload",
        headers=auth,
        files={"file": (f"{title}.txt", io.BytesIO(content.encode("utf-8")), "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    if progress is not None:
        doc = client.patch(
            f"{API}/documents/{doc['id']}", headers=auth, json={"read_progress": progress}
        ).json()
    return doc


def _note(client, auth, title):
    resp = client.post(
        f"{API}/documents",
        headers=auth,
        json={"type": "note", "title": title, "content": "队列测试笔记"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _add(client, auth, doc_id, priority=100, note=""):
    return client.post(
        f"{API}/reading-queue",
        headers=auth,
        json={"document_id": doc_id, "priority": priority, "note": note},
    )


def _list(client, auth, **params):
    resp = client.get(f"{API}/reading-queue", headers=auth, params={"limit": 200, **params})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_add_dedupes_clamps_and_reports_missing(client, auth_headers):
    book = _book(client, auth_headers, "队列入队书")
    created = _add(client, auth_headers, book["id"], priority=-5, note="x" * 600)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["created"] is True
    item = body["item"]
    assert item["priority"] == 0
    assert len(item["note"]) == 500
    assert item["status"] == "active"
    assert item["sort_order"] == 0
    assert item["document"]["id"] == book["id"]
    assert item["document"]["read_progress"] == 0.3

    # 重复入队：200 + created=false + 原条目
    duplicate = _add(client, auth_headers, book["id"], priority=999)
    assert duplicate.status_code == 200
    assert duplicate.json()["created"] is False
    assert duplicate.json()["item"]["id"] == item["id"]
    assert duplicate.json()["item"]["priority"] == 0

    other = _book(client, auth_headers, "队列夹取书")
    clamped = _add(client, auth_headers, other["id"], priority=5000)
    assert clamped.status_code == 201
    assert clamped.json()["item"]["priority"] == 1000

    missing = _add(client, auth_headers, str(uuid.uuid4()))
    assert missing.status_code == 404


def test_list_order_status_filter_and_pagination(client, auth_headers):
    a = _book(client, auth_headers, "队列排序甲")
    b = _book(client, auth_headers, "队列排序乙")
    c = _book(client, auth_headers, "队列排序丙")
    _add(client, auth_headers, a["id"], priority=50)
    _add(client, auth_headers, b["id"], priority=10)
    _add(client, auth_headers, c["id"], priority=50)

    body = _list(client, auth_headers)
    assert [i["document"]["id"] for i in body["items"]] == [b["id"], a["id"], c["id"]]
    assert body["total"] == 3

    # 状态过滤：paused 为空
    paused = _list(client, auth_headers, status="paused")
    assert paused["total"] == 0 and paused["items"] == []

    item_a = body["items"][1]
    patched = client.patch(
        f"{API}/reading-queue/{item_a['id']}", headers=auth_headers, json={"status": "paused"}
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "paused"

    active_ids = [i["document"]["id"] for i in _list(client, auth_headers)["items"]]
    assert active_ids == [b["id"], c["id"]]
    all_ids = [i["document"]["id"] for i in _list(client, auth_headers, status="all")["items"]]
    assert all_ids == [b["id"], a["id"], c["id"]]

    # 分页切片（status=all 覆盖含暂停项的全集）
    page1 = _list(client, auth_headers, status="all", limit=2, offset=0)
    page2 = _list(client, auth_headers, status="all", limit=2, offset=2)
    assert len(page1["items"]) == 2
    assert len(page2["items"]) == 1
    assert [i["id"] for i in page1["items"] + page2["items"]] == [
        i["id"] for i in _list(client, auth_headers, status="all")["items"]
    ]

    # 非法 status 显式 400
    assert (
        client.get(
            f"{API}/reading-queue", headers=auth_headers, params={"status": "bogus"}
        ).status_code
        == 400
    )


def test_patch_validates_fields_and_clamps(client, auth_headers):
    book = _book(client, auth_headers, "队列修改书")
    item = _add(client, auth_headers, book["id"]).json()["item"]

    empty = client.patch(
        f"{API}/reading-queue/{item['id']}", headers=auth_headers, json={}
    )
    assert empty.status_code == 400

    bad_status = client.patch(
        f"{API}/reading-queue/{item['id']}", headers=auth_headers, json={"status": "bogus"}
    )
    assert bad_status.status_code == 400

    patched = client.patch(
        f"{API}/reading-queue/{item['id']}",
        headers=auth_headers,
        json={"priority": -10, "note": "y" * 600, "status": "done"},
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["priority"] == 0
    assert len(body["note"]) == 500
    assert body["status"] == "done"

    missing = client.patch(
        f"{API}/reading-queue/{uuid.uuid4()}", headers=auth_headers, json={"note": "x"}
    )
    assert missing.status_code == 404


def test_reorder_same_priority_and_validation(client, auth_headers):
    docs = [_book(client, auth_headers, f"队列重排{i}") for i in range(3)]
    items = [_add(client, auth_headers, d["id"]).json()["item"] for d in docs]
    a, b, c = items

    resp = client.post(
        f"{API}/reading-queue/reorder",
        headers=auth_headers,
        json={"ordered_ids": [c["id"], a["id"], b["id"]]},
    )
    assert resp.status_code == 200, resp.text
    reordered = resp.json()["items"]
    assert [i["id"] for i in reordered] == [c["id"], a["id"], b["id"]]
    assert [i["sort_order"] for i in reordered] == [0, 1, 2]

    # 重排只写 sort_order，列表顺序随之更新
    listed = _list(client, auth_headers)["items"]
    assert [i["id"] for i in listed] == [c["id"], a["id"], b["id"]]

    assert (
        client.post(
            f"{API}/reading-queue/reorder", headers=auth_headers, json={"ordered_ids": []}
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"{API}/reading-queue/reorder",
            headers=auth_headers,
            json={"ordered_ids": [a["id"], a["id"]]},
        ).status_code
        == 400
    )
    missing = str(uuid.uuid4())
    missing_resp = client.post(
        f"{API}/reading-queue/reorder",
        headers=auth_headers,
        json={"ordered_ids": [a["id"], missing]},
    )
    assert missing_resp.status_code == 400
    assert missing_resp.json()["detail"]["missing_ids"] == [missing]

    # 暂停项不允许参与重排
    client.patch(
        f"{API}/reading-queue/{b['id']}", headers=auth_headers, json={"status": "paused"}
    )
    inactive = client.post(
        f"{API}/reading-queue/reorder",
        headers=auth_headers,
        json={"ordered_ids": [a["id"], b["id"]]},
    )
    assert inactive.status_code == 400
    assert inactive.json()["detail"]["inactive_ids"] == [b["id"]]


def test_auto_add_applicability_and_idempotency(client, auth_headers):
    book = _book(client, auth_headers, "自动入队书", progress=0.4)
    created = client.post(
        f"{API}/reading-queue/auto-add",
        headers=auth_headers,
        json={"document_id": book["id"]},
    )
    assert created.status_code == 201, created.text
    assert created.json()["created"] is True
    assert created.json()["item"]["priority"] == 100

    again = client.post(
        f"{API}/reading-queue/auto-add",
        headers=auth_headers,
        json={"document_id": book["id"]},
    )
    assert again.status_code == 200
    assert again.json()["skipped"] == "exists"

    finished = _book(client, auth_headers, "已读完不入队", progress=1.0)
    not_applicable = client.post(
        f"{API}/reading-queue/auto-add",
        headers=auth_headers,
        json={"document_id": finished["id"]},
    )
    assert not_applicable.status_code == 200
    assert not_applicable.json()["skipped"] == "not_applicable"

    note = _note(client, auth_headers, "笔记不入队")
    note_resp = client.post(
        f"{API}/reading-queue/auto-add",
        headers=auth_headers,
        json={"document_id": note["id"]},
    )
    assert note_resp.status_code == 200
    assert note_resp.json()["skipped"] == "not_applicable"

    missing = client.post(
        f"{API}/reading-queue/auto-add",
        headers=auth_headers,
        json={"document_id": str(uuid.uuid4())},
    )
    assert missing.status_code == 200
    assert missing.json()["skipped"] == "not_applicable"

    deleted = _book(client, auth_headers, "已删除不入队")
    client.delete(f"{API}/documents/{deleted['id']}", headers=auth_headers)
    deleted_resp = client.post(
        f"{API}/reading-queue/auto-add",
        headers=auth_headers,
        json={"document_id": deleted["id"]},
    )
    assert deleted_resp.status_code == 200
    assert deleted_resp.json()["skipped"] == "not_applicable"


def test_next_skips_paused_and_returns_204_when_empty(client, auth_headers):
    empty = client.get(f"{API}/reading-queue/next", headers=auth_headers)
    assert empty.status_code == 204

    high = _book(client, auth_headers, "下一本低优先")
    low = _book(client, auth_headers, "下一本高优先")
    high_item = _add(client, auth_headers, high["id"], priority=200).json()["item"]
    low_item = _add(client, auth_headers, low["id"], priority=10).json()["item"]

    first = client.get(f"{API}/reading-queue/next", headers=auth_headers)
    assert first.status_code == 200
    assert first.json()["id"] == low_item["id"]

    client.patch(
        f"{API}/reading-queue/{low_item['id']}", headers=auth_headers, json={"status": "paused"}
    )
    second = client.get(f"{API}/reading-queue/next", headers=auth_headers)
    assert second.json()["id"] == high_item["id"]

    client.delete(f"{API}/reading-queue/{high_item['id']}", headers=auth_headers)
    assert client.get(f"{API}/reading-queue/next", headers=auth_headers).status_code == 204


def test_remove_item(client, auth_headers):
    book = _book(client, auth_headers, "队列删除书")
    item = _add(client, auth_headers, book["id"]).json()["item"]

    assert (
        client.delete(f"{API}/reading-queue/{item['id']}", headers=auth_headers).status_code
        == 204
    )
    assert _list(client, auth_headers)["total"] == 0
    assert (
        client.delete(f"{API}/reading-queue/{item['id']}", headers=auth_headers).status_code
        == 404
    )
    assert (
        client.delete(f"{API}/reading-queue/{uuid.uuid4()}", headers=auth_headers).status_code
        == 404
    )
