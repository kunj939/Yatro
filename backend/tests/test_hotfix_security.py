"""Hotfixes of 2026-10-07: X-Forwarded-For is never believed by default, and send-code (paid SMS) is limited, India-only and budgeted."""
from __future__ import annotations

import importlib
import runpy
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from starlette.requests import Request

from app.core import middleware as mw
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.gateway.budget import budgets
from app.main import app
from app.models.user import VerificationCode
from app.services import verification_codes as V

ROOT = Path(__file__).resolve().parents[1]


def from_peer(asgi_app, host: str):
    """The app, as seen when the connection comes from `host` (a proxy): the test client always says 'testclient'."""
    async def wrapped(scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "client": (host, 4000)}
        await asgi_app(scope, receive, send)
    return wrapped


def req(peer: str | None, *xff: str) -> Request:
    headers = [(b"x-forwarded-for", x.encode()) for x in xff]
    return Request({"type": "http", "headers": headers, "client": (peer, 5000) if peer else None, "method": "GET", "path": "/"})


# --- 3a: the client address ---------------------------------------------------------------------------------------------------------

def test_by_default_the_header_is_never_believed(monkeypatch):
    monkeypatch.setattr(get_settings(), "trusted_proxy_ips", "")
    assert mw.client_ip(req("203.0.113.9", "1.2.3.4")) == "203.0.113.9"
    assert mw.client_ip(req("10.0.0.1", "1.2.3.4")) == "10.0.0.1"  # even a private-looking peer: nothing is trusted unless listed
    assert mw.client_ip(req(None)) == "unknown"


def test_a_spoofed_header_from_an_untrusted_client_is_ignored_even_when_proxies_are_configured(monkeypatch):
    monkeypatch.setattr(get_settings(), "trusted_proxy_ips", "10.0.0.1")
    assert mw.client_ip(req("203.0.113.9", "8.8.8.8")) == "203.0.113.9"  # a direct client claiming to be someone else
    assert mw.client_ip(req("203.0.113.9", "8.8.8.8, 10.0.0.1")) == "203.0.113.9"  # ...or to be behind our proxy


def test_behind_one_proxy_the_address_it_saw_is_the_client(monkeypatch):
    monkeypatch.setattr(get_settings(), "trusted_proxy_ips", "10.0.0.1")
    assert mw.client_ip(req("10.0.0.1", "198.51.100.7")) == "198.51.100.7"
    # the client prepended its own lie; our proxy appended the truth on the right
    assert mw.client_ip(req("10.0.0.1", "6.6.6.6, 198.51.100.7")) == "198.51.100.7"
    assert mw.client_ip(req("10.0.0.1", "6.6.6.6", "7.7.7.7, 198.51.100.7")) == "198.51.100.7"  # two header lines, same rule


def test_behind_two_proxies_the_right_most_address_that_is_not_ours_is_the_client(monkeypatch):
    monkeypatch.setattr(get_settings(), "trusted_proxy_ips", "10.0.0.1, 10.0.0.2")
    assert mw.client_ip(req("10.0.0.1", "198.51.100.7, 10.0.0.2")) == "198.51.100.7"
    assert mw.client_ip(req("10.0.0.1", "1.1.1.1, 198.51.100.7, 10.0.0.2")) == "198.51.100.7"  # the left-most is a claim
    assert mw.client_ip(req("10.0.0.2", "198.51.100.7")) == "198.51.100.7"  # one hop of the two


def test_networks_ipv6_ports_and_garbage(monkeypatch):
    monkeypatch.setattr(get_settings(), "trusted_proxy_ips", "10.0.0.0/8, fd00::/8")
    assert mw.client_ip(req("10.3.4.5", "198.51.100.7:5555")) == "198.51.100.7"
    assert mw.client_ip(req("10.3.4.5", "[2001:db8::1]:443")) == "2001:db8::1"
    assert mw.client_ip(req("fd00::5", "2001:db8::9")) == "2001:db8::9"
    assert mw.client_ip(req("10.3.4.5", "198.51.100.7, not-an-address")) == "10.3.4.5"  # unreadable: nothing left of it is used
    assert mw.client_ip(req("10.3.4.5", "10.9.9.9, 10.8.8.8")) == "10.9.9.9"  # everything is ours: the left-most is the caller
    assert mw.client_ip(req("10.3.4.5")) == "10.3.4.5"  # no header at all
    monkeypatch.setattr(get_settings(), "trusted_proxy_ips", "nonsense, 10.0.0.1")  # a typo does not trust everyone
    assert mw.client_ip(req("203.0.113.9", "1.2.3.4")) == "203.0.113.9"


def test_a_spoofed_header_does_not_buy_a_new_rate_limit_bucket(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "rate_limit_enabled", True)
    monkeypatch.setattr(s, "trusted_proxy_ips", "")
    mw.rate_limiter.reset()
    try:
        with TestClient(app) as c:
            codes = [c.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x" * 9},
                            headers={"X-Forwarded-For": f"203.0.113.{i}"}).status_code for i in range(14)]
        assert codes[:10] == [401] * 10 and 429 in codes[10:]  # the 11th attempt is refused: a different header every time changed nothing
    finally:
        mw.rate_limiter.reset()


def test_behind_a_trusted_proxy_real_clients_get_their_own_buckets(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "rate_limit_enabled", True)
    monkeypatch.setattr(s, "trusted_proxy_ips", "10.0.0.1")
    mw.rate_limiter.reset()
    try:
        with TestClient(from_peer(app, "10.0.0.1")) as c:
            different = [c.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x" * 9},
                                headers={"X-Forwarded-For": f"198.51.100.{i}"}).status_code for i in range(14)]
            assert 429 not in different  # fourteen different people behind the proxy are not one person
            same = [c.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x" * 9},
                           headers={"X-Forwarded-For": "6.6.6.6, 198.51.100.200"}).status_code for i in range(14)]
            assert 429 in same  # ...and one person is one person, whatever they put on the left
    finally:
        mw.rate_limiter.reset()


def test_the_server_settings_use_the_same_list(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "")
    cfg = runpy.run_path(str(ROOT / "gunicorn.conf.py"))
    assert cfg["forwarded_allow_ips"] != "*" and cfg["worker_class"] == "app.core.server_worker.Worker"
    pytest.importorskip("fcntl", reason="gunicorn's worker classes need Linux; the CI run covers this part")
    from app.core import server_worker
    importlib.reload(server_worker)
    assert server_worker.Worker.CONFIG_KWARGS["proxy_headers"] is False  # off: nobody is trusted
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "10.0.0.1,10.0.0.2")
    assert runpy.run_path(str(ROOT / "gunicorn.conf.py"))["forwarded_allow_ips"] == "10.0.0.1,10.0.0.2"
    importlib.reload(server_worker)
    assert server_worker.Worker.CONFIG_KWARGS["proxy_headers"] is True
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "")
    importlib.reload(server_worker)
    entry = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
    assert "forwarded-allow-ips=\"*\"" not in entry and "--no-proxy-headers" in entry


# --- 3b: send-code ----------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def otp(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "otp_resend_seconds", 0)
    monkeypatch.setattr(s, "otp_per_phone_hour", 3)
    monkeypatch.setattr(s, "otp_per_phone_day", 5)
    monkeypatch.setattr(s, "otp_per_ip_hour", 0)  # every test client is one address; the address limit has its own test
    monkeypatch.setattr(s, "trusted_proxy_ips", "")
    monkeypatch.setattr(s, "require_phone_code", True)  # these tests are about the texted code, which is off by default
    return s


def phone() -> str:
    return f"+9170{uuid.uuid4().int % 10**8:08d}"


def ask(c, target: str, channel="phone", **headers):
    return c.post("/api/auth/send-code", json={"channel": channel, "target": target}, headers=headers)


def backdate(target: str | None = None, ip_hash: str | None = None, **delta):
    """Make the codes for a target (or from an address) older, in Python: SQL date arithmetic is not portable (SQLite vs PostgreSQL)."""
    with SessionLocal() as db:
        q = select(VerificationCode).where(VerificationCode.target == target) if target else select(VerificationCode).where(VerificationCode.requested_ip_hash == ip_hash)
        for row in db.scalars(q):
            row.created_at = V._aware(row.created_at) - timedelta(**delta)
        db.commit()


def test_three_codes_an_hour_per_number(client, otp):
    n = phone()
    assert [ask(client, n).status_code for _ in range(3)] == [200, 200, 200]
    r = ask(client, n)
    assert r.status_code == 429
    assert "several codes" in r.json()["error"]["message"] and r.json()["error"]["details"]["retry_in"] > 0
    assert "account" not in r.json()["error"]["message"].lower()  # nothing about whether the number has one
    backdate(n, minutes=61)  # an hour later the three are out of the window
    assert ask(client, n).status_code == 200


def test_five_codes_a_day_per_number(client, otp, monkeypatch):
    monkeypatch.setattr(otp, "otp_per_phone_hour", 0)  # leave only the daily limit
    n = phone()
    assert [ask(client, n).status_code for _ in range(5)] == [200] * 5
    r = ask(client, n)
    assert r.status_code == 429 and "tomorrow" in r.json()["error"]["message"]
    backdate(n, hours=25)
    assert ask(client, n).status_code == 200


def test_the_limits_are_the_same_for_a_number_with_an_account(client, otp):
    from tests.conftest import send_codes, signup
    owned, free = phone(), phone()
    email = f"{uuid.uuid4().hex[:10]}@example.com"
    codes = send_codes(client, email, owned, with_phone=True)
    assert signup(client, email=email, phone=owned, codes=codes).status_code == 201  # now this number has an account (the sign-up asked for one code)
    with SessionLocal() as db:
        used = db.scalar(select(func.count()).select_from(VerificationCode).where(VerificationCode.target == owned))
    with_account = [ask(client, owned).status_code for _ in range(4)]
    without = [ask(client, free).status_code for _ in range(4)]
    assert without == [200, 200, 200, 429]
    assert with_account == [200] * (3 - used) + [429] * (4 - (3 - used))  # the same wall, counted the same way


def test_sixty_seconds_between_texts_to_the_same_number(client, otp, monkeypatch):
    monkeypatch.setattr(otp, "otp_resend_seconds", 60)
    n = phone()
    assert ask(client, n).status_code == 200
    r = ask(client, n)
    assert r.status_code == 429 and 0 < r.json()["error"]["details"]["retry_in"] <= 60 and "wait" in r.json()["error"]["message"]
    backdate(n, seconds=61)
    assert ask(client, n).status_code == 200


def test_the_default_cooldown_is_sixty_seconds():
    from app.core.config import Settings
    assert Settings.model_fields["otp_resend_seconds"].default == 60  # (the test environment sets it to 0 so sign-ups do not wait)


def test_only_one_code_is_ever_live_for_a_number(client, otp):
    n = phone()
    first, second = ask(client, n).json()["dev_code"], ask(client, n).json()["dev_code"]
    with SessionLocal() as db:
        live = db.scalar(select(func.count()).select_from(VerificationCode).where(VerificationCode.target == n, VerificationCode.consumed_at.is_(None)))
        assert live == 1
        row, err = V.check(db, "phone", n, first if first != second else "000000")
        assert (row is None) if first != second else True
        row, err = V.check(db, "phone", n, second)
        assert row is not None and err is None


def test_ten_codes_an_hour_per_address_and_a_spoofed_header_does_not_help(client, otp, monkeypatch):
    monkeypatch.setattr(otp, "otp_per_ip_hour", 10)
    backdate(ip_hash=V.ip_hash("testclient"), days=2)  # earlier tests also came from this one test address
    ok = [ask(client, phone(), **{"X-Forwarded-For": f"203.0.113.{i}"}).status_code for i in range(10)]
    assert ok == [200] * 10
    r = ask(client, phone(), **{"X-Forwarded-For": "203.0.113.250"})  # a new number, a new claimed address: still this connection
    assert r.status_code == 429 and "connection" in r.json()["error"]["message"]
    assert ask(client, f"new{uuid.uuid4().hex[:6]}@example.com", channel="email").status_code == 429  # email counts toward the same address
    with SessionLocal() as db:  # and only a hash of the address is kept
        hashes = {h for (h,) in db.execute(select(VerificationCode.requested_ip_hash).where(VerificationCode.requested_ip_hash.is_not(None)))}
        assert V.ip_hash("testclient") in hashes and all("testclient" not in h for h in hashes)
    backdate(ip_hash=V.ip_hash("testclient"), minutes=61)
    assert ask(client, phone()).status_code == 200


def test_only_indian_mobile_numbers(client, otp):
    for bad in ("+44 7700 900123", "+971 50 123 4567", "+1 202 555 0100", "0044 7700 900123"):
        r = ask(client, bad)
        assert r.status_code == 422 and r.json()["error"]["details"][0]["field"] == "phone", bad
        assert "Indian" in r.json()["error"]["details"][0]["message"] or "country" in r.json()["error"]["details"][0]["message"]
    for bad in ("+91 5123456789", "+91 98765 4321", "12345"):
        assert ask(client, bad).status_code == 422
    assert ask(client, "+91 98765 43210").status_code == 200 and ask(client, "09876543210").status_code == 200  # two ways of writing one number
    with SessionLocal() as db:  # nothing was kept for a refused number
        assert not db.scalar(select(func.count()).select_from(VerificationCode).where(VerificationCode.target.like("+44%")))


# --- the SMS budget and the kill switch -----------------------------------------------------------------------------------------

@pytest.fixture
def paid_sms(monkeypatch, otp):
    """A paid provider that sends nothing: so the daily budget applies. A fresh budget day each test."""
    sent: list[str] = []
    monkeypatch.setattr(V, "_sms_provider", lambda: "fast2sms")
    monkeypatch.setattr(V, "_send_sms", lambda to, code, provider: sent.append(to) or V.Sent("fast2sms"))
    monkeypatch.setattr(get_settings(), "budget_mode", "enforce")
    monkeypatch.setattr(get_settings(), "sms_daily_limit", 3)
    day = f"test-sms-{uuid.uuid4().hex}"
    monkeypatch.setattr(budgets, "day_key", lambda: day)
    budgets.reset()
    yield sent
    budgets.reset()


def test_the_daily_text_cap_ends_in_a_calm_message_never_a_5xx(client, paid_sms):
    assert [ask(client, phone()).status_code for _ in range(3)] == [200, 200, 200]
    n = phone()
    r = ask(client, n)
    assert r.status_code == 429 and r.status_code < 500
    body = r.json()["error"]
    assert body["code"] == "sms_daily_limit" and "tomorrow" in body["message"] and "traceback" not in r.text.lower()
    assert len(paid_sms) == 3  # the fourth text was never sent
    with SessionLocal() as db:  # and no code was left behind for it
        assert not db.scalar(select(func.count()).select_from(VerificationCode).where(VerificationCode.target == n))
    assert budgets.used("sms") == 3
    assert ask(client, f"mail{uuid.uuid4().hex[:6]}@example.com", channel="email").status_code == 200  # email is not stopped by the text cap


def test_a_text_that_failed_to_send_does_not_use_up_the_cap(client, paid_sms, monkeypatch):
    from app.core.errors import AppError

    def boom(to, code, provider):
        raise AppError("We couldn't send the text just now.", code="delivery_failed", status_code=502)
    monkeypatch.setattr(V, "_send_sms", boom)
    for _ in range(5):
        assert ask(client, phone()).status_code == 502
    assert budgets.used("sms") == 0  # five failures, nothing counted: the next real texts still go


def test_the_console_provider_is_free_and_is_not_counted(client, otp, monkeypatch):
    monkeypatch.setattr(get_settings(), "budget_mode", "enforce")
    monkeypatch.setattr(get_settings(), "sms_daily_limit", 1)
    day = f"test-sms-{uuid.uuid4().hex}"
    monkeypatch.setattr(budgets, "day_key", lambda: day)
    budgets.reset()
    assert [ask(client, phone()).status_code for _ in range(4)] == [200] * 4  # SMS_PROVIDER=console in tests: nothing is sent or billed
    assert budgets.used("sms") == 0


def test_the_cap_has_a_default_of_fifty_and_zero_means_no_cap():
    from app.core.config import Settings
    assert Settings(_env_file=None).sms_daily_limit == 50
    s = get_settings()
    old = s.sms_daily_limit
    try:
        s.sms_daily_limit = 0
        assert budgets.limit("sms") is None
        s.sms_daily_limit = 50
        assert budgets.limit("sms") == 50
    finally:
        s.sms_daily_limit = old
    assert any(row["group"] == "sms" for row in budgets.status())  # it shows in the admin budgets view


def test_the_kill_switch_stops_texts_calmly_and_instantly(client, otp, monkeypatch):
    n = phone()
    assert ask(client, n).status_code == 200
    monkeypatch.setenv("KILL_SMS", "true")
    r = ask(client, phone())
    assert r.status_code == 429 and r.json()["error"]["code"] == "sms_paused" and "later" in r.json()["error"]["message"]
    monkeypatch.setenv("KILL_SMS", "false")
    assert ask(client, phone()).status_code == 200  # no restart needed to switch it back
    monkeypatch.delenv("KILL_SMS")
    assert ask(client, phone()).status_code == 200


def test_the_kill_switch_defaults_to_the_setting(monkeypatch):
    from app.core import killswitch
    monkeypatch.delenv("KILL_SMS", raising=False)
    monkeypatch.setattr(get_settings(), "kill_sms", True)
    assert killswitch.killed("sms") is True
    monkeypatch.setenv("KILL_SMS", "0")  # the live value wins over the setting
    assert killswitch.killed("sms") is False


def test_migration_0011_is_expand_only():
    import importlib.util
    spec = importlib.util.spec_from_file_location("migration_lint", ROOT / "scripts" / "migration_lint.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.check_all() == []  # the lint covers every migration from 0009 on, 0011 included


def test_production_needs_a_real_public_app_url_and_development_does_not():
    from app.core.config import Settings
    pg = "postgresql+psycopg://u:p@db:5432/tripai"
    strong = "x" * 40
    for bad in ("", "http://localhost:3000", "http://127.0.0.1:3000"):
        with pytest.raises(ValueError, match="PUBLIC_APP_URL"):
            Settings(_env_file=None, environment="production", jwt_secret=strong, database_url=pg, public_app_url=bad)
    assert Settings(_env_file=None, environment="production", jwt_secret=strong, database_url=pg, public_app_url="https://app.example.com")
    assert Settings(_env_file=None, environment="development").public_app_url == "http://localhost:3000"  # dev: unset is fine
