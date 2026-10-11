"""Optional Redis (ADR 0008). Nothing requires it: with REDIS_URL unset every caller uses its in-process fallback.

Today only /readyz and the optional cache tier use it. It is reserved for what Phase 6 and a later worker fleet need (shared
rate-limit buckets, shared budget counters, an event relay between processes). A managed Redis is a paid service; the
docker-compose container is the free, self-hosted option.
"""
from __future__ import annotations

import logging
import threading

from app.core.config import get_settings

log = logging.getLogger("tripai.redis")
_lock = threading.Lock()
_client = None
_client_url = ""


def configured() -> bool:
    return bool(get_settings().redis_url)


def get():
    """The client, or None when REDIS_URL is unset or the library is missing. Connecting is lazy: a down Redis shows up on use."""
    global _client, _client_url
    url = get_settings().redis_url
    if not url:
        return None
    with _lock:
        if _client is None or _client_url != url:
            try:
                import redis
                _client = redis.Redis.from_url(url, socket_connect_timeout=1.0, socket_timeout=1.0, decode_responses=True)
                _client_url = url
            except Exception:  # noqa: BLE001
                log.warning("Redis client could not be created", exc_info=True)
                return None
        return _client


def ping() -> tuple[bool, str]:
    """(ok, detail). Not configured counts as fine: Redis is optional."""
    c = get()
    if c is None:
        return True, "not configured"
    try:
        c.ping()
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}"
