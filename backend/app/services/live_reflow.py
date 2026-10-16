"""Live trip mode (ADR 0021): "we're running late" re-flows the rest of one day of the plan.

Deterministic and offline: it uses only what the saved plan already knows (times, travel minutes between stops,
opening and closing hours, ratings) and never calls a provider, so it costs nothing and doesn't use a re-plan.

Rules, in order:
1. Stops from `from_item_id` on (default: the first stop that hasn't started yet) start `minutes` later. A gap that
   was already in the plan absorbs the delay before anything else moves.
2. Tickets (trains, flights and their arrival) never move. If the delay makes a ticket impossible to catch, the least
   important stops before it are dropped; if that still isn't enough, nothing changes and the answer says which ticket
   would be missed.
3. A stop that would now be closed (or a sunset now more than 20 minutes late) is dropped, least important first (meals last, a pinned must-see only if nothing
   else helps). A visit may be shortened to fit before closing, down to 60% of its time and never under 30 minutes.
4. The day may run later by at most the delay, and never past 23:30, or past the trip's end time on its last day.

Saved as a new itinerary version ("Running 45 min late"), so Undo is the previous version (`undo`).
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.trip import Alternative, Itinerary, ItineraryItem, Trip

FIXED = {"transport", "arrival"}  # tickets: a train or flight and the arrival it brings
NEVER_DROP = {"stay", "transport", "arrival", "free"}
MIN_VISIT = 30
LATEST = time(23, 30)


@dataclass
class Stop:
    item: ItineraryItem
    start: datetime
    end: datetime
    dropped: bool = False

    @property
    def minutes(self) -> int:
        return int((self.item.end_at - self.item.start_at).total_seconds() // 60)


@dataclass
class Result:
    status: str  # shifted | missed | nothing
    moved: int = 0
    dropped: list[str] = field(default_factory=list)
    shortened: list[str] = field(default_factory=list)
    missed: dict | None = None
    note: str | None = None

    def as_dict(self) -> dict:
        return {"status": self.status, "moved": self.moved, "dropped": self.dropped, "shortened": self.shortened,
                "missed": self.missed, "note": self.note}


def _clock(day: datetime, hhmm: str | None) -> datetime | None:
    if not hhmm or len(hhmm) < 4:
        return None
    try:
        h, m = (int(x) for x in hhmm.split(":")[:2])
    except ValueError:
        return None
    if h >= 24:
        return None
    return datetime.combine(day.date(), time(h, m))


def importance(item: ItineraryItem, pinned: set[str]) -> float:
    """Higher is kept longer. Meals and must-sees last; then sunsets; then the better-known sights."""
    meta = item.meta or {}
    if item.kind in NEVER_DROP:
        return math.inf
    if item.place_id and str(item.place_id) in pinned or meta.get("key") in pinned or meta.get("pinned"):
        return 900
    if item.kind == "meal":
        return 800
    rating = float(meta.get("rating") or 0)
    count = float(meta.get("rating_count") or 0)
    base = 60 if item.kind == "sunset" else 0
    return base + rating * 10 + math.log10(count + 1) * 5


def _boarding(item: ItineraryItem) -> int:
    meta = item.meta or {}
    if item.kind != "transport":
        return 0
    if meta.get("check_in_minutes"):
        return int(meta["check_in_minutes"])
    return 15


def _schedule(stops: list[Stop], first: int, minutes: int, day_limit: datetime) -> tuple[list[str], dict | None]:
    """Lay the kept stops out again from index `first`. Returns the names of visits that had to be shortened and, when a
    ticket can't be caught or a stop is closed or the day overflows, the first problem as {kind, index}."""
    shortened: list[str] = []
    prev_end: datetime | None = None
    # when the traveller is free again: the first stop to move, `minutes` after it was due
    ready = stops[first].item.start_at + timedelta(minutes=minutes)
    for n, s in enumerate(stops):
        if n < first:
            prev_end = s.end
            continue
        if s.dropped:
            continue
        it = s.item
        travel = timedelta(minutes=it.travel_minutes_from_prev or 0)
        if it.kind in FIXED:
            s.start, s.end = it.start_at, it.end_at
            free = ready if prev_end is None or prev_end < ready else prev_end
            if it.kind == "transport" and free + travel > it.start_at - timedelta(minutes=_boarding(it)):
                return shortened, {"kind": "ticket", "index": n}
            prev_end, ready = s.end, s.end
            continue
        # a stop starts when it was due, or when we can get there, whichever is later
        start = max(it.start_at, ready if n == first else max(ready, prev_end or ready) + travel)
        dur = s.minutes
        end = start + timedelta(minutes=dur)
        closes = _clock(start, it.closing_time)
        opens = _clock(start, it.opening_time)
        if closes is not None and opens is not None and closes <= opens:  # open past midnight (18:00-02:00)
            closes += timedelta(days=1)
        if it.kind == "sunset" and start > it.start_at + timedelta(minutes=20):  # the sun doesn't wait
            return shortened, {"kind": "closed", "index": n}
        if it.kind not in NEVER_DROP and closes is not None and closes > start and end > closes:
            fit = int((closes - start).total_seconds() // 60)
            if dur and fit >= max(MIN_VISIT, math.ceil(dur * 0.6)):
                end = closes
                shortened.append(it.title)
            else:
                return shortened, {"kind": "closed", "index": n}
        elif it.kind not in NEVER_DROP and closes is not None and start >= closes:
            return shortened, {"kind": "closed", "index": n}
        if it.kind not in NEVER_DROP and end > day_limit:
            return shortened, {"kind": "late", "index": n}
        s.start, s.end = start, end
        prev_end = end
    return shortened, None


def reflow(items: list[ItineraryItem], minutes: int, from_item_id: uuid.UUID | None, *, pinned: set[str],
           now: datetime | None = None, day_end: time | None = None) -> tuple[Result, list[Stop]]:
    """`items`: one day of the plan, in order. Changes nothing on them; returns the new layout to save."""
    stops = [Stop(i, i.start_at, i.end_at) for i in sorted(items, key=lambda i: i.position)]
    if not stops or minutes <= 0:
        return Result("nothing", note="Nothing to move."), stops
    if from_item_id is not None:
        first = next((n for n, s in enumerate(stops) if s.item.id == from_item_id), None)
    else:
        first = next((n for n, s in enumerate(stops) if s.item.kind not in FIXED and (now is None or s.item.start_at >= now)), None)
    if first is None:
        return Result("nothing", note="Nothing left to move today."), stops
    movable = [s for s in stops[first:] if s.item.kind not in FIXED]
    if not movable:
        return Result("nothing", note="Only tickets left today, and those keep their times."), stops
    planned_end = max(s.item.end_at for s in stops if s.item.kind not in FIXED)
    cap = datetime.combine(planned_end.date(), day_end or LATEST)
    day_limit = min(max(planned_end, planned_end + timedelta(minutes=minutes)), cap if cap > planned_end else planned_end)

    shortened: list[str] = []
    for _ in range(len(stops) + 1):
        shortened, problem = _schedule(stops, first, minutes, day_limit)
        if problem is None:
            break
        n = problem["index"]
        if problem["kind"] == "ticket":
            # drop the least important stop between where we are and the ticket
            before = [s for s in stops[first:n] if not s.dropped and s.item.kind not in NEVER_DROP]
        elif problem["kind"] == "closed":
            before = [stops[n]]
        else:  # the day runs too late: anything still to come may go
            before = [s for s in stops[first:] if not s.dropped and s.item.kind not in NEVER_DROP]
        if not before:
            if problem["kind"] == "ticket":
                t = stops[n].item
                for s in stops:
                    s.dropped, s.start, s.end = False, s.item.start_at, s.item.end_at
                return Result("missed", missed={"title": t.title, "time": t.start_at.strftime("%H:%M")},
                              note=f"Running {minutes} min late means missing {t.title} at {t.start_at.strftime('%H:%M')}. "
                                   "Re-plan the trip to find another way."), stops
            break
        victim = min(before, key=lambda s: importance(s.item, pinned))
        victim.dropped = True

    moved = sum(1 for s in stops if not s.dropped and (s.start != s.item.start_at or s.end != s.item.end_at))
    dropped = [s.item.title for s in stops if s.dropped]
    shortened = [t for t in shortened if t not in dropped]
    parts = [f"Running {_late(minutes)} late"]
    if dropped:
        parts.append("left out " + _join(dropped))
    if shortened:
        parts.append("shorter visit at " + _join(shortened))
    return Result("shifted" if moved or dropped else "nothing", moved=moved, dropped=dropped, shortened=shortened,
                  note="; ".join(parts) if moved or dropped else "The plan already had room for that delay."), stops


def _late(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h} h {m} min" if h and m else f"{h} h" if h else f"{m} min"


def _join(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


# ------------------------------------------------------------------------------------------------------ saving

def _copy_item(i: ItineraryItem, itinerary_id: uuid.UUID, position: int, start: datetime, end: datetime) -> ItineraryItem:
    meta = dict(i.meta or {})
    return ItineraryItem(
        itinerary_id=itinerary_id, position=position, day_index=i.day_index, kind=i.kind, start_at=start, end_at=end,
        title=i.title, subtitle=i.subtitle, place_id=i.place_id, transport_option_id=i.transport_option_id,
        distance_from_prev_m=i.distance_from_prev_m, travel_minutes_from_prev=i.travel_minutes_from_prev,
        travel_mode=i.travel_mode, travel_cost=i.travel_cost, leg_state=i.leg_state, polyline=i.polyline, cost=i.cost,
        cost_state=i.cost_state, recommended_minutes=i.recommended_minutes, opening_time=i.opening_time,
        closing_time=i.closing_time, hours_state=i.hours_state, hours_note=i.hours_note, sources=list(i.sources or []),
        reasons=list(i.reasons or []), explanation=i.explanation, meta=meta, google_fetched_at=i.google_fetched_at)


def _less_budget(breakdown: dict, dropped: list[ItineraryItem]) -> tuple[dict, Decimal]:
    """Take the dropped stops' own costs out of the budget breakdown (their lines and their categories)."""
    b = {**breakdown, "categories": [dict(c, items=list(c.get("items") or [])) for c in breakdown.get("categories") or []]}
    saved = Decimal("0")
    cat_of = {"meal": "food", "activity": "activities", "sunset": "activities"}
    for i in dropped:
        cost = Decimal(str(i.cost or 0))
        if cost <= 0:
            continue
        saved += cost
        cat = next((c for c in b["categories"] if c.get("key") == cat_of.get(i.kind)), None)
        if cat is None:
            continue
        cat["amount"] = float(Decimal(str(cat.get("amount") or 0)) - cost)
        for n, line in enumerate(cat["items"]):
            if i.title in str(line.get("label", "")) and abs(float(line.get("amount") or 0) - float(cost)) < 0.01:
                cat["items"].pop(n)
                break
    if saved:
        for k in ("planned", "subtotal"):
            if k in b:
                b[k] = float(Decimal(str(b[k])) - saved)
        if "remaining" in b:
            b["remaining"] = float(Decimal(str(b["remaining"])) + saved)
        if b.get("travellers"):
            b["per_person"] = round(float(b.get("planned", 0)) / max(1, int(b["travellers"])), 2)
    return b, saved


def apply_late(db: Session, trip: Trip, day_index: int, minutes: int, from_item_id: uuid.UUID | None,
               now: datetime | None = None) -> Result:
    it = trip.current_itinerary
    if it is None:
        raise AppError("Plan this trip first.", code="not_planned", status_code=409)
    day_items = [i for i in it.items if i.day_index == day_index]
    if not day_items:
        raise AppError("That day isn't in the plan.", code="no_such_day", status_code=404)
    last_day = max(i.day_index for i in it.items)
    pinned = {str(p) for p in (trip.preferences.pinned_place_ids if trip.preferences else []) or []}
    result, stops = reflow(day_items, minutes, from_item_id, pinned=pinned, now=now,
                           day_end=trip.end_time if day_index == last_day and trip.end_time > time(6) else None)
    if result.status != "shifted":
        return result
    layout = {s.item.id: s for s in stops}
    version = (db.scalar(select(Itinerary.version).where(Itinerary.trip_id == trip.id)
                         .order_by(Itinerary.version.desc()).limit(1)) or 0) + 1
    breakdown, saved = _less_budget(it.budget_breakdown or {}, [s.item for s in stops if s.dropped])
    db.execute(update(Itinerary).where(Itinerary.trip_id == trip.id).values(is_current=False))
    new = Itinerary(trip_id=trip.id, version=version, is_current=True, title=it.title, summary=it.summary,
                    tips=list(it.tips or []), total_cost=(it.total_cost or 0) - saved, remaining=(it.remaining or 0) + saved,
                    budget_breakdown=breakdown, warnings=list(it.warnings or []), pipeline_trace=list(it.pipeline_trace or []),
                    weather=it.weather, llm_status=it.llm_status, change_note=result.note,
                    candidate_pool={**(it.candidate_pool or {}),
                                    "live": {"from_version": it.version, "minutes": minutes, "day_index": day_index}})
    db.add(new)
    db.flush()
    pos = 0
    for i in sorted(it.items, key=lambda x: x.position):
        s = layout.get(i.id)
        if s is not None and s.dropped:
            continue
        start, end = (s.start, s.end) if s is not None else (i.start_at, i.end_at)
        row = _copy_item(i, new.id, pos, start, end)
        if s is not None and (start != i.start_at or end != i.end_at):
            row.meta = {**row.meta, "live_shifted": True}
        db.add(row)
        db.flush()
        for a in i.alternatives:
            db.add(Alternative(itinerary_item_id=row.id, place_id=a.place_id, transport_option_id=a.transport_option_id,
                               kind=a.kind, title=a.title, rank=a.rank, score=a.score, cost=a.cost, cost_delta=a.cost_delta,
                               time_delta_minutes=a.time_delta_minutes, distance_from_prev_m=a.distance_from_prev_m,
                               reason=a.reason, checks=dict(a.checks or {}), meta=dict(a.meta or {}),
                               google_fetched_at=a.google_fetched_at))
        pos += 1
    return result


def undo(db: Session, trip: Trip) -> bool:
    """Back to the version before the last "running late" change. False when the current version isn't one."""
    it = trip.current_itinerary
    live = (it.candidate_pool or {}).get("live") if it else None
    if not live:
        return False
    before = next((x for x in trip.itineraries if x.version == live.get("from_version")), None)
    if before is None:
        return False
    db.execute(update(Itinerary).where(Itinerary.trip_id == trip.id).values(is_current=False))
    db.execute(update(Itinerary).where(Itinerary.id == before.id).values(is_current=True))
    db.delete(it)
    return True
