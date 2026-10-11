"""Nightly pre-warming of saved timetables (ADR 0008). Interface, **off** (PREWARM_ENABLED=false).

What it would do: before travellers ask, queue the Tier 2 timetables most worth refreshing, so a plan finds a fresh copy instead of
an aging one. Every row in Tier 2 was requested by a traveller at some point (rows are created by a request or a refresh), and
only routes Tier 1 still knows to work are considered. Stale copies go before aging ones, oldest first.

It only *marks* rows (`needs_refresh`, `refresh_priority`); the existing refresh worker makes the calls, inside
RAIL_REFRESH_DAILY_CAP, in the background lane of the gateway budget (ADR 0007). The number it marks is limited to
PREWARM_BUDGET_SHARE of the day's RailRadar calls, minus what refresh already spent today.

Why it is off: RailRadar allows about 30 calls a day and 10 already go to demand-driven refresh. The metric that would justify it is
`tier2_aging` + `tier2_stale` hits per day in /system/rail-stats: if travellers keep landing on aging copies, turn it on.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.config import get_settings
from app.models.rail import RailCityRoute, RailTimetable
from app.providers import rail_storage as RS

log = logging.getLogger("tripai.prewarm")
PRIORITY = {"stale": RS.REFRESH_PRIORITY["stale"], "aging": RS.REFRESH_PRIORITY["aging"]}


def select_routes(db, top_n: int, now: datetime | None = None) -> list[RailTimetable]:
    now = now or datetime.now(UTC)
    known: set[tuple[str, str]] = set()
    for (pairs,) in db.execute(select(RailCityRoute.working_pairs).where(RailCityRoute.expires_at > now)).all():
        known.update((a, b) for a, b in (pairs or []))
    ranked = []
    for row in db.scalars(select(RailTimetable).where(RailTimetable.needs_refresh.is_(False), RailTimetable.rows != [])):
        if not RS.storage_allowed(row.source) or (known and (row.from_code, row.to_code) not in known):
            continue
        state = RS.freshness_of(RS._aware(row.fetched_at), now)  # noqa: SLF001
        if state in PRIORITY:
            ranked.append((-PRIORITY[state], RS._aware(row.fetched_at), row))  # noqa: SLF001
    ranked.sort(key=lambda x: (x[0], x[1]))
    return [r for _, _, r in ranked[:max(0, top_n)]]


def run(db, now: datetime | None = None) -> int:
    """Mark tonight's routes for refresh. Returns how many were marked (0 when off)."""
    s = get_settings()
    if not s.prewarm_enabled:
        return 0
    share = int(s.railradar_daily_calls * max(0.0, min(1.0, s.prewarm_budget_share)))
    marked = 0
    for row in select_routes(db, min(s.prewarm_top_n, share), now):
        state = RS.freshness_of(RS._aware(row.fetched_at), now)  # noqa: SLF001
        row.needs_refresh, row.refresh_priority = True, PRIORITY[state]
        marked += 1
    if marked:
        db.commit()
        log.info("prewarm: %d saved timetables queued for refresh (share %.0f%% of %d calls)", marked,
                 s.prewarm_budget_share * 100, s.railradar_daily_calls)
    return marked
