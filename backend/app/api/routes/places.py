from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import providers_dep
from app.core.errors import AppError, ProviderUnavailable
from app.core.middleware import limit
from app.providers.registry import Providers
from app.utils.locale import locale_for

router = APIRouter(prefix="/places", tags=["places"], dependencies=[Depends(limit("places", 90))])


@router.get("/autocomplete")
def autocomplete(q: str = Query("", max_length=100), p: Providers = Depends(providers_dep)):
    try:
        rows = p.places.autocomplete(q.strip(), limit=6) if len(q.strip()) >= 1 else p.places.autocomplete("", 6)
    except ProviderUnavailable:
        return {"results": [], "message": "Place search is temporarily unavailable. You can still type a city name."}
    return {"results": [{"place_id": s.place_id, "label": s.label, "secondary": s.secondary, "lat": s.lat,
                         "lng": s.lng} for s in rows], "mode": p.mode}


@router.get("/highlights")
def highlights(destination: str = Query(min_length=2, max_length=200), place_id: str | None = Query(default=None),
               p: Providers = Depends(providers_dep)):
    """A handful of the destination's best-known real places, for the trip wizard's optional 'favourite things
    to see' step — so a traveller can pin an actual place (e.g. Taj Mahal) rather than only a category. Not a
    full plan: no dates, budget or opening-hours checks; that happens once the trip is actually planned."""
    try:
        city = p.places.resolve(destination.strip(), place_id)
        if city is None:
            return {"results": [], "resolved": None}
        places = p.places.attractions(city, [])
    except ProviderUnavailable:
        return {"results": [], "resolved": None,
                "message": "Couldn't look up places for this destination right now."}
    except AppError:
        # e.g. demo mode's fixed sample city: a normal, expected case for this best-effort endpoint, not
        # something worth surfacing as an error while someone is still filling in the trip wizard.
        return {"results": [], "resolved": None}
    ranked = sorted(places, key=lambda place: -((place.rating_count or 0) * max(place.rating or 3.0, 1.0)))
    return {"results": [{"place_id": place.provider_place_id, "name": place.name,
                         "teaser": (place.description or ", ".join(t.replace("_", " ") for t in place.tags[:2]) or None),
                         "rating": place.rating, "rating_count": place.rating_count}
                        for place in ranked[:10]],
            # Coordinates and currency let the wizard ask for the budget in the destination's own currency — the
            # same one create_trip() will store it in once these coordinates are sent back.
            "resolved": {"place_id": city.provider_place_id, "name": city.name, "address": city.address,
                         "lat": city.lat, "lng": city.lng,
                         "currency": locale_for(city.lat, city.lng)[1] if city.lat is not None else None}}


@router.get("/reverse")
def reverse(lat: float = Query(ge=-90, le=90), lng: float = Query(ge=-180, le=180), p: Providers = Depends(providers_dep)):
    try:
        place = p.places.reverse(lat, lng)
    except ProviderUnavailable:
        place = None
    if not place:
        return {"result": None}
    return {"result": {"place_id": place.provider_place_id, "label": place.name, "secondary": place.address,
                       "lat": place.lat, "lng": place.lng}}