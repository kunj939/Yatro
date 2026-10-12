"""Deterministic cost and duration estimates.

These are used ONLY when a provider does not return an actual price. Every value produced here is
labelled `estimated` in the UI so users know to confirm it. Tune the constants per market.
"""
from __future__ import annotations

import math
from decimal import Decimal

from app.utils.money import round_rupees

# --- Intercity (per person unless noted) -------------------------------------------
TRAIN_CLASSES = {  # class -> (₹ per km, base ₹, comfort 1-5)
    "2S": (Decimal("0.62"), Decimal("20"), 2),
    "SL": (Decimal("0.55"), Decimal("30"), 2),
    "CC": (Decimal("2.10"), Decimal("60"), 4),
    "3A": (Decimal("1.90"), Decimal("60"), 4),
    "3E": (Decimal("1.75"), Decimal("60"), 4),
    "2A": (Decimal("2.70"), Decimal("60"), 5),
    "EC": (Decimal("4.40"), Decimal("60"), 5),
    "1A": (Decimal("4.50"), Decimal("60"), 5),
}
BUS_PER_KM = {"ordinary": Decimal("1.30"), "ac": Decimal("2.40")}
CAB_PER_KM = Decimal("13")  # sedan, per vehicle, one way incl. driver
CAB_CAPACITY = 4
SUV_PER_KM = Decimal("17")
SUV_CAPACITY = 6
OWN_CAR_FUEL_PER_KM = Decimal("7.5")
TOLL_PER_KM = Decimal("1.8")
FLIGHT_BASE = Decimal("2600")
FLIGHT_PER_KM = Decimal("2.8")

# --- Local (per vehicle) ------------------------------------------------------------
LOCAL_CAB_BASE = Decimal("60")
LOCAL_CAB_PER_KM = Decimal("17")
LOCAL_CAB_MIN = Decimal("90")
LOCAL_TRANSIT_PER_PERSON = Decimal("25")
WALK_MAX_M = 900

# --- Stay (per room / bed per night) --------------------------------------------------
STAY_NIGHTLY = {"premium": 11000, "tier1": 6000, "tier2": 2800, "budget": 1600, "oyo": 1200, "hostel": 650}
STAY_STARS = {"premium": 5, "tier1": 4, "tier2": 3, "budget": 2, "oyo": 2, "hostel": None}

# --- Food (per person per meal) -------------------------------------------------------
PRICE_LEVEL_COST = {0: 0, 1: 250, 2: 550, 3: 1100, 4: 2200}
DEFAULT_MEAL_COST = 450

# --- Attractions ----------------------------------------------------------------------
TAG_DURATION = {"fort": 120, "palace": 90, "museum": 75, "temple": 40, "garden": 45, "park": 60, "market": 60,
                "viewpoint": 45, "lake": 30, "landmark": 45, "zoo": 120, "adventure": 120, "gallery": 60,
                "beach": 90, "mountain": 90, "waterfall": 60, "national_park": 180}  # Keoladeo deserves half a day
TAG_FEE = {"fort": 100, "palace": 200, "museum": 50, "temple": 0, "garden": 30, "park": 20, "market": 0,
           "viewpoint": 0, "lake": 0, "landmark": 30, "zoo": 150, "adventure": 500, "gallery": 50}


def train_fare(distance_km: float, cls: str) -> Decimal:
    per_km, base, _ = TRAIN_CLASSES[cls]
    return round_rupees(base + per_km * Decimal(str(distance_km)))


def bus_fare(distance_km: float, kind: str = "ac") -> Decimal:
    return round_rupees(BUS_PER_KM[kind] * Decimal(str(distance_km)))


def cab_fare(distance_km: float, travellers: int) -> tuple[Decimal, int]:
    """Return (cost per vehicle, vehicles) for an outstation cab one way."""
    if travellers > CAB_CAPACITY:
        vehicles = math.ceil(travellers / SUV_CAPACITY)
        return round_rupees(SUV_PER_KM * Decimal(str(distance_km)) + TOLL_PER_KM * Decimal(str(distance_km))), vehicles
    return round_rupees((CAB_PER_KM + TOLL_PER_KM) * Decimal(str(distance_km))), 1


def own_car_cost(distance_km: float) -> Decimal:
    return round_rupees((OWN_CAR_FUEL_PER_KM + TOLL_PER_KM) * Decimal(str(distance_km)))


def flight_fare(distance_km: float) -> Decimal:
    return round_rupees(FLIGHT_BASE + FLIGHT_PER_KM * Decimal(str(distance_km)))


def vehicles_for(travellers: int, capacity: int = CAB_CAPACITY) -> int:
    return max(1, math.ceil(travellers / capacity))


def local_leg_cost(distance_m: int, mode: str, travellers: int) -> Decimal:
    if mode == "walk" or distance_m <= 0:
        return Decimal("0")
    km = Decimal(str(distance_m / 1000))
    if mode == "transit":
        return round_rupees(LOCAL_TRANSIT_PER_PERSON * travellers)
    if mode == "own_car":
        return round_rupees(OWN_CAR_FUEL_PER_KM * km + 20)  # + parking
    fare = max(LOCAL_CAB_MIN, LOCAL_CAB_BASE + LOCAL_CAB_PER_KM * km)
    return round_rupees(fare * vehicles_for(travellers))


def local_leg_minutes(drive_seconds: int, distance_m: int, mode: str) -> int:
    if mode == "walk":
        return max(1, round(distance_m / 1000 / 4.5 * 60))
    minutes = drive_seconds / 60
    if mode == "transit":
        minutes = minutes * 1.4 + 8
    elif mode == "cab":
        minutes += 4  # pickup wait
    return max(1, round(minutes))


METRO_KMH = 36  # average on a city metro including station stops
METRO_LINE_FACTOR = 1.15  # metro lines are straighter than roads: line length vs straight-line distance
METRO_WAIT_AND_WALK = 20  # minutes: walk to the station, wait for a train, walk out at the other end


def metro_minutes(road_m: int) -> int:
    """Door-to-door minutes by metro for a trip whose ROAD estimate is `road_m` (Gurugram to New Delhi: 37 km by
    road estimate, about 28 km straight, about 70 minutes by the Yellow Line). Derived from the straight-line
    distance, not from the road time, which in traffic is much slower than a metro."""
    from app.utils.geo import ROAD_FACTOR
    line_km = road_m / 1000 / ROAD_FACTOR * METRO_LINE_FACTOR
    return round(line_km / METRO_KMH * 60) + METRO_WAIT_AND_WALK


def metro_fare(distance_m: int) -> Decimal:
    """Per person, one way, on a city metro / suburban line (Delhi Metro's slabs top out around ₹64)."""
    return round_rupees(min(Decimal("70"), Decimal("10") + Decimal("1.6") * Decimal(str(distance_m / 1000))))


def stay_nightly(tier: str) -> Decimal:
    return Decimal(STAY_NIGHTLY.get(tier, 2800))


def stay_units(tier: str, travellers: int) -> int:
    return travellers if tier == "hostel" else max(1, math.ceil(travellers / 2))


def attraction_defaults(tags: list[str]) -> tuple[int, Decimal]:
    duration = max((TAG_DURATION[t] for t in tags if t in TAG_DURATION), default=60)
    fee = max((TAG_FEE[t] for t in tags if t in TAG_FEE), default=0)
    return duration, Decimal(fee)