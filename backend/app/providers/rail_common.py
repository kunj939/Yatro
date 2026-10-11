"""Shared building blocks for Indian Railways timetable providers (see indian_rail.py for IRCTC1,
railradar.py for RailRadar). Kept here so both sources match city names to stations the same way, save and
reuse timetables the same way, and estimate seat classes the same way — one provider's fix is both providers'
fix.
"""
from __future__ import annotations

import logging
import threading
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.provenance import SourceKind, stamp
from app.core.errors import ProviderUnavailable
from app.db.session import SessionLocal
from app.models.rail import RailStationLookup, RailTimetable
from app.core.telemetry import record_cache, record_pair
from app.providers import rail_storage as RS
from app.providers.rail_stations import GROUP_OF, STATION_GROUPS, STATIONS, group_stations, nearest_stations, station_position
from app.providers.types import PlaceData, TransportData
from app.services import fare_model as F
from app.utils.geo import estimate_road, haversine_m

log = logging.getLogger("tripai.rail_common")

STATION_TTL = timedelta(days=90)  # station codes practically never change
TIMETABLE_TTL = timedelta(days=14)  # timetables change a few times a year; special trains come and go
# Invariant I1: a train name, number or time is never shown from data older than this, even when every live source
# is down. Past it, the plan falls back to the station-network estimate (no names, no times).
MAX_SAVED_TIMETABLE_AGE = timedelta(days=45)

# The busiest, most-asked-about stations: checked before spending an API call on a station search at all.
# Covers most realistic trip destinations; anything else still falls through to a live search.
KNOWN_STATIONS: dict[str, tuple[str, str]] = {
    "delhi": ("NDLS", "New Delhi"), "new delhi": ("NDLS", "New Delhi"), "gurugram": ("GGN", "Gurgaon"),
    "gurgaon": ("GGN", "Gurgaon"), "noida": ("NDLS", "New Delhi"), "faridabad": ("FDB", "Faridabad"),
    "ghaziabad": ("GZB", "Ghaziabad"), "jaipur": ("JP", "Jaipur Jn"), "agra": ("AGC", "Agra Cantt"),
    "mumbai": ("CSMT", "Mumbai CST"), "pune": ("PUNE", "Pune Jn"), "ahmedabad": ("ADI", "Ahmedabad Jn"),
    "surat": ("ST", "Surat"), "vadodara": ("BRC", "Vadodara Jn"), "bengaluru": ("SBC", "KSR Bengaluru"),
    "bangalore": ("SBC", "KSR Bengaluru"), "mysuru": ("MYS", "Mysuru Jn"), "mysore": ("MYS", "Mysuru Jn"),
    "chennai": ("MAS", "Chennai Central"), "coimbatore": ("CBE", "Coimbatore Jn"),
    "kochi": ("ERS", "Ernakulam Jn"), "cochin": ("ERS", "Ernakulam Jn"),
    "thiruvananthapuram": ("TVC", "Thiruvananthapuram Central"), "kolkata": ("HWH", "Howrah Jn"),
    "hyderabad": ("SC", "Secunderabad Jn"), "secunderabad": ("SC", "Secunderabad Jn"),
    "lucknow": ("LKO", "Lucknow NR"), "kanpur": ("CNB", "Kanpur Central"), "varanasi": ("BSB", "Varanasi Jn"),
    "prayagraj": ("PRYJ", "Prayagraj Jn"), "allahabad": ("PRYJ", "Prayagraj Jn"), "patna": ("PNBE", "Patna Jn"),
    "bhopal": ("BPL", "Bhopal Jn"), "indore": ("INDB", "Indore Jn"), "gwalior": ("GWL", "Gwalior"),
    "jodhpur": ("JU", "Jodhpur Jn"), "udaipur": ("UDZ", "Udaipur City"), "jaisalmer": ("JSM", "Jaisalmer"),
    "ajmer": ("AII", "Ajmer Jn"), "amritsar": ("ASR", "Amritsar Jn"), "chandigarh": ("CDG", "Chandigarh"),
    "shimla": ("SML", "Shimla"), "dehradun": ("DDN", "Dehradun"), "haridwar": ("HW", "Haridwar Jn"),
    "rishikesh": ("RKSH", "Rishikesh"), "nagpur": ("NGP", "Nagpur"), "nashik": ("NK", "Nashik Road"),
    "goa": ("MAO", "Madgaon Jn"), "madgaon": ("MAO", "Madgaon Jn"), "panaji": ("MAO", "Madgaon Jn"),
    "bhubaneswar": ("BBS", "Bhubaneswar"), "puri": ("PURI", "Puri"), "guwahati": ("GHY", "Guwahati"),
    "ranchi": ("RNC", "Ranchi"), "raipur": ("R", "Raipur Jn"), "visakhapatnam": ("VSKP", "Visakhapatnam"),
    "vijayawada": ("BZA", "Vijayawada Jn"), "madurai": ("MDU", "Madurai Jn"), "tirupati": ("TPTY", "Tirupati"),
    "mangaluru": ("MAQ", "Mangaluru Central"), "mangalore": ("MAQ", "Mangaluru Central"),
}

# A city with poor direct connections often still has a big station close enough to reach by cab — exactly
# what a traveller booking their own ticket would do (e.g. Gurugram -> cab to New Delhi -> direct train).
# Tried only after the city's own station comes back with nothing, in this order, capped at 2 per city (see
# find_stations()) to keep API usage bounded. (station_code, station_name); no distance is stored here — the
# real cab distance to whichever station actually gets used is computed from coordinates in _options().
NEARBY_HUBS: dict[str, list[tuple[str, str]]] = {
    "gurugram": [("NDLS", "New Delhi"), ("NZM", "Hazrat Nizamuddin")],
    "gurgaon": [("NDLS", "New Delhi"), ("NZM", "Hazrat Nizamuddin")],
    "faridabad": [("NZM", "Hazrat Nizamuddin"), ("NDLS", "New Delhi")],
    "noida": [("ANVT", "Anand Vihar Terminal"), ("NZM", "Hazrat Nizamuddin")],
    "ghaziabad": [("ANVT", "Anand Vihar Terminal"), ("NDLS", "New Delhi")],
    # Places with no railway station of their own: the trip is a train to the nearest railhead, then a road
    # transfer (the plan's first leg, by cab, from the arrival station to the first stop). Only railheads within
    # about 90 km are listed, so that transfer is a realistic one-way drive, not a day of travelling.
    "mussoorie": [("DDN", "Dehradun")], "landour": [("DDN", "Dehradun")],
    "nainital": [("KGM", "Kathgodam")], "bhimtal": [("KGM", "Kathgodam")],
    "jim corbett": [("RMR", "Ramnagar")], "corbett": [("RMR", "Ramnagar")],
    "jim corbett national park": [("RMR", "Ramnagar")],
    "pushkar": [("AII", "Ajmer Jn")], "mount abu": [("ABR", "Abu Road")],
    "ooty": [("MTP", "Mettupalayam")], "udhagamandalam": [("MTP", "Mettupalayam")],
    "darjeeling": [("NJP", "New Jalpaiguri Jn")],
    "dharamshala": [("PTK", "Pathankot Jn")], "mcleodganj": [("PTK", "Pathankot Jn")],
    "mcleod ganj": [("PTK", "Pathankot Jn")],
}

# Cities whose railway stations still use the older name, for the live station-search fallback
ALIASES = {
    "gurugram": ["Gurgaon"], "bengaluru": ["Bangalore", "KSR Bengaluru"], "mysuru": ["Mysore"],
    "kochi": ["Ernakulam"], "thiruvananthapuram": ["Trivandrum"], "puducherry": ["Pondicherry"],
    "mumbai": ["Mumbai Central", "Chhatrapati Shivaji"], "kolkata": ["Howrah", "Sealdah"],
    "delhi": ["New Delhi"], "new delhi": ["New Delhi"], "chennai": ["Chennai Central"],
    "hyderabad": ["Secunderabad", "Hyderabad Decan"], "prayagraj": ["Prayagraj", "Allahabad"],
    "vadodara": ["Vadodara"], "varanasi": ["Varanasi", "Banaras"], "belagavi": ["Belgaum"],
    "kalaburagi": ["Gulbarga"], "mangaluru": ["Mangalore"], "shivamogga": ["Shimoga"],
}
MAJOR = {"NDLS", "CSMT", "MMCT", "BCT", "HWH", "MAS", "SBC", "SC", "JP", "LKO", "ADI", "PUNE", "BPL", "ERS", "TVC"}
_SUFFIX = re.compile(r"\b(JN|JUNCTION|CANTT|CANTONMENT|CITY|TERMINUS|TERMINAL|CENTRAL|MAIN|RLY|RAILWAY|STATION)\b")
_MAIN = re.compile(r"\b(JN|JUNCTION|CENTRAL|TERMINUS|TERMINAL)\b")

NON_AC = ["2S", "SL"]  # cheapest way to travel
AC_DAY = ["CC", "3E", "3A", "EC", "2A", "1A"]  # a comfortable air-conditioned option for day journeys
AC_NIGHT = ["3E", "3A", "2A", "1A", "CC"]  # berths for overnight journeys


def name_score(station: str, city: str) -> float:
    """How well a station name matches a city. 0 means it's a different place (e.g. Goreswar for Gurugram)."""
    st = re.sub(r"[^A-Z ]", " ", station.upper())
    base = " ".join(_SUFFIX.sub(" ", st).split())
    c = " ".join(re.sub(r"[^A-Z ]", " ", city.upper()).split())
    if not c:
        return 0.0
    main = 0.5 if _MAIN.search(st) else 0.0
    if base == c:
        return 3.0 + main
    if base.startswith(c + " ") or c.startswith(base + " ") and base:
        return 2.0 + main
    if f" {c} " in f" {base} ":
        return 1.0 + main
    return 0.0


def in_india(p: PlaceData) -> bool:
    return 6.5 <= p.lat <= 35.7 and 68.0 <= p.lng <= 97.5


def pick_classes(listed: list[str], duration: timedelta) -> list[str]:
    """One budget class and one AC class, so the engine can trade money for comfort."""
    known = [c for c in listed if c in F.TRAIN_CLASSES]
    ac_order = AC_NIGHT if duration > timedelta(hours=8) else AC_DAY
    budget = next((c for c in NON_AC if c in known), None)
    ac = next((c for c in ac_order if c in known), None)
    return [c for c in (budget, ac) if c] or known[:1]


def guess_classes(train_type: str, duration: timedelta, name: str = "") -> list[str]:
    """When a source doesn't say which classes a train actually offers (RailRadar's between-stations search
    doesn't), a sensible set is assumed from the train's category and name, so an estimated fare can still be
    shown. (Jaipur's Double Decker is listed as a plain "Superfast Express" but is all chair car.)"""
    t = f"{train_type or ''} {name or ''}".lower()
    if "vande bharat" in t or "shatabdi" in t or "double decker" in t:
        return ["CC"]
    if "tejas" in t or "gatimaan" in t:
        return ["CC", "EC"]
    if "rajdhani" in t or "duronto" in t:
        return ["3A", "2A"]
    if "garib rath" in t:
        return ["3E"]
    if "passenger" in t or "local" in t or "memu" in t or "demu" in t:
        return ["2S"]
    return pick_classes(["2S", "SL", "3A", "CC", "2A", "1A", "3E", "EC"], duration)


def first(d: dict, *keys: str, default: Any = None) -> Any:
    """A field a source has spelled several different ways over time; take the first one present."""
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return default


def hhmm(value: Any) -> tuple[int, int] | None:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})", str(value or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def minutes(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    m = re.match(r"^\s*(\d{1,2}):(\d{2})", str(value or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def resolve_station_point(places, code: str, name: str, city: PlaceData, log_prefix: str,
                          max_km: float = 40) -> tuple[float, float] | None:
    """Where the station actually is. None if the map puts it further than `max_km` from the city (a wrong
    name match: a live search once offered Goreswar, in Assam, for Gurugram). A railhead deliberately chosen
    for a place with no station of its own (Dehradun for Mussoorie) is checked against a wider limit."""
    known = station_position(code)  # a station in rail_stations.py has a position already: no map lookup needed
    if known:
        lat, lng = known
    else:
        try:
            p = places.resolve(f"{name} railway station {code}", None)
        except Exception:  # noqa: BLE001 - the map check is a safety net; the name match already ranked it
            return city.lat, city.lng
        if not p:
            return city.lat, city.lng
        lat, lng = p.lat, p.lng
    if haversine_m(lat, lng, city.lat, city.lng) > max_km * 1000:
        log.info("%s: station %s %s is %d km from %s, skipped", log_prefix, code, name,
                 haversine_m(lat, lng, city.lat, city.lng) // 1000, city.name)
        return None
    return lat, lng


def find_stations(places, place: PlaceData, search_rows_for: Callable[[str], list[dict]],
                  log_prefix: str) -> list[tuple[str, str]]:
    """A city's own stations first, best match first, then — since its own station may have no direct
    route — a couple of well-connected hub stations nearby that are worth trying (see NEARBY_HUBS).

    Checks the small built-in table of well-known cities first (no API call). For anything else, `search`
    is called (a source-specific live station search) and results are scored by name against the city — a
    live search can be fuzzy (searching "Gurugram" once returned Goreswar, a village in Assam).
    """
    city = place.name.split(",")[0].strip()
    key = city.lower()
    known = KNOWN_STATIONS.get(key)
    rows: list[dict] = []
    if known:
        out = [known]
    else:
        aliases = [a for a in ALIASES.get(key, []) if a.lower() != key]
        names = aliases[:1] + [city] if aliases else [city]  # railway name first ("Gurgaon", not "Gurugram")
        for q in names[:2]:
            try:
                rows += search_rows_for(q)  # type: ignore[operator]
            except Exception as exc:  # noqa: BLE001 - station search down: nearby stations below still work
                log.info("%s: station search for %r failed (%s); using the built-in station list", log_prefix, q, exc)
                break
            if any(name_score(str(first(r, "name", "eng_name", default="")), n) >= 3 for r in rows for n in names):
                break  # a clear match: don't spend a second call
        scored: dict[str, tuple[float, str]] = {}
        for row in rows:
            code = first(row, "code", "station_code", "stationCode")
            name = first(row, "name", "eng_name", "station_name", "stationName", default=code)
            if not code:
                continue
            score = max(name_score(str(name), n) for n in names)
            if score and str(code).upper() in MAJOR:
                score += 0.8  # the city's main terminal usually has the most trains
            if score > 0 and score > scored.get(str(code).upper(), (-1, ""))[0]:
                scored[str(code).upper()] = (score, str(name).title())
        ranked = sorted(scored.items(), key=lambda kv: -kv[1][0])
        out = []
        for code, (_, name) in ranked[:4]:
            pt = resolve_station_point(places, code, name, place, log_prefix)
            if pt is not None:  # found on the map near the city, or not checkable
                out.append((code, name))
            if len(out) == 2:
                break
    # A big city has several terminals with different trains (Delhi: New Delhi, Sarai Rohilla, Nizamuddin...): add the
    # others right after its own, so a train that doesn't end at the main station is still found.
    have = {code for code, _ in out}
    for code, _ in list(out):
        for c, n in group_stations(code):
            if c not in have:
                out.append((c, n))
                have.add(c)
    out += [(code, name) for code, name in NEARBY_HUBS.get(key, []) if code not in have][:2]
    # Any place, not only the ones listed above: if it has fewer than 3 stations to try, add the nearest big
    # ones (Mussoorie -> Dehradun, then Haridwar). The trip then runs to/from those, and the rest is by road.
    if len(out) < 3:
        out += [(code, name) for code, name, _ in
                nearest_stations(place.lat, place.lng, exclude={c for c, _ in out}, limit=3 - len(out))]
    if not out:
        log.warning("%s: no railway station or nearby railhead known for %r (candidates: %s)", log_prefix, city,
                    [(first(r, "code"), first(r, "name")) for r in (rows if not known else [])[:6]])
    return out


class RailCache:
    """Our own copy of a source's timetables and station lookups, so the same route isn't fetched twice.

    Kept per source (`source`), since two sources answer in different shapes: a saved IRCTC1 row and a
    saved RailRadar row for the same route both exist side by side, each read only by its own provider.
    """

    def __init__(self, source: str, enabled: bool = True):
        # I7: only allow-listed sources store anything at all (IRCTC1 hasn't confirmed storage is allowed)
        self.source, self.enabled = source, enabled and RS.storage_allowed(source)
        self._local = threading.local()  # per-thread: plans run side by side

    def _load(self, model, **where) -> list | None:
        if not self.enabled:
            return None
        ttl = STATION_TTL if model is RailStationLookup else TIMETABLE_TTL
        try:
            with SessionLocal() as db:
                row = db.query(model).filter_by(source=self.source, **where).first()
                if row is None:
                    return None
                fetched = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=UTC)
                if datetime.now(UTC) - fetched > ttl:
                    return None
                self._local.fetched_at = fetched
                return list(row.rows or [])
        except Exception as exc:  # noqa: BLE001 - the saved copy is an optimisation, never a failure
            log.info("Saved rail data (%s) unavailable: %s", self.source, exc)
            return None

    def _save(self, model, rows: list, **where) -> None:
        if not self.enabled:
            return
        try:
            with SessionLocal() as db:
                row = db.query(model).filter_by(source=self.source, **where).first() or model(source=self.source, **where)
                row.rows, row.fetched_at = rows, datetime.now(UTC)
                db.add(row)
                db.commit()
        except Exception as exc:  # noqa: BLE001
            log.info("Couldn't save rail data (%s): %s", self.source, exc)

    def stations(self, query: str) -> list | None:
        return self._load(RailStationLookup, query=query.strip().lower())

    def save_stations(self, query: str, rows: list) -> None:
        if rows:
            self._save(RailStationLookup, rows, query=query.strip().lower())

    def timetable(self, from_code: str, to_code: str, weekday: int) -> list | None:
        """Tier 2: a validated saved copy, served while not expired (stale-while-revalidate, ADR 0003)."""
        got = RS.load_timetable(self.source, from_code, to_code, weekday) if self.enabled else None
        record_cache(f"rail-saved:{self.source}", got is not None)
        if got is None:
            return None
        rows, self._local.fetched_at, _ = got
        return rows

    @property
    def last_fetched_at(self) -> datetime | None:
        """When the row last returned by timetable() was saved (its observation time)."""
        return getattr(self._local, "fetched_at", None)

    def timetable_any_age(self, from_code: str, to_code: str, weekday: int) -> tuple[list, datetime] | None:
        """For when the live source is down: the Tier 2 copy under the same rules (never older than 45 days, 30 in
        winter; legacy rows validated). Options built from it carry its save date (mark_saved_timetable)."""
        got = RS.load_timetable(self.source, from_code, to_code, weekday) if self.enabled else None
        return (got[0], got[1]) if got else None

    def save_timetable(self, from_code: str, to_code: str, weekday: int, rows: list) -> None:
        """Tier 2: validated first; a genuine "no trains" is Tier 1's business, not saved here."""
        if self.enabled and rows:
            RS.save_timetable(self.source, from_code, to_code, weekday, rows)


class NotAnswered(list):
    """An empty result that is NOT "no trains": the source answered with an error. Never becomes a Tier 1 negative."""


def tier1_on(src) -> bool:
    """Is route knowledge kept for this provider? Only when it stores data at all (allow-listed, storage on)."""
    return bool(getattr(getattr(src, "cache", None), "enabled", False))


def remember_pair(src, frm: str, to: str, day, rows: list, options: list[TransportData]) -> None:
    """Tier 1: this pair returned trains, or genuinely none. Unreadable rows or an error answer record nothing."""
    source = src.name
    for t in options:
        t.meta["rail_source"] = source  # which provider, for traveller reports (ADR 0003)
    if not tier1_on(src):
        return
    if options:
        RS.record_pair_result(source, frm, to, day.weekday(), "trains",
                              [str(t.meta.get("number")) for t in options if t.meta.get("number")])
    elif not rows and not isinstance(rows, NotAnswered):
        RS.record_pair_result(source, frm, to, day.weekday(), "empty")


def tag_rows(rows: list, source: SourceKind, observed_at: datetime | None = None) -> list:
    """Copies of timetable rows, each marked with where it came from (ADR 0002). The marks ride along to
    stamp_options(); saved copies in the database are never touched."""
    at = (observed_at or datetime.now(UTC)).isoformat()
    return [{**r, "_src": source.value, "_obs": at} for r in rows if isinstance(r, dict)]


def stamp_options(options: list[TransportData], rows: list) -> list[TransportData]:
    """Give every option built from `rows` (one timetable lookup) the rows' source and observation time."""
    first = next((r for r in rows if isinstance(r, dict) and r.get("_src")), None)
    src = SourceKind(first["_src"]) if first else SourceKind.LIVE_API
    obs = datetime.fromisoformat(first["_obs"]) if first and first.get("_obs") else None
    for t in options:
        stamp(t, src, obs)
    return options


def saved_or_raise(cache: RailCache, frm: str, to: str, weekday: int, exc: Exception, log_prefix: str):
    """The live timetable call failed: fall back to any saved copy of this route, or re-raise if there is none.
    Returns (rows, saved_at)."""
    old = cache.timetable_any_age(frm, to, weekday)
    if old is None:
        raise exc
    log.warning("%s: live timetable unavailable (%s); using the copy saved %s for %s -> %s",
                log_prefix, exc, old[1].date(), frm, to)
    return old


def mark_saved_timetable(options: list[TransportData], saved_at: datetime | None) -> None:
    """Options built from an older saved timetable keep their real train names and times, but say how old the
    timetable is and stop claiming the schedule was verified just now."""
    if saved_at is None:
        return
    for t in options:
        t.meta["schedule_state"] = "estimated"
        t.meta["saved_timetable"] = saved_at.date().isoformat()
        t.source = f"{t.source}; live timetable unavailable, using the copy saved {saved_at:%d %b %Y}"


def station_pairs(o_codes: list[tuple[str, str]], d_codes: list[tuple[str, str]],
                  cap: int = 5) -> list[tuple[tuple[str, str], tuple[str, str]]]:
    """Which (origin station, destination station) pairs to try, in order, stopping at the first that has trains.

    The places' own stations first; then a bigger station near the start; then one near the destination; and so
    on outwards. `cap` bounds the API calls one search can cost (each pair is one call, saved afterwards).
    """
    # by total distance from the two "own" stations; on a tie, move the start first (the bigger station near the
    # traveller) before moving the destination end
    order = sorted((i + j, -i, i, j) for i in range(min(3, len(o_codes))) for j in range(min(3, len(d_codes))))
    return [(o_codes[i], d_codes[j]) for _, _, i, j in order][:cap]


ENOUGH_TRAINS = 10  # distinct trains found, over all station pairs, after which asking about more isn't worth the calls
MAX_PAIRS_WITH_TRAINS = 3  # how many station pairs with trains one search may combine


def train_id(t: TransportData) -> str:
    return str(t.meta.get("number") or t.name)


def search_station_pairs(src, origin: PlaceData, dest: PlaceData, depart_day, return_day, o_codes, d_codes,
                         log_prefix: str, max_rows: int) -> tuple[list[TransportData], list[str]]:
    """Trains for a journey, over as many station pairs as it takes to have a real choice.

    The first pair with trains used to be the only one asked about, so a place with a thin main-station timetable
    (Jaipur to New Delhi: 3 trains) never showed the trains that end at its neighbour terminal (Delhi Sarai
    Rohilla: 9, including the Double Decker). Now the search carries on to the next pair while the trains found are
    few and either end is a multi-terminal city (see rail_stations.STATION_GROUPS); for an ordinary pair it still
    stops at the first one with trains. `src` is a rail provider: `.places`, `.trains()` and `._options()`.

    Returns (options, descriptions of the pairs tried). A pair whose live lookup fails (and has no saved copy)
    doesn't end the search; the error is raised only if nothing at all was found.
    """
    out: list[TransportData] = []
    tried: list[str] = []
    down: ProviderUnavailable | None = None
    seen: set[str] = set()
    with_trains = 0
    used_o: set[str] = set()
    used_d: set[str] = set()

    def same_place(code: str, used: set[str]) -> bool:
        """The station already in use, or another terminal of the same city (never a far-away railhead)."""
        return code in used or any(GROUP_OF.get(code) and GROUP_OF.get(code) == GROUP_OF.get(u) for u in used)

    # Tier 1 (ADR 0003): the station pairs that worked for this city pair before go first; pairs that genuinely
    # returned nothing on this weekday are skipped. Neither ever supplies a train fact.
    source, okey, dkey = src.name, RS.city_key(origin), RS.city_key(dest)
    t1 = tier1_on(src)  # route knowledge follows the provider's storage switch (and the I7 allow-list)
    names = {c: n for c, n in [*o_codes, *d_codes]}
    known = [((a, names.get(a) or (STATIONS[a][0] if a in STATIONS else a)),
              (b, names.get(b) or (STATIONS[b][0] if b in STATIONS else b)))
             for a, b in (RS.working_pairs(source, okey, dkey) if t1 else [])]
    ordered = known + [p for p in station_pairs(o_codes, d_codes) if (p[0][0], p[1][0]) not in {(a[0], b[0]) for a, b in known}]
    worked: list[tuple[str, str]] = []
    for n_tried, ((oc, on), (dc, dn)) in enumerate(ordered):
        if with_trains and not (same_place(oc, used_o) and same_place(dc, used_d)):
            continue  # once a pair has trains, only its city's other terminals are worth asking about
        if t1 and RS.known_empty(source, oc, dc, depart_day.weekday()):
            record_pair(source, oc, dc, "skipped_known_empty")
            RS.bump(source, tier1_skipped_empty=1)
            continue
        o_pt = resolve_station_point(src.places, oc, on, origin, log_prefix, max_km=120) or (origin.lat, origin.lng)
        d_pt = resolve_station_point(src.places, dc, dn, dest, log_prefix, max_km=120) or (dest.lat, dest.lng)
        o_label, d_label = f"{on} ({oc})", f"{dn} ({dc})"
        tried.append(f"{on} ({oc}) to {dn} ({dc})")
        try:
            rows = src.trains(oc, dc, depart_day)
        except ProviderUnavailable as exc:
            down = exc
            record_pair(src.name, oc, dc, _failure(src))  # a failure is never recorded as "no trains" (Tier 1)
            continue
        outbound = stamp_options(src._options(rows[:max_rows], "outbound", depart_day, origin, o_pt, d_pt, o_label, d_label), rows)
        remember_pair(src, oc, dc, depart_day, rows, outbound)
        record_pair(src.name, oc, dc, "trains" if outbound else ("error" if isinstance(rows, NotAnswered) else "empty"),
                    len({train_id(t) for t in outbound}))
        if not outbound:
            continue
        if n_tried < len(known):
            RS.bump(source, tier1_working_pair_hit=1)
        worked.append((oc, dc))
        back: list[TransportData] = []
        if return_day >= depart_day:
            try:
                back_rows = src.trains(dc, oc, return_day)
                back = stamp_options(src._options(back_rows[:max_rows], "return", return_day, origin, d_pt, o_pt,
                                                  d_label, o_label), back_rows)
                remember_pair(src, dc, oc, return_day, back_rows, back)
            except ProviderUnavailable:
                back = []  # the way out is known; the planner prices another way home if needed
        notes = transfer_notes(origin, dest, o_pt, d_pt, o_label, d_label)
        for t in [*outbound, *back]:
            t.meta["transfer_notes"] = notes
        out += [*outbound, *back]
        with_trains += 1
        used_o.add(oc)
        used_d.add(dc)
        seen |= {train_id(t) for t in outbound}
        multi = any(c in GROUP_OF for c in (*used_o, *used_d))
        if not multi or len(seen) >= ENOUGH_TRAINS or with_trains >= MAX_PAIRS_WITH_TRAINS:
            break
    if t1:
        RS.record_working_pairs(source, okey, dkey, worked)
    if not out and not with_trains:
        # No direct train between any of the stations: one change at a big junction on the way (Jaipur -> Mathura
        # -> Haridwar), as a traveller would book it.
        try:
            out, via = connecting_trains(src, origin, dest, depart_day, return_day, o_codes, d_codes, log_prefix,
                                         max_rows)
            tried += via
        except ProviderUnavailable as exc:
            down = down or exc
    if not out and down is not None:
        raise down
    return out, tried


# --- One change of train, when nothing runs direct -------------------------------------------------------------
CHANGE_MIN, CHANGE_MAX = 40, 360  # minutes at the junction: enough to change platforms, not a day spent waiting
MIN_CHANGE_KM = 150  # straight-line distance under which a change of train isn't worth suggesting
MIN_LEG_KM = 50  # each of the two trains should be a real journey, not a hop across the junction's suburbs
MAX_DETOUR = 1.35  # the junction may add at most this much to the straight-line distance
MAX_JUNCTIONS = 2  # junctions asked about per search (each costs up to four timetable calls, all saved)
MAX_CONNECTIONS = 8  # per direction, best first


def junctions(o_pt: tuple[float, float], d_pt: tuple[float, float], exclude: set[str],
              limit: int = MAX_JUNCTIONS) -> list[tuple[str, str]]:
    """Major junctions roughly on the way from one station to another, least detour first: [(code, name)]. One per
    city (Delhi's terminals count as one)."""
    direct = haversine_m(o_pt[0], o_pt[1], d_pt[0], d_pt[1])
    if direct < MIN_CHANGE_KM * 1000:
        return []
    ranked = []
    for code, (name, lat, lng, tier) in STATIONS.items():
        if tier < 3 or code in exclude:
            continue
        a = haversine_m(o_pt[0], o_pt[1], lat, lng)
        b = haversine_m(lat, lng, d_pt[0], d_pt[1])
        if min(a, b) < MIN_LEG_KM * 1000 or (a + b) / direct > MAX_DETOUR:
            continue
        ranked.append(((a + b) / direct, code, name))
    out: list[tuple[str, str]] = []
    cities: set[str] = set()
    for _, code, name in sorted(ranked):
        city = GROUP_OF.get(code, code)
        if city in cities:
            continue
        cities.add(city)
        main = STATION_GROUPS[city][0] if city in STATION_GROUPS else code
        if main not in exclude:  # a city's main terminal has the most trains: Old Delhi is nearer, New Delhi busier
            code, name = main, STATIONS[main][0]
        out.append((code, name))
        if len(out) == limit:
            break
    return out


def _leg(t: TransportData) -> dict:
    return {"name": t.name, "number": t.meta.get("number"), "service_class": t.service_class, "from_name": t.from_name,
            "to_name": t.to_name, "depart_at": t.depart_at.isoformat(), "arrive_at": t.arrive_at.isoformat(),
            "from": [t.from_lat, t.from_lng], "to": [t.to_lat, t.to_lng], "duration_minutes": t.duration_minutes,
            "cost_per_person": float(t.cost_per_person)}


def chain(first: TransportData, second: TransportData) -> TransportData:
    """Two trains with a change between them, as one journey: the times, fare and distance of both, the first/last-mile
    ride of the end that touches home, and both trains listed in meta["legs"] so the plan shows each one."""
    home = first if first.direction == "outbound" else second
    same = first.service_class == second.service_class
    # the journey is only as trustworthy as its weaker leg: saved beats nothing, and the older observation counts
    weaker = max((first, second), key=lambda t: (t.meta.get("source_kind") == SourceKind.SAVED_TIMETABLE.value,
                                                -(datetime.fromisoformat(t.meta["observed_at"]).timestamp()
                                                  if t.meta.get("observed_at") else 0)))
    joined = TransportData(
        direction=first.direction, mode="train", name=f"{first.name} + {second.name}",
        service_class=first.service_class if same else f"{first.service_class} + {second.service_class}",
        depart_at=first.depart_at, arrive_at=second.arrive_at,
        duration_minutes=int((second.arrive_at - first.depart_at).total_seconds() // 60),
        cost_per_person=first.cost_per_person + second.cost_per_person,
        distance_m=(first.distance_m or 0) + (second.distance_m or 0), from_name=first.from_name, to_name=second.to_name,
        from_lat=first.from_lat, from_lng=first.from_lng, to_lat=second.to_lat, to_lng=second.to_lng,
        comfort=min(first.comfort, second.comfort), verification_state="estimated", source=first.source,
        access_minutes=home.access_minutes, access_cost=home.access_cost, access_distance_m=home.access_distance_m,
        meta={"schedule_state": first.meta.get("schedule_state", "estimated"), "operator": "Indian Railways",
              "name_source": "timetable", "changes": 1, "change_at": first.to_name, "rail_source": first.meta.get("rail_source"),
              "change_minutes": int((second.depart_at - first.arrive_at).total_seconds() // 60),
              "legs": [_leg(first), _leg(second)]})
    src = weaker.meta.get("source_kind")
    return stamp(joined, SourceKind(src) if src else SourceKind.LIVE_API,
                 datetime.fromisoformat(weaker.meta["observed_at"]) if weaker.meta.get("observed_at") else None)


def connect(firsts: list[TransportData], seconds: list[TransportData]) -> list[TransportData]:
    """Every pairing of a first and a second train with a sensible wait between them, in matching classes where both
    trains have one (else each train's cheapest), quickest first."""
    by_train: dict[tuple, list[TransportData]] = {}
    for t in seconds:
        by_train.setdefault((train_id(t), t.depart_at), []).append(t)
    out: list[TransportData] = []
    firsts_by_train: dict[tuple, list[TransportData]] = {}
    for t in firsts:
        firsts_by_train.setdefault((train_id(t), t.depart_at), []).append(t)
    for f_opts in firsts_by_train.values():
        arrive = f_opts[0].arrive_at
        for s_opts in by_train.values():
            wait = (s_opts[0].depart_at - arrive).total_seconds() / 60
            if not CHANGE_MIN <= wait <= CHANGE_MAX:
                continue
            classes = [c for c in dict.fromkeys(t.service_class for t in f_opts) if c in {s.service_class for s in s_opts}]
            if classes:
                for cls in classes:
                    out.append(chain(next(t for t in f_opts if t.service_class == cls),
                                     next(t for t in s_opts if t.service_class == cls)))
            else:
                out.append(chain(min(f_opts, key=lambda t: t.cost_per_person),
                                 min(s_opts, key=lambda t: t.cost_per_person)))
    # quickest overall, and never more than a handful of pairings of the same two trains
    out.sort(key=lambda t: (t.duration_minutes, t.cost_per_person))
    kept, per_pair = [], {}
    for t in out:
        pair = tuple(leg["number"] or leg["name"] for leg in t.meta["legs"])
        if per_pair.get(pair, 0) < 2:
            per_pair[pair] = per_pair.get(pair, 0) + 1
            kept.append(t)
    return kept[:MAX_CONNECTIONS * 2]


def connecting_trains(src, origin: PlaceData, dest: PlaceData, depart_day, return_day, o_codes, d_codes,
                      log_prefix: str, max_rows: int) -> tuple[list[TransportData], list[str]]:
    """Trains with one change at a junction on the way, for when no direct train runs between any of the stations.
    Uses each place's own (first) station, tries up to MAX_JUNCTIONS junctions and keeps the first that connects.
    Returns (options, descriptions of what was tried)."""
    (oc, on), (dc, dn) = o_codes[0], d_codes[0]
    o_pt = resolve_station_point(src.places, oc, on, origin, log_prefix, max_km=120) or (origin.lat, origin.lng)
    d_pt = resolve_station_point(src.places, dc, dn, dest, log_prefix, max_km=120) or (dest.lat, dest.lng)
    o_label, d_label = f"{on} ({oc})", f"{dn} ({dc})"
    tried: list[str] = []
    exclude = {c for c, _ in [*o_codes, *d_codes]}

    def legs(frm, to, day, direction, a_pt, b_pt, a_label, b_label) -> list[TransportData]:
        try:
            leg_rows = src.trains(frm, to, day)
            got = stamp_options(src._options(leg_rows[:max_rows], direction, day, origin, a_pt, b_pt, a_label, b_label), leg_rows)
            remember_pair(src, frm, to, day, leg_rows, got)
        except ProviderUnavailable:
            record_pair(src.name, frm, to, _failure(src), kind="connection")
            raise
        record_pair(src.name, frm, to, "trains" if got else "empty", len({train_id(t) for t in got}), kind="connection")
        return got

    def onward(firsts, hc, to, direction, h_pt, b_pt, h_label, b_label) -> list[TransportData]:
        """The second trains, on whichever days the first ones reach the junction (an overnight first train
        connects the next morning)."""
        seconds: list[TransportData] = []
        for day in sorted({t.arrive_at.date() for t in firsts})[:2]:
            seconds += legs(hc, to, day, direction, h_pt, b_pt, h_label, b_label)
        return connect(firsts, seconds)
    for hc, hn in junctions(o_pt, d_pt, exclude):
        h_pt = station_position(hc)
        h_label = f"{hn} ({hc})"
        tried.append(f"{on} ({oc}) to {dn} ({dc}) with a change at {h_label}")
        firsts = legs(oc, hc, depart_day, "outbound", o_pt, h_pt, o_label, h_label)
        outbound = onward(firsts, hc, dc, "outbound", h_pt, d_pt, h_label, d_label) if firsts else []
        if not outbound:
            continue
        back: list[TransportData] = []
        if return_day >= depart_day:
            try:
                r_firsts = legs(dc, hc, return_day, "return", d_pt, h_pt, d_label, h_label)
                back = onward(r_firsts, hc, oc, "return", h_pt, o_pt, h_label, o_label) if r_firsts else []
            except ProviderUnavailable:
                back = []  # the way out is known; the planner prices another way home if needed
        notes = transfer_notes(origin, dest, o_pt, d_pt, o_label, d_label)
        for t in [*outbound, *back]:
            t.meta["transfer_notes"] = notes
        log.info("%s: no direct train %s -> %s; %d connection(s) via %s", log_prefix, oc, dc, len(outbound) + len(back),
                 h_label)
        return [*outbound, *back], tried
    return [], tried


def _failure(src) -> str:
    """How a station-pair lookup failed, for the Phase 0 measurements."""
    return "quota" if getattr(src, "quota_used_up", False) else "error"


def _short(place: PlaceData) -> str:
    return place.name.split(",")[0].strip()


def transfer_notes(origin: PlaceData, dest: PlaceData, o_pt: tuple[float, float], d_pt: tuple[float, float],
                   o_name: str, d_name: str, min_km: float = 8) -> list[str]:
    """Plain-language notes when the train doesn't start or end in the place itself, so the traveller knows
    the plan includes a road transfer at that end (taxi or bus)."""
    notes = []
    o_km = estimate_road(origin.lat, origin.lng, o_pt[0], o_pt[1])[0] / 1000
    d_km = estimate_road(dest.lat, dest.lng, d_pt[0], d_pt[1])[0] / 1000
    if o_km > min_km:
        notes.append(f"Your train leaves from {o_name}, about {o_km:.0f} km from {_short(origin)}, so the plan "
                     f"starts with a ride to that station.")
    if d_km > min_km:
        notes.append(f"The train stops at {d_name}, about {d_km:.0f} km from {_short(dest)}; the plan continues "
                     f"by road from there (taxi or bus).")
    return notes


# --- Offline estimate, for when no timetable source answers at all -------------------------------------------
ESTIMATE_SOURCE = ("Estimated from the Indian Railways network (no live timetable was available); "
                   "times and fare are approximate, so check the exact train before booking")
RAIL_DETOUR = 1.3  # track distance vs. straight line, typical for Indian main lines
RAIL_AVG_KMH = 55  # express trains including halts
MIN_RAIL_KM, MAX_RAIL_KM = 60, 2200


def estimated_rail_options(origin: PlaceData, dest: PlaceData, depart_day, return_day,
                           window_start: datetime, window_end: datetime, stations=None,
                           source: SourceKind = SourceKind.STATION_GUIDANCE) -> list[TransportData]:
    """A train journey built only from the station network: which stations to use at each end (the best-connected
    one within reach, e.g. New Delhi for Gurugram and Haridwar for Rishikesh), the road ride to and from them, and
    an approximate duration and fare for the rail leg.

    Never a named or numbered train — this only says "a train between these two stations is realistic, about
    this long and this much", shown with "timing estimated" so the traveller checks the exact one. Used when every
    timetable source is down or not configured, so a plan never silently loses its train.
    """
    if stations:  # given (validated against the gazetteer) by the AI guidance link of the source chain
        (oc, on), (dc, dn) = stations
    else:
        o = nearest_stations(origin.lat, origin.lng, limit=1)
        d = nearest_stations(dest.lat, dest.lng, limit=1)
        if not o or not d:
            return []
        (oc, on, _), (dc, dn, _) = o[0], d[0]
    if oc == dc:
        return []
    o_pt, d_pt = station_position(oc), station_position(dc)
    if not o_pt or not d_pt:
        return []
    km = haversine_m(o_pt[0], o_pt[1], d_pt[0], d_pt[1]) / 1000 * RAIL_DETOUR
    if not MIN_RAIL_KM <= km <= MAX_RAIL_KM:
        return []  # too short to be worth a train, or too far to assume a sensible direct one
    minutes = int(km / RAIL_AVG_KMH * 60) + 20
    dur = timedelta(minutes=minutes)
    classes = ["2S", "CC"] if dur <= timedelta(hours=8) else ["SL", "3A"]
    acc_m, acc_s = estimate_road(origin.lat, origin.lng, o_pt[0], o_pt[1])
    acc_min, acc_cost = max(5, round(acc_s / 60) + 5), F.local_leg_cost(acc_m, "cab", 1)
    o_label, d_label = f"{on} ({oc})", f"{dn} ({dc})"
    notes = transfer_notes(origin, dest, o_pt, d_pt, o_label, d_label)
    out: list[TransportData] = []
    for direction in ("outbound", "return"):
        if direction == "return" and return_day is None:
            continue
        if direction == "outbound":
            dep = window_start + timedelta(minutes=acc_min + 15)  # ride to the station, then 15 min to board
            frm, to, fpt, tpt = o_label, d_label, o_pt, d_pt
        else:
            dep = window_end - timedelta(minutes=acc_min) - dur  # home by the end of the window
            frm, to, fpt, tpt = d_label, o_label, d_pt, o_pt
        for cls in classes:
            out.append(TransportData(
                direction=direction, mode="train", name="", service_class=cls, depart_at=dep, arrive_at=dep + dur,
                duration_minutes=minutes, cost_per_person=F.train_fare(km, cls), distance_m=int(km * 1000),
                from_name=frm, to_name=to, from_lat=fpt[0], from_lng=fpt[1], to_lat=tpt[0], to_lng=tpt[1],
                comfort=F.TRAIN_CLASSES[cls][2], flexible=True, verification_state="estimated",
                source=ESTIMATE_SOURCE, access_minutes=acc_min, access_cost=acc_cost, access_distance_m=acc_m,
                meta={"schedule_state": "estimated", "estimated_rail": True, "operator": "Indian Railways",
                      "transfer_notes": notes}))
    return [stamp(t, source) for t in out]  # stations and distances only; no timetable