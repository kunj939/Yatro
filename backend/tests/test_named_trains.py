"""Regression for a real request: "take double decker train from Jaipur to Delhi" didn't work.

Two causes, both fixed: the search only looked at New Delhi station (3 Jaipur trains) and never at Delhi Sarai Rohilla,
where the Double Decker and eight other trains end; and a train named in a request was not understood as a train
at all. The timetable rows below have the shape RailRadar returns, with times from the real Saturday timetable.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.providers.rail_common import ENOUGH_TRAINS
from app.providers.railradar import RailRadarProvider
from app.providers.types import PlaceData, TransportData
from app.services import feasibility as FE
from app.services.engine_types import PlanInput

JAIPUR = PlaceData("google", "j", "Jaipur", "city", 26.9124, 75.7873)
NEW_DELHI = PlaceData("google", "d", "New Delhi", "city", 28.6139, 77.2090)


def row(number, name, dep, arr, minutes, frm="JP", to="NDLS", km=300):
    return {"train": {"number": number, "name": name, "type": "Superfast Express"},
            "from": {"code": frm, "departure": dep}, "to": {"code": to, "arrival": arr}, "distance": km, "duration": minutes}


NDLS_TRAINS = [row("12957", "Sabarmati BG - New Delhi Rajdhani Express", "02:50", "07:30", 280),
               row("19565", "Uttaranchal Express", "04:10", "10:25", 375),
               row("12016", "Daurai - New Delhi Shatabdi Express", "17:35", "22:30", 295)]
DEE_TRAINS = [row("12985", "Jaipur - Delhi Sarai Rohilla Double Decker Express", "05:45", "10:25", 280, to="DEE"),
              row("12216", "Bandra Terminus - Delhi Sarai Rohilla Garib Rath Express", "06:10", "11:00", 290, to="DEE")]
DEE_BACK = [row("12986", "Delhi Sarai Rohilla - Jaipur Double Decker Express", "17:25", "22:00", 275, frm="DEE", to="JP")]
NDLS_BACK = [row("12958", "New Delhi - Sabarmati BG Rajdhani Express", "19:55", "23:45", 230, frm="NDLS", to="JP")]


def provider(per_pair: dict, calls: list):
    rr = RailRadarProvider(None, "key", store=False)

    def fake_get(path, params, ttl):
        _, _, _, frm, to = path.split("/")
        calls.append((frm, to))
        return {"success": True, "data": {"trains": per_pair.get((frm, to), [])}}
    rr._get = fake_get
    return rr


def test_the_double_decker_is_found_through_delhi_sarai_rohilla():
    calls: list = []
    rr = provider({("JP", "NDLS"): NDLS_TRAINS, ("NDLS", "JP"): NDLS_BACK, ("JP", "DEE"): DEE_TRAINS,
                   ("DEE", "JP"): DEE_BACK}, calls)
    out = rr.intercity(JAIPUR, NEW_DELHI, date(2026, 10, 10), date(2026, 10, 10))
    names = {(t.direction, t.meta["number"], t.to_name if t.direction == "outbound" else t.from_name) for t in out}
    assert ("outbound", "12985", "Delhi Sarai Rohilla (DEE)") in names
    assert ("return", "12986", "Delhi Sarai Rohilla (DEE)") in names
    dd = next(t for t in out if t.meta["number"] == "12985")
    assert dd.service_class == "CC" and {t.service_class for t in out if t.meta["number"] == "12985"} == {"CC"}  # all chair car
    assert ("JP", "NDLS") in calls and ("JP", "DEE") in calls  # thin at New Delhi (3 trains), so the next terminal was asked


def test_a_well_served_pair_does_not_cost_more_calls():
    many = [row(str(12000 + i), f"Test Express {i}", f"{5 + i:02d}:00", f"{9 + i:02d}:30", 270) for i in range(ENOUGH_TRAINS)]
    calls: list = []
    rr = provider({("JP", "NDLS"): many, ("NDLS", "JP"): NDLS_BACK, ("JP", "DEE"): DEE_TRAINS}, calls)
    rr.intercity(JAIPUR, NEW_DELHI, date(2026, 10, 10), date(2026, 10, 10))
    assert ("JP", "DEE") not in calls  # enough trains already: no reason to spend more API calls


def test_one_train_serving_two_terminals_gives_two_distinct_options():
    both = [row("19565", "Uttaranchal Express", "04:10", "10:25", 375)]
    dee = [row("19565", "Uttaranchal Express", "04:10", "09:45", 335, to="DEE")]
    rr = provider({("JP", "NDLS"): both, ("JP", "DEE"): dee, ("NDLS", "JP"): [], ("DEE", "JP"): []}, [])
    out = [t for t in rr.intercity(JAIPUR, NEW_DELHI, date(2026, 10, 10), date(2026, 10, 10)) if t.direction == "outbound"]
    keys = {FE.option_key(t) for t in out}
    assert len(keys) == len(out) and {t.to_name for t in out} == {"New Delhi (NDLS)", "Delhi Sarai Rohilla (DEE)"}


# ------------------------------------------------------------------------------------------- matching and planning

def train(direction, number, name, dep, arr, frm="Jaipur Jn (JP)", to="Delhi Sarai Rohilla (DEE)", cls="CC"):
    day = datetime(2026, 10, 10)
    h, m = map(int, dep.split(":"))
    a, b = map(int, arr.split(":"))
    d = day.replace(hour=h, minute=m)
    r = day.replace(hour=a, minute=b)
    if r <= d:
        r += timedelta(days=1)
    return TransportData(direction=direction, mode="train", name=name, service_class=cls, depart_at=d, arrive_at=r,
                         duration_minutes=int((r - d).total_seconds() // 60), cost_per_person=Decimal(700), from_name=frm,
                         to_name=to, comfort=4, access_minutes=15, meta={"number": number, "source_kind": "live_api"})  # RailRadar rows


def plan_input(prefer=None):
    return PlanInput(origin_text="Jaipur", destination_text="New Delhi", origin_place_id=None, destination_place_id=None,
                     start=datetime(2026, 10, 10, 5), end=datetime(2026, 10, 10, 23), budget=Decimal(12000), travellers=2,
                     traveller_type="family", transport_modes=["train", "bus", "metro"], food="both", styles=["balanced"],
                     stay="no_stay", pinned_transport={"prefer": prefer} if prefer else {})


OPTIONS = [train("outbound", "12985", "Jaipur - Delhi Sarai Rohilla Double Decker Express", "05:45", "10:25"),
           train("outbound", "12957", "Sabarmati Bg - New Delhi Rajdhani Express", "02:50", "07:30", to="New Delhi (NDLS)", cls="3A"),
           train("outbound", "12016", "Daurai - New Delhi Shatabdi Express", "17:35", "22:30", to="New Delhi (NDLS)"),
           train("outbound", "15013", "Ranikhet Express", "15:25", "20:38", cls="2S"),
           train("return", "12986", "Delhi Sarai Rohilla - Jaipur Double Decker Express", "17:25", "22:00",
                 frm="Delhi Sarai Rohilla (DEE)", to="Jaipur Jn (JP)"),
           train("return", "12958", "New Delhi - Sabarmati Bg Rajdhani Express", "19:55", "23:45", frm="New Delhi (NDLS)",
                 to="Jaipur Jn (JP)", cls="3A"),
           train("return", "15715", "Ajmer Garib Nawaz Express", "13:46", "18:30", frm="Delhi Sarai Rohilla (DEE)",
                 to="Jaipur Jn (JP)", cls="2S")]


@pytest.mark.parametrize("wanted, number, expected", [
    ("double decker", "12985", True), ("Double-Decker", "12985", True), ("12985", "12985", True), ("12986", "12985", False),
    ("shatabdi", "12016", True), ("the Shatabdi Express", "12016", True), ("rajdhani", "12016", False),
    ("garib rath", "15715", False)])
def test_a_train_is_matched_by_name_or_number(wanted, number, expected):
    t = next(t for t in OPTIONS if t.meta["number"] == number)
    assert FE.service_matches(t, wanted) is expected


def test_the_named_train_is_used_both_ways_when_it_fits():
    inp = plan_input({"outbound": ["double decker"], "return": ["double decker"]})
    best = FE.rank_combos(FE.build_combos(OPTIONS, inp, JAIPUR, NEW_DELHI), inp)[0]
    assert best.outbound.meta["number"] == "12985" and best.ret.meta["number"] == "12986"
    assert FE.unmet_train_preferences(best.outbound, best.ret, OPTIONS, inp, "Jaipur", "New Delhi") == []


def test_a_train_that_cannot_fit_the_day_is_explained_not_silently_replaced():
    inp = plan_input({"outbound": ["shatabdi"]})  # leaves Jaipur at 17:35, arrives 22:30: no day trip with that
    combos = FE.rank_combos(FE.build_combos(OPTIONS, inp, JAIPUR, NEW_DELHI), inp)
    assert combos and combos[0].outbound.meta["number"] != "12016"  # the plan still works, without it
    note = FE.unmet_train_preferences(combos[0].outbound, combos[0].ret, OPTIONS, inp, "Jaipur", "New Delhi")[0]
    assert "Shatabdi" in note and "17:35" in note and "22:30" in note and "daylight" in note and "instead" in note


def test_a_train_that_does_not_exist_on_the_route_says_so_and_lists_what_does():
    inp = plan_input({"outbound": ["vande bharat"]})
    combos = FE.build_combos(OPTIONS, inp, JAIPUR, NEW_DELHI)
    note = FE.unmet_train_preferences(combos[0].outbound, combos[0].ret, OPTIONS, inp, "Jaipur", "New Delhi")[0]
    assert "couldn’t find a “vande bharat” train from Jaipur to New Delhi".replace("’", "'") in note.replace("’", "'")
    assert "Double Decker" in note or "Ranikhet" in note  # trains that do run are named


def test_the_refusal_names_the_pairing_that_came_closest():
    night_out = [t for t in OPTIONS if t.meta["number"] == "12016"]  # reaches New Delhi 22:30
    dawn_back = train("return", "99999", "Early Express", "06:00", "10:00", frm="New Delhi (NDLS)", to="Jaipur Jn (JP)")
    dawn_back.depart_at += timedelta(days=1)
    dawn_back.arrive_at += timedelta(days=1)
    inp = PlanInput(**{**plan_input().__dict__, "start": datetime(2026, 10, 10, 15), "end": datetime(2026, 10, 11, 11)})
    info = FE.time_infeasible(inp, night_out + [dawn_back], ["train"], NEW_DELHI)
    assert "Shatabdi" in info["main_conflict"] and "22:30" in info["main_conflict"] and "06:00" in info["main_conflict"]


def test_no_way_back_after_arriving_is_said_plainly():
    # It used to read "gets you back around 23:00, after your 23:00 return time", with no return at all.
    night_out = [t for t in OPTIONS if t.meta["number"] == "12016"]
    early_back = [train("return", "99999", "Early Express", "06:00", "10:00", frm="New Delhi (NDLS)", to="Jaipur Jn (JP)")]
    inp = PlanInput(**{**plan_input().__dict__, "start": datetime(2026, 10, 10, 15), "end": datetime(2026, 10, 10, 23)})
    info = FE.time_infeasible(inp, night_out + early_back, ["train"], NEW_DELHI)
    assert "22:30" in info["main_conflict"] and "no way back" in info["main_conflict"]
    assert any(a["label"] == "Add a day" for a in info["adjustments"])
