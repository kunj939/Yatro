"""Invariant I1, amended 2026-10-06 (ADR 0004): a fare is either a real provider fare, labelled verified, or an estimate
from the deterministic fare model, labelled "Estimated fare". AI never writes or changes an amount. A verified fare is
never downgraded silently."""
from datetime import UTC, datetime, timedelta

from app.providers.types import LLMResult
from app.services import llm_service as L
from app.services import train_fact_gate as G


def ticket(state, source_kind, observed_at=None, cost=500, **meta):
    return {"kind": "transport", "start_at": "2026-10-17T06:15", "end_at": "2026-10-17T10:00", "time": "06:15",
            "end_time": "10:00", "title": "Shatabdi Express 12002", "subtitle": "Departs 06:15", "cost": cost,
            "cost_state": state, "reasons": [], "explanation": None,
            "meta": {"mode": "train", "number": "12002", "to_name": "Agra Cantt (AGC)", "source_kind": source_kind,
                     "observed_at": observed_at, **meta}}


def serve(out):
    """The serializer's gates, in the order trip_service.item_out runs them."""
    return G.fare_item(G.label_item(G.redact_item(out)))


NOW = datetime.now(UTC).isoformat()


def test_an_estimated_fare_always_carries_the_estimated_label():
    out = serve(ticket("estimated", "live_api", NOW))
    assert out["meta"]["fare_label"] == "Estimated fare" and out["cost_state"] == "estimated"
    assert out["title"] == "Shatabdi Express 12002"  # a real timetabled train keeps its name; only the fare is estimated


def test_nothing_upstream_can_label_an_estimate_as_verified():
    out = serve(ticket("estimated", "live_api", NOW, fare_label="Verified fare"))
    assert out["meta"]["fare_label"] == "Estimated fare" and out["cost_state"] not in G.VERIFIED_FARE
    for state in ("uncertain", "unavailable", None):
        assert serve(ticket(state, "live_api", NOW))["meta"]["fare_label"] == "Estimated fare"


def test_station_guidance_shows_an_approximate_fare_with_no_train_attached():
    out = serve(ticket("estimated", "station_guidance"))
    assert out["meta"]["fare_label"] == "Approx. train fare for this distance"
    assert out["cost"] == 500  # the budget still works
    assert out["title"] == "Train to Agra Cantt (AGC) (timing estimated)" and out["meta"]["number"] is None
    assert out["time"] is None and "06:15" not in (out["subtitle"] or "")


def test_a_verified_fare_stays_verified():
    out = serve(ticket("verified_provider", "live_api", NOW))
    assert out["cost_state"] == "verified_provider" and out["meta"]["fare_label"] == "Verified fare"
    assert "fare_note" not in out["meta"]


def test_a_verified_fare_is_never_downgraded_silently():
    old = (datetime.now(UTC) - timedelta(days=50)).isoformat()  # its train is past the timetable limit: redacted
    out = serve(ticket("verified_provider", "live_api", old))
    assert out["time"] is None  # the train can't be shown...
    assert out["cost_state"] == "estimated" and out["meta"]["fare_label"] == "Approx. train fare for this distance"
    assert out["meta"]["fare_note"] == G.FARE_DOWNGRADED  # ...and the fare says why it is no longer verified


def test_sample_data_and_non_transport_items_are_left_alone():
    assert serve(ticket("demo", "sample"))["meta"]["fare_label"] == "Sample fare"
    meal = {"kind": "meal", "cost": 300, "cost_state": "estimated", "meta": {}}
    assert G.fare_item(meal) is meal
    assert G.fare_item({**ticket("estimated", "live_api", NOW), "cost": 0})["meta"].get("fare_label") is None


class AmountsLLM:
    live = True
    name = "fake"
    model = "fake"

    def complete(self, messages, json_schema=None, temperature=0.4):
        return LLMResult(text="", provider="fake", model="fake", data={
            "title": "Agra in a day", "summary": "Your whole day costs ₹4,720.",
            "items": [{"item_id": "a", "why": "The fare is about 650 rupees."}, {"item_id": "b", "why": "Quiet in the morning."}],
            "tips": ["Carry Rs. 200 for snacks.", "Start early."]})


def test_ai_text_containing_an_amount_is_rejected_even_when_copied_from_the_data():
    template = {"title": "T", "summary": "S", "whys": {"a": "W", "b": "X"}, "tips": ["tip"]}
    out, status, violations = L.narrate(AmountsLLM(), {"total": "₹4,720", "fare": 650}, template)
    assert out["title"] == "Agra in a day" and out["summary"] == "S"
    assert out["whys"] == {"a": "W", "b": "Quiet in the morning."}
    assert out["tips"] == ["Start early."]
    assert status == "partial" and violations == 3


def test_every_money_form_counts_as_an_amount():
    allowed = L.allowed_facts({"x": "₹1,200 $45 650 5000"})
    for text in ("₹1,200", "Rs. 1,200", "INR 1200", "$45", "650 rupees", "650/-", "₹5k", "45 dollars", "EUR 30"):
        assert L.violations_in(f"About {text} in all.", allowed), text
    assert L.violations_in("Leave at dawn and walk.", allowed) == []


def test_a_planned_trip_labels_every_transport_fare(client):
    trip = client.post("/api/demo/plan").json()
    items = [i for d in trip["itinerary"]["days"] for i in d["items"] if i["kind"] == "transport" and i["cost"]]
    assert items
    for i in items:
        label = i["meta"]["fare_label"]
        assert label in ("Verified fare", "Estimated fare", "Approx. train fare for this distance", "Sample fare")
        assert (label == "Verified fare") == (i["cost_state"] in G.VERIFIED_FARE)
