"""documents/batch 契约测试（RW-301 / RW-601）。

此前批量端点没有任何集成测试：confirm 门禁、部分缺失的报错形状、
标签合并与撤销都只靠人工点验。这里逐条钉死。
"""

import uuid

API = "/api"


def _note(client, auth, title, **extra):
    payload = {"type": "note", "title": title, "content": f"{title} 正文", **extra}
    resp = client.post(f"{API}/documents", headers=auth, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _get(client, auth, doc_id):
    resp = client.get(f"{API}/documents/{doc_id}", headers=auth)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _collection(client, auth, name):
    resp = client.post(f"{API}/collections", headers=auth, json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _batch_update(client, auth, ids, patch, confirm=True):
    return client.patch(
        f"{API}/documents/batch",
        headers=auth,
        json={"ids": ids, "patch": patch, "confirm": confirm},
    )


def test_batch_update_validates_confirm_ids_and_patch(client, auth_headers):
    doc = _note(client, auth_headers, f"批量校验-{uuid.uuid4().hex[:8]}")

    # confirm 门禁
    missing_confirm = _batch_update(
        client, auth_headers, [doc["id"]], {"language": "en"}, confirm=False
    )
    assert missing_confirm.status_code == 400
    assert "confirm" in missing_confirm.text

    # 空 ids / 非法 UUID / 超 500
    assert _batch_update(client, auth_headers, [], {"language": "en"}).status_code == 400
    assert (
        _batch_update(client, auth_headers, ["not-a-uuid"], {"language": "en"}).status_code
        == 400
    )
    too_many = [str(uuid.uuid4()) for _ in range(501)]
    assert (
        _batch_update(client, auth_headers, too_many, {"language": "en"}).status_code
        == 400
    )

    # 空 patch
    empty_patch = _batch_update(client, auth_headers, [doc["id"]], {})
    assert empty_patch.status_code == 400
    assert "patch" in empty_patch.text

    # 部分文档不存在：必须报出缺失 id，而不是静默跳过
    missing_id = str(uuid.uuid4())
    missing = _batch_update(
        client, auth_headers, [doc["id"], missing_id], {"language": "en"}
    )
    assert missing.status_code == 400
    detail = missing.json()["detail"]
    assert detail["missing_ids"] == [missing_id]

    # 目标分类不存在
    bad_collection = _batch_update(
        client, auth_headers,
        [doc["id"]], {"collection_id": str(uuid.uuid4())},
    )
    assert bad_collection.status_code == 400

    # 校验失败不得产生副作用
    assert _get(client, auth_headers, doc["id"])["language"] != "en"


def test_batch_update_merges_tags_and_can_undo(client, auth_headers):
    marker = uuid.uuid4().hex[:8]
    coll_id = _collection(client, auth_headers, f"批量分类-{marker}")
    first = _note(client, auth_headers, f"批量更新甲-{marker}", tags=["旧标签", "保留"])
    second = _note(client, auth_headers, f"批量更新乙-{marker}", tags=["旧标签"])
    before = {doc["id"]: _get(client, auth_headers, doc["id"]) for doc in (first, second)}

    resp = _batch_update(
        client,
        auth_headers,
        [first["id"], second["id"]],
        {
            "collection_id": coll_id,
            "language": "en",
            "add_tags": ["新标签", " 新标签 ", "保留"],
            "remove_tags": ["旧标签"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == 2

    first_after = _get(client, auth_headers, first["id"])
    assert first_after["collection_id"] == coll_id
    assert first_after["language"] == "en"
    # 去重、去空白后合并；remove 优先于 add 的结果
    assert sorted(t["name"] for t in first_after["tags"]) == ["保留", "新标签"]
    second_after = _get(client, auth_headers, second["id"])
    assert sorted(t["name"] for t in second_after["tags"]) == ["保留", "新标签"]

    # 操作日志保存 before/after，供撤销比对
    op = client.get(f"{API}/operations/{body['operation_id']}", headers=auth_headers)
    assert op.status_code == 200
    op_body = op.json()
    assert op_body["kind"] == "batch_update"
    assert op_body["document_count"] == 2
    assert op_body["before_state"]["docs"][first["id"]]["tags"] == ["保留", "旧标签"]

    # 撤销：分类、语言、标签全部回到操作前
    undo = client.post(
        f"{API}/operations/{body['operation_id']}/undo",
        headers=auth_headers,
        json={"confirm": True},
    )
    assert undo.status_code == 200, undo.text
    assert undo.json() == {"status": "undone", "restored": 2, "skipped": []}

    for doc_id, original in before.items():
        restored = _get(client, auth_headers, doc_id)
        assert restored["collection_id"] == original["collection_id"]
        assert restored["language"] == original["language"]
        assert sorted(t["name"] for t in restored["tags"]) == sorted(
            t["name"] for t in original["tags"]
        )

    # 重复撤销被拒绝
    again = client.post(
        f"{API}/operations/{body['operation_id']}/undo",
        headers=auth_headers,
        json={"confirm": True},
    )
    assert again.status_code == 400


def test_batch_update_can_clear_collection(client, auth_headers):
    marker = uuid.uuid4().hex[:8]
    coll_id = _collection(client, auth_headers, f"批量置空-{marker}")
    doc = _note(
        client, auth_headers, f"批量置空-{marker}", collection_id=coll_id
    )
    resp = _batch_update(
        client, auth_headers, [doc["id"]], {"collection_id": None}
    )
    assert resp.status_code == 200, resp.text
    assert _get(client, auth_headers, doc["id"])["collection_id"] is None


def test_batch_soft_delete_and_undo(client, auth_headers):
    marker = uuid.uuid4().hex[:8]
    docs = [
        _note(client, auth_headers, f"批量删除甲-{marker}"),
        _note(client, auth_headers, f"批量删除乙-{marker}"),
    ]
    ids = [doc["id"] for doc in docs]

    no_confirm = client.post(
        f"{API}/documents/batch/delete",
        headers=auth_headers,
        json={"ids": ids, "confirm": False},
    )
    assert no_confirm.status_code == 400

    resp = client.post(
        f"{API}/documents/batch/delete",
        headers=auth_headers,
        json={"ids": ids, "confirm": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] == 2

    for doc_id in ids:
        assert client.get(f"{API}/documents/{doc_id}", headers=auth_headers).status_code == 404
    with_deleted = client.get(
        f"{API}/documents",
        headers=auth_headers,
        params={"q": "批量删除", "include_deleted": True},
    ).json()
    assert all(
        next(d for d in with_deleted if d["id"] == doc_id)["deleted_at"] for doc_id in ids
    )

    # 重复删除已删除项：计数为 0，不报错
    repeat = client.post(
        f"{API}/documents/batch/delete",
        headers=auth_headers,
        json={"ids": ids, "confirm": True},
    )
    assert repeat.status_code == 200
    assert repeat.json()["deleted"] == 0

    # 撤销软删除
    undo = client.post(
        f"{API}/operations/{body['operation_id']}/undo",
        headers=auth_headers,
        json={"confirm": True},
    )
    assert undo.status_code == 200, undo.text
    assert undo.json()["restored"] == 2
    for doc_id in ids:
        assert client.get(f"{API}/documents/{doc_id}", headers=auth_headers).status_code == 200
