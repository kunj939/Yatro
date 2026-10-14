"""Scenario sweep: many different trips run through the real API and engine (with simulated providers), each
checked against the rules every plan must obey — not against fixed expected output. A new engine change that
breaks any rule for any kind of trip fails here.

Rules for a planned trip (`ready`):
  time      items in order, nothing overlapping, every leg fits before the next item, inside the trip window
  transport every departure is reachable from the item before it; check-out leaves time for the ride
  money     the breakdown adds up; remaining = budget - planned; "within" really is within
  meals     lunch, dinner and breakfast at sensible hours; a vegetarian trip never visits a non-veg-only place
  places    visits happen inside the opening hours shown; stops are near the destination
  substance a trip somewhere else includes something to see there
Rules for a refused trip (`infeasible`): a message, a reason, at least one fix; applying fixes never loops or
stretches a trip past 7 days.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta

import pytest

from app.api.deps import providers_dep
from app.schemas.trip import MAX_DAYS
from app.main import app
from app.utils.geo import haversine_m

from .conftest import register
from .sim_providers import CITIES, sim_providers

BASE = date(2026, 11, 7)  # a Saturday, comfortably in the future of the test clock
MAX_PLAN_SECONDS = 8.0  # with simulated providers; real APIs add network time on top


def _scenarios():
    S = []

    def s(name, o, d, days=0, start="07:00", end="22:00", budget=8000, n=2, modes=("train", "cab"), food="both",
          styles=("balanced",), stay="no_stay", tt="normal", **extra):
        S.append(pytest.param(dict(o=o, d=d, days=days, start=start, end=end, budget=budget, n=n, modes=list(modes),
                                   food=food, styles=list(styles), stay=stay, tt=tt, **extra), id=name))

    # day trips
    s("ncr-jaipur-day", "Gurugram", "Jaipur", start="05:00", end="23:30", food="veg")
    s("delhi-agra-day-cheap", "New Delhi", "Agra", budget=2500, styles=("budget",))
    s("mumbai-lonavala-car", "Mumbai", "Lonavala", modes=("own_car",), n=4, budget=6000)
    s("pune-mumbai-bus", "Pune", "Mumbai", modes=("bus",), food="non_veg")
    s("bengaluru-mysuru-mixed", "Bengaluru", "Mysuru", modes=("mixed",), styles=("sightseeing", "food"))
    s("local-delhi", "Gurugram", "New Delhi", modes=("metro",), budget=3000)
    s("day-trip-short-window", "New Delhi", "Agra", start="09:00", end="19:00")
    # weekends and longer
    s("weekend-jaipur-hotel", "New Delhi", "Jaipur", days=1, stay="tier2", budget=12000)
    s("weekend-rishikesh-hostel", "Jaipur", "Rishikesh", days=1, stay="hostel", modes=("train", "bus"), food="veg",
      styles=("relaxed",), budget=10000)
    s("weekend-mussoorie-railhead", "New Delhi", "Mussoorie", days=1, stay="budget", budget=14000)
    s("3d-udaipur-premium", "Mumbai", "Udaipur", days=2, stay="premium", modes=("flight", "cab"),
      styles=("premium", "romantic"), budget=90000)
    s("4d-goa-friends", "Pune", "Goa", days=3, stay="hostel", n=5, modes=("bus", "train"), styles=("adventure",),
      budget=40000)
    s("5d-varanasi-family", "New Delhi", "Varanasi", days=4, stay="tier1", n=4, food="veg", budget=80000)
    s("7d-amritsar-max-length", "New Delhi", "Amritsar", days=7, stay="tier2", budget=90000)
    s("overnight-return", "Gurugram", "Jaipur", start="05:00", end="01:30", next_day_end=True)
    # groups and budgets
    s("solo-tight", "New Delhi", "Agra", n=1, budget=900, styles=("budget",))
    s("big-group", "New Delhi", "Jaipur", days=1, n=12, stay="budget", budget=90000)
    s("hopeless-budget", "Mumbai", "Goa", days=2, stay="premium", budget=3000)
    s("rich-day", "New Delhi", "Agra", budget=200000, styles=("premium",), modes=("cab", "train", "flight"))
    # awkward transport
    s("sparse-trains", "New Delhi", "Jaipur", days=1, stay="tier2", tt="sparse", modes=("train",))
    s("no-transport-at-all", "New Delhi", "Varanasi", tt="road", modes=("train", "bus"))
    s("rail-apis-down", "Gurugram", "Rishikesh", days=1, stay="hostel", rail_estimate=True, modes=("train", "metro"))
    s("too-far-for-a-day", "Jaipur", "Rishikesh", start="07:00", end="22:00", modes=("bus",))
    # few places
    s("few-places", "New Delhi", "Agra", days=2, stay="tier2", sparse_places=True, budget=30000)
    # abroad, own currency
    s("london-edinburgh", "London", "Edinburgh", days=1, stay="tier2", modes=("train",), budget=600, coords=True)
    s("lisbon-sintra-day", "Lisbon", "Sintra", modes=("train", "bus"), budget=120, coords=True)
    s("osaka-kyoto-day", "Osaka", "Kyoto", modes=("train",), budget=30000, coords=True)
    return S


def _payload(sc: dict) -> dict:
    end_date = BASE + timedelta(days=sc["days"] + (1 if sc.get("next_day_end") else 0))
    loc = lambda n: ({"name": n, "lat": CITIES[n][0], "lng": CITIES[n][1]} if sc.get("coords") else {"name": n})  # noqa: E731
    return {"origin": loc(sc["o"]), "destination": loc(sc["d"]), "start_date": BASE.isoformat(),
            "end_date": end_date.isoformat(), "start_time": sc["start"], "end_time": sc["end"], "budget": sc["budget"],
            "travellers": sc["n"], "traveller_type": "friends" if sc["n"] > 2 else ("solo" if sc["n"] == 1 else "couple"),
            "transport_modes": sc["modes"], "food": sc["food"], "trip_styles": sc["styles"], "stay": sc["stay"]}


@pytest.fixture
def plus_user(client):
    h = register(client)
    assert client.post("/api/subscription/upgrade", headers=h, json={"product": "plus"}).status_code == 200
    return h


def _use(sc: dict):
    p = sim_providers(sc["tt"], sc.get("sparse_places", False), sc.get("rail_estimate", False), sc.get("slow_roads", 1.0))
    app.dependency_overrides[providers_dep] = lambda: p


@pytest.fixture(autouse=True)
def _reset_overrides():
    yield
    app.dependency_overrides.pop(providers_dep, None)


# ------------------------------------------------------------------------------------------------- the rules

def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _hm(s: str | None) -> int | None:
    if not s or ":" not in s:
        return None
    h, m = s.split(":")[:2]
    return int(h) * 60 + int(m)


def check_ready(trip: dict, sc: dict) -> list[str]:
    bad: list[str] = []
    it = trip["itinerary"]
    items = [i for d in it["days"] for i in d["items"]]
    if not items:
        return ["a ready trip has no itinerary items"]
    win_start = datetime.combine(_dt(trip["start_date"] + "T00:00").date(), datetime.strptime(trip["start_time"], "%H:%M").time())
    win_end = datetime.combine(_dt(trip["end_date"] + "T00:00").date(), datetime.strptime(trip["end_time"], "%H:%M").time())
    dest = trip["destination_detail"]

    # time (a train with no timetable carries no clock time at all, ADR 0002 / I1, so it can't be checked for order)
    timed = [i for i in items if i.get("time") is not None]
    for a, b in zip(timed, timed[1:]):
        a_end, b_start = _dt(a["end_at"]), _dt(b["start_at"])
        travel = timedelta(minutes=b["travel_minutes_from_prev"] or 0)
        if b_start < _dt(a["start_at"]):
            bad.append(f"out of order: {b['title']} ({b['time']}) after {a['title']} ({a['time']})")
        elif b_start + timedelta(minutes=2) < a_end + travel:
            bad.append(f"no time to get from {a['title']} (ends {a['end_time']}) to {b['title']} "
                       f"(starts {b['time']}, {travel.seconds // 60} min away)")
    first, last = _dt(items[0]["start_at"]), _dt(items[-1]["end_at"])
    if first < win_start - timedelta(minutes=75):
        bad.append(f"starts {first:%H:%M}, long before the {win_start:%H:%M} start")
    if last > win_end + timedelta(minutes=75):
        bad.append(f"ends {last:%d %H:%M}, long after the {win_end:%d %H:%M} end")

    # money
    b = it["budget"]
    if b:
        cats = sum(c["amount"] for c in b.get("categories", []))
        if abs(cats - b["planned"]) > 2:
            bad.append(f"budget categories add up to {cats}, not the planned {b['planned']}")
        if abs((b["budget"] - b["planned"]) - b["remaining"]) > 2:
            bad.append(f"remaining {b['remaining']} != budget {b['budget']} - planned {b['planned']}")
        if b["status"] in ("within", "tight") and b["planned"] > b["budget"] + 1:
            bad.append(f"status {b['status']} but planned {b['planned']} > budget {b['budget']}")
        if abs(it["total_cost"] - b["planned"]) > 2:
            bad.append(f"itinerary total {it['total_cost']} != planned {b['planned']}")

    # meals and food
    windows = {"breakfast": (6 * 60 + 30, 11 * 60), "lunch": (11 * 60, 16 * 60), "dinner": (18 * 60, 23 * 60)}
    for i in items:
        meal = i["meta"].get("meal")
        if i["kind"] == "meal" and meal in windows:
            lo, hi = windows[meal]
            if not lo <= _hm(i["time"]) <= hi:
                bad.append(f"{meal} at {i['time']} ({i['title']})")
        if i["kind"] == "meal" and sc["food"] == "veg" and "non-veg only" in i["title"]:
            bad.append(f"vegetarian trip sent to {i['title']}")

    # places
    for i in items:
        if i["kind"] in ("activity", "sunset", "meal"):
            o, c = _hm(i["opening_time"]), _hm(i["closing_time"])
            s, e = _hm(i["time"]), _hm(i["end_time"])
            if o is not None and c is not None and c > o and not (o - 1 <= s and e <= c + 1) and e >= s:
                bad.append(f"{i['title']} visited {i['time']}-{i['end_time']}, open {i['opening_time']}-{i['closing_time']}")
            lat, lng = i["meta"].get("lat"), i["meta"].get("lng")
            if lat is not None and dest.get("lat") is not None and haversine_m(lat, lng, dest["lat"], dest["lng"]) > 60_000:
                bad.append(f"{i['title']} is {haversine_m(lat, lng, dest['lat'], dest['lng']) // 1000:.0f} km from the destination")

    # substance
    local = haversine_m(*CITIES[sc["o"]], *CITIES[sc["d"]]) < 60_000
    if not local and not any(i["kind"] in ("activity", "sunset") for i in items):
        bad.append("a trip somewhere else with nothing to see there")
    return bad


def check_infeasible(body: dict) -> list[str]:
    f = body.get("feasibility") or {}
    bad = []
    if not f.get("message"):
        bad.append("refused with no message")
    if not (f.get("main_conflict") or f.get("message")):
        bad.append("refused with no reason")
    if not f.get("adjustments"):
        bad.append("refused with no suggested fix")
    return bad


# ------------------------------------------------------------------------------------------------- the sweep

@pytest.mark.parametrize("sc", _scenarios())
def test_scenario(client, plus_user, sc):
    _use(sc)
    r = client.post("/api/trips", headers=plus_user, json=_payload(sc))
    assert r.status_code == 201, r.text
    trip_id = r.json()["id"]
    t0 = time.perf_counter()
    r = client.post(f"/api/trips/{trip_id}/plan", headers=plus_user)
    took = time.perf_counter() - t0
    assert r.status_code == 200, r.text
    body = r.json()
    assert took < MAX_PLAN_SECONDS, f"planning took {took:.1f}s"
    if body["status"] == "ready":
        problems = check_ready(body["trip"], sc)
    else:
        assert body["status"] == "infeasible", body["status"]
        problems = check_infeasible(body)
    assert not problems, f"{body['status']}: " + "; ".join(problems)


@pytest.mark.parametrize("sc", [p for p in _scenarios() if p.id in (
    "hopeless-budget", "too-far-for-a-day", "sparse-trains", "solo-tight", "no-transport-at-all")])
def test_fixes_never_loop_or_overstretch(client, plus_user, sc):
    """Apply the first suggested fix up to four times: each round must change something new, keep the trip within
    7 days, and end either planned or refused for a still-sensible reason."""
    _use(sc)
    trip_id = client.post("/api/trips", headers=plus_user, json=_payload(sc)).json()["id"]
    body = client.post(f"/api/trips/{trip_id}/plan", headers=plus_user).json()
    seen = []
    for _ in range(4):
        if body["status"] != "infeasible":
            break
        assert not check_infeasible(body)
        fixes = [a for a in body["feasibility"]["adjustments"] if a["apply"].get("action") != "change_destination"]
        if not fixes:
            break
        fix = fixes[0]["apply"]
        assert fix not in seen, f"the same fix was suggested again: {fix}"
        seen.append(fix)
        r = client.post(f"/api/trips/{trip_id}/plan", headers=plus_user, json={"changes": fix})
        assert r.status_code in (200, 422), r.text
        if r.status_code == 422:
            break
        body = r.json()
        t = body["trip"]
        span = (date.fromisoformat(t["end_date"]) - date.fromisoformat(t["start_date"])).days
        assert span <= MAX_DAYS, f"a fix stretched the trip to {span} nights"
    if body["status"] == "ready":
        assert not check_ready(body["trip"], sc)


@pytest.mark.parametrize("message", ["Make it cheaper", "More relaxed", "See more places"])
def test_replans_keep_every_rule(client, plus_user, message):
    sc = dict(o="New Delhi", d="Jaipur", days=1, start="07:00", end="22:00", budget=15000, n=2, modes=["train", "cab"],
              food="veg", styles=["balanced"], stay="tier2", tt="normal")
    _use(sc)
    trip_id = client.post("/api/trips", headers=plus_user, json=_payload(sc)).json()["id"]
    first = client.post(f"/api/trips/{trip_id}/plan", headers=plus_user).json()
    assert first["status"] == "ready" and not check_ready(first["trip"], sc)
    r = client.post(f"/api/trips/{trip_id}/replan", headers=plus_user, json={"message": message})
    assert r.status_code in (200, 422), r.text
    if r.status_code == 200 and r.json()["status"] == "ready":
        assert not check_ready(r.json()["trip"], sc)


def test_must_see_places_are_planned_or_named_never_silently_dropped(client, plus_user):
    """Regression: five places pinned for a day trip to Agra, only two were planned and the rest (including the Taj
    Mahal) vanished with no word. Visitable must-sees must be planned; ones that can't fit must be named."""
    from .sim_providers import SimPlaces, _city
    sc = dict(o="New Delhi", d="Agra", days=0, start="05:00", end="22:00", budget=6000, n=2,
              modes=["train", "bus", "metro"], food="both", styles=["budget"], stay="no_stay", tt="normal")
    _use(sc)

    def daytime(p):  # open across the middle of the day, short, and not a sunset-only spot
        spans = p.opening_hours["weekly"][str(BASE.weekday())]
        return any(o <= "09:30" and c >= "16:30" for o, c in spans) and "sunset" not in p.tags and p.recommended_minutes <= 60
    visitable = [p.provider_place_id for p in SimPlaces().attractions(_city("Agra"), []) if daytime(p)][:3]
    assert len(visitable) == 3
    # ...plus one that can never fit (opens in the evening, after the last train home): it must be named, not lost
    evening = next(p.provider_place_id for p in SimPlaces().attractions(_city("Agra"), [])
                   if p.opening_hours["weekly"][str(BASE.weekday())] == [["17:00", "22:00"]])
    pins = visitable + [evening]
    trip_id = client.post("/api/trips", headers=plus_user, json={**_payload(sc), "include_place_ids": pins}).json()["id"]
    body = client.post(f"/api/trips/{trip_id}/plan", headers=plus_user).json()
    assert body["status"] == "ready", body.get("feasibility")
    it = body["trip"]["itinerary"]
    planned = {i["meta"].get("key") for d in it["days"] for i in d["items"]}
    assert set(visitable) <= planned, f"visitable must-sees dropped: {set(visitable) - planned}"
    if evening not in planned:
        assert "must-see" in " ".join(it["warnings"]), f"a pinned place was dropped silently: {it['warnings']}"
    assert not check_ready(body["trip"], sc)

# ------------------------------------------------------------------------------------------ "Change the plan"

def _planned_agra(client, user, **over):
    sc = dict(o="New Delhi", d="Agra", days=0, start="06:00", end="22:00", budget=9000, n=2,
              modes=["train", "bus", "metro"], food="both", styles=["balanced"], stay="no_stay", tt="normal")
    sc.update(over)
    _use(sc)
    trip_id = client.post("/api/trips", headers=user, json=_payload(sc)).json()["id"]
    body = client.post(f"/api/trips/{trip_id}/plan", headers=user).json()
    assert body["status"] == "ready", body
    return trip_id, body["trip"]


def _say(client, user, trip_id, text, stream=False):
    return client.post(f"/api/trips/{trip_id}/replan{'?stream=true' if stream else ''}", headers=user, json={"message": text})


@pytest.mark.parametrize("text, check", [
    ("start at 9am", lambda t: t["start_time"] == "09:00"),
    ("subah 8 baje shuru karo", lambda t: t["start_time"] == "08:00"),
    ("start 1 hour later", lambda t: t["start_time"] == "07:00"),
    ("back by 8 pm", lambda t: t["end_time"] == "20:00"),
    ("budget 12000", lambda t: t["budget"] == 12000),
    ("increase budget by 1000", lambda t: t["budget"] == 10000),
    ("we are 4 people", lambda t: t["travellers"] == 4),
    ("add one more day", lambda t: t["end_date"] > t["start_date"]),
    ("only veg food", lambda t: t["preferences"]["food"] == "veg"),
    ("no temples", lambda t: "-temple" in t["preferences"]["interests"]),
    ("more shopping", lambda t: "market" in t["preferences"]["interests"]),
    ("aaram se ghoomna hai", lambda t: t["preferences"]["pace"] == "relaxed"),
    ("by train", lambda t: t["preferences"]["transport_modes"][0] == "train"),
])
def test_what_is_typed_is_applied(client, plus_user, text, check):
    trip_id, before = _planned_agra(client, plus_user)
    r = _say(client, plus_user, trip_id, text)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready", (text, body.get("message") or body.get("feasibility"))
    assert check(body["trip"]), (text, body["trip"]["preferences"])
    assert body["trip"]["replans_left"] == before["replans_left"] - 1
    assert body["trip"]["itinerary"]["change_note"].startswith("Updated:")
    assert not check_ready(body["trip"], dict(o="New Delhi", d="Agra", food=body["trip"]["preferences"]["food"]))


def test_a_place_named_but_not_in_the_plan_is_found_and_planned(client, plus_user):
    # two days by cab: Fatehpur Sikri is 37 km out, which a single day cannot afford (that case is the next test)
    trip_id, _ = _planned_agra(client, plus_user, days=1, stay="tier2", modes=["train", "cab"], budget=25000)
    r = _say(client, plus_user, trip_id, "I also want to visit Fatehpur Sikri")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "ready", body
    titles = [i["title"] for d in body["trip"]["itinerary"]["days"] for i in d["items"]]
    assert any("Fatehpur Sikri" in t for t in titles), titles
    assert "added Fatehpur Sikri" in body["trip"]["itinerary"]["change_note"]


def test_the_note_never_claims_a_place_was_added_when_it_did_not_fit(client, plus_user):
    trip_id, _ = _planned_agra(client, plus_user)  # a day trip: 37 km each way plus the visit doesn't fit
    body = _say(client, plus_user, trip_id, "I also want to visit Fatehpur Sikri").json()
    note = body["trip"]["itinerary"]["change_note"]
    titles = [i["title"] for d in body["trip"]["itinerary"]["days"] for i in d["items"]]
    assert not any("Fatehpur Sikri" in t for t in titles)
    assert "didn't fit your time" in note and any("must-see" in w for w in body["trip"]["itinerary"]["warnings"])


@pytest.mark.parametrize("text", ["hello", "make it magical", "asdf qwer zxcv", "add Atlantis", "skip Narnia Castle"])
def test_a_request_that_changes_nothing_is_answered_not_rejected(client, plus_user, text):
    trip_id, before = _planned_agra(client, plus_user)
    for stream in (False, True):
        r = _say(client, plus_user, trip_id, text, stream)
        assert r.status_code == 200, r.text
        body = r.json() if not stream else __import__("json").loads(r.text.strip().splitlines()[-1])
        assert body["status"] == "no_change" and body["message"], body
        assert body["trip"]["itinerary"]["version"] == before["itinerary"]["version"]
        assert body["trip"]["replans_left"] == before["replans_left"]  # nothing was used up


def test_part_of_a_request_is_applied_and_the_rest_explained(client, plus_user):
    trip_id, _ = _planned_agra(client, plus_user)
    body = _say(client, plus_user, trip_id, "make it cheaper and add Atlantis").json()
    assert body["status"] == "ready" and "cheaper choices" in body["trip"]["itinerary"]["change_note"]
    assert "couldn’t find “atlantis”".replace("’", "'") in body["trip"]["itinerary"]["change_note"].replace("’", "'")


def test_asking_for_what_is_already_true_gets_a_straight_answer(client, plus_user):
    trip_id, trip = _planned_agra(client, plus_user)
    _say(client, plus_user, trip_id, "only veg food")
    again = _say(client, plus_user, trip_id, "only veg food").json()
    assert again["status"] == "no_change" and "already" in again["message"]
    now = client.get(f"/api/trips/{trip_id}", headers=plus_user).json()  # the plan as it is now, not as it was
    title = next(i["title"] for d in now["itinerary"]["days"] for i in d["items"] if i["kind"] == "activity")
    planned = _say(client, plus_user, trip_id, f"add {title}").json()
    assert planned["status"] == "no_change" and "already in your plan" in planned["message"]


@pytest.mark.parametrize("text", [
    "😀", "???", "a" * 900, "DROP TABLE trips;", "मुझे ताज महल देखना है", "ਪੰਜਾਬੀ ਵਿੱਚ ਗੱਲ", "1234567890", "<script>alert(1)</script>",
    "start at 99:99", "budget 0", "budget -5", "make it 0 days", "add 100 days", "we are 500 people", "start at 25", "  ,  ",
    "SKIP EVERYTHING", "no no no no no", "budget budget budget", "я хочу посмотреть форт", "ताज महल add करो", "!!!make it cheaper!!!"])
def test_nothing_typed_can_produce_an_error(client, plus_user, text):
    """The promise: any text gets a normal answer — applied, explained, or "nothing to change" — never a failure."""
    trip_id, _ = _planned_agra(client, plus_user)
    r = _say(client, plus_user, trip_id, text)
    assert r.status_code == 200, f"{text[:30]!r} -> {r.status_code} {r.text[:200]}"
    assert r.json()["status"] in ("ready", "no_change", "infeasible")


def test_slow_real_routes_do_not_cost_stops(client, plus_user, monkeypatch):
    """Real routes that turn out slower than the estimates used to remove stops after the day was built. The day is
    now built again with the real times known, so it keeps at least as many stops, and no stop is dropped with a
    'real travel times are longer' warning."""
    from app.services import route_optimizer as RO
    from .sim_providers import SimPlaces, _city
    sc = dict(o="New Delhi", d="Agra", days=0, start="05:30", end="22:00", budget=9000, n=2, modes=["train", "bus", "cab"],
              food="both", styles=["balanced"], stay="no_stay", tt="normal", slow_roads=1.6)
    pins = [p.provider_place_id for p in SimPlaces().attractions(_city("Agra"), [])
            if any(o <= "09:30" and c >= "16:30" for o, c in p.opening_hours["weekly"][str(BASE.weekday())])
            and "sunset" not in p.tags and p.recommended_minutes <= 90][:5]

    def stops(retry: bool) -> int:
        monkeypatch.setattr(RO.Travel, "calibration", RO.Travel.calibration if retry else (lambda self: 1.0))
        _use(sc)
        trip_id = client.post("/api/trips", headers=plus_user, json={**_payload(sc), "include_place_ids": pins}).json()["id"]
        body = client.post(f"/api/trips/{trip_id}/plan", headers=plus_user).json()
        assert body["status"] == "ready" and not check_ready(body["trip"], sc)
        assert not any("longer than first estimated" in w for w in body["trip"]["itinerary"]["warnings"])
        return sum(1 for d in body["trip"]["itinerary"]["days"] for i in d["items"] if i["kind"] in ("activity", "sunset"))
    assert stops(retry=True) >= stops(retry=False)


def test_no_internal_noise_in_the_warnings(client, plus_user):
    """A cab ruled out for distance, or the train station's distance from home, are not things to warn about: the
    first isn't asked about, and the second is already a step of its own in the plan."""
    trip_id, trip = _planned_agra(client, plus_user, modes=["train", "cab"])
    notes = " ".join(trip["itinerary"]["warnings"])
    assert "realistic" not in notes and "so the plan starts with a ride" not in notes
