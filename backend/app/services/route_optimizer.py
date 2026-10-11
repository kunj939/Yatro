"""Time-window scheduler + route optimizer.

1. Greedy construction: at each step pick the best feasible next stop (score minus travel/wait penalties)
   while protecting meal windows, the sunset anchor, the budget and the time needed to reach the day's end.
2. 2-opt improvement: reverse sub-sequences when that shortens total travel and every window still holds.
3. Real legs: consecutive stops are routed through the MapsProvider and the day is re-timed; stops that no
   longer fit are dropped (lowest score first).

Everything here is deterministic — no LLM is involved in timing, distance or cost.
"""
from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal

from app.core.telemetry import ContextThreadPool
from app.core.errors import ProviderUnavailable
from app.providers.base import MapsProvider
from app.services import fare_model as F
from app.services.engine_types import Candidate, DayFrame, Leg, PlanInput, Stop
from app.services.ranking import food_ok, serves
from app.utils import hours as H
from app.utils.geo import estimate_road
from app.utils.sun import sun_times

MEALS = {  # earliest start, latest start, minutes
    "breakfast": (time(7, 0), time(10, 15), 40),
    "lunch": (time(12, 15), time(14, 45), 60),
    "snack": (time(16, 0), time(17, 45), 30),
    "dinner": (time(18, 45), time(21, 45), 70),
}
MAX_ACTIVITIES = {"relaxed": 3, "normal": 5, "packed": 7}
DURATION_FACTOR = {"relaxed": 1.15, "normal": 1.0, "packed": 0.9}
TRAVEL_PENALTY = 0.015  # score units per travel minute
WAIT_PENALTY = 0.01
MAX_ACTIVITY_WAIT = timedelta(minutes=45)
MAX_MEAL_WAIT = timedelta(minutes=90)


def mm(n: int | float) -> timedelta:
    return timedelta(minutes=int(n))


class Travel:
    """Leg estimator. Estimates guide the search; `apply_real_routes` swaps in provider routes.

    `scale` stretches the estimated times (not the real ones), and `real` seeds legs already measured: together they
    let a plan be rebuilt once the real routes turn out slower than the estimates, instead of dropping stops."""

    def __init__(self, local_mode: str, travellers: int, scale: float = 1.0, real: dict | None = None,
                 walk_max_m: int = F.WALK_MAX_M):
        self.local_mode = local_mode
        self.travellers = travellers
        self.walk_max_m = walk_max_m  # shorter for a party with children or older travellers (services/age_fit.py)
        self.scale = scale
        self.real: dict[tuple, Leg] = dict(real or {})
        self._est: dict[tuple, Leg] = {}
        self._raw: dict[tuple, Leg] = {}

    def estimate(self, a: tuple[float, float], b: tuple[float, float]) -> Leg:
        """The unscaled estimate for a leg."""
        key = (a, b)
        if key not in self._raw:
            dist, dur = estimate_road(*a, *b)
            mode = "walk" if dist <= self.walk_max_m else self.local_mode
            self._raw[key] = Leg(dist, F.local_leg_minutes(dur, dist, mode) if dist else 0, mode,
                                 F.local_leg_cost(dist, mode, self.travellers), "estimated")
        return self._raw[key]

    def leg(self, a: tuple[float, float], b: tuple[float, float]) -> Leg:
        key = (a, b)
        if key in self.real:
            return self.real[key]
        if key not in self._est:
            raw = self.estimate(a, b)
            minutes = max(1, math.ceil(raw.minutes * self.scale)) if raw.minutes else 0
            self._est[key] = replace(raw, minutes=minutes)
        return self._est[key]

    def calibration(self) -> float:
        """How much slower the measured legs are than their estimates (1.0 = as estimated, never below)."""
        est = sum(self.estimate(*pair).minutes for pair in self.real)
        real = sum(leg.minutes for leg in self.real.values())
        return max(1.0, min(2.5, real / est)) if est > 0 else 1.0


SNACK_SHARE = Decimal("0.35")  # tea and a snack cost about a third of a meal


def snack_version(r: Candidate) -> Candidate:
    """The same place priced for a snack stop (a ₹900 "evening snack" isn't a snack)."""
    return replace(r, unit_cost=max(Decimal(40), (r.unit_cost * SNACK_SHARE).quantize(Decimal(1))))


SUNSET_EARLIEST = timedelta(minutes=150)  # a sunset spot is worth visiting from ~2.5 h before sunset...
SUNSET_LATEST = timedelta(minutes=30)  # ...until shortly after it


def sunset_of(frame: DayFrame) -> datetime:
    if frame.sunset is None:
        _, sunset = sun_times(frame.day, frame.start_point[0], frame.start_point[1])
        frame.sunset = sunset.replace(second=0, microsecond=0)
    return frame.sunset


def is_sunset_only(c: Candidate) -> bool:
    """Places whose whole point is the sunset view ("Sunset Point", "Sunset Viewpoint")."""
    return "sunset" in c.place.name.lower() or "sunset_only" in c.place.tags


def sunset_time_ok(c: Candidate, frame: DayFrame, start: datetime) -> bool:
    """Nobody goes to a sunset point at 10 am: such places are only scheduled close to sunset."""
    if not is_sunset_only(c):
        return True
    sunset = sunset_of(frame)
    return sunset - SUNSET_EARLIEST <= start <= sunset + SUNSET_LATEST


def _meal_stops(frame: DayFrame, styles: list[str]) -> list[Stop]:
    out = []
    day = frame.day
    for meal, (ws, we, dur) in MEALS.items():
        w_start, w_end = datetime.combine(day, ws), datetime.combine(day, we)
        if meal == "breakfast" and frame.start.time() > time(9, 45):
            continue
        if meal == "lunch" and not (frame.start <= datetime.combine(day, time(14, 15)) and
                                    frame.end >= datetime.combine(day, time(14, 0))):
            continue
        if meal == "snack" and ("food" not in styles or not (frame.start <= datetime.combine(day, time(16, 30))
                                                             and frame.end >= datetime.combine(day, time(17, 30)))):
            continue
        if meal == "dinner" and frame.end < datetime.combine(day, time(19, 50)):
            continue
        out.append(Stop(kind="meal", cand=None, name=meal.title(), duration=dur, meal=meal,
                        window=(max(w_start, frame.start), w_end), day_index=frame.index))
    return out


def simulate(frame: DayFrame, stops: list[Stop], travel: Travel, check_end: bool = True) -> tuple[bool, int, int]:
    """Time every stop in order. Returns (valid, travel_minutes, wait_minutes)."""
    t, p = frame.start, frame.start_point
    total_travel = total_wait = 0
    for s in stops:
        leg = travel.leg(p, s.point)
        arrive = t + mm(leg.minutes)
        start = arrive
        if s.kind == "meal":
            ws, we = s.window
            if start < ws:
                if ws - start > MAX_MEAL_WAIT and not s.wait_ok:
                    return False, 0, 0
                start = ws
            if start > we:
                return False, 0, 0
            end = start + mm(s.duration)
        elif s.kind == "sunset":
            if arrive > s.anchor - mm(15):
                return False, 0, 0
            start = max(arrive, s.anchor - mm(max(s.duration, 40)))
            end = s.anchor + mm(15)
        else:
            end = start + mm(s.duration)
        hours = s.cand.place.opening_hours if s.cand else None
        close = H.assumed_close(s.cand.place.tags) if s.cand and s.kind not in ("meal", "sunset") else H.UNKNOWN_CLOSE
        ok, _ = H.fits(hours, frame.day, start, end, close)
        if not ok:
            nxt = H.next_open(hours, frame.day, start, close)
            if s.kind == "sunset" or not nxt or nxt[0] - start > MAX_ACTIVITY_WAIT or nxt[0] + (end - start) > nxt[1]:
                return False, 0, 0
            if s.kind == "meal" and nxt[0] > s.window[1]:
                return False, 0, 0
            end = nxt[0] + (end - start)
            start = nxt[0]
        total_wait += int((start - arrive).total_seconds() // 60)
        total_travel += leg.minutes
        s.arrive, s.start, s.end, s.leg = arrive, start, end, leg
        t, p = end, s.point
    final = travel.leg(p, frame.end_point)
    frame.final_leg = final
    total_travel += final.minutes
    if check_end and t + mm(final.minutes) > frame.end:
        return False, 0, 0
    return True, total_travel, total_wait


class DayScheduler:
    def __init__(self, inp: PlanInput, travel: Travel, attractions: list[Candidate], restaurants: list[Candidate],
                 used: set[str], allowance: Decimal):
        self.inp = inp
        self.travel = travel
        self.attractions = attractions
        self.food = inp.effective_food
        self.restaurants = [r for r in restaurants if food_ok(r, self.food)]
        self.used = used
        self.allowance = allowance  # money left for activities + meals + local legs across all days
        self._cap = allowance  # allowance minus what later days need for their meals (set per build)
        self._ride = Decimal(0)
        self.warnings: list[str] = []
        self.factor = DURATION_FACTOR.get(inp.pace, 1.0)
        self.tp = TRAVEL_PENALTY
        self.force_first: str | None = None
        self.meal_history: set[str] = set()
        self._restaurant_keys = {r.key for r in self.restaurants}

    # --- helpers ------------------------------------------------------------------
    def _cost(self, c: Candidate | None, leg: Leg) -> Decimal:
        return (c.group_cost(self.inp.travellers) if c else Decimal(0)) + leg.cost

    def _duration(self, c: Candidate) -> int:
        return max(20, int(round(c.duration * self.factor / 5) * 5))

    def _state_after(self, frame: DayFrame, stops: list[Stop]) -> tuple[datetime, tuple[float, float]]:
        if not stops:
            return frame.start, frame.start_point
        return stops[-1].end, stops[-1].point

    def pick_sunset_spot(self, frame: DayFrame) -> Stop | None:
        _, sunset = sun_times(frame.day, frame.start_point[0], frame.start_point[1])
        frame.sunset = sunset.replace(second=0, microsecond=0)
        anchor = frame.sunset
        if not (frame.start + mm(60) <= anchor - mm(40) and anchor + mm(45) <= frame.end):
            return None
        best, best_val = None, -1e9
        for c in self.attractions:
            if c.key in self.used or "sunset" not in c.place.tags:
                continue
            ok, _ = H.fits(c.place.opening_hours, frame.day, anchor - mm(40), anchor + mm(15))
            if not ok:
                continue
            to_end = self.travel.leg(c.point, frame.end_point).minutes
            val = c.score - TRAVEL_PENALTY * to_end
            if val > best_val:
                best, best_val = c, val
        if not best:
            return None
        return Stop(kind="sunset", cand=best, name=best.place.name, duration=self._duration(best), anchor=anchor,
                    day_index=frame.index)

    def _best_meal(self, frame: DayFrame, meal: Stop, t: datetime, p: tuple[float, float], spent: Decimal,
                   last: bool, allow_wait: bool = False, pending: list[Stop] | None = None) -> Stop | None:
        """Best restaurant for this meal; the last commitment of the day must still reach the day's end point."""
        best, best_val, best_dur = None, -1e9, meal.duration
        for r in self.restaurants:
            if r.key in self.used or not serves(r, meal.meal):
                continue
            if meal.meal == "snack":
                r = snack_version(r)
            leg = self.travel.leg(p, r.point)
            arrive = t + mm(leg.minutes)
            start = max(arrive, meal.window[0])
            if start > meal.window[1] or (start - arrive > MAX_MEAL_WAIT and not allow_wait):
                continue
            dur = meal.duration
            if last:
                slack = frame.end - (start + mm(self.travel.leg(r.point, frame.end_point).minutes))
                dur = min(dur, int(slack.total_seconds() // 60))
                if dur < 45:
                    continue
            ok, _ = H.fits(r.place.opening_hours, frame.day, start, start + mm(dur))
            if not ok:
                continue
            cost = self._cost(r, leg)
            # later meals today must stay affordable *without* this restaurant (it can't serve two meals a day)
            reserve = self._meal_reserve(pending or [], exclude=r.key)
            if spent + cost + reserve > self._cap and not r.pinned:
                continue
            room = max(self._cap - spent - reserve, Decimal(1))
            val = (r.score - 2 * TRAVEL_PENALTY * leg.minutes - (0.35 if r.key in self.meal_history else 0)
                   - 0.6 * float(cost / room))  # prefer cheaper places when money is tight
            if val > best_val:
                best, best_val, best_dur = r, val, dur
        if not best:
            return None
        return replace(meal, cand=best, name=best.place.name, duration=best_dur, wait_ok=allow_wait)

    def _meal_reserve(self, pending: list[Stop], exclude: str | None = None) -> Decimal:
        """Money to keep aside so every remaining meal can still be afforded (cheapest suitable option that
        isn't already taken today) plus a ride to reach it."""
        total = Decimal(0)
        taken = set(self.used) | ({exclude} if exclude else set())
        for m in pending:
            options = sorted((r for r in self.restaurants if r.key not in taken and serves(r, m.meal)),
                             key=lambda r: r.group_cost(self.inp.travellers))
            if options:
                taken.add(options[0].key)
                first = snack_version(options[0]) if m.meal == "snack" else options[0]
                total += first.group_cost(self.inp.travellers)
            total += self._ride
        return total

    def _nearest_meal_minutes(self, p: tuple[float, float], meal: str) -> int:
        legs = [self.travel.leg(p, r.point).minutes for r in self.restaurants
                if r.key not in self.used and serves(r, meal)]
        return min(legs) if legs else 15

    # --- construction -------------------------------------------------------------
    def day_value(self, frame: DayFrame, stops: list[Stop], spent_delta: Decimal) -> float:
        ok, travel_min, wait_min = simulate(frame, stops, self.travel)
        if not ok:
            return -1e9
        acts = [s for s in stops if s.kind in ("activity", "sunset")]
        meals = [s for s in stops if s.kind == "meal"]
        expected = _meal_stops(frame, self.inp.styles)
        got = {s.meal for s in meals}
        # lunch and dinner are not optional: a day that skips one for another sight is a worse day
        missed_main = sum(1 for m in expected if m.meal in ("lunch", "dinner") and m.meal not in got)
        missed_other = sum(1 for m in expected if m.meal not in ("lunch", "dinner") and m.meal not in got)
        # long empty gaps between stops (e.g. 4 hours of "free time" after lunch) make a day worse
        idle, prev_end = 0.0, frame.start
        for st in stops:
            if st.start is not None and st.end is not None:
                gap = (st.start - prev_end).total_seconds() / 60 - (st.leg.minutes if st.leg else 0)
                idle += max(0.0, gap - 60)
                prev_end = st.end
        return (sum(min(s.cand.score, 1.5) for s in acts) + 0.6 * len(meals) - 3.0 * missed_main - 0.8 * missed_other
                + (0.8 if any(s.kind == "sunset" for s in stops) else 0) - 0.012 * travel_min - 0.004 * wait_min
                - 0.006 * idle
                - 0.3 * float(spent_delta / max(self.allowance, Decimal(1))))

    def later_days_reserve(self, later: list[DayFrame]) -> Decimal:
        """Money later days need for their meals (cheapest suitable option) plus two short local rides each,
        so an early day can't spend what the last day needs to eat."""
        total = Decimal(0)
        for f in later:
            for m in _meal_stops(f, self.inp.styles):
                costs = [(snack_version(r) if m.meal == "snack" else r).group_cost(self.inp.travellers)
                         for r in self.restaurants if serves(r, m.meal)]
                total += (min(costs) if costs else Decimal(0)) + F.local_leg_cost(3000, self.travel.local_mode,
                                                                                    self.inp.travellers)
            total += F.local_leg_cost(3000, self.travel.local_mode, self.inp.travellers)
        return total

    def build(self, frame: DayFrame, spent: Decimal, later: list[DayFrame] | None = None) -> tuple[list[Stop], Decimal]:
        """Multi-start construction: a few travel-penalty weights x forced first stops; keep the best day."""
        # Restaurants may be revisited on another day (with a small penalty); sights never repeat.
        self.meal_history |= {k for k in self.used if k in self._restaurant_keys}
        self.used -= self._restaurant_keys
        self._ride = F.local_leg_cost(3000, self.travel.local_mode, self.inp.travellers)  # a typical short ride
        later = later or []
        ess_later = self.later_days_reserve(later)
        self._cap = self.allowance - ess_later
        if later:
            # Share the discretionary money (after everyone's meals and rides) across days by available time,
            # so day 1 can't spend what day 2's sights need.
            free = self._cap - spent - self.later_days_reserve([frame])
            if free > 0:
                span = lambda f: max(1.0, (f.end - f.start).total_seconds())  # noqa: E731
                share_now = span(frame) / sum(span(f) for f in [frame, *later])
                self._cap -= free * Decimal(str(round(1 - share_now, 4)))
        base_used, base_warn = set(self.used), list(self.warnings)
        firsts: list[str | None] = [None] + [c.key for c in self.attractions[:8] if c.key not in base_used][:3]
        best = None
        for tp in (TRAVEL_PENALTY, TRAVEL_PENALTY * 2.5, TRAVEL_PENALTY * 5):
            for first in firsts:
                self.used, self.warnings, self.tp, self.force_first = set(base_used), list(base_warn), tp, first
                stops, new_spent = self._build_once(frame, spent)
                value = self.day_value(frame, stops, new_spent - spent)
                if best is None or value > best[0] + 1e-9:
                    best = (value, stops, new_spent, set(self.used), list(self.warnings))
        _, stops, new_spent, self.used, self.warnings = best
        self.tp, self.force_first = TRAVEL_PENALTY, None
        simulate(frame, stops, self.travel)
        return stops, new_spent

    def _build_once(self, frame: DayFrame, spent: Decimal) -> tuple[list[Stop], Decimal]:
        pending = _meal_stops(frame, self.inp.styles)
        sunset = self.pick_sunset_spot(frame)
        if sunset:
            self.used.add(sunset.cand.key)
        stops: list[Stop] = []
        activities = 0
        max_acts = MAX_ACTIVITIES.get(self.inp.pace, 5)
        for _ in range(24):
            t, p = self._state_after(frame, stops)
            meal = pending[0] if pending else None
            # 1) a meal whose window is open (or opening soon) comes first
            if meal and (t >= meal.window[0] - mm(25) or activities >= max_acts):
                if sunset and meal.window[0] > sunset.anchor:
                    meal = None  # dinner happens after sunset
                else:
                    pending.pop(0)
                    chosen = self._best_meal(frame, meal, t, p, spent, last=not pending and not sunset,
                                             pending=pending)
                    if chosen:
                        stops.append(chosen)
                        simulate(frame, stops, self.travel, check_end=False)
                        spent += self._cost(chosen.cand, chosen.leg)
                        self.used.add(chosen.cand.key)
                    else:
                        self.warnings.append(f"No {meal.meal} stop matched your food preference and timing on day "
                                             f"{frame.index + 1}.")
                    continue
            # 2) best activity that keeps every later commitment reachable
            best, best_val, best_leg, _best_start = None, -1e9, None, None
            reserve = self._meal_reserve(pending)
            if sunset:
                reserve += sunset.cand.group_cost(self.inp.travellers) + self.travel.leg(p, sunset.point).cost
            if activities < max_acts:
                for c in self.attractions:
                    if c.key in self.used:
                        continue
                    leg = self.travel.leg(p, c.point)
                    start = t + mm(leg.minutes)
                    dur = self._duration(c)
                    close = H.assumed_close(c.place.tags)
                    ok, _ = H.fits(c.place.opening_hours, frame.day, start, start + mm(dur), close)
                    if not ok:
                        nxt = H.next_open(c.place.opening_hours, frame.day, start, close)
                        if not nxt or nxt[0] - start > MAX_ACTIVITY_WAIT or nxt[0] + mm(dur) > nxt[1]:
                            continue
                        wait = int((nxt[0] - start).total_seconds() // 60)
                        start = nxt[0]
                    else:
                        wait = 0
                    end = start + mm(dur)
                    if not sunset_time_ok(c, frame, start):
                        continue
                    if meal and end + mm(self._nearest_meal_minutes(c.point, meal.meal)) > meal.window[1]:
                        continue
                    if sunset and end + mm(self.travel.leg(c.point, sunset.point).minutes) > sunset.anchor - mm(15):
                        continue
                    if not sunset:
                        tail = sum(m.duration + 12 for m in pending) + self.travel.leg(c.point, frame.end_point).minutes
                        if end + mm(tail) > frame.end:
                            continue
                    cost = self._cost(c, leg)
                    if spent + cost + reserve > self._cap and not c.pinned:
                        continue
                    # variety: a third market in one day is worth less than a first museum
                    same = sum(1 for x in stops if x.kind == "activity" and x.cand.place.tags[:1]
                               and x.cand.place.tags[:1] == c.place.tags[:1])
                    val = c.score - self.tp * leg.minutes - WAIT_PENALTY * wait - 0.45 * same
                    if self.force_first and activities == 0 and c.key == self.force_first:
                        val = 1e6
                    if val > best_val:
                        best, best_val, best_leg, _best_start = c, val, leg, start
            if best:
                stops.append(Stop(kind="activity", cand=best, name=best.place.name, duration=self._duration(best),
                                  day_index=frame.index))
                simulate(frame, stops, self.travel, check_end=False)
                spent += self._cost(best, best_leg)
                self.used.add(best.key)
                activities += 1
                continue
            # 3) head to the sunset spot, unless a meal is due long before it (eat first, then see what fits)
            meal_first = bool(pending) and pending[0].window[0] < sunset.anchor - mm(60) if sunset else False
            far_off = sunset is not None and t + mm(self.travel.leg(p, sunset.point).minutes) < sunset.anchor - mm(150)
            if sunset and not (meal_first and far_off):
                leg = self.travel.leg(p, sunset.point)
                affordable = spent + self._cost(sunset.cand, leg) + self._meal_reserve(pending) <= self._cap
                if t + mm(leg.minutes) <= sunset.anchor - mm(15) and (affordable or sunset.cand.pinned):
                    stops.append(sunset)
                    simulate(frame, stops, self.travel, check_end=False)
                    spent += self._cost(sunset.cand, sunset.leg)
                else:
                    self.used.discard(sunset.cand.key)
                sunset = None
                continue
            # 4) nothing else fits before the next meal: go eat, even if that means free time first.
            #    (Dropping the meal here would release its reserved money to later sights.)
            if pending:
                meal = pending.pop(0)
                chosen = self._best_meal(frame, meal, t, p, spent, last=not pending, allow_wait=True,
                                         pending=pending)
                if chosen:
                    stops.append(chosen)
                    simulate(frame, stops, self.travel, check_end=False)
                    spent += self._cost(chosen.cand, chosen.leg)
                    self.used.add(chosen.cand.key)
                else:
                    self.warnings.append(f"No {meal.meal} stop matched your food preference and timing on day "
                                         f"{frame.index + 1}.")
                continue
            break
        stops = self._repair(frame, stops)
        stops = self.two_opt(frame, stops)
        return stops, spent

    def _repair(self, frame: DayFrame, stops: list[Stop]) -> list[Stop]:
        """Drop the weakest activity (then the latest stop) until the whole day, incl. the final leg, is valid."""
        while stops and not simulate(frame, stops, self.travel)[0]:
            removable = [s for s in stops if s.kind == "activity" and not (s.cand and s.cand.pinned)]
            victim = min(removable, key=lambda s: s.cand.score if s.cand else 0) if removable else stops[-1]
            stops.remove(victim)
            if victim.cand:
                self.used.discard(victim.cand.key)
        return stops

    def two_opt(self, frame: DayFrame, stops: list[Stop]) -> list[Stop]:
        if len(stops) < 3:
            return stops
        ok, best_travel, _ = simulate(frame, stops, self.travel)
        if not ok:
            return stops
        improved = True
        while improved:
            improved = False
            for i in range(len(stops) - 1):
                for j in range(i + 2, len(stops) + 1):
                    trial = stops[:i] + stops[i:j][::-1] + stops[j:]
                    ok, travel_min, _ = simulate(frame, trial, self.travel)
                    if ok and travel_min < best_travel:
                        stops, best_travel, improved = trial, travel_min, True
        simulate(frame, stops, self.travel)
        return stops


def apply_real_routes(maps: MapsProvider, frames: list[DayFrame], travel: Travel) -> tuple[str, int]:
    """Route every consecutive pair through the MapsProvider (parallel, cached). Returns (state, failures)."""
    pairs: set[tuple] = set()
    for f in frames:
        pts = [f.start_point] + [s.point for s in f.stops] + [f.end_point]
        pairs.update(zip(pts, pts[1:]))
    pairs = {p for p in pairs if p[0] != p[1]}
    failures = 0

    def route(pair):
        a, b = pair
        est = travel.leg(a, b)
        mode = "walk" if est.mode == "walk" else "drive"
        try:
            r = maps.compute_route(a, b, mode)
        except ProviderUnavailable:
            return pair, None
        local_mode = "walk" if mode == "walk" else travel.local_mode
        minutes = F.local_leg_minutes(r.duration_s, r.distance_m, local_mode)
        return pair, Leg(r.distance_m, minutes, local_mode, F.local_leg_cost(r.distance_m, local_mode,
                                                                            travel.travellers), r.state, r.polyline)

    states = set()
    with ContextThreadPool(max_workers=8) as pool:
        for pair, leg in pool.map(route, pairs):
            if leg is None:
                failures += 1
            else:
                travel.real[pair] = leg
                states.add(leg.state)
    state = "estimated" if failures == len(pairs) and pairs else (states.pop() if len(states) == 1 else "mixed")
    return state, failures