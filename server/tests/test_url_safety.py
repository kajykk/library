"""SSRF 防护测试：url_safety.validate_url / fetch_with_guard。

背景：此前同一份防护有两套实现 —— clip.py 逐跳校验（做对了），
metadata_fetch.download_cover 用 follow_redirects=True（可被 302 绕过）。
现在两处共用 fetch_with_guard，这些测试锁住"每跳都校验"这个契约。
"""

from __future__ import annotations

import contextlib

import httpx
import pytest
from fastapi import HTTPException

from app.services import url_safety


# ---------- validate_url：地址与端口 ----------

@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.1.2.3/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data",  # 云元数据服务
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",  # IPv4-mapped IPv6，必须归一化后再比对
        "http://[64:ff9b::7f00:1]/",  # NAT64 前缀包裹的 127.0.0.1
    ],
)
def test_validate_url_blocks_private_and_metadata_addresses(url):
    with pytest.raises(HTTPException) as exc:
        url_safety.validate_url(url)
    assert exc.value.status_code == 400


def test_validate_url_blocks_non_web_port():
    """端口白名单：挡掉 22/3306/6379 这类内网服务探测（公网 IP 仍会被端口拦下）"""
    with pytest.raises(HTTPException) as exc:
        url_safety.validate_url("http://8.8.8.8:22/")
    assert exc.value.status_code == 400
    assert "port" in str(exc.value.detail)


def test_validate_url_allows_public_web_url():
    # 8.8.8.8 是字面 IP，不需要 DNS；端口省略
    assert url_safety.validate_url("http://8.8.8.8/") == "http://8.8.8.8/"
    assert url_safety.validate_url("https://8.8.8.8:443/x") == "https://8.8.8.8:443/x"


def test_validate_url_blocks_non_http_scheme():
    for url in ("file:///etc/passwd", "ftp://8.8.8.8/", "gopher://8.8.8.8/"):
        with pytest.raises(HTTPException):
            url_safety.validate_url(url)


# ---------- fetch_with_guard：逐跳校验 ----------

class _FakeResponse:
    def __init__(self, status_code=200, headers=None, body=b"", url="http://8.8.8.8/"):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body
        self.url = url
        self.charset_encoding = "utf-8"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def iter_bytes(self):
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _install_fake_stream(monkeypatch, handler):
    calls: list[str] = []

    @contextlib.contextmanager
    def fake_stream(method, url, **kwargs):
        calls.append(str(url))
        yield handler(str(url))

    monkeypatch.setattr(url_safety.httpx, "stream", fake_stream)
    return calls


def test_fetch_with_guard_revalidates_redirect_target(monkeypatch):
    """302 跳到云元数据地址必须被拦：这正是旧封面下载路径的绕过方式。"""
    def handler(url: str) -> _FakeResponse:
        if url.startswith("http://8.8.8.8/"):
            return _FakeResponse(
                status_code=302,
                headers={"location": "http://169.254.169.254/latest/meta-data"},
                url=url,
            )
        raise AssertionError(f"不应向内网址发起请求：{url}")

    calls = _install_fake_stream(monkeypatch, handler)

    with pytest.raises(HTTPException) as exc:
        url_safety.fetch_with_guard("http://8.8.8.8/start")
    assert exc.value.status_code == 400  # 内网地址 → 400，而不是 200/502
    # 只发起了首跳请求：第二跳在校验阶段就被挡下
    assert calls == ["http://8.8.8.8/start"]


def test_fetch_with_guard_follows_relative_location(monkeypatch):
    def handler(url: str) -> _FakeResponse:
        if url == "http://8.8.8.8/a":
            return _FakeResponse(status_code=302, headers={"location": "/b"}, url=url)
        return _FakeResponse(status_code=200, body=b"ok", url="http://8.8.8.8/b")

    _install_fake_stream(monkeypatch, handler)
    resp = url_safety.fetch_with_guard("http://8.8.8.8/a")
    assert resp.body == b"ok"
    assert resp.final_url == "http://8.8.8.8/b"
    assert resp.charset == "utf-8"


def test_fetch_with_guard_enforces_byte_limit(monkeypatch):
    def handler(url: str) -> _FakeResponse:
        return _FakeResponse(status_code=200, body=b"x" * 100, url=url)

    _install_fake_stream(monkeypatch, handler)
    with pytest.raises(HTTPException) as exc:
        url_safety.fetch_with_guard("http://8.8.8.8/big", max_bytes=10)
    assert exc.value.status_code == 413


def test_fetch_with_guard_caps_redirect_hops(monkeypatch):
    def handler(url: str) -> _FakeResponse:
        # 永远重定向：验证跳数上限确实生效而不是无限循环
        return _FakeResponse(status_code=302, headers={"location": "/next"}, url=url)

    calls = _install_fake_stream(monkeypatch, handler)
    with pytest.raises(HTTPException) as exc:
        url_safety.fetch_with_guard("http://8.8.8.8/loop", max_redirects=2)
    assert exc.value.status_code == 502
    assert "redirect" in str(exc.value.detail).lower()
    assert len(calls) == 3  # 首跳 + 2 次重定向


def test_fetch_with_guard_requires_location_on_redirect(monkeypatch):
    def handler(url: str) -> _FakeResponse:
        return _FakeResponse(status_code=302, headers={}, url=url)

    _install_fake_stream(monkeypatch, handler)
    with pytest.raises(HTTPException) as exc:
        url_safety.fetch_with_guard("http://8.8.8.8/no-location")
    assert exc.value.status_code == 502
    assert "Location" in str(exc.value.detail)
