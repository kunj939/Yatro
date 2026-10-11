"""Provider-neutral data transfer objects. Business logic only ever sees these."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

# Verification states shown to the user for every factual value.
VerificationState = Literal["verified_official", "verified_provider", "estimated", "uncertain", "unavailable", "demo"]


@dataclass
class RestaurantData:
    veg_only: bool = False
    serves_veg: bool = True
    serves_non_veg: bool = True
    cuisines: list[str] = field(default_factory=list)
    meal_types: list[str] = field(default_factory=lambda: ["lunch", "dinner"])
    avg_cost_per_person: Decimal | None = None
    cost_state: str = "estimated"


@dataclass
class HotelData:
    tier: str = "tier2"
    stars: int | None = None
    nightly_rate: Decimal | None = None
    rate_state: str = "estimated"
    amenities: list[str] = field(default_factory=list)


@dataclass
class PlaceData:
    provider: str
    provider_place_id: str
    name: str
    category: str  # city | attraction | restaurant | hotel | station
    lat: float
    lng: float
    address: str | None = None
    city: str | None = None
    rating: float | None = None
    rating_count: int | None = None
    price_level: int | None = None
    types: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    opening_hours: dict | None = None
    website: str | None = None
    business_status: str | None = None
    entry_fee: Decimal | None = None
    entry_fee_state: str = "estimated"
    recommended_minutes: int | None = None
    description: str | None = None
    restaurant: RestaurantData | None = None
    hotel: HotelData | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Suggestion:
    place_id: str
    label: str
    secondary: str | None = None
    lat: float | None = None
    lng: float | None = None


@dataclass
class RouteResult:
    distance_m: int
    duration_s: int
    polyline: str | None
    state: VerificationState
    mode: str


@dataclass
class MatrixResult:
    # (origin_index, destination_index) -> (distance_m, duration_s)
    cells: dict[tuple[int, int], tuple[int, int]]
    state: VerificationState


@dataclass
class TransportData:
    direction: Literal["outbound", "return"]
    mode: str  # train | bus | cab | own_car | flight
    name: str
    depart_at: datetime
    arrive_at: datetime
    duration_minutes: int
    cost_per_person: Decimal
    per_vehicle: bool = False  # cab / own car costs are per vehicle, not per person
    vehicle_cost: Decimal = Decimal("0")
    vehicle_capacity: int = 4
    service_class: str | None = None
    distance_m: int | None = None
    from_name: str | None = None
    to_name: str | None = None
    from_lat: float | None = None
    from_lng: float | None = None
    to_lat: float | None = None
    to_lng: float | None = None
    comfort: int = 3
    flexible: bool = False  # cab/own car/unscheduled estimates: departure time chosen by the planner
    verification_state: VerificationState = "estimated"
    source: str | None = None
    polyline: str | None = None
    access_minutes: int = 0  # origin -> station (outbound) or station -> origin (return)
    access_cost: Decimal = Decimal("0")  # per vehicle
    access_distance_m: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass
class WeatherData:
    summary: str
    temp_max_c: float | None
    temp_min_c: float | None
    precipitation_probability: int | None
    state: VerificationState
    source: str


@dataclass
class LLMMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass
class LLMResult:
    text: str
    data: dict | None
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None
    latency_ms: int = 0
