"""2026-10-04 代码审查第一批修复的回归测试。

每条测试对应审查报告里的一条结论，目的是把"修复过"钉死：
- S2  FTS 初始化不再依赖 auto_create_tables
- C1  naive datetime 按 UTC 标注而非按本地时区换算
- S5  下载头文件名不含 CRLF；标题写入侧拒绝控制字符
- C2  列表接口的 limit 有硬上限，不再"不传即全表"
"""

import io
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app.routers.documents import file_download_headers
from app.routers.stats import _build_day_map, _to_utc
from app import services
from app.services import search_index as si

API = "/api"


# ---------- S2：FTS 初始化与 auto_create_tables 解耦 ----------

def _fts5_supported(engine) -> bool:
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("SELECT fts5(?)", ("initialize",))
        return True
    except Exception:
        return False


def test_fts_index_ready_without_auto_create_tables():
    """生产路径（Alembic 建表 + KB_AUTO_CREATE_TABLES=0）下 FTS 必须可用。

    修复前 ensure_fts_index 只在 auto_create_tables=True 时被调用，_available 恒为 None
    → fts_available() 恒 False → 索引建好了也没人用，静默退化为 ILIKE 全表扫。
    """
    from app.database import Base

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    try:
        if not _fts5_supported(engine):
            pytest.skip("当前 SQLite 未编译 FTS5")
        # 模拟 Alembic 已建表（迁移 0004 同时建 FTS 表与触发器）
        Base.metadata.create_all(bind=engine)

        original = si._available
        si._available = None  # 全新进程、尚未探测的状态
        try:
            assert si.fts_available() is False
            assert si.ensure_fts_index(engine, reindex=False) is True
            assert si.fts_available() is True
            # 空库无落后量；回填路径留给 reindex_fts
            assert si.fts_stale_count(engine) == 0
        finally:
            si._available = original
    finally:
        engine.dispose()


def test_ensure_fts_index_reindex_only_when_stale():
    """reindex=False 时不得触发全量回填（大库回填会阻塞启动）。"""
    from app.database import Base
    from app.models import Document

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    try:
        if not _fts5_supported(engine):
            pytest.skip("当前 SQLite 未编译 FTS5")
        Base.metadata.create_all(bind=engine)
        with engine.begin() as conn:
            conn.execute(
                Document.__table__.insert(),
                [{"id": uuid.uuid4(), "type": "note", "title": "甲", "content": "正文甲"}],
            )

        original = si._available
        si._available = None
        try:
            si.ensure_fts_index(engine, reindex=False)
            # 建索引后插入的行尚未进 FTS → 落后 1 条，且不会被静默回填
            assert si.fts_stale_count(engine) == 1
            with engine.begin() as conn:
                assert conn.exec_driver_sql(
                    "SELECT count(*) FROM documents_fts"
                ).scalar() == 0

            si.ensure_fts_index(engine, reindex=True)
            assert si.fts_stale_count(engine) == 0
        finally:
            si._available = original
    finally:
        engine.dispose()


def test_lifespan_initializes_fts_outside_auto_create_branch():
    """结构性断言：ensure_fts_index 的调用不得缩进在 auto_create_tables 分支内。

    S2 的根因就是调用位置（只在 auto_create_tables=True 时探测），而不是函数本身。
    这条用源码缩进锁住调用位置，防止后续重构把调用挪回分支里。
    """
    import ast
    import inspect

    from app.main import lifespan

    tree = ast.parse(inspect.getsource(lifespan))
    inside_if: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            for child in ast.walk(node):
                inside_if.add(id(child))
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "ensure_fts_index"
    ]
    assert calls, "lifespan 中应调用 ensure_fts_index"
    assert all(
        id(c) not in inside_if for c in calls
    ), "ensure_fts_index 不得位于任何 if 分支内（否则 auto_create_tables=0 时 FTS 永不初始化）"


# ---------- C1：阅读时长的时区处理 ----------

def test_to_utc_annotates_naive_as_utc_without_conversion():
    """naive 值（SQLite 返回值）必须按 UTC 原样标注，不能当本地时间换算。

    修复前对 naive 调用 astimezone(utc)，Python 会按系统本地时区解释它，
    在 UTC+8 机器上日期整体偏移 8 小时。此断言不依赖运行机器的时区设置。
    """
    naive = datetime(2026, 3, 1, 7, 0)
    assert _to_utc(naive) == naive.replace(tzinfo=timezone.utc)

    aware = datetime(2026, 3, 1, 7, 0, tzinfo=timezone(timedelta(hours=8)))
    assert _to_utc(aware) == datetime(2026, 2, 28, 23, 0, tzinfo=timezone.utc)


def test_build_day_map_uses_utc_day_for_naive_records():
    rec = SimpleNamespace(
        start_time=datetime(2026, 3, 1, 7, 0),  # SQLite 返回值：naive UTC
        end_time=datetime(2026, 3, 1, 8, 0),
    )
    assert _build_day_map([rec]) == {"2026-03-01": 60.0}


# ---------- S5：下载头与标题的控制字符 ----------

def test_download_headers_strip_crlf_from_filename():
    from app.models import Document

    doc = Document(title="恶意\r\nX-Injected: 1", format="pdf")
    disposition = file_download_headers(doc)["Content-Disposition"]
    assert "\r" not in disposition
    assert "\n" not in disposition
    # 非白名单字符（CRLF、冒号）收敛为下划线，空格保留；UTF-8 段由 quote() 编码
    assert 'filename="__X-Injected_ 1.pdf"' in disposition
    assert "filename*=UTF-8''" in disposition
    # UTF-8 段保留了原标题，但 CRLF 已被百分号编码，不构成头注入
    assert "%0D%0A" in disposition


def test_create_document_rejects_control_chars_in_title(client, auth_headers):
    resp = client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={"type": "note", "title": "正常\r\nX-Injected: 1", "content": ""},
    )
    assert resp.status_code == 422


def test_patch_document_rejects_control_chars_in_title(client, auth_headers):
    created = client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={"type": "note", "title": "待改名的笔记", "content": ""},
    ).json()
    resp = client.patch(
        f"{API}/documents/{created['id']}",
        headers=auth_headers,
        json={"title": "改名\r\nSet-Cookie: x"},
    )
    assert resp.status_code == 422


def test_list_documents_keeps_tags_and_never_writes_summary_back(client, auth_headers):
    """轻量列表改成 defer + expunge 后，两件事必须仍然成立：

    1. tags（selectinload 预加载）在实例脱离 session 后仍可序列化；
    2. 写回的 200 字摘要不会被 autoflush 当成用户修改 UPDATE 进库覆盖正文
       —— 这是本改动最危险的一点，摘要覆盖全文不可逆。
    """
    body = {
        "type": "note",
        "title": "摘要不回写验证",
        "content": "这是不能被摘要覆盖的正文。" * 50,
        "tags": ["验证标签"],
    }
    created = client.post(f"{API}/documents", headers=auth_headers, json=body).json()

    listed = client.get(
        f"{API}/documents", headers=auth_headers, params={"q": "摘要不回写验证"}
    ).json()
    item = next(d for d in listed if d["id"] == created["id"])
    assert len(item["content"]) <= 201
    assert [t["name"] for t in item["tags"]] == ["验证标签"]

    # 列表请求之后再读详情：正文必须原封不动
    detail = client.get(f"{API}/documents/{created['id']}", headers=auth_headers).json()
    assert detail["content"] == body["content"]


# ---------- C2：列表规模上限 ----------

def _upload_text_file(client, auth_headers, content: bytes):
    resp = client.post(
        f"{API}/documents/upload",
        headers=auth_headers,
        files={"file": ("流式验证.txt", io.BytesIO(content), "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_range_full_and_suffix_requests(client, auth_headers):
    """Range 改流式后语义必须不变：`bytes=0-` 全量、suffix `bytes=-N` 取末尾 N 字节。

    suffix 语义此前被误解析为 0..N-1（个别下载工具拿到损坏数据段），
    而 `bytes=0-` 此前会把整个文件读进内存 —— 两条都在这里钉死。
    """
    body = b"0123456789" * 100  # 1000 字节
    doc = _upload_text_file(client, auth_headers, body)
    url = f"{API}/documents/{doc['id']}/file"

    full = client.get(url, headers={**auth_headers, "Range": "bytes=0-"})
    assert full.status_code == 206
    assert full.content == body
    assert full.headers["content-range"] == f"bytes 0-{len(body) - 1}/{len(body)}"
    assert full.headers["content-length"] == str(len(body))

    suffix = client.get(url, headers={**auth_headers, "Range": "bytes=-5"})
    assert suffix.status_code == 206
    assert suffix.content == body[-5:]
    assert suffix.headers["content-range"] == f"bytes {len(body) - 5}-{len(body) - 1}/{len(body)}"

    # 中间分段
    mid = client.get(url, headers={**auth_headers, "Range": "bytes=10-19"})
    assert mid.content == body[10:20]


def test_stats_health_reports_placeholders_and_orphans(client, auth_headers):
    """health 改成分页 + SQL 侧收敛后，三项输出语义必须与旧实现一致。"""
    marker = uuid.uuid4().hex[:8]
    created = client.post(
        f"{API}/documents",
        headers=auth_headers,
        json={
            "type": "note",
            "title": f"健康面板 {marker}",
            "content": f"参见 [[幽灵{marker}条目]] 的说明。",
        },
    ).json()

    body = client.get(f"{API}/stats/health", headers=auth_headers).json()
    # 占位提及：正文里的 [[x]] 没有同名文档
    assert any(p["mention"] == f"幽灵{marker}条目" for p in body["placeholders"])
    # 孤立文档：新建且无任何链接
    assert any(o["id"] == created["id"] for o in body["orphans"])


def test_list_documents_limit_has_hard_cap(client, auth_headers):
    resp = client.get(f"{API}/documents", headers=auth_headers, params={"limit": "1001"})
    assert resp.status_code == 422


def test_list_documents_still_returns_summary_and_detail_full_text(client, auth_headers):
    """轻量列表取前缀后仍是摘要，详情接口不受影响（防止前缀优化改坏语义）。"""
    body = {
        "type": "note",
        "title": "前缀优化验证",
        "content": "正文内容用于验证。" * 40,
    }
    created = client.post(f"{API}/documents", headers=auth_headers, json=body).json()

    listed = client.get(
        f"{API}/documents", headers=auth_headers, params={"q": "前缀优化验证"}
    ).json()
    item = next(d for d in listed if d["id"] == created["id"])
    assert len(item["content"]) <= 201

    detail = client.get(f"{API}/documents/{created['id']}", headers=auth_headers).json()
    assert detail["content"] == body["content"]
