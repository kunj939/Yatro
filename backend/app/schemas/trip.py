"""Request schemas for trips. Validation limits keep abuse and nonsense inputs out of the planner."""
from __future__ import annotations

import uuid
from datetime import date, time, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.common import APIModel
from app.services import age_fit
from app.services.interest_tags import INTEREST_CHIPS

TravellerType = Literal["solo", "couple", "partner", "friends", "family"]
Transport = Literal["train", "bus", "metro", "cab", "own_car", "flight", "mixed"]
Food = Literal["veg", "non_veg", "both"]
Style = Literal["premium", "balanced", "budget", "romantic", "adventure", "sightseeing", "food", "relaxed"]
Stay = Literal["premium", "tier1", "tier2", "budget", "oyo", "hostel", "no_stay"]
Pace = Literal["relaxed", "normal", "packed"]
AgeGroup = Literal["kids", "teens", "adults", "seniors"]  # 0-12, 13-17, 18-59, 60+ (services/age_fit.py)

MAX_DAYS = 7


class LocationIn(APIModel):
    name: str = Field(min_length=2, max_length=200)
    place_id: str | None = Field(default=None, max_length=255)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)


class TripCreate(APIModel):
    origin: LocationIn
    destination: LocationIn
    start_date: date
    end_date: date | None = None
    start_time: time = time(7, 0)
    end_time: time = time(22, 0)
    budget: Decimal = Field(gt=0, le=Decimal("9999999999"))  # fits Numeric(12, 2); rupiah, dong and won budgets run to millions
    travellers: int = Field(default=1, ge=1, le=20)
    traveller_type: TravellerType = "solo"
    age_groups: list[AgeGroup] = Field(default_factory=list, max_length=4,
                                       description="Which age groups are in the party. Empty = not asked: no age-based changes.")
    transport_modes: list[Transport] = Field(default_factory=lambda: ["mixed"], min_length=1, max_length=7)
    food: Food = "both"
    trip_styles: list[Style] = Field(default_factory=lambda: ["balanced"], min_length=1, max_length=4)
    stay: Stay = "no_stay"
    pace: Pace = "normal"
    notes: str | None = Field(default=None, max_length=500)
    title: str | None = Field(default=None, max_length=120)
    interests: list[str] | None = Field(default=None, max_length=10,
                                        description="Optional 'favourites' chip ids, e.g. forts_palaces. "
                                                    "Weighted higher when choosing what to visit.")
    include_place_ids: list[str] | None = Field(default=None, max_length=10,
                                                description="Optional: specific real places (provider place "
                                                            "ids) the traveller has already chosen to include.")

    @field_validator("interests")
    @classmethod
    def _interests(cls, v: list[str] | None) -> list[str] | None:
        # Chip ids only (never free text): unknown or malformed values are dropped rather than rejected, so
        # this never blocks trip creation over an optional field.
        return list(dict.fromkeys(c for c in (v or []) if c in INTEREST_CHIPS)) or None

    @field_validator("age_groups")
    @classmethod
    def _ages(cls, v: list[str]) -> list[str]:
        return age_fit.clean(v)  # no repeats, fixed order

    @field_validator("budget")
    @classmethod
    def _budget(cls, v: Decimal) -> Decimal:
        if v < 100:
            raise ValueError("Budget must be at least ₹100.")
        return v.quantize(Decimal("0.01"))

    @model_validator(mode="after")
    def _dates(self) -> TripCreate:
        end_date = self.end_date or self.start_date
        if end_date < self.start_date:
            raise ValueError("The trip can't end before it starts.")
        if (end_date - self.start_date).days + 1 > MAX_DAYS + 1:
            raise ValueError(f"Trips can be at most {MAX_DAYS} days in this version.")
        if end_date == self.start_date and self.end_time <= self.start_time:
            # e.g. start 05:00, back by 01:30 -> the return is on the next morning
            end_date = self.start_date + timedelta(days=1)
        self.end_date = end_date
        if self.start_date < date.today() - timedelta(days=1):
            raise ValueError("Pick a date from today onwards.")
        if len(set(self.transport_modes)) != len(self.transport_modes):
            self.transport_modes = list(dict.fromkeys(self.transport_modes))
        return self


class PreferencesPatch(APIModel):
    """Partial update used by replans and the infeasibility 'apply' buttons."""
    budget: Decimal | None = Field(default=None, gt=0, le=Decimal("9999999999"))  # fits Numeric(12, 2); rupiah, dong and won budgets run to millions
    travellers: int | None = Field(default=None, ge=1, le=20)
    start_date: date | None = None
    end_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    traveller_type: TravellerType | None = None
    age_groups: list[AgeGroup] | None = Field(default=None, max_length=4)  # replaces the current set
    transport_modes: list[Transport] | None = Field(default=None, min_length=1, max_length=7)
    food: Food | None = None
    trip_styles: list[Style] | None = Field(default=None, min_length=1, max_length=4)
    stay: Stay | None = None
    pace: Pace | None = None
    notes: str | None = Field(default=None, max_length=500)
    exclude_place_ids: list[str] | None = Field(default=None, max_length=50)
    include_place_ids: list[str] | None = Field(default=None, max_length=20)
    interests: list[str] | None = Field(default=None, max_length=10)  # chip ids; replaces the current set
    reset_pins: bool = False

    @field_validator("interests")
    @classmethod
    def _interests(cls, v: list[str] | None) -> list[str] | None:
        return list(dict.fromkeys(c for c in (v or []) if c in INTEREST_CHIPS)) if v is not None else None

    @field_validator("age_groups")
    @classmethod
    def _ages(cls, v: list[str] | None) -> list[str] | None:
        return age_fit.clean(v) if v is not None else None


class SwapRequest(APIModel):
    item_id: uuid.UUID
    alternative_id: uuid.UUID


class ReplanRequest(APIModel):
    message: str | None = Field(default=None, max_length=1000)
    changes: PreferencesPatch | None = None
    swap: SwapRequest | None = None
    stream: bool = False

    @model_validator(mode="after")
    def _one(self) -> ReplanRequest:
        if not (self.message or self.changes or self.swap):
            raise ValueError("Tell us what to change: a message, structured changes or a swap.")
        return self


class MemberCreate(APIModel):
    name: str = Field(min_length=1, max_length=80)
    email: str | None = Field(default=None, max_length=255)
    budget: Decimal | None = Field(default=None, ge=0, le=Decimal("9999999999"))  # fits Numeric(12, 2); rupiah, dong and won budgets run to millions
    food_pref: Food | None = None


class MemberUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    budget: Decimal | None = Field(default=None, ge=0, le=Decimal("9999999999"))  # fits Numeric(12, 2); rupiah, dong and won budgets run to millions
    food_pref: Food | None = None


class TripUpdate(APIModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    status: Literal["draft", "ready", "active", "completed"] | None = None