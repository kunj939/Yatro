"""Retries (ADR 0007): at most one, jittered, only for idempotent calls and retryable answers, under a retry budget.

Retryable: connect errors, timeouts, 502/503/504, and a 429 that is not a quota answer. Never: 500, other 4xx, quota 429s.
Retry budget, per provider: every call adds 0.1 tokens (cap 10), a retry costs 1, so a failing provider can add at most about
10% more calls (after an initial burst), never double the spend."""
from __future__ import annotations

import random
import threading

RETRYABLE_STATUS = {502, 503, 504}
MAX_DELAY = 2.0


def backoff(attempt: int, retry_after: float | None = None) -> float:
    """Full-jitter exponential delay (0.3 s base, 2 s cap); the provider's Retry-After wins if it is short enough."""
    if retry_after is not None and retry_after >= 0:
        return min(retry_after, MAX_DELAY)
    return random.uniform(0, min(MAX_DELAY, 0.3 * (2 ** attempt)))


def parse_retry_after(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


class RetryBudget:
    def __init__(self, cap: float = 10.0, per_call: float = 0.1) -> None:
        self.cap, self.per_call = cap, per_call
        self._tokens: dict[str, float] = {}
        self._lock = threading.Lock()

    def on_call(self, provider: str) -> None:
        with self._lock:
            self._tokens[provider] = min(self.cap, self._tokens.get(provider, self.cap) + self.per_call)

    def take(self, provider: str) -> bool:
        with self._lock:
            t = self._tokens.get(provider, self.cap)
            if t < 1:
                return False
            self._tokens[provider] = t - 1
            return True

    def reset(self) -> None:
        with self._lock:
            self._tokens.clear()


retry_budget = RetryBudget()
