import os
import tempfile

_db = os.path.join(tempfile.mkdtemp(prefix="tripai-test-"), "test.db")
os.environ.update({"DATABASE_URL": os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_db}"), "DEMO_MODE": "true", "RATE_LIMIT_ENABLED": "false",
                   "ENVIRONMENT": "test", "LLM_PROVIDER": "template", "LOG_LEVEL": "WARNING",
                   # no email/SMS provider in tests: codes come back as dev_code; no resend wait between test users
                   "SMTP_HOST": "", "SMS_PROVIDER": "console", "OTP_RESEND_SECONDS": "0",
                   # the gateway tests switch budgets on themselves; the rest of the suite runs many plans a day
                   "BUDGET_MODE": "off", "IRCTC1_ASSUME_EXHAUSTED": "false",
                   # every test client is one address: the per-address code limit (10 an hour) is tested by its own tests
                   "OTP_PER_IP_HOUR": "0"})
# The suite is written around 2 free plans (the product default is higher): pinned here so a plain `pytest` works too
os.environ.setdefault("FREE_TRIP_PLANS", "2")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.models  # noqa: E402,F401
from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402

Base.metadata.create_all(engine)


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


_counter = {"n": 0}


def send_codes(client, email, phone, with_phone=False):
    """Step one of signing up, as the sign-up page does it: a code to the email (and to the phone only when REQUIRE_PHONE_CODE is on)."""
    codes = {}
    for channel, target in (("email", email), *((("phone", phone),) if with_phone else ())):
        r = client.post("/api/auth/send-code", json={"channel": channel, "target": target})
        assert r.status_code == 200, r.text
        codes[channel] = r.json()["dev_code"]
    return codes


def signup(client, name="Test User", email=None, phone=None, password="travel123", confirm=None, codes=None):
    """The whole sign-up: codes sent, then the account created with them. Returns the register response."""
    _counter["n"] += 1
    email = email or f"user{_counter['n']}@example.com"
    phone = phone or f"+9170{_counter['n']:08d}"  # a range no test hardcodes
    codes = codes or send_codes(client, email, phone)
    return client.post("/api/auth/register", json={
        "name": name, "email": email, "phone": phone, "password": password,
        "password_confirm": password if confirm is None else confirm,
        "email_code": codes["email"], **({"phone_code": codes["phone"]} if "phone" in codes else {})})


def register(client, name="Test User"):
    r = signup(client, name)
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def auth(client):
    return register(client)


def trip_payload(**over):
    from app.api.routes.demo import demo_date
    d = demo_date()
    body = {"origin": {"name": "Gurugram"}, "destination": {"name": "Jaipur"}, "start_date": d.isoformat(),
            "start_time": "05:00", "end_time": "01:30", "budget": 5000, "travellers": 2, "traveller_type": "couple",
            "transport_modes": ["train", "cab"], "food": "veg", "trip_styles": ["balanced"], "stay": "no_stay"}
    body.update(over)
    return body
