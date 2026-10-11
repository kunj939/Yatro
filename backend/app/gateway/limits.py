"""Bulkheads (ADR 0007): a concurrency limit per provider, so one slow or rate-limited provider can't take every worker thread.
The wait for a slot counts against the plan's deadline; a call that would wait too long fails fast.

AIMD (GATEWAY_AIMD, off): the limit grows by one after a limit's worth of successes and halves on a timeout or 429. It is an
interface until concurrent load has been measured."""
from __future__ import annotations

import threading

from app.core.config import get_settings

DEFAULT_LIMITS = {"google": 16, "railradar": 4, "irctc1": 2, "llm": 4}
OTHER_LIMIT = 4


class Limiter:
    def __init__(self, limit: int, aimd: bool = False, floor: int = 1, ceiling: int | None = None) -> None:
        self.limit, self.inflight, self.aimd = limit, 0, aimd
        self.floor, self.ceiling = floor, ceiling or limit * 2
        self._ok_run = 0
        self._cond = threading.Condition()

    def acquire(self, timeout: float) -> bool:
        with self._cond:
            if not self._cond.wait_for(lambda: self.inflight < self.limit, timeout=max(0.0, timeout)):
                return False
            self.inflight += 1
            return True

    def release(self, ok: bool | None = None) -> None:
        """`ok` False = a timeout or 429 (AIMD halves the limit), True = success, None = neutral."""
        with self._cond:
            self.inflight -= 1
            if self.aimd and ok is not None:
                if ok:
                    self._ok_run += 1
                    if self._ok_run >= self.limit and self.limit < self.ceiling:
                        self.limit, self._ok_run = self.limit + 1, 0
                else:
                    self.limit, self._ok_run = max(self.floor, self.limit // 2), 0
            self._cond.notify_all()


_limiters: dict[str, Limiter] = {}
_lock = threading.Lock()


def limiter_for(key: str) -> Limiter:
    with _lock:
        lim = _limiters.get(key)
        if lim is None:
            lim = _limiters[key] = Limiter(DEFAULT_LIMITS.get(key, OTHER_LIMIT), aimd=get_settings().gateway_aimd)
        return lim


def snapshot() -> dict:
    with _lock:
        return {k: {"limit": v.limit, "inflight": v.inflight} for k, v in sorted(_limiters.items())}


def reset() -> None:
    with _lock:
        _limiters.clear()
