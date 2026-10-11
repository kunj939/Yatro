"""The admin console API (ADR 0010): the gate, users, trips, live controls, the audit trail and the guard rails."""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest
from sqlalchemy import delete, select

from app.core import brownout, killswitch, runtime
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.admin import AdminAudit, RuntimeSetting
from app.models.user import User

from .conftest import register, signup, trip_payload


def _me(client, h):
    return client.get("/api/auth/me", headers=h).json()["user"]


@pytest.fixture(autouse=True)
def _clean():
    s = get_settings()
    before = s.admin_emails
    with SessionLocal() as db:
        db.execute(delete(RuntimeSetting))
        db.commit()
    runtime.invalidate()
    yield
    s.admin_emails = before
    with SessionLocal() as db:
        db.execute(delete(RuntimeSetting))
        db.commit()
    runtime.invalidate()


@pytest.fixture
def owner(client):
    h = register(client, "Owner One")
    get_settings().admin_emails = _me(client, h)["email"]
    return h


def _make_admin(client, owner_h, name="Second Admin"):
    h = register(client, name)
    u = _me(client, h)
    r = client.patch(f"/api/admin/users/{u['id']}", headers=owner_h, json={"is_admin": True})
    assert r.status_code == 200, r.text
    return h, u


def _put(client, h, path, **body):
    return client.put(f"/api/admin{path}", headers=h, json=body)


def _audit(action=None):
    with SessionLocal() as db:
        q = select(AdminAudit).order_by(AdminAudit.created_at)
        return [a for a in db.scalars(q) if action is None or a.action == action]


# ------------------------------------------------------------------------------------------------------------------ the gate

PATHS = [("get", "/me"), ("get", "/overview"), ("get", "/users"), ("get", "/trips"), ("get", "/plans"), ("get", "/controls"),
         ("get", "/audit")]


def test_everyone_else_gets_a_plain_404(client):
    h = register(client)
    get_settings().admin_emails = ""
    for method, p in PATHS:
        assert getattr(client, method)(f"/api/admin{p}", headers=h).status_code == 404, p
        assert getattr(client, method)(f"/api/admin{p}").status_code == 404, p
        assert getattr(client, method)(f"/api/admin{p}", headers={"Authorization": "Bearer junk"}).status_code == 404, p
    assert client.patch(f"/api/admin/users/{_me(client, h)['id']}", headers=h, json={"is_active": False}).status_code == 404
    assert client.put("/api/admin/controls/switches/signups", headers=h, json={"on": True}).status_code == 404


def test_owner_and_made_admin_get_in_and_a_suspended_admin_does_not(client, owner):
    assert client.get("/api/admin/me", headers=owner).json()["role"] == "owner"
    adm, u = _make_admin(client, owner)
    assert client.get("/api/admin/me", headers=adm).json()["role"] == "admin"
    assert client.get("/api/admin/overview", headers=adm).status_code == 200
    assert client.patch(f"/api/admin/users/{u['id']}", headers=owner, json={"is_active": False}).status_code == 200
    assert client.get("/api/admin/me", headers=adm).status_code == 404  # suspended: the token no longer works


# ------------------------------------------------------------------------------------------------------------------ users

def test_overview_has_the_numbers(client, owner):
    o = client.get("/api/admin/overview", headers=owner).json()
    assert o["users"]["total"] >= 1 and o["users"]["owners"] == 1
    assert len(o["series"]["signups"]) == 14 and "running" in o["runner"]
    assert any(b["group"] == "google" for b in o["budgets"])


def test_users_list_search_filter_sort_and_page(client, owner):
    for n in ("Zed Searchable", "Amy Searchable"):
        register(client, n)
    r = client.get("/api/admin/users", headers=owner, params={"q": "searchable", "sort": "name"}).json()
    assert [u["name"] for u in r["items"]] == ["Amy Searchable", "Zed Searchable"] and r["total"] == 2
    assert client.get("/api/admin/users", headers=owner, params={"q": "%"}).json()["total"] == 0  # a wildcard is not a pattern
    assert client.get("/api/admin/users", headers=owner, params={"q": "searchable", "page_size": 1, "page": 2}).json()["items"][0]["name"] == "Zed Searchable"
    assert client.get("/api/admin/users", headers=owner, params={"status": "admins"}).json()["total"] >= 1
    assert client.get("/api/admin/users", headers=owner, params={"status": "bogus"}).status_code == 422
    row = client.get("/api/admin/users", headers=owner, params={"q": "amy searchable"}).json()["items"][0]
    assert row["plan"] == "free" and row["free_plans_left"] == 2 and row["email_verified"] and "password" not in str(row)


def test_edit_an_account_plans_credits_verification_and_name(client, owner):
    h = register(client, "Plain Person")
    u = _me(client, h)
    r = client.patch(f"/api/admin/users/{u['id']}", headers=owner, json={"plan": "plus", "plus_days": 10, "trip_credits": 3, "name": "Renamed",
                                                                         "email_verified": False, "reason": "support ticket"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["plan"] == "plus" and d["trip_credits"] == 3 and d["name"] == "Renamed" and d["email_verified"] is False
    assert client.get("/api/auth/me", headers=h).json()["subscription"]["plan"] == "plus"
    a = _audit("user.update")[-1]
    assert a.actor_email and a.target_label == u["email"] and a.reason == "support ticket" and "plan" in a.detail and a.detail["name"][1] == "Renamed"
    n = len(_audit())
    # the same values again change nothing and leave no trail; an empty edit is refused
    assert client.patch(f"/api/admin/users/{u['id']}", headers=owner, json={"name": "Renamed"}).status_code == 200
    assert len(_audit()) == n
    assert client.patch(f"/api/admin/users/{u['id']}", headers=owner, json={}).status_code == 422
    assert client.patch(f"/api/admin/users/{u['id']}", headers=owner, json={"plus_days": 5}).status_code == 422
    r = client.patch(f"/api/admin/users/{u['id']}", headers=owner, json={"plan": "free"}).json()
    assert r["plan"] == "free"


def test_free_plans_can_be_given_back(client, owner):
    h = register(client)
    u = _me(client, h)
    t = client.post("/api/trips", json=trip_payload(), headers=h).json()
    assert client.post(f"/api/trips/{t['id']}/plan", headers=h).json()["status"] == "ready"
    assert client.get("/api/auth/me", headers=h).json()["subscription"]["free_plans_left"] == 1
    client.patch(f"/api/admin/users/{u['id']}", headers=owner, json={"free_plans_used": 0})
    assert client.get("/api/auth/me", headers=h).json()["subscription"]["free_plans_left"] == 2


def test_a_suspended_account_cannot_sign_in_or_use_its_token(client, owner):
    r = signup(client, "To Suspend", email="suspendme@example.com", password="travel123")
    tok = {"Authorization": f"Bearer {r.json()['access_token']}"}
    uid = r.json()["user"]["id"]
    assert client.patch(f"/api/admin/users/{uid}", headers=owner, json={"is_active": False}).status_code == 200
    assert client.get("/api/auth/me", headers=tok).status_code == 401
    assert client.post("/api/auth/login", json={"email": "suspendme@example.com", "password": "travel123"}).status_code == 401
    assert client.patch(f"/api/admin/users/{uid}", headers=owner, json={"is_active": True}).status_code == 200
    assert client.get("/api/auth/me", headers=tok).status_code == 200


def test_guard_rails_nobody_can_lock_the_owners_out(client, owner):
    me = _me(client, owner)
    for body in ({"is_active": False}, {"is_admin": True}):  # an owner's access is set on the server, not here
        r = client.patch(f"/api/admin/users/{me['id']}", headers=owner, json=body)
        assert r.status_code == 403, body
    adm, au = _make_admin(client, owner)
    # an admin made from the console is not an owner: cannot touch the owner, make admins, or suspend another admin
    assert client.patch(f"/api/admin/users/{me['id']}", headers=adm, json={"is_active": False}).status_code == 403
    other = _me(client, register(client))
    assert client.patch(f"/api/admin/users/{other['id']}", headers=adm, json={"is_admin": True}).json()["error"]["code"] == "owner_only"
    adm2, au2 = _make_admin(client, owner, "Third Admin")
    assert client.patch(f"/api/admin/users/{au2['id']}", headers=adm, json={"is_active": False}).json()["error"]["code"] == "owner_only"
    assert client.patch(f"/api/admin/users/{au['id']}", headers=adm, json={"is_active": False}).status_code == 403  # not even themselves
    # but an admin can look after ordinary accounts, and the owner can remove an admin
    assert client.patch(f"/api/admin/users/{other['id']}", headers=adm, json={"free_plans_used": 1}).status_code == 200
    assert client.patch(f"/api/admin/users/{au2['id']}", headers=owner, json={"is_admin": False}).status_code == 200
    assert client.get("/api/admin/me", headers=adm2).status_code == 404


def test_deleting_an_account_needs_the_email_and_takes_its_trips_with_it(client, owner):
    h = register(client, "Doomed")
    u = _me(client, h)
    t = client.post("/api/trips", json=trip_payload(), headers=h).json()
    url = f"/api/admin/users/{u['id']}/delete"
    assert client.post(url, headers=owner, json={"confirm_email": "wrong@example.com"}).status_code == 422
    assert client.get(f"/api/admin/users/{u['id']}", headers=owner).status_code == 200
    assert client.post(url, headers=owner, json={"confirm_email": u["email"].upper(), "reason": "asked to"}).status_code == 204
    assert client.get(f"/api/admin/users/{u['id']}", headers=owner).status_code == 404
    assert client.get(f"/api/admin/trips/{t['id']}", headers=owner).status_code == 404
    a = _audit("user.delete")[-1]
    assert a.target_label == u["email"] and a.detail["trips_deleted"] == 1 and a.reason == "asked to"
    me = _me(client, owner)
    assert client.post(f"/api/admin/users/{me['id']}/delete", headers=owner, json={"confirm_email": me["email"]}).status_code == 403


# ------------------------------------------------------------------------------------------------------------------ trips

def test_trips_list_detail_and_delete(client, owner):
    h = register(client, "Trip Owner")
    t = client.post("/api/trips", json=trip_payload(), headers=h).json()
    r = client.get("/api/admin/trips", headers=owner, params={"q": "jaipur"}).json()
    assert any(x["id"] == t["id"] and x["owner"]["name"] == "Trip Owner" for x in r["items"])
    d = client.get(f"/api/admin/trips/{t['id']}", headers=owner).json()
    assert d["trip"]["read_only"] is True and d["owner"]["email"]
    assert client.get("/api/admin/trips", headers=owner, params={"owner": _me(client, h)["id"]}).json()["total"] == 1
    assert client.post(f"/api/admin/trips/{t['id']}/delete", headers=owner, json={"reason": "spam"}).status_code == 204
    assert client.get(f"/api/trips/{t['id']}", headers=h).status_code == 404
    assert _audit("trip.delete")[-1].reason == "spam"


# ------------------------------------------------------------------------------------------------------------------ live controls

def test_controls_list_and_validate(client, owner):
    c = client.get("/api/admin/controls", headers=owner).json()
    assert {s["name"] for s in c["switches"]} == {"signups", "plans", "sms", "sharing", "ai_narration", "plan_emails"}
    assert all(s["people_see"] and s["effect"] for s in c["switches"])
    assert {n["key"] for n in c["numbers"]} == {"brownout_level", "free_trip_plans", "daily_plan_cap"}
    assert _put(client, owner, "/controls/switches/nope", on=True).status_code == 404
    assert _put(client, owner, "/controls/settings/brownout_level", value=9).status_code == 422
    assert _put(client, owner, "/controls/settings/nope", value=1).status_code == 404


def test_pausing_signups_stops_new_accounts_but_not_sign_in(client, owner):
    signup(client, "Existing", email="existing@example.com", password="travel123")
    assert _put(client, owner, "/controls/switches/signups", on=True, reason="launch day").status_code == 200
    r = client.post("/api/auth/send-code", json={"channel": "email", "target": "new@example.com"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "signups_paused"
    assert "little while" in r.json()["error"]["message"]
    assert client.post("/api/auth/login", json={"email": "existing@example.com", "password": "travel123"}).status_code == 200
    assert client.get("/api/config").json()["signups_open"] is False
    assert client.delete("/api/admin/controls/switch:signups", headers=owner).status_code == 200
    assert client.post("/api/auth/send-code", json={"channel": "email", "target": "new@example.com"}).status_code == 200
    a = _audit("control.switch")[-1]
    assert a.target_id == "signups" and a.detail == {"from": False, "to": True} and a.reason == "launch day"
    assert _audit("control.clear")


def test_pausing_plans_is_a_calm_answer_and_uses_no_free_plan(client, owner):
    h = register(client)
    t = client.post("/api/trips", json=trip_payload(), headers=h).json()
    _put(client, owner, "/controls/switches/plans", on=True)
    r = client.post(f"/api/trips/{t['id']}/plan", headers=h)
    assert r.status_code == 200 and r.json()["state"] == "unavailable" and r.json()["reason"] == "paused"
    assert r.json()["quota_used"] is False
    assert client.get("/api/auth/me", headers=h).json()["subscription"]["free_plans_left"] == 2
    _put(client, owner, "/controls/switches/plans", on=False)
    assert client.post(f"/api/trips/{t['id']}/plan", headers=h).json()["status"] == "ready"


def test_sms_sharing_and_ai_switches(client, owner):
    from app.core.errors import AppError
    from app.services import verification_codes as V
    _put(client, owner, "/controls/switches/sms", on=True)
    with pytest.raises(AppError) as e:
        V.send_sms_code("+919800000000", "123456")
    assert e.value.code == "sms_paused"
    _put(client, owner, "/controls/switches/sms", on=False)
    assert V.send_sms_code("+919800000000", "123456").console  # the free console provider in tests
    h = register(client)
    t = client.post("/api/trips", json=trip_payload(), headers=h).json()
    client.post(f"/api/trips/{t['id']}/plan", headers=h)
    assert client.post(f"/api/trips/{t['id']}/share", headers=h, json={}).status_code in (200, 201)
    _put(client, owner, "/controls/switches/sharing", on=True)
    assert client.post(f"/api/trips/{t['id']}/share", headers=h, json={}).status_code == 404
    assert client.get("/api/config").json()["share_links_enabled"] is False
    assert brownout.configured() == 0
    _put(client, owner, "/controls/switches/ai_narration", on=True)
    assert brownout.configured() == 1


def test_numbers_busy_mode_and_free_plans(client, owner):
    assert _put(client, owner, "/controls/settings/brownout_level", value=3, reason="load").status_code == 200
    assert brownout.configured() == 3 and brownout.base_level() == 3
    c = _put(client, owner, "/controls/settings/free_trip_plans", value=5).json()
    assert next(n for n in c["numbers"] if n["key"] == "free_trip_plans")["source"] == "admin"
    h = register(client)
    assert client.get("/api/auth/me", headers=h).json()["subscription"]["free_plans_total"] == 5
    assert client.get("/api/config").json()["free_trip_plans"] == 5
    c = client.delete("/api/admin/controls/free_trip_plans", headers=owner).json()
    assert next(n for n in c["numbers"] if n["key"] == "free_trip_plans")["value"] == 2
    client.delete("/api/admin/controls/brownout_level", headers=owner)
    assert brownout.configured() == 0
    assert client.delete("/api/admin/controls/bogus", headers=owner).status_code == 404


def test_the_daily_plan_cap_can_be_raised_live(client, owner):
    from app.gateway.budget import budgets
    assert budgets.limit("plans") == 25
    c = _put(client, owner, "/controls/settings/daily_plan_cap", value=60, reason="launch day").json()
    assert next(n for n in c["numbers"] if n["key"] == "daily_plan_cap")["value"] == 60 and budgets.limit("plans") == 60
    _put(client, owner, "/controls/settings/daily_plan_cap", value=0)
    assert budgets.limit("plans") is None  # 0 = no cap
    client.delete("/api/admin/controls/daily_plan_cap", headers=owner)
    assert budgets.limit("plans") == 25


def test_the_admins_choice_beats_the_environment_and_clearing_gives_it_back(client, owner, monkeypatch):
    monkeypatch.setenv("KILL_SMS", "true")
    assert killswitch.killed("sms") and killswitch.source("sms") == "env"
    _put(client, owner, "/controls/switches/sms", on=False)
    assert not killswitch.killed("sms") and killswitch.source("sms") == "admin"
    client.delete("/api/admin/controls/switch:sms", headers=owner)
    assert killswitch.killed("sms") and killswitch.source("sms") == "env"


def test_a_database_problem_never_takes_a_lever_down(monkeypatch):
    import app.db.session as S
    runtime.invalidate()

    class Boom:
        def __call__(self, *a, **k):
            raise RuntimeError("database is down")
    monkeypatch.setattr(S, "SessionLocal", Boom())
    runtime._warned = True  # keep the log quiet
    assert runtime.get("switch:sms", "fallback") == "fallback"
    assert runtime.free_trip_plans() == get_settings().free_trip_plans
    assert brownout.base_level() == get_settings().brownout_level
    runtime.invalidate()


# ------------------------------------------------------------------------------------------------------------------ the trail

def test_audit_trail_filters_pages_and_has_no_edit_route(client, owner):
    h = register(client)
    u = _me(client, h)
    client.patch(f"/api/admin/users/{u['id']}", headers=owner, json={"name": "A. Name"})
    _put(client, owner, "/controls/switches/sharing", on=True)
    r = client.get("/api/admin/audit", headers=owner).json()
    assert r["total"] >= 2 and r["items"][0]["at"] >= r["items"][-1]["at"]
    assert all(i["action"].startswith("control") for i in client.get("/api/admin/audit", headers=owner, params={"action": "control"}).json()["items"])
    assert client.get("/api/admin/audit", headers=owner, params={"q": u["email"]}).json()["total"] >= 1
    assert len(client.get("/api/admin/audit", headers=owner, params={"page_size": 1}).json()["items"]) == 1
    audit_id = r["items"][0]["id"]
    for m in ("put", "patch", "delete"):
        assert getattr(client, m)(f"/api/admin/audit/{audit_id}", headers=owner).status_code in (404, 405)


def test_the_browser_may_use_every_method_the_console_uses(client):
    """A browser asks first (a preflight) and only sends PUT / PATCH if the server says it may: a method missing from the CORS list
    makes a button silently do nothing, and tests that call the API directly never see it."""
    origin = get_settings().cors_origin_list[0]
    for method, path in (("PUT", "/api/admin/controls/switches/signups"), ("PATCH", "/api/admin/users/x"), ("POST", "/api/admin/users/x/delete"),
                         ("DELETE", "/api/admin/controls/switch:sms")):
        r = client.options(path, headers={"Origin": origin, "Access-Control-Request-Method": method,
                                          "Access-Control-Request-Headers": "authorization,content-type"})
        assert r.status_code == 200, (method, r.text)
        assert method in r.headers["access-control-allow-methods"], method


def test_admin_answers_are_never_cached(client, owner):
    assert client.get("/api/admin/users", headers=owner).headers["cache-control"] == "no-store"


# ------------------------------------------------------------------------------------------------------------------ migration

def test_migration_0013_upgrades_and_downgrades_on_sqlite():
    from tests.test_rail_storage import _alembic
    path = os.path.join(tempfile.mkdtemp(prefix="tripai-mig13-"), "m.db")
    url = f"sqlite:///{path}"
    _alembic(url, "upgrade", "0012")
    _alembic(url, "upgrade", "0013")
    db = sqlite3.connect(path)
    tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    assert {"runtime_settings", "admin_audit"} <= tables
    assert "is_admin" in {r[1] for r in db.execute("pragma table_info(users)")}
    db.close()
    _alembic(url, "downgrade", "0012")
    db = sqlite3.connect(path)
    assert "admin_audit" not in {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    assert "is_admin" not in {r[1] for r in db.execute("pragma table_info(users)")}
    db.close()
    _alembic(url, "upgrade", "head")
