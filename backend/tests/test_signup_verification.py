"""Sign-up proves the email address and the mobile number with one-time codes; the password is typed twice."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.user import VerificationCode
from app.services import verification_codes as V
from tests.conftest import send_codes, signup


@pytest.mark.parametrize("raw, expected", [
    ("9876543210", "+919876543210"), ("098765 43210", "+919876543210"), ("+91 98765-43210", "+919876543210"),
    ("0091 9876543210", "+919876543210")])
def test_mobile_numbers_are_read_however_they_are_written(raw, expected):
    assert V.normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["12345", "+91 5123456789", "abc", "+0 12345678", "1234567890", "+44 7700 900123", "+971 50 123 4567",
                                 "+1 202 555 0100", "+977 9812345678", "0044 7700 900123"])
def test_impossible_mobile_numbers_are_refused(raw):
    with pytest.raises(ValueError):
        V.normalize_phone(raw)


def test_codes_are_masked_when_shown():
    assert V.mask("email", "kavitaa@outlook.com") == "k••••••@outlook.com"
    assert V.mask("phone", "+919876543210") == "+91 •••••• 3210"


@pytest.fixture
def phone_codes_on():
    """The text-message code is off by default (sign-up proves the email only); some tests cover it for when an SMS provider exists."""
    s = get_settings()
    s.require_phone_code = True
    yield
    s.require_phone_code = False


def test_a_new_account_has_a_verified_email_and_a_mobile_on_file(client):
    r = signup(client, "Asha", email="asha@example.com", phone="98111 22333")
    assert r.status_code == 201, r.text
    user = r.json()["user"]
    assert user["phone"] == "+919811122333" and user["email_verified"] and not user["phone_verified"]  # kept for WhatsApp, not code-checked
    # ...and can sign in with either
    for ident in ("asha@example.com", "+91 98111 22333", "9811122333"):
        assert client.post("/api/auth/login", json={"email": ident, "password": "travel123"}).status_code == 200


def test_a_mobile_number_belongs_to_one_account_however_it_is_written(client):
    assert signup(client, email="first@example.com", phone="+919800000301").status_code == 201
    for written in ("+919800000301", "98000 00301", "098000 00301", "+91 98000-00301"):
        r = signup(client, email=f"other{abs(hash(written))}@example.com", phone=written)
        assert r.status_code == 409 and r.json()["error"]["code"] == "phone_taken", written
        assert r.json()["error"]["details"][0]["field"] == "phone"  # the form sends the person back to the mobile field
    # a different email with the SAME email is still refused as the email, and a fresh number works
    assert signup(client, email="first@example.com", phone="+919800000302").json()["error"]["code"] == "email_taken"
    assert signup(client, email="second@example.com", phone="+919800000302").status_code == 201


def test_the_database_itself_refuses_a_second_account_on_one_number(client):
    """Two sign-ups at the same instant both pass the check; the unique key on users.phone settles it, and says which was taken."""
    from sqlalchemy.exc import IntegrityError

    from app.models.user import User
    assert signup(client, email="race1@example.com", phone="+919800000303").status_code == 201
    with SessionLocal() as db:
        db.add(User(email="race2@example.com", name="R", password_hash="x", phone="+919800000303"))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


def test_a_wrong_email_code_is_refused_and_counts_down(client):
    codes = send_codes(client, "wrong@example.com", "+919800000101")
    bad = {"email": "000000" if codes["email"] != "000000" else "111111"}
    r = signup(client, email="wrong@example.com", phone="+919800000101", codes=bad)
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "invalid_code" and err["details"] == [
        {"field": "email_code", "message": "That code isn't right. 4 tries left."}]
    assert signup(client, email="wrong@example.com", phone="+919800000101", codes=codes).status_code == 201


def test_too_many_wrong_tries_kill_the_code(client):
    codes = send_codes(client, "tries@example.com", "+919800000102")
    wrong = "000000" if codes["email"] != "000000" else "111111"
    for _ in range(get_settings().otp_max_attempts):
        signup(client, email="tries@example.com", phone="+919800000102", codes={"email": wrong})
    r = signup(client, email="tries@example.com", phone="+919800000102", codes=codes)  # now even the right one fails
    assert r.status_code == 400 and "Too many wrong tries" in r.json()["error"]["message"]


def test_an_expired_code_is_refused(client):
    codes = send_codes(client, "late@example.com", "+919800000103")
    with SessionLocal() as db:
        db.execute(update(VerificationCode).where(VerificationCode.target == "late@example.com")
                   .values(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
        db.commit()
    r = signup(client, email="late@example.com", phone="+919800000103", codes=codes)
    assert r.status_code == 400 and "expired" in r.json()["error"]["message"]


def test_a_new_code_replaces_the_old_one(client):
    first = send_codes(client, "again@example.com", "+919800000104")
    second = send_codes(client, "again@example.com", "+919800000104")
    if first["email"] != second["email"]:
        assert signup(client, email="again@example.com", phone="+919800000104", codes=first).status_code == 400
    assert signup(client, email="again@example.com", phone="+919800000104", codes=second).status_code == 201


def test_the_two_passwords_must_match(client):
    r = signup(client, email="twice@example.com", phone="+919800000105", password="travel123", confirm="travel124")
    assert r.status_code == 422
    assert {"field": "password_confirm", "message": "Value error, The two passwords don't match."} in r.json()["error"]["details"]


def test_no_text_is_sent_unless_the_phone_code_is_switched_on(client):
    r = client.post("/api/auth/send-code", json={"channel": "phone", "target": "+919800000108"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "phone_code_off"
    assert "dev_code" not in r.text


def test_the_answer_does_not_say_whether_an_email_has_an_account(client):
    assert signup(client, email="owner@example.com", phone="+919800000106").status_code == 201
    t_mail = client.post("/api/auth/send-code", json={"channel": "email", "target": "OWNER@example.com"})
    f_mail = client.post("/api/auth/send-code", json={"channel": "email", "target": "nobody-here@example.com"})
    assert t_mail.status_code == f_mail.status_code == 200 and set(t_mail.json()) == set(f_mail.json())
    # it is said only at sign-up, to someone holding a valid code; with a wrong code the answer is the same for everyone
    bad = signup(client, email="owner@example.com", phone="+919800000199", codes={"email": "000000"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_code"
    ok = signup(client, email="owner@example.com", phone="+919800000199", codes={"email": t_mail.json()["dev_code"]})
    assert ok.status_code == 409 and ok.json()["error"]["code"] == "email_taken"


def test_with_the_phone_code_switched_on_both_codes_are_needed(client, phone_codes_on):
    codes = send_codes(client, "both@example.com", "+919800000110", with_phone=True)
    only_email = signup(client, email="both@example.com", phone="+919800000110", codes={"email": codes["email"]})
    assert only_email.status_code == 400 and only_email.json()["error"]["details"][0]["field"] == "phone_code"
    codes = send_codes(client, "both@example.com", "+919800000110", with_phone=True)
    wrong = "000000" if codes["phone"] != "000000" else "111111"
    r = signup(client, email="both@example.com", phone="+919800000110", codes={"email": codes["email"], "phone": wrong})
    assert r.status_code == 400 and r.json()["error"]["details"] == [{"field": "phone_code", "message": "That code isn't right. 4 tries left."}]
    ok = signup(client, email="both@example.com", phone="+919800000110", codes=codes)
    assert ok.status_code == 201 and ok.json()["user"]["phone_verified"] is True


def test_asking_again_too_soon_is_refused():
    from fastapi.testclient import TestClient
    from app.main import app
    s = get_settings()
    s.otp_resend_seconds = 30
    try:
        with TestClient(app) as c:
            assert c.post("/api/auth/send-code", json={"channel": "email", "target": "soon@example.com"}).status_code == 200
            r = c.post("/api/auth/send-code", json={"channel": "email", "target": "soon@example.com"})
            assert r.status_code == 429 and 0 < r.json()["error"]["details"]["retry_in"] <= 30
    finally:
        s.otp_resend_seconds = 0


def test_production_never_pretends_to_send(client):
    s = get_settings()
    s.environment = "production"
    try:
        r = client.post("/api/auth/send-code", json={"channel": "email", "target": "prod-check@example.com"})
        assert r.status_code == 503 and r.json()["error"]["code"] == "delivery_unavailable"
        assert "dev_code" not in r.text
    finally:
        s.environment = "test"

def test_a_broken_mail_server_falls_back_to_the_dev_code_in_development_only(monkeypatch):
    """A blocked or misconfigured mail server must not lock a developer out of sign-up; production still refuses honestly."""
    import smtplib

    from app.core.errors import AppError
    s = get_settings()
    saved = (s.smtp_host, s.smtp_port, s.environment)

    class Down:
        def __init__(self, *a, **k):
            raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
    monkeypatch.setattr(V.smtplib, "SMTP", Down)
    try:
        s.smtp_host, s.smtp_port = "smtp.example.invalid", 587
        s.environment = "development"
        assert V.send_email_code("a@example.com", "123456").console
        s.environment = "production"
        with pytest.raises(AppError) as e:
            V.send_email_code("a@example.com", "123456")
        assert e.value.code == "delivery_failed" and e.value.status_code == 502
    finally:
        s.smtp_host, s.smtp_port, s.environment = saved