from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import current_user, is_owner
from app.core import killswitch
from app.core.config import get_settings
from app.core.errors import AppError, Unauthorized
from app.core.middleware import client_ip, limit
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    MeResponse,
    NotificationPrefs,
    RegisterRequest,
    SendCodeRequest,
    SendCodeResponse,
    UpiUpdate,
    UserOut,
)
from app.services import quota_service, referral_service
from app.services import verification_codes as V

router = APIRouter(prefix="/auth", tags=["auth"])
_auth_limit = limit("auth", get_settings().rate_limit_auth_per_minute)
# Constant-time-ish login: verify against a dummy hash when the user doesn't exist.
_DUMMY_HASH = hash_password("not-a-real-password-1")

EMAIL_TAKEN = "An account with this email already exists. Sign in instead."
PHONE_TAKEN = "This mobile number is already on an account. Sign in instead."
SIGNUPS_PAUSED = "We're not taking new sign-ups for a little while. Please try again later."


def _signups_open() -> None:
    """The admin console's "new sign-ups" switch (ADR 0010). Sign-in is unaffected; this is a calm 403, never a 5xx."""
    if killswitch.killed("signups"):
        raise AppError(SIGNUPS_PAUSED, code="signups_paused", status_code=403)


def _user_out(user: User) -> UserOut:
    out = UserOut.model_validate(user)
    out.is_admin = is_owner(user) or bool(user.is_admin)  # owners (ADMIN_EMAILS) and admins made in the console
    return out


def _auth_response(db: Session, user: User) -> AuthResponse:
    token, expires = create_access_token(user.id)
    return AuthResponse(access_token=token, expires_in=expires, user=_user_out(user),
                        subscription=quota_service.quota_status(db, user))


def _email_taken(db: Session, email: str) -> bool:
    return db.scalar(select(User.id).where(func.lower(User.email) == email.lower())) is not None


def _phone_taken(db: Session, phone: str) -> bool:
    return db.scalar(select(User.id).where(User.phone == phone)) is not None


def _field_error(field: str, message: str, code: str, status: int = 422) -> AppError:
    return AppError(message, code=code, status_code=status, details=[{"field": field, "message": message}])


@router.post("/send-code", response_model=SendCodeResponse, dependencies=[Depends(_auth_limit)])
def send_code(body: SendCodeRequest, request: Request, db: Session = Depends(get_db)):
    """Step one of signing up: a six-digit code to the email address, or by text to the mobile number.

    The answer is the same whether or not the address or number already has an account: nothing is looked up here, so nothing can
    leak. (That is checked when the account is created, and only for someone who holds both codes.) Indian mobile numbers only; the
    limits are in verification_codes.issue and the text budget in send_sms_code."""
    _signups_open()  # a code is only ever asked for to create an account; sign-in uses the password
    if body.channel == "phone" and not get_settings().require_phone_code:
        # Sign-up proves the email only; the mobile is collected (unique) for WhatsApp sharing later. No texts are sent unless REQUIRE_PHONE_CODE is on.
        raise AppError("Mobile numbers aren't verified by code right now.", code="phone_code_off", status_code=400)
    if body.channel == "email":
        target = body.target.strip().lower()
        if "@" not in target or "." not in target.rpartition("@")[2]:
            raise _field_error("email", "Enter a valid email address.", "invalid_email")
    else:
        try:
            target = V.normalize_phone(body.target)
        except ValueError as exc:
            raise _field_error("phone", str(exc), "invalid_phone") from exc
    code = V.issue(db, body.channel, target, ip=client_ip(request))
    sent = V.send_email_code(target, code) if body.channel == "email" else V.send_sms_code(target, code)
    db.commit()  # only once it has actually gone out (a failed send raises before this, leaving no live code)
    s = get_settings()
    return SendCodeResponse(channel=body.channel, sent_to=V.mask(body.channel, target), expires_in=s.otp_ttl_minutes * 60,
                            resend_in=s.otp_resend_seconds, dev_code=code if sent.console else None)


@router.post("/register", response_model=AuthResponse, status_code=201, dependencies=[Depends(_auth_limit)])
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    """Step two: the account is created once the EMAIL code is right, so every account has a working email address. The mobile number is
    stored (normalised, +91 only) but not code-checked unless REQUIRE_PHONE_CODE is on; it can belong to one account only."""
    _signups_open()
    email = body.email.lower()
    # The codes are checked FIRST, so "this email/number already has an account" is only ever said to someone who holds a valid code
    # for it (send-code never says it: it is the same answer for every address and number).
    email_row, email_err = V.check(db, "email", email, body.email_code)
    need_phone = get_settings().require_phone_code
    phone_row, phone_err = V.check(db, "phone", body.phone, body.phone_code or "") if need_phone else (None, None)
    if email_err or phone_err:
        db.commit()  # the wrong try counts, even though the request is refused
        details = [{"field": f, "message": m} for f, m in (("email_code", email_err), ("phone_code", phone_err)) if m]
        raise AppError(details[0]["message"] if len(details) == 1 else "Both codes need another look.",
                       code="invalid_code", status_code=400, details=details)
    if _email_taken(db, email):
        raise _field_error("email", EMAIL_TAKEN, "email_taken", 409)
    if _phone_taken(db, body.phone):
        raise _field_error("phone", PHONE_TAKEN, "phone_taken", 409)
    now = datetime.now(UTC)
    email_row.consumed_at = now
    if phone_row is not None:
        phone_row.consumed_at = now
    user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password), phone=body.phone,
                email_verified_at=now, phone_verified_at=now if need_phone else None)
    db.add(user)
    try:
        db.flush()
    except IntegrityError:  # two sign-ups at the same instant: the database's unique keys decide, and say which one was taken
        db.rollback()
        if _phone_taken(db, body.phone):
            raise _field_error("phone", PHONE_TAKEN, "phone_taken", 409)
        raise AppError(EMAIL_TAKEN, code="email_taken", status_code=409)
    quota_service.get_subscription(db, user)
    referral_service.attach(db, user, body.referral_code)  # a friend's code: their welcome bonus now, the inviter's later
    db.commit()
    return _auth_response(db, user)


@router.post("/login", response_model=AuthResponse, dependencies=[Depends(_auth_limit)])
def login(body: LoginRequest, db: Session = Depends(get_db)):
    """Sign in with the email address or the mobile number, and the password."""
    ident = body.email.strip()
    user = None
    if "@" in ident:
        user = db.scalar(select(User).where(func.lower(User.email) == ident.lower()))
    else:
        try:
            user = db.scalar(select(User).where(User.phone == V.normalize_phone(ident)))
        except ValueError:
            user = None
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok or not user.is_active:
        raise Unauthorized("Email, mobile number or password is incorrect.")
    resp = _auth_response(db, user)
    db.commit()
    return resp


@router.patch("/me/upi", response_model=UserOut)
def set_my_upi(body: UpiUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The signed-in person saves (or removes) their OWN UPI ID. Nobody can set anyone else's (ADR 0006)."""
    if not get_settings().upi_links_enabled:
        raise AppError("UPI links are switched off.", code="upi_off", status_code=404)
    user.upi_id = body.upi_id
    db.commit()
    return _user_out(user)


@router.patch("/me/notifications", response_model=UserOut)
def set_my_notifications(body: NotificationPrefs, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Email me my plan when a new one is ready: on or off. Only the person can change their own."""
    user.email_plans = body.email_plans
    db.commit()
    return _user_out(user)


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    out = MeResponse(user=_user_out(user), subscription=quota_service.quota_status(db, user))
    db.commit()
    return out
