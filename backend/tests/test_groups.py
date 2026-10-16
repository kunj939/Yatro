"""Plan together (ADR 0022): invite link, replies without accounts, results, privacy, and "Make the plan"."""
from __future__ import annotations

import secrets
from datetime import date, timedelta

from tests.conftest import register
from tests.test_scenarios import CITIES, _use, plus_user  # noqa: F401

START = date.today() + timedelta(days=10)


def loc(name):
    return {"name": name, "lat": CITIES[name][0], "lng": CITIES[name][1]}


def make_group(client, h, **over):
    body = {"origin": loc("New Delhi"), "options": [loc("Jaipur"), loc("Agra")], "window_start": START.isoformat(),
            "window_end": (START + timedelta(days=6)).isoformat(), "days": 2, "stay": "tier2", **over}
    r = client.post("/api/groups", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def token_of(bundle):
    return bundle["group"]["url"].rstrip("/").split("/")[-1]


def dev():
    return secrets.token_urlsafe(18)


def reply(client, token, device, name, budget=None, food="both", days=(), votes=(), headers=None):
    body = {"device": device, "name": name, "budget": budget, "food": food,
            "dates": [(START + timedelta(days=d)).isoformat() for d in days], "votes": list(votes)}
    return client.post(f"/api/groups/join/{token}", json=body, headers=headers or {})


def test_link_works_and_a_tampered_one_doesnt(client):
    h = register(client)
    b = make_group(client, h)
    t = token_of(b)
    assert "wa.me" in b["wa_url"] and b["group"]["url"] in b["text"]
    assert client.get(f"/api/groups/join/{t}").status_code == 200
    assert client.get(f"/api/groups/join/{t[:-1]}x").status_code == 404
    assert client.get("/api/groups/join/nonsense").status_code == 404


def test_window_rules(client):
    h = register(client)
    body = {"origin": loc("New Delhi"), "options": [loc("Jaipur")], "window_start": START.isoformat(),
            "window_end": START.isoformat(), "days": 3}
    assert client.post("/api/groups", headers=h, json=body).status_code == 422  # 3 days don't fit in 1
    body.update(window_start=(date.today() - timedelta(days=1)).isoformat(), days=1)
    assert client.post("/api/groups", headers=h, json=body).status_code == 422  # the past
    body.update(window_start=START.isoformat(), options=[loc("Jaipur"), loc("Jaipur")])
    assert client.post("/api/groups", headers=h, json=body).status_code == 422  # same place twice


def test_one_reply_per_browser_and_it_can_be_edited(client):
    h = register(client)
    t = token_of(make_group(client, h))
    d = dev()
    assert reply(client, t, d, "Priya", 9000, "veg", days=[0, 1]).status_code == 200
    v = reply(client, t, d, "Priya S", 8000, "veg", days=[0, 1, 2]).json()
    assert v["replies_count"] == 1 and v["mine"]["name"] == "Priya S" and v["mine"]["budget"] == 8000
    other = client.get(f"/api/groups/join/{t}", params={"device": dev()}).json()
    assert other["mine"] is None and other["names"] == ["Priya S"]


def test_friends_never_see_budgets(client):
    h = register(client)
    t = token_of(make_group(client, h))
    reply(client, t, dev(), "Kabir", 7777, days=[0, 1])
    v = client.get(f"/api/groups/join/{t}").json()
    assert "7777" not in str(v) and "replies" not in v and v["budgets_given"] == 1
    owner = client.get(f"/api/groups/{v['id']}", headers=h).json()["group"]
    assert owner["replies"][0]["budget"] == 7777 and owner["budget"]["per_person"] == 7777


def test_results_votes_dates_food_and_lowest_budget(client):
    h = register(client)
    b = make_group(client, h)
    t, (jaipur, agra) = token_of(b), [o["id"] for o in b["group"]["options"]]
    reply(client, t, dev(), "A", 12000, "non_veg", days=[0, 1, 2, 3], votes=[jaipur])
    reply(client, t, dev(), "B", 8000, "veg", days=[2, 3, 4], votes=[jaipur, agra])
    reply(client, t, dev(), "C", None, "both", days=[2, 3], votes=[agra, jaipur])
    g = client.get(f"/api/groups/{b['group']['id']}", headers=h).json()["group"]
    votes = {o["name"]: o["votes"] for o in g["options"]}
    assert votes == {"Jaipur": 3, "Agra": 2}
    assert g["best_dates"][0] == {"start": (START + timedelta(days=2)).isoformat(), "end": (START + timedelta(days=3)).isoformat(),
                                  "fits": 3, "of": 3}
    assert g["food"] == {"veg": 1, "non_veg": 1, "both": 1}
    assert g["budget"]["per_person"] == 8000 and g["budget"]["people"] == 4  # three friends and the organiser


def test_make_the_plan_creates_one_trip_with_everyone_then_closes(client, plus_user):  # noqa: F811
    b = make_group(client, plus_user)
    gid, t = b["group"]["id"], token_of(b)
    jaipur = b["group"]["options"][0]["id"]
    me = reply(client, t, dev(), "Organiser", 10000, "both", days=[0, 1], votes=[jaipur], headers=plus_user).json()
    assert me["replies_count"] == 1
    reply(client, t, dev(), "Priya", 9000, "veg", days=[0, 1], votes=[jaipur])
    reply(client, t, dev(), "Kabir", None, "non_veg", days=[0, 1], votes=[jaipur])
    r = client.post(f"/api/groups/{gid}/make", headers=plus_user, json={})
    assert r.status_code == 200, r.text
    trip_id = r.json()["trip_id"]
    again = client.post(f"/api/groups/{gid}/make", headers=plus_user, json={}).json()
    assert again["trip_id"] == trip_id  # a second tap opens the same trip
    trip = client.get(f"/api/trips/{trip_id}", headers=plus_user).json()
    assert trip["destination"] == "Jaipur" and trip["start_date"] == START.isoformat()
    assert trip["travellers"] == 3 and float(trip["budget"]) == 27000  # 9,000 each, the lowest given
    assert trip["preferences"]["food"] == "both"
    names = {m["name"]: m for m in trip["members"]}
    assert set(names) == {"Test User", "Priya", "Kabir"} and names["Priya"]["food_pref"] == "veg"
    assert names["Test User"]["budget"] == 10000  # the organiser's own reply fills their member
    assert reply(client, t, dev(), "Late", 5000).status_code == 409  # the link stops taking replies
    view = client.get(f"/api/groups/join/{t}").json()
    assert view["status"] == "made" and view["trip_id"] == trip_id
    # planning the new trip is the normal path: one plan
    _use(dict(tt="normal"))
    p = client.post(f"/api/trips/{trip_id}/plan", headers=plus_user)
    assert p.status_code == 200 and p.json()["status"] == "ready", p.text[:300]


def test_make_needs_a_reply_and_a_budget(client):
    h = register(client)
    b = make_group(client, h)
    gid, t = b["group"]["id"], token_of(b)
    assert client.post(f"/api/groups/{gid}/make", headers=h, json={}).status_code == 409
    reply(client, t, dev(), "Nobudget", None, days=[0, 1])
    assert client.post(f"/api/groups/{gid}/make", headers=h, json={}).json()["error"]["code"] == "no_budget"


def test_only_the_owner_manages_a_group(client):
    h, other = register(client), register(client, name="Someone Else")
    b = make_group(client, h)
    gid = b["group"]["id"]
    assert client.get(f"/api/groups/{gid}", headers=other).status_code == 404
    assert client.post(f"/api/groups/{gid}/make", headers=other, json={}).status_code == 404
    assert client.delete(f"/api/groups/{gid}", headers=other).status_code == 404
    assert [g["id"] for g in client.get("/api/groups", headers=h).json()["groups"]] == [gid]


def test_the_owner_can_remove_a_reply(client):
    h = register(client)
    b = make_group(client, h)
    reply(client, token_of(b), dev(), "Spam", 1)
    g = client.get(f"/api/groups/{b['group']['id']}", headers=h).json()["group"]
    out = client.delete(f"/api/groups/{g['id']}/replies/{g['replies'][0]['id']}", headers=h).json()
    assert out["group"]["replies_count"] == 0


def test_reply_cap(client, monkeypatch):
    from app.services import group_service
    monkeypatch.setattr(group_service, "MAX_REPLIES", 2)
    h = register(client)
    t = token_of(make_group(client, h))
    assert reply(client, t, dev(), "1").status_code == 200
    assert reply(client, t, dev(), "2").status_code == 200
    assert reply(client, t, dev(), "3").status_code == 409


def test_retention_deletes_old_groups(client):
    from datetime import UTC, datetime
    from app.db.session import SessionLocal
    from app.models.group import GroupPlan
    from app.services import group_service
    h = register(client)
    gid = make_group(client, h)["group"]["id"]
    with SessionLocal() as db:
        assert group_service.purge_old(db, datetime.now(UTC) + timedelta(days=60)) >= 1
        db.commit()
        assert db.get(GroupPlan, __import__("uuid").UUID(gid)) is None
