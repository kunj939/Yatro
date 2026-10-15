"""A trip abroad is planned, estimated and explained in its own currency and timezone — never assumed to be
India. See app/utils/locale.py (detecting them), app/utils/money.py (formatting/converting), and the tz/currency
threading through app/providers/google.py and app/services/{feasibility,planner,alternatives}.py.
"""
from datetime import datetime
from decimal import Decimal

from app.providers.google import to_place
from app.services.engine_types import PlanInput
from app.services.feasibility import MinCost, budget_infeasible, combo_reason
from app.utils.money import fmt_money, to_currency


def test_fmt_money_uses_the_right_symbol_and_grouping():
    assert fmt_money(123456, "INR") == "₹1,23,456"  # Indian grouping only for INR
    assert fmt_money(123456, "USD") == "$123,456"  # ordinary 3-digit grouping elsewhere
    assert fmt_money(4500, "GBP") == "£4,500"
    assert fmt_money(500, "AED") == "AED 500"  # no common symbol: code + space
    assert fmt_money(-99.5, "EUR") == "-€99.50"


def test_to_currency_is_identity_for_inr_and_unknown_codes():
    assert to_currency(Decimal(1000), "INR") == Decimal(1000)
    assert to_currency(Decimal(1000), "XYZ") == Decimal(1000)  # never crashes on an unrecognised code
    assert to_currency(Decimal(1000), "GBP") != Decimal(1000)  # a real, different-scale conversion


def _inp(currency: str, **over) -> PlanInput:
    base = dict(origin_text="A", destination_text="B", origin_place_id=None, destination_place_id=None,
               start=datetime(2026, 6, 1, 9), end=datetime(2026, 6, 1, 21), budget=Decimal(1000), travellers=2,
               traveller_type="couple", transport_modes=["train"], food="both", styles=["balanced"], stay="no_stay",
               currency=currency)
    base.update(over)
    return PlanInput(**base)


def test_budget_infeasible_message_is_in_the_trips_own_currency():
    inp = _inp("GBP", budget=Decimal(50))
    mc = MinCost(transport=Decimal(80), stay=Decimal(0), food=Decimal(20), local=Decimal(10), nights=0, rooms=1)
    info = budget_infeasible(inp, mc, None, Decimal(0), Decimal(0))
    assert "£" in info["message"] and "₹" not in info["message"]
    assert "£" in info["main_conflict"]
    assert any("£" in a["label"] for a in info["adjustments"] if a["key"] == "higher_budget")


def test_budget_infeasible_stays_in_rupees_for_an_indian_trip():
    inp = _inp("INR", budget=Decimal(500))
    mc = MinCost(transport=Decimal(800), stay=Decimal(0), food=Decimal(200), local=Decimal(100), nights=0, rooms=1)
    info = budget_infeasible(inp, mc, None, Decimal(0), Decimal(0))
    assert "₹" in info["message"]


def test_combo_reason_formats_money_in_the_trips_currency():
    from app.providers.types import TransportData

    def t(direction, cost):
        return TransportData(direction=direction, mode="train", name="X", depart_at=datetime(2026, 6, 1, 10),
                             arrive_at=datetime(2026, 6, 1, 12), duration_minutes=120, cost_per_person=cost,
                             access_minutes=10)

    class C:
        local = False
        outbound = t("outbound", Decimal(40))
        ret = t("return", Decimal(40))
        cost = Decimal(90)
        comfort = 3

        def label(self):
            return "Train both ways"

        @property
        def dest_start(self):
            return datetime(2026, 6, 1, 12)

        @property
        def dest_end(self):
            return datetime(2026, 6, 1, 20)
    cheap = C()
    expensive = C()
    expensive.cost = Decimal(150)
    expensive.comfort = 5
    inp = _inp("EUR")
    text = combo_reason(expensive, [cheap, expensive], inp)
    assert "€" in text and "₹" not in text


def test_attraction_entry_fee_is_converted_for_a_non_indian_destination():
    raw = {"id": "p1", "displayName": {"text": "Test Museum"}, "types": ["museum"],
          "location": {"latitude": 51.5, "longitude": -0.12}}
    gbp = to_place(raw, "attraction", "GBP")
    inr = to_place(raw, "attraction", "INR")
    assert gbp.entry_fee is not None and inr.entry_fee is not None
    assert gbp.entry_fee != inr.entry_fee  # converted, not the same rupee number relabelled
    assert gbp.entry_fee_state == "estimated"


def test_restaurant_real_fare_is_kept_only_when_currency_matches():
    raw_gbp = {"id": "r1", "displayName": {"text": "Test Cafe"}, "types": ["restaurant"],
              "location": {"latitude": 51.5, "longitude": -0.12},
              "priceRange": {"startPrice": {"currencyCode": "GBP", "units": "20"},
                             "endPrice": {"currencyCode": "GBP", "units": "30"}}}
    as_gbp = to_place(raw_gbp, "restaurant", "GBP")
    assert as_gbp.restaurant.cost_state == "verified_provider" and as_gbp.restaurant.avg_cost_per_person == Decimal(25)
    as_inr = to_place(raw_gbp, "restaurant", "INR")  # the trip is INR; a GBP-quoted price isn't a real INR fare
    assert as_inr.restaurant.cost_state == "estimated"


def test_restaurant_estimate_fallback_is_converted_too():
    raw = {"id": "r2", "displayName": {"text": "No price data"}, "types": ["restaurant"],
          "location": {"latitude": 35.68, "longitude": 139.69}}
    jpy = to_place(raw, "restaurant", "JPY")
    inr = to_place(raw, "restaurant", "INR")
    assert jpy.restaurant.avg_cost_per_person != inr.restaurant.avg_cost_per_person
    assert jpy.restaurant.cost_state == "estimated"


# --- destination resolution and locale (a destination typed without picking a suggestion) --------------------

def _places(monkeypatch, suggestions=None):
    from types import SimpleNamespace

    from app.providers.google import GooglePlacesProvider
    from app.providers.types import Suggestion
    p = GooglePlacesProvider.__new__(GooglePlacesProvider)
    calls = SimpleNamespace(autocomplete=[], details=[], text_search=[])

    def autocomplete(q, limit=6):
        calls.autocomplete.append(q)
        return suggestions if suggestions is not None else []
    monkeypatch.setattr(p, "autocomplete", autocomplete)

    def cached(ns, key, ttl, fetch):
        calls.details.append(key[0])
        return {"id": key[0], "displayName": {"text": "Mussoorie"}, "types": ["locality"],
                "location": {"latitude": 30.4598, "longitude": 78.0644}, "formattedAddress": "Mussoorie, Uttarakhand, India"}
    monkeypatch.setattr("app.providers.google.cached", cached)

    def text_search(text, bias, max_results=1):
        calls.text_search.append(text)
        return [{"id": "TS1", "displayName": {"text": "Masuri"}, "types": ["locality"],
                 "location": {"latitude": 28.66, "longitude": 77.5}}]
    monkeypatch.setattr(p, "_text_search", text_search)
    return p, calls, Suggestion


def test_a_misspelt_destination_resolves_through_autocomplete_not_a_lookalike_village(monkeypatch):
    from app.providers.types import Suggestion
    p, calls, _ = _places(monkeypatch, [Suggestion("PID_MUSSOORIE", "Mussoorie", "Uttarakhand, India")])
    place = p.resolve("Masoorie")
    assert place.name == "Mussoorie" and calls.details == ["PID_MUSSOORIE"]
    assert calls.text_search == []  # the plain text search (which finds "Masuri") was never needed


def test_a_picked_suggestion_is_used_as_is(monkeypatch):
    p, calls, _ = _places(monkeypatch, [])
    p.resolve("Anything", "PID_PICKED")
    assert calls.autocomplete == [] and calls.details == ["PID_PICKED"]


def test_text_search_is_the_fallback_when_autocomplete_has_nothing(monkeypatch):
    p, calls, _ = _places(monkeypatch, [])
    assert p.resolve("Obscure place").name == "Masuri"
    assert calls.text_search == ["Obscure place"]


def _trip(**over):
    from types import SimpleNamespace
    base = dict(destination_name="London", destination_place_id="PID", destination_lat=None, destination_lng=None,
                timezone="Asia/Kolkata", currency="INR", budget=Decimal(100000), plan_count=0,
                current_itinerary=None, members=[SimpleNamespace(budget=Decimal(50000))])
    base.update(over)
    return SimpleNamespace(**base)


def _planner(dest_lat, dest_lng):
    from types import SimpleNamespace

    from app.providers.types import PlaceData
    from app.services.planner import Planner
    pl = Planner.__new__(Planner)
    pl.warnings = []
    pl.p = SimpleNamespace(places=SimpleNamespace(
        resolve=lambda name, pid: PlaceData("google", "x", name, "city", dest_lat, dest_lng)))
    return pl


def test_a_london_trip_gets_pounds_and_the_rupee_budget_is_converted_once():
    trip = _trip()
    pl = _planner(51.5074, -0.1278)
    pl._sync_locale(trip)
    assert trip.currency == "GBP" and trip.timezone == "Europe/London"
    assert trip.budget == Decimal(950)  # 1,00,000 rupees at the approximate rate, not "£100,000"
    assert trip.members[0].budget == Decimal(475)
    assert any("approximate rate" in w for w in pl.warnings)
    pl._sync_locale(trip)  # a second plan must not convert again
    assert trip.budget == Decimal(950)


def test_an_indian_trip_is_left_alone():
    trip = _trip(destination_name="Jaipur")
    pl = _planner(26.9124, 75.7873)
    pl._sync_locale(trip)
    assert trip.currency == "INR" and trip.budget == Decimal(100000) and pl.warnings == []


def test_a_replan_uses_the_stored_coordinates_without_resolving_again():
    trip = _trip(destination_lat=35.68, destination_lng=139.69, currency="JPY", timezone="Asia/Tokyo",
                 plan_count=1, budget=Decimal(180000))
    pl = _planner(0, 0)
    pl.p.places.resolve = lambda *a: (_ for _ in ()).throw(AssertionError("should not resolve again"))
    pl._sync_locale(trip)
    assert trip.currency == "JPY" and trip.budget == Decimal(180000)

def test_a_budget_typed_in_rupees_for_london_is_converted_to_pounds(client):
    """The wizard asked in ₹ (London not resolved yet) and the trip runs in £: ₹5,00,000 must become about £4,750,
    not £5,00,000. A budget typed in the destination's own money is kept as it is."""
    from tests.conftest import register, trip_payload
    user = register(client)
    london = {"name": "London", "lat": 51.5072, "lng": -0.1276}
    t = client.post("/api/trips", headers=user, json=trip_payload(destination=london, budget=500000, budget_currency="INR")).json()
    assert t["currency"] == "GBP" and 3000 < t["budget"] < 7000, t["budget"]
    same = client.post("/api/trips", headers=user, json=trip_payload(destination=london, budget=2000, budget_currency="GBP")).json()
    assert same["budget"] == 2000
