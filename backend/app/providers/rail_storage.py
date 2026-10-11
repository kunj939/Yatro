"""Two-tier rail storage (ADR 0003).

Tier 1 — route knowledge. Internal only: decides WHAT to look up, never supplies anything a traveller sees.
  - rail_city_routes: the station pairs that last returned trains for a city pair (365 days)
  - rail_pair_results: per station pair and weekday, "trains" (365 days) or a genuine "empty" (60 days), plus the train
    numbers seen (only to prioritise refreshes)
Tier 2 — timetable facts. Shown to travellers, validated before saving, at most 45 days old (30 in winter).
  - rail_timetables: validated rows per (from, to, weekday, source), with freshness, refresh flags and reports

Only sources on the RAIL_STORAGE_SOURCES allow-list may write either tier (I7: RailRadar confirmed; IRCTC1 not). Google
never writes here (I6): it isn't a rail source, and the allow-list would refuse it anyway.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.core.provenance import freshness, max_timetable_age
from app.db.session import SessionLocal
from app.models.rail import RailCityRoute, RailDailyStat, RailPairResult, RailTimetable
from app.providers.rail_stations import STATIONS
from app.utils.geo import haversine_m

log = logging.getLogger("tripai.rail_storage")

CITY_ROUTE_TTL = timedelta(days=365)
PAIR_TRAINS_TTL = timedelta(days=365)
PAIR_EMPTY_TTL = timedelta(days=60)
MIN_KMH, MAX_KMH = 10, 180
REFRESH_PRIORITY = {"aging": 1, "stale": 2, "reported": 3}


def storage_allowed(source: str) -> bool:
    from app.core.config import get_settings
    allowed = {s.strip().lower() for s in get_settings().rail_storage_sources.split(",") if s.strip()}
    return source.lower() in allowed


def _aware(dt: datetime | None) -> datetime | None:
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=UTC))


def city_key(place) -> str:
    return (getattr(place, "name", "") or "").split(",")[0].strip().lower()


# --- Validation (Tier 2 only stores what passes) --------------------------------------------------------------------

def _first(d: dict, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return default


def _hhmm(v) -> tuple[int, int] | None:
    m = re.match(r"^\s*(\d{1,2})[:.](\d{2})", str(v or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return int(m.group(1)), int(m.group(2))


def _fields(row: dict) -> dict:
    """The fields validation needs, from either a RailRadar-shaped row or a flat (IRCTC1-shaped) one."""
    train, frm, to = row.get("train") or {}, row.get("from") or {}, row.get("to") or {}
    flat = {k: v for k, v in row.items() if not isinstance(v, dict)}  # a nested "from" without a code isn't a code
    train, frm, to = (d if isinstance(d, dict) else {} for d in (train, frm, to))
    return {
        "name": str(_first(train, "name", "trainName") or _first(flat, "train_name", "trainName", "name") or "").strip(),
        "number": str(_first(train, "number", "trainNumber") or _first(flat, "train_number", "trainNumber", "number") or "").strip(),
        "dep": _hhmm(_first(frm, "departure", "std") or _first(flat, "from_std", "departure_time", "departureTime")),
        "arr": _hhmm(_first(to, "arrival", "sta") or _first(flat, "to_sta", "arrival_time", "arrivalTime")),
        "from": str(_first(frm, "code") or _first(flat, "from", "from_station_code", "fromStationCode") or "").upper(),
        "to": str(_first(to, "code") or _first(flat, "to", "to_station_code", "toStationCode") or "").upper(),
        "minutes": row.get("duration") if isinstance(row.get("duration"), (int, float)) else None,
        "km": row.get("distance") if isinstance(row.get("distance"), (int, float)) else None,
    }


def _minutes(f: dict) -> int | None:
    if f["minutes"]:
        return int(f["minutes"])
    if f["dep"] and f["arr"]:
        m = (f["arr"][0] * 60 + f["arr"][1]) - (f["dep"][0] * 60 + f["dep"][1])
        return m if m > 0 else m + 1440  # arrives the next day
    return None


def validate_rows(rows: list, frm: str, to: str) -> tuple[list, dict]:
    """(rows to keep, report). A row is kept only with a name, a 4-6 digit number, parseable departure and arrival,
    the requested station codes, and a positive, plausible duration (10-180 km/h). A train number listed twice with
    different times is dropped entirely. If most rows fail, nothing is kept."""
    dropped: Counter = Counter()
    kept: list[tuple[dict, dict]] = []
    straight = None
    if frm in STATIONS and to in STATIONS:
        a, b = STATIONS[frm], STATIONS[to]
        straight = haversine_m(a[1], a[2], b[1], b[2]) / 1000 * 1.3
    for row in rows:
        if not isinstance(row, dict):
            dropped["not_a_row"] += 1
            continue
        f = _fields(row)
        mins = _minutes(f)
        km = f["km"] or straight
        reason = ("no_name" if not f["name"] else "bad_number" if not re.fullmatch(r"\d{4,6}", f["number"])
                  else "bad_departure" if not f["dep"] else "bad_arrival" if not (f["arr"] or f["minutes"])
                  else "wrong_station" if f["from"] != frm.upper() or f["to"] != to.upper()
                  else "bad_duration" if not mins or mins <= 0
                  else "implausible_speed" if km and not (MIN_KMH <= km / (mins / 60) <= MAX_KMH) else None)
        if reason:
            dropped[reason] += 1
            continue
        if len(f["number"]) != 5:  # Indian Railways numbers are normally 5 digits; valid data is not rejected over it
            log.info("rail validation %s->%s: train %s has a %d-digit number (kept)", frm, to, f["number"], len(f["number"]))
        kept.append((row, f))
    # the same train number with conflicting times: keep none of them
    times: dict[str, set] = {}
    for _, f in kept:
        times.setdefault(f["number"], set()).add((f["dep"], f["arr"], _minutes(f)))
    conflicted = {n for n, ts in times.items() if len(ts) > 1}
    if conflicted:
        dropped["conflicting_duplicate"] += sum(1 for _, f in kept if f["number"] in conflicted)
    good, seen = [], set()
    for row, f in kept:
        if f["number"] in conflicted or f["number"] in seen:
            continue
        seen.add(f["number"])
        good.append(row)
    report = {"status": "ok", "input": len(rows), "kept": len(good), "dropped": dict(dropped)}
    if rows and len(good) * 2 < len(rows):
        report["status"] = "rejected"
        return [], report
    if dropped:
        log.info("rail validation %s->%s: kept %d of %d, dropped %s", frm, to, len(good), len(rows), dict(dropped))
    return good, report


# --- Tier 2: timetable facts ----------------------------------------------------------------------------------------

def _signature(rows: list) -> dict[str, tuple]:
    out = {}
    for r in rows:
        f = _fields(r)
        out[f["number"]] = (f["dep"], f["arr"])
    return out


def save_timetable(source: str, frm: str, to: str, weekday: int, rows: list, now: datetime | None = None) -> dict:
    """Validate, then save (replacing any older copy and logging what changed). Returns the validation report."""
    if not storage_allowed(source):
        return {"status": "not_allowed"}
    now = now or datetime.now(UTC)
    good, report = validate_rows(rows, frm, to)
    if not good:
        bump(source, tier2_rejected=1)
        return report
    with SessionLocal() as db:
        row = db.scalar(select(RailTimetable).where(RailTimetable.source == source, RailTimetable.from_code == frm,
                                                    RailTimetable.to_code == to, RailTimetable.weekday == weekday))
        if row is not None and row.rows:
            old, new = _signature(row.rows), _signature(good)
            added, removed = sorted(set(new) - set(old)), sorted(set(old) - set(new))
            retimed = sorted(n for n in set(old) & set(new) if old[n] != new[n])
            if added or removed or retimed:
                log.info("timetable %s %s->%s (%s) changed: added %s, removed %s, retimed %s", source, frm, to,
                         weekday, added, removed, retimed)
                report["changes"] = {"added": added, "removed": removed, "retimed": retimed}
                bump(source, tier2_replaced=1)
        row = row or RailTimetable(source=source, from_code=frm, to_code=to, weekday=weekday)
        row.rows, row.fetched_at, row.last_verified_at = good, now, now
        row.expires_at = now + max_timetable_age(now)
        row.validation, row.needs_refresh, row.refresh_priority = report, False, 0
        db.add(row)
        db.commit()
    bump(source, tier2_saved=1)
    return report


def load_timetable(source: str, frm: str, to: str, weekday: int, now: datetime | None = None) -> tuple[list, datetime, str] | None:
    """(rows, saved_at, freshness) if a usable saved copy exists, else None. Expired copies are never returned.
    Aging and stale copies are returned (stale-while-revalidate) and queued for refresh."""
    if not storage_allowed(source):
        return None
    now = now or datetime.now(UTC)
    try:
        with SessionLocal() as db:
            row = db.scalar(select(RailTimetable).where(RailTimetable.source == source, RailTimetable.from_code == frm,
                                                        RailTimetable.to_code == to, RailTimetable.weekday == weekday))
            if row is None or not row.rows:
                bump(source, tier2_miss=1)
                return None
            saved_at = _aware(row.fetched_at)
            fresh = freshness_of(saved_at, now)
            ends = _aware(row.expires_at)  # also ended early: a refresh found the route no longer runs
            if fresh == "expired" or (ends is not None and ends <= now):
                bump(source, tier2_miss=1, tier2_expired=1)
                return None
            rows = list(row.rows)
            if (row.validation or {}).get("status") == "legacy":  # saved before validation existed: check now
                rows, report = validate_rows(rows, frm, to)
                if not rows:
                    bump(source, tier2_miss=1)
                    return None
            if fresh in ("aging", "stale") and row.refresh_priority < REFRESH_PRIORITY[fresh]:
                row.needs_refresh, row.refresh_priority = True, REFRESH_PRIORITY[fresh]
                db.commit()
            bump(source, tier2_hit=1, **{f"tier2_{fresh}": 1})
            return rows, saved_at, fresh
    except Exception as exc:  # noqa: BLE001 - saved data is an optimisation, never a failure
        log.info("saved timetable unavailable (%s %s->%s): %s", source, frm, to, exc)
        return None


def freshness_of(saved_at: datetime, now: datetime | None = None) -> str:
    from app.core.provenance import SourceKind
    return freshness(SourceKind.SAVED_TIMETABLE, saved_at, now)


def mark_reported(source: str, frm: str, to: str, weekday: int, now: datetime | None = None) -> bool:
    """A traveller says this route's train didn't run or ran at another time: refresh it first."""
    now = now or datetime.now(UTC)
    with SessionLocal() as db:
        row = db.scalar(select(RailTimetable).where(RailTimetable.source == source, RailTimetable.from_code == frm,
                                                    RailTimetable.to_code == to, RailTimetable.weekday == weekday))
        if row is None:
            return False
        row.needs_refresh, row.refresh_priority, row.reported_at = True, REFRESH_PRIORITY["reported"], now
        db.commit()
    bump(source, reports=1)
    return True


# --- Tier 1: route knowledge (never shown) --------------------------------------------------------------------------

def record_pair_result(source: str, frm: str, to: str, weekday: int, outcome: str, numbers: list[str] | None = None,
                       now: datetime | None = None) -> None:
    """outcome "trains" or "empty". Callers must only pass "empty" for a successful response with zero rows."""
    if not storage_allowed(source) or outcome not in ("trains", "empty"):
        return
    now = now or datetime.now(UTC)
    try:
        with SessionLocal() as db:
            row = db.scalar(select(RailPairResult).where(RailPairResult.source == source, RailPairResult.from_code == frm,
                                                         RailPairResult.to_code == to, RailPairResult.weekday == weekday))
            row = row or RailPairResult(source=source, from_code=frm, to_code=to, weekday=weekday)
            row.outcome, row.checked_at = outcome, now
            row.expires_at = now + (PAIR_TRAINS_TTL if outcome == "trains" else PAIR_EMPTY_TTL)
            row.train_numbers = sorted(set(numbers or []))[:50] if outcome == "trains" else []
            db.add(row)
            db.commit()
    except Exception as exc:  # noqa: BLE001
        log.info("couldn't record pair result: %s", exc)


def known_empty(source: str, frm: str, to: str, weekday: int, now: datetime | None = None) -> bool:
    if not storage_allowed(source):
        return False
    now = now or datetime.now(UTC)
    try:
        with SessionLocal() as db:
            row = db.scalar(select(RailPairResult).where(RailPairResult.source == source, RailPairResult.from_code == frm,
                                                         RailPairResult.to_code == to, RailPairResult.weekday == weekday))
            return bool(row and row.outcome == "empty" and _aware(row.expires_at) > now)
    except Exception:  # noqa: BLE001
        return False


def working_pairs(source: str, origin_key: str, dest_key: str, now: datetime | None = None) -> list[tuple[str, str]]:
    if not storage_allowed(source):
        return []
    now = now or datetime.now(UTC)
    try:
        with SessionLocal() as db:
            row = db.scalar(select(RailCityRoute).where(RailCityRoute.source == source, RailCityRoute.origin_key == origin_key,
                                                        RailCityRoute.dest_key == dest_key))
            if row is None or _aware(row.expires_at) <= now:
                return []
            return [tuple(p) for p in row.working_pairs or []]
    except Exception:  # noqa: BLE001
        return []


def record_working_pairs(source: str, origin_key: str, dest_key: str, pairs: list[tuple[str, str]],
                         now: datetime | None = None) -> None:
    """Replace the city pair's working pairs with the ones that just returned trains (newer results win)."""
    if not storage_allowed(source) or not pairs:
        return
    now = now or datetime.now(UTC)
    try:
        with SessionLocal() as db:
            row = db.scalar(select(RailCityRoute).where(RailCityRoute.source == source, RailCityRoute.origin_key == origin_key,
                                                        RailCityRoute.dest_key == dest_key))
            row = row or RailCityRoute(source=source, origin_key=origin_key, dest_key=dest_key)
            row.working_pairs = [list(p) for p in dict.fromkeys(pairs)][:5]
            row.checked_at, row.expires_at = now, now + CITY_ROUTE_TTL
            db.add(row)
            db.commit()
    except Exception as exc:  # noqa: BLE001
        log.info("couldn't record working pairs: %s", exc)


# --- Metrics ---------------------------------------------------------------------------------------------------------

def bump(source: str, day: date | None = None, **counters: int) -> None:
    """Add to today's counters for a source (Tier 2 hits/misses/freshness, Tier 1 savings, refresh calls)."""
    from app.core import telemetry
    tr = telemetry.current()
    if tr is not None:
        tr.add("cache", {"provider": f"rail-tiers:{source}", "hit": bool(counters.get("tier2_hit")), **counters})
    key = (day or datetime.now(UTC).date()).isoformat()
    try:
        with SessionLocal() as db:
            row = db.scalar(select(RailDailyStat).where(RailDailyStat.day == key, RailDailyStat.source == source))
            row = row or RailDailyStat(day=key, source=source, counters={})
            c = dict(row.counters or {})
            for k, v in counters.items():
                c[k] = c.get(k, 0) + v
            row.counters = c
            db.add(row)
            db.commit()
    except Exception:  # noqa: BLE001 - metrics never break planning
        log.debug("rail stats write failed", exc_info=True)


def daily_stats(days: int = 30) -> list[dict]:
    since = (datetime.now(UTC).date() - timedelta(days=days - 1)).isoformat()
    with SessionLocal() as db:
        rows = db.scalars(select(RailDailyStat).where(RailDailyStat.day >= since).order_by(RailDailyStat.day)).all()
        return [{"day": r.day, "source": r.source, **(r.counters or {})} for r in rows]
