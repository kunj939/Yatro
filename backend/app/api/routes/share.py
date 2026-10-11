"""Public share page data (ADR 0006). No sign-in: the token is the key. Unknown, revoked and expired links all answer 404
with the same body. Never cached, never indexed."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import providers_dep
from app.core import runtime
from app.core.config import get_settings
from app.core.errors import NotFound
from app.core.middleware import limit
from app.db.session import get_db
from app.providers.registry import Providers
from app.services import google_content, share_service
from app.services import trip_service as TS

router = APIRouter(prefix="/share", tags=["share"])
_share_limit = limit("share", get_settings().share_rate_limit_per_minute)
GONE = "This link isn't available. Ask the person who sent it for a new one."


@router.get("/{token}", dependencies=[Depends(_share_limit)])
def get_shared_trip(token: str, db: Session = Depends(get_db), providers: Providers = Depends(providers_dep)):
    # Cache-Control and X-Robots-Tag are set by the middleware, so the 404 and 429 answers carry them too
    link = share_service.find_active(db, token) if runtime.share_links_enabled() else None
    if link is None:
        raise NotFound(GONE)
    trip = TS.load_trip(db, link.trip_id)
    if trip.current_itinerary is None:
        raise NotFound(GONE)
    if google_content.refresh_trip(db, trip, providers)["refreshed"]:  # Google content older than 30 days (I6)
        trip = TS.load_trip(db, link.trip_id)
    link.view_count += 1
    link.last_viewed_at = datetime.now(UTC)
    db.commit()
    return share_service.share_view(trip, link)
