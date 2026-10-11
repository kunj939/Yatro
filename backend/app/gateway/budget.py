"""Daily budgets and the cost ledger (invariant I5, ADR 0007).

A call's estimated price is reserved before it is sent and settled afterwards. Warn at 80% of a day's budget, stop at 100%
(BUDGET_MODE=enforce): a stop raises BudgetExhausted, a ProviderUnavailable, so every caller's fall-through applies.
Background work may use only the first BACKGROUND_BUDGET_SHARE of a day so it can't starve users.

Counting is in process and written to `daily_counters` every BUDGET_FLUSH_SECONDS as a delta, which also reads back other
workers' totals: no database write per call (Phase 0 showed what that does on SQLite). With N workers a day can overshoot by
about N x the spend of one flush interval; Redis (Phase 5) makes it exact. The day resets at midnight in BUDGET_TZ.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core import runtime
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.gateway import context as C
from app.gateway.errors import BudgetExhausted
from app.models.counters import DailyCounter

log = logging.getLogger("tripai.gateway.budget")
GROUP_OF = {"google-places": "google", "google-routes": "google", "google-geocoding": "google", "openrouter": "llm",
            "brave": "search", "serper": "search", "tavily": "search", "railradar": "railradar", "irctc1": "irctc1"}
UNITS = {"google": "usd", "llm": "usd", "search": "usd", "railradar": "calls", "irctc1": "calls", "plans": "plans", "sms": "calls"}
SCALE = {"usd": 1_000_000, "calls": 1, "plans": 1}  # stored as integers: micro-dollars, calls, plans
WARN_AT = 0.8


class Budgets:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pending: dict[tuple[str, str], float] = {}  # (day, group) -> units spent here, not yet written
        self._synced: dict[tuple[str, str], float] = {}  # (day, group) -> the database total at the last sync
        self._warned: set[tuple[str, str]] = set()

    # --- the day ---------------------------------------------------------------------------------------------------------
    def _tz(self) -> ZoneInfo:
        return ZoneInfo(get_settings().budget_tz)

    def day_key(self) -> str:
        return datetime.fromtimestamp(C.now(), self._tz()).date().isoformat()

    def reset_at(self) -> datetime:
        """The next midnight in BUDGET_TZ: when a stopped budget (or the daily plan cap) reopens."""
        local = datetime.fromtimestamp(C.now(), self._tz())
        return datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tzinfo=self._tz())

    # --- limits and use ----------------------------------------------------------------------------------------------
    @staticmethod
    def limit(group: str) -> float | None:
        s = get_settings()
        return {"google": s.google_daily_usd, "llm": s.llm_daily_usd, "search": s.search_daily_usd,
                "railradar": s.railradar_daily_calls, "irctc1": s.irctc1_daily_calls, "plans": runtime.number("daily_plan_cap", s.daily_plan_cap) or None,
                "sms": s.sms_daily_limit or None}.get(group)  # 0 = no cap

    def _load(self, day: str, group: str) -> float:
        name = f"b:{group}"
        try:
            with SessionLocal() as db:
                v = db.scalar(select(DailyCounter.value).where(DailyCounter.day == day, DailyCounter.name == name))
        except Exception:  # noqa: BLE001
            return 0.0
        return (v or 0) / SCALE[UNITS[group]]

    def _used_locked(self, day: str, group: str) -> float:
        key = (day, group)
        if key not in self._synced:
            self._synced[key] = self._load(day, group)
        return self._synced[key] + self._pending.get(key, 0.0)

    def used(self, group: str) -> float:
        with self._lock:
            return self._used_locked(self.day_key(), group)

    def exhausted(self, group: str) -> bool:
        lim = self.limit(group)
        s = get_settings()
        return s.budget_mode == "enforce" and lim is not None and self.used(group) >= lim

    def reserve(self, group: str | None, cost: float, priority: str = "foreground") -> float:
        """Check the budget and count `cost` against it. Returns what was reserved (hand it back to settle())."""
        s = get_settings()
        if group is None or s.budget_mode == "off" or cost <= 0 and group != "plans":
            return 0.0
        lim = self.limit(group)
        day = self.day_key()
        key = (day, group)
        with self._lock:
            used = self._used_locked(day, group)
            if lim is not None:
                if used + cost > lim:
                    msg = f"Today's {group} budget is used up."
                    if s.budget_mode == "enforce":
                        raise BudgetExhausted(msg, provider=group, retry_at=self.reset_at())
                    log.warning("%s (BUDGET_MODE=warn: letting the call through)", msg)
                elif priority == "background" and used + cost > lim * s.background_budget_share and s.budget_mode == "enforce":
                    raise BudgetExhausted(f"Background work has used its share of today's {group} budget.", provider=group,
                                          retry_at=self.reset_at())
                if (used + cost) >= lim * WARN_AT and key not in self._warned:
                    self._warned.add(key)
                    log.warning("budget warning: %s has used %.0f%% of today's %s %s", group, 100 * (used + cost) / lim if lim else 100,
                                lim, UNITS[group])
            self._pending[key] = self._pending.get(key, 0.0) + cost
        return cost

    def settle(self, group: str | None, reserved: float, actual: float) -> None:
        """The call's real cost: a failed call is refunded (actual 0), a timeout keeps the reservation, an LLM call adds
        its reported cost."""
        if group is None or reserved == actual:
            return
        key = (self.day_key(), group)
        with self._lock:
            self._pending[key] = max(0.0, self._pending.get(key, 0.0) + (actual - reserved))

    # --- writing out -------------------------------------------------------------------------------------------------
    def flush(self) -> int:
        """Write the deltas to daily_counters and read back the totals. Returns how many counters were written."""
        with self._lock:
            todo = {k: v for k, v in self._pending.items() if v}
        written = 0
        for (day, group), delta in todo.items():
            scale = SCALE[UNITS[group]]
            amount = int(round(delta * scale))
            name = f"b:{group}"
            total = None
            for _ in range(2):
                try:
                    with SessionLocal() as db:
                        if db.scalar(select(DailyCounter.id).where(DailyCounter.day == day, DailyCounter.name == name)) is None:
                            db.add(DailyCounter(day=day, name=name, value=0))
                            db.flush()
                        db.execute(update(DailyCounter).where(DailyCounter.day == day, DailyCounter.name == name)
                                   .values(value=DailyCounter.value + amount))
                        db.commit()
                        total = db.scalar(select(DailyCounter.value).where(DailyCounter.day == day, DailyCounter.name == name))
                    break
                except IntegrityError:  # another worker created the row first
                    continue
                except Exception:  # noqa: BLE001 - counts stay pending and go out next time
                    log.debug("budget flush failed", exc_info=True)
                    break
            if total is None:
                continue
            with self._lock:
                self._pending[(day, group)] = self._pending.get((day, group), 0.0) - delta
                self._synced[(day, group)] = total / scale
            written += 1
        with self._lock:  # forget finished days
            today = self.day_key()
            for d in [k for k in self._synced if k[0] != today and not self._pending.get(k)]:
                self._synced.pop(d, None)
                self._pending.pop(d, None)
        return written

    # --- views -----------------------------------------------------------------------------------------------------------
    def status(self) -> list[dict]:
        out = []
        for group in ("google", "llm", "search", "railradar", "irctc1", "plans", "sms"):
            lim = self.limit(group)
            used = self.used(group)
            out.append({"group": group, "unit": UNITS[group], "limit": lim, "used": round(used, 4),
                        "percent": round(100 * used / lim, 1) if lim else None, "warned": (self.day_key(), group) in self._warned,
                        "stopped": bool(lim is not None and used >= lim)})
        return out

    def reset(self) -> None:
        """Tests: forget what is held in memory (the database counters are untouched)."""
        with self._lock:
            self._pending.clear()
            self._synced.clear()
            self._warned.clear()


budgets = Budgets()


def start_flush_thread() -> threading.Thread | None:
    s = get_settings()
    if not s.gateway_enabled or s.budget_flush_seconds <= 0 or s.environment == "test":
        return None

    def loop():
        stop = threading.Event()
        while True:
            stop.wait(s.budget_flush_seconds)
            try:
                budgets.flush()
            except Exception:  # noqa: BLE001
                log.debug("budget flush cycle failed", exc_info=True)
    t = threading.Thread(target=loop, daemon=True, name="budget-flush")
    t.start()
    return t
