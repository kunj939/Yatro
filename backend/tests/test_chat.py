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


def test_one_items_price_and_pasted_plan_text_never_change_the_trip(client, plus_user):  # noqa: F811
    """A traveller pasted a cab card and asked for it under ₹200: that is one cab, not a ₹200 trip, and the card's own
    words ("catch the train leaving at 20:10") are not a new start time or a train-only trip."""
    trip_id = _planned_trip(client, plus_user)
    trip = client.get(f"/api/trips/{trip_id}", headers=plus_user).json()
    items = [i for d in trip["itinerary"]["days"] for i in d["items"]]
    priced = next(i for i in items if (i.get("cost") or 0) + (i.get("travel_cost") or 0) > 0 and i["kind"] == "transport")

    cab = _ask(client, plus_user, trip_id, "cab is ₹358, reduce it under 200INR. kyuki itna jyada nhi lagta")
    assert cab["action"] == "answer" and "can't set one item's price" in cab["text"]
    pasted = _ask(client, plus_user, trip_id, f"{priced['title']}\n₹358 reduce it under 200INR")
    assert pasted["action"] == "answer" and pasted["text"].startswith(priced["title"])
    card = "\n".join(s for s in ("Travel", priced["title"], priced.get("subtitle")) if s)
    about = _ask(client, plus_user, trip_id, card)
    assert about["action"] == "answer" and priced["title"] in about["text"]
    change = _ask(client, plus_user, trip_id, f"{priced['title']}\nadd one more day")
    assert change == {"action": "change", "message": "add one more day"}  # only their own words go to the planner
    assert _ask(client, plus_user, trip_id, "budget 20000 kar do")["action"] == "change"  # the trip's money still is
    app.dependency_overrides.clear()


def test_ask_about_a_stop_answers_from_wikipedia_and_the_plan(client, plus_user, monkeypatch):  # noqa: F811
    """"Ask" on a stop: what the place is (Wikipedia's own introduction, linked), then how it fits this trip. Follow-ups
    keep the stop, "skip it" skips that stop, and a question that names a stop finds it without a tap."""
    from app.services import chat_service
    wiki = {"title": "Test Fort", "extract": "Test Fort is a hill fort built in 1726. It is known for its long walls and "
                                              "the view over the city.", "url": "https://en.wikipedia.org/wiki/Test_Fort"}
    from app.core.config import get_settings
    asked = []
    monkeypatch.setattr(get_settings(), "wikipedia_enabled", True)
    monkeypatch.setattr(chat_service.wikipedia, "about", lambda name, near=None, qid=None: asked.append(name) or wiki)
    trip_id = _planned_trip(client, plus_user)
    trip = client.get(f"/api/trips/{trip_id}", headers=plus_user).json()
    stop = next(i for d in trip["itinerary"]["days"] for i in d["items"] if i["kind"] == "activity")

    def ask(text, item_id=None):
        r = client.post(f"/api/trips/{trip_id}/chat", headers=plus_user,
                        json={"message": text, "history": [], "item_id": item_id})
        assert r.status_code == 200, r.text
        return r.json()

    famous = ask(f"Why is {stop['title']} famous?", stop["id"])
    assert famous["action"] == "answer" and "built in 1726" in famous["text"] and "In your plan: Day" in famous["text"]
    assert famous["source"]["url"] == wiki["url"] and famous["item_id"] == stop["id"] and asked[0] == stop["title"]
    long = ask("How long should I spend?", stop["id"])
    assert long["text"].startswith("In your plan: Day") and "1726" not in long["text"] and long["source"] is None
    assert ask("skip it", stop["id"]) == {"action": "change", "message": f"skip {stop['title']}"}
    named = ask(f"tell me about {stop['title']}?")
    assert named["item_id"] == stop["id"] and "1726" in named["text"]
    app.dependency_overrides.clear()


def test_only_the_owner_can_chat(client, plus_user):  # noqa: F811
    from .conftest import register
    trip_id = _planned_trip(client, plus_user)
    other = register(client, "Someone Else")
    assert client.post(f"/api/trips/{trip_id}/chat", headers=other, json={"message": "hi"}).status_code == 404
    app.dependency_overrides.clear()
