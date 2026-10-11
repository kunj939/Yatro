"""Public sample trip (no sign-up, no API keys, no quota): Gurugram -> Jaipur, couple, ₹5,000, vegetarian,
train + cab, one day. Always planned with the offline demo providers and cached for a few hours."""
from __future__ import annotations

import secrets
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.trips import stream_plan
from app.core.config import get_settings
from app.core.errors import NotFound
from app.core.middleware import limit
from app.core.security import hash_password
from app.db.session import get_db
from app.models.expense import Expense, ExpenseSplit
from app.models.trip import Trip, TripMember, TripPreferences
from app.models.user import User
from app.providers.registry import get_demo_providers
from app.services import trip_service as TS

router = APIRouter(prefix="/demo", tags=["demo"])
DEMO_EMAIL = "demo@tripai.local"
FRESH_FOR = timedelta(hours=6)


def demo_date(today: date | None = None) -> date:
    """The next Saturday at least two days away — a realistic weekend day trip."""
    today = today or date.today()
    d = today + timedelta(days=2)
    return d + timedelta(days=(5 - d.weekday()) % 7)


def _demo_user(db: Session) -> User:
    user = db.scalar(select(User).where(User.email == DEMO_EMAIL))
    if user is None:
        user = User(email=DEMO_EMAIL, name="TripAI sample", is_active=False,
                    password_hash=hash_password(secrets.token_urlsafe(32)))
        db.add(user)
        db.flush()
    return user


# A sample trip planned before this server started may come from older engine code: plan it again.
_BOOTED_AT = datetime.now(UTC)


def _latest(db: Session, user: User) -> Trip | None:
    trip = db.scalar(select(Trip).where(Trip.owner_id == user.id, Trip.status == "ready")
                     .order_by(Trip.created_at.desc()).limit(1))
    if trip is None:
        return None
    created = trip.created_at if trip.created_at.tzinfo else trip.created_at.replace(tzinfo=UTC)
    if datetime.now(UTC) - created > FRESH_FOR or trip.start_date != demo_date() or created < _BOOTED_AT:
        return None
    return trip


def _new_demo_trip(db: Session, user: User) -> Trip:
    d = demo_date()
    trip = Trip(owner_id=user.id, title="Jaipur day trip", status="draft", origin_name="Gurugram",
                destination_name="Jaipur", start_date=d, end_date=d + timedelta(days=1), start_time=time(5, 0),
                end_time=time(1, 30), budget=Decimal(5000), travellers=2, timezone=get_settings().default_timezone,
                quota_consumed=True, quota_source="demo")
    trip.preferences = TripPreferences(traveller_type="couple", transport_modes=["train", "cab"], food="veg",
                                       trip_styles=["balanced", "romantic"], stay="no_stay", pace="normal",
                                       interests=[], excluded_place_ids=[], pinned_place_ids=[], pinned_transport={})
    aarav = TripMember(name="Aarav", is_owner=True, food_pref="veg", budget=Decimal(2500))
    meera = TripMember(name="Meera", food_pref="veg", budget=Decimal(2500))
    trip.members = [aarav, meera]
    db.add(trip)
    db.flush()
    for desc, cat, amount, payer in (("Train tickets (both ways)", "transport", Decimal(660), aarav),
                                     ("Lunch at LMB", "food", Decimal(900), meera),
                                     ("Cabs around Jaipur", "local", Decimal(450), aarav)):
        half = (amount / 2).quantize(Decimal("0.01"))
        db.add(Expense(trip_id=trip.id, description=desc, category=cat, amount=amount, paid_by_member_id=payer.id,
                       split_type="equal", splits=[ExpenseSplit(member_id=aarav.id, amount=half),
                                                   ExpenseSplit(member_id=meera.id, amount=amount - half)]))
    db.flush()
    return trip


@router.get("/trip")
def demo_trip(db: Session = Depends(get_db)):
    user = _demo_user(db)
    db.commit()
    trip = _latest(db, user)
    if trip is None:
        raise NotFound("The sample trip hasn't been planned yet.", code="demo_not_ready")
    return TS.trip_out(TS.load_trip(db, trip.id), read_only=True)


@router.post("/plan", dependencies=[Depends(limit("demo", 10))])
def demo_plan(stream: bool = Query(False), db: Session = Depends(get_db)):
    user = _demo_user(db)
    trip = _new_demo_trip(db, user)
    db.commit()
    providers = get_demo_providers()
    if stream:
        return stream_plan(trip.id, user.id, providers, change_note=None, is_replan=False, read_only=True)
    TS.run_plan(db, providers, user, trip)
    db.expire_all()
    return TS.trip_out(TS.load_trip(db, trip.id), read_only=True)