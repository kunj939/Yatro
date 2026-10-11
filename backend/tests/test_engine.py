from datetime import date

from app.providers.demo import DemoPlacesProvider, DemoSearchProvider
from app.services.engine_types import Candidate
from app.services.intent import keyword_intent
from app.services.llm_service import allowed_facts, violations_in
from app.services.verification import trust_tier, verify_place
from app.utils.hours import extract_hours_from_text


def _cand(name):
    places = DemoPlacesProvider()
    city = places.resolve("Jaipur")
    p = next(a for a in places.attractions(city, []) if a.name == name)
    return Candidate(p.provider_place_id, p, "attraction")


def test_hours_extraction():
    assert extract_hours_from_text("Open 9:00 AM to 5:00 PM daily") == ("09:00", "17:00")
    assert extract_hours_from_text("Timings: 9.30 am - 5 pm") == ("09:30", "17:00")
    assert extract_hours_from_text("Tickets ₹50-200") is None


def test_trust_tiers():
    assert trust_tier("https://alberthalljaipur.gov.in/visit", None) == "official"
    assert trust_tier("https://www.royaljaipur.in/x", "https://www.royaljaipur.in") == "official"
    assert trust_tier("https://www.tripadvisor.in/a", None) == "reputable"
    assert trust_tier("https://random-blog.example.com", None) == "generic"


def test_conflicting_sources_are_uncertain():
    v = verify_place(DemoSearchProvider(), _cand("Hawa Mahal"), date(2026, 10, 3), "demo")
    assert v.state == "uncertain" and "disagree" in v.note
    assert any(s["url"].startswith("https://www.example-travel-guide.com") for s in v.sources)


def test_official_source_verifies():
    v = verify_place(DemoSearchProvider(), _cand("Albert Hall Museum"), date(2026, 10, 3), "demo")
    assert v.state == "verified_official"


def test_fact_guard_rejects_invented_numbers():
    payload = {"cost": "₹4,720", "distance": "2.1 km", "time": "09:30", "travel": "12 min"}
    allowed = allowed_facts(payload)
    assert violations_in("Walk 2.1 km, leaving at 09:30 (12 min).", allowed) == []
    # I1 (ADR 0004): no amount of money in AI text at all, even one copied exactly from the data
    assert violations_in("Spend ₹4,720 and walk 2.1 km.", allowed) == ["₹4,720"]
    assert violations_in("Entry is ₹999 and it opens at 8:15 am, 7 km away.", allowed) == ["₹999", "7 km", "8:15 am"]


def test_keyword_intent_fallback():
    c = keyword_intent("Increase budget by 1000 and skip Hawa Mahal, more relaxed please",
                       [{"id": "demo:jaipur:hawa-mahal", "name": "Hawa Mahal", "kind": "attraction"}])
    assert c["budget_change"] == {"mode": "increase", "amount": 1000.0}
    assert c["pace"] == "relaxed"
    assert c["exclude_place_ids"] == ["demo:jaipur:hawa-mahal"]


def test_trip_length_changes_are_understood():
    from app.services.intent import _day_change, keyword_intent
    assert _day_change("add one more day") == 1
    assert _day_change("extend the trip by 2 days") == 2
    assert _day_change("ek din aur") == 1
    assert _day_change("make it one day less") == -1
    assert _day_change("skip hawa mahal") == 0
    assert keyword_intent("add one more day", [])["extend_days"] == 1


def test_famous_landmarks_outrank_obscure_high_ratings():
    from app.services.ranking import famous
    assert famous(100_000) == 1.0 and famous(300) < 0.55