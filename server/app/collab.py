"""实时协作：pycrdt-websocket（Yjs 协议）+ SQLite 持久化

- 每个文档一个协作房间：/ws/collab/doc-<uuid>
- 更新写入 data/collab.db（yupdates/ycheckpoints），房间冷启动时回放
- 认证：query 参数或 header 中的 X-API-Token
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs

from pycrdt import Doc
from pycrdt.store.sqlite import SQLiteYStore
from pycrdt.websocket import ASGIServer, WebsocketServer, exception_logger
from pycrdt.websocket.yroom import YRoom

from .config import get_settings

logger = logging.getLogger("kb.collab")

ROOM_PATTERN = re.compile(r"^(/ws/collab)?/?doc-[0-9a-fA-F-]{1,64}$")

SCHEMA_VERSION = 2


class CollabStore(SQLiteYStore):
    """指向固定库文件的 YStore 实例（每个文档用 path 区分）"""

    def __init__(self, path: str, db_path: str):
        self.db_path = db_path
        super().__init__(path=path, log=logger)


def init_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS yupdates "
            "(path TEXT NOT NULL, yupdate BLOB, metadata BLOB, timestamp REAL NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_yupdates_path_timestamp ON yupdates (path, timestamp)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ycheckpoints "
            "(path TEXT NOT NULL PRIMARY KEY, checkpoint BLOB NOT NULL, timestamp REAL NOT NULL)"
        )
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _apply_stored_updates(db_path: Path, doc_path: str, ydoc: Doc) -> None:
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT yupdate FROM yupdates WHERE path = ? ORDER BY timestamp",
                (doc_path,),
            ).fetchall()
    except sqlite3.Error:
        return
    for (update,) in rows:
        if update:
            try:
                ydoc.apply_update(update)
            except Exception:
                logger.warning("协作历史回放失败: %s", doc_path)


class CollabWebsocketServer(WebsocketServer):
    def __init__(self, db_path: Path):
        super().__init__(
            rooms_ready=True,
            auto_clean_rooms=False,
            exception_handler=exception_logger,
            log=logger,
        )
        self.db_path = db_path
        init_db(db_path)

    async def get_room(self, name: str) -> YRoom:
        if name not in self.rooms:
            doc_path = name.rsplit("/", 1)[-1]
            ydoc = Doc()
            _apply_stored_updates(self.db_path, doc_path, ydoc)
            store = CollabStore(path=doc_path, db_path=str(self.db_path))
            self.rooms[name] = YRoom(
                ready=True,
                ystore=store,
                ydoc=ydoc,
                exception_handler=exception_logger,
                log=logger,
            )
        room = self.rooms[name]
        await self.start_room(room)
        return room


def _reject_unauthorized(message: dict, scope: dict) -> bool:
    settings = get_settings()
    token = None
    query = parse_qs(scope.get("query_string", b"").decode())
    token = (query.get("token") or [None])[0]
    if not token:
        for name, value in scope.get("headers", []):
            if name == b"x-api-token":
                token = value.decode("latin-1")
                break
    path = scope.get("path", "")
    if token != settings.api_token:
        logger.warning("协作连接被拒绝（Token 无效）: %s", path)
        return True
    if not ROOM_PATTERN.match(path):
        logger.warning("协作连接被拒绝（非法房间名）: %s", path)
        return True
    return False


def build_collab_asgi(data_dir: str) -> ASGIServer:
    data_path = Path(data_dir)
    data_path.mkdir(parents=True, exist_ok=True)
    db_path = data_path / "collab.db"
    server = CollabWebsocketServer(db_path)
    return server, ASGIServer(server, on_connect=_reject_unauthorized)
