"""Response cache so repeated planning doesn't re-bill external APIs: in-process LRU -> Redis -> database -> provider.

Only the database tier is on by default (ADR 0008); the LRU (CACHE_LRU_ENTRIES > 0) and Redis (CACHE_REDIS_ENABLED=true with
REDIS_URL) tiers exist behind flags, off, until a measurement shows a database lookup matters (a lookup is about 1 ms in a plan
that takes seconds). Rules that bind every tier:

- The caller decides what may be cached. A provider whose terms forbid storage (IRCTC1, I7) is never passed in here, so no tier
  ever sees it; `storage_allowed` is checked at the call site.
- No tier keeps an entry longer than the `ttl` the caller gave (the table in providers/ttl.py; Google content at most 7 days, I6).
- Redis holds nothing personal: the key is a hash of the request, the value a provider response.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.telemetry import record_cache
from app.db.session import SessionLocal
from app.models.catalog import ApiCache

log = logging.getLogger("tripai.cache")
_MISS = object()


def cache_key(provider: str, *parts: Any) -> str:
    raw = json.dumps([provider, *parts], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class LRUTier:
    """A small in-process cache. Per process, so a second worker has its own copy (each entry still expires on its own)."""

    def __init__(self) -> None:
        self._d: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Any:
        with self._lock:
            hit = self._d.get(key)
            if hit is None:
                return _MISS
            if hit[0] <= time.time():
                del self._d[key]
                return _MISS
            self._d.move_to_end(key)
            return hit[1]

    def put(self, key: str, value: Any, ttl_seconds: float, max_entries: int) -> None:
        with self._lock:
            self._d[key] = (time.time() + ttl_seconds, value)
            self._d.move_to_end(key)
            while len(self._d) > max_entries:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()

    def __len__(self) -> int:
        return len(self._d)


lru = LRUTier()


def _redis():
    s = get_settings()
    if not (s.cache_redis_enabled and s.redis_url):
        return None
    from app.core import redis_client
    return redis_client.get()


def _redis_get(key: str) -> Any:
    c = _redis()
    if c is None:
        return _MISS
    try:
        raw = c.get("cache:" + key)
        return _MISS if raw is None else json.loads(raw)
    except Exception:  # noqa: BLE001 - a tier that fails is a miss, never an error
        log.debug("redis cache read failed", exc_info=True)
        return _MISS


def _redis_put(key: str, value: Any, ttl: timedelta) -> None:
    c = _redis()
    if c is None or ttl.total_seconds() <= 0:
        return
    try:
        c.setex("cache:" + key, int(ttl.total_seconds()), json.dumps(value, default=str))
    except Exception:  # noqa: BLE001
        log.debug("redis cache write failed", exc_info=True)


def _read(key: str, now: datetime) -> Any:
    """A live cached value from the fastest tier that has it, or _MISS."""
    s = get_settings()
    if s.cache_lru_entries > 0:
        v = lru.get(key)
        if v is not _MISS:
            return v
    v = _redis_get(key)
    if v is not _MISS:
        return v
    try:
        with SessionLocal() as db:
            row = db.scalar(select(ApiCache).where(ApiCache.cache_key == key))
            if row is not None:
                expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
                if expires > now:
                    if s.cache_lru_entries > 0:  # fill the faster tier, for what is left of the entry's life
                        lru.put(key, row.response, (expires - now).total_seconds(), s.cache_lru_entries)
                    return row.response
    except Exception:  # cache must never break planning
        log.debug("cache read failed", exc_info=True)
    return _MISS


def peek(provider: str, key_parts: list[Any]) -> Any:
    """The cached value for these parts if there is a live one, else None. Never fetches."""
    v = _read(cache_key(provider, *key_parts), datetime.now(UTC))
    return None if v is _MISS else v


def cached(provider: str, key_parts: list[Any], ttl: timedelta, fetch: Callable[[], Any]) -> Any:
    key = cache_key(provider, *key_parts)
    now = datetime.now(UTC)
    s = get_settings()
    v = _read(key, now)
    if v is not _MISS:
        record_cache(provider, True)
        return v
    record_cache(provider, False)
    value = fetch()
    _write(provider, key, value, ttl, now)
    return value


def _write(provider: str, key: str, value: Any, ttl: timedelta, now: datetime) -> None:
    s = get_settings()
    try:
        with SessionLocal() as db:
            db.execute(delete(ApiCache).where(ApiCache.cache_key == key))
            db.add(ApiCache(provider=provider, cache_key=key, response=value, expires_at=now + ttl))
            db.commit()
    except Exception:
        log.debug("cache write failed", exc_info=True)
    if s.cache_lru_entries > 0:
        lru.put(key, value, ttl.total_seconds(), s.cache_lru_entries)
    _redis_put(key, value, ttl)


def store(provider: str, key_parts: list[Any], value: Any, ttl: timedelta) -> None:
    """Keep a value under these parts for `ttl`, replacing what was there (a copy kept longer than the cache)."""
    _write(provider, cache_key(provider, *key_parts), value, ttl, datetime.now(UTC))
