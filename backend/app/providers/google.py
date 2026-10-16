"""Google Maps Platform: Places API (New), Routes API, Geocoding API.

Uses place IDs everywhere and a server-side key only. Responses are cached in `api_cache`.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.core.provenance import SourceKind, stamp
from app.core.telemetry import ContextThreadPool
from app.providers import ttl as ttls
from app.core.config import get_settings
from app.core.errors import ProviderUnavailable
from app.providers import http
from app.providers.airports import airport_label, nearest_airports
from app.providers.base import HotelProvider, MapsProvider, PlacesProvider, TransportProvider
from app.providers.cache import cached
from app.providers.types import (
    HotelData,
    MatrixResult,
    PlaceData,
    RestaurantData,
    RouteResult,
    Suggestion,
    TransportData,
)
from app.services import fare_model as F
from app.utils.geo import estimate_road, haversine_m
from app.utils.locale import currency_for
from app.services import destination_profile
from app.utils.money import to_currency

log = logging.getLogger("tripai.google")

AIRPORT_MINUTES = 90  # check-in and security before a domestic flight, and getting out after it
AIRPORT_MINUTES_ABROAD = 180  # an international flight: earlier check-in, emigration, immigration
MIN_FLIGHT_KM = 300  # between airports closer than this, nobody flies


def airport_flights(origin: PlaceData, dest: PlaceData, window_start: datetime, window_end: datetime,
                    currency: str) -> list[TransportData] | None:
    """A flight between the airports that serve the two places, with the road ride at each end: Gurugram ->
    Mussoorie is a cab to Delhi airport, the flight to Dehradun, and the drive up from Jolly Grant (the planner adds
    that last part from the arrival airport, as it does from a railway station).

    Timing and fare are estimates (no flight schedule source), so the departure is placed where it suits the trip.
    Returns [] when flying makes no sense (one airport serves both places, or they are too close), and None when an
    airport at either end isn't known, so the caller falls back to a city-to-city estimate."""
    o_air, d_air = nearest_airports(origin.lat, origin.lng, 1), nearest_airports(dest.lat, dest.lng, 1)
    if not o_air or not d_air:
        return None
    (oc, olat, olng, _), (dc, dlat, dlng, _) = o_air[0], d_air[0]
    air_km = haversine_m(olat, olng, dlat, dlng) / 1000
    if oc == dc or air_km < MIN_FLIGHT_KM:
        return []
    at_airports = AIRPORT_MINUTES if _in_india(olat, olng) == _in_india(dlat, dlng) else AIRPORT_MINUTES_ABROAD
    minutes = int(40 + air_km / 750 * 60) + at_airports  # taxi, climb and cruise, plus the time in the airports
    acc_m, acc_s = estimate_road(origin.lat, origin.lng, olat, olng)
    acc_min, acc_cost = max(10, round(acc_s / 60) + 10), F.local_leg_cost(acc_m, "cab", 1)
    fare = to_currency(F.flight_fare(air_km), currency)
    out = []
    for direction in ("outbound", "return"):
        if direction == "outbound":
            dep = window_start + timedelta(minutes=acc_min)
            frm, to, fpt, tpt = oc, dc, (olat, olng), (dlat, dlng)
        else:
            dep = window_end - timedelta(minutes=acc_min + minutes)  # home by the end of the trip
            frm, to, fpt, tpt = dc, oc, (dlat, dlng), (olat, olng)
        out.append(TransportData(
            direction=direction, mode="flight", name="", service_class="Economy", depart_at=dep,
            arrive_at=dep + timedelta(minutes=minutes), duration_minutes=minutes, cost_per_person=fare,
            distance_m=int(air_km * 1000), from_name=airport_label(frm), to_name=airport_label(to), from_lat=fpt[0],
            from_lng=fpt[1], to_lat=tpt[0], to_lng=tpt[1], comfort=5, flexible=True, verification_state="estimated",
            source="Airports from Yaitro's airport list; flight time and fare estimated (no live flight schedule)",
            access_minutes=acc_min, access_cost=to_currency(acc_cost, currency), access_distance_m=acc_m,
            meta={"schedule_state": "estimated", "airport_minutes": at_airports}))
    return out

RAIL_TYPES = {"HEAVY_RAIL", "RAIL", "COMMUTER_TRAIN", "HIGH_SPEED_TRAIN", "LONG_DISTANCE_TRAIN"}
BUS_TYPES = {"BUS", "INTERCITY_BUS", "TROLLEYBUS"}


# A stop name is only shown if it's in a script an English-reading traveller can read. Some transit feeds
# (regional bus and rail operators especially) carry stop names only in a local script even when English is
# requested; in that case the city name is used instead of showing unreadable text.
_NON_LATIN = re.compile(r"[\u0900-\u0DFF\u0F00-\u0FFF\u1000-\u109F\u3040-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF]")


LOCAL_VEHICLE = {"SUBWAY": "metro", "METRO_RAIL": "metro", "LIGHT_RAIL": "metro", "MONORAIL": "metro", "TRAM": "tram",
                 "BUS": "bus", "INTERCITY_BUS": "bus", "TROLLEYBUS": "bus", "SHARE_TAXI": "shared taxi",
                 "COMMUTER_TRAIN": "local train", "HEAVY_RAIL": "train", "RAIL": "train", "FERRY": "ferry"}


def local_transit(route: dict) -> dict | None:
    """The public-transport rides in a Google route, in order: what to take (metro, bus, local train), the line, where
    to get on and off, and how many stops; with the fare when Google has one. Stop names in a script an English
    reader can't read are left out rather than shown garbled. None when the route is walking only."""
    steps = []
    for leg in route.get("legs", []):
        for s in leg.get("steps", []):
            d = s.get("transitDetails")
            if not d:
                continue
            line = d.get("transitLine", {})
            stops = d.get("stopDetails", {})
            name = (line.get("nameShort") or line.get("name") or "").strip()
            frm = _stop_label((stops.get("departureStop") or {}).get("name"), "")
            to = _stop_label((stops.get("arrivalStop") or {}).get("name"), "")
            steps.append({"vehicle": LOCAL_VEHICLE.get(line.get("vehicle", {}).get("type", ""), "bus"),
                          "line": name if name and not _NON_LATIN.search(name) else None,
                          "from": frm or None, "to": to or None, "stops": d.get("stopCount"),
                          "headsign": _stop_label(d.get("headsign"), "") or None})
    if not steps:
        return None
    fare = route.get("travelAdvisory", {}).get("transitFare")
    amount = (float(fare.get("units", 0) or 0) + (fare.get("nanos", 0) or 0) / 1e9) if fare else None
    return {"steps": steps, "fare": amount, "currency": fare.get("currencyCode") if fare else None}


def _stop_label(raw: str | None, city: str) -> str:
    raw = (raw or "").strip()
    return raw if raw and not _NON_LATIN.search(raw) else city


def _ts(value: str, tz: ZoneInfo) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(tz)


def _in_india(lat: float, lng: float) -> bool:
    """Rough bounding box, used only to pick Indian Railways seat classes (2S/CC) over a generic class."""
    return 6.5 <= lat <= 35.7 and 68.0 <= lng <= 97.5
PLACES = "https://places.googleapis.com/v1"
ROUTES = "https://routes.googleapis.com"
GEOCODE = "https://maps.googleapis.com/maps/api/geocode/json"

PLACE_FIELDS = ",".join([
    "id", "displayName", "formattedAddress", "location", "rating", "userRatingCount", "priceLevel", "priceRange",
    "types", "primaryType", "regularOpeningHours", "websiteUri", "businessStatus", "servesVegetarianFood",
    "editorialSummary", "servesBreakfast", "servesLunch", "servesDinner", "addressComponents",
])
PRICE_LEVELS = {"PRICE_LEVEL_FREE": 0, "PRICE_LEVEL_INEXPENSIVE": 1, "PRICE_LEVEL_MODERATE": 2,
                "PRICE_LEVEL_EXPENSIVE": 3, "PRICE_LEVEL_VERY_EXPENSIVE": 4}
TYPE_TAGS = {
    "museum": ["museum"], "art_gallery": ["gallery", "museum"], "park": ["park", "nature"], "garden": ["garden", "romantic"],
    "hindu_temple": ["temple", "spiritual"], "church": ["spiritual"], "mosque": ["spiritual"], "place_of_worship": ["spiritual"],
    "historical_landmark": ["heritage", "landmark"], "tourist_attraction": ["landmark"], "zoo": ["zoo", "nature"],
    "amusement_park": ["adventure"], "market": ["market", "shopping"], "shopping_mall": ["shopping"],
    "hiking_area": ["adventure", "nature", "mountain"], "national_park": ["nature", "adventure", "national_park"],
    "observation_deck": ["viewpoint", "sunset", "observation"], "beach": ["nature", "romantic", "sunset", "beach"], "cafe": ["cafe"], "fine_dining_restaurant": ["fine_dining", "premium"],
    # adults only (never planned for a party with anyone under 18, services/age_fit.py); also what the "nightlife" favourite matches
    "night_club": ["nightlife"], "bar": ["nightlife"], "casino": ["nightlife"],
}
NAME_TAGS = {"fort": ["fort", "heritage"], "palace": ["palace", "heritage"], "mahal": ["palace", "heritage"],
             "lake": ["lake", "romantic"], "sunset": ["sunset", "viewpoint"], "view": ["viewpoint"], "point": ["viewpoint"],
             "bazaar": ["market", "street_food"], "market": ["market"], "temple": ["temple"], "mandir": ["temple"],
             "museum": ["museum"], "garden": ["garden"], "bagh": ["garden"], "ghat": ["spiritual", "sunset"],
             # what a destination's character is read from (services/destination_profile.py)
             "beach": ["beach", "sunset", "nature"], "valley": ["mountain", "nature", "viewpoint"], "peak": ["mountain", "viewpoint"],
             "glacier": ["mountain", "nature"], "snow": ["mountain"], "trek": ["mountain", "adventure"],
             "waterfall": ["waterfall", "nature"], " falls": ["waterfall", "nature"], "national park": ["national_park", "nature"],
             "sanctuary": ["national_park", "nature"], "bird": ["national_park", "nature"]}
# What a saved trip needs back when its Google content is older than 30 days (ADR 0005): no atmosphere fields.
REFRESH_FIELDS = "id,displayName,formattedAddress,location,regularOpeningHours,businessStatus,rating,userRatingCount"
# Interests whose places Wikidata lists well enough that a Google search for them adds little once it has answered.
WIKIDATA_COVERS = {"fort", "palace", "temple", "spiritual", "museum", "lake", "nature", "viewpoint", "heritage",
                   "sightseeing", "premium", "relaxed"}
VEG_NAME_HINTS = ("pure veg", "bhojnalaya", "mishthan", "vegetarian", "sweets", "thali")


def _key() -> str:
    key = get_settings().google_maps_server_key
    if not key:
        raise ProviderUnavailable("Google Maps key is not configured.")
    return key


def _headers(field_mask: str) -> dict:
    return {"X-Goog-Api-Key": _key(), "X-Goog-FieldMask": field_mask, "Content-Type": "application/json"}


def _duration_s(v: str | None) -> int:
    return int(float(v.rstrip("s"))) if v else 0


def convert_hours(regular: dict | None) -> dict | None:
    """Places `regularOpeningHours.periods` (0=Sunday) -> {"weekly": {"0".."6" (0=Monday)}}."""
    if not regular or not regular.get("periods"):
        return None
    weekly: dict[str, list] = {str(d): [] for d in range(7)}
    for p in regular["periods"]:
        o, c = p.get("open"), p.get("close")
        if o and not c:  # open 24 hours
            return {"weekly": {str(d): [["00:00", "24:00"]] for d in range(7)}, "source": "google"}
        day = (o["day"] - 1) % 7
        weekly[str(day)].append([f'{o.get("hour", 0):02d}:{o.get("minute", 0):02d}',
                                 f'{c.get("hour", 0):02d}:{c.get("minute", 0):02d}'])
    return {"weekly": weekly, "source": "google"}


def _tags(types: list[str], name: str) -> list[str]:
    tags: list[str] = []
    for t in types:
        tags += TYPE_TAGS.get(t, [])
    low = name.lower()
    for word, extra in NAME_TAGS.items():
        if word in low:
            tags += extra
    return sorted(set(tags)) or ["landmark"]


def _city_of(p: dict) -> str | None:
    for comp in p.get("addressComponents", []) or []:
        if "locality" in comp.get("types", []):
            return comp.get("longText")
    return None


def to_place(p: dict, category: str, currency: str = "INR") -> PlaceData:
    name = (p.get("displayName") or {}).get("text", "Unknown place")
    types = p.get("types", [])
    loc = p.get("location", {})
    price_level = PRICE_LEVELS.get(p.get("priceLevel", ""), None)
    place = PlaceData(provider="google", provider_place_id=p["id"], name=name, category=category,
                      lat=loc.get("latitude", 0.0), lng=loc.get("longitude", 0.0), address=p.get("formattedAddress"),
                      city=_city_of(p), rating=p.get("rating"), rating_count=p.get("userRatingCount"),
                      price_level=price_level, types=types, opening_hours=convert_hours(p.get("regularOpeningHours")),
                      website=p.get("websiteUri"), business_status=p.get("businessStatus"),
                      description=(p.get("editorialSummary") or {}).get("text"))
    if category == "attraction":
        place.tags = _tags(types, name)
        place.recommended_minutes, fee = F.attraction_defaults(place.tags)
        place.entry_fee = to_currency(fee, currency)
        place.entry_fee_state = "estimated"
    elif category == "restaurant":
        place.tags = _tags(types, name)
        veg_only = "vegetarian_restaurant" in types or any(h in name.lower() for h in VEG_NAME_HINTS)
        cost, state = None, "estimated"
        pr = p.get("priceRange") or {}
        if pr.get("startPrice") and pr["startPrice"].get("currencyCode") == currency:
            lo = int(pr["startPrice"].get("units", 0))
            hi = int((pr.get("endPrice") or {}).get("units", lo * 2) or lo * 2)
            cost, state = Decimal((lo + hi) // 2), "verified_provider"  # already in `currency`: no conversion
        if cost is None:
            base = Decimal(F.PRICE_LEVEL_COST.get(price_level, F.DEFAULT_MEAL_COST) if price_level is not None
                           else F.DEFAULT_MEAL_COST)
            cost = to_currency(base, currency)
        meals = [m for m, f in (("breakfast", "servesBreakfast"), ("lunch", "servesLunch"), ("dinner", "servesDinner"))
                 if p.get(f)] or ["lunch", "dinner"]
        if "cafe" in types or "bakery" in types:
            meals = sorted(set(meals + ["snack", "breakfast"]))
        place.restaurant = RestaurantData(veg_only=veg_only, serves_veg=bool(p.get("servesVegetarianFood", True)) or veg_only,
                                          serves_non_veg=not veg_only, meal_types=meals, avg_cost_per_person=cost,
                                          cost_state=state)
        place.recommended_minutes = 60
    return place


class GoogleMapsProvider(MapsProvider):
    name = "google-routes"
    live = True
    MODES = {"drive": "DRIVE", "walk": "WALK", "transit": "TRANSIT", "two_wheeler": "TWO_WHEELER"}

    def compute_route(self, origin, dest, mode="drive", depart_at=None) -> RouteResult:
        body = {"origin": {"location": {"latLng": {"latitude": origin[0], "longitude": origin[1]}}},
                "destination": {"location": {"latLng": {"latitude": dest[0], "longitude": dest[1]}}},
                "travelMode": self.MODES.get(mode, "DRIVE")}
        if depart_at and depart_at.astimezone(UTC) > datetime.now(UTC) + timedelta(minutes=5):
            body["departureTime"] = depart_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
            if mode == "drive":
                body["routingPreference"] = "TRAFFIC_AWARE"

        fields = "routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline"
        if mode == "transit":
            fields += ",routes.legs.steps.transitDetails,routes.legs.steps.travelMode,routes.travelAdvisory.transitFare"

        def fetch():
            r = http.request("POST", f"{ROUTES}/directions/v2:computeRoutes", provider="Google Routes",
                             headers=_headers(fields), json=body)
            return r.json()

        data = cached("google-transit" if mode == "transit" else "google-routes", [body],
                      ttls.get("google-transit" if mode == "transit" else "google-routes"), fetch)
        routes = data.get("routes") or []
        if not routes:
            raise ProviderUnavailable("No route found.")
        r = routes[0]
        return RouteResult(int(r.get("distanceMeters", 0)), _duration_s(r.get("duration")),
                           (r.get("polyline") or {}).get("encodedPolyline"), "verified_provider", mode,
                           local_transit(r) if mode == "transit" else None)

    def compute_matrix(self, points, mode="drive") -> MatrixResult:
        if len(points) ** 2 > 625:
            raise ProviderUnavailable("Too many points for one route matrix.")
        wps = [{"waypoint": {"location": {"latLng": {"latitude": a, "longitude": b}}}} for a, b in points]
        body = {"origins": wps, "destinations": wps, "travelMode": self.MODES.get(mode, "DRIVE"),
                "routingPreference": "TRAFFIC_UNAWARE"}

        def fetch():
            r = http.request("POST", f"{ROUTES}/distanceMatrix/v2:computeRouteMatrix", provider="Google Routes",
                             headers=_headers("originIndex,destinationIndex,duration,distanceMeters,condition"),
                             json=body, timeout=25.0)
            return r.json()

        rows = cached("google-matrix", [body], ttls.get("google-matrix"), fetch)
        cells: dict[tuple[int, int], tuple[int, int]] = {}
        for el in rows:
            i, j = el.get("originIndex", 0), el.get("destinationIndex", 0)
            if el.get("condition") == "ROUTE_EXISTS" or i == j:
                cells[(i, j)] = (int(el.get("distanceMeters", 0)), _duration_s(el.get("duration")))
        for i, a in enumerate(points):  # fill gaps deterministically, never with invented "live" data
            for j, b in enumerate(points):
                cells.setdefault((i, j), estimate_road(*a, *b))
        return MatrixResult(cells, "verified_provider")


class GooglePlacesProvider(PlacesProvider):
    name = "google-places"
    live = True

    def _text_search(self, query: str, center: tuple[float, float] | None, radius: float = 25000,
                     max_results: int = 12, veg: bool = False) -> list[dict]:
        body: dict = {"textQuery": query, "maxResultCount": max_results, "languageCode": "en"}
        if center:
            body["locationBias"] = {"circle": {"center": {"latitude": center[0], "longitude": center[1]},
                                               "radius": radius}}

        def fetch():
            r = http.request("POST", f"{PLACES}/places:searchText", provider="Google Places",
                             headers=_headers(",".join(f"places.{f}" for f in PLACE_FIELDS.split(","))), json=body)
            return r.json().get("places", [])

        return cached("google-places-search", [body], ttls.get("google-places-search"), fetch)

    def autocomplete(self, query: str, limit: int = 6) -> list[Suggestion]:
        body = {"input": query, "languageCode": "en"}

        def fetch():
            r = http.request("POST", f"{PLACES}/places:autocomplete", provider="Google Places",
                             headers={"X-Goog-Api-Key": _key(), "Content-Type": "application/json"}, json=body)
            return r.json().get("suggestions", [])

        rows = cached("google-autocomplete", [body], ttls.get("google-autocomplete"), fetch)
        out = []
        for s in rows:
            p = s.get("placePrediction")
            if not p:
                continue
            fmt = p.get("structuredFormat") or {}
            out.append(Suggestion(p["placeId"], (fmt.get("mainText") or {}).get("text") or p["text"]["text"],
                                  (fmt.get("secondaryText") or {}).get("text")))
        return out[:limit]

    def _best_suggestion_id(self, text: str) -> str | None:
        """The place Google's autocomplete would suggest first for this text. Unlike a plain text search it
        tolerates misspellings ("Masoorie" -> Mussoorie, not a village of similar spelling near Ghaziabad)."""
        try:
            found = self.autocomplete(text, limit=1)
        except ProviderUnavailable:
            return None
        return found[0].place_id if found else None

    def resolve(self, text: str, place_id: str | None = None) -> PlaceData | None:
        if (not place_id or place_id.startswith("demo:")) and text.strip():
            place_id = self._best_suggestion_id(text)  # a name typed without picking a suggestion
        if place_id and not place_id.startswith("demo:"):
            def fetch():
                r = http.request("GET", f"{PLACES}/places/{place_id}", provider="Google Places",
                                 headers=_headers(PLACE_FIELDS))
                return r.json()
            p = cached("google-place-details", [place_id], ttls.get("google-place-details"), fetch)
        else:
            rows = self._text_search(text, None, max_results=1)
            if not rows:
                return None
            p = rows[0]
        place = to_place(p, "city")
        place.city = place.city or place.name
        return place

    def reverse(self, lat: float, lng: float) -> PlaceData | None:
        def fetch():
            r = http.request("GET", GEOCODE, provider="Google Geocoding",
                             params={"latlng": f"{lat},{lng}", "key": _key(), "result_type": "locality"})
            return r.json().get("results", [])

        rows = cached("google-reverse", [round(lat, 3), round(lng, 3)], ttls.get("google-reverse"), fetch)
        if not rows:
            return None
        r = rows[0]
        loc = r["geometry"]["location"]
        name = r["address_components"][0]["long_name"]
        return PlaceData(provider="google", provider_place_id=r["place_id"], name=name, category="city",
                         lat=loc["lat"], lng=loc["lng"], address=r.get("formatted_address"), city=name)

    def attraction(self, place_id: str, city: PlaceData) -> PlaceData | None:
        if not place_id or place_id.startswith(("demo:", "sim:")):
            return None

        def fetch():
            r = http.request("GET", f"{PLACES}/places/{place_id}", provider="Google Places",
                             headers=_headers(PLACE_FIELDS))
            return r.json()
        try:
            p = cached("google-place-details", [place_id], ttls.get("google-place-details"), fetch)  # same cache entry resolve() uses
        except ProviderUnavailable:
            return None
        if not p or not p.get("id"):
            return None
        return to_place(p, "attraction", currency_for(city.lat, city.lng))

    def refresh_place(self, place_id: str) -> PlaceData | None:
        if not place_id or place_id.startswith(("demo:", "sim:")):
            return None
        r = http.request("GET", f"{PLACES}/places/{place_id}", provider="Google Places", headers=_headers(REFRESH_FIELDS))
        p = r.json()  # not cached: the point is today's content
        return to_place(p, "attraction") if p and p.get("id") else None

    def find_attraction(self, name: str, city: PlaceData, max_km: float = 60) -> PlaceData | None:
        try:
            suggestions = self.autocomplete(f"{name} {city.name.split(',')[0]}", limit=3)
        except ProviderUnavailable:
            return None
        for s in suggestions:
            place = self.attraction(s.place_id, city)
            if place and haversine_m(place.lat, place.lng, city.lat, city.lng) <= max_km * 1000:
                return place
        return None

    def _searches(self, jobs: list[tuple[str, float, int]], center: tuple[float, float]) -> list[list[dict]]:
        """Several text searches at once (they don't depend on each other), in the order asked. One that fails or times
        out gives no rows instead of sinking the plan; only when every one fails is the error raised (the caller's
        fallback then applies)."""
        def one(job):
            q, radius, n = job
            try:
                return self._text_search(q, center, radius=radius, max_results=n)
            except ProviderUnavailable as exc:
                log.warning("Google search %r skipped: %s", q, exc)
                return exc
        if not jobs:
            return []
        if len(jobs) == 1:
            out = [one(jobs[0])]
        else:
            with ContextThreadPool(max_workers=min(4, len(jobs)), thread_name_prefix="places") as pool:
                out = list(pool.map(one, jobs))
        failures = [r for r in out if isinstance(r, ProviderUnavailable)]
        if failures and len(failures) == len(out):
            raise failures[0]
        return [[] if isinstance(r, ProviderUnavailable) else r for r in out]

    def attractions(self, city: PlaceData, interests: list[str], known: list[PlaceData] | None = None) -> list[PlaceData]:
        currency = currency_for(city.lat, city.lng)  # offline, from the destination's own coordinates
        queries = [f"top tourist attractions in {city.name}"]
        # Style-level hints (from trip_styles) plus specific place tags (from the traveller's own "favourites"
        # picks or chat mentions) both widen the search here, not just the ranking of whatever this default
        # query already returned.
        extra = {"romantic": "sunset point", "adventure": "adventure activities", "food": "famous food market",
                 "relaxed": "gardens and lakes", "sightseeing": "historical monuments", "premium": "heritage palace",
                 "fort": "forts", "palace": "palaces", "temple": "temples", "spiritual": "spiritual and pilgrimage sites",
                 "nature": "nature spots and viewpoints", "lake": "lakes", "museum": "museums",
                 "market": "markets and bazaars", "street_food": "street food spots", "photography": "photogenic spots",
                 "heritage": "heritage sites", "nightlife": "nightlife and bars", "viewpoint": "viewpoints",
                 "sunset": "sunset points", "adventure_activities": "adventure activities",
                 "family": "family friendly attractions and kids activities", "day_trips": "day trips and nearby getaways"}
        # With Wikidata's famous sights already in hand (`known`, providers/wikidata.py), the searches it covers (forts,
        # temples, museums, lakes, "places near") are skipped: only what it can't know (bars, street food, markets) is asked.
        known = known or []
        lean = sum(1 for k in known if (k.meta or {}).get("wiki_links", 0) >= 3) >= 10
        wanted = [i for i in dict.fromkeys(interests) if i in extra and not (lean and i in WIKIDATA_COVERS)]
        queries += [f"{extra[i]} in {city.name}" for i in wanted][:3]
        seen, out = set(), []

        center = (city.lat, city.lng)

        def take(rows: list[dict]) -> None:
            for p in rows:
                if p["id"] in seen or p.get("businessStatus") in ("CLOSED_PERMANENTLY",):
                    continue
                if any(t in p.get("types", []) for t in ("lodging", "restaurant", "travel_agency")):
                    continue
                seen.add(p["id"])
                out.append(to_place(p, "attraction", currency))

        def run_all(qs: list[tuple[str, float]], must: bool) -> None:
            try:
                for rows in self._searches([(q, r, 12) for q, r in qs], center):
                    take(rows)
            except ProviderUnavailable:
                if must and not known:
                    raise  # nothing at all to plan with: the caller decides
                log.warning("Google sight searches for %s failed; planning with what we have", city.name)

        run_all([(q, 25000) for q in queries], must=True)
        # What kind of place is it? A beach town gets "best beaches in Goa", the hills "viewpoints, valleys and treks",
        # a big city "famous landmarks and museums": the search behind the plan matches the destination.
        more = [(f"{q} in {city.name}", 25000) for q in destination_profile.queries(destination_profile.profile(out + known))
                if not any(q in done for done in queries)]
        # A small town has few real sights of its own (Bharatpur): add the well-known places around it, so the days
        # are filled with Deeg or Fatehpur Sikri rather than neighbourhood parks.
        if not lean and sum(1 for p in out if (p.rating_count or 0) >= 500) < 10:
            more.append((f"famous places to visit near {city.name}", 60000))
        run_all(more, must=False)
        return out[:48 if "day_trips" in wanted else 32]  # a long trip needs a bigger pool to fill its days

    def restaurants(self, city: PlaceData, food: str, premium: bool) -> list[PlaceData]:
        currency = currency_for(city.lat, city.lng)
        queries = [f"{'pure vegetarian ' if food == 'veg' else ''}restaurants in {city.name}",
                   f"famous local food in {city.name}", f"cafes in {city.name}"]
        if premium:
            queries.append(f"fine dining in {city.name}")
        seen, out = set(), []
        for rows in self._searches([(q, 15000, 10) for q in queries], (city.lat, city.lng)):
            for p in rows:
                if p["id"] in seen or p.get("businessStatus") == "CLOSED_PERMANENTLY":
                    continue
                seen.add(p["id"])
                out.append(to_place(p, "restaurant", currency))
        return out[:24]


class GoogleHotelProvider(HotelProvider):
    name = "google-hotels"
    live = True
    QUERIES = {"premium": "5 star luxury hotel", "tier1": "4 star hotel", "tier2": "3 star hotel",
               "budget": "budget hotel", "oyo": "OYO hotel", "hostel": "backpacker hostel"}

    def __init__(self, places: GooglePlacesProvider):
        self.places = places

    def hotels(self, city: PlaceData, tier: str) -> list[PlaceData]:
        currency = currency_for(city.lat, city.lng)
        rows = self.places._text_search(f"{self.QUERIES.get(tier, 'hotel')} in {city.name}", (city.lat, city.lng),
                                        max_results=8)
        out = []
        for p in rows:
            place = to_place(p, "hotel", currency)
            place.hotel = HotelData(tier=tier, stars=F.STAY_STARS.get(tier),
                                    nightly_rate=to_currency(F.stay_nightly(tier), currency), rate_state="estimated")
            out.append(place)
        return out


class GoogleTransportProvider(TransportProvider):
    """Intercity options from the Routes API; fares come from the deterministic fare model (labelled estimated)."""
    name = "google-transport"
    live = True

    def __init__(self, maps: GoogleMapsProvider, default_tz: str = "Asia/Kolkata"):
        self.maps = maps
        self.default_tz = default_tz  # used only if a caller doesn't pass its own trip's timezone

    def _transit(self, origin, dest, depart_at: datetime, rail: bool, tz: ZoneInfo) -> dict | None:
        body = {"origin": {"location": {"latLng": {"latitude": origin.lat, "longitude": origin.lng}}},
                "destination": {"location": {"latLng": {"latitude": dest.lat, "longitude": dest.lng}}},
                "travelMode": "TRANSIT", "computeAlternativeRoutes": True, "languageCode": "en",
                "transitPreferences": {"allowedTravelModes": ["TRAIN", "RAIL"] if rail else ["BUS"]}}
        aware = depart_at.replace(tzinfo=tz)  # the destination's own local time, not assumed to be India
        if aware > datetime.now(UTC):
            body["departureTime"] = aware.astimezone(UTC).isoformat().replace("+00:00", "Z")
        fields = ("routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline,routes.travelAdvisory.transitFare,"
                  "routes.legs.steps.transitDetails,routes.legs.steps.travelMode")

        def fetch():
            return http.request("POST", f"{ROUTES}/directions/v2:computeRoutes", provider="Google Routes",
                                headers=_headers(fields), json=body).json()
        try:
            return cached("google-transit", [body], ttls.get("google-transit"), fetch)
        except ProviderUnavailable as exc:
            # Usually a key problem: Routes API not enabled, or not allowed in the key's API restrictions
            log.warning("Google transit request failed (%s -> %s): %s", origin.name, dest.name, exc)
            return None

    def _scheduled(self, data: dict | None, direction: str, mode: str, km: float,
                   home: PlaceData, origin_place: PlaceData, dest_place: PlaceData, tz: ZoneInfo,
                   currency: str) -> list[TransportData]:
        """Turn Google transit routes into options. A service name or number is shown only when the live
        timetable provides one; otherwise the option stays unnamed (never invented).

        Some transit feeds only carry a stop's name in the local script, even when English is requested. Rather
        than show that to an English-reading traveller, an unreadable stop name falls back to the city name.
        """
        from_city, to_city = ((origin_place.name, dest_place.name) if direction == "outbound"
                              else (dest_place.name, origin_place.name))
        out = []
        main_types = RAIL_TYPES if mode == "train" else BUS_TYPES
        for route in (data or {}).get("routes", [])[:3]:
            steps = [s["transitDetails"] for leg in route.get("legs", []) for s in leg.get("steps", [])
                     if s.get("transitDetails")]
            if not steps:
                continue
            try:
                timed = [(t, _ts(t["stopDetails"]["departureTime"], tz), _ts(t["stopDetails"]["arrivalTime"], tz))
                         for t in steps]
            except (KeyError, ValueError):
                continue
            def kind(x):
                return x[0].get("transitLine", {}).get("vehicle", {}).get("type") or "OTHER"
            # The journey is named after its longest leg. If that leg isn't the requested kind of vehicle
            # (e.g. a regional rapid-transit hop followed by a long bus ride), this isn't a train journey.
            longest = max(timed, key=lambda x: x[2] - x[1])
            if not (kind(longest) in main_types
                    or (kind(longest) == "OTHER" and longest[2] - longest[1] >= timedelta(minutes=45))):
                continue
            main = longest[0]
            main_minutes = (longest[2] - longest[1]).total_seconds() / 60
            dep, arr = timed[0][1], timed[-1][2]
            line = main.get("transitLine", {})
            name = (line.get("name") or "").strip()
            number = (main.get("tripShortText") or line.get("nameShort") or "").strip()
            if not name and number:
                name, number = number, ""
            if number and number in name:
                number = ""
            if mode == "bus" and re.search(r"\s(to|-|–)\s", name) and to_city.split(",")[0].lower() not in name.lower():
                name = ""  # the route's end points ("Jaipur to Rupaidiha"), not where this traveller gets off
            agencies = line.get("agencies") or []
            operator = (agencies[0].get("name") if agencies else None) or None
            fs, ls = steps[0]["stopDetails"], steps[-1]["stopDetails"]
            from_stop, to_stop = fs.get("departureStop", {}), ls.get("arrivalStop", {})
            f_ll = from_stop.get("location", {}).get("latLng", {})
            t_ll = to_stop.get("location", {}).get("latLng", {})
            # Transfer between home and the station, estimated from real coordinates (not a flat guess)
            station = f_ll if direction == "outbound" else t_ll
            if station.get("latitude") is not None:
                acc_m, acc_s = estimate_road(home.lat, home.lng, station["latitude"], station["longitude"])
                acc_min, acc_cost = max(5, round(acc_s / 60) + 5), to_currency(F.local_leg_cost(acc_m, "cab", 1), currency)
            else:
                acc_m, acc_min, acc_cost = 0, 30, to_currency(F.local_leg_cost(8000, "cab", 1), currency)
            fare = route.get("travelAdvisory", {}).get("transitFare")
            india = _in_india(home.lat, home.lng)  # still used for Indian Railways seat-class conventions
            total_minutes = max(1.0, (arr - dep).total_seconds() / 60)
            if mode == "train" and main_minutes < 0.5 * total_minutes:
                continue  # the "train" would be less than half the journey: not honest to call it a train trip
            # Indian Railways seat classes only for Indian Railways-type services (not metro / RRTS / suburban)
            regional = any(w in name.lower() for w in ("rrts", "namo bharat", "metro", "local", "suburban", "emu"))
            ir = india and mode == "train" and kind((main, dep, arr)) != "COMMUTER_TRAIN" and not regional
            classes = (["2S", "CC"] if ir else [None]) if mode == "train" else (["AC"] if india else [None])
            for cls in classes:
                if fare and fare.get("currencyCode") == currency and cls in ("2S", "AC", None):
                    pp, state = Decimal(fare.get("units", 0)), "verified_provider"  # a real fare: no conversion
                else:
                    base = F.train_fare(km, cls or "2S") if mode == "train" else F.bus_fare(km, "ac")
                    pp, state = to_currency(base, currency), "estimated"
                out.append(TransportData(
                    direction=direction, mode=mode, name=name, service_class=cls,
                    depart_at=dep.replace(tzinfo=None), arrive_at=arr.replace(tzinfo=None),
                    duration_minutes=int((arr - dep).total_seconds() // 60), cost_per_person=pp,
                    distance_m=route.get("distanceMeters"), from_name=_stop_label(from_stop.get("name"), from_city),
                    to_name=_stop_label(to_stop.get("name"), to_city), from_lat=f_ll.get("latitude"), from_lng=f_ll.get("longitude"),
                    to_lat=t_ll.get("latitude"), to_lng=t_ll.get("longitude"),
                    comfort=4 if cls == "CC" else (2 if mode == "train" else 3), verification_state=state,
                    source="Google Routes (live timetable)" + ("" if state != "estimated" else "; fare estimated"),
                    polyline=(route.get("polyline") or {}).get("encodedPolyline"), access_minutes=acc_min,
                    access_cost=acc_cost, access_distance_m=acc_m,
                    meta={"schedule_state": "verified_provider", "number": number or None, "operator": operator,
                          "changes": max(0, len(steps) - 1), "name_source": "timetable" if name else None}))
        return [stamp(t, SourceKind.LIVE_API) for t in out]  # Google's own transit timetable (ADR 0002)

    def intercity_options(self, origin, dest, depart_day, return_day, window_start, window_end, modes,
                          tz: str = None, currency: str = "INR"):
        zone = ZoneInfo(tz or self.default_tz)  # the trip's own destination timezone, not assumed to be India
        try:
            drive = self.maps.compute_route((origin.lat, origin.lng), (dest.lat, dest.lng), "drive",
                                            window_start.replace(tzinfo=zone))
        except ProviderUnavailable:
            dist, dur = estimate_road(origin.lat, origin.lng, dest.lat, dest.lng)
            drive = RouteResult(dist, dur, None, "estimated", "drive")
        km = drive.distance_m / 1000
        drive_min = max(1, drive.duration_s // 60)
        out: list[TransportData] = []

        def flexible(direction, mode, name, minutes, per_vehicle, comfort, per_person=Decimal("0")):
            start = window_start if direction == "outbound" else window_end - timedelta(minutes=minutes)
            a, b = (origin, dest) if direction == "outbound" else (dest, origin)
            out.append(TransportData(
                direction=direction, mode=mode, name=name, depart_at=start, arrive_at=start + timedelta(minutes=minutes),
                duration_minutes=minutes, cost_per_person=to_currency(per_person, currency), per_vehicle=per_vehicle > 0,
                vehicle_cost=to_currency(per_vehicle, currency), distance_m=drive.distance_m, from_name=a.name, to_name=b.name,
                from_lat=a.lat, from_lng=a.lng, to_lat=b.lat, to_lng=b.lng, comfort=comfort, flexible=True,
                verification_state="estimated", polyline=drive.polyline,
                source=f"Google Routes ({'live' if drive.state != 'estimated' else 'unavailable'}); fare estimated",
                meta={"route_state": drive.state}))

        for direction in ("outbound", "return"):
            if "cab" in modes:
                flexible(direction, "cab", "Outstation cab", drive_min + 10, F.cab_fare(km, 1)[0], 4)
            if "own_car" in modes:
                flexible(direction, "own_car", "Your car", drive_min, F.own_car_cost(km), 4)
            # `flexible()` itself converts per_vehicle/per_person into `currency`; the fare_model call above
            # stays in rupee terms, same as everywhere else in this file

        # Every transit lookup (mode x direction x departure probe) is independent: fetch them all at once rather
        # than up to 12 round trips in a row, then read the answers back in the original order.
        # Probe from an hour before the requested start (Yaitro accepts +/- 60 min when that's all there is).
        probes = [(mode, rail, direction, base + timedelta(minutes=shift))
                  for mode, rail in (("train", True), ("bus", False)) if mode in modes
                  for direction, base in (("outbound", window_start - timedelta(minutes=45)),
                                          ("return", window_end - timedelta(minutes=drive_min + 210)))
                  for shift in (0, 105, 210)]

        def lookup(p):
            _, rail, direction, at = p
            frm, to = (origin, dest) if direction == "outbound" else (dest, origin)
            return self._transit(frm, to, at, rail, zone)
        answers: dict = {}
        if probes:
            with ContextThreadPool(max_workers=min(6, len(probes)), thread_name_prefix="transit") as pool:
                answers = dict(zip(probes, pool.map(lookup, probes)))
        for mode in ("train", "bus"):
            if mode not in modes:
                continue
            found, seen, routes_n = [], set(), 0
            for p in (p for p in probes if p[0] == mode):
                direction, data = p[2], answers[p]
                for route in (data or {}).get("routes", []):
                    routes_n += 1
                    seen |= {st["transitDetails"].get("transitLine", {}).get("vehicle", {}).get("type", "OTHER")
                             for leg in route.get("legs", []) for st in leg.get("steps", [])
                             if st.get("transitDetails")}
                found += self._scheduled(data, direction, mode, km, origin, origin, dest, zone, currency)
            if found:
                out += found
            elif mode == "bus":
                for direction in ("outbound", "return"):
                    flexible(direction, "bus", "", int(drive_min * 1.15) + 20, Decimal("0"),
                             3, F.bus_fare(km, "ac"))
            elif mode == "train":
                log.warning("No train timetable from Google for %s -> %s (%d transit routes returned, vehicle types: "
                            "%s); offering a time window instead of a named train. Yaitro never invents trains.",
                            origin.name, dest.name, routes_n, ", ".join(sorted(seen)) or "none")
                # No source had a real timetable for this route. Rather than drop trains from the plan entirely,
                # offer an honest time window ("timing estimated", no name, no number — see transport_label()) so
                # the traveller knows a train is realistic here and roughly when, and can check the exact one
                # themselves. Never a specific invented train name, number or time.
                if km <= 2000:  # beyond this a train corridor existing at all is no longer a safe assumption
                    train_min = int(km / 55 * 60) + 40  # ~55 km/h average including stops, plus a boarding buffer
                    for direction in ("outbound", "return"):
                        flexible(direction, "train", "", train_min, Decimal("0"), 3, F.train_fare(km, "2S"))

        if "flight" in modes and km > 350:
            from app.providers import google_flights  # real flights when a SerpApi key is set (ADR 0020)
            flights = google_flights.flights(origin, dest, depart_day, return_day, currency)
            why = ("no flight search key is set" if not google_flights.enabled()
                   else "Google Flights didn't answer for this route")  # shown with an estimated flight (ADR 0020)
            if flights:
                ways = {f.direction for f in flights}
                # a direction Google Flights had nothing for keeps the estimated flight, so the trip still plans
                flights += [f for f in airport_flights(origin, dest, window_start, window_end, currency) or []
                            if f.direction not in ways]
            else:
                flights = airport_flights(origin, dest, window_start, window_end, currency)
            for f in flights or []:
                if f.verification_state == "estimated" and f.flexible:
                    f.source = f"{f.source} ({why})"
            if flights is not None:
                out += flights
            else:  # an airport we don't know at one end: city to city, with time for the airports included
                minutes = int(60 + km / 750 * 60)
                for direction in ("outbound", "return"):
                    flexible(direction, "flight", "", minutes + 150, Decimal("0"), 5, F.flight_fare(km))
        # every `flexible()` call above already converts its money into `currency` before it's stored

        for o in out:
            if o.mode == "train" and "source_kind" not in o.meta:
                # Google's no-timetable train is a window, not a timetable (ADR 0002)
                stamp(o, SourceKind.LIVE_API if not o.flexible else SourceKind.STATION_GUIDANCE)
        seen, unique = set(), []
        for o in out:
            key = (o.direction, o.mode, o.name, o.service_class, o.depart_at)
            if key not in seen:
                seen.add(key)
                unique.append(o)
        return unique