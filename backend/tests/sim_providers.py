"""Simulated providers for scenario testing: any city, deterministic, offline, with the awkward cases real data
has — split and evening-only opening hours, a weekly closing day, veg-only and non-veg-only restaurants, sparse or
badly-timed timetables, long overnight trains, buses with changes. Prices are generated in rupees and converted
into the destination's own currency, the way the live providers do.

The engine sees the same interfaces as with Google, so these scenarios exercise the real planner end to end.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.providers.base import HotelProvider, MapsProvider, PlacesProvider, TransportProvider
from app.providers.demo import DemoMapsProvider, DemoSearchProvider, DemoWeatherProvider
from app.providers.llm import TemplateLLMProvider
from app.providers.rail_chain import RailAwareTransport, RailChain
from app.providers.registry import Providers
from app.providers.types import HotelData, PlaceData, RestaurantData, RouteResult, Suggestion, TransportData
from app.services import fare_model as F
from app.utils.geo import estimate_road, haversine_m
from app.utils.locale import currency_for
from app.utils.money import round_rupees, to_currency

CITIES: dict[str, tuple[float, float]] = {
    "Gurugram": (28.4595, 77.0266), "New Delhi": (28.6139, 77.2090), "Jaipur": (26.9124, 75.7873),
    "Agra": (27.1767, 78.0081), "Rishikesh": (30.0869, 78.2676), "Mussoorie": (30.4598, 78.0644),
    "Udaipur": (24.5854, 73.7125), "Mumbai": (19.0760, 72.8777), "Pune": (18.5204, 73.8567),
    "Lonavala": (18.7546, 73.4062), "Goa": (15.4909, 73.8278), "Bengaluru": (12.9716, 77.5946),
    "Mysuru": (12.2958, 76.6394), "Varanasi": (25.3176, 82.9739), "Amritsar": (31.6340, 74.8723),
    "London": (51.5072, -0.1276), "Edinburgh": (55.9533, -3.1883), "Lisbon": (38.7223, -9.1393),
    "Sintra": (38.8029, -9.3817), "Kyoto": (35.0116, 135.7681), "Osaka": (34.6937, 135.5023),
}

TAG_SETS = [["fort", "heritage", "palace"], ["museum", "heritage"], ["temple", "spiritual"], ["garden", "nature", "relaxed"],
            ["viewpoint", "sunset", "romantic"], ["market", "shopping", "street_food"], ["lake", "nature", "romantic"],
            ["landmark", "photography"], ["adventure", "nature"], ["gallery", "museum"], ["park", "relaxed"]]


def _daily(spans, closed: int | None = None) -> dict:
    return {"weekly": {str(d): ([] if d == closed else spans) for d in range(7)}, "source": "sim"}


HOURS = [lambda: _daily([["09:00", "17:00"]]), lambda: _daily([["06:00", "20:00"]]),
         lambda: _daily([["10:00", "18:00"]], closed=0), lambda: _daily([["09:30", "13:00"], ["15:00", "19:30"]]),
         lambda: _daily([["00:00", "23:59"]]), lambda: _daily([["17:00", "22:00"]]),
         lambda: _daily([["08:00", "16:30"]], closed=4)]


# Real places that exist but that the area search doesn't return — found only by name or by id, the way
# "also visit Fatehpur Sikri" or a pinned Taj Mahal reaches the planner in live data.
HIDDEN: dict[str, list[tuple[str, float, float, list[str]]]] = {
    "Agra": [("Fatehpur Sikri", 27.0940, 77.6679, ["fort", "palace", "heritage"]),
             ("Sikandra Fort", 27.2200, 77.9500, ["heritage", "landmark"])],
}


def _rng(*parts) -> random.Random:
    return random.Random("|".join(map(str, parts)))


def _near(rng: random.Random, lat: float, lng: float, km: float) -> tuple[float, float]:
    return lat + rng.uniform(-km, km) / 111, lng + rng.uniform(-km, km) / 100


def _city(name: str) -> PlaceData:
    lat, lng = CITIES[name]
    return PlaceData("sim", f"sim:city:{name}", name, "city", lat, lng, address=f"{name}", city=name)


def _money(inr, city: PlaceData) -> Decimal:
    return to_currency(Decimal(inr), currency_for(city.lat, city.lng))


class SimPlaces(PlacesProvider):
    name = "sim-places"
    live = True  # behave like live data, so nothing is labelled "demo"

    def __init__(self, sparse_places: bool = False):
        self.sparse = sparse_places

    def autocomplete(self, query: str, limit: int = 6) -> list[Suggestion]:
        return [Suggestion(f"sim:city:{n}", n, None, *CITIES[n]) for n in CITIES if query.lower() in n.lower()][:limit]

    def resolve(self, text: str, place_id: str | None = None) -> PlaceData | None:
        for n in CITIES:
            if (place_id and place_id == f"sim:city:{n}") or n.lower() in (text or "").lower():
                return _city(n)
        return None

    def reverse(self, lat: float, lng: float) -> PlaceData | None:
        return _city(min(CITIES, key=lambda n: haversine_m(lat, lng, *CITIES[n])))

    def attractions(self, city: PlaceData, interests: list[str]) -> list[PlaceData]:
        rng, out = _rng("att", city.name), []
        for i in range(4 if self.sparse else 22):
            tags = rng.choice(TAG_SETS)
            lat, lng = _near(rng, city.lat, city.lng, 4)
            out.append(PlaceData("sim", f"sim:{city.name}:a{i}", f"{city.name} {tags[0].title()} {i}", "attraction",
                                 lat, lng, address=f"{city.name}", city=city.name, rating=round(rng.uniform(3.6, 4.8), 1),
                                 rating_count=rng.randint(200, 90000), tags=tags, opening_hours=rng.choice(HOURS)(),
                                 business_status="OPERATIONAL", entry_fee=_money(rng.choice([0, 0, 30, 50, 100, 200, 500]), city),
                                 entry_fee_state="estimated", recommended_minutes=rng.choice([30, 45, 60, 90, 120]),
                                 description=f"A sample {tags[0]} in {city.name}."))
        return out

    def _hidden(self, city: PlaceData) -> list[PlaceData]:
        return [PlaceData("sim", f"sim:{city.name}:hidden{i}", name, "attraction", lat, lng, address=name, city=city.name,
                          rating=4.5, rating_count=40000, tags=tags, opening_hours=_daily([["09:00", "17:00"]]),
                          business_status="OPERATIONAL", entry_fee=_money(50, city), entry_fee_state="estimated",
                          recommended_minutes=60) for i, (name, lat, lng, tags) in enumerate(HIDDEN.get(city.name, []))]

    def attraction(self, place_id: str, city: PlaceData) -> PlaceData | None:
        return next((p for p in [*self.attractions(city, []), *self._hidden(city)] if p.provider_place_id == place_id), None)

    def find_attraction(self, name: str, city: PlaceData, max_km: float = 60) -> PlaceData | None:
        import difflib
        want = name.lower()
        best = max(((difflib.SequenceMatcher(None, want, p.name.lower()).ratio(), p)
                    for p in [*self._hidden(city), *self.attractions(city, [])]), key=lambda x: x[0], default=(0, None))
        return best[1] if best[0] >= 0.7 and haversine_m(best[1].lat, best[1].lng, city.lat, city.lng) <= max_km * 1000 else None

    def restaurants(self, city: PlaceData, food: str, premium: bool) -> list[PlaceData]:
        rng, out = _rng("food", city.name), []
        kinds = [(True, True, False), (False, True, True), (False, False, True)]  # veg_only, serves_veg, serves_non_veg
        for i in range(3 if self.sparse else 18):
            veg_only, veg, non_veg = kinds[i % 3]
            lat, lng = _near(rng, city.lat, city.lng, 4)
            meals = rng.choice([["breakfast", "snack"], ["lunch", "dinner"], ["breakfast", "lunch", "snack", "dinner"],
                                ["snack", "dinner"], ["lunch", "snack"]])
            label = " (veg)" if veg_only else (" (non-veg only)" if not veg else "")
            out.append(PlaceData("sim", f"sim:{city.name}:r{i}", f"{city.name} Kitchen {i}{label}", "restaurant", lat, lng,
                                 city=city.name, rating=round(rng.uniform(3.7, 4.7), 1), rating_count=rng.randint(100, 30000),
                                 tags=["local_favourite"], opening_hours=rng.choice(HOURS[:4])(), business_status="OPERATIONAL",
                                 recommended_minutes=60,
                                 restaurant=RestaurantData(veg_only=veg_only, serves_veg=veg, serves_non_veg=non_veg,
                                                           cuisines=["Local"], meal_types=meals,
                                                           avg_cost_per_person=_money(rng.choice([150, 300, 450, 700, 1200]), city),
                                                           cost_state="estimated")))
        return out


class SimHotels(HotelProvider):
    name = "sim-hotels"

    def hotels(self, city: PlaceData, tier: str) -> list[PlaceData]:
        rng = _rng("hotel", city.name, tier)
        base = F.STAY_NIGHTLY.get(tier, 2800)
        return [PlaceData("sim", f"sim:{city.name}:h{tier}{i}", f"{city.name} {tier.title()} Stay {i}", "hotel",
                          *_near(rng, city.lat, city.lng, 4), city=city.name, tags=[tier],
                          hotel=HotelData(tier=tier, stars=F.STAY_STARS.get(tier),
                                          nightly_rate=_money(int(base * rng.uniform(0.8, 1.2)), city)))
                for i in range(3)]


@dataclass
class Timetable:
    """How a route is served: 'normal', 'sparse' (one awkward train each way), 'road' (no trains or buses at all)."""
    kind: str = "normal"


class SimTransport(TransportProvider):
    name = "sim-transport"
    live = True

    def __init__(self, timetable: Timetable):
        self.tt = timetable

    @staticmethod
    def _station(city: PlaceData, mode: str) -> tuple[str, float, float]:
        rng = _rng("station", city.name, mode)
        lat, lng = _near(rng, city.lat, city.lng, 6)
        return f"{city.name} {'Junction' if mode == 'train' else 'Bus Stand' if mode == 'bus' else 'Airport'}", lat, lng

    def intercity_options(self, origin, dest, depart_day: date, return_day: date, window_start, window_end, modes,
                          tz=None, currency="INR"):
        km = haversine_m(origin.lat, origin.lng, dest.lat, dest.lng) / 1000 * 1.25
        out: list[TransportData] = []

        def add(direction, mode, day, hhmm, minutes, pp_inr, cls=None, changes=0, comfort=3):
            a, b = (origin, dest) if direction == "outbound" else (dest, origin)
            (fn, flat, flng), (tn, tlat, tlng) = self._station(a, mode), self._station(b, mode)
            dep = datetime.combine(day, datetime.min.time()) + timedelta(hours=int(hhmm[:2]), minutes=int(hhmm[3:]))
            acc_m, acc_s = estimate_road(origin.lat, origin.lng, *((flat, flng) if direction == "outbound" else (tlat, tlng)))
            out.append(TransportData(
                direction=direction, mode=mode, name=f"Sim {mode.title()} {hhmm.replace(':', '')}", service_class=cls,
                depart_at=dep, arrive_at=dep + timedelta(minutes=minutes), duration_minutes=minutes,
                cost_per_person=to_currency(round_rupees(Decimal(pp_inr)), currency), distance_m=int(km * 1000),
                from_name=fn, to_name=tn, from_lat=flat, from_lng=flng, to_lat=tlat, to_lng=tlng, comfort=comfort,
                verification_state="estimated", source="Simulated timetable",
                access_minutes=max(5, acc_s // 60 + 5), access_cost=to_currency(F.local_leg_cost(acc_m, "cab", 1), currency),
                access_distance_m=acc_m,
                meta={"schedule_state": "verified_provider", "number": hhmm.replace(":", ""), "changes": changes,
                      "source_kind": "live_api"}))  # a simulated live timetable API

        if self.tt.kind != "road":
            if "train" in modes and 60 < km < 2500:
                mins = int(km / 55 * 60) + 20
                classes = ["2S", "CC"] if mins <= 480 else ["SL", "3A"]
                times = (["05:30", "06:40", "09:10", "13:30", "17:00", "21:30"] if self.tt.kind == "normal"
                         else ["23:40"])
                back = (["06:00", "08:30", "14:00", "16:45", "18:30", "22:15"] if self.tt.kind == "normal"
                        else ["03:50"])
                for direction, day, ts in (("outbound", depart_day, times), ("return", return_day, back)):
                    for t in ts:
                        for cls in classes:
                            add(direction, "train", day, t, mins, F.train_fare(km, cls), cls,
                                comfort=F.TRAIN_CLASSES[cls][2])
            if "bus" in modes and 40 < km < 900 and self.tt.kind == "normal":
                mins = int(km / 45 * 60) + 15
                for direction, day, ts in (("outbound", depart_day, ["06:00", "08:00", "14:00", "22:00"]),
                                           ("return", return_day, ["07:00", "15:30", "18:00", "22:30"])):
                    for i, t in enumerate(ts):
                        add(direction, "bus", day, t, mins + 90 * (i == 1), F.bus_fare(km), "AC", changes=2 * (i == 1))
            if "flight" in modes and km > 450 and self.tt.kind == "normal":
                mins = int(km / 700 * 60) + 40
                for direction, day, ts in (("outbound", depart_day, ["07:05", "19:10"]),
                                           ("return", return_day, ["09:40", "20:35"])):
                    for t in ts:
                        add(direction, "flight", day, t, mins, F.flight_fare(km), "Economy", comfort=5)
        for mode in ("cab", "own_car"):
            if mode not in modes:
                continue
            minutes = int(km / 50 * 60) + 15
            pv = F.own_car_cost(km) if mode == "own_car" else F.cab_fare(km, 1)[0]
            for direction in ("outbound", "return"):
                start = window_start if direction == "outbound" else window_end - timedelta(minutes=minutes)
                a, b = (origin, dest) if direction == "outbound" else (dest, origin)
                out.append(TransportData(direction=direction, mode=mode, name="", depart_at=start,
                                         arrive_at=start + timedelta(minutes=minutes), duration_minutes=minutes,
                                         cost_per_person=Decimal(0), per_vehicle=True, vehicle_cost=to_currency(pv, currency),
                                         distance_m=int(km * 1000), from_name=a.name, to_name=b.name, from_lat=a.lat,
                                         from_lng=a.lng, to_lat=b.lat, to_lng=b.lng, comfort=4, flexible=True,
                                         verification_state="estimated", source="Simulated road estimate"))
        return out


class SlowMaps(DemoMapsProvider):
    """Real routes that take `factor` times longer than the planner's own estimates (traffic, a city's one-way
    streets): the situation where a day built on estimates doesn't fit once the real routes come in."""
    name = "sim-slow-maps"

    def __init__(self, factor: float):
        self.factor = factor

    def compute_route(self, origin, dest, mode="drive", depart_at=None) -> RouteResult:
        r = super().compute_route(origin, dest, mode, depart_at)
        return RouteResult(r.distance_m, int(r.duration_s * self.factor), r.polyline, "verified_provider", r.mode)


def sim_providers(timetable: str = "normal", sparse_places: bool = False, rail_estimate: bool = False,
                  slow_roads: float = 1.0) -> Providers:
    """`rail_estimate`: no timetable source answers for trains, so Indian train trips go through the station-network
    estimate (rail_chain), exactly as when every rail API is down."""
    places = SimPlaces(sparse_places)
    transport: TransportProvider = SimTransport(Timetable(timetable))
    if rail_estimate:
        class NoTrains(SimTransport):
            def intercity_options(self, *a, **k):
                return [t for t in super().intercity_options(*a, **k) if t.mode != "train"]
        transport = RailAwareTransport(NoTrains(Timetable(timetable)), RailChain([]))
    maps: MapsProvider = SlowMaps(slow_roads) if slow_roads != 1.0 else DemoMapsProvider()
    return Providers(maps, places, SimHotels(), transport, DemoSearchProvider(), DemoWeatherProvider(),
                     TemplateLLMProvider())
