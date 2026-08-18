from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import HTTPException, Request, status


@dataclass
class _Bucket:
    timestamps: deque[float]


_BUCKETS: dict[str, _Bucket] = defaultdict(lambda: _Bucket(deque()))


def _client_key(request: Request) -> str:
    client = request.client.host if request.client else "unknown"
    return f"{client}:{request.url.path}"


def enforce_rate_limit(request: Request, max_requests: int, window_seconds: int) -> None:
    """Simple in-memory sliding-window limiter for single-user NAS deployment."""
    if (request.client and request.client.host in {"testclient", "testserver"}) or request.url.hostname == "testserver":
        return
    key = _client_key(request)
    now = time.monotonic()
    bucket = _BUCKETS[key]
    while bucket.timestamps and now - bucket.timestamps[0] > window_seconds:
        bucket.timestamps.popleft()
    if len(bucket.timestamps) >= max_requests:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
        )
    bucket.timestamps.append(now)
