"""A Jaipur -> Goa plan once listed a short Rajasthan passenger train as reaching Madgaon in nine hours, then sent the
travellers to a waterfall at 02:38 and a fort at 05:07, all under the day they left. These pin the three fixes."""
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace as NS

from app.providers.rail_chain import RailChain, plausible
from app.services.feasibility import Combo
from app.services.planning_support import DAY_START, build_frames


def _train(km, minutes, number="04703", name="Hanumangarh - Shri Ganganagar Passenger Special"):
    return NS(mode="train", distance_m=int(km * 1000), duration_minutes=minutes, meta={"number": number}, name=name)


def test_a_train_faster_than_any_indian_train_is_not_plausible():
    assert not plausible(_train(1900, 560))  # Jaipur -> Madgaon in 9 h 20 min: over 200 km/h
    assert plausible(_train(1900, 33 * 60))  # the real thing takes well over a day
    assert plausible(_train(240, 4 * 60 + 30))  # Delhi -> Agra
    assert not plausible(_train(300, 0))
    assert plausible(NS(mode="flight", distance_m=1_900_000, duration_minutes=150))  # only trains are checked


class _Source:
    def __init__(self, name, out):
        self.name, self.out, self.last_issue = name, out, None

    def intercity(self, *a):
        return list(self.out)


def test_the_chain_drops_impossible_trains_and_moves_on_when_none_are_left():
    place = NS(name="x", lat=0.0, lng=0.0)
    real = _train(1900, 34 * 60, number="12977", name="Marusagar Express")
    chain = RailChain([_Source("first", [_train(1900, 560)]), _Source("second", [real])])
    assert chain.intercity(place, place, None, None) == [real]
    mixed = RailChain([_Source("only", [_train(1900, 560), real])])
    assert mixed.intercity(place, place, None, None) == [real]


def _combo(arrive: datetime, leave: datetime, outbound=True) -> Combo:
    return Combo(NS() if outbound else None, None, Decimal(0), Decimal(0), arrive, leave, (15.27, 73.96), "Madgaon",
                 (15.38, 73.83), "Goa airport", 1.0)


def test_after_a_night_arrival_the_first_day_starts_in_the_morning():
    frames = build_frames(_combo(datetime(2026, 10, 18, 2, 5), datetime(2026, 10, 21, 18, 0)), None,
                          NS(name="Goa", lat=15.5, lng=73.8))
    assert frames[0].day.isoformat() == "2026-10-18" and frames[0].start == datetime(2026, 10, 18, DAY_START.hour, DAY_START.minute)


def test_a_morning_arrival_and_a_trip_with_no_journey_keep_their_own_start():
    goa = NS(name="Goa", lat=15.5, lng=73.8)
    morning = build_frames(_combo(datetime(2026, 10, 18, 7, 10), datetime(2026, 10, 20, 18, 0)), None, goa)
    assert morning[0].start == datetime(2026, 10, 18, 7, 10)
    sunrise = build_frames(_combo(datetime(2026, 10, 18, 5, 0), datetime(2026, 10, 18, 11, 0), outbound=False), None, goa)
    assert sunrise[0].start == datetime(2026, 10, 18, 5, 0)  # a local sunrise walk the traveller asked for


def test_a_day_does_not_jump_across_a_spread_out_destination():
    """Goa is 100 km end to end: a highly rated fort 70 km away must not follow a stop in the south on the same day
    (the quality gate would then refuse the whole plan with "Day 1 jumps 67 km between two stops")."""
    from datetime import date, time
    from app.providers.types import PlaceData
    from app.services.engine_types import Candidate, DayFrame, PlanInput
    from app.services.route_optimizer import DayScheduler, Travel

    def place(key, lat, lng, score):
        return Candidate(key=key, place=PlaceData("sim", key, key, "tourist_attraction", lat, lng), kind="attraction",
                         score=score, duration=60)

    south = (15.27, 73.96)  # Madgaon
    near1, near2 = place("colva-beach", 15.28, 73.92, 10), place("margao-market", 15.29, 73.95, 6)
    far = place("north-fort", 15.87, 73.79, 9)  # about 68 km north of the first stop
    day = date(2026, 10, 18)
    inp = PlanInput("Jaipur", "Goa", None, None, datetime(2026, 10, 17, 7), datetime(2026, 10, 20, 22), Decimal(50000),
                    2, "couple", ["train"], "both", ["balanced"], "budget", pace="normal")
    frame = DayFrame(1, day, datetime.combine(day, time(9)), datetime.combine(day, time(20)), south, "Madgaon", south, "Madgaon")
    sched = DayScheduler(inp, Travel("cab", 2), [near1, far, near2], [], set(), Decimal(50000))
    stops, _ = sched.build(frame, Decimal(0))
    names = [s.name for s in stops if s.kind == "activity"]
    assert "colva-beach" in names and "north-fort" not in names, names
