"""Helpers for the planner: input building, candidate construction, day frames, catalog persistence."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.catalog import Hotel, Place, Restaurant
from app.models.trip import Trip
from app.providers.types import HotelData, PlaceData, TransportData
from app.services import age_fit
from app.services import destination_profile
from app.services import fare_model as F
from app.services.engine_types import Candidate, DayFrame, PlanInput
from app.services.feasibility import NIGHT_ARRIVAL_BEFORE, Combo, boarding_minutes, exit_minutes
from app.services.intent import interests_from_notes
from app.services.ranking import _quality, score_attraction, score_restaurant
from app.utils import hours as H
from app.utils.geo import estimate_road, haversine_m
from app.utils.money import to_currency

DAY_START = time(8, 30)
DAY_END = time(22, 0)
LATE_RETURN_CUTOFF = time(6, 0)


def plan_input(trip: Trip) -> PlanInput:
    p = trip.preferences
    styles = list(p.trip_styles or []) or ["balanced"]
    wanted = list(dict.fromkeys([*(p.interests or []), *interests_from_notes(p.notes)]))
    # "-temple" in the saved interests means "see fewer temples" (set by a request such as "no temples")
    interests = [i for i in wanted if not i.startswith("-")]
    avoid = {i[1:] for i in wanted if i.startswith("-")} - set(interests)
    start, end = datetime.combine(trip.start_date, trip.start_time), datetime.combine(trip.end_date, trip.end_time)
    stay = p.stay
    if stay == "no_stay" and end.date() > start.date() and end.time() >= LATE_RETURN_CUTOFF:
        # "No stay" was chosen for a day trip; once the trip spans a night (e.g. after "Add a day"), a night in Agra
        # with nowhere to sleep is never what was meant. A night spent on a train still gets no hotel (see
        # Combo.nights), so this only ever adds a bed where one is needed.
        stay = "budget"
    ages = age_fit.clean(getattr(p, "age_groups", None))  # a trip made before ages were asked has none
    return PlanInput(
        origin_text=trip.origin_name, destination_text=trip.destination_name,
        origin_place_id=trip.origin_place_id, destination_place_id=trip.destination_place_id,
        start=start, end=end,
        budget=Decimal(trip.budget), travellers=trip.travellers, traveller_type=p.traveller_type, age_groups=ages,
        transport_modes=list(p.transport_modes or []), food=p.food, styles=styles, stay=stay,
        pace=age_fit.effective_pace(p.pace, ages),
        interests=interests, avoid_tags=avoid, notes=p.notes, excluded=set(p.excluded_place_ids or []),
        pinned=list(p.pinned_place_ids or []), pinned_transport=dict(p.pinned_transport or {}),
        member_budgets=[(m.name, Decimal(m.budget)) for m in trip.members if m.budget is not None and not m.is_owner],
        member_food=[m.food_pref for m in trip.members if m.food_pref],
        timezone=trip.timezone or "Asia/Kolkata", currency=trip.currency or "INR")


def return_day(inp: PlanInput) -> date:
    """A return 'by 01:30' the next morning still means travelling back on the previous evening."""
    d = inp.end.date()
    if inp.end.time() < LATE_RETURN_CUTOFF and d > inp.start.date():
        d -= timedelta(days=1)
    return d


def provider_state(place: PlaceData) -> str:
    return "demo" if place.provider == "demo" else "verified_provider"


_LOCAL_AREA = re.compile(r"\b(block|sector|nagar|colony|phase|ward|society|enclave|vihar|puram|pocket|layout|extension|scheme)\b", re.I)
_SIGHT_TYPES = {"national_park", "tourist_attraction", "historical_landmark", "amusement_park", "zoo", "botanical_garden"}


def local_park(p: PlaceData) -> bool:
    """A neighbourhood park ("C Block Park", "Deendayal Nagar Park", "rajesh pilot park"): somewhere locals walk, not a
    sight to travel for. A park that is a known attraction with plenty of visitors (Lodhi Garden) is not one."""
    types = set(p.types or [])
    if not ({"park", "playground", "dog_park"} & types or " park" in f" {p.name.lower()}"):
        return False
    n = p.rating_count or 0
    if _SIGHT_TYPES & types and n >= 1000:
        return False
    return bool(_LOCAL_AREA.search(p.name)) or n < 1000


def weak_place(p: PlaceData, city: str | None) -> bool:
    """Places that are rarely real sights: barely reviewed, a neighbourhood park, or named after the city itself
    ("JAIPUR MARKET", "Rishikesh - The City Of Love") without many visitors."""
    n = p.rating_count
    if n is not None and n < 50:
        return True
    if local_park(p):
        return True
    return bool(city) and city.lower() in p.name.lower() and (n or 0) < 1500


def attraction_candidates(places: list[PlaceData], inp: PlanInput, activity_allowance: Decimal,
                          city: str | None = None) -> list[Candidate]:
    inp.dest_kinds = destination_profile.profile(places)  # beach / mountain / heritage / city: lifts matching sights
    strong = [p for p in places if p.provider_place_id in inp.pinned or not weak_place(p, city)]
    if len(strong) >= 6:
        places = strong  # small towns keep the weaker ones rather than an empty plan
    out = []
    for p in places:
        key = p.provider_place_id
        if key in inp.excluded and key not in inp.pinned:
            continue
        if not age_fit.allowed(inp.age_groups, p.tags, p.types, pinned=key in inp.pinned):
            continue  # a night club is not on the list for a party with children in it
        dur_default, fee_default = F.attraction_defaults(p.tags)
        c = Candidate(key=key, place=p, kind="attraction",
                      unit_cost=p.entry_fee if p.entry_fee is not None else to_currency(fee_default, inp.currency),
                      cost_state=p.entry_fee_state if p.entry_fee is not None else "estimated",
                      duration=p.recommended_minutes or dur_default, pinned=key in inp.pinned)
        c.hours_state = provider_state(p) if p.opening_hours else "unavailable"
        score_attraction(c, inp, activity_allowance)
        out.append(c)
    if inp.avoid_tags:
        # "no temples": leave them out when enough else is left to fill the days; otherwise they stay, ranked last
        kept = [c for c in out if c.pinned or not (set(c.place.tags) & inp.avoid_tags)]
        if len(kept) >= 6:
            out = kept
    return sorted(out, key=lambda c: -c.score)


def restaurant_candidates(places: list[PlaceData], inp: PlanInput, meal_pp: Decimal) -> list[Candidate]:
    out = []
    for p in places:
        key = p.provider_place_id
        if (key in inp.excluded and key not in inp.pinned) or not p.restaurant:
            continue
        if not age_fit.allowed(inp.age_groups, (), p.types, pinned=key in inp.pinned):
            continue  # tags are not used for restaurants: only a place that really is a bar or club (by provider type) is kept out
        cost = p.restaurant.avg_cost_per_person or to_currency(Decimal(F.DEFAULT_MEAL_COST), inp.currency)
        c = Candidate(key=key, place=p, kind="restaurant", unit_cost=cost, cost_state=p.restaurant.cost_state,
                      duration=60, pinned=key in inp.pinned)
        c.hours_state = provider_state(p) if p.opening_hours else "unavailable"
        score_restaurant(c, inp, meal_pp)
        out.append(c)
    return sorted(out, key=lambda c: -c.score)


def hotel_candidates(places: list[PlaceData], inp: PlanInput, centre: tuple[float, float]) -> list[Candidate]:
    out = []
    for p in places:
        key = p.provider_place_id
        if key in inp.excluded and key not in inp.pinned:
            continue
        h = p.hotel or HotelData(tier=inp.stay)
        rate = h.nightly_rate if h.nightly_rate is not None else to_currency(F.stay_nightly(inp.stay), inp.currency)
        c = Candidate(key=key, place=p, kind="hotel", unit_cost=rate, cost_state=h.rate_state, duration=0,
                      pinned=key in inp.pinned)
        dist_km = haversine_m(p.lat, p.lng, *centre) / 1000
        # every km from the sights costs two cab rides a day, so distance matters as much as rating
        c.score = round(_quality(p.rating, p.rating_count) - 0.15 * dist_km + (10 if c.pinned else 0), 4)
        c.hours_state = "n/a"
        c.reasons = [f"{dist_km:.1f} km from the main sights"]
        out.append((dist_km, c))
    near = [c for d, c in out if d <= 6 or c.pinned]
    pool = near if near else [c for _, c in out]  # far hotels only when nothing closer exists
    return sorted(pool, key=lambda c: -c.score)


def estimated_hotel(dest: PlaceData, tier: str, currency: str = "INR") -> Candidate:
    """Fallback when no hotel provider data is available: an explicitly-estimated placeholder."""
    rate = to_currency(F.stay_nightly(tier), currency)
    p = PlaceData(provider="estimate", provider_place_id=f"estimate:hotel:{tier}:{dest.provider_place_id}",
                  name=f"{dest.name} hotel ({tier.replace('tier', 'Tier ')})", category="hotel", lat=dest.lat,
                  lng=dest.lng, city=dest.name, hotel=HotelData(tier=tier, stars=F.STAY_STARS.get(tier),
                                                                nightly_rate=rate))
    c = Candidate(key=p.provider_place_id, place=p, kind="hotel", unit_cost=rate, cost_state="estimated",
                  duration=0)
    c.hours_state = "n/a"
    return c


def sights_hub(cands: list[Candidate], fallback: tuple[float, float]) -> tuple[float, float]:
    """Where to look for a hotel: the sight with the least total distance to the others (a medoid). With sights spread
    over a region (North and South Goa) their average falls between the clusters, far from all of them; the medoid
    sits in the cluster where most of them are."""
    if not cands:
        return fallback
    pts = [c.place for c in cands]
    best = min(pts, key=lambda p: sum(haversine_m(p.lat, p.lng, q.lat, q.lng) for q in pts))
    return best.lat, best.lng


def centroid(cands: list[Candidate], fallback: tuple[float, float]) -> tuple[float, float]:
    if not cands:
        return fallback
    return (sum(c.place.lat for c in cands) / len(cands), sum(c.place.lng for c in cands) / len(cands))




def build_frames(combo: Combo, hotel: Candidate | None, dest: PlaceData) -> list[DayFrame]:
    start, end = combo.dest_start, combo.dest_end
    night = combo.night_arrival and end > datetime.combine(start.date(), DAY_START)
    if night:
        # A train that pulls in at 01:55 is followed by rest, not a waterfall at 02:38 and a fort at 05:07 (places
        # listed "open 24 hours" would otherwise be scheduled in the dark). Sightseeing starts with the morning.
        start = datetime.combine(start.date(), DAY_START)
    days = (end.date() - start.date()).days + 1
    base = hotel.point if hotel else (dest.lat, dest.lng)
    base_name = hotel.place.name if hotel else f"{dest.name} centre"
    if days <= 1:
        return [DayFrame(0, start.date(), start, end, combo.start_point, combo.start_name, combo.end_point,
                         combo.end_name)]
    frames = []
    for i in range(days):
        d = start.date() + timedelta(days=i)
        first, last = i == 0, i == days - 1
        f_start = start if first else datetime.combine(d, DAY_START)
        f_end = end if last else max(datetime.combine(d, DAY_END), f_start + timedelta(minutes=30))
        if last and f_end < f_start + timedelta(minutes=30):
            # An early departure on the last day: the day starts when you have to leave the hotel, which is the
            # ride to the station before the train — not the train's own time (a 07:45 train from a hotel an hour
            # away means checking out by 06:30). The slower of cab and metro is allowed for, plus checking out.
            dist, dur = estimate_road(*base, *combo.end_point)
            ride = max(F.local_leg_minutes(dur, dist, "cab"), F.local_leg_minutes(dur, dist, "transit")) if dist else 0
            f_start = min(f_start, f_end - timedelta(minutes=ride + 10))
        # after a night arrival the traveller has slept at the hotel: the first day starts there, not at the station
        from_station = first and not (night and hotel)
        frames.append(DayFrame(i, d, f_start, f_end, combo.start_point if from_station else base,
                               combo.start_name if from_station else base_name, combo.end_point if last else base,
                               combo.end_name if last else base_name))
    return frames


def night_transfer(combo: Combo, hotel: Candidate, travellers: int) -> tuple[int, int, Decimal]:
    """(metres, minutes, cost) of the cab from the station to the hotel straight after a night arrival."""
    dist, dur = estimate_road(*combo.start_point, *hotel.point)
    return dist, F.local_leg_minutes(dur, dist, "cab"), F.local_leg_cost(dist, "cab", travellers)


STATION_TRANSFER_MIN_M = 8000  # closer than this, the ride to the station isn't worth a separate choice
METRO_WALK_MINUTES = 10  # walking from home to the metro platform


def choose_station_access(options: list[TransportData], inp: PlanInput, origin: PlaceData) -> None:
    """How to get between home and the station: by metro where a metro links the two (Gurugram to New Delhi on
    the Yellow Line), otherwise by cab.

    Metro is used when the traveller didn't pick cabs, travels on a budget, or left the choice to us ("mixed")
    and it costs at most half an hour more than a cab. Updates each option in place: its transfer time and cost,
    and, for an estimated (flexible) journey, its timing, so leaving home still lines up with the requested start.
    """
    from app.providers.rail_stations import metro_area, metro_runs_at  # providers -> services: one-way at load time
    home_area = metro_area(origin.lat, origin.lng)
    if not home_area:
        return
    modes = set(inp.transport_modes or [])
    budget = "budget" in (inp.styles or [])
    for t in options:
        if t.mode not in ("train", "bus", "flight") or (t.access_distance_m or 0) < STATION_TRANSFER_MIN_M:
            continue
        lat, lng = (t.from_lat, t.from_lng) if t.direction == "outbound" else (t.to_lat, t.to_lng)
        if lat is None or lng is None or metro_area(lat, lng) != home_area:
            continue
        metro_min = F.metro_minutes(t.access_distance_m)
        extra = metro_min - t.access_minutes
        if not ("cab" not in modes or budget or ("mixed" in modes and extra <= 30)):
            continue
        # The metro has to be running when you board it: not at 04:54 (first train about 05:15), and a train that
        # reaches the station at 22:50 can't be followed by a metro home. Outside those hours, the cab stays.
        if t.direction == "outbound":
            board = t.depart_at - timedelta(minutes=boarding_minutes(t)) - timedelta(minutes=metro_min)
            board += timedelta(minutes=METRO_WALK_MINUTES)  # the walk to the platform comes first
        else:
            board = t.arrive_at + timedelta(minutes=exit_minutes(t))
        if not metro_runs_at(home_area, board):
            continue
        if t.flexible:  # estimated timing: keep "leave home" / "home again" where they were
            shift = timedelta(minutes=extra if t.direction == "outbound" else -extra)
            t.depart_at, t.arrive_at = t.depart_at + shift, t.arrive_at + shift
        t.access_minutes = metro_min
        t.access_cost = to_currency(F.metro_fare(t.access_distance_m), inp.currency)  # per person, see group_cost
        t.meta["access_mode"] = "transit"


def estimated_transport(origin: PlaceData, dest: PlaceData, inp: PlanInput, modes: list[str],
                        ret_day: date) -> list[TransportData]:
    """Fallback when the transport provider is unavailable: flexible road options from the fare model."""
    dist, dur = estimate_road(origin.lat, origin.lng, dest.lat, dest.lng)
    km, minutes = dist / 1000, max(30, dur // 60)
    out = []
    for direction in ("outbound", "return"):
        a, b = (origin, dest) if direction == "outbound" else (dest, origin)
        for mode in modes:
            if mode == "cab":
                pv, pp, mins, comfort, name = F.cab_fare(km, 1)[0], Decimal(0), minutes + 10, 4, "Outstation cab (estimate)"
            elif mode == "own_car":
                pv, pp, mins, comfort, name = F.own_car_cost(km), Decimal(0), minutes, 4, "Your car (estimate)"
            elif mode == "bus":
                pv, pp, mins, comfort, name = Decimal(0), F.bus_fare(km), int(minutes * 1.15) + 20, 3, ""
            elif mode == "train":
                pv, pp, mins, comfort, name = Decimal(0), F.train_fare(km, "2S"), int(minutes * 1.05) + 30, 2, ""
            else:
                continue
            dep = inp.start + timedelta(minutes=30) if direction == "outbound" else \
                datetime.combine(ret_day, inp.end.time()) - timedelta(minutes=mins) if ret_day == inp.end.date() \
                else inp.end - timedelta(minutes=mins)
            out.append(TransportData(direction=direction, mode=mode, name=name, depart_at=dep,
                                     arrive_at=dep + timedelta(minutes=mins), duration_minutes=mins,
                                     cost_per_person=pp, per_vehicle=pv > 0, vehicle_cost=pv, distance_m=dist,
                                     from_name=a.name, to_name=b.name, from_lat=a.lat, from_lng=a.lng, to_lat=b.lat,
                                     to_lng=b.lng, comfort=comfort, flexible=True, verification_state="estimated",
                                     source="Yaitro fare model (provider unavailable)"))
    return out


# --------------------------------------------------------------------------------------------------------
# Catalog persistence: places / restaurants / hotels (provider data cached for reuse and for the UI)
# --------------------------------------------------------------------------------------------------------

def upsert_places(db: Session, cands: list[Candidate]) -> None:
    by_provider: dict[str, list[Candidate]] = {}
    for c in cands:
        by_provider.setdefault(c.place.provider, []).append(c)
    now = utcnow()
    rows: dict[tuple[str, str], Place] = {}
    for provider, group in by_provider.items():
        keys = [c.key for c in group]
        existing = {p.provider_place_id: p for p in db.scalars(
            select(Place).where(Place.provider == provider, Place.provider_place_id.in_(keys)))}
        for c in group:
            p = c.place
            row = existing.get(c.key)
            if row is None:
                row = Place(provider=provider, provider_place_id=c.key, name=p.name, category=p.category,
                            lat=p.lat, lng=p.lng)
                db.add(row)
                existing[c.key] = row
            row.name, row.category, row.lat, row.lng = p.name[:255], p.category, p.lat, p.lng
            row.city, row.address, row.rating, row.rating_count = p.city, (p.address or "")[:500] or None, p.rating, p.rating_count
            row.price_level, row.types, row.tags, row.opening_hours = p.price_level, p.types, p.tags, p.opening_hours
            row.website, row.business_status = (p.website or "")[:500] or None, p.business_status
            row.entry_fee, row.entry_fee_state = p.entry_fee, p.entry_fee_state
            row.recommended_minutes, row.description, row.meta, row.fetched_at = p.recommended_minutes, p.description, p.meta, now
            if p.restaurant:
                r = row.restaurant or Restaurant()
                r.veg_only, r.serves_veg, r.serves_non_veg = p.restaurant.veg_only, p.restaurant.serves_veg, p.restaurant.serves_non_veg
                r.cuisines, r.meal_types = p.restaurant.cuisines, p.restaurant.meal_types
                r.avg_cost_per_person, r.cost_state = p.restaurant.avg_cost_per_person, p.restaurant.cost_state
                row.restaurant = r
            if p.hotel:
                h = row.hotel or Hotel(tier=p.hotel.tier)
                h.tier, h.stars, h.nightly_rate = p.hotel.tier, p.hotel.stars, p.hotel.nightly_rate
                h.rate_state, h.amenities = p.hotel.rate_state, p.hotel.amenities
                row.hotel = h
            rows[(provider, c.key)] = row
    db.flush()
    for c in cands:
        c.db_id = rows[(c.place.provider, c.key)].id


def hours_display(c: Candidate, day: date, at: datetime) -> tuple[str | None, str | None]:
    return H.span_for_display(c.place.opening_hours, day, at)