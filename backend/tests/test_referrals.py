"""Invite a friend (ADR 0012): the friend's bonus at sign-up, the inviter's when the friend's first plan is ready, one
inviter per account, a monthly cap, the admin switch and numbers, and the friend's Plus discount."""
import pytest
from sqlalchemy import delete, select

from app.core import runtime
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.admin import RuntimeSetting
from app.models.referral import Referral
from tests.conftest import send_codes, trip_payload

_n = {"i": 0}


@pytest.fixture(autouse=True)
def _clean():
    def wipe():
        with SessionLocal() as db:
            db.execute(delete(RuntimeSetting))
            db.commit()
        runtime.invalidate()
    wipe()
    yield
    wipe()


def _join(client, name="Friend", ref=None):
    _n["i"] += 1
    email, phone = f"ref{_n['i']}@example.com", f"+9171{_n['i']:08d}"
    codes = send_codes(client, email, phone)
    r = client.post("/api/auth/register", json={"name": name, "email": email, "phone": phone, "password": "travel123",
                                               "password_confirm": "travel123", "email_code": codes["email"],
                                               **({"referral_code": ref} if ref is not None else {})})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _credits(client, h):
    return client.get("/api/subscription", headers=h).json()["trip_credits"]


def _card(client, h):
    return client.get("/api/subscription/referrals", headers=h).json()


def _plan(client, h):
    trip = client.post("/api/trips", json=trip_payload(), headers=h).json()
    out = client.post(f"/api/trips/{trip['id']}/plan", json={}, headers=h).json()
    assert out["status"] == "ready", out


def _admin(client):
    h = _join(client, "Owner")
    get_settings().admin_emails = client.get("/api/auth/me", headers=h).json()["user"]["email"]
    return h


def test_both_sides_are_rewarded_the_friend_now_and_the_inviter_after_the_friends_first_plan(client):
    priya = _join(client, "Priya Sharma")
    card = _card(client, priya)
    assert card["code"].startswith("PRIYA") and card["link"].endswith(f"/register?ref={card['code']}") and card["enabled"]
    friend = _join(client, "Rahul", ref=card["code"].lower())  # typed in any case
    assert _credits(client, friend) == 2 and _credits(client, priya) == 0  # the inviter waits for real use
    assert _card(client, friend)["invited_by"] == "Priya"
    _plan(client, friend)
    assert _credits(client, priya) == 2
    card = _card(client, priya)
    assert (card["invited"], card["rewarded"], card["plans_earned"]) == (1, 1, 2)
    _plan(client, friend)  # a second plan earns nothing more
    assert _credits(client, priya) == 2


def test_a_wrong_or_missing_code_never_blocks_sign_up_and_gives_nothing(client):
    h = _join(client, "Stranger", ref="NOPE1234")
    assert _credits(client, h) == 0 and _card(client, h)["invited_by"] is None


def test_the_friends_first_month_of_plus_is_discounted_once(client):
    inviter = _join(client, "Asha")
    friend = _join(client, "Dev", ref=_card(client, inviter)["code"])
    prices = client.get("/api/subscription", headers=friend).json()["prices"]
    assert prices["plus_first_month_inr"] == round(prices["plus_monthly_inr"] * 0.5)
    assert client.get("/api/subscription", headers=inviter).json()["prices"]["plus_first_month_inr"] is None
    assert client.post("/api/subscription/upgrade", json={"product": "plus"}, headers=friend).status_code == 200
    assert client.get("/api/subscription", headers=friend).json()["prices"]["plus_first_month_inr"] is None  # used


def test_the_monthly_cap_stops_rewards_but_not_friends_joining(client):
    admin = _admin(client)
    assert client.put("/api/admin/controls/settings/referral_monthly_cap", headers=admin,
                      json={"value": 1, "reason": "test"}).status_code == 200
    inviter = _join(client, "Meena")
    code = _card(client, inviter)["code"]
    a, b = _join(client, "A", ref=code), _join(client, "B", ref=code)
    _plan(client, a)
    _plan(client, b)
    assert _credits(client, inviter) == 2  # one reward this month, not two
    with SessionLocal() as db:
        assert sorted(r.status for r in db.scalars(select(Referral).where(Referral.code == code))) == ["capped", "rewarded"]
    assert _credits(client, b) == 2  # the friend's own bonus is never capped


def test_the_admin_switch_and_numbers_change_the_offer_live(client):
    admin = _admin(client)
    inviter = _join(client, "Kabir")
    code = _card(client, inviter)["code"]
    assert client.put("/api/admin/controls/settings/referral_reward_friend", headers=admin,
                      json={"value": 5, "reason": "launch week"}).status_code == 200
    assert _credits(client, _join(client, "Five", ref=code)) == 5
    assert client.put("/api/admin/controls/switches/referrals", headers=admin,
                      json={"on": True, "reason": "abuse check"}).status_code == 200
    assert _credits(client, _join(client, "Paused", ref=code)) == 0 and _card(client, inviter)["enabled"] is False
    listed = client.get("/api/admin/referrals", headers=admin).json()
    mine = [r for r in listed["referrals"] if r["code"] == code]  # the test database holds other tests' referrals too
    assert listed["enabled"] is False and len(mine) == 1 and mine[0]["friend_credits"] == 5 and mine[0]["inviter"]["name"] == "Kabir"
    assert client.get("/api/admin/referrals", headers=inviter).status_code == 404  # admins only
