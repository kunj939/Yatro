"""Calendar feed for a trip (ADR 0015)."""
from app.main import app

from .conftest import register
from .test_scenarios import _payload, _use, plus_user  # noqa: F401  (plus_user is a fixture)

SC = dict(o="New Delhi", d="Jaipur", days=2, start="07:00", end="22:00", budget=30000, n=2, modes=["train", "cab"],
          food="veg", styles=["balanced"], stay="tier2", tt="normal")


def _planned_trip(client, headers) -> str:
    _use(SC)
    trip_id = client.post("/api/trips", headers=headers, json=_payload(SC)).json()["id"]
    assert client.post(f"/api/trips/{trip_id}/plan", headers=headers).json()["status"] == "ready"
    return trip_id


def test_subscribe_links_and_a_live_feed(client, plus_user):  # noqa: F811
    trip_id = _planned_trip(client, plus_user)
    links = client.post(f"/api/trips/{trip_id}/calendar", headers=plus_user).json()
    assert links["webcal_url"].startswith("webcal://") and links["google_url"].startswith("https://calendar.google.com/")
    assert client.post(f"/api/trips/{trip_id}/calendar", headers=plus_user).json() == links  # the same link every time

    path = links["feed_url"].split("://", 1)[1].split("/", 1)[1]
    r = client.get("/" + path)  # no sign-in: the token is the key
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    body = r.text
    assert body.startswith("BEGIN:VCALENDAR\r\n") and body.endswith("END:VCALENDAR\r\n")
    events = body.count("BEGIN:VEVENT")
    assert events >= 5 and events == body.count("END:VEVENT")
    assert "DTSTART:2026" in body and "Z\r\n" in body  # times in UTC
    assert "BEGIN:VALARM" in body  # a reminder before the train
    assert all(len(line.encode()) <= 75 for line in body.split("\r\n"))  # folded per RFC 5545
    assert "Free time" not in body

    assert client.delete(f"/api/trips/{trip_id}/calendar", headers=plus_user).status_code == 204
    assert client.get("/" + path).status_code == 404
    app.dependency_overrides.clear()


def test_download_is_for_the_owner_only(client, plus_user):  # noqa: F811
    trip_id = _planned_trip(client, plus_user)
    r = client.get(f"/api/trips/{trip_id}/calendar.ics", headers=plus_user)
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and "BEGIN:VEVENT" in r.text
    other = register(client, "Someone Else")
    assert client.get(f"/api/trips/{trip_id}/calendar.ics", headers=other).status_code == 404
    assert client.post(f"/api/trips/{trip_id}/calendar", headers=other).status_code == 404
    assert client.get("/api/cal/not-a-real-token-at-all-xyz.ics").status_code == 404
    app.dependency_overrides.clear()
