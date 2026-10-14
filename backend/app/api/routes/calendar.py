"""Calendar feed for a trip (ADR 0015). The owner turns it on and gets subscribe links; calendars then fetch the feed
by its secret token, no sign-in. Turning it off (or a new token) makes the old URL answer 404."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.core.middleware import limit
from app.db.session import get_db
from app.models.trip import Trip
from app.models.user import User
from app.services import calendar_service as CAL
from app.services import trip_service as TS

router = APIRouter(tags=["calendar"])
_feed_limit = limit("calendar", get_settings().share_rate_limit_per_minute)
GONE = "This calendar link isn't available any more."


def _feed_url(request: Request, token: str) -> str:
    """The feed's public address. Behind a proxy the request looks like http; calendars need https outside localhost."""
    base = str(request.base_url).rstrip("/")
    if not any(h in base for h in ("localhost", "127.0.0.1")):
        base = "https://" + base.split("://", 1)[1]
    return f"{base}{get_settings().api_prefix}/cal/{token}.ics"


def _planned(db: Session, trip_id: uuid.UUID, user: User) -> Trip:
    trip = TS.load_trip(db, trip_id, user)
    if trip.current_itinerary is None:
        raise AppError("Plan this trip first.", code="not_planned", status_code=409)
    return trip


@router.post("/trips/{trip_id}/calendar")
def turn_on(trip_id: uuid.UUID, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip = _planned(db, trip_id, user)
    token = CAL.ensure_token(trip)
    db.commit()
    return CAL.links(_feed_url(request, token))


@router.delete("/trips/{trip_id}/calendar", status_code=204)
def turn_off(trip_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    trip = TS.load_trip(db, trip_id, user)
    trip.calendar_token = None
    db.commit()
    return Response(status_code=204)


@router.get("/trips/{trip_id}/calendar.ics")
def download(trip_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """A one-off file: the plan as it is now, for calendars that import rather than subscribe."""
    trip = _planned(db, trip_id, user)
    name = "".join(c if c.isalnum() else "-" for c in trip.destination_name.split(",")[0]).strip("-") or "trip"
    return Response(CAL.ics(trip), media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}-Yaitro.ics"'})


@router.get("/cal/{token}.ics", dependencies=[Depends(_feed_limit)])
def feed(token: str, db: Session = Depends(get_db)):
    trip = db.scalar(select(Trip).where(Trip.calendar_token == token)) if len(token) >= 20 else None
    if trip is None:
        raise NotFound(GONE)
    trip = TS.load_trip(db, trip.id)
    if trip.current_itinerary is None:
        raise NotFound(GONE)
    return Response(CAL.ics(trip), media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="trip.ics"'})
