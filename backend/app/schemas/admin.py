"""Requests the admin console sends (ADR 0010)."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import APIModel


class UserPatch(APIModel):
    """Edit an account. Only the fields sent change; the ones that really change are written to the audit trail."""
    is_active: bool | None = None  # false = suspended: cannot sign in or use a token already issued
    name: str | None = Field(default=None, min_length=1, max_length=80)
    free_plans_used: int | None = Field(default=None, ge=0, le=1000)  # 0 gives all their free plans back
    trip_credits: int | None = Field(default=None, ge=0, le=1000)  # one-trip credits, set to this number
    plan: Literal["free", "plus"] | None = None
    plus_days: int | None = Field(default=None, ge=1, le=3650)  # with plan "plus": how long (30 if not given)
    email_verified: bool | None = None
    phone_verified: bool | None = None
    is_admin: bool | None = None  # owners only
    reason: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _something(self):
        if not any(v is not None for k, v in self.model_dump().items() if k != "reason"):
            raise ValueError("Nothing to change.")
        return self


class DeleteUser(APIModel):
    confirm_email: str = Field(min_length=3, max_length=255)
    reason: str | None = Field(default=None, max_length=200)


class DeleteTrip(APIModel):
    reason: str | None = Field(default=None, max_length=200)


class SwitchSet(APIModel):
    on: bool
    reason: str | None = Field(default=None, max_length=200)


class NumberSet(APIModel):
    value: int = Field(ge=-1_000_000, le=1_000_000)
    reason: str | None = Field(default=None, max_length=200)
