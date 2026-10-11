from __future__ import annotations

import re
import uuid
from typing import Literal

from pydantic import EmailStr, Field, ValidationInfo, field_validator

from app.schemas.common import APIModel
from app.services.verification_codes import normalize_phone


class SendCodeRequest(APIModel):
    channel: Literal["email", "phone"]
    target: str = Field(min_length=3, max_length=255)  # the email address, or the mobile number


class SendCodeResponse(APIModel):
    channel: str
    sent_to: str  # masked: k•••••@outlook.com, +91 •••••• 3210
    expires_in: int  # seconds
    resend_in: int  # seconds before another code can be asked for
    dev_code: str | None = None  # only without an email/SMS provider, outside production


class RegisterRequest(APIModel):
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    password_confirm: str | None = Field(default=None, max_length=128)
    phone: str = Field(min_length=6, max_length=24)
    email_code: str = Field(min_length=4, max_length=10)
    phone_code: str | None = Field(default=None, min_length=4, max_length=10)  # only used when REQUIRE_PHONE_CODE is on

    @field_validator("password")
    @classmethod
    def _strength(cls, v: str) -> str:
        if v.isdigit() or v.isalpha():
            raise ValueError("Use at least one letter and one number or symbol.")
        return v

    @field_validator("password_confirm")
    @classmethod
    def _same(cls, v: str | None, info: ValidationInfo) -> str | None:
        if v is not None and "password" in info.data and v != info.data["password"]:
            raise ValueError("The two passwords don't match.")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalize_phone(v)


class LoginRequest(APIModel):
    email: str = Field(min_length=3, max_length=255)  # the email address or the mobile number
    password: str = Field(min_length=1, max_length=128)


UPI_ID = re.compile(r"^[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z]{2,64}$")


class UpiUpdate(APIModel):
    """The person's OWN UPI ID, or null to remove it."""
    upi_id: str | None = Field(default=None, max_length=320)

    @field_validator("upi_id")
    @classmethod
    def _valid(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        if v and not UPI_ID.match(v):
            raise ValueError("That doesn't look like a UPI ID (like name@bank).")
        return v or None


class NotificationPrefs(APIModel):
    email_plans: bool


class UserOut(APIModel):
    id: uuid.UUID
    name: str
    email: str
    phone: str | None = None
    email_verified: bool = False
    phone_verified: bool = False
    upi_id: str | None = None
    free_plans_used: int
    is_admin: bool = False  # only ever told to the person themself: shows the Admin link in their menu
    email_plans: bool = True  # email me my plan when a new one is ready


class QuotaOut(APIModel):
    plan: str
    status: str
    free_plans_total: int
    free_plans_used: int
    free_plans_left: int
    trip_credits: int
    can_plan: bool
    current_period_end: str | None = None
    prices: dict
    payments_mode: str


class AuthResponse(APIModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
    subscription: QuotaOut


class MeResponse(APIModel):
    user: UserOut
    subscription: QuotaOut
