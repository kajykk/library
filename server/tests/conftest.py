"""必须在导入 app 之前设置环境变量（模块级 engine 在导入时初始化）"""

import os
import sys
import tempfile
import pathlib

SERVER_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_ROOT))

_TMP = tempfile.mkdtemp(prefix="kb-test-")
os.environ["KB_DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["KB_DATA_DIR"] = str(pathlib.Path(_TMP) / "data")
os.environ["KB_API_TOKEN"] = "test-token"
os.environ["KB_AUTO_CREATE_TABLES"] = "1"
os.environ["KB_CORS_ORIGINS"] = "http://localhost:3000"

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def auth_headers():
    return {"X-API-Token": "test-token"}
