"""Phase 3, Part A (ADR 0006): share links, the public view, the WhatsApp text, UPI IDs.

I8 is the core: the public view and text are built from an allow-list and must never carry an email, phone, password,
member budget, UPI ID, or (unless the owner chose it) the trip budget or the expense split. Revocation and expiry are
tested against the real endpoints; nothing here calls an external service."""
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.share import ShareLink
from app.services import share_service
from tests.conftest import register, trip_payload

BUDGET = 7321  # unusual on purpose: searched for in the public output
MEMBER_BUDGET = 4444
OWNER_UPI = "owner.secret@okbank"


def _owner(client):
    auth = register(client)
    me = client.get("/api/auth/me", headers=auth).json()["user"]
    return auth, me


def _planned(client, auth, **over):
    trip = client.post("/api/trips", json=trip_payload(budget=BUDGET, **over), headers=auth).json()
    r = client.post(f"/api/trips/{trip['id']}/plan", headers=auth)
    assert r.status_code == 200, r.text
    return trip["id"]


def _share(client, auth, trip_id, **body):
    r = client.post(f"/api/trips/{trip_id}/share", json=body, headers=auth)
    assert r.status_code == 201, r.text
    j = r.json()
    return j, j["url"].rsplit("/", 1)[1]


def _public(client, token):
    return client.get(f"/api/share/{token}")


@pytest.fixture
def seeded(client):
    """A planned trip with a member (email, budget), an expense, the owner's UPI ID, and the owner's identity."""
    auth, me = _owner(client)
    assert client.patch("/api/auth/me/upi", json={"upi_id": OWNER_UPI}, headers=auth).status_code == 200
    trip_id = _planned(client, auth)
    owner_member = client.get(f"/api/trips/{trip_id}", headers=auth).json()["members"][0]["id"]
    riya = client.post(f"/api/trips/{trip_id}/members", headers=auth,
                       json={"name": "Riya", "email": "riya-secret@example.com", "budget": MEMBER_BUDGET}).json()
    assert client.post(f"/api/trips/{trip_id}/expenses", headers=auth, json={
        "description": "Hotel advance", "amount": 1450, "paid_by_member_id": owner_member, "split_type": "equal"}).status_code == 201
    return {"auth": auth, "me": me, "trip_id": trip_id, "riya": riya}


SECRETS = lambda s: [s["me"]["email"], s["me"]["phone"], "password", "password_hash", "riya-secret@example.com",
                     str(MEMBER_BUDGET), OWNER_UPI, "upi", "token_hash", str(s["me"]["id"])]


# --- I8: nothing private in the public view or the text ---------------------------------------------------------------

def test_the_public_view_and_text_hold_no_private_data(client, seeded):
    j, token = _share(client, seeded["auth"], seeded["trip_id"])
    view = _public(client, token)
    assert view.status_code == 200
    blob = json.dumps(view.json()) + j["text"] + unquote(j["wa_url"])
    for secret in SECRETS(seeded):
        assert secret not in blob, secret
    assert str(BUDGET) not in blob and "Hotel advance" not in blob  # the budget and expenses stay private by default
    from app.utils.money import fmt_money  # ...in the form the page prints them too, and in the plan's own sentences
    remaining = client.get(f"/api/trips/{seeded['trip_id']}", headers=seeded["auth"]).json()["itinerary"]["remaining"]
    for form in (f"{BUDGET:,}", fmt_money(BUDGET), fmt_money(remaining), f"{int(remaining):,}"):
        assert form not in blob, form
    summary = view.json()["itinerary"]["summary"] or ""
    assert "budget" not in summary.lower() and "leaving" not in summary.lower()
    full = client.get(f"/api/trips/{seeded['trip_id']}", headers=seeded["auth"]).json()["itinerary"]["summary"]
    assert "budget" in full.lower()  # the owner's own view still has it: it is the share that drops it


def test_the_public_view_has_exactly_the_allow_listed_fields(client, seeded):
    _, token = _share(client, seeded["auth"], seeded["trip_id"])
    v = _public(client, token).json()
    assert set(v) == {"trip", "itinerary", "google"}
    assert set(v["trip"]) == {"title", "destination", "start_date", "end_date", "travellers", "currency"}
    assert set(v["itinerary"]) == {"title", "summary", "tips", "days", "total_cost", "cost_per_person"}
    item_keys = set(share_service._ITEM_KEYS) | {"meta"}
    for d in v["itinerary"]["days"]:
        assert set(d) == {"index", "date", "label", "items"}
        for i in d["items"]:
            assert set(i) == item_keys
            assert set(i["meta"]) <= set(share_service._META_KEYS) | {"legs"}
            assert not {"lat", "lng", "from", "to", "polyline"} & (set(i) | set(i["meta"]))  # no coordinates, no route lines
            for leg in i["meta"].get("legs") or []:
                assert set(leg) == set(share_service._LEG_KEYS)


def test_costs_are_shown_with_their_labels(client, seeded):
    _, token = _share(client, seeded["auth"], seeded["trip_id"])
    v = _public(client, token).json()
    assert v["itinerary"]["total_cost"] > 0 and v["itinerary"]["cost_per_person"] > 0
    costed = [i for d in v["itinerary"]["days"] for i in d["items"] if i["cost"]]
    assert costed and all(i["cost_state"] for i in costed)
    assert all(i["meta"].get("fare_label") for i in costed if i["kind"] == "transport")


def test_the_owner_budget_shows_only_when_asked(client, seeded):
    _, plain = _share(client, seeded["auth"], seeded["trip_id"])
    assert "budget" not in _public(client, plain).json()
    j, token = _share(client, seeded["auth"], seeded["trip_id"], show_budget=True)
    v = _public(client, token).json()
    assert v["budget"]["budget"] == BUDGET and v["budget"]["remaining"] is not None
    assert "budget" in v["itinerary"]["summary"].lower()  # shown on purpose: the summary keeps its budget sentence
    assert f"{BUDGET:,}" in j["text"] or str(BUDGET) in j["text"]
    sid = j["share"]["id"]  # the owner can turn it back off
    assert client.patch(f"/api/trips/{seeded['trip_id']}/shares/{sid}", json={"show_budget": False}, headers=seeded["auth"]).status_code == 200
    assert "budget" not in _public(client, token).json()


def test_the_split_appears_only_for_plan_and_split(client, seeded):
    _, plain = _share(client, seeded["auth"], seeded["trip_id"])
    assert "split" not in _public(client, plain).json()
    _, token = _share(client, seeded["auth"], seeded["trip_id"], include_split=True)
    split = _public(client, token).json()["split"]
    assert split["total"] == 1450 and {m["name"] for m in split["members"]} >= {"Riya"}
    assert split["expenses"][0]["description"] == "Hotel advance"
    blob = json.dumps(split)
    assert "riya-secret@example.com" not in blob and str(MEMBER_BUDGET) not in blob and OWNER_UPI not in blob


def test_the_token_is_never_stored(client, seeded):
    _, token = _share(client, seeded["auth"], seeded["trip_id"])
    with SessionLocal() as db:
        row = db.scalar(select(ShareLink).where(ShareLink.token_prefix == token[:6]))
        stored = " ".join(str(getattr(row, c.name)) for c in ShareLink.__table__.columns)
    assert token not in stored and row.token_hash == share_service._hash(token) and len(token) >= 40


# --- Revoking, expiring, who may do what ------------------------------------------------------------------------------

def test_unknown_revoked_and_expired_links_are_indistinguishable(client, seeded):
    j, token = _share(client, seeded["auth"], seeded["trip_id"])
    unknown = _public(client, "x" * 43)
    assert unknown.status_code == 404
    sid = j["share"]["id"]
    assert client.delete(f"/api/trips/{seeded['trip_id']}/shares/{sid}", headers=seeded["auth"]).status_code == 204
    revoked = _public(client, token)
    _, other = _share(client, seeded["auth"], seeded["trip_id"])
    with SessionLocal() as db:
        link = db.scalar(select(ShareLink).where(ShareLink.token_prefix == other[:6]))
        link.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    expired = _public(client, other)
    assert revoked.status_code == expired.status_code == 404
    assert revoked.json()["error"]["message"] == expired.json()["error"]["message"] == unknown.json()["error"]["message"]


def test_a_link_expires_30_days_after_the_trips_last_day_by_default(client, seeded):
    j, _ = _share(client, seeded["auth"], seeded["trip_id"])
    trip = client.get(f"/api/trips/{seeded['trip_id']}", headers=seeded["auth"]).json()
    end = datetime.fromisoformat(trip["end_date"]).date()
    assert datetime.fromisoformat(j["share"]["expires_at"]).date() == end + timedelta(days=30)
    never, _ = _share(client, seeded["auth"], seeded["trip_id"], expires="never")
    assert never["share"]["expires_at"] is None


def test_the_owner_can_extend_remove_or_restore_the_expiry(client, seeded):
    auth, tid = seeded["auth"], seeded["trip_id"]
    j, _ = _share(client, auth, tid)
    sid, first = j["share"]["id"], datetime.fromisoformat(j["share"]["expires_at"])
    r = client.patch(f"/api/trips/{tid}/shares/{sid}", json={"extend_days": 10}, headers=auth).json()
    assert datetime.fromisoformat(r["expires_at"]) == first + timedelta(days=10)
    assert client.patch(f"/api/trips/{tid}/shares/{sid}", json={"expires": "never"}, headers=auth).json()["expires_at"] is None
    back = client.patch(f"/api/trips/{tid}/shares/{sid}", json={"expires": "default"}, headers=auth).json()
    assert datetime.fromisoformat(back["expires_at"]) == first
    assert client.patch(f"/api/trips/{tid}/shares/{sid}", json={"extend_days": 0}, headers=auth).status_code == 422


def test_the_shared_links_list_shows_active_links_and_counts_views(client, seeded):
    auth, tid = seeded["auth"], seeded["trip_id"]
    a, token = _share(client, auth, tid)
    b, _ = _share(client, auth, tid)
    _public(client, token)
    _public(client, token)
    rows = {s["id"]: s for s in client.get(f"/api/trips/{tid}/shares", headers=auth).json()["shares"]}
    assert set(rows) == {a["share"]["id"], b["share"]["id"]} and rows[a["share"]["id"]]["view_count"] == 2
    assert all(len(s["prefix"]) == 6 and "url" not in s and "token" not in s for s in rows.values())
    client.delete(f"/api/trips/{tid}/shares/{a['share']['id']}", headers=auth)
    assert [s["id"] for s in client.get(f"/api/trips/{tid}/shares", headers=auth).json()["shares"]] == [b["share"]["id"]]


def test_only_the_owner_can_create_list_change_or_revoke(client, seeded):
    tid = seeded["trip_id"]
    j, token = _share(client, seeded["auth"], tid)
    stranger = register(client)
    sid = j["share"]["id"]
    assert client.post(f"/api/trips/{tid}/share", json={}, headers=stranger).status_code == 404
    assert client.get(f"/api/trips/{tid}/shares", headers=stranger).status_code == 404
    assert client.patch(f"/api/trips/{tid}/shares/{sid}", json={"expires": "never"}, headers=stranger).status_code == 404
    assert client.delete(f"/api/trips/{tid}/shares/{sid}", headers=stranger).status_code == 404
    assert client.post(f"/api/trips/{tid}/share-text", json={"token": token}, headers=stranger).status_code == 404
    assert client.post(f"/api/trips/{tid}/share", json={}).status_code == 401
    assert _public(client, token).status_code == 200  # untouched


def test_a_link_cannot_be_used_through_another_trip(client, seeded):
    other = _planned(client, seeded["auth"])
    _, token = _share(client, seeded["auth"], seeded["trip_id"])
    r = client.post(f"/api/trips/{other}/share-text", json={"token": token}, headers=seeded["auth"])
    assert r.status_code == 404


def test_an_unplanned_trip_cannot_be_shared(client):
    auth = register(client)
    trip = client.post("/api/trips", json=trip_payload(), headers=auth).json()
    assert client.post(f"/api/trips/{trip['id']}/share", json={}, headers=auth).status_code == 409


def test_the_public_response_is_never_cached_or_indexed(client, seeded):
    _, token = _share(client, seeded["auth"], seeded["trip_id"])
    r = _public(client, token)
    assert r.headers["cache-control"] == "no-store" and "noindex" in r.headers["x-robots-tag"]
    assert "noindex" in _public(client, "y" * 43).headers["x-robots-tag"]


def test_the_public_endpoint_is_rate_limited(client, seeded):
    from app.core.middleware import rate_limiter
    s = get_settings()
    s.rate_limit_enabled = True
    rate_limiter.reset()
    try:
        codes = [_public(client, "z" * 43).status_code for _ in range(s.share_rate_limit_per_minute + 2)]
    finally:
        s.rate_limit_enabled = False
        rate_limiter.reset()
    assert codes[0] == 404 and codes[-1] == 429


def test_sharing_can_be_switched_off(client, seeded):
    _, token = _share(client, seeded["auth"], seeded["trip_id"])
    s = get_settings()
    s.share_links_enabled = False
    try:
        assert client.post(f"/api/trips/{seeded['trip_id']}/share", json={}, headers=seeded["auth"]).status_code == 404
        assert _public(client, token).status_code == 404  # existing links stop working too
    finally:
        s.share_links_enabled = True
    assert _public(client, token).status_code == 200


# --- The WhatsApp text --------------------------------------------------------------------------------------------------

def test_the_text_has_place_names_a_link_and_whatsapp_formatting(client, seeded):
    j, token = _share(client, seeded["auth"], seeded["trip_id"])
    view = _public(client, token).json()
    text = j["text"]
    assert text.startswith("*") and "_Planned with Yaitro_" in text and f"Full plan: {j['url']}" in text
    named = [i["title"] for d in view["itinerary"]["days"] for i in d["items"] if i["kind"] in ("activity", "meal")]
    assert named and all(t in text for t in named)  # real names, never "Stop 1"
    assert "Stop 1" not in text and "Total ≈" in text and "(est.)" in text
    assert j["url"].startswith(get_settings().public_app_url.rstrip("/") + "/s/")


def test_the_text_is_made_fresh_every_time_and_stored_nowhere(client, seeded):
    j, token = _share(client, seeded["auth"], seeded["trip_id"])
    again = client.post(f"/api/trips/{seeded['trip_id']}/share-text", json={"token": token}, headers=seeded["auth"])
    assert again.status_code == 200 and again.json()["text"] == j["text"] and again.json()["url"] == j["url"]
    with SessionLocal() as db:
        cols = {c.name for c in ShareLink.__table__.columns}
    assert not {"text", "message", "body", "content"} & cols


def test_the_wa_me_link_has_no_phone_number_and_round_trips_the_text(client, seeded):
    j, _ = _share(client, seeded["auth"], seeded["trip_id"])
    u = urlparse(j["wa_url"])
    assert (u.scheme, u.netloc, u.path) == ("https", "wa.me", "/")  # no number: the user picks the chat (I9)
    assert "\n" not in j["wa_url"]
    # WhatsApp gets a short message whose link it shows as the trip's card; the full text stays in "text" for copying
    sent = parse_qs(u.query)["text"][0]
    assert sent.endswith(j["url"]) and len(sent) < 400 and sent.count("\n") <= 4 and len(j["text"]) > len(sent)


def test_a_long_trip_is_shortened_to_fit_a_message():
    day = {"index": 0, "date": "2026-10-17", "label": "Day 1", "items": [
        {"kind": "activity", "title": f"A very long place name number {n}", "time": "10:00", "cost": 500, "cost_state": "estimated",
         "meta": {}} for n in range(60)]}
    view = {"trip": {"title": "T", "currency": "INR", "travellers": 2, "start_date": "2026-10-17", "end_date": "2026-10-20"},
            "itinerary": {"title": "Long", "days": [{**day, "index": n, "label": f"Day {n + 1}", "date": f"2026-10-{17 + n}"}
                                                    for n in range(4)], "total_cost": 100000, "cost_per_person": 50000},
            "google": {"attribution": share_service.ATTRIBUTION}}
    text = share_service.share_text(view, "https://yaitro.example/s/abc")
    assert len(text) <= share_service.TEXT_LIMIT and "see the link" in text
    assert text.endswith(f"_{share_service.ATTRIBUTION}_") and "Full plan: https://yaitro.example/s/abc" in text


# --- UPI IDs (ADR 0006, decision 3) -------------------------------------------------------------------------------------

@pytest.mark.parametrize("good", ["asha@okhdfcbank", "a.b-c_d@ybl", "9876543210@paytm"])
def test_a_person_can_save_their_own_upi_id(client, good):
    auth, _ = _owner(client)
    r = client.patch("/api/auth/me/upi", json={"upi_id": good}, headers=auth)
    assert r.status_code == 200 and r.json()["upi_id"] == good
    assert client.get("/api/auth/me", headers=auth).json()["user"]["upi_id"] == good
    assert client.patch("/api/auth/me/upi", json={"upi_id": None}, headers=auth).json()["upi_id"] is None


@pytest.mark.parametrize("bad", ["no-at-sign", "@bank", "a@", "a b@bank", "x@ba nk", "a@b@c", "<script>@x"])
def test_an_invalid_upi_id_is_refused(client, bad):
    auth, _ = _owner(client)
    assert client.patch("/api/auth/me/upi", json={"upi_id": bad}, headers=auth).status_code == 422


def test_nobody_else_can_set_a_upi_id_and_none_is_stored_for_members(client, seeded):
    # the only way in is the signed-in person's own account; trip members have no UPI field at all
    assert client.patch("/api/auth/me/upi", json={"upi_id": "x@y"}).status_code == 401
    member_fields = set(seeded["riya"])
    assert "upi_id" not in member_fields and "upi" not in " ".join(member_fields)
    r = client.post(f"/api/trips/{seeded['trip_id']}/members", headers=seeded["auth"], json={"name": "Kabir", "upi_id": "k@bank"})
    assert "upi_id" not in r.json()
    settle = client.get(f"/api/trips/{seeded['trip_id']}/settlement", headers=seeded["auth"]).text
    assert "upi" not in settle.lower()  # the settlement API never carries one, so the browser-only typed ID can't reach it


def test_the_upi_switch_turns_the_feature_off(client):
    auth, _ = _owner(client)
    s = get_settings()
    s.upi_links_enabled = False
    try:
        assert client.patch("/api/auth/me/upi", json={"upi_id": "ab@bank"}, headers=auth).status_code == 404
    finally:
        s.upi_links_enabled = True

# --- send the plan to my own WhatsApp (the user's saved mobile, one tap; Yaitro sends nothing) ----------------------------------------------

def test_the_plan_can_be_opened_in_the_owners_own_whatsapp_chat(client):
    from urllib.parse import quote
    auth, me = _owner(client)
    r, _ = _share(client, auth, _planned(client, auth))
    digits = me["phone"].lstrip("+")
    assert r["wa_me_url"].startswith(f"https://wa.me/{digits}?text=")
    assert r["wa_me_url"].split("?text=", 1)[1] == r["wa_url"].split("?text=", 1)[1]  # the same message, ready to send
    assert quote(r["url"], safe="") in r["wa_me_url"]
    assert r["wa_url"].startswith("https://wa.me/?text=")  # the pick-a-chat link is unchanged
    # the message itself still holds no phone number (the number is only in the chat address the person opens)
    assert me["phone"] not in r["text"] and digits not in r["text"]


def test_a_person_with_no_saved_mobile_gets_no_own_chat_link_and_nobody_elses_number_is_used(client):
    from app.models.user import User
    auth = register(client)
    other = register(client)
    other_phone = client.get("/api/auth/me", headers=other).json()["user"]["phone"]
    uid = client.get("/api/auth/me", headers=auth).json()["user"]["id"]
    trip_id = _planned(client, auth)
    with SessionLocal() as db:
        u = db.get(User, __import__("uuid").UUID(uid))
        u.phone = None
        db.commit()
    r, _ = _share(client, auth, trip_id)
    assert r["wa_me_url"] is None and r["wa_url"].startswith("https://wa.me/?text=")
    assert other_phone.lstrip("+") not in json.dumps(r)


def test_wa_link_keeps_only_digits_of_the_number():
    assert share_service.wa_link("hi there", "+91 98765-43210") == "https://wa.me/919876543210?text=hi%20there"
    assert share_service.wa_link("hi", None) == "https://wa.me/?text=hi"
    assert share_service.wa_link("hi", "") == "https://wa.me/?text=hi"


def test_the_whatsapp_message_uses_only_symbols_whatsapp_desktop_can_show(client, seeded):
    """WhatsApp Desktop on Windows turns every emoji in a pre-filled message into "�" (📅 and ✈ alike); punctuation such as
    · – → ₹ comes through. So the message has no emoji or emoji symbols at all."""
    from urllib.parse import parse_qs, urlparse
    j, _ = _share(client, seeded["auth"], seeded["trip_id"])
    sent = parse_qs(urlparse(j["wa_url"]).query)["text"][0]
    emoji = [ch for ch in sent if ord(ch) >= 0x2300 or ch == "️"]  # symbols, dingbats, emoji and the emoji selector
    assert not emoji, emoji
    assert "→" in sent and sent.endswith(j["url"])
