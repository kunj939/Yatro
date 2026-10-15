"""Real flights from Google Flights, through SerpApi's `google_flights` engine (ADR 0020).

Google has no public flights API, so Google Flights is read through SerpApi: for each direction, one search between
the airports that serve the two places (up to two each: Ranchi -> London asks IXR to LHR,LGW), economy, one adult.
Each result becomes one journey with its real flights: airline and flight number, the airports, the times, each
layover ("change at Delhi, 3 h 10 min") and Google's price. Prices are per person (one adult searched).

Times are each airport's own local clock, as on the ticket: the flight leaves Ranchi at 06:10 Ranchi time and lands
in London at 18:25 London time. That is also how the plan reads its times: the start ("leave at 07:00") is the
traveller's clock at home, and the days at the destination run on the destination's clock. The real elapsed time is
`duration_minutes` (and `meta.duration_minutes`), which the two clocks' difference doesn't show.

Nothing here is required: without a key, or on any failure, the caller keeps its estimated flight.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.core.config import get_settings
from app.providers import http
from app.providers import ttl as ttls
from app.providers.airports import AIRPORTS, airport_label, nearest_airports
from app.providers.cache import cached, peek
from app.providers.types import PlaceData, TransportData
from app.services import fare_model as F
from app.utils.geo import estimate_road, haversine_m
from app.utils.locale import country_for
from app.utils.money import to_currency

log = logging.getLogger("tripai.flights")

SERPAPI = "https://serpapi.com/search.json"
MAX_RESULTS = 8  # per direction: enough real choices for the planner and the "Swap" list
MAX_STOPS = 2
CHECK_IN = {False: 120, True: 180}  # minutes at the airport before departure: domestic, international
EXIT = {False: 30, True: 60}  # minutes from landing to the kerb: domestic, international (immigration, bags)


def enabled() -> bool:
    return bool(get_settings().serpapi_api_key)


REUSE_WEEKS = 4  # a search answers the same weekday up to this many weeks either side (one timetable season)


def _search(frm: list[str], to: list[str], day: date, currency: str, market: str) -> dict:
    """One search per route and date, kept a week (`serpapi-flights`) and shared by every trip; a date with no search
    of its own reuses the nearest search on the same weekday up to four weeks away, so Ranchi -> London searched for
    Saturday 7 Nov also answers Saturday 14 Nov. Returns {"day": the date searched, "seen": when, "data": Google
    Flights' answer}; the caller moves the times to its own date when they differ."""
    s = get_settings()
    route = {"engine": "google_flights", "departure_id": ",".join(frm), "arrival_id": ",".join(to), "type": "2",
             "travel_class": "1", "adults": "1", "currency": currency, "hl": "en", "gl": market}

    def fetch():
        r = http.request("GET", SERPAPI, provider="SerpApi Google Flights",
                         params={**route, "outbound_date": day.isoformat(), "api_key": s.serpapi_api_key})
        return {"day": day.isoformat(), "seen": date.today().isoformat(), "data": r.json()}

    for weeks in range(1, REUSE_WEEKS + 1):  # the nearest weeks first, earlier before later
        for near in (day - timedelta(weeks=weeks), day + timedelta(weeks=weeks)):
            hit = peek("serpapi-flights", [route, near.isoformat()])
            if hit:
                return hit
    return cached("serpapi-flights", [route, day.isoformat()], ttls.get("serpapi-flights"), fetch)  # never the API key


def _local(value: str, shift_days: int = 0) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M") + timedelta(days=shift_days)
    except (TypeError, ValueError):
        return None


def _airport(code: str, raw_name: str | None) -> tuple[str, float | None, float | None]:
    a = AIRPORTS.get(code)
    if a:
        return airport_label(code), a[1], a[2]
    return (f"{raw_name} ({code})" if raw_name else code), None, None


def journey(group: dict, direction: str, home: PlaceData, currency: str, shift_days: int = 0,
            searched: str | None = None, seen: str | None = None) -> TransportData | None:
    """One Google Flights result as one journey, in each airport's local time; None when it can't be trusted whole
    (no price, no times, more than two stops). `shift_days`: the result was searched for another date (the same
    weekday, `searched`), so its times are moved to this one and the price is only an estimate."""
    segs = group.get("flights") or []
    price = group.get("price")
    if not segs or price is None or len(segs) - 1 > MAX_STOPS:
        return None
    first, last = segs[0], segs[-1]
    dep_code, arr_code = first["departure_airport"].get("id", ""), last["arrival_airport"].get("id", "")
    dep_local = _local(first["departure_airport"].get("time"), shift_days)
    arr_local = _local(last["arrival_airport"].get("time"), shift_days)
    if not dep_local or not arr_local:
        return None
    depart_at, arrive_at = dep_local, arr_local  # each airport's own clock, as on the ticket
    layovers = group.get("layovers") or []
    legs = []
    for s in segs:
        minutes = int(s.get("duration") or 0)
        frm_name, _, _ = _airport(s["departure_airport"].get("id", ""), s["departure_airport"].get("name"))
        to_name, _, _ = _airport(s["arrival_airport"].get("id", ""), s["arrival_airport"].get("name"))
        d, a = _local(s["departure_airport"].get("time"), shift_days), _local(s["arrival_airport"].get("time"), shift_days)
        if not d or not a:
            return None
        legs.append({"name": s.get("airline") or "Flight", "number": s.get("flight_number"),
                     "service_class": s.get("travel_class") or "Economy", "from_name": frm_name, "to_name": to_name,
                     "depart_at": d.isoformat(), "arrive_at": a.isoformat(), "duration_minutes": minutes,
                     "depart_local": f"{d:%H:%M}", "arrive_local": f"{a:%H:%M}", "airplane": s.get("airplane"),
                     "overnight": bool(s.get("overnight"))})
    total = int(group.get("total_duration") or sum(lg["duration_minutes"] for lg in legs)
                + sum(int(x.get("duration") or 0) for x in layovers))

    frm, flat, flng = _airport(dep_code, first["departure_airport"].get("name"))
    to, tlat, tlng = _airport(arr_code, last["arrival_airport"].get("name"))
    intl = bool(flat is not None and tlat is not None and country_for(flat, flng) != country_for(tlat, tlng))
    # the road ride at the home end: to the airport before an outbound flight, from it after the flight back
    end = (flat, flng) if direction == "outbound" else (tlat, tlng)
    acc_m, acc_s = estimate_road(home.lat, home.lng, *end) if end[0] is not None else (0, 0)
    acc_min = max(10, round(acc_s / 60) + 10) if acc_m else 0
    stop = layovers[0] if layovers else None
    airlines = list(dict.fromkeys(s.get("airline") for s in segs if s.get("airline")))
    t = TransportData(
        direction=direction, mode="flight", name=airlines[0] if len(segs) == 1 and airlines else " + ".join(airlines),
        depart_at=depart_at, arrive_at=arrive_at, duration_minutes=total,
        cost_per_person=Decimal(str(price)), service_class=first.get("travel_class") or "Economy",
        distance_m=int(haversine_m(flat, flng, tlat, tlng)) if flat is not None and tlat is not None else None,
        from_name=frm, to_name=to, from_lat=flat, from_lng=flng, to_lat=tlat, to_lng=tlng, comfort=5,
        verification_state="estimated" if shift_days else "verified_provider",
        source=("Google Flights (via SerpApi): " + (f"schedule and price for {searched}, on the same weekday" if shift_days
                                                    else f"schedule and price as seen on {seen or 'the day of planning'}")),
        access_minutes=acc_min, access_cost=to_currency(F.local_leg_cost(acc_m, "cab", 1), currency) if acc_m else Decimal(0),
        access_distance_m=acc_m,
        meta={"schedule_state": "live", "number": first.get("flight_number") if len(segs) == 1 else None,
              "operator": ", ".join(airlines), "changes": len(segs) - 1,
              "change_at": f"{stop.get('name')} ({stop.get('id')})" if stop else None,
              "change_minutes": int(stop.get("duration") or 0) if stop else None,
              "legs": legs if len(segs) > 1 else None, "check_in_minutes": CHECK_IN[intl], "exit_minutes": EXIT[intl],
              "depart_local": f"{depart_at:%H:%M}", "arrive_local": f"{arrive_at:%H:%M}", "duration_minutes": total,
              "fare_label": (f"Google Flights price for {_nice(searched)}" if shift_days
                             else f"Google Flights price, seen {_nice(seen)}" if seen else "Google Flights price"),
              "schedule_from": searched if shift_days else None, "international": intl,
              "overnight": any(lg["overnight"] for lg in legs) or arrive_at.date() > depart_at.date()})
    if (arrive_at - depart_at).total_seconds() <= 0 and not intl:
        return None  # a domestic flight that lands before it leaves: the data is wrong
    return t


def _nice(iso: str | None) -> str:
    try:
        return date.fromisoformat(iso).strftime("%d %b").lstrip("0") if iso else ""
    except ValueError:
        return iso or ""


def flights(origin: PlaceData, dest: PlaceData, depart_day: date, return_day: date,
            currency: str) -> list[TransportData] | None:
    """Real flights both ways, or None when there's no key, no airport we know at an end, or Google Flights can't
    be read (the caller then keeps its estimated flight). A direction with no usable result is simply missing."""
    if not enabled():
        return None
    o_air, d_air = nearest_airports(origin.lat, origin.lng, 2), nearest_airports(dest.lat, dest.lng, 2)
    if not o_air or not d_air:
        return None
    oc, dc = [a[0] for a in o_air], [a[0] for a in d_air]
    if set(oc) & set(dc):
        return None  # one airport serves both places: nobody flies
    market = (country_for(origin.lat, origin.lng) or "in").lower()
    out: list[TransportData] = []
    try:
        for direction, frm, to, day in (("outbound", oc, dc, depart_day), ("return", dc, oc, return_day)):
            found = _search(frm, to, day, currency, market)
            data, searched = found.get("data") or {}, found.get("day") or day.isoformat()
            shift = (day - date.fromisoformat(searched)).days  # 0, or whole weeks: the same weekday
            groups = [*(data.get("best_flights") or []), *(data.get("other_flights") or [])]
            for g in groups[:MAX_RESULTS * 2]:
                t = journey(g, direction, origin, currency, shift, searched, found.get("seen"))
                if t:
                    out.append(t)
                if sum(1 for x in out if x.direction == direction) >= MAX_RESULTS:
                    break
    except Exception as exc:  # noqa: BLE001 - an optional source: the estimate stands
        log.warning("Google Flights unavailable for %s -> %s: %s", origin.name, dest.name, exc)
        return None
    return out or None
