"""URL safety validation to prevent SSRF attacks.

对外暴露两个入口：
- `validate_url`：只做校验（调用方自己发请求时使用）；
- `fetch_with_guard`：**校验 + 发请求一体**（推荐）——手工逐跳跟随重定向，每一跳都
  重新校验，并流式限制响应体积。

此前 clip 与封面下载各有一套实现：clip 做对了（逐跳校验），封面下载用
`follow_redirects=True` 只校验初始 URL，可被 `302 → 169.254.169.254` 绕过。
本模块是唯一实现，两处共用。
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

from ..config import get_settings

_BLOCKED_RANGES = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("192.0.0.0/24"),  # IETF 协议分配
    ipaddress.ip_network("198.18.0.0/15"),  # 基准测试网段，常被用于绕过
    ipaddress.ip_network("224.0.0.0/4"),  # 组播
    ipaddress.ip_network("240.0.0.0/4"),  # 保留
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("64:ff9b::/96"),  # NAT64 IPv4-mapped
]


def _normalize_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address):
    """IPv4-mapped IPv6（如 ::ffff:127.0.0.1）归一化为 IPv4 再比对，
    否则这类地址不会命中任何 v4 黑名单网段。"""
    if ip.version == 6 and getattr(ip, "ipv4_mapped", None) is not None:
        return ip.ipv4_mapped
    if ip.version == 6 and getattr(ip, "sixtofour", None) is not None:
        return ip.sixtofour
    return ip


def _resolve_all_ips(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail=f"Cannot resolve host: {host}") from exc
    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip not in ips:
            ips.append(ip)
    return ips


# 端口白名单：只放通常见 Web 端口，挡掉 22/3306/6379 这类内网服务探测
_ALLOWED_PORTS = {80, 443, 8080, 8443}

DEFAULT_MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = 10.0


def _validate_port(url: str) -> None:
    parsed = urlparse(url)
    port = parsed.port
    # 未显式指定端口时按 scheme 取默认值，不必校验
    if port is None:
        return
    if port not in _ALLOWED_PORTS:
        raise HTTPException(status_code=400, detail="Blocked: port not allowed")


def validate_url(url: str) -> str:
    """Validate a URL is safe to fetch. Raises HTTPException on unsafe URLs."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Only http/https schemes allowed")
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail="No hostname in URL")

    # KB_ALLOW_LOCAL_CLIP=1 仅用于测试/e2e：放行本机地址，便于对本地 fixture 服务做端到端验证。
    # 端口校验同样在此开关内 —— e2e 后端跑在 8111，若端口白名单独立于该开关，
    # 剪藏 `http://localhost:8111/api/health` 会被自己的防护挡掉。
    if not get_settings().allow_local_clip:
        _validate_port(url)
        for ip in _resolve_all_ips(parsed.hostname):
            normalized = _normalize_ip(ip)
            for net in _BLOCKED_RANGES:
                if normalized in net:
                    raise HTTPException(
                        status_code=400,
                        detail="Blocked: internal/private address",
                    )
    return url


@dataclass(frozen=True)
class FetchedResponse:
    """fetch_with_guard 的结果。

    charset 必须带上：中文站点常见 GBK，若调用方一律按 utf-8 解码会整页乱码。
    """

    body: bytes
    content_type: str
    charset: str | None
    final_url: str


def fetch_with_guard(
    url: str,
    *,
    max_bytes: int = 5 * 1024 * 1024,
    timeout: float = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
) -> FetchedResponse:
    """手工逐跳跟随重定向地取回响应体：**每一跳**都重新做 SSRF 校验。

    httpx 的 `follow_redirects=True` 只在首跳前校验一次，攻击者用
    `302 → http://169.254.169.254/latest/meta-data` 即可绕过，所以这里自己跳。

    超过跳数上限、体积上限或不可达时抛 HTTPException（502 上游故障 / 413 响应过大）。
    """
    current_url = url
    for hop in range(max_redirects + 1):
        validate_url(current_url)
        try:
            with httpx.stream(
                "GET",
                current_url,
                timeout=httpx.Timeout(timeout),
                headers=headers or {},
                follow_redirects=False,
            ) as resp:
                if 300 <= resp.status_code < 400:
                    location = resp.headers.get("location")
                    if not location:
                        raise HTTPException(
                            status_code=502,
                            detail=f"Redirect {resp.status_code} missing Location header",
                        )
                    if hop >= max_redirects:
                        raise HTTPException(
                            status_code=502,
                            detail=f"Too many redirects (max {max_redirects})",
                        )
                    # 相对路径 Location 基于当前 URL 绝对化
                    current_url = str(httpx.URL(str(resp.url)).join(location))
                    continue

                resp.raise_for_status()
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    if len(buf) + len(chunk) > max_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=f"Remote response exceeds {max_bytes} bytes limit",
                        )
                    buf.extend(chunk)
                content_type = resp.headers.get("content-type", "").split(";")[0].strip()
                return FetchedResponse(
                    body=bytes(buf),
                    content_type=content_type,
                    charset=resp.charset_encoding,
                    final_url=str(resp.url),
                )
        except HTTPException:
            raise
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"抓取失败：{exc}") from exc
    raise HTTPException(status_code=502, detail=f"Too many redirects (max {max_redirects})")
