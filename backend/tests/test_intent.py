"""Whatever a traveller types into "Change the plan" must be understood, applied, or answered honestly.

The rule reader covers English and Hinglish with no network; the model path is exercised with a fake model, so
these tests need no API key and no internet.
"""
import pytest

from app.core.errors import ProviderUnavailable
from app.providers.base import LLMProvider
from app.providers.types import LLMResult
from app.services.intent import INTEREST_TAGS, _from_model, rule_intent, understand

POOL = [{"id": "p-taj", "name": "Taj Mahal", "kind": "attraction"}, {"id": "p-fort", "name": "Agra Fort", "kind": "attraction"},
        {"id": "p-itmad", "name": "Itmad-ud-Daula's Tomb", "kind": "attraction"},
        {"id": "p-mehtab", "name": "Mehtab Bagh", "kind": "attraction"}, {"id": "p-akbar", "name": "Akbar's Tomb", "kind": "attraction"},
        {"id": "p-pinch", "name": "Pinch of Spice", "kind": "restaurant"}]
CTX = {"destination": "Agra", "travellers": 2}


def read(message: str):
    return rule_intent(message, POOL, CTX)


@pytest.mark.parametrize("message, expected", [
    # times, in English and Hinglish
    ("start at 9am", {"start_time": "09:00"}),
    ("I want to start at 9:30 in the morning and be back by 8 pm", {"start_time": "09:30", "end_time": "20:00"}),
    ("back by 6", {"end_time": "18:00"}), ("come back by 9", {"end_time": "21:00"}), ("leave at 6 am", {"start_time": "06:00"}),
    ("subah 7 baje shuru karo", {"start_time": "07:00"}), ("raat 10 baje tak", {"end_time": "22:00"}),
    ("dopahar 2 baje nikalna", {"start_time": "14:00"}), ("reach home by 10 pm", {"end_time": "22:00"}),
    ("start 2 hours later", {"start_shift": 120}), ("end an hour earlier", {"end_shift": -60}),
    ("stay 2 hours longer", {"end_shift": 120}), ("start 1 hour later and stay 2 hours longer", {"start_shift": 60, "end_shift": 120}),
    # money
    ("budget 8000", {"budget_change": {"mode": "set", "amount": 8000.0}}),
    ("increase budget by 2000", {"budget_change": {"mode": "increase", "amount": 2000.0}}),
    ("reduce the budget by 500", {"budget_change": {"mode": "decrease", "amount": 500.0}}),
    ("make budget 10k", {"budget_change": {"mode": "set", "amount": 10000.0}}), ("₹6,500", {"budget_change": {"mode": "set", "amount": 6500.0}}),
    ("under 7000", {"budget_change": {"mode": "set", "amount": 7000.0}}), ("ek lakh budget", {"budget_change": {"mode": "set", "amount": 100000.0}}),
    ("mujhe 3000 rupaye me karna hai", {"budget_change": {"mode": "set", "amount": 3000.0}}),
    ("5000 per person", {"budget_change": {"mode": "set", "amount": 10000.0}}),  # two travellers
    # who is travelling, and for how long
    ("we are 4 people", {"travellers": 4}), ("add 2 more people", {"travellers_add": 2}), ("just me", {"travellers": 1, "traveller_type": "solo"}),
    ("hum 5 log", {"travellers": 5}), ("we will be 3 friends", {"travellers": 3, "traveller_type": "friends"}),
    ("add one more day", {"extend_days": 1}), ("ek din aur", {"extend_days": 1}), ("make it a 3 day trip", {"days_total": 3}),
    ("day trip please", {"days_total": 1}), ("stay 2 more days", {"extend_days": 2}),
    # pace, style, food, stay, transport
    ("More relaxed", {"pace": "relaxed"}), ("aaram se ghoomna hai", {"pace": "relaxed"}), ("bahut zyada walking hai", {"pace": "relaxed"}),
    ("See more places", {"pace": "packed"}), ("Make it cheaper", {"add_styles": ["budget"]}), ("sasta plan banao", {"add_styles": ["budget"]}),
    ("budget is tight", {"add_styles": ["budget"]}), ("More romantic", {"add_styles": ["romantic"]}),
    ("only veg food", {"food": "veg"}), ("non veg khana chahiye", {"food": "both"}), ("only non veg", {"food": "non_veg"}),
    ("hostel", {"stay": "hostel"}), ("hotel nahi chahiye", {"stay": "no_stay"}), ("5 star hotel", {"stay": "premium"}),
    ("by train", {"transport_only": ["train"]}), ("no bus", {"transport_remove": ["bus"]}), ("bus nahi", {"transport_remove": ["bus"]}),
    ("also allow flight", {"transport_add": ["flight"]}), ("metro se jana hai", {"transport_only": ["metro"]}),
    # places already in the plan
    ("Skip Agra Fort", {"exclude_place_ids": ["p-fort"]}), ("skip Taj Mahal", {"exclude_place_ids": ["p-taj"]}),
    ("remove itmad ud daula and mehtab bagh", {"exclude_place_ids": ["p-itmad", "p-mehtab"]}),
    ("I don't want to see Agra Fort", {"exclude_place_ids": ["p-fort"]}), ("Taj Mahal chahiye", {"include_place_ids": ["p-taj"]}),
    ("itimad ud daulah", {"include_place_ids": ["p-itmad"]}),  # a spelling variant
    ("taj", {"include_place_ids": ["p-taj"]}), ("agra fort and taj", {"include_place_ids": ["p-fort", "p-taj"]}),
    ("skip pinch of spice", {"exclude_place_ids": ["p-pinch"]}),
    # places that aren't in the plan: to be looked up by name
    ("add Fatehpur Sikri", {"add_place_names": ["fatehpur sikri"]}), ("I also want to visit Sikandra", {"add_place_names": ["sikandra"]}),
    ("Fatehpur Sikri bhi dekhna hai", {"add_place_names": ["fatehpur sikri"]}),
    ("I want to see Sikandra and Mathura", {"add_place_names": ["sikandra", "mathura"]}),
    ("add shopping at Sadar Bazaar", {"add_place_names": ["sadar bazaar"], "interests": ["market", "shopping"]}),
    ("remove Sadar Bazaar", {"unknown_skips": ["sadar bazaar"]}),
    # interests
    ("more shopping", {"interests": ["market", "shopping"]}), ("no temples", {"avoid_tags": ["temple", "spiritual"]}),
    ("temples nahi chahiye", {"avoid_tags": ["temple", "spiritual"]}), ("less museums", {"avoid_tags": ["museum", "gallery"]}),
    ("I love street food and photography", {"interests": ["street_food", "food", "cafe", "photography"]}),
    ("swap the museum for a park", {"avoid_tags": ["museum", "gallery"], "interests": ["nature", "lake", "garden"]}),
])
def test_what_was_asked_is_understood(message, expected):
    got = read(message)
    for field, value in expected.items():
        assert getattr(got, field) == value, f"{message!r}: {field} = {getattr(got, field)!r}, wanted {value!r}"


def test_several_changes_in_one_sentence_are_all_applied():
    got = read("make it cheaper and add Fatehpur Sikri and start at 8, no temples")
    assert got.add_styles == ["budget"] and got.add_place_names == ["fatehpur sikri"]
    assert got.start_time == "08:00" and got.avoid_tags == ["temple", "spiritual"]


@pytest.mark.parametrize("message", [
    "update my plan", "plan", "4 stars restaurant", "I have only 5 hours", "we are 2 days", "go to Agra in the morning",
    "see the sights", "add some good restaurants", "I want to go to Jaipur instead", "make it better", "hello"])
def test_nothing_is_misread_into_a_change(message):
    """Words that look like money, places or times but aren't must not change the trip."""
    assert not read(message).has_change(), message


def test_what_nothing_recognised_is_reported_not_dropped():
    got = read("add Fatehpur Sikri, and the kitchen sink must stay warm")
    assert got.add_place_names == ["fatehpur sikri"] and got.ignored  # the second half is named, not silently lost
    assert read("budget kam karo").needs  # understood, but "how much?" is missing
    assert read("skip lunch").unsupported and read("book the tickets").unsupported
    assert "Plan a trip" in read("I want to go to Jaipur instead").unsupported[0]


def test_one_items_price_is_not_the_trip_budget():
    for message, kind in (("Cab to Jaipur Jn ₹358 reduce it under 200INR", "cab"), ("hotel under 3000", "hotel"),
                          ("the train ticket is too costly, make it under ₹500", "ticket")):
        got = read(message)
        assert got.budget_change is None and got.item_price == kind and not got.has_change(), message
    assert read("trip budget under 3000").budget_change == {"mode": "set", "amount": 3000.0}


def test_a_mode_named_in_passing_is_not_a_mode_change():
    for message in ("Cab to Jaipur Jn (JP)", "From the hotel to catch the train", "the cab was late"):
        got = read(message)
        assert not (got.transport_only or got.transport_add or got.transport_remove), message
    assert read("go by train").transport_only == ["train"] and read("train").transport_only == ["train"]
    assert read("make it a 12 day trip").days_total == 12  # trips run up to 30 nights


def test_numbers_that_are_not_money_are_left_alone():
    for message in ("add 2 more days", "start at 9 and see 4 places", "for 4 people", "5 stars hotel"):
        assert read(message).budget_change is None, message


# ----------------------------------------------------------------------------------------------- the model path

class FakeLLM(LLMProvider):
    name, model, live = "fake", "fake-model", True

    def __init__(self, data=None, error=None):
        self.data, self.error = data, error

    def complete(self, messages, *, json_schema=None, temperature=0.3, max_tokens=None):
        if self.error:
            raise self.error
        return LLMResult(text="{}", data=self.data, provider="fake", model="fake-model")


def _model(**fields):
    base = {"budget_change": None, "days_total": None, "extend_days": 0, "pace": None, "add_styles": [], "remove_styles": [],
            "food": None, "stay": None, "transport_modes": [], "transport_action": None, "start_time": None, "end_time": None,
            "travellers": None, "traveller_type": None, "exclude_place_ids": [], "include_place_ids": [],
            "add_place_names": [], "interests": [], "avoid_interests": [], "reply": ""}
    return {**base, **fields}


def test_a_models_answer_is_checked_before_it_is_used():
    got = _from_model(_model(budget_change={"mode": "set", "amount": 9000}, stay="castle", pace="turbo", start_time="25:99",
                             include_place_ids=["p-taj", "made-up"], interests=["market", "not_a_tag"], travellers=99,
                             add_styles=["romantic", "spicy"], transport_modes=["train", "teleport"],
                             transport_action="add", add_place_names=["Fatehpur Sikri"]), POOL)
    assert got.budget_change == {"mode": "set", "amount": 9000.0}
    assert got.stay is None and got.pace is None and got.start_time is None and got.travellers is None
    assert got.include_place_ids == ["p-taj"] and got.interests == ["market"] and got.add_styles == ["romantic"]
    assert got.transport_add == ["train"] and got.add_place_names == ["Fatehpur Sikri"]
    assert set(got.interests) <= INTEREST_TAGS


def test_rules_fill_in_what_the_model_leaves_out():
    llm = FakeLLM(_model(add_place_names=["Fatehpur Sikri"]))
    got = understand(llm, "also visit Fatehpur Sikri and start at 9am", CTX, POOL)
    assert got.source == "ai" and got.add_place_names == ["Fatehpur Sikri", "fatehpur sikri"][:1] + ["fatehpur sikri"]
    assert got.start_time == "09:00"  # the model returned no time; the rule reader found it


@pytest.mark.parametrize("llm", [FakeLLM(error=ProviderUnavailable("down")), FakeLLM(data=None), FakeLLM(error=RuntimeError("boom"))])
def test_a_broken_model_never_breaks_a_request(llm):
    got = understand(llm, "start at 9am", CTX, POOL)
    assert got.start_time == "09:00" and got.source == "rules"


def test_the_models_own_reply_is_kept_for_a_request_it_could_not_turn_into_a_change():
    got = understand(FakeLLM(_model(reply="I can change budget, days, times, food and places.")), "make it magical", CTX, POOL)
    assert not got.has_change() and "budget" in got.reply


def test_a_place_name_is_not_also_read_as_a_wish_for_that_kind_of_place():
    got = read("also visit Sikandra Fort")
    assert got.add_place_names == ["sikandra fort"] and got.interests == [] and not got.avoid_tags
    got = read("add Sadar Bazaar and more forts")
    assert got.add_place_names == ["sadar bazaar"] and "fort" in got.interests
