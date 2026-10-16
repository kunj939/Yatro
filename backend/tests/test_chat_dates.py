"""The chat understands trip dates (2026-10-16: "change it to 20 Oct to 26 Oct" became "6 days" on the old dates)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.intent import Intent, _drop_echoes, _merge, rule_intent
from tests.test_scenarios import _planned_agra, _say, plus_user  # noqa: F401

CTX = {"start_date": "2026-10-24", "end_date": "2026-10-25", "days": 2, "start_time": "07:00", "end_time": "22:00",
       "travellers": 1, "destination": "Udaipur"}


def _future(day: int, month: int) -> date:
    t = date.today()
    d = date(t.year, month, day)
    return d if d >= t else date(t.year + 1, month, day)


@pytest.mark.parametrize("text", ["make ki 20 oct to 26 oct", "I want to change trip dates. now change it to: 20 Oct to 26 Oct",
                                  "20-26 oct", "oct 20 to 26", "change dates to 20th october to 26th october",
                                  "20/10 to 26/10", "20 oct se 26 oct tak kar do", "from 20 October to 26 October"])
def test_a_date_range_in_many_phrasings(text):
    it = rule_intent(text, [], CTX)
    assert (it.start_date, it.end_date) == (_future(20, 10).isoformat(), _future(26, 10).isoformat())
    assert it.days_total is None and it.budget_change is None and it.travellers is None and it.start_time is None


def test_a_range_over_new_year_ends_next_year():
    it = rule_intent("28 dec to 3 jan", [], CTX)
    s, e = date.fromisoformat(it.start_date), date.fromisoformat(it.end_date)
    assert (s.month, s.day, e.month, e.day) == (12, 28, 1, 3) and e.year == s.year + 1


@pytest.mark.parametrize("text, start, end", [("postpone by 2 days", "2026-10-26", "2026-10-27"),
                                              ("move the trip a week later", "2026-10-31", "2026-11-01"),
                                              ("prepone by 1 day", "2026-10-23", "2026-10-24"),
                                              ("push it back by a week", "2026-10-31", "2026-11-01")])
def test_moving_the_trip(text, start, end):
    it = rule_intent(text, [], CTX)
    assert (it.start_date, it.end_date) == (start, end)


def test_a_single_new_start_and_the_old_time_rules_still_work():
    assert rule_intent("start on 22 oct", [], CTX).start_date == _future(22, 10).isoformat()
    assert rule_intent("22 oct se shuru karo", [], CTX).start_date == _future(22, 10).isoformat()
    later = rule_intent("start an hour later", [], CTX)
    assert later.start_shift == 60 and later.start_date is None
    assert rule_intent("budget 12000", [], CTX).budget_change == {"mode": "set", "amount": 12000.0}


def test_the_models_echoes_of_current_settings_are_dropped():
    ai = Intent(source="ai", start_time="07:00", end_time="22:00", travellers=1, days_total=6)
    rules = rule_intent("20 oct to 26 oct", [], CTX)
    out = _merge(_drop_echoes(ai, rules, CTX), rules)
    assert out.start_time is None and out.end_time is None and out.travellers is None
    assert out.days_total is None and out.start_date == rules.start_date  # the range, not "6 days"


def test_the_chat_moves_the_trip_to_the_dates_asked(client, plus_user):  # noqa: F811
    tid, trip = _planned_agra(client, plus_user)
    s = date.fromisoformat(trip["start_date"]) + timedelta(days=10)
    e = s + timedelta(days=2)
    r = _say(client, plus_user, tid, f"change the dates to {s.day} {s:%b} to {e.day} {e:%b}")
    assert r.status_code == 200, r.text
    out = r.json()["trip"]
    assert (out["start_date"], out["end_date"]) == (s.isoformat(), e.isoformat())
    note = out["itinerary"]["change_note"] or ""
    assert "dates" in note and "already" not in note


def test_past_dates_are_refused_kindly(client, plus_user):  # noqa: F811
    tid, trip = _planned_agra(client, plus_user)
    y = date.today() - timedelta(days=3)
    r = _say(client, plus_user, tid, f"start on {y.day} {y:%b} {y.year}")
    body = r.json()
    assert r.status_code == 200
    assert body["trip"]["start_date"] == trip["start_date"]
