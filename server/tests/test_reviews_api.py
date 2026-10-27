"""复习 API 契约测试（RW-302 / RW-303 / RW-601）。

覆盖：due 卡片口径与 document/new_limit 限制、四档评分与错误分支、
snooze、stats 的时区偏移与本地日界字段。此前只有标注挂钩测试间接触及 due。
"""

import uuid
from datetime import datetime, timedelta, timezone

API = "/api"


def _note(client, auth, prefix):
    title = f"{prefix}-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        f"{API}/documents",
        headers=auth,
        json={"type": "note", "title": title, "content": f"{title} 正文"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _annotate(client, auth, doc_id, type_="highlight", quote="关键句"):
    resp = client.post(
        f"{API}/annotations",
        headers=auth,
        json={
            "document_id": doc_id,
            "type": type_,
            "anchor": {"chapter": 1, "text": quote},
            "quote": quote,
            "content": "",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _due(client, auth, **params):
    resp = client.get(f"{API}/reviews/due", headers=auth, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _aware(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def test_due_cards_filter_document_and_annotation_type(client, auth_headers):
    doc = _note(client, auth_headers, "复习待办")
    highlight = _annotate(client, auth_headers, doc["id"], "highlight", "高亮")
    note_ann = _annotate(client, auth_headers, doc["id"], "note", "笔记")
    bookmark = _annotate(client, auth_headers, doc["id"], "bookmark", "书签")

    body = _due(client, auth_headers, document_id=doc["id"])
    assert body["total"] == 2
    ids = {card["annotation_id"] for card in body["cards"]}
    assert ids == {highlight["id"], note_ann["id"]}
    assert bookmark["id"] not in ids

    card = next(c for c in body["cards"] if c["annotation_id"] == highlight["id"])
    assert card["document_id"] == doc["id"]
    assert card["document_title"] == doc["title"]
    assert card["type"] == "highlight"
    assert card["quote"] == "高亮"
    assert card["interval_days"] == 0
    assert card["repetitions"] == 0
    assert card["ease"] == 2.5
    assert card["due_at"] is not None
    assert set(card["preview"]) == {"again", "hard", "good", "easy"}
    assert all(isinstance(v, str) and v for v in card["preview"].values())

    # 其他文档的卡不会混入 document_id 过滤结果
    other = _note(client, auth_headers, "复习待办其他")
    other_ann = _annotate(client, auth_headers, other["id"])
    assert other_ann["id"] not in {
        c["annotation_id"] for c in _due(client, auth_headers, document_id=doc["id"])["cards"]
    }

    # 软删除文档的卡全部消失
    client.delete(f"{API}/documents/{doc['id']}", headers=auth_headers)
    after = _due(client, auth_headers, document_id=doc["id"])
    assert after["total"] == 0
    assert after["cards"] == []


def test_due_new_limit_and_query_validation(client, auth_headers):
    doc = _note(client, auth_headers, "复习新卡上限")
    for index in range(3):
        _annotate(client, auth_headers, doc["id"], quote=f"句{index}")

    limited = _due(client, auth_headers, document_id=doc["id"], new_limit=2)
    assert limited["total"] == 3
    assert len(limited["cards"]) == 2

    # limit 只约束旧卡；全是新卡时不受 limit 影响
    all_cards = _due(client, auth_headers, document_id=doc["id"], limit=1, new_limit=10)
    assert len(all_cards["cards"]) == 3

    assert (
        client.get(
            f"{API}/reviews/due", headers=auth_headers, params={"limit": 101}
        ).status_code
        == 422
    )
    assert (
        client.get(
            f"{API}/reviews/due", headers=auth_headers, params={"new_limit": 0}
        ).status_code
        == 422
    )
    assert (
        client.get(
            f"{API}/reviews/due", headers=auth_headers, params={"document_id": "not-a-uuid"}
        ).status_code
        == 422
    )


def test_rate_good_again_easy_and_error_paths(client, auth_headers):
    doc = _note(client, auth_headers, "复习评分")
    good_ann = _annotate(client, auth_headers, doc["id"])

    good = client.post(
        f"{API}/reviews/{good_ann['id']}/rate", headers=auth_headers, json={"rating": "good"}
    )
    assert good.status_code == 200, good.text
    good_body = good.json()
    assert good_body["interval_days"] == 1
    assert good_body["repetitions"] == 1
    assert good_body["lapses"] == 0
    assert good_body["ease"] == 2.5
    assert timedelta(hours=23) < _aware(good_body["due_at"]) - datetime.now(timezone.utc) < timedelta(hours=25)
    assert set(good_body["next_preview"]) == {"again", "hard", "good", "easy"}

    again = client.post(
        f"{API}/reviews/{good_ann['id']}/rate", headers=auth_headers, json={"rating": "again"}
    )
    assert again.status_code == 200, again.text
    again_body = again.json()
    assert again_body["interval_days"] == 0
    assert again_body["repetitions"] == 0
    assert again_body["lapses"] == 1
    assert again_body["ease"] == 2.3
    assert timedelta(minutes=9) < _aware(again_body["due_at"]) - datetime.now(timezone.utc) < timedelta(minutes=11)

    easy_ann = _annotate(client, auth_headers, doc["id"], quote="第二句")
    easy = client.post(
        f"{API}/reviews/{easy_ann['id']}/rate", headers=auth_headers, json={"rating": "easy"}
    )
    assert easy.status_code == 200
    assert easy.json()["interval_days"] == 4
    assert easy.json()["repetitions"] == 1
    assert easy.json()["ease"] == 2.65

    assert (
        client.post(
            f"{API}/reviews/{uuid.uuid4()}/rate", headers=auth_headers, json={"rating": "good"}
        ).status_code
        == 404
    )

    bookmark = _annotate(client, auth_headers, doc["id"], "bookmark", "书签")
    stopped = client.post(
        f"{API}/reviews/{bookmark['id']}/rate", headers=auth_headers, json={"rating": "good"}
    )
    assert stopped.status_code == 400
    assert "书签" in stopped.text

    deleted_doc = _note(client, auth_headers, "复习已删文档")
    deleted_ann = _annotate(client, auth_headers, deleted_doc["id"])
    client.delete(f"{API}/documents/{deleted_doc['id']}", headers=auth_headers)
    assert (
        client.post(
            f"{API}/reviews/{deleted_ann['id']}/rate",
            headers=auth_headers,
            json={"rating": "good"},
        ).status_code
        == 404
    )

    assert (
        client.post(
            f"{API}/reviews/{easy_ann['id']}/rate",
            headers=auth_headers,
            json={"rating": "perfect"},
        ).status_code
        == 422
    )


def test_snooze_schedule_and_validation(client, auth_headers):
    doc = _note(client, auth_headers, "复习延后")
    ann = _annotate(client, auth_headers, doc["id"])

    snoozed = client.post(
        f"{API}/reviews/{ann['id']}/snooze", headers=auth_headers, json={"days": 3}
    )
    assert snoozed.status_code == 200, snoozed.text
    body = snoozed.json()
    assert body["interval_days"] == 0
    assert body["repetitions"] == 0
    assert timedelta(days=2, hours=23) < _aware(body["due_at"]) - datetime.now(timezone.utc) < timedelta(days=3, hours=1)
    assert set(body["next_preview"]) == {"again", "hard", "good", "easy"}

    assert (
        client.post(
            f"{API}/reviews/{ann['id']}/snooze", headers=auth_headers, json={"days": 2}
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"{API}/reviews/{uuid.uuid4()}/snooze", headers=auth_headers, json={"days": 1}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{API}/reviews/{ann['id']}/snooze", headers=auth_headers, json={}
        ).status_code
        == 422
    )


def test_stats_shape_local_day_and_tz_validation(client, auth_headers):
    doc = _note(client, auth_headers, "复习统计")
    ann = _annotate(client, auth_headers, doc["id"])

    stats = client.get(
        f"{API}/reviews/stats", headers=auth_headers, params={"tz_offset_minutes": 480}
    )
    assert stats.status_code == 200, stats.text
    body = stats.json()
    assert body["tz_offset_minutes"] == 480
    for key in ("due_now", "due_today", "new_today", "reviewed_today", "upcoming_7d"):
        assert isinstance(body[key], int)
        assert body[key] >= 0
    assert body["due_now"] >= 1
    assert body["new_today"] >= 1
    assert body["upcoming_7d"] >= body["due_today"]

    client.post(
        f"{API}/reviews/{ann['id']}/rate", headers=auth_headers, json={"rating": "good"}
    )
    today = client.get(
        f"{API}/reviews/stats", headers=auth_headers, params={"tz_offset_minutes": 0}
    ).json()
    assert today["reviewed_today"] >= 1

    assert (
        client.get(
            f"{API}/reviews/stats", headers=auth_headers, params={"tz_offset_minutes": 900}
        ).status_code
        == 422
    )
    assert (
        client.get(
            f"{API}/reviews/stats", headers=auth_headers, params={"tz_offset_minutes": -900}
        ).status_code
        == 422
    )
