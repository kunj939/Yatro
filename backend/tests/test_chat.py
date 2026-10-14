"""The trip chat (ADR 0016): changes go to the re-planner, questions are answered from this trip only."""
from app.main import app

from .test_calendar import _planned_trip
from .test_scenarios import plus_user  # noqa: F401  (a fixture)


def _ask(client, headers, trip_id, text, history=None):
    r = client.post(f"/api/trips/{trip_id}/chat", headers=headers, json={"message": text, "history": history or []})
    assert r.status_code == 200, r.text
    return r.json()


def test_changes_go_to_the_planner_and_questions_get_answers(client, plus_user):  # noqa: F811
    trip_id = _planned_trip(client, plus_user)
    assert _ask(client, plus_user, trip_id, "add one more day")["action"] == "change"
    assert _ask(client, plus_user, trip_id, "budget 20000 kar do")["action"] == "change"

    train = _ask(client, plus_user, trip_id, "When is my train?")
    assert train["action"] == "answer" and "Day 1" in train["text"] and "Train" in train["text"]
    money = _ask(client, plus_user, trip_id, "kitna paisa bacha hai?")
    assert money["action"] == "answer" and "left" in money["text"]
    day2 = _ask(client, plus_user, trip_id, "what's on day 2?")
    assert day2["action"] == "answer" and day2["text"].startswith("Day 2")
    off = _ask(client, plus_user, trip_id, "who won the cricket match yesterday?")
    assert off["action"] == "answer" and "only help with this trip" in off["text"]
    app.dependency_overrides.clear()


def test_only_the_owner_can_chat(client, plus_user):  # noqa: F811
    from .conftest import register
    trip_id = _planned_trip(client, plus_user)
    other = register(client, "Someone Else")
    assert client.post(f"/api/trips/{trip_id}/chat", headers=other, json={"message": "hi"}).status_code == 404
    app.dependency_overrides.clear()
