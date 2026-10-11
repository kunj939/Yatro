"""The cost ledger: what each provider endpoint was asked, how it answered, what it cost, what was skipped and why
(ADR 0007). In memory (a few hundred small counters); /system/budgets reads it, Phase 7 exports it."""
from __future__ import annotations

import threading
from collections import defaultdict, deque

_FIELDS = ("calls", "ok", "errors", "retries", "usd", "latency_ms", "circuit_skips", "budget_stops", "deadline_skips",
           "bulkhead_skips", "coalescible", "quota_hits")
SEEN_PREFIXES = ("x-ratelimit", "ratelimit", "x-rapidapi", "retry-after")  # quota headers: recorded, then acted on if recognised
REMAINING = ("x-ratelimit-requests-remaining", "x-ratelimit-remaining", "ratelimit-remaining", "x-ratelimit-remaining-requests",
             "x-rapidapi-requests-remaining")
LIMIT = ("x-ratelimit-requests-limit", "x-ratelimit-limit", "ratelimit-limit", "x-ratelimit-limit-requests", "x-rapidapi-requests-limit")


class Ledger:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.stats: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: dict.fromkeys(_FIELDS, 0.0))
        self.recent: dict[str, deque] = defaultdict(lambda: deque(maxlen=50))  # provider -> (ok, latency_ms)
        self.headers: dict[str, dict[str, str]] = defaultdict(dict)
        self.users: dict[str, float] = defaultdict(float)  # user id -> USD today-ish (Phase 6 turns this into caps)

    def add(self, provider: str, endpoint: str, **inc: float) -> None:
        with self._lock:
            row = self.stats[(provider, endpoint)]
            for k, v in inc.items():
                row[k] += v

    def outcome(self, provider: str, ok: bool, latency_ms: float) -> None:
        with self._lock:
            self.recent[provider].append((ok, latency_ms))

    def see_headers(self, provider: str, headers) -> tuple[int | None, int | None]:
        """Remember any quota headers; return (remaining, limit) when the usual names are present."""
        seen = {k.lower(): v for k, v in headers.items() if k.lower().startswith(SEEN_PREFIXES)}
        if seen:
            with self._lock:
                self.headers[provider].update(seen)

        def num(names):
            for n in names:
                try:
                    return int(float(seen[n]))
                except (KeyError, ValueError, TypeError):
                    continue
            return None
        return num(REMAINING), num(LIMIT)

    def add_user(self, user_id: str | None, usd: float) -> None:
        if user_id and usd:
            with self._lock:
                self.users[user_id] += usd

    def snapshot(self) -> dict:
        with self._lock:
            return {"endpoints": [{"provider": p, "endpoint": e, **{k: round(v, 4) for k, v in row.items()}}
                                  for (p, e), row in sorted(self.stats.items())],
                    "headers_seen": {p: dict(h) for p, h in self.headers.items()}}

    def reset(self) -> None:
        with self._lock:
            self.stats.clear()
            self.recent.clear()
            self.headers.clear()
            self.users.clear()


ledger = Ledger()
