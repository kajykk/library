"""书架排序白名单与 /documents/ids 契约测试（RW-304 / RW-601）。

排序键此前没有任何测试：非法 sort 静默回退、分页丢行、ids 截断口径
都只靠人工检查。这里为每个排序键与分页/截断行为钉死预期。
"""

import uuid
from datetime import datetime

from app.database import SessionLocal
from app.models import Document

API = "/api"
MARK = uuid.uuid4().hex[:8]


def _note(client, auth, suffix, author=""):
    title = f"排序{MARK}-{suffix}"
    resp = client.post(
        f"{API}/documents",
        headers=auth,
        json={"type": "note", "title": title, "content": f"{title} 正文", "author": author},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _set_fields(doc_id, **fields):
    with SessionLocal() as db:
        doc = db.get(Document, uuid.UUID(doc_id))
        assert doc is not None
        for key, value in fields.items():
            setattr(doc, key, value)
        db.commit()


def _titles(client, auth, sort=None, **params):
    query = {"q": MARK, "limit": 100, **params}
    if sort is not None:
        query["sort"] = sort
    resp = client.get(f"{API}/documents", headers=auth, params=query)
    assert resp.status_code == 200, resp.text
    return [d["title"].split("-")[-1] for d in resp.json()]


def _ids(client, auth, **params):
    resp = client.get(f"{API}/documents/ids", headers=auth, params={"q": MARK, **params})
    return resp


def test_sort_keys_and_invalid_value(client, auth_headers):
    a = _note(client, auth_headers, "A", author="乙")
    b = _note(client, auth_headers, "B", author="")
    c = _note(client, auth_headers, "C", author="甲")
    d = _note(client, auth_headers, "D", author="甲")

    base = datetime(2026, 1, 1, 0, 0, 0)  # noqa: DTZ001 — 模拟库里存的 naive 时间戳
    _set_fields(a["id"], created_at=base, last_read_at=None, read_progress=0.2, file_size=300)
    _set_fields(
        b["id"], created_at=base.replace(day=2), last_read_at=base.replace(day=6),
        read_progress=0.9, file_size=100,
    )
    _set_fields(
        c["id"], created_at=base.replace(day=3), last_read_at=base.replace(day=4),
        read_progress=0.5, file_size=200,
    )
    _set_fields(
        d["id"], created_at=base.replace(day=4), last_read_at=base.replace(day=5),
        read_progress=0.9, file_size=100,
    )

    assert _titles(client, auth_headers) == ["D", "C", "B", "A"]  # 默认 created_desc
    assert _titles(client, auth_headers, "created_desc") == ["D", "C", "B", "A"]
    assert _titles(client, auth_headers, "created_asc") == ["A", "B", "C", "D"]
    assert _titles(client, auth_headers, "title_asc") == ["A", "B", "C", "D"]
    # 空作者排最前；其余按 Unicode 码点（乙 U+4E59 < 甲 U+7532），同作者书名 tiebreak
    assert _titles(client, auth_headers, "author_asc") == ["B", "A", "C", "D"]
    # 未读（NULL）排最后
    assert _titles(client, auth_headers, "last_read_desc") == ["B", "D", "C", "A"]
    # 同进度按 id desc 兜底（id 随机，只断言分组与后续顺序）
    by_progress = _titles(client, auth_headers, "progress_desc")
    assert set(by_progress[:2]) == {"B", "D"}
    assert by_progress[2:] == ["C", "A"]
    by_size = _titles(client, auth_headers, "file_size_desc")
    assert by_size[:2] == ["A", "C"]
    assert set(by_size[2:]) == {"B", "D"}

    # 非法排序必须显式 400，不能静默回退默认值
    bad = client.get(
        f"{API}/documents", headers=auth_headers, params={"q": MARK, "sort": "id;drop"}
    )
    assert bad.status_code == 400
    assert "sort" in bad.text


def test_sort_pagination_has_stable_tiebreaker(client, auth_headers):
    titles = ["P1", "P2", "P3", "P4"]
    for suffix in titles:
        _note(client, auth_headers, f"{suffix}-{uuid.uuid4().hex[:4]}")

    first = _titles(client, auth_headers, "created_desc", limit=2, offset=0)
    second = _titles(client, auth_headers, "created_desc", limit=2, offset=2)
    assert len(first) == len(second) == 2
    assert set(first).isdisjoint(second)
    assert len(first + second) == 4

    # 同 created_at 时由 id desc 兜底，翻页不会重复
    same = datetime(2026, 2, 1, 0, 0, 0)  # noqa: DTZ001 — 同上，naive 落库值
    ids = [
        d["id"]
        for d in client.get(
            f"{API}/documents", headers=auth_headers, params={"q": MARK, "limit": 100}
        ).json()
    ]
    for doc_id in ids:
        _set_fields(doc_id, created_at=same)
    page1 = _titles(client, auth_headers, "created_desc", limit=2, offset=0)
    page2 = _titles(client, auth_headers, "created_desc", limit=2, offset=2)
    everyone = _titles(client, auth_headers, "created_desc", limit=100)
    assert page1 + page2 == everyone[:4]
    assert len(set(everyone)) == len(everyone)


def test_documents_ids_total_truncation_and_filters(client, auth_headers):
    marker = uuid.uuid4().hex[:8]
    first = _note(client, auth_headers, f"IDS-A-{marker}", author="甲")
    second = _note(client, auth_headers, f"IDS-B-{marker}", author="甲")
    third = _note(client, auth_headers, f"IDS-C-{marker}", author="乙")

    resp = client.get(
        f"{API}/documents/ids", headers=auth_headers, params={"q": marker}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 默认 created_desc：third 最新
    assert body["ids"] == [third["id"], second["id"], first["id"]]
    assert body["total"] == 3
    assert body["truncated"] is False

    # max 小于总数：返回前 max 个并标记截断
    limited = client.get(
        f"{API}/documents/ids", headers=auth_headers, params={"q": marker, "max": 2}
    ).json()
    assert limited["ids"] == [third["id"], second["id"]]
    assert limited["total"] == 3
    assert limited["truncated"] is True

    # 与列表共用筛选条件
    by_author = client.get(
        f"{API}/documents/ids", headers=auth_headers, params={"q": marker, "author": "甲"}
    ).json()
    assert by_author["ids"] == [second["id"], first["id"]]

    # 软删除后从 ids 结果中消失
    assert client.delete(f"{API}/documents/{third['id']}", headers=auth_headers).status_code == 204
    after = client.get(
        f"{API}/documents/ids", headers=auth_headers, params={"q": marker}
    ).json()
    assert third["id"] not in after["ids"]
    assert after["total"] == 2

    # max 越界：0 与超过硬上限都 422
    assert (
        client.get(
            f"{API}/documents/ids", headers=auth_headers, params={"q": marker, "max": 0}
        ).status_code
        == 422
    )
    assert (
        client.get(
            f"{API}/documents/ids", headers=auth_headers, params={"q": marker, "max": 5001}
        ).status_code
        == 422
    )
