"""导入 API 的白名单与路径包含校验测试（RW-102）。

RD-02 是阻断级安全缺陷：旧实现直接 `Path(req.paths[i])` 复制客户端传来的绝对路径，
持 token 者可以把服务器上任何可读文件搬进书库，再经文件端点读回来。
本文件的用例都是冲着这条路径去的。
"""

import os
from pathlib import Path

import pytest

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import Document
from app.services import directory_scan
from app.services.file_storage import _data_root

Base.metadata.create_all(engine)

H = {"X-API-Token": "test-token"}


class _ScanSettings:
    def __init__(self, dirs, max_files: int = 20000):
        self.import_dirs = ",".join(str(d) for d in dirs)
        self.import_max_files = max_files


def _data_files() -> set[str]:
    root = _data_root()
    if not root.is_dir():
        return set()
    return {str(p) for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def whitelist(tmp_path, monkeypatch):
    """一个白名单目录（含子目录）+ 一个目录外的敏感文件。"""
    root = tmp_path / "inbox"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("甲书正文", encoding="utf-8")
    (root / "sub" / "b.txt").write_text("乙书正文", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("服务器上的敏感文件", encoding="utf-8")

    monkeypatch.setattr(directory_scan, "get_settings", lambda: _ScanSettings([root]))
    return {"root": root, "dir_id": directory_scan.make_dir_id(root), "secret": secret}


def _execute(client, body):
    return client.post("/api/imports/execute", json=body, headers=H)


class TestDirsAndScan:
    def test_unconfigured_whitelist_returns_empty(self, client, monkeypatch):
        """未配置白名单：dirs 空数组、scan 无候选，且不退化成接受任意路径。"""
        monkeypatch.setattr(directory_scan, "get_settings", lambda: _ScanSettings([]))
        dirs = client.get("/api/imports/dirs", headers=H)
        assert dirs.status_code == 200
        assert dirs.json()["dirs"] == []

        scan = client.get("/api/imports/scan", headers=H)
        assert scan.status_code == 200
        assert scan.json()["importable"] == []
        assert scan.json()["total"] == 0

    def test_execute_is_rejected_when_whitelist_empty(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(directory_scan, "get_settings", lambda: _ScanSettings([]))
        resp = _execute(client, {
            "dir_id": "whatever",
            "rel_paths": [str(tmp_path / "secret.txt")],
            "confirm": True,
        })
        assert resp.status_code == 400
        assert "白名单" in resp.json()["detail"]

    def test_scan_returns_relative_paths_only(self, client, whitelist):
        scan = client.get("/api/imports/scan", headers=H)
        assert scan.status_code == 200
        items = scan.json()["importable"]
        assert {i["rel_path"] for i in items} == {"a.txt", "sub/b.txt"}
        for item in items:
            assert item["dir_id"] == whitelist["dir_id"]
            # 绝对路径不出服务器：任何字段都不该带上白名单目录的全路径
            assert not any(str(whitelist["root"]) in str(v) for v in item.values())
            assert "path" not in item

    def test_dirs_exposes_id_label_and_flags(self, client, whitelist):
        dirs = client.get("/api/imports/dirs", headers=H).json()["dirs"]
        assert [d["id"] for d in dirs] == [whitelist["dir_id"]]
        assert dirs[0]["label"] == "inbox"
        assert dirs[0]["exists"] is True and dirs[0]["readable"] is True


class TestExecuteRejectsEscapes:
    def test_absolute_windows_path_returns_400_and_copies_nothing(self, client, whitelist):
        before = _data_files()
        resp = _execute(client, {
            "dir_id": whitelist["dir_id"],
            "rel_paths": ["C:\\Windows\\win.ini"],
            "confirm": True,
        })
        assert resp.status_code == 400
        assert "absolute_path" in resp.json()["detail"]
        assert _data_files() == before

    def test_absolute_posix_path_returns_400(self, client, whitelist):
        resp = _execute(client, {
            "dir_id": whitelist["dir_id"],
            "rel_paths": ["/etc/passwd"],
            "confirm": True,
        })
        assert resp.status_code == 400

    def test_traversal_returns_400(self, client, whitelist):
        before = _data_files()
        resp = _execute(client, {
            "dir_id": whitelist["dir_id"],
            "rel_paths": ["../secret.txt"],
            "confirm": True,
        })
        assert resp.status_code == 400
        assert "path_traversal" in resp.json()["detail"]
        assert _data_files() == before
        with SessionLocal() as db:
            assert db.query(Document).filter(Document.title == "secret").count() == 0

    def test_unknown_dir_id_returns_400(self, client, whitelist):
        resp = _execute(client, {
            "dir_id": "deadbeef0000",
            "rel_paths": ["a.txt"],
            "confirm": True,
        })
        assert resp.status_code == 400
        assert "dir_id" in resp.json()["detail"]

    def test_symlink_escape_is_refused_per_item(self, client, whitelist, tmp_path):
        """白名单里放一个指向外部的软链：realpath 后越界，逐项失败且不复制。"""
        link = whitelist["root"] / "link.txt"
        try:
            os.symlink(str(whitelist["secret"]), str(link))
        except (OSError, NotImplementedError):
            pytest.skip("当前环境不允许创建符号链接")
        before = _data_files()
        resp = _execute(client, {
            "dir_id": whitelist["dir_id"],
            "rel_paths": ["link.txt"],
            "confirm": True,
        })
        assert resp.status_code == 200
        assert resp.json()["failed"] == 1
        assert resp.json()["results"][0]["reason"] == "escapes_whitelist"
        assert _data_files() == before

    def test_confirm_is_required(self, client, whitelist):
        resp = _execute(client, {
            "dir_id": whitelist["dir_id"], "rel_paths": ["a.txt"], "confirm": False,
        })
        assert resp.status_code == 400

    def test_over_500_paths_rejected(self, client, whitelist):
        resp = _execute(client, {
            "dir_id": whitelist["dir_id"],
            "rel_paths": ["a.txt"] * 501,
            "confirm": True,
        })
        assert resp.status_code == 400


class TestExecuteHappyPath:
    def test_imports_selected_relative_paths(self, client, whitelist):
        resp = _execute(client, {
            "dir_id": whitelist["dir_id"],
            "rel_paths": ["a.txt", "sub/b.txt"],
            "confirm": True,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["imported"], body["skipped"], body["failed"]) == (2, 0, 0)
        assert {r["rel_path"] for r in body["results"]} == {"a.txt", "sub/b.txt"}

        with SessionLocal() as db:
            docs = db.query(Document).filter(Document.title.in_(["a", "b"])).all()
            assert len(docs) == 2
            by_title = {d.title: d for d in docs}
            assert by_title["a"].format == "txt"
            assert by_title["a"].type == "book"
            assert by_title["a"].file_path.startswith("files/")
            for d in docs:
                db.delete(d)
            db.commit()

    def test_missing_file_reports_failed_without_breaking_batch(self, client, whitelist):
        resp = _execute(client, {
            "dir_id": whitelist["dir_id"],
            "rel_paths": ["nope.txt", "a.txt"],
            "confirm": True,
        })
        body = resp.json()
        assert body["imported"] == 1 and body["failed"] == 1
        assert body["results"][0]["reason"] == "file_not_found"
        with SessionLocal() as db:
            for d in db.query(Document).filter(Document.title == "a").all():
                db.delete(d)
            db.commit()
