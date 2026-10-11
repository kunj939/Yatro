"""Request logging, request IDs, security headers, body-size guard and an in-memory rate limiter.

The rate limiter is process-local (fine for a single-instance MVP). Swap `RateLimiter` for a
Redis-backed implementation when running multiple API replicas.
"""
from __future__ import annotations

import ipaddress
import logging
import threading
import time
import uuid
from collections import defaultdict
from functools import lru_cache

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.core.errors import RateLimited, error_body

log = logging.getLogger("tripai.http")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > settings.max_body_bytes:
            return JSONResponse(status_code=413, content=error_body("payload_too_large", "Request body is too large."))
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = (time.perf_counter() - start) * 1000
        response.headers["x-request-id"] = request_id
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-frame-options"] = "DENY"
        response.headers["referrer-policy"] = "strict-origin-when-cross-origin"
        if f"{settings.api_prefix}/share/" in request.url.path:  # share pages: every answer, errors included (ADR 0006)
            response.headers["cache-control"] = "no-store"
            response.headers["x-robots-tag"] = "noindex, nofollow"
        log.info("%s %s %s %.0fms rid=%s", request.method, request.url.path, response.status_code, elapsed, request_id)
        return response


class RateLimiter:
    """Sliding-window counter keyed by (bucket, client)."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, bucket: str, client: str, limit: int, window: float = 60.0) -> None:
        if not get_settings().rate_limit_enabled:
            return
        now = time.monotonic()
        key = (bucket, client)
        with self._lock:
            hits = [t for t in self._hits[key] if now - t < window]
            if len(hits) >= limit:
                retry = int(window - (now - hits[0])) + 1
                self._hits[key] = hits
                raise RateLimited(f"Too many requests. Try again in {retry}s.", details={"retry_after": retry})
            hits.append(now)
            self._hits[key] = hits

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = RateLimiter()


@lru_cache(maxsize=8)
def _trusted_networks(raw: str) -> tuple:
    nets = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            log.error("TRUSTED_PROXY_IPS: %r is not an IP address or network; ignored", item)
    return tuple(nets)


def _ip(text: str):
    """An address from one X-Forwarded-For entry ("1.2.3.4", "1.2.3.4:5678", "[::1]:80", "::1"); None if it isn't one."""
    t = text.strip().strip('"')
    if t.startswith("[") and "]" in t:
        t = t[1:t.index("]")]
    elif t.count(":") == 1:  # IPv4 with a port
        t = t.split(":")[0]
    try:
        return ipaddress.ip_address(t.split("%")[0])
    except ValueError:
        return None


def client_ip(request: Request) -> str:
    """The address of whoever is really calling.

    X-Forwarded-For is written by the client, so it is never believed by default: with TRUSTED_PROXY_IPS empty the answer is
    the socket address. When the request comes from one of OUR proxies (an address in TRUSTED_PROXY_IPS), the header is read from
    the RIGHT: each of our proxies appends the address it received the request from, so the right-most entry that is not one of
    ours is the real client, and anything to its left is whatever the client chose to claim and is ignored."""
    peer = request.client.host if request.client else "unknown"
    nets = _trusted_networks(get_settings().trusted_proxy_ips)
    if not nets:
        return peer

    def ours(ip) -> bool:
        return any(ip in n for n in nets if n.version == ip.version)
    peer_ip = _ip(peer)
    if peer_ip is None or not ours(peer_ip):
        return peer  # not one of our proxies: whatever it says about itself or others is ignored
    entries = [e for line in request.headers.getlist("x-forwarded-for") for e in line.split(",") if e.strip()]
    nearest = str(peer_ip)
    for entry in reversed(entries):
        ip = _ip(entry)
        if ip is None:
            return nearest  # an entry we can't read: trust nothing to its left, use the last address we could vouch for
        if not ours(ip):
            return str(ip)
        nearest = str(ip)
    return nearest  # the whole chain is ours: the left-most address is the caller


def client_key(request: Request) -> str:
    return client_ip(request)


def limit(bucket: str, per_minute: int | None = None):
    """FastAPI dependency factory, e.g. `Depends(limit("plan", 6))`."""

    def _dep(request: Request) -> None:
        settings = get_settings()
        rate_limiter.check(bucket, client_key(request), per_minute or settings.rate_limit_default_per_minute)

    return _dep
