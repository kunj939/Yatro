"""Offline providers backed by app/demo/data.py. Same interfaces as the live providers."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from app.core.provenance import SourceKind, stamp
from app.core.errors import AppError
from app.demo import data as D
from app.providers.base import (
    HotelProvider,
    MapsProvider,
    PlacesProvider,
    TransportProvider,
    WeatherProvider,
    WebSearchProvider,
)
from app.providers.types import (
    HotelData,
    MatrixResult,
    PlaceData,
    RestaurantData,
    RouteResult,
    SearchResult,
    Suggestion,
    TransportData,
    WeatherData,
)
from app.services import fare_model as F
from app.utils.geo import encode_polyline, estimate_road, haversine_m


def _city(c: dict) -> PlaceData:
    return PlaceData(provider="demo", provider_place_id=c["id"], name=c["name"], category="city", lat=c["lat"],
                     lng=c["lng"], address=f'{c["name"]}, {c["secondary"]}', city=c["name"], meta={"hub": c["hub"]})


def find_city(text: str | None = None, place_id: str | None = None) -> dict | None:
    for c in D.CITIES:
        if place_id and c["id"] == place_id:
            return c
    q = (text or "").strip().lower()
    if not q:
        return None
    for c in D.CITIES:
        if q == c["name"].lower() or any(a in q for a in c["aliases"]) or c["name"].lower() in q:
            return c
    return None


class DemoMapsProvider(MapsProvider):
    name = "demo-maps"

    def compute_route(self, origin, dest, mode="drive", depart_at=None) -> RouteResult:
        dist, dur = estimate_road(*origin, *dest, mode="walk" if mode == "walk" else "drive")
        mid = ((origin[0] + dest[0]) / 2 + (dest[1] - origin[1]) * 0.06, (origin[1] + dest[1]) / 2 - (dest[0] - origin[0]) * 0.06)
        return RouteResult(dist, dur, encode_polyline([origin, mid, dest]), "demo", mode)

    def compute_matrix(self, points, mode="drive") -> MatrixResult:
        cells = {}
        for i, a in enumerate(points):
            for j, b in enumerate(points):
                cells[(i, j)] = (0, 0) if i == j else estimate_road(*a, *b, mode="drive")
        return MatrixResult(cells, "demo")


class DemoPlacesProvider(PlacesProvider):
    name = "demo-places"

    def autocomplete(self, query: str, limit: int = 6) -> list[Suggestion]:
        q = query.strip().lower()
        out = [Suggestion(c["id"], c["name"], c["secondary"], c["lat"], c["lng"]) for c in D.CITIES
               if not q or q in c["name"].lower() or any(q in a for a in c["aliases"])]
        return out[:limit]

    def resolve(self, text: str, place_id: str | None = None) -> PlaceData | None:
        c = find_city(text, place_id)
        return _city(c) if c else None

    def reverse(self, lat: float, lng: float) -> PlaceData | None:
        c = min(D.CITIES, key=lambda c: haversine_m(lat, lng, c["lat"], c["lng"]))
        return _city(c)

    def _ensure_jaipur(self, city: PlaceData) -> None:
        if city.provider_place_id != D.DEMO_DESTINATION_ID:
            raise AppError(f"Demo mode has sample data for Jaipur only. Add GOOGLE_MAPS_SERVER_KEY to plan trips to "
                           f"{city.name}.", code="demo_destination_unsupported", status_code=422)

    def attractions(self, city: PlaceData, interests: list[str]) -> list[PlaceData]:
        self._ensure_jaipur(city)
        return [PlaceData(provider="demo", provider_place_id=f"demo:jaipur:{a['id']}", name=a["name"],
                          category="attraction", lat=a["lat"], lng=a["lng"], address=f"{a['name']}, Jaipur",
                          city="Jaipur", rating=a["rating"], rating_count=a["reviews"], tags=a["tags"],
                          opening_hours={"weekly": a["hours"], "source": "demo"}, website=a.get("website"),
                          business_status="OPERATIONAL", entry_fee=Decimal(a["fee"]), entry_fee_state="demo",
                          recommended_minutes=a["minutes"], description=a["desc"], meta={"demo_key": a["id"]})
                for a in D.ATTRACTIONS]

    def restaurants(self, city: PlaceData, food: str, premium: bool) -> list[PlaceData]:
        self._ensure_jaipur(city)
        out = []
        for r in D.RESTAURANTS:
            out.append(PlaceData(
                provider="demo", provider_place_id=f"demo:jaipur:{r['id']}", name=r["name"], category="restaurant",
                lat=r["lat"], lng=r["lng"], address=f"{r['name']}, Jaipur", city="Jaipur", rating=r["rating"],
                rating_count=r["reviews"], tags=r["tags"], opening_hours={"weekly": r["hours"], "source": "demo"},
                business_status="OPERATIONAL", recommended_minutes=60,
                restaurant=RestaurantData(veg_only=r["veg_only"], serves_veg=True, serves_non_veg=not r["veg_only"],
                                          cuisines=r["cuisines"], meal_types=r["meals"],
                                          avg_cost_per_person=Decimal(r["cost"]), cost_state="demo"),
                meta={"demo_key": r["id"]}))
        return out

    def station(self, city: PlaceData) -> PlaceData:
        name, lat, lng = D.HUBS["jp"]["train"]
        return PlaceData(provider="demo", provider_place_id="demo:jaipur:station", name=name, category="station",
                         lat=lat, lng=lng, city="Jaipur")


class DemoHotelProvider(HotelProvider):
    name = "demo-hotels"

    def hotels(self, city: PlaceData, tier: str) -> list[PlaceData]:
        return [PlaceData(provider="demo", provider_place_id=f"demo:jaipur:{h['id']}", name=h["name"], category="hotel",
                          lat=h["lat"], lng=h["lng"], city="Jaipur", tags=[h["tier"]],
                          hotel=HotelData(tier=h["tier"], stars=h["stars"], nightly_rate=Decimal(h["rate"]),
                                          rate_state="demo"))
                for h in D.HOTELS if h["tier"] == tier]


def _at(day: date, hhmm: str, offset_min: int = 0) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m) + timedelta(minutes=offset_min)


class DemoTransportProvider(TransportProvider):
    name = "demo-transport"

    def intercity_options(self, origin, dest, depart_day, return_day, window_start, window_end, modes,
                          tz: str | None = None, currency: str = "INR"):  # the sample trip is always IST and rupees
        if dest.provider_place_id != D.DEMO_DESTINATION_ID:
            raise AppError("Demo mode has sample transport for trips to Jaipur only.",
                           code="demo_destination_unsupported", status_code=422)
        hub = D.HUBS[origin.meta.get("hub", "ggn")]
        jp = D.HUBS["jp"]
        off = hub["offset"]
        out: list[TransportData] = []

        def access(point) -> tuple[int, int, Decimal]:
            dist, dur = estimate_road(origin.lat, origin.lng, point[1], point[2])
            return dist, round(dur / 60) + 5, F.local_leg_cost(dist, "cab", 1)

        def add(direction, mode, name, cls, day, hhmm, minutes, pp, hub_pt, jp_pt, comfort):
            dep = _at(day, hhmm, -off if direction == "outbound" else 0)
            arr = dep + timedelta(minutes=minutes + (off if direction == "return" else 0))
            dist, acc_min, acc_cost = access(hub_pt)
            frm, to = (hub_pt, jp_pt) if direction == "outbound" else (jp_pt, hub_pt)
            out.append(TransportData(
                direction=direction, mode=mode, name=name, service_class=cls, depart_at=dep, arrive_at=arr,
                duration_minutes=int((arr - dep).total_seconds() // 60), cost_per_person=Decimal(pp),
                distance_m=D.ROAD_KM * 1000, from_name=frm[0], from_lat=frm[1], from_lng=frm[2], to_name=to[0],
                to_lat=to[1], to_lng=to[2], comfort=comfort, verification_state="demo",
                source="Sample timetable (demo mode): not a real service; check the operator for actual departures",
                access_minutes=acc_min, access_cost=acc_cost, access_distance_m=dist,
                polyline=encode_polyline([(frm[1], frm[2]), (27.6, 76.6), (to[1], to[2])])))

        if "train" in modes:
            for t, mins in D.TRAINS_OUT:
                for cls, fare in D.TRAIN_FARES.items():
                    add("outbound", "train", "", cls, depart_day, t, mins, fare, hub["train"], jp["train"],
                        4 if cls == "CC" else 2)
            for t, mins in D.TRAINS_BACK:
                for cls, fare in D.TRAIN_FARES.items():
                    add("return", "train", "", cls, return_day, t, mins, fare, hub["train"], jp["train"],
                        4 if cls == "CC" else 2)
        if "bus" in modes:
            for t, mins, fare, kind in D.BUSES_OUT:
                add("outbound", "bus", "", kind.upper() if kind == "ac" else "Ordinary", depart_day, t, mins, fare,
                    hub["bus"], jp["bus"], 3 if kind == "ac" else 1)
            for t, mins, fare, kind in D.BUSES_BACK:
                add("return", "bus", "", kind.upper() if kind == "ac" else "Ordinary", return_day, t, mins, fare,
                    hub["bus"], jp["bus"], 3 if kind == "ac" else 1)
        if "flight" in modes:
            for t, mins, fare in D.FLIGHTS_OUT:
                add("outbound", "flight", "", "Economy", depart_day, t, mins, fare, hub["air"], jp["air"], 5)
            for t, mins, fare in D.FLIGHTS_BACK:
                add("return", "flight", "", "Economy", return_day, t, mins, fare, hub["air"], jp["air"], 5)
        for mode in ("cab", "own_car"):
            if mode not in modes:
                continue
            per_vehicle = F.own_car_cost(D.ROAD_KM) if mode == "own_car" else F.cab_fare(D.ROAD_KM, 1)[0]
            for direction, day in (("outbound", depart_day), ("return", return_day)):
                start = window_start if direction == "outbound" else window_end - timedelta(minutes=D.ROAD_MINUTES)
                a, b = ((origin.lat, origin.lng), (dest.lat, dest.lng))
                if direction == "return":
                    a, b = b, a
                out.append(TransportData(
                    direction=direction, mode=mode, name="Outstation cab (sedan)" if mode == "cab" else "Your car",
                    depart_at=start, arrive_at=start + timedelta(minutes=D.ROAD_MINUTES),
                    duration_minutes=D.ROAD_MINUTES, cost_per_person=Decimal("0"), per_vehicle=True,
                    vehicle_cost=per_vehicle, distance_m=D.ROAD_KM * 1000, comfort=4, flexible=True,
                    from_name=origin.name if direction == "outbound" else dest.name,
                    to_name=dest.name if direction == "outbound" else origin.name,
                    from_lat=a[0], from_lng=a[1], to_lat=b[0], to_lng=b[1], verification_state="demo",
                    source="Yaitro fare model (demo)", polyline=encode_polyline([a, (27.6, 76.6), b])))
        return [stamp(t, SourceKind.SAMPLE) for t in out]  # demo mode's sample data


class DemoSearchProvider(WebSearchProvider):
    name = "demo-search"

    def search(self, query: str, num: int = 5) -> list[SearchResult]:
        q = query.lower()
        for a in D.ATTRACTIONS:
            if a["name"].lower() in q:
                return [SearchResult(**r) for r in D.SEARCH_RESULTS.get(a["id"], [])][:num]
        return []


class DemoWeatherProvider(WeatherProvider):
    name = "demo-weather"

    def forecast(self, lat, lng, day) -> WeatherData | None:
        w = D.WEATHER
        return WeatherData(w["summary"], w["max"], w["min"], w["rain"], "demo", "Yaitro demo data")