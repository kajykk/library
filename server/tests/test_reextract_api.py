"""重提取 API 契约测试（RW-401 / RW-601）。

只覆盖端点契约：confirm、ids 校验、去重、缺失/软删、单飞 409、status 形状。
真实后台提取已在 R4 用临时库探针 42 例验证（REWORK §7.7），这里 monkeypatch
掉线程启动，避免测试依赖 PDF 解析耗时与外部工具。
"""

import io
import uuid

import pytest

from app.services import reextract_job

API = "/api"
_COUNTER = [0]


def _txt(client, auth, title="重提取"):
    _COUNTER[0] += 1
    unique = f"{title}-{_COUNTER[0]}"
    content = f"{unique} 正文\n" * 20
    resp = client.post(
        f"{API}/documents/upload",
        headers=auth,
        files={"file": (f"{unique}.txt", io.BytesIO(content.encode("utf-8")), "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
def start_spy(monkeypatch):
    calls = []

    def fake_start(ids, timeout_seconds, overwrite):
        calls.append(
            {"ids": list(ids), "timeout": timeout_seconds, "overwrite": overwrite}
        )
        return True

    monkeypatch.setattr(reextract_job, "start", fake_start)
    return calls


def _start(client, auth, ids, **extra):
    return client.post(
        f"{API}/documents/reextract",
        headers=auth,
        json={"confirm": True, "ids": ids, **extra},
    )


def test_requires_confirm(client, auth_headers):
    doc = _txt(client, auth_headers)
    resp = client.post(
        f"{API}/documents/reextract",
        headers=auth_headers,
        json={"ids": [doc["id"]]},
    )
    assert resp.status_code == 400
    assert "confirm" in resp.json()["detail"]


def test_rejects_empty_ids(client, auth_headers):
    resp = _start(client, auth_headers, [])
    assert resp.status_code == 400
    assert "不能为空" in resp.json()["detail"]


def test_rejects_invalid_uuid(client, auth_headers):
    resp = _start(client, auth_headers, ["not-a-uuid"])
    assert resp.status_code == 400
    assert "非法 UUID" in resp.json()["detail"]


def test_rejects_more_than_500_ids(client, auth_headers):
    resp = _start(client, auth_headers, [str(uuid.uuid4()) for _ in range(501)])
    assert resp.status_code == 400
    assert "最多 500" in resp.json()["detail"]


def test_missing_and_soft_deleted_report_missing_ids(client, auth_headers):
    doc = _txt(client, auth_headers)
    assert (
        client.delete(f"{API}/documents/{doc['id']}", headers=auth_headers).status_code
        == 204
    )
    absent = str(uuid.uuid4())
    resp = _start(client, auth_headers, [doc["id"], absent])
    assert resp.status_code == 400
    missing = resp.json()["detail"]["missing_ids"]
    assert missing == [str(uuid.UUID(doc["id"])), str(uuid.UUID(absent))]


def test_dedupes_and_passes_options(client, auth_headers, start_spy):
    doc = _txt(client, auth_headers)
    resp = _start(
        client,
        auth_headers,
        [doc["id"], doc["id"]],
        timeout_seconds=7,
        overwrite="always",
    )
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"status": "started", "total": 1}
    assert len(start_spy) == 1
    assert start_spy[0]["ids"] == [uuid.UUID(doc["id"])]
    assert start_spy[0]["timeout"] == 7
    assert start_spy[0]["overwrite"] == "always"


def test_conflict_when_job_already_running(client, auth_headers, monkeypatch):
    doc = _txt(client, auth_headers)
    monkeypatch.setattr(reextract_job, "start", lambda *args, **kwargs: False)
    resp = _start(client, auth_headers, [doc["id"]])
    assert resp.status_code == 409


def test_status_shape(client, auth_headers, monkeypatch):
    snapshot = {
        "name": "reextract",
        "running": False,
        "total": 3,
        "done": 3,
        "updated": 2,
        "skipped": 1,
        "empty": 0,
        "failed": 0,
        "current_title": None,
        "error": None,
        "started_at": "2026-10-27T00:00:00",
        "finished_at": "2026-10-27T00:01:00",
    }
    monkeypatch.setattr(reextract_job, "status", lambda: snapshot)
    resp = client.get(f"{API}/documents/reextract-status", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == snapshot
