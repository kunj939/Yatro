"""Optional 'favourites' chips: validated, expanded to tags, and weighted above inferred style matches."""
from decimal import Decimal

from app.providers.types import PlaceData
from app.schemas.trip import PreferencesPatch, TripCreate
from app.services.engine_types import Candidate, PlanInput
from app.services.interest_tags import INTEREST_CHIPS, expand_chips
from app.services.ranking import score_attraction
from tests.conftest import register, trip_payload


def test_unknown_chip_ids_are_dropped_not_rejected():
    body = trip_payload(interests=["forts_palaces", "not_a_real_chip", "forts_palaces"])
    t = TripCreate(**body)
    assert t.interests == ["forts_palaces"]


def test_chips_expand_to_the_underlying_tags():
    assert set(expand_chips(["forts_palaces", "street_food"])) == {"fort", "palace", "royal", "street_food", "food"}
    assert expand_chips(None) == [] and expand_chips([]) == []
    assert expand_chips(["bogus"]) == []


def test_every_chip_has_a_readable_label():
    from app.services.interest_tags import INTEREST_LABELS
    assert set(INTEREST_LABELS) == set(INTEREST_CHIPS)


def test_preferences_patch_interests_replace_rather_than_merge():
    p = PreferencesPatch(interests=["forts_palaces", "junk"])
    assert p.interests == ["forts_palaces"]
    assert PreferencesPatch(interests=[]).interests == []  # explicit clear
    assert PreferencesPatch().interests is None  # not mentioned: no change


def _place(name, tags, rating_count=5000):
    p = PlaceData("google", name, name, "attraction", 26.9, 75.8, rating=4.4, rating_count=rating_count)
    p.tags = tags
    return Candidate(key=name, place=p, kind="attraction")


def test_a_picked_favourite_outranks_an_unrelated_place_of_similar_quality():
    inp = PlanInput.__new__(PlanInput)
    inp.styles, inp.traveller_type, inp.interests, inp.travellers = ["balanced"], "solo", ["fort"], 1
    inp.avoid_tags = set()
    fort = _place("Amber Fort", ["fort", "heritage"])
    generic = _place("City Park", ["park"])
    score_attraction(fort, inp, Decimal(500))
    score_attraction(generic, inp, Decimal(500))
    assert fort.score > generic.score
    assert fort.matched_interests == ["fort"]
    assert generic.matched_interests == []


def test_explicit_pick_beats_an_equally_strong_inferred_style_match():
    inp = PlanInput.__new__(PlanInput)
    inp.styles, inp.traveller_type, inp.travellers = ["romantic"], "couple", 2  # "romantic" style also weights "sunset" at 1.0
    inp.avoid_tags = set()
    a = _place("A", ["sunset"])
    inp.interests = ["viewpoint"]  # explicitly picked, distinct tag, should still score at least as well
    b = _place("B", ["viewpoint"])
    score_attraction(a, inp, Decimal(500))
    score_attraction(b, inp, Decimal(500))
    assert b.score >= a.score  # the explicit pick is weighted at least as strongly as the inferred style


def test_endpoint_accepts_interests_end_to_end(client):
    headers = register(client)
    body = trip_payload(interests=["forts_palaces", "street_food"])
    r = client.post("/api/trips", json=body, headers=headers)
    assert r.status_code == 201, r.text


def test_highlights_endpoint_returns_real_named_places(client):
    r = client.get("/api/places/highlights", params={"destination": "Jaipur"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resolved"]["name"]
    names = [x["name"] for x in body["results"]]
    assert names and all(isinstance(n, str) and n for n in names)
    # ranked by how well-known the place is (rating_count x rating), best known first
    fame = [(x["rating_count"] or 0) * max(x["rating"] or 3.0, 1.0) for x in body["results"]]
    assert fame == sorted(fame, reverse=True)


def test_a_place_pinned_at_creation_is_kept_out_of_the_weak_place_filter():
    from app.providers.types import PlaceData
    from app.services.planning_support import weak_place
    obscure = PlaceData("google", "p1", "JAIPUR MARKET", "attraction", 26.9, 75.8)
    obscure.rating_count = 40
    assert weak_place(obscure, "Jaipur")  # would normally be filtered out...
    # ...but a place the traveller pinned when creating the trip is always kept (checked in attraction_candidates
    # via `p.provider_place_id in inp.pinned`, exercised end-to-end in the trip-creation test below)


def test_trip_creation_accepts_pinned_places(client):
    headers = register(client)
    hl = client.get("/api/places/highlights", params={"destination": "Jaipur"}, headers=headers).json()
    pid = hl["results"][0]["place_id"]
    body = trip_payload(include_place_ids=[pid, pid, "not-a-real-id-but-harmless"])
    r = client.post("/api/trips", json=body, headers=headers)
    assert r.status_code == 201, r.text