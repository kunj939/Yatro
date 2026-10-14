"""Indian Railways timetables from the third-party RailRadar API (railradar.in).

The other of two Indian Railways timetable sources Yaitro can use (see rail_chain.py, which tries this
alongside indian_rail.py's IRCTC1 and Google has none of its own). Train name, number, departure and arrival
are exactly what the timetable says. RailRadar's "trains between stations" search doesn't return fare or seat
class, so those are estimated from the train's category and the route's distance (marked "Estimated") — the
train itself is never invented, only its price and which classes it likely offers.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta

from app.core.provenance import SourceKind
from app.core.errors import ProviderUnavailable
from app.gateway.core import request as gateway_request
from app.core import brownout
from app.providers import ttl as ttls
from app.providers import http
from app.providers.cache import cached
from app.providers.rail_common import (
    NotAnswered,
    tag_rows,
    RailCache,
    find_stations,
    first,
    guess_classes,
    hhmm,
    mark_saved_timetable,
    saved_or_raise,
    search_station_pairs,
)
from app.providers.types import PlaceData, TransportData
from app.services import fare_model as F
from app.utils.geo import estimate_road

log = logging.getLogger("tripai.railradar")

SOURCE = "Indian Railways timetable (RailRadar, third-party)"
BASE = "https://api.railradar.in/v1"
MAX_TRAINS = 8  # per direction, after sorting by departure


class RailRadarProvider:
    name = "railradar"

    def __init__(self, places, api_key: str, store: bool = True,
                station_search: Callable[[str], list[dict]] | None = None):
        self.places = places
        self.key = api_key
        self.cache = RailCache(self.name, enabled=store)
        # Optional: RailRadar itself has no documented public station-search endpoint, so when IRCTC1 is also
        # configured its station search is borrowed for cities outside the built-in KNOWN_STATIONS table.
        self.station_search = station_search
        self.last_issue: str | None = None
        self.quota_used_up = False
        self.saved_at = None  # set when a timetable came from an older saved copy because the live call failed

    # --- API calls --------------------------------------------------------------------------------------------
    def _get(self, path: str, params: dict, ttl: timedelta, use_cache: bool = True) -> dict:
        if brownout.active(4):  # busy: saved timetables only, no live rail lookups (ADR 0008, brownout L4)
            raise ProviderUnavailable("Live rail lookups are paused while we're busy")
        def fetch():
            # One call through the gateway: deadline, timeout, a budgeted retry for rate limits and 5xx, circuit breaker,
            # daily budget (ADR 0007). `raw`: a quota 429 or a 4xx comes back here so the messages below stay RailRadar's own.
            resp = gateway_request("GET", f"{BASE}{path}", provider="RailRadar", params=params, raw=True,
                                   headers={"Authorization": f"Bearer {self.key}"})
            if resp.status_code == 429:
                text = resp.text[:300]
                self.quota_used_up = True
                log.warning("RailRadar quota used up: %s", text)
                raise ProviderUnavailable("RailRadar quota used up")
            if resp.status_code in (401, 403):
                log.warning("RailRadar auth rejected (%s): check RAILRADAR_API_KEY", resp.status_code)
                raise ProviderUnavailable("RailRadar rejected the API key")
            if resp.status_code >= 400:
                log.warning("RailRadar %s -> %s %s", path, resp.status_code, resp.text[:300])
                raise ProviderUnavailable(f"RailRadar rejected the request ({resp.status_code})")
            time.sleep(0.2)
            return resp.json()
        return cached("railradar", [path, params], ttl, fetch) if use_cache else fetch()

    def _station_rows(self, query: str) -> list[dict]:
        """RailRadar has no public station-search endpoint of its own, so this only has something to return
        when a fallback search (IRCTC1's, if configured) was passed in."""
        if not self.station_search:
            return []
        saved = self.cache.stations(query)
        if saved is not None:
            return saved
        rows = self.station_search(query)
        self.cache.save_stations(query, rows)
        return rows

    def station_codes(self, place: PlaceData) -> list[tuple[str, str]]:
        return find_stations(self.places, place, self._station_rows, "RailRadar")

    def trains(self, frm: str, to: str, day: date) -> list[dict]:
        saved = self.cache.timetable(frm, to, day.weekday())
        if saved is not None:
            saved = tag_rows(saved, SourceKind.SAVED_TIMETABLE, self.cache.last_fetched_at)
            log.info("RailRadar: using the saved %s timetable %s -> %s (no API call)", day.strftime("%A"), frm, to)
            return saved
        try:
            data = self._get(f"/trains/between/{frm}/{to}", {"date": day.isoformat()}, ttls.get("railradar"))
        except ProviderUnavailable as exc:
            rows, self.saved_at = saved_or_raise(self.cache, frm, to, day.weekday(), exc, "RailRadar")
            return tag_rows(rows, SourceKind.SAVED_TIMETABLE, self.saved_at)
        if not isinstance(data, dict) or data.get("success") is False:
            msg = (data or {}).get("error") or (data or {}).get("message") or "no result"
            log.warning("RailRadar trains/between %s->%s: %s", frm, to, msg)
            self.last_issue = f"the train timetable service said: {msg}"
            return NotAnswered()
        rows = [r for r in (data.get("data", {}) or {}).get("trains", []) or [] if isinstance(r, dict)]
        self.cache.save_timetable(frm, to, day.weekday(), rows)
        return tag_rows(rows, SourceKind.LIVE_API)

    def fetch_live(self, frm: str, to: str, day: date) -> list[dict]:
        """For the Tier 2 refresh worker (ADR 0003): always the live API, never the saved copy or the response cache."""
        data = self._get(f"/trains/between/{frm}/{to}", {"date": day.isoformat()}, timedelta(0), use_cache=False)
        if not isinstance(data, dict) or data.get("success") is False:
            raise ProviderUnavailable("RailRadar refresh failed")
        return [r for r in (data.get("data", {}) or {}).get("trains", []) or [] if isinstance(r, dict)]

    # --- Conversion into Yaitro transport options --------------------------------------------------------------
    def _options(self, rows: list[dict], direction: str, day: date, home: PlaceData, frm_pt, to_pt,
                 frm_name: str, to_name: str) -> list[TransportData]:
        out: list[TransportData] = []
        road_m, _ = estimate_road(frm_pt[0], frm_pt[1], to_pt[0], to_pt[1])
        fallback_km = max(1.0, road_m / 1000)
        station = frm_pt if direction == "outbound" else to_pt
        acc_m, acc_s = estimate_road(home.lat, home.lng, station[0], station[1])
        acc_min, acc_cost = max(5, round(acc_s / 60) + 5), F.local_leg_cost(acc_m, "cab", 1)
        skipped = 0
        for row in rows:
            train = row.get("train") or {}
            frm_leg, to_leg = row.get("from") or {}, row.get("to") or {}
            name = str(first(train, "name", "trainName", default="")).strip().title()
            number = str(first(train, "number", "trainNumber", default="")).strip()
            dep = hhmm(first(frm_leg, "departure", "std", "time", "scheduledDeparture"))
            if not (name and dep):
                skipped += 1
                continue
            depart_at = datetime.combine(day, datetime.min.time()).replace(hour=dep[0], minute=dep[1])
            dur = row.get("duration")  # minutes; RailRadar gives this directly, sidestepping day-offset guessing
            arr = hhmm(first(to_leg, "arrival", "sta", "time", "scheduledArrival"))
            if isinstance(dur, (int, float)) and dur > 0:
                arrive_at = depart_at + timedelta(minutes=dur)
            elif arr:
                arrive_at = depart_at.replace(hour=arr[0], minute=arr[1])
                if arrive_at <= depart_at:
                    arrive_at += timedelta(days=1)
            else:
                skipped += 1
                continue
            km = row.get("distance")
            km = float(km) if isinstance(km, (int, float)) and km > 0 else fallback_km
            classes = guess_classes(str(first(train, "type", default="")), arrive_at - depart_at, name)
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
                    verification_state="estimated", source=f"{SOURCE}; fare and class estimated",
                    access_minutes=acc_min, access_cost=acc_cost, access_distance_m=acc_m,
                    meta={"schedule_state": "verified_provider", "number": number or None,
                          "operator": "Indian Railways", "name_source": "timetable"}))
        if rows and not out:
            log.warning("RailRadar: %d trains returned but none could be read (first item keys: %s)",
                        len(rows), list(rows[0])[:15])
            self.last_issue = "the train timetable came back in a format Yaitro couldn't read"
        elif skipped:
            log.info("RailRadar: skipped %d incomplete rows", skipped)
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
            out, tried = search_station_pairs(self, origin, dest, depart_day, return_day, o_codes, d_codes, "RailRadar",
                                              MAX_TRAINS * 3)
            if out:
                mark_saved_timetable(out, self.saved_at)
            log.info("RailRadar: %d train options for %s <-> %s over %d station pair(s)", len(out), origin.name,
                     dest.name, len(tried))
            if not out and not self.last_issue:
                self.last_issue = "no train was found for these dates on " + "; ".join(tried)
            return out
        except ProviderUnavailable as exc:
            log.warning("RailRadar unavailable (%s): check RAILRADAR_API_KEY and the monthly quota", exc)
            self.last_issue = (
                "the free quota of the train timetable API (RailRadar) is used up; it resets with your plan "
                "period, or upgrade the plan" if self.quota_used_up else
                "the train timetable service didn't answer (check the RailRadar key and the monthly quota)")
            return []