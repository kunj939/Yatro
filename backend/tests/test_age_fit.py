"""Age groups in the party: asked in the wizard, stored with the trip, and used to choose and pace the places (services/age_fit.py).

Rules under test:
  * nothing changes without age information, or for adults only;
  * places for adults only (night clubs, bars) are never planned for a party with anyone under 18, unless pinned by name;
  * a place that suits one group badly is demoted for the whole party (the strictest member rules); a good fit is lifted;
  * children and older travellers get a relaxed pace and shorter walks, unless the traveller chose a pace themselves.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.api.deps import providers_dep
from app.main import app
from app.providers.types import PlaceData
from app.schemas.trip import PreferencesPatch, TripCreate
from app.services import age_fit as AF
from app.services.engine_types import Candidate, PlanInput
from app.services.planning_support import attraction_candidates, restaurant_candidates
from app.services.ranking import score_attraction
from app.providers.types import RestaurantData

from .conftest import register, trip_payload
from .sim_providers import SimPlaces, sim_providers

BASE = date(2026, 11, 7)


# ---------------------------------------------------------------------------------------------------- the rules themselves

def test_unknown_and_repeated_groups_are_dropped_and_the_order_is_fixed():
    assert AF.clean(["seniors", "kids", "kids", "alien", "adults"]) == ["kids", "adults", "seniors"]
    assert AF.clean(None) == [] and AF.clean([]) == []


def test_nothing_changes_without_age_information_or_for_adults_only():
    assert AF.fit([], ["zoo", "adventure", "nightlife"]) == (0.0, None)
    assert AF.fit(["adults"], ["zoo", "adventure", "nightlife"]) == (0.0, None)
    assert AF.effective_pace("normal", ["adults"]) == "normal"
    assert AF.walk_limit_m(["adults", "teens"], 900) == 900
    assert AF.search_hints(["adults"]) == [] and AF.search_hints([]) == []
    assert AF.describe(["adults"]) == "" and AF.describe([]) == ""
    assert AF.allowed(["adults"], ["nightlife"], ["night_club"])  # adults may go


def test_a_zoo_suits_children_and_a_trek_does_not_suit_older_travellers():
    lift, who = AF.fit(["kids"], ["zoo", "nature"])
    assert lift > 0.3 and who == "kids" and AF.reason(["kids"], ["zoo", "nature"]) == "good for children"
    drop, who = AF.fit(["seniors"], ["adventure", "nature"])
    assert drop < -0.4 and who is None and AF.reason(["seniors"], ["adventure", "nature"]) is None
    assert AF.fit(["seniors"], ["garden", "temple"])[0] > 0.3
    assert AF.fit(["seniors"], ["fort"])[0] < 0  # steep climbs: demoted a little, not removed


def test_the_strictest_member_rules_in_a_mixed_party():
    # teenagers love adventure but a grandparent in the party makes it a poor fit for everyone
    assert AF.fit(["teens", "seniors"], ["adventure"])[0] < 0
    # children and grandparents both fine with a garden
    assert AF.fit(["kids", "seniors"], ["garden"])[0] > 0
    # a zoo lifts the party when no one minds it (nobody is hurt by it, children gain)
    assert AF.fit(["kids", "seniors"], ["zoo"])[0] > 0
    # lift and drop are bounded: age re-orders close calls, it never dwarfs the ranking's own terms
    assert -0.5 <= AF.fit(["seniors"], ["adventure", "nightlife", "fort"])[0] <= 0
    assert AF.fit(["kids"], ["zoo", "family", "park", "garden"])[0] <= 0.4


def test_a_bar_is_adults_only_but_a_restaurant_with_a_bar_is_not():
    assert AF.adults_only(["nightlife"], ["night_club"])
    assert AF.adults_only(["nightlife"], ["bar", "point_of_interest"])
    assert AF.adults_only([], ["casino"])
    assert not AF.adults_only(["nightlife"], ["bar", "restaurant", "food"])  # most restaurants list "bar"
    assert not AF.adults_only(["cafe"], ["cafe", "food"])
    assert not AF.adults_only(["museum"], ["museum"])


def test_adults_only_places_are_kept_from_children_and_teens_unless_pinned():
    for groups in (["kids"], ["teens"], ["adults", "kids"], ["seniors", "teens"]):
        assert not AF.allowed(groups, ["nightlife"], ["night_club"]), groups
        assert AF.allowed(groups, ["nightlife"], ["night_club"], pinned=True), groups  # asked for by name
    for groups in ([], ["adults"], ["seniors"], ["adults", "seniors"]):
        assert AF.allowed(groups, ["nightlife"], ["night_club"]), groups


def test_children_and_older_travellers_get_a_relaxed_pace_unless_the_traveller_chose_one():
    for groups in (["kids"], ["seniors"], ["adults", "kids"], ["teens", "seniors"]):
        assert AF.effective_pace("normal", groups) == "relaxed", groups
        assert AF.effective_pace("packed", groups) == "packed"  # their own choice stands
        assert AF.effective_pace("relaxed", groups) == "relaxed"
        assert AF.walk_limit_m(groups, 900) == 500
    assert AF.effective_pace("normal", ["teens"]) == "normal"  # teenagers keep up
    assert AF.effective_pace(None, ["adults"]) == "normal"


def test_search_hints_add_at_most_one_extra_search_and_the_words_read_well():
    assert AF.search_hints(["kids", "seniors"]) == ["family"]
    assert AF.search_hints(["seniors"]) == ["relaxed"]
    assert AF.search_hints(["adults", "teens"]) == ["adventure"]
    assert AF.describe(["kids"]) == "with children"
    assert AF.describe(["adults", "kids", "seniors"]) == "with children and older travellers"
    assert AF.describe(["kids", "teens", "seniors"]) == "with children, teenagers and older travellers"


# ------------------------------------------------------------------------------------------------------- the request

def test_the_request_accepts_age_groups_and_cleans_them():
    t = TripCreate(**trip_payload(age_groups=["seniors", "adults", "adults", "kids"]))
    assert t.age_groups == ["kids", "adults", "seniors"]
    assert TripCreate(**trip_payload()).age_groups == []  # not asked: nothing changes
    with pytest.raises(ValueError):
        TripCreate(**trip_payload(age_groups=["toddlers"]))  # an unknown band is a mistake in the client, not a thing to guess
    assert PreferencesPatch(age_groups=["kids"]).age_groups == ["kids"]
    assert PreferencesPatch(age_groups=[]).age_groups == []  # explicit clear
    assert PreferencesPatch().age_groups is None  # not mentioned: no change


def test_age_groups_are_stored_returned_and_changeable(client):
    h = register(client)
    r = client.post("/api/trips", json=trip_payload(age_groups=["adults", "kids"]), headers=h)
    assert r.status_code == 201, r.text
    trip = r.json()
    assert trip["preferences"]["age_groups"] == ["kids", "adults"]
    again = client.get(f"/api/trips/{trip['id']}", headers=h).json()
    assert again["preferences"]["age_groups"] == ["kids", "adults"]
    r = client.patch(f"/api/trips/{trip['id']}/preferences", json={"age_groups": ["seniors"]}, headers=h)
    assert r.status_code == 200 and r.json()["preferences"]["age_groups"] == ["seniors"]
    r = client.patch(f"/api/trips/{trip['id']}/preferences", json={"age_groups": []}, headers=h)
    assert r.json()["preferences"]["age_groups"] == []
    # a trip made without the field (an older client) still works and reports an empty list
    old = client.post("/api/trips", json=trip_payload(), headers=h).json()
    assert old["preferences"]["age_groups"] == []


# ------------------------------------------------------------------------------------------- ranking and candidate lists

def _inp(ages, **kw) -> PlanInput:
    inp = PlanInput.__new__(PlanInput)  # only what scoring reads
    inp.styles, inp.traveller_type, inp.interests, inp.travellers = ["balanced"], "family", [], 2
    inp.avoid_tags, inp.age_groups, inp.excluded, inp.pinned = set(), ages, set(), []
    inp.food, inp.member_food, inp.currency = "both", [], "INR"
    inp.__dict__.update(kw)
    return inp


def _cand(name, tags, types=(), count=20000, pinned=False) -> Candidate:
    p = PlaceData("google", name, name, "attraction", 26.9, 75.8, rating=4.4, rating_count=count, types=list(types))
    p.tags = list(tags)
    return Candidate(key=name, place=p, kind="attraction", pinned=pinned)


def test_a_place_that_suits_the_ages_ranks_higher_and_says_why():
    zoo, plain = _cand("Zoo", ["zoo", "nature"]), _cand("Plain", ["landmark"])
    for c in (zoo, plain):
        score_attraction(c, _inp(["kids"]), Decimal(500))
    assert zoo.score > plain.score and zoo.age_note == "good for children" and plain.age_note is None
    zoo_no_ages = _cand("Zoo", ["zoo", "nature"])
    score_attraction(zoo_no_ages, _inp([]), Decimal(500))
    assert zoo_no_ages.score < zoo.score and zoo_no_ages.age_note is None  # the same place, without the ages: no lift, no note


def test_a_trek_drops_for_a_party_with_an_older_traveller():
    trek, garden = _cand("Trek", ["adventure", "nature"]), _cand("Garden", ["garden", "nature"])
    for c in (trek, garden):
        score_attraction(c, _inp(["adults", "seniors"]), Decimal(500))
    assert garden.score > trek.score
    t2 = _cand("Trek", ["adventure", "nature"])
    score_attraction(t2, _inp(["adults"]), Decimal(500))
    assert t2.score > trek.score  # adults alone: unchanged


def test_a_pinned_must_see_is_never_demoted_out_of_the_plan():
    fort = _cand("Amber Fort", ["fort", "adventure"], pinned=True)
    score_attraction(fort, _inp(["seniors"]), Decimal(500))
    assert fort.score > 5  # the +10 for a place the traveller asked for by name dwarfs the age adjustment


def test_night_clubs_are_filtered_for_children_and_kept_for_adults_and_when_pinned():
    club = PlaceData("google", "club", "Moonlight Club", "attraction", 26.9, 75.8, rating=4.5, rating_count=9000,
                     types=["night_club"], tags=["nightlife"])
    museum = PlaceData("google", "museum", "City Museum", "attraction", 26.9, 75.8, rating=4.5, rating_count=9000,
                       types=["museum"], tags=["museum"])
    bunch = [club, museum] + [PlaceData("google", f"p{i}", f"Place {i}", "attraction", 26.9, 75.8, rating=4.4,
                                        rating_count=9000, tags=["landmark"]) for i in range(8)]
    names = lambda inp: {c.place.name for c in attraction_candidates(bunch, inp, Decimal(1000))}  # noqa: E731
    assert "Moonlight Club" not in names(_inp(["kids", "adults"]))
    assert "Moonlight Club" not in names(_inp(["teens"]))
    assert "Moonlight Club" in names(_inp(["adults"]))
    assert "Moonlight Club" in names(_inp([]))
    assert "Moonlight Club" in names(_inp(["kids"], pinned=["club"]))  # asked for by name: the traveller's call
    assert "City Museum" in names(_inp(["kids"]))


def test_restaurants_with_a_bar_stay_for_families_but_a_club_does_not():
    def resto(name, types):
        return PlaceData("google", name, name, "restaurant", 26.9, 75.8, rating=4.4, rating_count=900, types=types,
                         restaurant=RestaurantData(), tags=[])
    pool = [resto("Bar & Grill", ["restaurant", "bar", "food"]), resto("Late Club", ["night_club"]),
            resto("Thali House", ["restaurant", "food"])]
    got = {c.place.name for c in restaurant_candidates(pool, _inp(["kids"]), Decimal(300))}
    assert got == {"Bar & Grill", "Thali House"}


def test_plan_input_carries_the_ages_and_the_pace(client):
    from app.db.session import SessionLocal
    from app.services import planning_support as PS
    from app.services import trip_service as TS
    h = register(client)
    t = client.post("/api/trips", json=trip_payload(age_groups=["adults", "seniors"]), headers=h).json()
    with SessionLocal() as db:
        inp = PS.plan_input(TS.load_trip(db, __import__("uuid").UUID(t["id"])))
    assert inp.age_groups == ["adults", "seniors"] and inp.pace == "relaxed"
    t = client.post("/api/trips", json=trip_payload(age_groups=["adults", "seniors"], pace="packed"), headers=h).json()
    with SessionLocal() as db:
        assert PS.plan_input(TS.load_trip(db, __import__("uuid").UUID(t["id"]))).pace == "packed"
    t = client.post("/api/trips", json=trip_payload(), headers=h).json()
    with SessionLocal() as db:
        inp = PS.plan_input(TS.load_trip(db, __import__("uuid").UUID(t["id"])))
    assert inp.age_groups == [] and inp.pace == "normal"


# ------------------------------------------------------------------------------------ end to end, simulated providers

class AgePlaces(SimPlaces):
    """The simulated city plus a night club, a zoo, and a trek: the places an age-aware plan should treat differently."""

    def attractions(self, city, interests):
        out = super().attractions(city, interests)
        mk = lambda pid, name, tags, types: PlaceData(  # noqa: E731
            "sim", f"sim:{city.name}:{pid}", name, "attraction", city.lat + 0.004, city.lng + 0.004, address=city.name,
            city=city.name, rating=4.7, rating_count=60000, tags=tags, types=types, business_status="OPERATIONAL",
            opening_hours={"weekly": {str(d): [["00:00", "23:59"]] for d in range(7)}, "source": "sim"},
            entry_fee=Decimal(0), entry_fee_state="estimated", recommended_minutes=60)
        return out + [mk("club", "Moonlight Club", ["nightlife"], ["night_club"]),
                      mk("zoo", "City Zoo", ["zoo", "nature"], ["zoo"]),
                      mk("trek", "Ridge Trek", ["adventure", "nature"], ["hiking_area"])]


@pytest.fixture
def age_world():
    p = sim_providers()
    p.places = AgePlaces()
    app.dependency_overrides[providers_dep] = lambda: p
    yield
    app.dependency_overrides.pop(providers_dep, None)


def _plan(client, h, **over):
    payload = {"origin": {"name": "New Delhi"}, "destination": {"name": "Agra"}, "start_date": BASE.isoformat(),
               "end_date": (BASE + timedelta(days=1)).isoformat(), "start_time": "07:00", "end_time": "21:00", "budget": 40000,
               "travellers": 3, "traveller_type": "family", "transport_modes": ["train", "cab"], "food": "both",
               "trip_styles": ["balanced"], "stay": "tier2", "interests": ["nightlife"]}
    payload.update(over)
    trip = client.post("/api/trips", json=payload, headers=h)
    assert trip.status_code == 201, trip.text
    r = client.post(f"/api/trips/{trip.json()['id']}/plan", headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "ready", out
    return out["trip"]


def _titles(trip) -> list[str]:
    return [i["title"] for d in trip["itinerary"]["days"] for i in d["items"]]


def _activities_per_day(trip) -> list[int]:
    return [sum(1 for i in d["items"] if i["kind"] == "activity") for d in trip["itinerary"]["days"]]


def test_a_family_with_children_is_never_sent_to_a_night_club(client, age_world):
    h = register(client)
    h = {**h}
    assert client.post("/api/subscription/upgrade", headers=h, json={"product": "plus"}).status_code == 200
    # the traveller even asked for nightlife, and the club is the best-known place around: still not for a party with a child in it
    kids = _plan(client, h, age_groups=["adults", "kids"])
    assert "Moonlight Club" not in _titles(kids)
    assert kids["itinerary"]["pipeline_trace"][0]["detail"].count("with children") == 1  # the plan says who it was made for
    # the same trip for adults is allowed to include it (the club is the top-ranked place for a nightlife fan)
    adults = _plan(client, h, age_groups=["adults"])
    assert "Moonlight Club" in _titles(adults)


def test_older_travellers_get_fewer_stops_a_day_and_no_trek(client, age_world):
    h = register(client)
    assert client.post("/api/subscription/upgrade", headers=h, json={"product": "plus"}).status_code == 200
    seniors = _plan(client, h, age_groups=["adults", "seniors"], interests=[], trip_styles=["adventure"])
    assert max(_activities_per_day(seniors)) <= 3  # relaxed pace
    assert "Ridge Trek" not in _titles(seniors)
    assert seniors["preferences"]["age_groups"] == ["adults", "seniors"]


def test_children_get_family_places_first_and_the_reason_is_shown(client, age_world):
    h = register(client)
    assert client.post("/api/subscription/upgrade", headers=h, json={"product": "plus"}).status_code == 200
    kids = _plan(client, h, age_groups=["kids"], interests=[], trip_styles=["balanced"])
    assert "City Zoo" in _titles(kids)
    zoo = next(i for d in kids["itinerary"]["days"] for i in d["items"] if i["title"] == "City Zoo")
    assert any(r["key"] == "age" and r["text"] == "good for children" for r in zoo["reasons"])


def test_a_replan_can_add_the_ages_and_the_plan_changes_with_them(client, age_world):
    h = register(client)
    assert client.post("/api/subscription/upgrade", headers=h, json={"product": "plus"}).status_code == 200
    first = _plan(client, h, age_groups=["adults"])
    assert "Moonlight Club" in _titles(first)
    r = client.post(f"/api/trips/{first['id']}/replan", headers=h, json={"changes": {"age_groups": ["adults", "kids"]}})
    assert r.status_code == 200, r.text
    again = r.json()["trip"]
    assert again["preferences"]["age_groups"] == ["kids", "adults"]
    assert "Moonlight Club" not in _titles(again)
    assert "children" in (again["itinerary"]["change_note"] or "")


# ------------------------------------------------------------------------------------------------------------- migration

def test_migration_0012_adds_and_removes_the_column_on_sqlite():
    import os
    import sqlite3
    import tempfile

    from tests.test_rail_storage import _alembic
    path = os.path.join(tempfile.mkdtemp(prefix="tripai-mig12-"), "m.db")
    url = f"sqlite:///{path}"
    _alembic(url, "upgrade", "0011")
    db = sqlite3.connect(path)
    assert "age_groups" not in {r[1] for r in db.execute("pragma table_info(trip_preferences)")}
    db.close()
    _alembic(url, "upgrade", "0012")
    db = sqlite3.connect(path)
    assert "age_groups" in {r[1] for r in db.execute("pragma table_info(trip_preferences)")}
    db.close()
    _alembic(url, "downgrade", "0011")
    db = sqlite3.connect(path)
    assert "age_groups" not in {r[1] for r in db.execute("pragma table_info(trip_preferences)")}
    db.close()
    _alembic(url, "upgrade", "head")
