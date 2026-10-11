"""Indian Railways timetables from the third-party "IRCTC1" API on RapidAPI.

One of two Indian Railways timetable sources TripAI can use (see rail_chain.py, which tries this alongside
railradar.py and Google has none of its own). Everything shown is what the timetable says: train name,
number, departure and arrival. Fares are estimated from distance and class (marked "Estimated"). If the API
is unavailable or the quota is used up, no train is offered from here (never invented) — the chain moves on
to the next configured source.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

from app.core.provenance import SourceKind
from app.core.errors import ProviderUnavailable
from app.gateway.core import request as gateway_request
from app.core import brownout
from app.providers import ttl as ttls
from app.providers import http
from app.providers.cache import cached
from app.providers.rail_storage import storage_allowed
from app.providers.rail_common import (
    NotAnswered,
    tag_rows,
    RailCache,
    find_stations,
    first,
    hhmm,
    mark_saved_timetable,
    minutes,
    pick_classes,
    saved_or_raise,
    search_station_pairs,
)
from app.providers.types import PlaceData, TransportData
from app.services import fare_model as F
from app.utils.geo import estimate_road

log = logging.getLogger("tripai.indian_rail")

SOURCE = "Indian Railways timetable (IRCTC1 via RapidAPI, third-party)"
MAX_TRAINS = 8  # per direction, after sorting by departure


class IndianRailProvider:
    name = "irctc1"

    def __init__(self, places, api_key: str, host: str, store: bool = True):
        self.places = places  # used to place stations on the map (cached)
        self.key, self.host = api_key, host
        self.cache = RailCache(self.name, enabled=store)
        self.last_issue: str | None = None  # why the last search found no trains, shown to the traveller
        self.quota_used_up = False
        self.saved_at = None  # set when a timetable came from an older saved copy because the live call failed

    # --- API calls --------------------------------------------------------------------------------------------
    def _get(self, path: str, params: dict, ttl: timedelta) -> dict:
        if brownout.active(4):  # busy: saved timetables only, no live rail lookups (ADR 0008, brownout L4)
            raise ProviderUnavailable("Live rail lookups are paused while we're busy")
        def fetch():
            # One call through the gateway (ADR 0007). While IRCTC1's quota circuit is open this raises at once, with no
            # network call; at most one probe a day goes out to find out when the monthly quota has reset.
            resp = gateway_request("GET", f"https://{self.host}{path}", provider="IRCTC1", params=params, raw=True,
                                   headers={"x-rapidapi-key": self.key, "x-rapidapi-host": self.host})
            if resp.status_code == 429:  # the gateway only hands a 429 back when it is a quota answer
                self.quota_used_up = True
                log.warning("IRCTC1 quota used up: %s", resp.text[:300])
                raise ProviderUnavailable("IRCTC1 quota used up")
            if resp.status_code >= 400:
                log.warning("IRCTC1 %s -> %s %s", path, resp.status_code, resp.text[:300])
                raise ProviderUnavailable(f"IRCTC1 rejected the request ({resp.status_code})")
            time.sleep(0.4)  # stay under per-second limits between our own calls
            return resp.json()
        return cached("irctc1", [path, params], ttl, fetch) if storage_allowed("irctc1") else fetch()  # I7

    def _station_rows(self, query: str) -> list[dict]:
        saved = self.cache.stations(query)
        if saved is not None:
            return saved
        data = self._get("/api/v1/searchStation", {"query": query}, ttls.get("irctc1-station"))
        found = data.get("data") if isinstance(data, dict) else data
        rows = [r for r in found or [] if isinstance(r, dict)]
        self.cache.save_stations(query, rows)
        return rows

    def station_codes(self, place: PlaceData) -> list[tuple[str, str]]:
        return find_stations(self.places, place, self._station_rows, "IRCTC1")

    def trains(self, frm: str, to: str, day: date) -> list[dict]:
        saved = self.cache.timetable(frm, to, day.weekday())
        if saved is not None:
            saved = tag_rows(saved, SourceKind.SAVED_TIMETABLE, self.cache.last_fetched_at)
            log.info("IRCTC1: using the saved %s timetable %s -> %s (no API call)", day.strftime("%A"), frm, to)
            return saved
        try:
            data = self._get("/api/v3/trainBetweenStations",
                             {"fromStationCode": frm, "toStationCode": to, "dateOfJourney": day.isoformat()},
                             ttls.get("irctc1-between"))
        except ProviderUnavailable as exc:
            rows, self.saved_at = saved_or_raise(self.cache, frm, to, day.weekday(), exc, "IRCTC1")
            return tag_rows(rows, SourceKind.SAVED_TIMETABLE, self.saved_at)
        if isinstance(data, dict) and data.get("status") is False:
            log.warning("IRCTC1 trainBetweenStations %s->%s: %s", frm, to, data.get("message"))
            self.last_issue = f"the train timetable service said: {data.get('message') or 'no result'}"
            return NotAnswered()
        rows = [r for r in (data.get("data") if isinstance(data, dict) else data) or [] if isinstance(r, dict)]
        self.cache.save_timetable(frm, to, day.weekday(), rows)
        return tag_rows(rows, SourceKind.LIVE_API)

    # --- Conversion into TripAI transport options --------------------------------------------------------------
    def _options(self, rows: list[dict], direction: str, day: date, home: PlaceData, frm_pt, to_pt,
                 frm_name: str, to_name: str) -> list[TransportData]:
        out: list[TransportData] = []
        road_m, _ = estimate_road(frm_pt[0], frm_pt[1], to_pt[0], to_pt[1])
        km = max(1.0, road_m / 1000)
        station = frm_pt if direction == "outbound" else to_pt
        acc_m, acc_s = estimate_road(home.lat, home.lng, station[0], station[1])
        acc_min, acc_cost = max(5, round(acc_s / 60) + 5), F.local_leg_cost(acc_m, "cab", 1)
        skipped = 0
        for row in rows:
            name = str(first(row, "train_name", "trainName", "name", default="")).strip().title()
            number = str(first(row, "train_number", "trainNumber", "number", default="")).strip()
            dep = hhmm(first(row, "from_std", "from_sta", "departure_time", "departureTime", "from_time"))
            arr = hhmm(first(row, "to_sta", "to_std", "arrival_time", "arrivalTime", "to_time"))
            if not (name and dep and arr):
                skipped += 1
                continue
            depart_at = datetime.combine(day, datetime.min.time()).replace(hour=dep[0], minute=dep[1])
            dur = minutes(first(row, "duration", "travel_time", "travelTime"))
            if dur:
                arrive_at = depart_at + timedelta(minutes=dur)
            else:
                arrive_at = depart_at.replace(hour=arr[0], minute=arr[1])
                days = int(first(row, "to_day", "d_day", "arrival_day", default=0) or 0)
                arrive_at += timedelta(days=days)
                if arrive_at <= depart_at:
                    arrive_at += timedelta(days=1)
            listed = [str(c).upper() for c in (first(row, "class_type", "classes", "class", default=[]) or [])]
            classes = pick_classes(listed, arrive_at - depart_at)
            if not classes:
                skipped += 1
                continue
            for cls in classes:
                out.append(TransportData(
                    direction=direction, mode="train", name=name, service_class=cls,
                    depart_at=depart_at, arrive_at=arrive_at,
                    duration_minutes=int((arrive_at - depart_at).total_seconds() // 60),
                    cost_per_person=F.train_fare(km, cls), distance_m=int(km * 1000),
                    from_name=frm_name, to_name=to_name, from_lat=frm_pt[0], from_lng=frm_pt[1],
                    to_lat=to_pt[0], to_lng=to_pt[1], comfort=F.TRAIN_CLASSES[cls][2],
                    verification_state="estimated", source=f"{SOURCE}; fare estimated",
                    access_minutes=acc_min, access_cost=acc_cost, access_distance_m=acc_m,
                    meta={"schedule_state": "verified_provider", "number": number or None,
                          "operator": "Indian Railways", "name_source": "timetable"}))
        if rows and not out:
            log.warning("IRCTC1: %d trains returned but none could be read (first item keys: %s)",
                        len(rows), list(rows[0])[:15])
            self.last_issue = "the train timetable came back in a format TripAI couldn't read"
        elif skipped:
            log.info("IRCTC1: skipped %d incomplete rows", skipped)
        return out

    def intercity(self, origin: PlaceData, dest: PlaceData, depart_day: date, return_day: date) -> list[TransportData]:
        self.last_issue, self.quota_used_up, self.saved_at = None, False, None
        try:
            o_codes, d_codes = self.station_codes(origin), self.station_codes(dest)
            if not o_codes or not d_codes:
                missing = origin.name if not o_codes else dest.name
                self.last_issue = f"no railway station was found for {missing}"
                return []
            # Each place's own station first, then its neighbouring terminals and nearby hubs, over as many pairs as it
            # takes to have a real choice (see rail_common.search_station_pairs).
            out, tried = search_station_pairs(self, origin, dest, depart_day, return_day, o_codes, d_codes, "IRCTC1",
                                              MAX_TRAINS * 3)
            if out:
                mark_saved_timetable(out, self.saved_at)
            log.info("IRCTC1: %d train options for %s <-> %s over %d station pair(s)", len(out), origin.name,
                     dest.name, len(tried))
            if not out and not self.last_issue:
                self.last_issue = "no train was found for these dates on " + "; ".join(tried)
            return out
        except ProviderUnavailable as exc:
            log.warning("IRCTC1 unavailable (%s): check IRCTC_RAPIDAPI_KEY, the subscription and the monthly quota",
                        exc)
            self.last_issue = (
                "the free quota of the train timetable API (IRCTC1 on RapidAPI) is used up; it resets "
                "with your plan period, or upgrade the plan" if self.quota_used_up else
                "the train timetable service didn't answer (check the IRCTC key, the RapidAPI "
                "subscription and the monthly quota)")
            return []