"""Google content in saved trips (ADR 0005, invariant I6).

Our data is ours and kept for as long as the trip exists: stop order, our planned times, our cost estimates, budget, user
notes, and each stop's Google `place_id`. Google's content is refreshable and is never shown once it is older than
30 days: place name, address, rating, opening hours, coordinates (and the route lines drawn between stops).

Three pieces work together:
- `refresh_trip` re-fetches a stale trip's places from their place_id when the trip is opened (cheapest field mask,
  bounded per trip, per day and in time);
- `gate_item` / `gate_alternative` run on every response (owner view, share page, share text) and replace whatever is
  still expired with "Details unavailable — open in Google Maps";
- `scrub_saved_trips` (called from retention.py) clears expired Google fields from the stored rows.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import wait
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from sqlalchemy import select

from app.core.config import get_settings
from app.core.telemetry import ContextThreadPool
from app.models.counters import DailyCounter

log = logging.getLogger("tripai.google_content")

GOOGLE_MAX_AGE = timedelta(days=30)
UNAVAILABLE = "Details unavailable — open in Google Maps"
COUNTER = "google_refresh"
# Meta keys that came from Google. Everything else in meta (our keys, times, per-person costs) is ours.
GOOGLE_META_KEYS = ("lat", "lng", "address", "rating", "rating_count", "tags", "website", "veg_only")
# Titles built as "<our words><Google place name>": our words are kept, the name goes.
_PREFIXES = {"Check out: ": "your stay", "Check in: ": "your stay", "Start from ": "your stay", "Back to ": "your stay",
             "Back at ": "your last stop"}
_PREFIX = re.compile("^(" + "|".join(re.escape(p) for p in _PREFIXES) + ")")
_FREE = "Free time"  # "Free time near <a Google place>" loses the place
_KEEP_SUBTITLE = ("stay", "free", "sunset", "arrival", "transport")  # ours ("Day 1", "Sunset at 18:10"); others are Google's


def _aware(dt: datetime | None) -> datetime | None:
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=UTC))


def expired(fetched_at: datetime | None, now: datetime | None = None) -> bool:
    """True when Google content stamped at `fetched_at` is older than 30 days. NULL = no Google content."""
    if fetched_at is None:
        return False
    return (now or datetime.now(UTC)) - _aware(fetched_at) > GOOGLE_MAX_AGE


def valid_until(fetched_at: datetime | None) -> datetime | None:
    return None if fetched_at is None else _aware(fetched_at) + GOOGLE_MAX_AGE


def provider_id(row) -> str | None:
    """The Google place_id of an item or alternative, if its place is a Google one."""
    place = getattr(row, "place", None)
    return place.provider_place_id if place is not None and place.provider == "google" and place.provider_place_id else None


def maps_url(place_id: str, query: str | None) -> str:
    """Google Maps' documented "search" URL. `query` is required alongside `query_place_id`, so the trip's destination
    as the traveller typed it (our data) is used."""
    return ("https://www.google.com/maps/search/?api=1&query=" + quote((query or "").strip() or "place", safe="")
            + "&query_place_id=" + quote(place_id, safe=""))


def _prefix(title: str | None, meta: dict | None) -> str | None:
    m = _PREFIX.match(title or "")
    return m.group(1) if m else (meta or {}).get("title_prefix")


# --- The gate: runs on every response ---------------------------------------------------------------------------------

def unavailable_title(kind: str, title: str | None, meta: dict | None, has_place: bool) -> str:
    if kind == "free":
        return _FREE
    prefix = _prefix(title, meta)
    if prefix:
        return prefix + _PREFIXES[prefix]
    return UNAVAILABLE if has_place else (title or "")


def gate_item(out: dict, item, dest: str | None, now: datetime | None = None) -> dict:
    """Replace an item's expired Google content. Our own fields are untouched."""
    if not expired(item.google_fetched_at, now):
        return out
    pid = provider_id(item)
    meta = {k: v for k, v in (out.get("meta") or {}).items() if k not in GOOGLE_META_KEYS}
    meta["google_expired"] = True
    if pid:
        meta["maps_url"] = maps_url(pid, dest)
    return {**out, "title": unavailable_title(item.kind, out.get("title"), out.get("meta"), bool(pid)),
            "subtitle": out.get("subtitle") if item.kind in _KEEP_SUBTITLE else None,
            "polyline": None, "opening_time": None, "closing_time": None, "hours_state": None, "hours_note": None,
            "explanation": None, "reasons": [], "sources": [], "meta": meta}


def gate_alternative(out: dict, alt, dest: str | None, now: datetime | None = None) -> dict:
    if not expired(alt.google_fetched_at, now):
        return out
    pid = provider_id(alt)
    meta = {k: v for k, v in (out.get("meta") or {}).items() if k not in GOOGLE_META_KEYS}
    meta["google_expired"] = True
    if pid:
        meta["maps_url"] = maps_url(pid, dest)
    return {**out, "title": UNAVAILABLE, "reason": None, "meta": meta}


def narration_expired(items, itinerary_created_at: datetime, now: datetime | None = None) -> bool:
    """The summary and tips name places (written from Google content when the plan was made): they go with it."""
    return expired(itinerary_created_at, now) and any(i.google_fetched_at is not None for i in items)


# --- At rest: clear the fields themselves ------------------------------------------------------------------------------

def scrub_item(i) -> None:
    prefix = _prefix(i.title, i.meta)
    meta = {k: v for k, v in (i.meta or {}).items() if k not in GOOGLE_META_KEYS}
    if prefix:
        meta["title_prefix"] = prefix  # so "Check in: " survives the hotel's name
    i.meta = meta
    if i.kind == "free":
        i.title = _FREE
    elif prefix or i.place_id:
        i.title = ""
    if i.kind not in _KEEP_SUBTITLE:
        i.subtitle = None
    i.polyline = i.opening_time = i.closing_time = i.hours_state = i.hours_note = None
    i.explanation, i.reasons, i.sources = None, [], []


def scrub_alternative(a) -> None:
    a.meta = {k: v for k, v in (a.meta or {}).items() if k not in GOOGLE_META_KEYS}
    a.title, a.reason = "", None


def scrub_saved_trips(db, now: datetime | None = None) -> int:
    """Clear Google fields older than 30 days from itinerary items and alternatives. Returns how many rows changed."""
    from app.models.trip import Alternative, ItineraryItem
    cutoff = (now or datetime.now(UTC)) - GOOGLE_MAX_AGE
    n = 0
    for i in db.scalars(select(ItineraryItem).where(ItineraryItem.google_fetched_at < cutoff)).unique().all():
        before = (i.title, dict(i.meta or {}), i.polyline, i.explanation, list(i.reasons or []))
        scrub_item(i)
        n += before != (i.title, dict(i.meta or {}), i.polyline, i.explanation, list(i.reasons or []))
    for a in db.scalars(select(Alternative).where(Alternative.google_fetched_at < cutoff,
                                                  Alternative.title != "")).unique().all():
        scrub_alternative(a)
        n += 1
    return n


# --- Refresh when a trip is opened --------------------------------------------------------------------------------------

def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def refreshed_today(db) -> int:
    row = db.scalar(select(DailyCounter).where(DailyCounter.day == _today(), DailyCounter.name == COUNTER))
    return row.value if row else 0


def _bump(db, n: int) -> None:
    row = db.scalar(select(DailyCounter).where(DailyCounter.day == _today(), DailyCounter.name == COUNTER))
    if row is None:
        row = DailyCounter(day=_today(), name=COUNTER, value=0)
        db.add(row)
    row.value += n
    db.commit()  # the spend is recorded before the calls are made


def apply_place(item, p, now: datetime) -> None:
    """Write a fresh Google place onto an item. The old Google content is cleared first, so nothing stale survives."""
    from app.utils import hours as H
    prefix = _prefix(item.title, item.meta)
    scrub_item(item)
    item.title = ((prefix or "") + p.name)[:255]
    meta = {k: v for k, v in (item.meta or {}).items() if k != "title_prefix"}
    meta.update(lat=p.lat, lng=p.lng, address=p.address, rating=p.rating, rating_count=p.rating_count)
    item.meta = meta
    if p.rating:
        count = f" by {p.rating_count:,} visitors" if p.rating_count else ""
        item.reasons = [{"key": "rating", "text": f"rated {p.rating}{count}"}]
    if p.opening_hours:
        o, c = H.span_for_display(p.opening_hours, item.start_at.date(), item.start_at)
        item.opening_time, item.closing_time = o, c
        item.hours_state = "verified_provider" if o else "unavailable"
    item.google_fetched_at = now


def apply_alternative(alt, p, now: datetime) -> None:
    scrub_alternative(alt)
    alt.title = p.name[:255]
    alt.meta = {**(alt.meta or {}), "lat": p.lat, "lng": p.lng, "rating": p.rating}
    alt.google_fetched_at = now


def refresh_trip(db, trip, providers, now: datetime | None = None) -> dict:
    """Re-fetch the Google content of a trip's current itinerary where it is older than 30 days. Bounded: at most
    GOOGLE_REFRESH_PER_TRIP places a trip, GOOGLE_REFRESH_DAILY_CAP a day, GOOGLE_REFRESH_BUDGET_SECONDS of waiting.
    Whatever is not refreshed stays expired, and the gate shows it as unavailable."""
    s = get_settings()
    now = now or datetime.now(UTC)
    it = trip.current_itinerary
    result = {"stale": 0, "refreshed": 0, "failed": 0, "skipped": None}
    if it is None or s.google_refresh_daily_cap <= 0:
        result["skipped"] = "off" if it is not None else "no_itinerary"
        return result
    items = [i for i in it.items if expired(i.google_fetched_at, now) and provider_id(i)]
    alts = [a for i in it.items for a in i.alternatives if expired(a.google_fetched_at, now) and provider_id(a)]
    wanted: list[str] = []
    for row in [*items, *alts]:
        pid = provider_id(row)
        if pid not in wanted:
            wanted.append(pid)
    result["stale"] = len(wanted)
    room = min(s.google_refresh_per_trip, s.google_refresh_daily_cap - refreshed_today(db))
    wanted = wanted[:max(0, room)]
    if not wanted:
        result["skipped"] = "cap" if result["stale"] else None
        return result
    _bump(db, len(wanted))
    pool = ContextThreadPool(max_workers=min(6, len(wanted)))
    futures = {pool.submit(providers.places.refresh_place, pid): pid for pid in wanted}
    done, _ = wait(futures, timeout=s.google_refresh_budget_seconds)
    pool.shutdown(wait=False, cancel_futures=True)
    fresh = {}
    for f in done:
        try:
            p = f.result()
        except Exception as exc:  # noqa: BLE001 - a failed refresh just leaves the stop "unavailable"
            log.info("Google place refresh failed: %s", exc)
            continue
        if p is not None:
            fresh[futures[f]] = p
    for i in items:
        if provider_id(i) in fresh:
            apply_place(i, fresh[provider_id(i)], now)
    for a in alts:
        if provider_id(a) in fresh:
            apply_alternative(a, fresh[provider_id(a)], now)
    db.commit()
    result["refreshed"], result["failed"] = len(fresh), len(wanted) - len(fresh)
    return result
