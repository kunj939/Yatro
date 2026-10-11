"""The plan by email (services/plan_email.py): sent to the owner when a NEW plan is ready, never for a re-plan, never against their choice,
and never able to hurt the plan. Plus the richer WhatsApp text both share."""
from __future__ import annotations

import json
import re
import uuid

import pytest

from app.core import runtime
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.admin import RuntimeSetting
from app.services import mailer, plan_email, share_service
from tests.conftest import register, trip_payload


@pytest.fixture
def outbox(monkeypatch):
    """Mail is "configured" and captured instead of sent."""
    monkeypatch.setattr(mailer, "configured", lambda: True)  # (not smtp_host: sign-up codes use that, and must keep falling back in tests)
    sent: list[dict] = []
    monkeypatch.setattr(mailer, "send_mail", lambda to, subject, text, html=None: sent.append({"to": to, "subject": subject, "text": text, "html": html}))
    with SessionLocal() as db:
        db.query(RuntimeSetting).delete()
        db.commit()
    runtime.invalidate()
    yield sent
    with SessionLocal() as db:
        db.query(RuntimeSetting).delete()
        db.commit()
    runtime.invalidate()


def _me(client, h):
    return client.get("/api/auth/me", headers=h).json()["user"]


def _plan(client, h, **over):
    t = client.post("/api/trips", json=trip_payload(**over), headers=h)
    assert t.status_code == 201, t.text
    r = client.post(f"/api/trips/{t.json()['id']}/plan", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "ready", r.text
    return r.json()["trip"]


def _titles(trip):
    return [i["title"] for d in trip["itinerary"]["days"] for i in d["items"] if i["kind"] in ("activity", "meal")]


# ------------------------------------------------------------------------------------------------------------ when it is sent

def test_a_new_plan_is_emailed_to_its_owner_once_and_a_replan_is_not(client, outbox):
    h = register(client)
    me = _me(client, h)
    trip = _plan(client, h)
    assert len(outbox) == 1 and outbox[0]["to"] == me["email"]
    assert "Jaipur" in outbox[0]["subject"] and outbox[0]["subject"].startswith("✈️ Your Yatro plan is ready")
    r = client.post(f"/api/trips/{trip['id']}/replan", headers=h, json={"message": "make it more relaxed"})
    assert r.status_code == 200
    assert len(outbox) == 1  # a re-plan does not email again


def test_nothing_is_sent_when_the_person_switched_plan_emails_off(client, outbox):
    h = register(client)
    r = client.patch("/api/auth/me/notifications", json={"email_plans": False}, headers=h)
    assert r.status_code == 200 and r.json()["email_plans"] is False and _me(client, h)["email_plans"] is False
    _plan(client, h)
    assert outbox == []
    client.patch("/api/auth/me/notifications", json={"email_plans": True}, headers=h)
    _plan(client, h)
    assert len(outbox) == 1


def test_nothing_is_sent_when_the_admin_switch_is_on_or_no_mail_server_is_set(client, outbox, monkeypatch):
    h = register(client)
    with SessionLocal() as db:
        db.add(RuntimeSetting(key="switch:plan_emails", value=True, reason=None, updated_by="t", updated_at=__import__("datetime").datetime.now(__import__("datetime").UTC)))
        db.commit()
    runtime.invalidate()
    _plan(client, h)
    assert outbox == []
    with SessionLocal() as db:
        db.query(RuntimeSetting).delete()
        db.commit()
    runtime.invalidate()
    monkeypatch.setattr(mailer, "configured", lambda: False)
    _plan(client, h)
    assert outbox == []  # nothing configured: skipped quietly, the plan is fine


def test_a_mail_failure_never_hurts_the_plan(client, outbox, monkeypatch):
    def boom(*a, **k):
        raise mailer.MailError("SMTPServerDisconnected: Connection unexpectedly closed")
    monkeypatch.setattr(mailer, "send_mail", boom)
    h = register(client)
    trip = _plan(client, h)  # still 200 / ready
    assert trip["status"] == "ready"
    assert client.get("/api/auth/me", headers=h).json()["subscription"]["free_plans_left"] == 1  # and the plan counted normally


# ------------------------------------------------------------------------------------------------------------ what is in it

def test_the_email_has_every_place_the_totals_and_the_right_buttons(client, outbox):
    h = register(client)
    me = _me(client, h)
    trip = _plan(client, h)
    m = outbox[0]
    text, html = m["text"], m["html"]
    for name in _titles(trip):
        assert name in text and name.replace("&", "&amp;") in html  # real names in both parts
    total = trip["itinerary"]["total_cost"]
    assert f"{int(total):,}" in text
    assert f"{get_settings().public_app_url.rstrip('/')}/trips/{trip['id']}" in html  # "Open your plan" goes to the private page
    wa = re.search(r'href="(https://wa\.me/[^"]+)"', html)
    assert wa and wa.group(1).startswith(f"https://wa.me/{me['phone'].lstrip('+')}?text=")  # the person's OWN number, text ready
    assert "*" not in text.split("\n")[0]  # the plain-text part has no WhatsApp markup
    assert "Turn plan emails off" in html and "/account" in html
    assert "<table" in html and "max-width:620px" in html  # a real HTML email


def test_the_email_holds_nothing_about_anyone_else_and_escapes_what_people_typed(client, outbox):
    h = register(client)
    other = register(client)
    other_me = _me(client, other)
    t = client.post("/api/trips", json=trip_payload(title="<script>alert(1)</script> & co"), headers=h).json()
    client.post(f"/api/trips/{t['id']}/members", json={"name": "Guest", "email": "guest-secret@example.com", "budget": 4444}, headers=h)
    client.patch("/api/auth/me/upi", json={"upi_id": "owner.secret@okbank"}, headers=h)
    assert client.post(f"/api/trips/{t['id']}/plan", headers=h).json()["status"] == "ready"
    blob = json.dumps(outbox[0])
    for secret in (other_me["email"], other_me["phone"], "guest-secret@example.com", "owner.secret@okbank", "4444"):
        assert secret not in blob, secret
    assert "<script>alert(1)</script>" not in outbox[0]["html"]  # nothing typed by a person is trusted as HTML


# ------------------------------------------------------------------------------------------------------------ "Email me this plan"

def test_email_me_this_plan_works_on_demand_and_says_where_it_went(client, outbox):
    h = register(client)
    me = _me(client, h)
    trip = _plan(client, h)
    outbox.clear()
    r = client.post(f"/api/trips/{trip['id']}/email", headers=h)
    assert r.status_code == 200 and "•" in r.json()["sent_to"] and me["email"] not in r.text
    assert len(outbox) == 1 and outbox[0]["to"] == me["email"]


def test_email_me_this_plan_refuses_calmly(client, outbox, monkeypatch):
    h = register(client)
    draft = client.post("/api/trips", json=trip_payload(), headers=h).json()
    assert client.post(f"/api/trips/{draft['id']}/email", headers=h).json()["error"]["code"] == "not_ready"  # no plan yet
    # somebody else's trip is not found, never emailed
    other = register(client)
    planned = _plan(client, other)
    assert client.post(f"/api/trips/{planned['id']}/email", headers=h).status_code == 404
    # a mail server that is down: a plain sentence, not a 500
    monkeypatch.setattr(mailer, "send_mail", lambda *a, **k: (_ for _ in ()).throw(mailer.MailError("down")))
    mine = _plan(client, h)
    r = client.post(f"/api/trips/{mine['id']}/email", headers=h)
    assert r.status_code == 502 and r.json()["error"]["code"] == "delivery_failed"
    monkeypatch.setattr(mailer, "configured", lambda: False)
    assert client.post(f"/api/trips/{mine['id']}/email", headers=h).json()["error"]["code"] == "email_unavailable"


# ------------------------------------------------------------------------------------------------------------ the WhatsApp text

def test_the_whatsapp_text_is_richer_but_keeps_its_promises(client):
    h = register(client)
    trip = _plan(client, h)
    t = client.post(f"/api/trips/{trip['id']}/share", json={}, headers=h).json()["text"]
    assert t.startswith("*🧳 ") and "_Planned with Yatro_ ✨" in t and "📅 *Day 1" in t and "💰 *Total ≈" in t and "🔗 Full plan: " in t
    assert "━━━" in t and len(t) <= share_service.TEXT_LIMIT
    assert re.search(r"(🚆|🚌|✈️|🚕|🚗|🚇)", t)  # travel has its icon


def test_emojis_fit_the_kind_of_stop():
    e = share_service.item_emoji
    assert e({"kind": "transport", "meta": {"mode": "train"}}) == "🚆" and e({"kind": "transport", "meta": {"mode": "flight"}}) == "✈️"
    assert e({"kind": "activity", "title": "Amber Fort", "meta": {}}) == "🏰" and e({"kind": "activity", "title": "Birla Mandir", "meta": {}}) == "🛕"
    assert e({"kind": "activity", "title": "Albert Hall Museum", "meta": {}}) == "🏛️" and e({"kind": "activity", "title": "Something", "meta": {}}) == "📍"
    assert e({"kind": "meal", "meta": {"meal": "breakfast"}}) == "🥞" and e({"kind": "sunset", "meta": {}}) == "🌇"


def test_tips_are_the_first_thing_to_go_when_a_trip_is_long():
    day = {"index": 0, "date": "2026-10-17", "label": "Day 1", "items": [
        {"kind": "activity", "title": f"Place number {n}", "time": "10:00", "cost": 500, "cost_state": "estimated", "meta": {}} for n in range(40)]}
    view = {"trip": {"title": "T", "currency": "INR", "travellers": 2, "start_date": "2026-10-17", "end_date": "2026-10-17"},
            "itinerary": {"title": "Long", "days": [day], "total_cost": 20000, "cost_per_person": 10000, "tips": ["Carry water.", "Start early."]},
            "google": {"attribution": None}}
    full = share_service.share_text(view, "https://y.example/s/x", limit=100000)
    short = share_service.share_text(view, "https://y.example/s/x", limit=1500)
    assert "Good to know" in full and "Carry water." in full
    assert len(short) <= 1500 and "Good to know" not in short and "Full plan: https://y.example/s/x" in short


# ------------------------------------------------------------------------------------------------------------ migration

def test_migration_0014_adds_and_removes_the_column_on_sqlite():
    import os
    import sqlite3
    import tempfile

    from tests.test_rail_storage import _alembic
    path = os.path.join(tempfile.mkdtemp(prefix="tripai-mig14-"), "m.db")
    url = f"sqlite:///{path}"
    _alembic(url, "upgrade", "0013")
    db = sqlite3.connect(path)
    db.execute("insert into users (id, created_at, updated_at, email, name, password_hash, is_active, free_plans_used, is_admin) "
               "values (?, '2026-10-11', '2026-10-11', 'old@example.com', 'Old', 'x', 1, 0, 0)", (uuid.uuid4().hex,))
    db.commit()
    db.close()
    _alembic(url, "upgrade", "0014")
    db = sqlite3.connect(path)
    assert "email_plans" in {r[1] for r in db.execute("pragma table_info(users)")}
    assert db.execute("select email_plans from users where email='old@example.com'").fetchone()[0] == 1  # existing accounts: on
    db.close()
    _alembic(url, "downgrade", "0013")
    db = sqlite3.connect(path)
    assert "email_plans" not in {r[1] for r in db.execute("pragma table_info(users)")}
    db.close()
    _alembic(url, "upgrade", "head")


# ------------------------------------------------------------------------------------------------------------ the sender itself

def test_the_mailer_builds_a_proper_two_part_message_and_logs_in(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(s, "smtp_port", 587)
    monkeypatch.setattr(s, "smtp_user", "me@example.test")
    monkeypatch.setattr(s, "smtp_password", "app-password")
    monkeypatch.setattr(s, "smtp_from", "Yatro <hello@example.test>")
    seen = {}

    class Fake:
        def __init__(self, host, port, timeout=None):
            seen["host"], seen["port"] = host, port
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, context=None): seen["tls"] = True
        def login(self, user, pw): seen["login"] = (user, pw)
        def send_message(self, msg): seen["msg"] = msg
    monkeypatch.setattr(mailer.smtplib, "SMTP", Fake)
    mailer.send_mail("to@example.com", "✈️ Hi", "plain body", "<p>html body</p>")
    m = seen["msg"]
    assert seen["tls"] and seen["login"] == ("me@example.test", "app-password") and (seen["host"], seen["port"]) == ("smtp.example.test", 587)
    assert m["To"] == "to@example.com" and m["Subject"] == "✈️ Hi" and "hello@example.test" in m["From"] and m["From"].startswith("Yatro")
    assert m.get_body(("plain",)).get_content().strip() == "plain body" and "<p>html body</p>" in m.get_body(("html",)).get_content()


def test_the_mailer_turns_every_failure_into_one_error_and_refuses_without_a_server(monkeypatch):
    import smtplib
    s = get_settings()
    monkeypatch.setattr(s, "smtp_host", "")
    with pytest.raises(mailer.MailError):
        mailer.send_mail("a@example.com", "s", "t")
    monkeypatch.setattr(s, "smtp_host", "smtp.example.test")

    class Down:
        def __init__(self, *a, **k): raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
    monkeypatch.setattr(mailer.smtplib, "SMTP", Down)
    with pytest.raises(mailer.MailError):
        mailer.send_mail("a@example.com", "s", "t")