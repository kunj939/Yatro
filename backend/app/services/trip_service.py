"""Trip use-cases shared by the HTTP routes (and, later, any other client such as a mobile app)."""
from __future__ import annotations

import difflib
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.gateway import context as gateway_context
from app.services import google_content
from app.services import train_fact_gate as gate
from app.core.config import get_settings
from app.core.errors import AppError, NotFound, ProviderUnavailable
from app.models.trip import Alternative, Itinerary, ItineraryItem, Trip, TripMember, TripPreferences
from app.models.user import User
from app.providers.registry import Providers
from app.schemas.trip import PreferencesPatch, TripCreate
from app.services import age_fit, expense_service, quota_service
from app.services import intent as intent_service
from app.core import telemetry
from app.services.engine_types import StageReporter
from app.services.interest_tags import INTEREST_LABELS, expand_chips
from app.services.planner import Planner, PlanResult
from app.utils.locale import locale_for
from app.utils.money import D, fmt_money

log = logging.getLogger("tripai.trips")

MAX_TRIP_DAYS = 7  # same limit as the wizard and feasibility.MAX_TRIP_DAYS
TYPE_LABEL = {"solo": "Solo", "couple": "Couple", "partner": "Partner", "friends": "Friends", "family": "Family"}


# ------------------------------------------------------------------------------------------------------
# Loading
# ------------------------------------------------------------------------------------------------------

def load_trip(db: Session, trip_id: uuid.UUID, owner: User | None = None, for_update: bool = False) -> Trip:
    q = (select(Trip).where(Trip.id == trip_id)
         .options(selectinload(Trip.preferences), selectinload(Trip.members), selectinload(Trip.expenses),
                  selectinload(Trip.itineraries).selectinload(Itinerary.items).selectinload(ItineraryItem.alternatives))
         .execution_options(populate_existing=True))
    trip = db.scalar(q)
    if trip is None or (owner is not None and trip.owner_id != owner.id):
        raise NotFound("Trip not found.")
    return trip


def list_trips(db: Session, user: User) -> list[Trip]:
    return list(db.scalars(select(Trip).where(Trip.owner_id == user.id).order_by(Trip.created_at.desc())
                           .options(selectinload(Trip.preferences), selectinload(Trip.itineraries))))


# ------------------------------------------------------------------------------------------------------
# Creation & changes
# ------------------------------------------------------------------------------------------------------

def default_title(origin: str, dest: str, start: date, end: date) -> str:
    """`end` is the last day spent travelling (a return just after midnight still counts as the same day)."""
    days = (end - start).days + 1
    return f"{dest} {'day trip' if days == 1 else f'{days}-day trip'}" if dest else f"Trip from {origin}"


def create_trip(db: Session, user: User, data: TripCreate) -> Trip:
    end_date = data.end_date or data.start_date
    # The destination's own timezone and currency, worked out offline from its coordinates — a trip to
    # London runs on Europe/London and shows £, not IST and ₹. Falls back to India only when the wizard
    # didn't have coordinates for the destination (a freely typed name with no autocomplete pick).
    timezone, currency = locale_for(data.destination.lat, data.destination.lng)
    trip = Trip(owner_id=user.id, status="draft",
                title=(data.title or default_title(data.origin.name, data.destination.name, data.start_date,
                                                   end_date if data.end_time > time(6) else end_date - timedelta(days=1))),
                origin_name=data.origin.name, origin_place_id=data.origin.place_id, origin_lat=data.origin.lat,
                origin_lng=data.origin.lng, destination_name=data.destination.name,
                destination_place_id=data.destination.place_id, destination_lat=data.destination.lat,
                destination_lng=data.destination.lng, start_date=data.start_date, end_date=end_date,
                start_time=data.start_time, end_time=data.end_time, budget=data.budget, travellers=data.travellers,
                timezone=timezone, currency=currency)
    trip.preferences = TripPreferences(traveller_type=data.traveller_type, age_groups=list(data.age_groups),
                                       transport_modes=list(data.transport_modes),
                                       food=data.food, trip_styles=list(data.trip_styles), stay=data.stay,
                                       pace=data.pace, notes=data.notes, interests=expand_chips(data.interests),
                                       excluded_place_ids=[],
                                       pinned_place_ids=list(dict.fromkeys(data.include_place_ids or []))[:10],
                                       pinned_transport={})
    trip.members = [TripMember(user_id=user.id, name=user.name[:80], is_owner=True, food_pref=data.food)]
    db.add(trip)
    db.flush()
    return trip


def _keep_train_preference(pinned: dict | None) -> dict:
    """Specific options chosen earlier (a swapped bus) stop applying when the timing or modes change; a train the
    traveller asked for by name still does."""
    return {k: v for k, v in (pinned or {}).items() if k == "prefer"}


def apply_patch(trip: Trip, patch: PreferencesPatch) -> list[str]:
    """Apply a structured change set. Returns human-readable notes of what changed."""
    p = trip.preferences
    notes: list[str] = []
    if patch.budget is not None and D(patch.budget) != D(trip.budget):
        notes.append(f"budget {fmt_money(trip.budget, trip.currency)} → {fmt_money(patch.budget, trip.currency)}")
        trip.budget = D(patch.budget)
    if patch.travellers is not None and patch.travellers != trip.travellers:
        notes.append(f"{patch.travellers} travellers")
        trip.travellers = patch.travellers
    timing_changed = False
    def auto_title() -> str:  # the title create_trip() would have given these dates
        last = trip.end_date if trip.end_time > time(6) else trip.end_date - timedelta(days=1)
        return default_title(trip.origin_name, trip.destination_name, trip.start_date, max(trip.start_date, last))
    # Any "<destination> day trip" / "<destination> N-day trip" was ours, even if earlier changes left it stale
    titled_automatically = bool(re.fullmatch(rf"{re.escape(trip.destination_name or '')} (day trip|\d+-day trip)",
                                             trip.title or ""))
    for field in ("start_date", "end_date", "start_time", "end_time"):
        value = getattr(patch, field)
        if value is not None and value != getattr(trip, field):
            setattr(trip, field, value)
            timing_changed = True
    if timing_changed:
        if datetime.combine(trip.end_date, trip.end_time) <= datetime.combine(trip.start_date, trip.start_time):
            if trip.end_date == trip.start_date:
                trip.end_date = trip.start_date + timedelta(days=1)
            else:
                raise AppError("The trip can't end before it starts.", code="invalid_dates", status_code=422)
        if (trip.end_date - trip.start_date).days > MAX_TRIP_DAYS:
            raise AppError(f"Trips can be at most {MAX_TRIP_DAYS} days in this version.", code="trip_too_long",
                           status_code=422)
        if titled_automatically:  # "Rishikesh 2-day trip" must not survive a change to six days
            trip.title = auto_title()
        p.pinned_transport = _keep_train_preference(p.pinned_transport)
        notes.append("new timing")
    for field, label in (("traveller_type", "traveller type"), ("food", "food preference"), ("stay", "stay"),
                         ("pace", "pace"), ("notes", "notes")):
        value = getattr(patch, field)
        if value is not None and value != getattr(p, field):
            setattr(p, field, value)
            notes.append(f"{label}: {value}".replace("_", " "))
    if patch.age_groups is not None and list(patch.age_groups) != list(p.age_groups or []):
        p.age_groups = list(patch.age_groups)
        notes.append("ages: " + (", ".join(age_fit.LABELS[g] for g in p.age_groups) or "not given"))
    if patch.transport_modes is not None and list(patch.transport_modes) != list(p.transport_modes or []):
        p.transport_modes = list(dict.fromkeys(patch.transport_modes))
        p.pinned_transport = _keep_train_preference(p.pinned_transport)
        notes.append("transport: " + ", ".join(m.replace("_", " ") for m in p.transport_modes))
    if patch.trip_styles is not None and list(patch.trip_styles) != list(p.trip_styles or []):
        p.trip_styles = list(dict.fromkeys(patch.trip_styles))
        notes.append("style: " + ", ".join(p.trip_styles))
    if patch.reset_pins:
        p.pinned_place_ids, p.excluded_place_ids, p.pinned_transport = [], [], {}
    if patch.exclude_place_ids:
        p.excluded_place_ids = list(dict.fromkeys([*(p.excluded_place_ids or []), *patch.exclude_place_ids]))[-50:]
        p.pinned_place_ids = [k for k in (p.pinned_place_ids or []) if k not in patch.exclude_place_ids]
    if patch.include_place_ids:
        p.pinned_place_ids = list(dict.fromkeys([*(p.pinned_place_ids or []), *patch.include_place_ids]))[-20:]
        p.excluded_place_ids = [k for k in (p.excluded_place_ids or []) if k not in patch.include_place_ids]
    if patch.interests is not None:
        # An explicit chip selection replaces the current favourites (unlike chat-derived interests below,
        # which only add to them: a definite pick and an incidental mention in conversation aren't the same).
        p.interests = expand_chips(patch.interests)
        notes.append("favourites: " + ", ".join(INTEREST_LABELS[c] for c in patch.interests) if patch.interests
                     else "cleared your favourites")
    return notes


def swap_patch(trip: Trip, item_id: uuid.UUID, alternative_id: uuid.UUID) -> tuple[PreferencesPatch | None, str]:
    it = trip.current_itinerary
    item = next((i for i in (it.items if it else []) if i.id == item_id), None)
    if item is None:
        raise NotFound("That itinerary item no longer exists. Refresh and try again.")
    alt = next((a for a in item.alternatives if a.id == alternative_id), None)
    if alt is None:
        raise NotFound("That alternative is no longer available.")
    note = f"Swapped {item.title} for {alt.title}"
    if alt.kind == "transport":
        direction = "outbound" if item.meta.get("key", "").startswith("outbound") else "return"
        pinned = dict(trip.preferences.pinned_transport or {})
        pinned[direction] = alt.meta.get("key")
        prefer = dict(pinned.get("prefer") or {})
        prefer.pop(direction, None)  # an explicit swap overrides a train asked for by name, for that direction
        pinned["prefer"] = prefer
        if not prefer:
            pinned.pop("prefer")
        trip.preferences.pinned_transport = pinned
        return None, note
    old_key, new_key = item.meta.get("key"), alt.meta.get("key")
    if not new_key:
        raise AppError("This alternative can't be applied.", code="invalid_alternative", status_code=422)
    return PreferencesPatch(exclude_place_ids=[old_key] if old_key else None, include_place_ids=[new_key]), note


STYLE_WORDS = {"budget": "cheaper choices", "premium": "more comfort", "romantic": "a romantic feel",
               "adventure": "more adventure", "sightseeing": "more sightseeing", "food": "a food focus",
               "relaxed": "a relaxed feel", "balanced": "a balanced mix"}
FOOD_WORDS = {"veg": "vegetarian food", "non_veg": "non-vegetarian food", "both": "veg and non-veg food"}
STAY_WORDS = {"premium": "a 5-star stay", "tier1": "a 4-star stay", "tier2": "a 3-star stay", "budget": "a budget hotel",
              "oyo": "an economy room", "hostel": "a hostel", "no_stay": "no hotel"}
MODE_WORDS = {"train": "train", "bus": "bus", "metro": "metro", "cab": "cab", "own_car": "your own car",
              "flight": "flight", "mixed": "the best mix"}
CHANGE_TRY = ("Try: “budget 8000” · “add a day” · “start at 9am” · “we are 4” · “only veg” · “add Fatehpur Sikri” · "
              "“skip Agra Fort” · “more shopping” · “no temples”.")


@dataclass
class IntentOutcome:
    """What a typed request turned into for this trip: the changes, in words, and everything that couldn't be done."""
    patch: PreferencesPatch
    interests: list[str] | None  # the new interest tags ("-temple" = see fewer), or None if unchanged
    labels: list[str]
    issues: list[str]
    reply: str | None
    source: str
    prefer_trains: dict | None = None  # {"outbound": ["double decker"], "return": [...]}, or None if unchanged
    vague: bool = False  # nothing in the request was recognised at all
    asked: str = ""  # what the traveller typed, for the answer to quote

    @property
    def changed(self) -> bool:
        return bool(self.labels)

    def note(self) -> str:
        """The one message shown to the traveller: what changed, then what didn't."""
        parts = ["Updated: " + "; ".join(self.labels) + "."] if self.labels else []
        return " ".join([*parts, *self.issues]).strip()

    def nothing_message(self) -> str:
        """What to say when there was no change to make. A request nothing recognised also gets the list of things
        that can be asked for."""
        if self.vague:
            quoted = self.asked if len(self.asked) <= 60 else self.asked[:57] + "…"
            return self.reply or f"I couldn't turn “{quoted}” into a change. {CHANGE_TRY}"
        if self.issues:
            return " ".join(self.issues)
        return self.reply or f"Nothing to change there. {CHANGE_TRY}"


def _shift_time(t: time, minutes: int) -> time:
    total = max(0, min(23 * 60 + 59, t.hour * 60 + t.minute + minutes))
    return time(total // 60, total % 60)


def _similar(request: str, found: str) -> bool:
    """Is the place a lookup returned plausibly the one that was asked for? ("fatehpur sikri" -> "Fatehpur Sikri")"""
    want = [w for w in re.findall(r"[a-z0-9]+", request.lower()) if len(w) >= 3]
    have = re.findall(r"[a-z0-9]+", found.lower())
    return any(difflib.SequenceMatcher(None, w, h).ratio() >= 0.75 for w in want for h in have)


def intent_patch(db: Session, providers: Providers, user: User, trip: Trip, message: str) -> IntentOutcome:
    """Turn a typed request into changes for `trip`. Never raises for a request it can't act on: what can be done
    is returned as changes, and what can't is explained in `issues`."""
    it = trip.current_itinerary
    p = trip.preferences
    pool = (it.candidate_pool or {}).get("places", []) if it else []
    planned = {(i.meta or {}).get("key") for i in (it.items if it else [])} - {None}
    candidates = [{**c, "planned": c["id"] in planned} for c in pool]
    names = {c["id"]: c["name"] for c in pool}
    days = (trip.end_date - trip.start_date).days + 1
    ctx = {"destination": trip.destination_name, "origin": trip.origin_name, "start_date": trip.start_date.isoformat(),
           "end_date": trip.end_date.isoformat(), "days": days, "start_time": trip.start_time.strftime("%H:%M"),
           "end_time": trip.end_time.strftime("%H:%M"), "budget": float(trip.budget), "currency": trip.currency,
           "travellers": trip.travellers, "traveller_type": p.traveller_type, "styles": p.trip_styles, "food": p.food,
           "stay": p.stay, "pace": p.pace, "transport": p.transport_modes,
           "planned_stops": [i.title for i in (it.items if it else []) if i.kind in ("activity", "meal", "sunset")]}
    intent = intent_service.understand(providers.llm, message, ctx, candidates, db=db, user_id=user.id, trip_id=trip.id)
    labels: list[str] = []
    issues: list[str] = []
    money = lambda v: fmt_money(v, trip.currency)  # noqa: E731

    # budget
    budget = None
    if intent.budget_change:
        amount, current = D(intent.budget_change["amount"]), D(trip.budget)
        mode = intent.budget_change["mode"]
        new = amount if mode == "set" else (current + amount if mode == "increase" else current - amount)
        if new <= 0:
            issues.append(f"That would leave nothing to spend; your budget stays {money(current)}.")
        elif new == current:
            issues.append(f"Your budget is already {money(current)}.")
        else:
            budget = new
            labels.append(f"budget {money(current)} → {money(new)}")

    # trip length
    end_date = None
    target = None
    if intent.days_total:
        target = trip.start_date + timedelta(days=intent.days_total - 1)
    elif intent.extend_days:
        target = trip.end_date + timedelta(days=intent.extend_days)
    if target is not None:
        if target < trip.start_date:
            target = trip.start_date
            issues.append("A trip needs at least one day, so I made it a day trip.")
        if (target - trip.start_date).days > MAX_TRIP_DAYS:
            target = trip.start_date + timedelta(days=MAX_TRIP_DAYS)
            issues.append(f"Trips can be at most {MAX_TRIP_DAYS + 1} days, so I set it to {MAX_TRIP_DAYS + 1}.")
        if target == trip.end_date:
            issues.append(f"The trip is already {(target - trip.start_date).days + 1} day"
                          f"{'s' if target != trip.start_date else ''} long.")
        else:
            end_date = target
            n = (target - trip.start_date).days + 1
            labels.append("a day trip" if n == 1 else f"{n} days")

    # start and end times
    start_time = end_time = None
    new_start = time.fromisoformat(intent.start_time) if intent.start_time else None
    new_end = time.fromisoformat(intent.end_time) if intent.end_time else None
    if intent.start_shift and not new_start:
        new_start = _shift_time(trip.start_time, intent.start_shift)
    if intent.end_shift and not new_end:
        new_end = _shift_time(trip.end_time, intent.end_shift)
    if new_start and new_start != trip.start_time:
        start_time = new_start
        labels.append(f"start at {new_start:%H:%M}")
    elif new_start:
        issues.append(f"The trip already starts at {new_start:%H:%M}.")
    if new_end and new_end != trip.end_time:
        end_time = new_end
        labels.append(f"back by {new_end:%H:%M}")
    elif new_end:
        issues.append(f"The trip already ends at {new_end:%H:%M}.")

    # who is travelling
    travellers = traveller_type = None
    count = intent.travellers or (min(20, trip.travellers + intent.travellers_add) if intent.travellers_add else None)
    kind = intent.traveller_type
    if count and count != trip.travellers:
        travellers = count
        labels.append(f"{count} traveller{'s' if count != 1 else ''}")
        if not kind:  # keep the group type believable: four people aren't a "couple"
            if count == 1:
                kind = "solo"
            elif count > 2 and p.traveller_type in ("solo", "couple", "partner"):
                kind = "friends"
    if kind and kind != p.traveller_type:
        traveller_type = kind
        if not travellers:
            labels.append(f"{TYPE_LABEL.get(kind, kind).lower()} trip")
    elif count and count == trip.travellers and not travellers:
        issues.append(f"The trip already has {count} traveller{'s' if count != 1 else ''}.")

    # style, pace, food, stay
    styles = list(p.trip_styles or [])
    for s in intent.add_styles:
        if s not in styles:
            styles.append(s)
    styles = [s for s in styles if s not in intent.remove_styles] or ["balanced"]
    if "budget" in styles and "premium" in styles:
        styles.remove("premium" if "budget" in intent.add_styles else "budget")
    styles = styles[:4]
    trip_styles = styles if styles != list(p.trip_styles or []) else None
    if trip_styles:
        gained = [s for s in styles if s not in (p.trip_styles or [])]
        labels.append(", ".join(STYLE_WORDS.get(s, s) for s in gained) if gained else "a simpler style")
    elif intent.add_styles:
        issues.append("It already leans " + ("cheaper" if "budget" in intent.add_styles else "that way")
                      + ", so there's no more I can tighten there; adjust the budget or stay if you want to go further.")
    pace = None
    if intent.pace and intent.pace != p.pace:
        pace = intent.pace
        labels.append({"relaxed": "a relaxed pace", "packed": "more stops", "normal": "a normal pace"}[pace])
    food = None
    if intent.food and intent.food != p.food:
        food = intent.food
        labels.append(FOOD_WORDS[food])
    elif intent.food:
        issues.append(f"It's already planned for {FOOD_WORDS[intent.food]}.")
    stay = None
    if intent.stay and intent.stay != p.stay:
        stay = intent.stay
        labels.append(STAY_WORDS[stay])
    elif intent.stay:
        issues.append(f"It's already planned with {STAY_WORDS[intent.stay]}.")

    # transport
    cur_modes = list(p.transport_modes or [])
    modes = list(cur_modes)
    if intent.transport_only:
        keep_local = [m for m in cur_modes if m in ("cab", "metro") and m not in intent.transport_only]
        if "metro" in intent.transport_only:  # "by metro" displaces the cab, and the other way round
            keep_local = [m for m in keep_local if m != "cab"]
        if "cab" in intent.transport_only:
            keep_local = [m for m in keep_local if m != "metro"]
        modes = [*intent.transport_only, *keep_local]
    modes += [m for m in intent.transport_add if m not in modes]
    modes = [m for m in dict.fromkeys(modes) if m not in intent.transport_remove] or ["mixed"]
    if "mixed" in modes and len(modes) > 1:
        modes = [m for m in modes if m != "mixed"]
    if intent.train_names and "train" not in modes and "mixed" not in modes:
        modes.append("train")  # naming a train only makes sense if trains are allowed
    transport_modes = modes if sorted(modes) != sorted(cur_modes) else None
    if transport_modes:
        labels.append("travel by " + ", ".join(MODE_WORDS.get(m, m) for m in modes))
    elif intent.transport_only or intent.transport_add or intent.transport_remove:
        issues.append("Your travel choices already match that.")

    # a train asked for by name: a preference, kept across later changes (see _keep_train_preference)
    prefer = None
    if intent.train_names:
        both = intent.train_direction not in ("outbound", "return")
        cur_pref = dict((p.pinned_transport or {}).get("prefer") or {})
        new_pref = {**cur_pref, **{d: list(intent.train_names) for d in (("outbound", "return") if both else (intent.train_direction,))}}
        said = " / ".join(n if n.isdigit() else n.title() for n in intent.train_names)
        if new_pref != cur_pref:
            prefer = new_pref
            labels.append(f"use the {said}" + ("" if both else (" on the way there" if intent.train_direction == "outbound"
                                                              else " on the way back")))
        else:
            issues.append(f"The plan already prefers the {said}.")

    # interests: tags to favour ("shopping") and tags to see less of ("-temple")
    cur_interests = list(p.interests or [])
    interests = list(cur_interests)
    for tag in intent.interests:
        interests = [t for t in interests if t != f"-{tag}"]
        if tag not in interests:
            interests.append(tag)
    for tag in intent.avoid_tags:
        interests = [t for t in interests if t != tag]
        if f"-{tag}" not in interests:
            interests.append(f"-{tag}")
    interests = interests[-12:]
    new_interests = interests if interests != cur_interests else None
    if new_interests:
        def said(tags: list[str], marker: str) -> str:
            seen = {intent.interest_labels.get(t) or t.replace("_", " ") for t in tags}
            return ", ".join(sorted(seen))
        if [t for t in intent.interests if t not in cur_interests]:
            labels.append("more " + said(intent.interests, ""))
        if [t for t in intent.avoid_tags if f"-{t}" not in cur_interests]:
            labels.append("fewer " + said(intent.avoid_tags, ""))

    # places — from the plan's own list, then ones looked up by name
    include: list[str] = []
    exclude: list[str] = []
    pinned = set(p.pinned_place_ids or [])

    def add_place(pid: str, name: str) -> None:
        if pid in planned:
            issues.append(f"{name} is already in your plan.")
        elif pid in pinned:
            issues.append(f"{name} is already on your must-see list but didn't fit your time; a later return or an "
                          "extra day would make room for it.")
        elif pid not in include:
            include.append(pid)
            labels.append(f"added {name}")
    for pid in intent.include_place_ids:
        add_place(pid, names.get(pid, "That place"))
    for pid in intent.exclude_place_ids:
        name = names.get(pid, "That place")
        if pid in planned or pid in pinned:
            exclude.append(pid)
            labels.append(f"skipped {name}")
        else:
            issues.append(f"{name} isn't in your plan.")
    for name in intent.unknown_skips:
        issues.append(f"I couldn't find “{name}” in your plan.")
    if intent.add_place_names:
        try:
            dest = providers.places.resolve(trip.destination_name, trip.destination_place_id)
        except (AppError, ProviderUnavailable):
            dest = None
        for wanted in intent.add_place_names:
            place = None
            if dest is not None:
                try:
                    place = providers.places.find_attraction(wanted, dest)
                except (AppError, ProviderUnavailable):
                    place = None
            if place is not None and _similar(wanted, place.name):
                add_place(place.provider_place_id, place.name)
            else:
                issues.append(f"I couldn't find “{wanted}” near {trip.destination_name}.")

    # what was understood but can't be done, or wasn't understood at all
    issues.extend(f"Tell me {n}." for n in intent.needs)
    issues.extend(intent.unsupported)
    issues.extend(f"I couldn't act on “{x}”." for x in intent.ignored)

    patch = PreferencesPatch(budget=budget, travellers=travellers, traveller_type=traveller_type,
                             start_time=start_time, end_time=end_time, end_date=end_date, pace=pace, food=food,
                             stay=stay, trip_styles=trip_styles, transport_modes=transport_modes,
                             include_place_ids=include or None, exclude_place_ids=exclude or None)
    return IntentOutcome(patch, new_interests, labels, _dedupe_keep(issues), intent.reply, intent.source,
                         vague=intent.is_empty(), asked=" ".join(message.split()), prefer_trains=prefer)


def _dedupe_keep(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def apply_intent(trip: Trip, outcome: IntentOutcome) -> list[str]:
    """Apply an IntentOutcome: the structured patch, plus the interest tags (which a patch can't carry)."""
    notes = apply_patch(trip, outcome.patch)
    if outcome.prefer_trains is not None:
        trip.preferences.pinned_transport = {**(trip.preferences.pinned_transport or {}), "prefer": outcome.prefer_trains}
        notes.append("preferred train")
    if outcome.interests is not None:
        trip.preferences.interests = outcome.interests
        notes.append("interests")
    return notes


# ------------------------------------------------------------------------------------------------------
# Running the planner (quota-aware, transactional)
# ------------------------------------------------------------------------------------------------------

def run_plan(db: Session, providers: Providers, user: User, trip: Trip, *, reporter: StageReporter | None = None,
             change_note: str | None = None, is_replan: bool = False) -> PlanResult:
    """Quota must already be reserved (see quota_service.reserve). Commits on success; refunds on failure."""
    trip.status = "planning"
    db.commit()
    s = get_settings()
    seconds = s.plan_deadline_seconds if s.gateway_enabled else 0
    with telemetry.plan_trace(f"{trip.origin_name} -> {trip.destination_name}{' (replan)' if is_replan else ''}"), \
            gateway_context.plan_scope(seconds, user.id) as scope:
        result = _run_plan(db, providers, user, trip, reporter, change_note, is_replan)
        if result.status == "ready" and scope.degraded:
            _note_degraded(db, trip, scope)
        if result.status == "ready" and not is_replan:  # the first plan of a trip: email it to its owner (background; never affects the plan)
            from app.services import plan_email
            plan_email.after_plan(user.id, trip.id)
            from app.services import referral_service  # a friend's first plan is what earns their inviter's reward
            referral_service.on_first_plan(db, user)
            db.commit()
        return result


_PART = {"google-routes": "travel times between stops", "google-places": "some place details", "google-geocoding": "some place lookups",
         "google": "travel times and place details", "search": "opening-hours checks", "open-meteo": "the weather",
         "llm": "the written summary (plain template text instead)"}


def _note_degraded(db: Session, trip: Trip, scope) -> None:
    """Parts of a finished plan that were estimated or left out because a call was skipped or failed (the plan deadline, a
    stopped budget, a provider down) are listed in its Plan notes. Rail is not listed: the source chain labels its own."""
    parts = []
    for d in scope.degraded:
        part = _PART.get(d.get("provider") or "") or _PART.get(d["group"])
        if d["group"] in ("railradar", "irctc1") or part is None or part in parts:
            continue
        parts.append(part)
    if scope.deadline_stage:
        log.warning("plan for trip %s met its deadline during stage %r; degraded: %s", trip.id, scope.deadline_stage, parts or "none shown")
    it = db.scalar(select(Itinerary).where(Itinerary.trip_id == trip.id, Itinerary.is_current.is_(True))
                   .order_by(Itinerary.version.desc()))  # the plan just saved (the trip's loaded list may predate it)
    if not parts or it is None:
        return
    why = "took too long" if scope.deadline_stage else "weren't available"
    note = f"Some live data {why}, so these parts are estimated or left out: {', '.join(parts)}. Everything else was checked as usual."
    it.warnings = [*(it.warnings or []), note][:5]
    db.commit()


def _run_plan(db: Session, providers: Providers, user: User, trip: Trip, reporter: StageReporter | None,
              change_note: str | None, is_replan: bool) -> PlanResult:
    try:
        result = Planner(db, providers, reporter, user.id).plan(trip, change_note)
        if is_replan and result.status == "ready":
            trip.replan_count = (trip.replan_count or 0) + 1
        if result.status == "infeasible":
            quota_service.refund(db, user, trip)
        db.commit()
        return result
    except Exception:
        db.rollback()
        trip = db.get(Trip, trip.id)
        user = db.get(User, user.id)
        if trip is not None:
            quota_service.refund(db, user, trip)
            trip.status = "ready" if trip.plan_count else "draft"
            db.commit()
        raise


def check_replan_allowed(trip: Trip) -> None:
    if not trip.plan_count:
        raise AppError("Plan this trip first.", code="not_planned", status_code=409)
    if (trip.replan_count or 0) >= get_settings().max_replans_per_trip:
        raise AppError("This trip has reached its replan limit. Create a new trip to keep exploring.",
                       code="replan_limit", status_code=429)


# ------------------------------------------------------------------------------------------------------
# Serialisation
# ------------------------------------------------------------------------------------------------------

def _num(v) -> float | None:
    return None if v is None else float(v)


def alt_out(a: Alternative, dest: str | None = None) -> dict:
    out = {"id": str(a.id), "kind": a.kind, "title": a.title, "rank": a.rank, "score": a.score, "cost": _num(a.cost),
           "cost_delta": _num(a.cost_delta), "time_delta_minutes": a.time_delta_minutes,
           "distance_from_prev_m": a.distance_from_prev_m, "reason": a.reason, "checks": a.checks or {},
           "meta": a.meta or {}}
    return google_content.gate_alternative(gate.redact_alternative(out), a, dest)


def item_out(i: ItineraryItem, dest: str | None = None) -> dict:
    """The final gates, in order: I1 train facts, I1 fares, then I6 (Google content older than 30 days)."""
    out = gate.fare_item(gate.label_item(gate.redact_item(_item_out(i, dest))))
    return google_content.gate_item(out, i, dest)


def _item_out(i: ItineraryItem, dest: str | None = None) -> dict:
    return {"id": str(i.id), "position": i.position, "day_index": i.day_index, "kind": i.kind,
            "start_at": i.start_at.isoformat(timespec="minutes"), "end_at": i.end_at.isoformat(timespec="minutes"),
            "time": i.start_at.strftime("%H:%M"), "end_time": i.end_at.strftime("%H:%M"),
            "duration_minutes": int((i.end_at - i.start_at).total_seconds() // 60),
            "title": i.title, "subtitle": i.subtitle, "place_id": str(i.place_id) if i.place_id else None,
            "distance_from_prev_m": i.distance_from_prev_m, "travel_minutes_from_prev": i.travel_minutes_from_prev,
            "travel_mode": i.travel_mode, "travel_cost": _num(i.travel_cost), "leg_state": i.leg_state,
            "polyline": i.polyline, "cost": _num(i.cost), "cost_state": i.cost_state,
            "recommended_minutes": i.recommended_minutes, "opening_time": i.opening_time,
            "closing_time": i.closing_time, "hours_state": i.hours_state, "hours_note": i.hours_note,
            "sources": i.sources or [], "reasons": i.reasons or [], "explanation": i.explanation, "meta": i.meta or {},
            "alternatives": [alt_out(a, dest) for a in sorted(i.alternatives, key=lambda a: a.rank)]}


def itinerary_out(it: Itinerary, trip: Trip) -> dict:
    items = [item_out(i, trip.destination_name) for i in sorted(it.items, key=lambda i: i.position)]
    stale_text = google_content.narration_expired(it.items, it.created_at)
    days = []
    for n, idx in enumerate(sorted({i["day_index"] for i in items})):
        day_items = [i for i in items if i["day_index"] == idx]
        d = trip.start_date + timedelta(days=idx)
        days.append({"index": idx, "date": d.isoformat(), "label": f"Day {n + 1}", "items": day_items})
    return {"id": str(it.id), "version": it.version, "title": it.title,
            "summary": None if stale_text else it.summary, "tips": [] if stale_text else (it.tips or []),
            "summary_expired": stale_text,
            "total_cost": _num(it.total_cost), "remaining": _num(it.remaining), "budget": it.budget_breakdown or {},
            "warnings": it.warnings or [], "pipeline_trace": it.pipeline_trace or [], "weather": it.weather,
            "llm_status": it.llm_status, "change_note": it.change_note, "created_at": it.created_at.isoformat(),
            "data_mode": (it.candidate_pool or {}).get("data_mode"), "local_mode": (it.candidate_pool or {}).get("local_mode"),
            "brownout": (it.candidate_pool or {}).get("brownout"),  # {level, labels{part: text}} when made under load (ADR 0008)
            "days": days}


def member_out(m: TripMember) -> dict:
    return {"id": str(m.id), "name": m.name, "email": m.email, "budget": _num(m.budget), "food_pref": m.food_pref,
            "is_owner": m.is_owner}


def expense_out(e) -> dict:
    return {"id": str(e.id), "description": e.description, "category": e.category, "amount": _num(e.amount),
            "paid_by_member_id": str(e.paid_by_member_id), "split_type": e.split_type,
            "created_at": e.created_at.isoformat(),
            "splits": [{"member_id": str(s.member_id), "amount": _num(s.amount)} for s in e.splits]}


def trip_card(t: Trip) -> dict:
    it = t.current_itinerary
    p = t.preferences
    return {"id": str(t.id), "title": t.title, "status": t.status, "origin": t.origin_name,
            "destination": t.destination_name, "start_date": t.start_date.isoformat(), "end_date": t.end_date.isoformat(),
            "travellers": t.travellers, "traveller_type": p.traveller_type if p else None, "budget": _num(t.budget),
            "estimated_cost": _num(it.total_cost) if it else None, "remaining": _num(it.remaining) if it else None,
            "feasible": (t.feasibility or {}).get("feasible"), "data_mode": t.data_mode, "currency": t.currency,
            "created_at": t.created_at.isoformat()}


def trip_out(t: Trip, *, read_only: bool = False) -> dict:
    p = t.preferences
    it = t.current_itinerary
    return {
        **trip_card(t),
        "start_time": t.start_time.strftime("%H:%M"), "end_time": t.end_time.strftime("%H:%M"),
        "origin_detail": {"name": t.origin_name, "place_id": t.origin_place_id, "lat": t.origin_lat, "lng": t.origin_lng},
        "destination_detail": {"name": t.destination_name, "place_id": t.destination_place_id,
                               "lat": t.destination_lat, "lng": t.destination_lng},
        "currency": t.currency, "timezone": t.timezone,
        "preferences": {"traveller_type": p.traveller_type, "age_groups": p.age_groups or [],
                        "transport_modes": p.transport_modes, "food": p.food,
                        "trip_styles": p.trip_styles, "stay": p.stay, "pace": p.pace, "notes": p.notes,
                        "interests": p.interests, "excluded_place_ids": p.excluded_place_ids,
                        "pinned_place_ids": p.pinned_place_ids, "pinned_transport": p.pinned_transport},
        "feasibility": t.feasibility, "plan_count": t.plan_count, "replan_count": t.replan_count,
        "replans_left": max(0, get_settings().max_replans_per_trip - (t.replan_count or 0)),
        "quota_source": t.quota_source, "read_only": read_only,
        "members": [member_out(m) for m in t.members],
        "expenses": [expense_out(e) for e in t.expenses],
        "expense_summary": expense_service.summary(t),
        "itinerary": itinerary_out(it, t) if it else None,
        "versions": [{"version": i.version, "created_at": i.created_at.isoformat(), "change_note": i.change_note,
                      "total_cost": _num(i.total_cost)} for i in t.itineraries],
    }