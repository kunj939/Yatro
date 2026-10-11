"""Circuit breakers (ADR 0007), one per provider + endpoint, plus a provider-wide quota circuit.

Endpoint breaker: closed -> open after 5 failures in a row, or at least half of the last 20 calls (with 10 or more) failing.
Open for 30 s, then 60, 120, 240, 300 s. After that ONE probe call is let through (half-open); success closes it, failure
re-opens it for longer. Client errors (400, 404...) are not failures; timeouts, transport errors, 5xx and 429 are.

Quota circuit: a quota-exhausted answer (or a remaining-quota header at 0) opens the whole provider at once, until a single
probe is due (BREAKER_QUOTA_OPEN_MINUTES, 24 h for IRCTC1). The state is stored in `provider_circuit`, so a restart or
another worker doesn't spend a call to rediscover it. Only that probe may call the provider while it is open.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.gateway import context as C
from app.gateway.errors import CircuitOpen, QuotaExhausted
from app.models.circuit import ProviderCircuit

log = logging.getLogger("tripai.gateway.breaker")
BACKOFF = (30, 60, 120, 240, 300)
STATE_TTL = 30.0  # how long a stored quota state is trusted before it is read again
ASSUMED = "assumed exhausted (IRCTC1_ASSUME_EXHAUSTED)"


def _dt(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, UTC)


def quota_minutes(provider: str) -> int:
    return 1440 if provider == "irctc1" else get_settings().breaker_quota_open_minutes


class _Breaker:
    def __init__(self) -> None:
        self.window: deque[bool] = deque(maxlen=20)
        self.consecutive = 0
        self.state = "closed"  # closed | open | half
        self.open_until = 0.0
        self.level = 0
        self.probing = False


class Breakers:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._b: dict[tuple[str, str], _Breaker] = {}
        self._quota: dict[str, tuple[float | None, float]] = {}  # provider -> (open_until epoch or None, read at)
        self._gen: dict[str, int] = {}  # bumped on every change, so a slow read can't overwrite a newer state

    # --- stored quota state ---------------------------------------------------------------------------------------
    def _read(self, provider: str) -> float | None:
        try:
            with SessionLocal() as db:
                row = db.scalar(select(ProviderCircuit).where(ProviderCircuit.provider == provider, ProviderCircuit.endpoint == "*"))
                snap = None if row is None else (row.open_until, row.reason)
        except Exception:  # noqa: BLE001 - the gateway never fails because its own storage did
            log.debug("couldn't read circuit state", exc_info=True)
            return None
        s = get_settings()
        if provider == "irctc1":  # writes happen after the read session is closed (SQLite)
            if snap is None and s.irctc1_assume_exhausted:
                until = C.now() + quota_minutes(provider) * 60
                self._write(provider, until, ASSUMED)
                return until
            if snap is not None and snap[1] == ASSUMED and not s.irctc1_assume_exhausted:
                self._write(provider, None)  # the owner says IRCTC1 has quota again
                return None
        if snap is None:
            return None
        until = snap[0] if snap[0].tzinfo else snap[0].replace(tzinfo=UTC)
        return until.timestamp()

    def _write(self, provider: str, until: float | None, reason: str = "quota exhausted") -> None:
        try:
            with SessionLocal() as db:
                row = db.scalar(select(ProviderCircuit).where(ProviderCircuit.provider == provider, ProviderCircuit.endpoint == "*"))
                if until is None:
                    if row is not None:
                        db.delete(row)
                elif row is None:
                    db.add(ProviderCircuit(provider=provider, endpoint="*", open_until=_dt(until), reason=reason))
                else:
                    row.open_until, row.reason = _dt(until), reason
                db.commit()
        except Exception:  # noqa: BLE001
            log.debug("couldn't write circuit state", exc_info=True)

    def quota_until(self, provider: str, fresh: bool = False) -> float | None:
        with self._lock:
            cached = self._quota.get(provider)
            if cached is not None and not fresh and C.now() - cached[1] < STATE_TTL:
                return cached[0]
            gen = self._gen.get(provider, 0)
        until = self._read(provider)
        with self._lock:
            if self._gen.get(provider, 0) != gen:  # someone claimed a probe or changed the state while we were reading
                return self._quota[provider][0] if provider in self._quota else until
            self._quota[provider] = (until, C.now())
        return until

    def set_quota(self, provider: str, reason: str = "quota exhausted") -> float:
        until = C.now() + quota_minutes(provider) * 60
        with self._lock:
            self._quota[provider] = (until, C.now())
            self._gen[provider] = self._gen.get(provider, 0) + 1
        self._write(provider, until, reason)
        log.warning("%s quota exhausted: closed to calls until %s (then one probe)", provider, _dt(until).isoformat(timespec="minutes"))
        return until

    def clear_quota(self, provider: str) -> None:
        with self._lock:
            self._quota[provider] = (None, C.now())
            self._gen[provider] = self._gen.get(provider, 0) + 1
        self._write(provider, None)
        log.warning("%s answered again: quota circuit closed", provider)

    # --- the checks and the outcomes ---------------------------------------------------------------------------------
    def before(self, provider: str, endpoint: str) -> str | None:
        """Raises CircuitOpen/QuotaExhausted, or returns None, or "quota-probe"/"half-open" for the one probe call."""
        now = C.now()
        until = self.quota_until(provider)
        if until is not None:
            if now < until:
                raise QuotaExhausted(f"{provider} quota is used up.", provider=provider, endpoint=endpoint, retry_at=_dt(until))
            with self._lock:  # a probe is due: one caller (in this process) wins it, and the next probe is pushed out
                again = self._quota.get(provider)
                if again and again[0] is not None and again[0] > now:
                    raise QuotaExhausted(f"{provider} quota is used up.", provider=provider, endpoint=endpoint, retry_at=_dt(again[0]))
                nxt = now + quota_minutes(provider) * 60
                self._quota[provider] = (nxt, now)
                self._gen[provider] = self._gen.get(provider, 0) + 1
            self._write(provider, nxt, "probe claimed")
            return "quota-probe"
        with self._lock:
            b = self._b.setdefault((provider, endpoint), _Breaker())
            if b.state == "closed":
                return None
            if b.state == "open" and now < b.open_until:
                raise CircuitOpen(f"{provider} is failing; paused.", provider=provider, endpoint=endpoint, retry_at=_dt(b.open_until))
            if b.probing:
                raise CircuitOpen(f"{provider} is being checked.", provider=provider, endpoint=endpoint, retry_at=_dt(now + 5))
            b.state, b.probing = "half", True
            return "half-open"

    def release(self, provider: str, endpoint: str, token: str | None) -> None:
        """The probe call was never sent (a later check refused it): let the next caller be the probe."""
        if token == "half-open":
            with self._lock:
                b = self._b.get((provider, endpoint))
                if b is not None and b.state == "half":
                    b.state, b.probing, b.open_until = "open", False, 0.0

    def record(self, provider: str, endpoint: str, ok: bool, token: str | None = None, quota: bool = False) -> None:
        """`ok`: the provider answered sensibly. `quota`: it said its quota is used up."""
        if quota:
            self.set_quota(provider)
            return
        if token == "quota-probe" and ok:
            self.clear_quota(provider)
        now = C.now()
        with self._lock:
            b = self._b.setdefault((provider, endpoint), _Breaker())
            was_half = b.state == "half"
            b.window.append(ok)
            if ok:
                b.consecutive = 0
                if was_half:
                    b.state, b.probing, b.level = "closed", False, 0
                    b.window.clear()
                    log.warning("%s %s recovered: circuit closed", provider, endpoint)
                return
            b.consecutive += 1
            failures = sum(1 for x in b.window if not x)
            trip = was_half or b.consecutive >= 5 or (len(b.window) >= 10 and failures * 2 >= len(b.window))
            if trip:
                b.level = min(b.level + 1, len(BACKOFF)) if b.state != "closed" else 1
                b.state, b.probing = "open", False
                b.open_until = now + BACKOFF[b.level - 1]
                log.warning("%s %s circuit open for %ds (%d failures in the last %d calls)", provider, endpoint,
                            BACKOFF[b.level - 1], failures, len(b.window))

    # --- views ---------------------------------------------------------------------------------------------------------
    def is_open(self, provider: str) -> bool:
        now = C.now()
        until = self.quota_until(provider)
        if until is not None and now < until:
            return True
        with self._lock:
            return any(b.state == "open" and now < b.open_until for (p, _), b in self._b.items() if p == provider)

    def open_until(self, provider: str) -> float | None:
        """When the provider may be tried again (the latest of its open circuits), or None when it is not open."""
        now = C.now()
        until = self.quota_until(provider)
        times = [until] if until is not None and now < until else []
        with self._lock:
            times += [b.open_until for (p, _), b in self._b.items() if p == provider and b.state == "open" and now < b.open_until]
        return max(times) if times else None

    def snapshot(self) -> list[dict]:
        now = C.now()
        with self._lock:
            out = [{"provider": p, "endpoint": e, "state": b.state, "retry_in": max(0, round(b.open_until - now)) if b.state == "open" else 0,
                    "recent_failures": sum(1 for x in b.window if not x), "recent_calls": len(b.window)} for (p, e), b in sorted(self._b.items())]
            quota = {p: v[0] for p, v in self._quota.items() if v[0] is not None and v[0] > now}
        out += [{"provider": p, "endpoint": "*", "state": "quota", "retry_in": round(u - now), "recent_failures": 0, "recent_calls": 0}
                for p, u in sorted(quota.items())]
        return out

    def reset(self) -> None:
        """Tests: forget everything held in memory (stored state is untouched)."""
        with self._lock:
            self._b.clear()
            self._quota.clear()
            self._gen.clear()


breakers = Breakers()
