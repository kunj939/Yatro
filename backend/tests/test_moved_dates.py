"""Moving a trip to other dates (2026-10-16): "make it 3 days earlier" read as a 3-day length, and a moved trip found
Tuesday's train but no Thursday timetable, so the way back was only a cab and the plan "needed" ₹20,000 more."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from app.providers import rail_chain
from app.providers.types import PlaceData, TransportData
from app.services.intent import rule_intent

CTX = {"start_date": "2026-10-30", "end_date": "2026-11-01", "days": 3, "start_time": "07:00", "end_time": "22:00",
       "travellers": 3, "destination": "Rishikesh"}


def test_earlier_and_later_move_the_trip_and_keep_its_length():
    for text, start, end in (("make it 3 days earlier", "2026-10-27", "2026-10-29"), ("3 din pehle kar do", "2026-10-27", "2026-10-29"),
                             ("make it 2 days later", "2026-11-01", "2026-11-03"), ("a week earlier", "2026-10-23", "2026-10-25")):
        it = rule_intent(text, [], CTX)
        assert (it.start_date, it.end_date, it.days_total) == (start, end, None), text


def test_a_length_is_still_a_length():
    assert rule_intent("make it 3 days", [], CTX).days_total == 3
    assert rule_intent("add one more day", [], CTX).extend_days == 1
    assert rule_intent("start 2 hours later", [], CTX).start_shift == 120


JAIPUR = PlaceData(provider="t", provider_place_id="j", name="Jaipur", category="city", lat=26.91, lng=75.79)
RISHIKESH = PlaceData(provider="t", provider_place_id="r", name="Rishikesh", category="city", lat=30.09, lng=78.27)


def _t(direction, mode, day, hour, cost, name="", flexible=False):
    dep = datetime.combine(day, datetime.min.time()) + timedelta(hours=hour)
    return TransportData(direction=direction, mode=mode, name=name, depart_at=dep, arrive_at=dep + timedelta(hours=8),
                         duration_minutes=480, cost_per_person=Decimal(cost), flexible=flexible)


def test_a_missing_return_timetable_still_gets_a_train_estimate(monkeypatch):
    out_day, back_day = date(2026, 10, 27), date(2026, 10, 29)

    class Google:
        name = "google"

        def intercity_options(self, *a, **k):
            return [_t("outbound", "cab", out_day, 8, 4000), _t("return", "cab", back_day, 8, 4000)]

    class Chain:
        name, providers, last_issue = "railradar", [object()], None

        def intercity(self, *a):  # Tuesday's real train, nothing for Thursday
            return [_t("outbound", "train", out_day, 22, 1095, "Yoga Express 19031")]

    def estimate(origin, dest, depart_day, return_day, ws, we, stations=None, source=None):
        return [_t("outbound", "train", depart_day, 9, 900, flexible=True), _t("return", "train", return_day, 9, 900, flexible=True)]

    monkeypatch.setattr(rail_chain, "estimated_rail_options", estimate)
    t = rail_chain.RailAwareTransport(Google(), Chain())
    got = t.intercity_options(JAIPUR, RISHIKESH, out_day, back_day, datetime(2026, 10, 27, 7), datetime(2026, 10, 29, 22),
                              ["train", "bus", "cab"])
    trains = {(x.direction, x.name or "estimate") for x in got if x.mode == "train"}
    assert ("outbound", "Yoga Express 19031") in trains  # the real one stays
    assert ("return", "estimate") in trains  # and the way back is a train again, not only a cab
    assert ("outbound", "estimate") not in trains  # no estimate where a real timetable exists
