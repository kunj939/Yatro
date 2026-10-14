import json

from tests.conftest import register, signup, trip_payload


def _plan(client, auth, **over):
    trip = client.post("/api/trips", json=trip_payload(**over), headers=auth)
    assert trip.status_code == 201, trip.text
    r = client.post(f"/api/trips/{trip.json()['id']}/plan", headers=auth)
    return trip.json(), r


def test_health_and_config(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    cfg = client.get("/api/config").json()
    assert cfg["mode"] == "demo" and cfg["free_trip_plans"] == 2
    assert "key" not in json.dumps(cfg).lower().replace("key_", "")


def test_auth_flow(client):
    r = signup(client, "A", email="flow@example.com", phone="+919812300001", password="short")
    assert r.status_code == 422 and r.json()["error"]["message"] == "Some fields need attention."
    r = signup(client, "A", email="flow@example.com", phone="+919812300001", password="goodpass1")
    assert r.status_code == 201
    r = client.post("/api/auth/send-code", json={"channel": "email", "target": "FLOW@example.com"})
    assert r.status_code == 200  # an address that already has an account gets the same answer as any other (nothing leaks)
    assert client.post("/api/auth/login", json={"email": "flow@example.com", "password": "nope1234"}).status_code == 401
    tok = client.post("/api/auth/login", json={"email": "flow@example.com", "password": "goodpass1"}).json()
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok['access_token']}"}).json()
    assert me["user"]["email"] == "flow@example.com" and me["subscription"]["free_plans_left"] == 2
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/trips", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_demo_trip_is_a_good_day(client):
    assert client.get("/api/demo/trip").status_code == 404
    trip = client.post("/api/demo/plan").json()
    it = trip["itinerary"]
    items = [i for d in it["days"] for i in d["items"]]
    kinds = [i["kind"] for i in items]
    assert kinds[0] == "transport" and kinds[-1] == "arrival"
    assert "sunset" in kinds
    sunset_at = kinds.index("sunset")
    assert any(i["kind"] == "meal" and i["meta"].get("meal") == "dinner" for i in items[sunset_at:])
    b = it["budget"]
    assert b["planned"] <= 5000 and round(b["planned"] + b["remaining"], 2) == 5000
    assert trip["read_only"] is True
    assert any(i["hours_state"] == "uncertain" for i in items)  # Hawa Mahal conflict is surfaced
    assert any(i["alternatives"] for i in items)
    for i in items:
        if i["kind"] in ("activity", "meal", "sunset"):
            assert i["explanation"].startswith("Selected because")
            assert i["distance_from_prev_m"] is not None and i["travel_minutes_from_prev"] is not None
    assert client.get("/api/demo/trip").status_code == 200
    assert trip["expense_summary"]["total"] == 2010


def test_quota_two_lifetime_plans_and_free_replans(client):
    auth = register(client)
    t1, r1 = _plan(client, auth)
    assert r1.status_code == 200 and r1.json()["status"] == "ready", r1.text
    t2, r2 = _plan(client, auth)
    assert r2.status_code == 200
    t3, r3 = _plan(client, auth)
    assert r3.status_code == 402
    assert r3.json()["error"]["message"] == "This month's 2 free trip plans are used. They come back on the 1st."
    # replans of an existing trip never consume quota
    trip = r1.json()["trip"]
    item = next(i for d in trip["itinerary"]["days"] for i in d["items"] if i["alternatives"])
    alt = item["alternatives"][0]
    r = client.post(f"/api/trips/{t1['id']}/replan", headers=auth,
                    json={"swap": {"item_id": item["id"], "alternative_id": alt["id"]}})
    assert r.status_code == 200, r.text
    new = r.json()["trip"]
    titles = [i["title"] for d in new["itinerary"]["days"] for i in d["items"]]
    assert any(alt["title"] in t for t in titles) and new["itinerary"]["version"] == 2
    r = client.post(f"/api/trips/{t1['id']}/replan", headers=auth, json={"message": "make it more relaxed"})
    assert r.status_code == 200 and r.json()["trip"]["preferences"]["pace"] == "relaxed"
    # a request that changes nothing is an answer, not an error: the plan and the change count stay as they are
    before = client.get(f"/api/trips/{t1['id']}", headers=auth).json()
    r = client.post(f"/api/trips/{t1['id']}/replan", headers=auth, json={"message": "hello there"})
    assert r.status_code == 200 and r.json()["status"] == "no_change" and r.json()["message"]
    after = client.get(f"/api/trips/{t1['id']}", headers=auth).json()
    assert after["itinerary"]["version"] == before["itinerary"]["version"]
    assert after["replans_left"] == before["replans_left"]
    sub = client.get("/api/subscription", headers=auth).json()
    assert sub["free_plans_left"] == 0 and not sub["can_plan"]
    # a one-trip purchase (mock) unlocks exactly one more plan
    assert client.post("/api/subscription/upgrade", headers=auth, json={"product": "one_trip"}).json()["trip_credits"] == 1
    assert client.post(f"/api/trips/{t3['id']}/plan", headers=auth).status_code == 200
    assert client.get("/api/subscription", headers=auth).json()["trip_credits"] == 0


def test_infeasible_budget_is_explained_and_refunded(client):
    auth = register(client)
    trip, r = _plan(client, auth, budget=1500)
    body = r.json()
    assert body["status"] == "infeasible"
    f = body["feasibility"]
    assert f["message"] == "The requested trip is not feasible within ₹1,500."
    assert f["main_conflict"] and {a["key"] for a in f["adjustments"]} >= {"higher_budget", "nearby_destination"}
    assert client.get("/api/subscription", headers=auth).json()["free_plans_left"] == 2
    raise_budget = next(a for a in f["adjustments"] if a["key"] == "higher_budget")["apply"]
    r = client.post(f"/api/trips/{trip['id']}/plan", headers=auth, json={"changes": raise_budget})
    assert r.json()["status"] == "ready"
    assert client.get("/api/subscription", headers=auth).json()["free_plans_left"] == 1


def test_streaming_plan_emits_stages(client):
    auth = register(client)
    trip = client.post("/api/trips", json=trip_payload(), headers=auth).json()
    with client.stream("POST", f"/api/trips/{trip['id']}/plan?stream=true", headers=auth) as r:
        events = [json.loads(line) for line in r.iter_lines() if line]
    stages = [e for e in events if e["type"] == "stage" and e["status"] == "done"]
    assert [s["key"] for s in stages] == ["understand", "routes", "places", "hours", "prices", "optimize", "budget",
                                          "alternatives", "itinerary"]
    assert events[-1]["type"] == "result" and events[-1]["status"] == "ready"


def test_validation_and_ownership(client):
    auth = register(client)
    bad = client.post("/api/trips", headers=auth, json=trip_payload(budget=-5, travellers=0))
    assert bad.status_code == 422 and {d["field"] for d in bad.json()["error"]["details"]} >= {"budget", "travellers"}
    unsupported = client.post("/api/trips", headers=auth, json=trip_payload(destination={"name": "Goa"})).json()
    r = client.post(f"/api/trips/{unsupported['id']}/plan", headers=auth)
    assert r.status_code in (422,) and "Demo mode" in r.json()["error"]["message"]
    assert client.get("/api/subscription", headers=auth).json()["free_plans_left"] == 2  # refunded
    other = register(client)
    assert client.get(f"/api/trips/{unsupported['id']}", headers=other).status_code == 404
