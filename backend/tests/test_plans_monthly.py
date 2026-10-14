"""ADR 0013: free plans come back every month, and Plus can be bought for a year."""
from datetime import UTC, datetime

from sqlalchemy import update

from app.db.session import SessionLocal
from app.models.user import User
from tests.conftest import register, trip_payload


def _quota(client, h):
    return client.get("/api/subscription", headers=h).json()


def _me_id(client, h):
    return client.get("/api/auth/me", headers=h).json()["user"]["id"]


def test_free_plans_start_again_in_a_new_month(client):
    h = register(client)
    trip = client.post("/api/trips", json=trip_payload(), headers=h).json()
    assert client.post(f"/api/trips/{trip['id']}/plan", json={}, headers=h).json()["status"] == "ready"
    q = _quota(client, h)
    assert q["free_plans_used"] == 1 and q["free_plans_reset"].endswith("-01")
    with SessionLocal() as db:  # pretend the plan was used last month
        db.execute(update(User).where(User.email == client.get("/api/auth/me", headers=h).json()["user"]["email"])
                   .values(free_period="2000-01"))
        db.commit()
    q = _quota(client, h)
    assert q["free_plans_used"] == 0 and q["free_plans_left"] == q["free_plans_total"]


def test_plus_can_be_bought_for_a_year(client):
    h = register(client)
    q = _quota(client, h)
    assert q["prices"]["plus_yearly_inr"] < 12 * q["prices"]["plus_monthly_inr"]
    q = client.post("/api/subscription/upgrade", json={"product": "plus_yearly"}, headers=h).json()
    assert q["plan"] == "plus"
    days = (datetime.fromisoformat(q["current_period_end"]) - datetime.now(UTC)).days
    assert 360 <= days <= 366
