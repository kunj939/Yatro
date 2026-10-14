"""Regression for a real plan (Jaipur -> Rishikesh, 2 days): a 14 h bus with 2 changes out, arrival at 22:00,
check-out at 07:25 for a 07:45 train an hour's drive away, and nothing seen in between. None of that may happen."""
from datetime import datetime
from decimal import Decimal

from app.providers.types import PlaceData, TransportData
from app.services import feasibility as FE
from app.services import plan_quality as Q
from app.services import planning_support as PS
from app.services.engine_types import DayFrame, PlanInput

JAIPUR = PlaceData("t", "j", "Jaipur", "city", 26.91, 75.79)
RISHIKESH = PlaceData("t", "r", "Rishikesh", "city", 30.09, 78.27)
HARIDWAR = (29.945, 78.169)
HOSTEL = (30.13, 78.32)  # Upper Tapovan, about 28 km by road from Haridwar station


def _inp(**over) -> PlanInput:
    base = dict(origin_text="Jaipur", destination_text="Rishikesh", origin_place_id=None, destination_place_id=None,
                start=datetime(2026, 10, 10, 7), end=datetime(2026, 10, 11, 22), budget=Decimal(10000), travellers=2,
                traveller_type="friends", transport_modes=["train", "bus"], food="veg", styles=["relaxed"],
                stay="hostel", currency="INR")
    base.update(over)
    return PlanInput(**base)


def _t(direction, mode, dep, arr, pp=500, cls=None, changes=0, frm=None, to=None):
    return TransportData(direction=direction, mode=mode, name=f"{mode} {dep:%H%M}", service_class=cls,
                         depart_at=dep, arrive_at=arr, duration_minutes=int((arr - dep).total_seconds() // 60),
                         cost_per_person=Decimal(pp), access_minutes=20, meta={"changes": changes},
                         from_lat=frm[0] if frm else None, from_lng=frm[1] if frm else None,
                         to_lat=to[0] if to else None, to_lng=to[1] if to else None)


def test_daytime_hours_not_clock_hours_are_what_count():
    assert FE.explore_minutes(datetime(2026, 10, 10, 21, 58), datetime(2026, 10, 11, 7, 25)) == 0
    assert FE.explore_minutes(datetime(2026, 10, 10, 6, 0), datetime(2026, 10, 10, 12, 0)) == 240


def test_arriving_at_night_and_leaving_at_dawn_is_not_a_trip():
    out = _t("outbound", "bus", datetime(2026, 10, 10, 7, 36), datetime(2026, 10, 10, 21, 58), changes=2)
    back = _t("return", "train", datetime(2026, 10, 11, 7, 45), datetime(2026, 10, 11, 18, 30), cls="2S")
    assert FE.build_combos([out, back], _inp(), JAIPUR, RISHIKESH) == []
    # ...and the explanation says why, with the real journey times
    info = FE.time_infeasible(_inp(), [out, back], ["train", "bus"], RISHIKESH)
    # the explanation names the pairing and its real times, not just "the journey is long"
    assert "arriving in Rishikesh at 21:58" in info["main_conflict"] and "no daylight" in info["main_conflict"]
    assert {a["key"] for a in info["adjustments"]} >= {"return_later", "nearby_destination"}


def test_a_long_bus_with_changes_and_ten_hours_in_2S_lose_to_a_proper_train():
    inp = _inp(start=datetime(2026, 10, 10, 5), end=datetime(2026, 10, 12, 22))
    bus = _t("outbound", "bus", datetime(2026, 10, 10, 5, 30), datetime(2026, 10, 10, 19, 50), pp=1280, changes=2)
    sleeper = _t("outbound", "train", datetime(2026, 10, 10, 5, 45), datetime(2026, 10, 10, 16, 30), pp=650, cls="SL")
    back = _t("return", "train", datetime(2026, 10, 12, 8, 0), datetime(2026, 10, 12, 18, 45), pp=650, cls="SL")
    seat = _t("return", "train", datetime(2026, 10, 12, 8, 0), datetime(2026, 10, 12, 18, 45), pp=300, cls="2S")
    best = FE.rank_combos(FE.build_combos([bus, sleeper, back, seat], inp, JAIPUR, RISHIKESH), inp)[0]
    assert best.outbound is sleeper and best.ret is back


def test_check_out_leaves_time_to_reach_an_early_train():
    back = _t("return", "train", datetime(2026, 10, 11, 7, 45), datetime(2026, 10, 11, 18, 30), frm=HARIDWAR)
    combo = FE.Combo(None, back, Decimal(0), Decimal(0), datetime(2026, 10, 10, 14, 0), datetime(2026, 10, 11, 7, 25),
                     (RISHIKESH.lat, RISHIKESH.lng), "Rishikesh", HARIDWAR, "Haridwar Jn (HW)", 3)

    class Hostel:
        point = HOSTEL
        place = PlaceData("t", "h", "The Hosteller", "hotel", *HOSTEL)
    last = PS.build_frames(combo, Hostel(), RISHIKESH)[-1]
    ride_min = (last.end - last.start).total_seconds() / 60
    assert ride_min >= 45  # leaving the hostel well before 07:25, not at it


def test_a_plan_with_nothing_to_see_is_blocked():
    night = DayFrame(0, datetime(2026, 10, 10).date(), datetime(2026, 10, 10, 21, 58), datetime(2026, 10, 10, 22, 30),
                     HARIDWAR, "Haridwar", HOSTEL, "Hostel")
    rep = Q.review([night], (RISHIKESH.lat, RISHIKESH.lng), None, HOSTEL, local_trip=False)
    assert not rep.ok and rep.all_travel


def test_return_suggestion_never_pushes_the_trip_a_day_further_each_time():
    # The end time had drifted to 07:12 on 16 Oct. A day train gets home 18:30 that day; an overnight one 06:47 on
    # 17 Oct. Suggesting the overnight one moved the end to 06:47 on the 17th — where nothing fits again — so each
    # re-plan added a day. The suggestion must be the soonest way home, and applying it must make the trip work.
    inp = _inp(start=datetime(2026, 10, 10, 7), end=datetime(2026, 10, 16, 7, 12))
    out = _t("outbound", "train", datetime(2026, 10, 10, 7, 40), datetime(2026, 10, 10, 15, 0))
    day = _t("return", "train", datetime(2026, 10, 16, 7, 45), datetime(2026, 10, 16, 18, 10))
    night = _t("return", "train", datetime(2026, 10, 16, 20, 0), datetime(2026, 10, 17, 6, 27))
    info = FE.time_infeasible(inp, [out, day, night], ["train"], RISHIKESH)
    fix = next(a for a in info["adjustments"] if a["key"] == "return_later")
    assert fix["apply"] == {"end_date": "2026-10-16", "end_time": "18:30"} and fix["label"] == "Return by 18:30"
    fixed = _inp(start=inp.start, end=datetime(2026, 10, 16, 18, 30))
    assert FE.build_combos([out, day, night], fixed, JAIPUR, RISHIKESH)  # the suggestion actually fits


def test_no_suggestion_stretches_a_trip_past_the_longest_allowed():
    # a trip already at the 30-night limit: a later train home would make it 31, so it isn't offered
    inp = _inp(start=datetime(2026, 10, 10, 7), end=datetime(2026, 11, 9, 7, 12))
    out = _t("outbound", "train", datetime(2026, 10, 10, 7, 40), datetime(2026, 10, 10, 15, 0))
    late = _t("return", "train", datetime(2026, 11, 9, 20, 0), datetime(2026, 11, 10, 6, 27))
    info = FE.time_infeasible(inp, [out, late], ["train"], RISHIKESH)
    assert not any(a["key"] == "return_later" for a in info["adjustments"])
