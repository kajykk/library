"""孤儿隔离的按选择移动与真撤销测试（RW-103）。

RD-03 的旧实现有三处致命问题：无视客户端勾选把整个孤儿集合一次搬走、
用 `Path(rel).name` 压平目录结构、`except: pass` 静默吞掉失败，
而 `_undo_orphan_quarantine` 只把日志标成 undone 却不动文件。
本文件的用例逐条对着这三处写。
"""

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

import pytest

from app.database import Base, SessionLocal, engine
from app.models import Document, utcnow
from app.services.file_storage import _data_root
from app.services import orphan_quarantine

Base.metadata.create_all(engine)

H = {"X-API-Token": "test-token"}
MARK = f"oq{uuid.uuid4().hex[:6]}"


def _files_dir() -> Path:
    d = _data_root() / "files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _quarantine_root() -> Path:
    return _data_root() / "quarantine"


@pytest.fixture
def orphans():
    """临时 data 目录里放 3 个无引用的孤儿文件，用例结束后一律清干净。"""
    names = [f"{MARK}_1.txt", f"{MARK}_2.txt", f"{MARK}_3.txt"]
    contents = {n: f"孤儿文件内容 {n}" for n in names}
    for n in names:
        _files_dir().joinpath(n).write_text(contents[n], encoding="utf-8")
    rels = [f"files/{n}" for n in names]

    yield {"names": names, "rels": rels, "contents": contents}

    for n in names:
        _files_dir().joinpath(n).unlink(missing_ok=True)
    # 隔离副本可能散落在多个时间戳目录里，按文件名回收
    root = _quarantine_root()
    if root.is_dir():
        for p in root.rglob("*"):
            if p.is_file() and p.name in names:
                p.unlink(missing_ok=True)


def _post(client, rel_paths, confirm=True):
    return client.post(
        "/api/documents/orphans/quarantine",
        json={"rel_paths": rel_paths, "confirm": confirm},
        headers=H,
    )


def _manifest(qdir_rel: str) -> dict:
    path = _data_root() / Path(*qdir_rel.split("/")) / orphan_quarantine.MANIFEST_NAME
    return json.loads(path.read_text(encoding="utf-8"))


def _results(body: dict) -> dict:
    return {r["rel_path"]: r for r in body["results"]}


class TestSelectionIsHonoured:
    def test_only_checked_files_move(self, client, orphans):
        """勾 2 个只动 2 个：第 3 个既不被顺手搬走，也不出现在 manifest 里。"""
        chosen = orphans["rels"][:2]
        resp = _post(client, chosen)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["moved"], body["skipped"], body["failed"]) == (2, 0, 0)

        for rel in chosen:
            assert not (_data_root() / Path(*rel.split("/"))).exists()
        leftover = orphans["rels"][2]
        assert (_data_root() / Path(*leftover.split("/"))).is_file()

        assert body["operation_id"]
        manifest = _manifest(body["quarantine_dir"])
        assert [i["rel_path"] for i in manifest["items"]] == chosen

    def test_moved_to_keeps_directory_structure(self, client, orphans):
        """禁止压平：moved_to 必须保留 files/ 这一段。"""
        resp = _post(client, orphans["rels"][:1])
        body = resp.json()
        item = _results(body)[orphans["rels"][0]]
        assert item["moved_to"] == f"{body['quarantine_dir']}/{orphans['rels'][0]}"
        assert (item["moved_to"].count("/")) >= 2
        assert (_data_root() / Path(*item["moved_to"].split("/"))).is_file()

    def test_manifest_records_size_sha_and_time(self, client, orphans):
        resp = _post(client, orphans["rels"][:1])
        body = resp.json()
        rel = orphans["rels"][0]
        item = next(i for i in _manifest(body["quarantine_dir"])["items"] if i["rel_path"] == rel)
        raw = orphans["contents"][orphans["names"][0]].encode("utf-8")
        assert item["status"] == "moved"
        assert item["size"] == len(raw)
        assert item["sha256"] == hashlib.sha256(raw).hexdigest()
        assert item["moved_at"]

    def test_operation_log_after_state_carries_moves(self, client, orphans):
        resp = _post(client, orphans["rels"][:1])
        body = resp.json()
        log = client.get(f"/api/operations/{body['operation_id']}", headers=H).json()
        assert log["kind"] == "orphan_quarantine"
        assert log["undoable"] is True


class TestRejectionsAndSkips:
    def test_missing_confirm_is_400_and_moves_nothing(self, client, orphans):
        resp = _post(client, orphans["rels"], confirm=False)
        assert resp.status_code == 400
        for rel in orphans["rels"]:
            assert (_data_root() / Path(*rel.split("/"))).is_file()

    @pytest.mark.parametrize(
        "bad",
        [
            "C:\\Windows\\win.ini",
            "/etc/passwd",
            "../secret.txt",
            "files/../files/x.txt",
            "covers/xxx.jpg",
            "",
        ],
    )
    def test_illegal_shapes_reject_whole_batch(self, client, orphans, bad):
        """形状非法（绝对路径/穿越/非 files 目录）整批 400，且一个文件都不动。"""
        resp = _post(client, [orphans["rels"][0], bad])
        assert resp.status_code == 400, resp.text
        assert (_data_root() / Path(*orphans["rels"][0].split("/"))).is_file()

    def test_empty_selection_is_400(self, client, orphans):
        assert _post(client, []).status_code == 400

    def test_over_capacity_is_400(self, client, orphans):
        paths = [f"files/{MARK}_{i}.txt" for i in range(501)]
        assert _post(client, paths).status_code == 400

    def test_total_size_cap_is_400(self, client, orphans, monkeypatch):
        monkeypatch.setattr(orphan_quarantine, "MAX_TOTAL_BYTES", 1)
        resp = _post(client, orphans["rels"][:1])
        assert resp.status_code == 400
        assert "字节" in resp.json()["detail"]

    def test_referenced_file_is_skipped_not_orphan(self, client, orphans):
        """文档（含软删除）仍引用的文件不是孤儿：skipped=not_orphan，文件留在原地。"""
        rel = orphans["rels"][0]
        db = SessionLocal()
        try:
            doc = Document(
                id=uuid.uuid4(),
                type="book",
                title=f"{MARK} 引用中的文档",
                content="",
                file_path=rel,
                format="txt",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            db.add(doc)
            db.commit()
            resp = _post(client, [rel])
            assert resp.status_code == 200
            body = resp.json()
            assert body["moved"] == 0
            assert _results(body)[rel]["reason"] == "not_orphan"
            assert (_data_root() / Path(*rel.split("/"))).is_file()
            # 什么都没移动 → 不留隔离目录，也不写撤销日志
            assert body["quarantine_dir"] is None
            assert body["operation_id"] is None
        finally:
            db.delete(doc)
            db.commit()
            db.close()

    def test_absent_file_is_skipped(self, client, orphans):
        resp = _post(client, [f"files/{MARK}_不存在的.txt"])
        assert resp.status_code == 200
        body = resp.json()
        assert body["skipped"] == 1
        assert body["operation_id"] is None

    def test_symlink_is_rejected(self, client, orphans):
        link = _files_dir() / f"{MARK}_link.txt"
        target = _files_dir() / f"{MARK}_target.txt"
        target.write_text("真实文件", encoding="utf-8")
        try:
            os.symlink(str(target), str(link))
        except (OSError, NotImplementedError):
            pytest.skip("当前环境不支持创建符号链接")
        try:
            resp = _post(client, [f"files/{link.name}"])
            assert resp.status_code == 400
            assert "符号链接" in resp.json()["detail"]
            assert target.is_file()
        finally:
            link.unlink(missing_ok=True)
            target.unlink(missing_ok=True)


class TestNoOverwrite:
    def test_existing_destination_is_skipped(self, client, orphans, monkeypatch):
        """隔离目录里同名文件已存在 → skipped，既不覆盖旧副本也不移动新文件。"""
        rel = orphans["rels"][0]
        name = orphans["names"][0]
        monkeypatch.setattr(orphan_quarantine, "_timestamp", lambda: "20261027_000000")
        dest_dir = _quarantine_root() / "20261027_000000" / "files"
        dest_dir.mkdir(parents=True, exist_ok=True)
        sentinel = dest_dir / name
        sentinel.write_text("上一次运行留下的副本", encoding="utf-8")

        resp = _post(client, [rel])
        assert resp.status_code == 200
        body = resp.json()
        assert (body["moved"], body["skipped"]) == (0, 1)
        assert _results(body)[rel]["reason"] == "destination_exists"
        assert sentinel.read_text(encoding="utf-8") == "上一次运行留下的副本"
        assert (_data_root() / Path(*rel.split("/"))).is_file()

    def test_same_second_runs_get_different_dirs(self, client, orphans, monkeypatch):
        """同秒两次运行不能共用目录：第一份 manifest 是撤销依据，不能被覆盖。"""
        monkeypatch.setattr(orphan_quarantine, "_timestamp", lambda: "20261027_120000")
        first = _post(client, orphans["rels"][:1]).json()
        second = _post(client, orphans["rels"][1:2]).json()
        assert first["quarantine_dir"] == "quarantine/20261027_120000"
        assert second["quarantine_dir"] == "quarantine/20261027_120000-2"
        assert _manifest(first["quarantine_dir"])["items"][0]["rel_path"] == orphans["rels"][0]


class TestUndo:
    def _undo(self, client, operation_id):
        return client.post(
            f"/api/operations/{operation_id}/undo", json={"confirm": True}, headers=H
        )

    def test_undo_puts_files_back_at_original_paths(self, client, orphans):
        chosen = orphans["rels"][:2]
        body = _post(client, chosen).json()
        qdir = body["quarantine_dir"]
        for rel in chosen:
            assert not (_data_root() / Path(*rel.split("/"))).exists()

        resp = self._undo(client, body["operation_id"])
        assert resp.status_code == 200, resp.text
        outcome = resp.json()
        assert outcome["status"] == "undone"
        assert outcome["restored"] == 2
        assert outcome["skipped"] == []

        for rel in chosen:
            back = _data_root() / Path(*rel.split("/"))
            assert back.is_file()
            assert back.read_text(encoding="utf-8") == orphans["contents"][Path(rel).name]
        # 隔离目录里不再留有这两份副本
        assert list((_data_root() / Path(*qdir.split("/"))).rglob("*.txt")) == []
        assert _manifest(qdir)["items"][0]["status"] == "restored"

    def test_undo_twice_is_400(self, client, orphans):
        body = _post(client, orphans["rels"][:1]).json()
        assert self._undo(client, body["operation_id"]).status_code == 200
        resp = self._undo(client, body["operation_id"])
        assert resp.status_code == 400
        assert "已撤销" in resp.json()["detail"]

    def test_undo_skips_when_original_path_occupied(self, client, orphans):
        """原位置已被别的文件占用 → skipped，绝不覆盖，日志保持 applied 以便重试。"""
        rel = orphans["rels"][0]
        name = orphans["names"][0]
        body = _post(client, [rel]).json()
        (_data_root() / "files").joinpath(name).write_text("后来者", encoding="utf-8")

        resp = self._undo(client, body["operation_id"])
        assert resp.status_code == 200
        outcome = resp.json()
        assert outcome["status"] == "partial"
        assert outcome["restored"] == 0
        assert outcome["skipped"][0]["reason"] == "original_exists"
        assert (_data_root() / "files").joinpath(name).read_text(encoding="utf-8") == "后来者"
        assert _manifest(body["quarantine_dir"])["items"][0]["status"] == "moved"

        # 日志保持 applied，移开占用者后还能再撤销一次
        assert client.get(f"/api/operations/{body['operation_id']}", headers=H).json()["status"] == "applied"
        (_data_root() / "files").joinpath(name).unlink()
        again = self._undo(client, body["operation_id"])
        assert again.status_code == 200
        assert again.json()["restored"] == 1
        assert again.json()["status"] == "undone"
        log = client.get(f"/api/operations/{body['operation_id']}", headers=H).json()
        assert log["status"] == "undone"

    def test_undo_refuses_when_manifest_lost(self, client, orphans):
        body = _post(client, orphans["rels"][:1]).json()
        shutil.rmtree(_data_root() / Path(*body["quarantine_dir"].split("/")), ignore_errors=True)
        resp = self._undo(client, body["operation_id"])
        assert resp.status_code == 400
        assert "not undoable" in resp.json()["detail"]
