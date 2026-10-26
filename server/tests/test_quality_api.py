"""quality API 测试（B1-03）"""

import sys
import pathlib

SERVER_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_ROOT))

import uuid
import pytest
from app.database import Base, engine, SessionLocal
from app.models import Document, DocumentQuality, utcnow

Base.metadata.create_all(engine)


class TestQualityAuth:
    def test_no_token_returns_401(self, client):
        resp = client.get("/api/quality/summary")
        assert resp.status_code == 401

    def test_wrong_token_returns_401(self, client):
        resp = client.get("/api/quality/summary", headers={"X-API-Token": "wrong"})
        assert resp.status_code == 401


class TestQualitySummary:
    def test_empty_db_summary(self, client):
        resp = client.get("/api/quality/summary", headers={"X-API-Token": "test-token"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 0
        assert data["min_content_chars"] == 500
        assert "counts" in data
        assert "stale" in data

    def test_summary_has_required_fields(self, client):
        resp = client.get("/api/quality/summary", headers={"X-API-Token": "test-token"})
        data = resp.json()
        for key in ("total", "audited", "stale", "min_content_chars", "counts", "duplicate_groups"):
            assert key in data


class TestQualityIssues:
    def test_invalid_issue_code_returns_400(self, client):
        resp = client.get(
            "/api/quality/issues?issue=bogus",
            headers={"X-API-Token": "test-token"},
        )
        assert resp.status_code == 400

    def test_empty_issues_list(self, client):
        resp = client.get(
            "/api/quality/issues?issue=all",
            headers={"X-API-Token": "test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "items" in data
        assert isinstance(data["items"], list)


class TestQualityAudit:
    def test_full_and_ids_conflict_returns_400(self, client):
        resp = client.post(
            "/api/quality/audit",
            json={"ids": ["x"], "full": True},
            headers={"X-API-Token": "test-token"},
        )
        assert resp.status_code == 400

    def test_too_many_ids_returns_400(self, client):
        ids = [str(uuid.uuid4()) for _ in range(501)]
        resp = client.post(
            "/api/quality/audit",
            json={"ids": ids},
            headers={"X-API-Token": "test-token"},
        )
        assert resp.status_code == 400

    def test_start_audit_returns_202(self, client):
        resp = client.post(
            "/api/quality/audit",
            json={"full": True},
            headers={"X-API-Token": "test-token"},
        )
        # 202 或 409（如果已有任务在跑）
        assert resp.status_code in (202, 409)

    def test_invalid_uuid_returns_400(self, client):
        resp = client.post(
            "/api/quality/audit",
            json={"ids": ["not-a-uuid"]},
            headers={"X-API-Token": "test-token"},
        )
        assert resp.status_code == 400


class TestAuditStatus:
    def test_status_returns_fields(self, client):
        resp = client.get("/api/quality/audit-status", headers={"X-API-Token": "test-token"})
        assert resp.status_code == 200
        data = resp.json()
        assert "name" in data
        assert "running" in data
        assert "total" in data
        assert "done" in data
        assert "error" in data


class TestQualityOrphans:
    def test_orphans_endpoint(self, client):
        resp = client.get("/api/quality/orphans", headers={"X-API-Token": "test-token"})
        assert resp.status_code == 200
        data = resp.json()
        assert "orphan_files" in data
        assert "orphan_covers" in data
        assert "missing_files" in data
        assert "total_bytes" in data
