"""Alternative generation — a core feature.

Each alternative is produced by actually substituting it into the day and re-simulating the schedule, so it is
guaranteed to respect opening hours, the time window, route compatibility, the budget and the user's preferences.
Nothing random is ever offered.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import timedelta
from decimal import Decimal

from app.core.provenance import part_of_day, train_shows_facts
from app.providers.types import TransportData
from app.services.engine_types import Candidate, DayFrame, Stop
from app.services.feasibility import (
    Combo,
    _valid_outbound,
    _valid_return,
    group_cost,
    option_key,
    transport_label,
)
from app.services.ranking import food_ok, serves
from app.services.route_optimizer import TRAVEL_PENALTY, Travel, simulate, sunset_time_ok
from app.utils import hours as H
from app.utils.money import fmt_money

MAX_ALTERNATIVES = 3
NEARBY_M = 3500


@dataclass
class AltResult:
    kind: str  # place | transport | hotel
    title: str
    score: float
    cost: Decimal
    cost_delta: Decimal
    time_delta: int
    distance_m: int | None
    reason: str
    checks: dict
    cand: Candidate | None = None
    transport: TransportData | None = None
    meta: dict = field(default_factory=dict)


def _copy(stops: list[Stop]) -> list[Stop]:
    return [replace(s) for s in stops]


def _stop_cost(s: Stop, travellers: int) -> Decimal:
    return (s.cand.group_cost(travellers) if s.cand else Decimal(0)) + (s.leg.cost if s.leg else Decimal(0))


def _day_cost(frame: DayFrame, stops: list[Stop], travellers: int) -> Decimal:
    return sum((_stop_cost(s, travellers) for s in stops), Decimal(0)) + (frame.final_leg.cost if frame.final_leg else 0)


def _travel_minutes(frame: DayFrame, stops: list[Stop]) -> int:
    return sum(s.leg.minutes for s in stops if s.leg) + (frame.final_leg.minutes if frame.final_leg else 0)


def place_alternatives(frame: DayFrame, stops: list[Stop], idx: int, pool: list[Candidate], used: set[str],
                       travel: Travel, travellers: int, food: str, remaining: Decimal,
                       currency: str = "INR") -> list[AltResult]:
    target = stops[idx]
    base_cost = _day_cost(frame, stops, travellers)
    base_travel = _travel_minutes(frame, stops)
    prev_name = stops[idx - 1].name if idx > 0 else frame.start_name
    out: list[AltResult] = []
    for c in pool:
        if c.key in used or c.key == (target.cand.key if target.cand else None):
            continue
        if target.kind == "meal" and (not food_ok(c, food) or not serves(c, target.meal)):
            continue
        if target.kind == "sunset" and "sunset" not in c.place.tags:
            continue
        trial = _copy(stops)
        dur = target.duration if target.kind == "meal" else max(20, c.duration)
        trial[idx] = replace(target, cand=c, name=c.place.name, duration=dur)
        ok, travel_min, _ = simulate(frame, trial, travel)
        if not ok:
            continue
        new = trial[idx]
        if target.kind == "activity" and not sunset_time_ok(c, frame, new.start):
            continue  # a sunset point is never offered as a morning or midday swap
        cost_delta = _day_cost(frame, trial, travellers) - base_cost
        if cost_delta > remaining:
            continue
        open_s, close_s = H.span_for_display(c.place.opening_hours, frame.day, new.start)
        nearby = new.leg.distance_m <= max(NEARBY_M, int((target.leg.distance_m if target.leg else 0) * 1.5))
        checks = {"within_budget": True, "open_at_visit": True, "fits_schedule": True, "nearby": nearby,
                  "matches_preferences": bool(c.matched) or target.kind == "meal"}
        bits = []
        if open_s:
            bits.append(f"open {open_s}–{close_s}")
        bits.append(f"{new.leg.distance_m / 1000:.1f} km from {prev_name}")
        if cost_delta < 0:
            bits.append(f"{fmt_money(-cost_delta, currency)} cheaper")
        elif cost_delta > 0:
            bits.append(f"{fmt_money(cost_delta, currency)} more")
        else:
            bits.append("same cost")
        if c.matched:
            bits.append("matches " + ", ".join(c.matched[:2]))
        if target.kind == "meal" and c.place.restaurant and c.place.restaurant.veg_only:
            bits.append("pure vegetarian")
        score = c.score - TRAVEL_PENALTY * (travel_min - base_travel)
        out.append(AltResult("place", c.place.name, round(score, 4), c.group_cost(travellers), cost_delta,
                             (dur - target.duration) + (travel_min - base_travel), new.leg.distance_m, "; ".join(bits).capitalize() + ".", checks, cand=c,
                             meta={"visit_start": new.start.strftime("%H:%M"), "visit_end": new.end.strftime("%H:%M"),
                                   "opening_time": open_s, "closing_time": close_s}))
    simulate(frame, stops, travel)  # restore timings of the chosen plan
    out.sort(key=lambda a: -a.score)
    return out[:MAX_ALTERNATIVES]


def transport_alternatives(options: list[TransportData], chosen: Combo, direction: str, inp, remaining: Decimal,
                           frames: list[DayFrame]) -> list[AltResult]:
    current = chosen.outbound if direction == "outbound" else chosen.ret
    fare_cur, acc_cur = group_cost(current, inp.travellers)
    first_needed = frames[0].stops[0].start if frames and frames[0].stops else chosen.dest_end
    last_done = frames[-1].stops[-1].end if frames and frames[-1].stops else chosen.dest_start
    out = []
    for t in options:
        if t.direction != direction or option_key(t) == option_key(current):
            continue
        if direction == "outbound":
            if not _valid_outbound(t, inp) or t.arrive_at + timedelta(minutes=10) > chosen.dest_end - timedelta(hours=2):
                continue
            fits_day = t.arrive_at <= first_needed
            time_delta = int((t.arrive_at - current.arrive_at).total_seconds() // 60)
        else:
            if not _valid_return(t, inp) or t.depart_at < chosen.dest_start + timedelta(hours=2):
                continue
            fits_day = t.depart_at - timedelta(minutes=20) >= last_done
            time_delta = int((t.depart_at - current.depart_at).total_seconds() // 60)
        fare, acc = group_cost(t, inp.travellers)
        delta = (fare + acc) - (fare_cur + acc_cur)
        if delta > remaining:
            continue
        when = (f"departs {t.depart_at:%H:%M}, arrives {t.arrive_at:%H:%M}" if train_shows_facts(t)
                else f"leaves {part_of_day(t.depart_at).lower()}, timing estimated")
        money = (f"{fmt_money(-delta, inp.currency)} cheaper" if delta < 0
                else (f"{fmt_money(delta, inp.currency)} more" if delta > 0 else "same fare"))
        effect = "" if fits_day else (" — the day's plan will be re-optimised around it" if direction == "outbound"
                                      else " — the last stops will be adjusted")
        checks = {"within_budget": True, "fits_time_window": True, "keeps_current_plan": fits_day,
                  "matches_preferences": t.mode in (inp.transport_modes or []) or "mixed" in (inp.transport_modes or [])}
        score = t.comfort * 0.5 - float(delta) / max(float(inp.budget), 1) * 4 + (1 if fits_day else 0)
        out.append(AltResult("transport", transport_label(t), round(score, 4), fare + acc, delta, time_delta,
                             t.distance_m, f"{when}; {money}{effect}.", checks, transport=t,
                             meta={"key": option_key(t), "mode": t.mode, "depart_at": t.depart_at.isoformat(),
                                   "arrive_at": t.arrive_at.isoformat(), "state": t.verification_state,
                                   "source_kind": t.meta.get("source_kind"), "observed_at": t.meta.get("observed_at")}))
    out.sort(key=lambda a: -a.score)
    return out[:MAX_ALTERNATIVES]


def hotel_alternatives(hotels: list[Candidate], chosen: Candidate, nights: int, rooms: int,
                       centre: tuple[float, float], remaining: Decimal, currency: str = "INR") -> list[AltResult]:
    from app.utils.geo import haversine_m
    base = chosen.unit_cost * nights * rooms
    out = []
    for h in hotels:
        if h.key == chosen.key:
            continue
        cost = h.unit_cost * nights * rooms
        delta = cost - base
        if delta > remaining:
            continue
        dist = int(haversine_m(h.place.lat, h.place.lng, *centre))
        money = (f"{fmt_money(-delta, currency)} cheaper" if delta < 0
                else (f"{fmt_money(delta, currency)} more" if delta > 0 else "same price"))
        stars = f"{h.place.hotel.stars}-star, " if h.place.hotel and h.place.hotel.stars else ""
        out.append(AltResult("hotel", h.place.name, round(h.score - dist / 20000, 4), cost, delta, 0, dist,
                             f"{stars}{dist / 1000:.1f} km from the day's sights; {money}.",
                             {"within_budget": True, "matches_preferences": True, "nearby": dist < 5000}, cand=h))
    out.sort(key=lambda a: -a.score)
    return out[:MAX_ALTERNATIVES]