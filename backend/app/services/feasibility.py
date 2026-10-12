"""Feasibility engine.

* Builds every realistic outbound/return transport combination inside the user's time window.
* Ranks them by time at the destination, comfort and cost (weights depend on trip style).
* When nothing fits, explains WHY with a minimum-cost analysis and offers concrete, one-click adjustments.

No LLM is involved: every number here is deterministic.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal

from app.core.provenance import part_of_day, train_shows_facts
from app.providers.types import PlaceData, TransportData
from app.services import fare_model as F
from app.services.budget_engine import BudgetLine, buffer_pct
from app.services.engine_types import PlanInput
from app.utils.geo import haversine_m
from app.utils.money import fmt_money, round_rupees, to_currency

LOCAL_TRIP_KM = 60
INTERCITY_MODES = ("train", "bus", "cab", "own_car", "flight")
MODE_LABEL = {"train": "train", "bus": "bus", "cab": "cab", "own_car": "own car", "flight": "flight", "metro": "metro"}
STAY_LABEL = {"premium": "Premium (5-star)", "tier1": "Tier 1 (4-star)", "tier2": "Tier 2 (3-star)", "budget": "Budget",
              "oyo": "OYO", "hostel": "Hostel", "no_stay": "No stay"}
STAY_ORDER = ["premium", "tier1", "tier2", "budget", "oyo", "hostel"]
BOARDING_BUFFER = 15  # minutes before a scheduled departure
EXIT_BUFFER = 10  # minutes to leave the station/airport after arrival
NIGHT_ARRIVAL_BEFORE = time(6, 0)  # arriving before this, the night is spent at the hotel, not at the station
MIN_DEST_MINUTES = 120
MAX_TRIP_DAYS = 7  # the wizard's limit too; a suggested fix must never stretch a trip past it
EARLIEST_START = time(4, 30)  # never suggest leaving home before this


def option_key(t: TransportData) -> str:
    # The stations are part of the identity: one train can serve two terminals of a city (Uttaranchal Express reaches
    # both New Delhi and Delhi Sarai Rohilla), and those are different options with different arrival times.
    return (f"{t.direction}|{t.mode}|{t.name}|{t.service_class or ''}|{t.depart_at:%Y%m%d%H%M}"
            f"|{t.from_name or ''}|{t.to_name or ''}")


MODE_TITLE = {"train": "Train", "bus": "Bus", "flight": "Flight", "cab": "Cab", "own_car": "Drive"}


def transport_label(t: TransportData) -> str:
    """How a journey is named everywhere (timeline, budget, alternatives).

    A service name or number appears only when a live timetable supplied it. Otherwise the journey is described
    by its departure time and destination ("06:40 train to Jaipur Junction"), never by an invented name.
    """
    mode = MODE_TITLE.get(t.mode, t.mode.title())
    notes = [t.service_class] if t.service_class else []
    if t.mode in ("cab", "own_car"):
        return f"{mode}: {t.name}" if t.name else f"{mode} to {t.to_name or 'destination'}"
    legs = t.meta.get("legs") or []
    if len(legs) > 1:  # two trains with a change between them
        names = ", then ".join(f"{leg['name']} {leg['number']}" if leg.get("number") else leg["name"] for leg in legs)
        notes.append(f"change at {_station(t.meta.get('change_at'))}")
        return f"{mode}: {names} ({', '.join(notes)})"
    if t.name:
        number = t.meta.get("number")
        label = f"{mode}: {t.name}" + (f" {number}" if number else "")
        changes = t.meta.get("changes") or 0
        if changes:
            notes.append(f"{changes} change{'s' if changes > 1 else ''}")
    elif t.flexible:
        label = f"{mode} to {t.to_name or 'destination'}"
        notes.append("timing estimated")
    else:
        start = f"{t.depart_at:%H:%M}" if train_shows_facts(t) else part_of_day(t.depart_at)
        label = f"{start} {mode.lower()} to {t.to_name or 'destination'}"
        changes = t.meta.get("changes") or 0
        if changes:
            notes.append(f"{changes} change{'s' if changes > 1 else ''}")
        if t.verification_state == "demo":
            notes.append("sample timing")
    return label + (f" ({', '.join(notes)})" if notes else "")


def _station(label: str | None) -> str:
    """"Mathura Jn (MTJ)" -> "Mathura Jn"."""
    return re.sub(r"\s*\([A-Z0-9]+\)$", "", label or "the junction")


def at(t: TransportData, moment: datetime | None = None) -> str:
    """"at 07:40" for a train from a real timetable (or any other mode); "in the morning" for one without (I1: a
    train's clock time is never shown unless it comes from a timetable)."""
    moment = moment or t.depart_at
    if train_shows_facts(t):
        return f"at {moment:%H:%M}"
    part = part_of_day(moment).lower()
    return "at night" if part == "night" else f"in the {part}"


def transport_lead(t: TransportData) -> str:
    """Opening phrase for the trip summary."""
    if t.mode == "own_car":
        return f"Drive out at {t.depart_at:%H:%M}"
    if t.mode == "cab" or t.flexible:
        return f"Leave by {MODE_LABEL.get(t.mode, t.mode)} {at(t)}"
    if t.name:
        return f"Leave on the {t.name} {at(t)}"
    return f"Take the {MODE_LABEL.get(t.mode, t.mode)} to {t.to_name or 'your destination'} {at(t)}"


def group_cost(t: TransportData, travellers: int) -> tuple[Decimal, Decimal]:
    """(fare for the group, first/last-mile transfers for the group)."""
    if t.per_vehicle:
        fare = t.vehicle_cost * F.vehicles_for(travellers, t.vehicle_capacity)
    else:
        fare = t.cost_per_person * travellers
    if not t.access_cost:
        access = Decimal(0)
    elif t.meta.get("access_mode") == "transit":
        access = t.access_cost * travellers  # a metro ticket each
    else:
        access = t.access_cost * F.vehicles_for(travellers)  # one cab per four people
    return round_rupees(fare), round_rupees(access)


def intercity_modes(inp: PlanInput) -> tuple[list[str], bool]:
    """Modes to search, and whether we had to widen the user's choice (e.g. metro-only for an intercity trip)."""
    chosen = set(inp.transport_modes or [])
    if not chosen or "mixed" in chosen:
        return list(INTERCITY_MODES), False
    modes = [m for m in INTERCITY_MODES if m in chosen]
    if modes:
        return modes, False
    return ["train", "bus"], True


DISTANCE_CAPS = {"cab": 60, "own_car": 120, "bus": 500}  # km beyond which the mode is impractical for this leg


def distance_capped_modes(modes: list[str], km: float) -> tuple[list[str], list[str]]:
    """Drops a mode once the distance makes it impractical (a cab for 400 km, a bus for 700 km) — but never
    drops the traveller's only choice; there is nothing left to fall back to. Returns (kept, dropped)."""
    kept = [m for m in modes if m not in DISTANCE_CAPS or km <= DISTANCE_CAPS[m]]
    if not kept:
        return modes, []  # every requested mode was capped out: keep them all rather than leave nothing to search
    return kept, [m for m in modes if m not in kept]


MAX_TRAIN_KM = 2000  # beyond this, assuming a usable rail corridor exists (Delhi -> Istanbul) is no longer safe


def mode_realistic(mode: str, km: float) -> bool:
    """Could this mode plausibly carry someone this far? (A bus for 4,500 km, a train across borders: no.)"""
    if mode in DISTANCE_CAPS:
        return km <= DISTANCE_CAPS[mode]
    return km <= MAX_TRAIN_KM if mode == "train" else True


def local_mode(inp: PlanInput, outbound: TransportData | None) -> str:
    chosen = set(inp.transport_modes or [])
    if outbound is not None and outbound.mode == "own_car" or ("own_car" in chosen and outbound is None):
        return "own_car"
    if "cab" in chosen or "mixed" in chosen or "own_car" in chosen:
        return "cab"
    if "metro" in chosen or "budget" in inp.styles:
        return "transit"
    return "cab"


def is_local_trip(origin: PlaceData, dest: PlaceData) -> bool:
    return haversine_m(origin.lat, origin.lng, dest.lat, dest.lng) < LOCAL_TRIP_KM * 1000


@dataclass(eq=False)
class Combo:
    outbound: TransportData | None
    ret: TransportData | None
    fare: Decimal
    access: Decimal
    dest_start: datetime
    dest_end: datetime
    start_point: tuple[float, float]
    start_name: str
    end_point: tuple[float, float]
    end_name: str
    comfort: float
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    @property
    def cost(self) -> Decimal:
        return self.fare + self.access

    @property
    def local(self) -> bool:
        return self.outbound is None

    @property
    def night_arrival(self) -> bool:
        """A journey that pulls in between midnight and 06:00: the traveller needs a room from the moment they arrive."""
        return self.outbound is not None and self.dest_start.time() < NIGHT_ARRIVAL_BEFORE

    @property
    def nights(self) -> int:
        # arriving at 00:48 on the 18th and leaving on the 19th is two nights in a room, not one
        return (self.dest_end.date() - self.dest_start.date()).days + (1 if self.night_arrival else 0)

    def label(self) -> str:
        if self.local:
            return "Local trip"
        o, r = self.outbound, self.ret
        if o.mode == r.mode:
            return f"{MODE_LABEL[o.mode].title()} both ways"
        return f"{MODE_LABEL[o.mode].title()} out, {MODE_LABEL[r.mode]} back"

    def budget_lines(self, travellers: int, currency: str = "INR") -> list[BudgetLine]:
        if self.local:
            return []
        lines = []
        for t in (self.outbound, self.ret):
            fare, access = group_cost(t, travellers)
            who = "per vehicle" if t.per_vehicle else f"{travellers} × {fmt_money(t.cost_per_person, currency)}"
            lines.append(BudgetLine("transport", f"{'Outbound' if t.direction == 'outbound' else 'Return'}: "
                                                 f"{transport_label(t)}, {who}", fare, t.verification_state))
            if access:
                lines.append(BudgetLine("transport", f"Transfer {'to' if t.direction == 'outbound' else 'from'} "
                                                     f"{t.from_name if t.direction == 'outbound' else t.to_name}",
                                        access, "estimated"))
        return lines


FLEX = timedelta(minutes=60)  # a train 40 min before your start time is better than no train at all
EXPLORE_FROM, EXPLORE_UNTIL = time(8, 0), time(21, 0)  # when sights, meals and walks can realistically happen


def explore_minutes(start: datetime, end: datetime) -> int:
    """Minutes between `start` and `end` that fall in daytime (08:00-21:00) — the time a traveller can actually
    see something. Arriving at 22:00 and leaving at 07:45 is ten hours "at the destination" but zero to explore,
    which is exactly the plan this exists to stop."""
    total, day = 0, start.date()
    while day <= end.date():
        a = max(start, datetime.combine(day, EXPLORE_FROM))
        b = min(end, datetime.combine(day, EXPLORE_UNTIL))
        total += max(0, int((b - a).total_seconds() // 60))
        day += timedelta(days=1)
    return total


def journey_penalty(t: TransportData | None, inp: PlanInput) -> float:
    """How tiring one journey is, beyond its length: changes along the way, and long hours on a bus seat or in an
    unreserved second-sitting carriage. Stronger for relaxed and premium trips, lighter for budget ones."""
    if t is None:
        return 0.0
    hours = t.duration_minutes / 60
    changes = int(t.meta.get("changes") or 0)
    pen = 0.5 * changes + (0.4 * changes if hours > 6 else 0)  # a missed connection on a long trip strands you
    if t.mode == "bus" and hours > 7:
        pen += 0.25 * (hours - 7)
    if t.mode == "train" and t.service_class == "2S" and hours > 5:
        pen += 0.25 * (hours - 5)  # 2S is a seat with no reservation guarantee: fine for 3 hours, not for 10
    styles = set(inp.styles)
    if styles & {"relaxed", "premium"}:
        pen *= 1.5
    elif "budget" in styles:
        pen *= 0.5
    return pen


def _leave_home(t: TransportData) -> datetime:
    return t.depart_at - timedelta(minutes=t.access_minutes + (0 if t.flexible else BOARDING_BUFFER))


def _home_again(t: TransportData) -> datetime:
    return t.arrive_at + timedelta(minutes=t.access_minutes)


def _valid_outbound(t: TransportData, inp: PlanInput) -> bool:
    """Leaving home up to an hour before the requested start is allowed (ranked lower, and said so)."""
    return _leave_home(t) >= inp.start - FLEX and t.arrive_at < inp.end


def _valid_return(t: TransportData, inp: PlanInput) -> bool:
    return _home_again(t) <= inp.end + FLEX and t.depart_at > inp.start


def outside_window_minutes(c: Combo, inp: PlanInput) -> tuple[int, int]:
    """(minutes before your start time you'd leave, minutes after your end time you'd be back)."""
    early = late = 0
    if c.outbound is not None:
        early = max(0, int((inp.start - _leave_home(c.outbound)).total_seconds() // 60))
    if c.ret is not None:
        late = max(0, int((_home_again(c.ret) - inp.end).total_seconds() // 60))
    return early, late


# --- Trains asked for by name ("take the Double Decker", "12985", "Shatabdi") ---------------------------------------
_TRAIN_FILLER = {"train", "trains", "express", "exp", "the", "by", "take", "special", "superfast", "sf", "mail", "from", "to",
                 "via", "a", "an", "on"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower().replace("-", " "))


def service_matches(t: TransportData, wanted: str) -> bool:
    """Is `t` the train the traveller named? A train number matches exactly; a name matches when every word of it is
    in the train's name, in any order ("double decker" finds "Jaipur - Delhi Sarai Rohilla Double Decker Express")."""
    w = (wanted or "").strip().lower()
    if re.fullmatch(r"\d{4,6}", w):  # a train number: this train's, or either one of a connection's
        return w in {str(t.meta.get("number") or "")} | {str(leg.get("number") or "") for leg in t.meta.get("legs") or []}
    name = _words(t.name)
    needed = [x for x in _words(w) if x not in _TRAIN_FILLER]
    return bool(needed) and all(x in name for x in needed)


def preferred_trains(inp: PlanInput) -> dict[str, list[str]]:
    """{"outbound": ["double decker"], "return": [...]}: the trains the traveller asked for, by direction."""
    prefer = (inp.pinned_transport or {}).get("prefer") or {}
    return {d: [str(w) for w in prefer.get(d) or [] if str(w).strip()] for d in ("outbound", "return")
            if prefer.get(d)}


def _wanted(t: TransportData, wanted: list[str]) -> bool:
    return any(service_matches(t, w) for w in wanted)


def _restrict_to_preferred(outs: list[TransportData], rets: list[TransportData], inp: PlanInput):
    """Keep only the trains the traveller named — for each direction where at least one of them is usable. A named
    train that can't be used (it leaves in the evening for a same-day trip) restricts nothing: the plan is built
    without it, and say_unmet_train_preferences() explains why."""
    want = preferred_trains(inp)
    if want.get("outbound") and any(_wanted(t, want["outbound"]) for t in outs):
        outs = [t for t in outs if _wanted(t, want["outbound"])]
    if want.get("return") and any(_wanted(t, want["return"]) for t in rets):
        rets = [t for t in rets if _wanted(t, want["return"])]
    return outs, rets


def build_combos(options: list[TransportData], inp: PlanInput, origin: PlaceData, dest: PlaceData) -> list[Combo]:
    pinned = inp.pinned_transport or {}
    outs = [t for t in options if t.direction == "outbound" and _valid_outbound(t, inp)]
    rets = [t for t in options if t.direction == "return" and _valid_return(t, inp)]
    if pinned.get("outbound") and any(option_key(t) == pinned["outbound"] for t in outs):
        outs = [t for t in outs if option_key(t) == pinned["outbound"]]
    if pinned.get("return") and any(option_key(t) == pinned["return"] for t in rets):
        rets = [t for t in rets if option_key(t) == pinned["return"]]
    wanted_outs, wanted_rets = _restrict_to_preferred(outs, rets, inp)
    combos = _combine(wanted_outs, wanted_rets, inp, dest)
    if not combos and (len(wanted_outs) != len(outs) or len(wanted_rets) != len(rets)):
        combos = _combine(outs, rets, inp, dest)  # the named train doesn't fit together with the way back: plan without
    return combos


def _combine(outs: list[TransportData], rets: list[TransportData], inp: PlanInput, dest: PlaceData) -> list[Combo]:
    combos = []
    for o in outs:
        dest_start = o.arrive_at + timedelta(minutes=0 if o.flexible else EXIT_BUFFER)
        for r in rets:
            dest_end = r.depart_at - timedelta(minutes=0 if r.flexible else BOARDING_BUFFER + 5)
            if (dest_end - dest_start).total_seconds() / 60 < MIN_DEST_MINUTES:
                continue
            if explore_minutes(dest_start, dest_end) < MIN_DEST_MINUTES:
                continue  # there in time to sleep, but never awake there in daylight: a trip of pure travel
            fo, ao = group_cost(o, inp.travellers)
            fr, ar = group_cost(r, inp.travellers)
            sp = (o.to_lat or dest.lat, o.to_lng or dest.lng)
            ep = (r.from_lat or dest.lat, r.from_lng or dest.lng)
            combos.append(Combo(o, r, fo + fr, ao + ar, dest_start, dest_end, sp, o.to_name or dest.name, ep,
                                r.from_name or dest.name, (o.comfort + r.comfort) / 2))
    return combos


def _why_not_usable(t: TransportData, inp: PlanInput, direction: str, dest_name: str) -> str:
    """In a few words, why a train the traveller asked for isn't in the plan."""
    if direction == "outbound":
        if _leave_home(t) < inp.start - FLEX:
            return f"it leaves before your {inp.start:%H:%M} start"
        if t.arrive_at >= inp.end:
            return f"it arrives after your {inp.end:%H:%M} end"
        if explore_minutes(t.arrive_at, inp.end) < MIN_DEST_MINUTES:
            return f"it reaches {dest_name} at {t.arrive_at:%H:%M}, too late to see anything in daylight"
        return "no way back leaves you enough time there"
    if t.depart_at <= inp.start:
        return "it leaves before your trip starts"
    if _home_again(t) > inp.end + FLEX:
        return f"it gets you home at {_home_again(t):%H:%M}, after your {inp.end:%H:%M} end"
    return "no way there leaves you enough time at the destination"


def unmet_train_preferences(outbound: TransportData | None, ret: TransportData | None, options: list[TransportData],
                            inp: PlanInput, origin_name: str, dest_name: str) -> list[str]:
    """One sentence for each trip direction where the traveller named a train and the plan doesn't use it: what was
    asked, what the timetable says about it, and why it can't be used — never a silent substitution."""
    out: list[str] = []
    for direction, chosen in (("outbound", outbound), ("return", ret)):
        names = preferred_trains(inp).get(direction)
        if not names or chosen is None or _wanted(chosen, names):
            continue
        a, b = (origin_name, dest_name) if direction == "outbound" else (dest_name, origin_name)
        said = " or ".join(f"\u201c{w}\u201d" for w in names)
        instead = chosen.name or MODE_TITLE.get(chosen.mode, chosen.mode)
        found = sorted((t for t in options if t.direction == direction and _wanted(t, names)), key=lambda t: t.depart_at)
        if not found:
            running = sorted({(t.depart_at, t.name) for t in options if t.direction == direction and t.mode == "train" and t.name})
            some = "; ".join(f"{n} {d:%H:%M}" for d, n in running[:3])
            out.append(f"I couldn't find a {said} train from {a} to {b} on {inp.start:%a %d %b}"
                       + (f" (trains that run: {some})" if some else "") + f", so I planned {instead} instead.")
            continue
        valid = _valid_outbound if direction == "outbound" else _valid_return
        t = min(found, key=lambda x: (not valid(x, inp), abs((x.depart_at - inp.start).total_seconds())))
        number = f" {t.meta['number']}" if t.meta.get("number") else ""
        out.append(f"The {t.name}{number} runs {t.depart_at:%H:%M}\u2013{t.arrive_at:%H:%M} from {t.from_name or a} to "
                   f"{t.to_name or b}, but {_why_not_usable(t, inp, direction, dest_name)}; I planned {instead} instead.")
    return out


def local_combo(inp: PlanInput, origin: PlaceData) -> Combo:
    home = (origin.lat, origin.lng)
    return Combo(None, None, Decimal(0), Decimal(0), inp.start, inp.end, home, origin.name, home, origin.name, 3)


PUBLIC_MODES = {"train", "bus", "metro", "flight"}


def public_first(combos: list[Combo], inp: PlanInput) -> list[Combo]:
    """Intercity trips (> 60 km) use public transport whenever it fits: a cab is used only if the traveller
    chose nothing but road transport, or no train/bus/flight fits the time window."""
    chosen = set(inp.transport_modes or [])
    if not (chosen & (PUBLIC_MODES | {"mixed"})):
        return combos  # the traveller asked for a cab or their own car
    def road_legs(c: Combo) -> int:
        return sum(1 for t in (c.outbound, c.ret) if t is not None and t.mode not in PUBLIC_MODES)
    fewest = min((road_legs(c) for c in combos), default=0)
    return [c for c in combos if road_legs(c) == fewest]


def _small_hours(t: datetime) -> bool:
    return t.hour < 5 or (t.hour == 5 and t.minute < 30)


def timing_penalty(c: Combo, inp: PlanInput) -> float:
    """Prefer journeys close to the times the traveller asked for, and never a 4 am check-out without reason."""
    pen = 0.0
    if c.outbound is not None:
        leave = _leave_home(c.outbound)
        late_start = (leave - (inp.start + FLEX)).total_seconds() / 60
        pen += 0.006 * max(0.0, late_start)  # a 12:48 train when you asked for 07:00 wastes the morning
        if _small_hours(leave):
            pen += 1.5
    if c.ret is not None:
        leave = c.ret.depart_at - timedelta(minutes=c.ret.access_minutes)
        overnight = c.ret.depart_at.hour >= 20 and c.ret.arrive_at.date() > c.ret.depart_at.date()
        if _small_hours(leave) and not overnight:
            pen += 1.5  # leaving the hotel between midnight and 5:30
        early_end = ((inp.end - FLEX) - _home_again(c.ret)).total_seconds() / 60
        if inp.end.date() > inp.start.date():
            pen += 0.002 * max(0.0, early_end)  # coming home much earlier than needed on a multi-day trip
    return round(pen, 4)


def rank_combos(combos: list[Combo], inp: PlanInput) -> list[Combo]:
    combos = public_first(combos, inp)
    styles = set(inp.styles)
    cost_w = 9.0 if "budget" in styles else (1.5 if "premium" in styles else 4.0)
    comfort_w = 1.2 if "premium" in styles else (0.2 if "budget" in styles else 0.5)
    budget = max(float(inp.budget), 1.0)
    for c in combos:
        # Daytime at the destination, not clock time: an overnight stay between a late arrival and an early
        # departure is not time to explore.
        hours = explore_minutes(c.dest_start, c.dest_end) / 60 if not c.local else \
            (c.dest_end - c.dest_start).total_seconds() / 3600
        per_day = hours / max(1, c.nights + 1)
        early, late = outside_window_minutes(c, inp)
        preferred = sum(1 for t in (c.outbound, c.ret) if t is not None and t.mode in inp.transport_modes)
        # Between train and bus (both "public"), train is the better default — faster, usually more comfortable,
        # often cheaper for the distance — so it's preferred even when the fare alone is a little higher. This is
        # a strong nudge, not an absolute rule: a bus that fits the time window when the only train doesn't will
        # still win, rather than the trip being wrongly called infeasible.
        train_legs = sum(1 for t in (c.outbound, c.ret) if t is not None and t.mode == "train")
        tiring = journey_penalty(c.outbound, inp) + journey_penalty(c.ret, inp)
        c.score = round(0.6 * min(per_day, 11) + comfort_w * c.comfort - cost_w * float(c.cost) / budget * 4
                        + 1.2 * preferred + 0.9 * train_legs - 0.008 * (early + late) - timing_penalty(c, inp)
                        - tiring, 4)
    return sorted(combos, key=lambda c: -c.score)


def combo_reason(c: Combo, all_combos: list[Combo], inp: PlanInput) -> str:
    if c.local:
        return "Your destination is close enough to explore locally, so no intercity ticket is needed."
    cheapest = min(all_combos, key=lambda x: x.cost)
    hours = explore_minutes(c.dest_start, c.dest_end) / 60
    parts = [f"gives you about {hours:.1f} daytime hours to explore"]
    if c is cheapest:
        parts.append("is the cheapest option that fits your time window")
    else:
        parts.append(f"costs {fmt_money(c.cost - cheapest.cost, inp.currency)} more than the cheapest option but "
                     f"is more comfortable" if c.comfort > cheapest.comfort
                     else f"costs {fmt_money(c.cost, inp.currency)} for the group")
    if c.outbound.mode in inp.transport_modes or c.ret.mode in inp.transport_modes:
        parts.append("matches your transport preference")
    early, late = outside_window_minutes(c, inp)
    if early:
        parts.append(f"means leaving {early} min before the start time you asked for")
    if late:
        parts.append(f"gets you back {late} min after your end time")
    return f"{c.label()} {parts[0]}, " + ", ".join(parts[1:]) + "."


# ---------------------------------------------------------------------------------------------------------
# Infeasibility analysis
# ---------------------------------------------------------------------------------------------------------

@dataclass
class MinCost:
    transport: Decimal
    stay: Decimal
    food: Decimal
    local: Decimal
    nights: int
    rooms: int
    travel_mode: str | None = None  # how the cheapest way there goes ("flight"), to name it in an explanation

    @property
    def total(self) -> Decimal:
        return self.transport + self.stay + self.food + self.local


def drop_noops(adjustments: list[dict], inp: PlanInput) -> list[dict]:
    """Remove suggested fixes that would change nothing: re-planning with them gives the same refusal, and the same
    suggestion again (e.g. "Fewer paid activities" for a trip that is already relaxed and budget-styled)."""
    current = {"budget": inp.budget, "stay": inp.stay, "pace": inp.pace, "trip_styles": sorted(inp.styles),
               "transport_modes": sorted(inp.transport_modes), "start_date": inp.start.date().isoformat(),
               "end_date": inp.end.date().isoformat(), "start_time": f"{inp.start:%H:%M}", "end_time": f"{inp.end:%H:%M}"}

    def changes(apply: dict) -> bool:
        if apply.get("action"):  # e.g. choose another destination: always something new for the traveller to do
            return True
        for key, value in apply.items():
            if key not in current:
                return True
            if key in ("trip_styles", "transport_modes"):
                value = sorted(value)
            elif key == "budget":
                value = Decimal(str(value))
            if value != current[key]:
                return True
        return False
    return [a for a in adjustments if changes(a.get("apply") or {})]


def ceil_to(value: Decimal, step: int = 500) -> int:
    return int(math.ceil(float(value) / step) * step)


def time_infeasible(inp: PlanInput, options: list[TransportData], modes: list[str], dest: PlaceData) -> dict:
    outs = [t for t in options if t.direction == "outbound"]
    rets = [t for t in options if t.direction == "return"]
    valid_out = [t for t in outs if _valid_outbound(t, inp)]
    valid_ret = [t for t in rets if _valid_return(t, inp)]
    adjustments = []
    if not options:
        conflict = f"No {', '.join(MODE_LABEL[m] for m in modes)} connection to {dest.name} was found for these dates."
    elif not valid_out:
        first = min(outs, key=lambda t: t.depart_at)
        need = first.depart_at - timedelta(minutes=first.access_minutes + BOARDING_BUFFER)
        conflict = (f"The earliest {MODE_LABEL[first.mode]} leaves {at(first)}, which means starting by "
                    f"{need:%H:%M} — before your {inp.start:%H:%M} start.")
        adjustments.append({"key": "start_earlier", "label": f"Start at {need:%H:%M}",
                            "description": "Leave a little earlier to catch the first departure.",
                            "apply": {"start_time": f"{need:%H:%M}"}})
    elif not valid_ret:
        # The return that gets you home SOONEST after your end time, not the one arriving last: suggesting the
        # latest (an overnight train home at 06:47 the next morning) moved the end to that early hour, where again
        # nothing fitted, so every re-plan pushed the trip one more day out.
        homes = [_home_again(t) for t in rets if t.depart_at > inp.start]
        back = min((h for h in homes if h > inp.end), default=None) or max(homes, default=None)
        if back is None:  # every way home leaves before you'd even get there
            first = min(valid_out, key=lambda t: t.arrive_at)
            conflict = (f"The earliest {MODE_LABEL[first.mode]} gets you to {dest.name} {at(first, first.arrive_at)}, and "
                        f"no way back leaves after that on {inp.end:%d %b}.")
            if (inp.end.date() + timedelta(days=1) - inp.start.date()).days <= MAX_TRIP_DAYS:
                adjustments.append({"key": "return_later", "label": "Add a day",
                                    "description": "Stay overnight and come back the next day.",
                                    "apply": {"end_date": (inp.end.date() + timedelta(days=1)).isoformat()}})
            back = None
        else:
            when = f"{back:%H:%M}" if back.date() == inp.end.date() else f"{back:%H:%M} on {back:%a %d %b}"
            conflict = (f"The earliest way home after your stay gets you back around {when}, after your "
                        f"{inp.end:%H:%M} return time on {inp.end:%d %b}.")
        if back is not None and (back.date() - inp.start.date()).days <= MAX_TRIP_DAYS:
            adjustments.append({"key": "return_later", "label": f"Return by {when}",
                                "description": "Allow a later return so the connection home fits.",
                                "apply": {"end_date": back.date().isoformat(), "end_time": f"{back:%H:%M}"}})
    else:
        # Services run both ways, but no pairing leaves a day at the destination. Name the pairing that came closest,
        # with its real times — "the Shatabdi gets you there at 22:30" explains far more than "the journey is long".
        def daylight(o: TransportData, r: TransportData) -> int:
            return explore_minutes(o.arrive_at + timedelta(minutes=0 if o.flexible else EXIT_BUFFER),
                                   r.depart_at - timedelta(minutes=0 if r.flexible else BOARDING_BUFFER + 5))

        o, r = max(((o, r) for o in valid_out for r in valid_ret), key=lambda p: daylight(*p))
        hours, mins = divmod(daylight(o, r), 60)
        left = f"{hours} h {mins:02d} min of daylight" if daylight(o, r) else "no daylight time at all"

        def svc(t: TransportData) -> str:
            return f"{t.name or MODE_TITLE.get(t.mode, t.mode)}{' ' + str(t.meta['number']) if t.meta.get('number') else ''}"
        conflict = (f"The best pairing is {svc(o)}, arriving in {dest.name} {at(o, o.arrive_at)}, with {svc(r)} home, "
                    f"leaving {at(r)}: that leaves {left} there, and you need at least "
                    f"{MIN_DEST_MINUTES // 60} hours to see anything. A trip like this is mostly travel.")
        # An earlier departure the start time ruled out may be all it takes: the 06:00 train makes a real day trip
        # where the 07:52 bus can't. Offer the latest start that works, before suggesting a night away.
        early = []
        for e in outs:
            leave = _leave_home(e)
            if _valid_outbound(e, inp) or leave.date() != inp.start.date() or leave.time() < EARLIEST_START:
                continue
            if any(daylight(e, x) >= MIN_DEST_MINUTES for x in valid_ret if x.depart_at > e.arrive_at):
                early.append((leave, e))
        if early:
            leave, e = max(early, key=lambda p: p[0])
            leave_at = leave.replace(minute=leave.minute - leave.minute % 5)
            adjustments.append({"key": "start_earlier", "label": f"Start at {leave_at:%H:%M}",
                                "description": f"Catch {svc(e)} {at(e)}, in {dest.name} "
                                               f"{at(e, e.arrive_at)}: a full day there, no night away.",
                                "apply": {"start_time": f"{leave_at:%H:%M}"}})
        if (inp.end.date() + timedelta(days=1) - inp.start.date()).days <= MAX_TRIP_DAYS:
            adjustments.append({"key": "return_later", "label": "Add a day",
                                "description": "Stay overnight so the trip isn't only travel.",
                                "apply": {"end_date": (inp.end.date() + timedelta(days=1)).isoformat()}})
    faster = [m for m in ("flight", "cab", "train") if m not in modes]
    if faster:
        adjustments.append({"key": "faster_transport", "label": f"Allow {MODE_LABEL[faster[0]]}",
                            "description": "A faster or more flexible mode may fit your window.",
                            "apply": {"transport_modes": sorted(set(inp.transport_modes) | {faster[0]})}})
    adjustments.append({"key": "nearby_destination", "label": "Pick a closer destination",
                        "description": f"A place nearer to {inp.origin_text} leaves more time for the trip itself.",
                        "apply": {"action": "change_destination"}})
    for a in adjustments:  # a fix that adds a night also books a bed for it, if the trip was planned without one
        end_date = a["apply"].get("end_date")
        if inp.stay == "no_stay" and end_date and end_date > inp.start.date().isoformat():
            a["apply"]["stay"] = "budget"
            a["description"] += " Includes a budget hotel for the night."
    return {"feasible": False, "kind": "time",
            "message": f"The requested trip is not feasible between {inp.start:%H:%M} on {inp.start:%d %b} and "
                       f"{inp.end:%H:%M} on {inp.end:%d %b}.",
            "main_conflict": conflict, "adjustments": drop_noops(adjustments, inp)}


def budget_infeasible(inp: PlanInput, mc: MinCost, cheapest_any_mode: Decimal | None, cheapest_stay: Decimal,
                      activities_cost: Decimal) -> dict:
    budget = inp.budget
    need = mc.total
    pct = buffer_pct(inp.styles)
    over = need - budget
    # Which requirement drives the overshoot? A cheaper mode only counts if the traveller actually ruled it out: with
    # every mode allowed, "your transport preference is the main reason" blamed a choice nobody made.
    chosen = set(inp.transport_modes or [])
    ruled_out = not ({"train", "bus"} <= chosen or "mixed" in chosen)
    cheaper_mode = cheapest_any_mode is not None and mc.transport > cheapest_any_mode and ruled_out
    drivers = []
    if mc.stay > cheapest_stay:
        drivers.append(("stay", mc.stay - cheapest_stay))
    if cheaper_mode:
        drivers.append(("transport", mc.transport - cheapest_any_mode))
    if mc.nights >= 2:
        per_night = (mc.stay / mc.nights) if mc.nights else Decimal(0)
        drivers.append(("duration", per_night + mc.food / max(1, mc.nights + 1)))
    driver = max(drivers, key=lambda d: d[1])[0] if drivers else "budget"
    if need >= 2 * budget:
        driver = "budget"  # far beyond the budget: no single preference explains it, and saying one does misleads

    who = f"{inp.travellers} traveller{'s' if inp.travellers > 1 else ''}"
    how = f" by {MODE_LABEL.get(mc.travel_mode, mc.travel_mode)}" if mc.travel_mode else ""
    tier = STAY_LABEL.get(inp.stay, inp.stay)
    if driver == "stay":
        conflict = (f"Your {tier} hotel requirement is the main reason the trip exceeds your budget: "
                    f"{mc.nights} night{'s' if mc.nights != 1 else ''} × {mc.rooms} "
                    f"room{'s' if mc.rooms != 1 else ''} costs {fmt_money(mc.stay, inp.currency)}.")
    elif driver == "transport":
        modes = ", ".join(MODE_LABEL.get(m, m) for m in inp.transport_modes)
        conflict = (f"Your transport choice ({modes}) is the main reason: the cheapest return journey with it costs "
                    f"{fmt_money(mc.transport, inp.currency)} for {inp.travellers}, against about "
                    f"{fmt_money(cheapest_any_mode, inp.currency)} by train or bus.")
    elif driver == "duration":
        conflict = (f"The trip length is the main reason: {mc.nights} nights of stay and meals add up to more than "
                    f"{fmt_money(budget, inp.currency)}.")
    else:
        conflict = (f"Even the most economical version — cheapest transport, basic meals and free sights — needs about "
                    f"{fmt_money(need, inp.currency)} for {who}, against your budget of {fmt_money(budget, inp.currency)}.")
        if mc.transport > 0:
            conflict += (f" Getting there and back{how} alone is about {fmt_money(mc.transport, inp.currency)} for "
                         f"{inp.travellers}.")

    adjustments = []
    if inp.stay in STAY_ORDER and mc.nights > 0:
        for t in STAY_ORDER[STAY_ORDER.index(inp.stay) + 1:]:
            # fare_model's rates are in rupees; mc.stay is in the trip's currency (Rp in Bali)
            new_stay = to_currency(F.stay_nightly(t), inp.currency) * F.stay_units(t, inp.travellers) * mc.nights
            if new_stay >= mc.stay:
                continue  # e.g. two hostel beds cost the same as one budget room
            if need - mc.stay + new_stay <= budget or t == STAY_ORDER[-1]:
                adjustments.append({"key": "cheaper_hotel", "label": f"Switch to {STAY_LABEL[t]}",
                                    "description": f"Saves about {fmt_money(mc.stay - new_stay, inp.currency)} on the stay.",
                                    "saves": float(mc.stay - new_stay), "apply": {"stay": t}})
                break
    if cheaper_mode:
        adjustments.append({"key": "cheaper_transport", "label": "Allow train and bus",
                            "description": f"Saves about {fmt_money(mc.transport - cheapest_any_mode, inp.currency)} on travel.",
                            "saves": float(mc.transport - cheapest_any_mode),
                            "apply": {"transport_modes": sorted(set(inp.transport_modes) | {"train", "bus"})}})
    if activities_cost > 0:
        adjustments.append({"key": "fewer_activities", "label": "Fewer paid activities",
                            "description": "A relaxed pace with mostly free sights.",
                            "saves": float(round_rupees(activities_cost * Decimal("0.5"))),
                            "apply": {"pace": "relaxed", "trip_styles": sorted(set(inp.styles) | {"budget"})}})
    if mc.nights >= 1:
        shorter = inp.end.date() - timedelta(days=1)
        per_night = mc.stay / mc.nights
        adjustments.append({"key": "shorter_trip", "label": f"One night fewer (back {shorter:%d %b})",
                            "description": f"Saves about {fmt_money(round_rupees(per_night + mc.food / (mc.nights + 1)), inp.currency)}.",
                            "saves": float(round_rupees(per_night + mc.food / (mc.nights + 1))),
                            "apply": {"end_date": shorter.isoformat()}})
    adjustments.append({"key": "nearby_destination", "label": "Pick a closer destination",
                        "description": f"Shorter travel from {inp.origin_text} cuts the biggest fixed cost.",
                        "apply": {"action": "change_destination"}})
    suggested = ceil_to(need * (1 + pct))
    adjustments.append({"key": "higher_budget", "label": f"Raise budget to {fmt_money(suggested, inp.currency)}",
                        "description": "Keeps every preference as it is, with a small buffer.",
                        "apply": {"budget": suggested}})
    return {"feasible": False, "kind": "budget",
            "message": f"The requested trip is not feasible within {fmt_money(budget, inp.currency)}.",
            "main_conflict": conflict,
            "minimum": {"transport": float(mc.transport), "stay": float(mc.stay), "food": float(mc.food),
                        "local": float(mc.local), "total": float(need), "over_by": float(over)},
            "adjustments": drop_noops(adjustments, inp)}


def member_warnings(inp: PlanInput, planned: Decimal) -> list[str]:
    if not inp.member_budgets:
        return []
    share = planned / max(1, inp.travellers)
    return [f"{name}'s budget ({fmt_money(b, inp.currency)}) is below their equal share of {fmt_money(share, inp.currency)}."
            for name, b in inp.member_budgets if b is not None and b < share]