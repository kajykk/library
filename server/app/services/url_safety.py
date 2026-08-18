"""URL safety validation to prevent SSRF attacks."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

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
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


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


def validate_url(url: str) -> str:
    """Validate a URL is safe to fetch. Raises HTTPException on unsafe URLs."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Only http/https schemes allowed")
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail="No hostname in URL")

    # KB_ALLOW_LOCAL_CLIP=1 仅用于测试/e2e：放行本机地址，便于对本地 fixture 服务做端到端验证
    if not get_settings().allow_local_clip:
        for ip in _resolve_all_ips(parsed.hostname):
            for net in _BLOCKED_RANGES:
                if ip in net:
                    raise HTTPException(
                        status_code=400,
                        detail="Blocked: internal/private address",
                    )
    return url
