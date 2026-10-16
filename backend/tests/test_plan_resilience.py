"""A plan should come out whenever it reasonably can (2026-10-16: "We're a little busy" kept showing for a Rishikesh trip).

- A timeout because our own plan ran out of time doesn't trip the provider's circuit for everyone.
- One failed Google search doesn't sink the plan: the others still count.
- With Google's sights unavailable, Wikidata's famous sights plan the trip.
- An open Google circuit no longer turns new plans away.
- Restaurant search failing leaves the meals to the traveller, but the plan still comes.
"""
from __future__ import annotations

import httpx
import pytest

from app.api.deps import providers_dep
from app.core.errors import ProviderUnavailable
from app.gateway import capacity
from app.gateway import context as C
from app.gateway.breaker import breakers
from app.gateway.core import request
from app.main import app
from app.providers.google import GooglePlacesProvider
from app.providers.types import PlaceData
from tests.sim_providers import sim_providers
from tests.test_gateway import RAIL, gw  # noqa: F401
from tests.test_scenarios import _payload, plus_user  # noqa: F401

CITY = PlaceData(provider="google", provider_place_id="c", name="Rishikesh", category="city", lat=30.08, lng=78.27)


def row(n):
    return {"id": f"p{n}", "displayName": {"text": f"Sight {n}"}, "location": {"latitude": 30.08, "longitude": 78.27},
            "types": ["tourist_attraction"], "rating": 4.5, "userRatingCount": 1000 + n}


def test_timeouts_from_the_plans_own_clock_do_not_open_the_circuit(gw):  # noqa: F811
    gw.handler = lambda r: httpx.ReadTimeout("slow")
    for _ in range(6):
        with C.plan_scope(2.0):  # the call only gets what is left of the plan's time, less than its own 8 s timeout
            with pytest.raises(ProviderUnavailable):
                request("GET", RAIL, provider="RailRadar", retries=0)
    assert not breakers.is_open("railradar")
    for _ in range(5):  # real timeouts, at the provider's own limit, still do
        with pytest.raises(ProviderUnavailable):
            request("GET", RAIL, provider="RailRadar", retries=0)
    assert breakers.is_open("railradar")


def test_one_failed_search_does_not_sink_the_sights(monkeypatch):
    calls = []

    def search(self, q, center, radius=25000, max_results=12, veg=False):
        calls.append(q)
        if "sunset" in q:
            raise ProviderUnavailable("Google Places unavailable (timeout)")
        return [row(len(calls) * 10 + i) for i in range(5)]

    monkeypatch.setattr(GooglePlacesProvider, "_text_search", search)
    out = GooglePlacesProvider().attractions(CITY, ["romantic", "temple"])
    assert any("sunset" in q for q in calls) and len(out) >= 10


def test_restaurants_survive_one_failed_search(monkeypatch):
    seen: list[str] = []

    def search(self, q, center, radius=25000, max_results=12, veg=False):
        if q.startswith("cafes"):
            raise ProviderUnavailable("timeout")
        seen.append(q)  # fixed ids per query (hash() is randomised per run and once made two queries collide)
        base = 100 * (sorted(["restaurants", "famous"]).index("famous" if q.startswith("famous") else "restaurants") + 1)
        return [{**row(base + i), "types": ["restaurant"]} for i in range(4)]

    monkeypatch.setattr(GooglePlacesProvider, "_text_search", search)
    assert len(GooglePlacesProvider().restaurants(CITY, "both", False)) == 8


def test_every_search_failing_still_raises_so_a_fallback_can_apply(monkeypatch):
    def search(self, *a, **k):
        raise ProviderUnavailable("down")

    monkeypatch.setattr(GooglePlacesProvider, "_text_search", search)
    with pytest.raises(ProviderUnavailable):
        GooglePlacesProvider().attractions(CITY, [])
    with pytest.raises(ProviderUnavailable):
        GooglePlacesProvider().restaurants(CITY, "both", False)


def test_wikidata_sights_plan_the_trip_when_google_cannot(monkeypatch):
    from app.providers import wiki_places
    famous = [PlaceData(provider="wikidata", provider_place_id=f"Q{n}", name=f"Famous {n}", category="attraction",
                        lat=30.1, lng=78.3, meta={"wiki_links": 20}) for n in range(8)]
    monkeypatch.setattr(wiki_places.wikidata, "sights", lambda city: famous)

    class Down:
        name, live = "google-places", True

        def attractions(self, *a, **k):
            raise ProviderUnavailable("down")

    assert [p.name for p in wiki_places.WikiAwarePlaces(Down()).attractions(CITY, [])] == [p.name for p in famous]
    monkeypatch.setattr(wiki_places.wikidata, "sights", lambda city: famous[:2])  # too few to plan with: say so
    with pytest.raises(ProviderUnavailable):
        wiki_places.WikiAwarePlaces(Down()).attractions(CITY, [])


def test_an_open_google_circuit_no_longer_turns_new_plans_away(gw):  # noqa: F811
    gw.handler = lambda r: httpx.Response(500)
    for _ in range(5):
        with pytest.raises(ProviderUnavailable):
            request("POST", "https://places.googleapis.com/v1/places:searchText", provider="Google Places", retries=0)
    assert breakers.is_open("google-places")

    class P:
        places = type("Places", (), {"live": True})()

    assert capacity.check_new_plan(P()) is None


def test_a_plan_still_comes_when_restaurant_search_fails(client, plus_user):  # noqa: F811
    p = sim_providers()

    def down(*a, **k):
        raise ProviderUnavailable("Google Places unavailable (timeout)")

    p.places.restaurants = down
    app.dependency_overrides[providers_dep] = lambda: p
    try:
        sc = dict(o="New Delhi", d="Agra", days=0, start="06:00", end="22:00", budget=9000, n=2, modes=["train", "bus", "metro"],
                  food="both", styles=["balanced"], stay="no_stay", tt="normal")
        tid = client.post("/api/trips", headers=plus_user, json=_payload(sc)).json()["id"]
        body = client.post(f"/api/trips/{tid}/plan", headers=plus_user).json()
        assert body["status"] == "ready", body
        assert any("Restaurant suggestions couldn't be loaded" in w for w in body["trip"]["itinerary"]["warnings"])
    finally:
        app.dependency_overrides.pop(providers_dep, None)
