"""The quality gate and the +/- 60 minute departure window."""
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.providers.types import TransportData
from app.services import plan_quality as Q
from app.services.engine_types import DayFrame, Leg, Stop
from app.services.feasibility import _valid_outbound, _valid_return


def test_meal_warnings_become_one_line():
    raw = [f"No {m} stop matched your food preference and timing on {d}." for d in ("Sun 18 Oct", "Mon 19 Oct", "Tue 20 Oct")
           for m in ("breakfast", "lunch", "snack", "dinner")]
    out = Q.consolidate_warnings(raw + ["Some current information could not be verified."])
    assert len(out) == 2
    assert out[0].startswith("Couldn't fit every meal") and "lunch on Sun 18 Oct, Mon 19 Oct and Tue 20 Oct" in out[0]
    assert "snack on" not in out[0]


def test_countries_are_too_broad_but_cities_and_states_are_fine():
    assert Q.too_broad(["country", "political"])
    assert not Q.too_broad(["locality", "political"])
    assert not Q.too_broad(["administrative_area_level_1", "political"])  # Goa, Kerala...


def _frame(i, stops, hours=12):
    start = datetime(2026, 10, 3 + i, 8, 30)
    return DayFrame(i, start.date(), start, start + timedelta(hours=hours), (0, 0), "a", (0, 0), "b", stops=stops)


def _stop(h, km=1):
    s = Stop(kind="activity", cand=None, name="x", duration=60)
    s.start, s.end = datetime(2026, 10, 3, h), datetime(2026, 10, 3, h + 1)
    s.leg = Leg(minutes=20, distance_m=km * 1000, mode="cab", cost=Decimal(100), state="estimated")
    return s


def test_empty_trip_is_blocked():
    rep = Q.review([_frame(i, []) for i in range(5)], (40.0, -100.0), (41.0, -87.0), None, False)
    assert not rep.ok and any("far from the destination" in b for b in rep.blocking)
    assert any("nothing planned" in b for b in rep.blocking)


def test_far_jump_between_stops_is_blocked():
    rep = Q.review([_frame(0, [_stop(10), _stop(12, km=400)])], (26.9, 75.8), None, None, True)
    assert not rep.ok and "400 km" in rep.blocking[0]


def _t(direction, dep, arr):
    return TransportData(direction=direction, mode="train", name="", depart_at=dep, arrive_at=arr,
                         duration_minutes=int((arr - dep).total_seconds() // 60), cost_per_person=Decimal(100),
                         access_minutes=20)


def test_trains_within_an_hour_of_the_window_are_allowed():
    class Inp:
        start, end = datetime(2026, 10, 3, 6, 0), datetime(2026, 10, 3, 22, 0)
    d = date(2026, 10, 3)
    early = _t("outbound", datetime.combine(d, datetime.min.time()) + timedelta(hours=5, minutes=35),
               datetime(2026, 10, 3, 9, 30))
    too_early = _t("outbound", datetime(2026, 10, 3, 4, 30), datetime(2026, 10, 3, 8, 30))
    late_back = _t("return", datetime(2026, 10, 3, 19, 0), datetime(2026, 10, 3, 22, 30))
    assert _valid_outbound(early, Inp) and not _valid_outbound(too_early, Inp)
    assert _valid_return(late_back, Inp)


def test_public_transport_beats_cab_for_intercity_trips():
    from app.services.feasibility import Combo, public_first

    def t(mode, direction):
        return TransportData(direction=direction, mode=mode, name="", depart_at=datetime(2026, 10, 3, 7),
                             arrive_at=datetime(2026, 10, 3, 11), duration_minutes=240,
                             cost_per_person=Decimal(100))

    def combo(a, b):
        c = Combo.__new__(Combo)
        c.outbound, c.ret = t(a, "outbound"), t(b, "return")
        return c
    combos = [combo("cab", "cab"), combo("train", "cab"), combo("train", "train"), combo("bus", "train")]

    class Inp:
        transport_modes = ["train", "cab"]
    kept = public_first(combos, Inp)
    assert [(c.outbound.mode, c.ret.mode) for c in kept] == [("train", "train"), ("bus", "train")]

    Inp.transport_modes = ["cab"]  # asked only for a cab: respected
    assert len(public_first(combos, Inp)) == 4
    Inp.transport_modes = ["train"]
    only_road = [combo("cab", "cab")]  # no train fits: the cab is still offered rather than nothing
    assert public_first(only_road, Inp) == only_road


def test_empty_day_is_flagged_so_the_planner_can_blame_the_budget():
    rep = Q.review([_frame(0, [])], (26.9, 75.8), None, None, True)
    assert not rep.ok and rep.only_empty_days and rep.empty_days == 1



def test_trains_near_the_requested_time_and_no_4am_checkout():
    from app.services.feasibility import Combo, timing_penalty

    class Inp:
        start, end = datetime(2026, 10, 3, 7, 0), datetime(2026, 10, 5, 11, 0)

    def t(direction, dep, arr):
        x = TransportData(direction=direction, mode="train", name="", depart_at=dep, arrive_at=arr,
                          duration_minutes=int((arr - dep).total_seconds() // 60), cost_per_person=Decimal(100))
        x.access_minutes = 20
        return x

    def combo(out, ret):
        c = Combo.__new__(Combo)
        c.outbound, c.ret = out, ret
        return c
    ret_ok = t("return", datetime(2026, 10, 5, 6, 30), datetime(2026, 10, 5, 10, 45))
    morning = combo(t("outbound", datetime(2026, 10, 3, 7, 30), datetime(2026, 10, 3, 12, 0)), ret_ok)
    noon = combo(t("outbound", datetime(2026, 10, 3, 12, 48), datetime(2026, 10, 3, 17, 50)), ret_ok)
    four_am = combo(morning.outbound, t("return", datetime(2026, 10, 5, 4, 30), datetime(2026, 10, 5, 8, 47)))
    assert timing_penalty(morning, Inp) < timing_penalty(noon, Inp)
    assert timing_penalty(four_am, Inp) >= timing_penalty(morning, Inp) + 1.5


def test_budget_and_ac_class_are_both_offered():
    from datetime import timedelta as td

    from app.providers.indian_rail import pick_classes
    assert pick_classes(["1A", "2A", "3A", "SL"], td(hours=5)) == ["SL", "3A"]
    assert pick_classes(["CC", "EC"], td(hours=4)) == ["CC"]
    assert pick_classes(["2S", "CC", "EC"], td(hours=4)) == ["2S", "CC"]


def test_generic_and_barely_reviewed_places_are_dropped():
    from app.providers.types import PlaceData
    from app.services.planning_support import weak_place
    def place(name, n):
        p = PlaceData("google", name, name, "attraction", 26.9, 75.8)
        p.rating_count = n
        return p
    assert weak_place(place("JAIPUR MARKET", 300), "Jaipur")
    assert weak_place(place("Hidden spot", 12), "Jaipur")
    assert not weak_place(place("Hawa Mahal", 80000), "Jaipur")
    assert not weak_place(place("Jaipur Wax Museum", 15000), "Jaipur")


def test_snacks_cost_a_fraction_of_a_meal():
    from app.providers.types import PlaceData
    from app.services.engine_types import Candidate
    from app.services.route_optimizer import snack_version
    c = Candidate(key="k", place=PlaceData("google", "k", "Cafe", "restaurant", 0, 0), kind="restaurant",
                  unit_cost=Decimal(450))
    assert snack_version(c).unit_cost == Decimal(158) and snack_version(c).key == "k"


def test_places_far_from_the_destination_block_the_plan_and_name_the_destination():
    from app.providers.types import PlaceData
    from app.services.engine_types import Candidate

    def stop_at(name, lat, lng):
        s = _stop(10)
        s.cand = Candidate(key=name, place=PlaceData("google", name, name, "attraction", lat, lng), kind="attraction")
        return s
    masuri = (28.66, 77.50)  # a village near Ghaziabad
    near = stop_at("Local park", 28.68, 77.52)
    mussoorie = stop_at("Kempty Falls", 30.4, 78.03)  # the sights that were actually found: ~230 km away
    rep = Q.review([_frame(0, [near, mussoorie])], masuri, None, None, True, "Masuri")
    assert not rep.ok
    assert "Kempty Falls" in rep.blocking[0] and "\u201cMasuri\u201d" in rep.blocking[0]
    assert "exact place" in rep.blocking[0]
    ok = Q.review([_frame(0, [near])], masuri, None, None, True, "Masuri")
    assert ok.ok


def test_distance_caps_drop_impractical_modes_but_never_the_only_choice():
    from app.services.feasibility import distance_capped_modes
    kept, dropped = distance_capped_modes(["train", "cab"], 400)
    assert kept == ["train"] and dropped == ["cab"]  # a 400 km cab isn't offered when train exists
    kept, dropped = distance_capped_modes(["bus", "own_car"], 50)
    assert kept == ["bus", "own_car"] and dropped == []  # both fine at 50 km
    kept, dropped = distance_capped_modes(["own_car"], 300)
    assert kept == ["own_car"] and dropped == []  # the only choice is kept even though it's a long drive
    kept, dropped = distance_capped_modes(["bus"], 700)
    assert kept == ["bus"] and dropped == []  # same for a single-mode bus request past 500 km
    kept, dropped = distance_capped_modes(["train", "bus", "cab", "own_car"], 650)
    assert kept == ["train"] and set(dropped) == {"bus", "cab", "own_car"}


def test_train_is_preferred_over_an_equally_valid_bus():
    from datetime import datetime
    from decimal import Decimal

    from app.providers.types import TransportData
    from app.services.feasibility import Combo, rank_combos

    def leg(mode, cost):
        return TransportData(direction="outbound", mode=mode, name="X", depart_at=datetime(2026, 6, 1, 8),
                             arrive_at=datetime(2026, 6, 1, 13), duration_minutes=300, cost_per_person=Decimal(cost),
                             access_minutes=10)

    def combo(mode, cost):
        t = leg(mode, cost)
        return Combo(outbound=t, ret=t, fare=Decimal(cost) * 2, access=Decimal(0),
                    dest_start=datetime(2026, 6, 1, 13), dest_end=datetime(2026, 6, 1, 20),
                    start_point=(0, 0), start_name="A", end_point=(0, 0), end_name="B", comfort=3)
    bus = combo("bus", 400)
    train = combo("train", 450)  # a little more expensive, same comfort rating

    class Inp:
        styles, transport_modes = ["balanced"], ["train", "bus"]
        start, end = datetime(2026, 6, 1, 7), datetime(2026, 6, 1, 21)
        budget = Decimal(5000)
    ranked = rank_combos([bus, train], Inp)
    assert ranked[0] is train