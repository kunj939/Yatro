"""Tier 2 upkeep (ADR 0003): traveller reports and the background refresh of saved timetables.

Refresh order: reported routes, then stale, then aging; oldest first within each. At most RAIL_REFRESH_DAILY_CAP live
calls a day (0 = off) until Phase 4 brings real budgets. Only RailRadar is refreshed (the only allow-listed source).
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.db.session import SessionLocal
from app.gateway import context as gateway_context
from app.models.rail import RailDailyStat, RailTimetable, RailTrainReport
from app.models.trip import Itinerary, ItineraryItem
from app.providers import rail_storage as RS

log = logging.getLogger("tripai.rail_refresh")
_CODE = re.compile(r"\(([A-Z0-9]{2,6})\)\s*$")
CHECK_EVERY = timedelta(hours=1)


def _code(label: str | None) -> str | None:
    m = _CODE.search(label or "")
    return m.group(1) if m else None


def report_train(db, user, trip, item_id, problem: str, note: str | None) -> dict:
    """A traveller says the train in `item_id` didn't run or ran at another time. Stores the report and puts each of
    its routes (both legs of a connection) at the front of the refresh queue."""
    item = db.get(ItineraryItem, item_id)
    it = db.get(Itinerary, item.itinerary_id) if item is not None else None
    if item is None or it is None or it.trip_id != trip.id:
        raise NotFound("That part of the plan wasn't found.")
    meta = item.meta or {}
    if item.kind != "transport" or meta.get("mode") != "train" or meta.get("source_kind") not in ("live_api", "saved_timetable"):
        raise AppError("Only trains from a timetable can be reported.", code="not_a_timetabled_train", status_code=422)
    source = meta.get("rail_source") or "railradar"
    legs = meta.get("legs") or [{"from_name": meta.get("from_name"), "to_name": meta.get("to_name"),
                                 "number": meta.get("number"), "depart_at": item.start_at.isoformat()}]
    routes = []
    for leg in legs:
        frm, to = _code(leg.get("from_name")), _code(leg.get("to_name"))
        if not frm or not to:
            continue
        weekday = datetime.fromisoformat(str(leg.get("depart_at"))).weekday()
        db.add(RailTrainReport(trip_id=trip.id, user_id=user.id, source=source, from_code=frm, to_code=to,
                               weekday=weekday, train_number=str(leg.get("number") or "")[:10] or None,
                               problem=problem, note=(note or "")[:500] or None))
        routes.append((frm, to, weekday))
    db.commit()
    queued = sum(1 for frm, to, wd in routes if RS.mark_reported(source, frm, to, wd))
    return {"reported": len(routes), "queued_for_refresh": queued,
            "message": "Thanks. We'll re-check this train's timetable before anyone is shown it again."}


def _calls_today(source: str) -> int:
    with SessionLocal() as db:
        row = db.scalar(select(RailDailyStat).where(RailDailyStat.day == date.today().isoformat(),
                                                    RailDailyStat.source == source))
        return int((row.counters or {}).get("refresh_calls", 0)) if row else 0


def _next_date(weekday: int, today: date | None = None) -> date:
    today = today or date.today()
    return today + timedelta(days=(weekday - today.weekday()) % 7 or 7)


def refresh_due(provider, limit: int | None = None) -> int:
    """Refresh queued routes within today's cap. `provider` is a RailRadarProvider (anything with `name` and
    `fetch_live(frm, to, day)`). Returns how many live calls were made."""
    cap = get_settings().rail_refresh_daily_cap if limit is None else limit
    left = cap - _calls_today(provider.name)
    if left <= 0:
        return 0
    with SessionLocal() as db:
        due = db.scalars(select(RailTimetable).where(RailTimetable.source == provider.name, RailTimetable.needs_refresh.is_(True))
                         .order_by(RailTimetable.refresh_priority.desc(), RailTimetable.fetched_at.asc()).limit(left)).all()
        todo = [(r.from_code, r.to_code, r.weekday) for r in due]
    made = 0
    for frm, to, wd in todo:
        try:
            with gateway_context.background():  # may use only a share of the day's budget (ADR 0007)
                rows = provider.fetch_live(frm, to, _next_date(wd))
        except Exception as exc:  # noqa: BLE001 - one failed refresh never stops the rest
            log.info("refresh %s->%s failed: %s", frm, to, exc)
            continue
        finally:
            made += 1
            RS.bump(provider.name, refresh_calls=1)
        if rows:
            RS.save_timetable(provider.name, frm, to, wd, rows)  # validated; replaces and logs differences
            RS.record_pair_result(provider.name, frm, to, wd, "trains")
        else:
            RS.record_pair_result(provider.name, frm, to, wd, "empty")
            with SessionLocal() as db:  # nothing runs now: stop showing the old copy
                row = db.scalar(select(RailTimetable).where(RailTimetable.source == provider.name, RailTimetable.from_code == frm,
                                                            RailTimetable.to_code == to, RailTimetable.weekday == wd))
                if row is not None:
                    row.expires_at, row.needs_refresh = datetime.now(UTC), False
                    db.commit()
    if made:
        log.info("rail refresh: %d live calls (cap %d/day)", made, cap)
    return made


def start_refresh_thread() -> threading.Thread | None:
    s = get_settings()
    if s.rail_refresh_daily_cap <= 0 or not s.railradar_api_key:
        return None
    from app.providers.railradar import RailRadarProvider
    provider = RailRadarProvider(None, s.railradar_api_key)

    def loop():
        stop = threading.Event()
        while True:
            try:
                refresh_due(provider)
            except Exception:  # noqa: BLE001
                log.exception("rail refresh cycle failed")
            stop.wait(CHECK_EVERY.total_seconds())
    t = threading.Thread(target=loop, daemon=True, name="rail-refresh")
    t.start()
    return t
