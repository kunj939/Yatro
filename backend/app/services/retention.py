"""Data retention (ADR 0002, invariant I6).

Google Maps Platform content may not be kept beyond its terms: place IDs indefinitely, coordinates at most 30 days, and
other place content only as a temporary cache. The `places` table is a write-side mirror (nothing reads it back as a
cache), so after 30 days a Google row keeps only its `provider_place_id`; everything else is cleared. Rows are not
deleted, because saved itineraries reference them.

Expired `api_cache` rows are deleted too (Google responses there already expire within 7 days).

Runs once at startup and then every 24 h in a daemon thread; RETENTION_ENABLED=false switches it off.
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select

from app.db.session import SessionLocal
from app.models.catalog import ApiCache, Hotel, Place, Restaurant

log = logging.getLogger("tripai.retention")

GOOGLE_CONTENT_MAX_AGE = timedelta(days=30)
GOOGLE_PROVIDERS = ("google",)
INTERVAL = timedelta(hours=24)


def scrub_google_places(db, now: datetime | None = None) -> int:
    """Clear everything but the place ID from Google rows older than 30 days. Returns how many were scrubbed."""
    cutoff = (now or datetime.now(UTC)) - GOOGLE_CONTENT_MAX_AGE
    rows = db.scalars(select(Place).where(Place.provider.in_(GOOGLE_PROVIDERS), Place.name != "",
                                          or_(Place.fetched_at < cutoff,
                                              Place.fetched_at.is_(None) & (Place.updated_at < cutoff)))).all()
    for p in rows:
        p.name, p.lat, p.lng = "", 0.0, 0.0
        p.address = p.rating = p.rating_count = p.price_level = p.opening_hours = None
        p.website = p.business_status = p.entry_fee = p.description = p.city = None
        p.recommended_minutes = None
        p.types, p.tags, p.meta = [], [], {}
    ids = [p.id for p in rows]
    if ids:  # restaurant and hotel details were derived from the same Google content
        db.execute(delete(Restaurant).where(Restaurant.place_id.in_(ids)))
        db.execute(delete(Hotel).where(Hotel.place_id.in_(ids)))
    return len(rows)


def scrub_saved_trips(db, now: datetime | None = None) -> int:
    """I6, ADR 0005: the same 30-day limit for the Google content copied into saved itineraries."""
    from app.services import google_content
    return google_content.scrub_saved_trips(db, now)


def purge_expired_cache(db, now: datetime | None = None) -> int:
    res = db.execute(delete(ApiCache).where(ApiCache.expires_at < (now or datetime.now(UTC))))
    return res.rowcount or 0


def purge_rail(db, now: datetime | None = None) -> dict:
    """Expired rows of both rail tiers (ADR 0008), plus anything from a source that may not be stored (I7).

    Tier 2 timetables are never shown past 45 days, so rows a week past their expiry (or 90 days old) go. Tier 1 knowledge and
    station lookups go when their own lifetime has passed; counters and reports are kept for a year."""
    from app.models.rail import RailCityRoute, RailDailyStat, RailPairResult, RailStationLookup, RailTimetable, RailTrainReport
    from app.providers.rail_storage import storage_allowed
    now = now or datetime.now(UTC)
    out: dict[str, int] = {}
    t2 = delete(RailTimetable).where(or_(RailTimetable.expires_at < now - timedelta(days=7), RailTimetable.fetched_at < now - timedelta(days=90)))
    out["tier2_timetables"] = db.execute(t2).rowcount or 0
    out["tier1_city_routes"] = db.execute(delete(RailCityRoute).where(RailCityRoute.expires_at < now)).rowcount or 0
    out["tier1_pair_results"] = db.execute(delete(RailPairResult).where(RailPairResult.expires_at < now)).rowcount or 0
    out["station_lookups"] = db.execute(delete(RailStationLookup).where(RailStationLookup.fetched_at < now - timedelta(days=90))).rowcount or 0
    out["train_reports"] = db.execute(delete(RailTrainReport).where(RailTrainReport.created_at < now - timedelta(days=365))).rowcount or 0
    out["daily_stats"] = db.execute(delete(RailDailyStat).where(RailDailyStat.day < (now - timedelta(days=400)).date().isoformat())).rowcount or 0
    forbidden = 0
    for model in (RailTimetable, RailCityRoute, RailPairResult, RailStationLookup):
        for source in {s for (s,) in db.execute(select(model.source).distinct()).all()}:
            if not storage_allowed(source):  # e.g. IRCTC1: not confirmed, so nothing of it is kept
                forbidden += db.execute(delete(model).where(model.source == source)).rowcount or 0
    out["not_allowed_sources"] = forbidden
    return out


def purge_plan_jobs(db, now: datetime | None = None) -> int:
    from app.services import plan_jobs
    return plan_jobs.purge_old(db, now)


def run_retention(now: datetime | None = None) -> dict:
    with SessionLocal() as db:
        scrubbed = scrub_google_places(db, now)
        scrubbed_trips = scrub_saved_trips(db, now)
        purged = purge_expired_cache(db, now)
        rail = purge_rail(db, now)
        jobs = purge_plan_jobs(db, now)
        from app.services import group_service
        groups = group_service.purge_old(db, now)  # group-plan replies 30 days after (ADR 0022)
        from app.services import prewarm
        prewarm.run(db, now)  # off unless PREWARM_ENABLED (ADR 0008)
        db.commit()
    if scrubbed or purged or scrubbed_trips or any(rail.values()) or jobs or groups:
        log.info("retention: scrubbed %d Google places and %d saved-trip rows older than 30 days, deleted %d expired cache rows, "
                 "rail rows %s, %d old plan jobs, %d old group plans", scrubbed, scrubbed_trips, purged,
                 {k: v for k, v in rail.items() if v}, jobs, groups)
    return {"google_places_scrubbed": scrubbed, "saved_trip_rows_scrubbed": scrubbed_trips, "cache_rows_deleted": purged,
            "rail": rail, "plan_jobs_deleted": jobs, "group_plans_deleted": groups}


def start_retention_thread() -> threading.Thread | None:
    from app.core.config import get_settings
    if not get_settings().retention_enabled:
        return None

    def loop():
        stop = threading.Event()
        while True:
            try:
                run_retention()
            except Exception:  # noqa: BLE001 - retention must never take the app down; it retries next cycle
                log.exception("retention run failed; will retry in 24 h")
            stop.wait(INTERVAL.total_seconds())
    t = threading.Thread(target=loop, daemon=True, name="retention")
    t.start()
    return t
