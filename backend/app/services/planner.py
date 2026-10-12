"""The planning pipeline.

User input -> real-world data -> verification -> feasibility -> budget engine -> route optimisation
-> candidate ranking -> alternatives -> LLM narration -> persisted itinerary.

Only the final narration step touches the LLM, and its output is fact-guarded.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.provenance import part_of_day, train_shows_facts
from app.core import brownout
from app.core.config import get_settings
from app.core.errors import AppError, ProviderUnavailable
from app.models.catalog import WebSource
from app.models.trip import Alternative, Itinerary, ItineraryItem, TransportOption, Trip
from app.providers.registry import Providers
from app.providers.types import PlaceData, TransportData
from app.services import age_fit
from app.services import alternatives as ALT
from app.services import fare_model as F
from app.services import feasibility as FE
from app.services import llm_service
from app.services import plan_quality as Q
from app.services import planning_support as PS
from app.services.budget_engine import BudgetBreakdown, BudgetLine, buffer_pct, compute
from app.services.engine_types import Candidate, DayFrame, PlanInput, StageReporter, Stop
from app.services.route_optimizer import DayScheduler, Travel, _meal_stops, apply_real_routes, simulate
from app.services.verification import verify_candidates
from app.utils.geo import haversine_m
from app.utils.locale import locale_for
from app.utils.money import fmt_money, round_rupees, to_currency

log = logging.getLogger("tripai.planner")

MEAL_LABEL = {"breakfast": "Breakfast", "lunch": "Lunch", "snack": "Evening snack", "dinner": "Dinner"}
TRY_COMBOS = 4
PINNED_VISIT_VALUE = 4.0  # score for each must-see place (picked by the traveller) that makes it into the plan


@dataclass
class Attempt:
    combo: FE.Combo
    frames: list[DayFrame]
    sched: DayScheduler
    travel: Travel
    value: float
    breakdown: BudgetBreakdown
    hotel: Candidate | None


@dataclass
class PlanResult:
    status: str  # ready | infeasible
    feasibility: dict
    itinerary_id: str | None = None
    warnings: list[str] = field(default_factory=list)


def _join(parts: list[str]) -> str:
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _clock(d: dict, key: str) -> str:
    """A draft's time for the AI narration payload: a part of day for a train without a timetable (I1), so the AI is
    never handed a train time it could repeat."""
    t = d.get("transport")
    if t is not None and not train_shows_facts(t):
        return part_of_day(d[key])
    return f"{d[key]:%H:%M}"


def _hours(minutes: int) -> str:
    """90 -> "1 h 30 min", 180 -> "3 h"."""
    h, m = divmod(int(minutes), 60)
    return " ".join(p for p in (f"{h} h" if h else "", f"{m} min" if m else "") if p) or "0 min"


def _smaller(cands: list, keep_min: int) -> list:
    """Brownout L3: half of the unpinned candidates (at least `keep_min`), best first as they already are; pinned ones stay."""
    rest = [c for c in cands if not c.pinned]
    keep = {id(c) for c in rest[:max(keep_min, (len(rest) + 1) // 2)]}
    return [c for c in cands if c.pinned or id(c) in keep]


class Planner:
    def __init__(self, db: Session, providers: Providers, reporter: StageReporter | None = None, user_id=None):
        self.db = db
        self.p = providers
        self.r = reporter or StageReporter()
        self.user_id = user_id
        self.settings = get_settings()
        self.warnings: list[str] = []
        self.no_timetable: list[str] = []
        self.priced_modes: list[str] = []

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def _label_brownout(self, itinerary: Itinerary) -> None:
        """A plan made under a brownout level says so, next to each part it touches (itinerary.brownout, shown by the app beside
        the description, the alternatives and the rail section) and in Plan notes. The levels change effort, never a fact."""
        snap = brownout.snapshot()
        if not snap:
            return
        itinerary.candidate_pool = {**(itinerary.candidate_pool or {}), "brownout": snap}
        itinerary.warnings = [*(itinerary.warnings or []), *snap["labels"].values()]
        if "rail" in snap["labels"]:
            for item in itinerary.items:
                if item.kind == "transport" and (item.meta or {}).get("mode") == "train":
                    item.meta = {**(item.meta or {}), "brownout": snap["labels"]["rail"]}

    # ------------------------------------------------------------------------------------------------
    def _sync_locale(self, trip: Trip) -> None:
        """The destination's own timezone and currency, before anything is timed or costed.

        The wizard almost never has coordinates for the destination (a Google suggestion carries only a place
        id), so trip creation can't know them: they're worked out here from the resolved destination. On a
        trip's first plan, if that changes the currency, the budget the traveller typed (the wizard shows
        rupees) is converted at an approximate rate rather than being read as, say, 6,000 pounds.
        """
        lat, lng = trip.destination_lat, trip.destination_lng
        if lat is None or lng is None:
            try:
                dest = self.p.places.resolve(trip.destination_name, trip.destination_place_id)
            except (AppError, ProviderUnavailable):
                return  # _resolve() reports a real problem properly, in the routes stage
            if dest is None:
                return
            lat, lng = dest.lat, dest.lng
        timezone, currency = locale_for(lat, lng)
        first_plan = trip.plan_count == 0 and trip.current_itinerary is None
        if currency != trip.currency and first_plan:
            was = trip.currency
            trip.budget = round_rupees(to_currency(trip.budget, currency)) if was == "INR" else trip.budget
            for m in trip.members:
                if m.budget is not None and was == "INR":
                    m.budget = round_rupees(to_currency(m.budget, currency))
            self.warn(f"Prices for this trip are in {currency}. Your budget was converted from rupees at an "
                      f"approximate rate ({fmt_money(trip.budget, currency)}); this is an estimate, not a live rate.")
        trip.timezone, trip.currency = timezone, currency

    def plan(self, trip: Trip, change_note: str | None = None) -> PlanResult:
        r = self.r
        # 1. Understanding preferences ------------------------------------------------------------------
        # Started before _sync_locale, which may call a provider: the traveller sees progress at once (ADR 0002).
        r.start("understand")
        self._sync_locale(trip)
        inp = PS.plan_input(trip)
        modes, widened = FE.intercity_modes(inp)
        if widened:
            self.warn("Metro only works inside a city, so we also looked at trains and buses for the intercity part.")
        who = {"solo": "Solo", "couple": "Couple", "partner": "Partner trip", "friends": "Friends",
               "family": "Family"}.get(inp.traveller_type, inp.traveller_type)
        food = {"veg": "Vegetarian", "non_veg": "Non-vegetarian", "both": "Veg + non-veg"}[inp.effective_food]
        ages = age_fit.describe(inp.age_groups)  # "with children and older travellers" (empty for adults only)
        r.done("understand", f"{who}, {inp.travellers} traveller{'s' if inp.travellers > 1 else ''}"
                             f"{' ' + ages if ages else ''}, {fmt_money(inp.budget, inp.currency)}, {food}")

        # 2. Finding routes ---------------------------------------------------------------------------------
        r.start("routes")
        origin, dest = self._resolve(trip)
        local = FE.is_local_trip(origin, dest)
        options: list[TransportData] = []
        if local:
            combos = [FE.local_combo(inp, origin)]
            r.done("routes", f"{dest.name} is close to {origin.name}: local travel only")
        else:
            km = haversine_m(origin.lat, origin.lng, dest.lat, dest.lng) / 1000
            # A mode that makes no sense at this distance (a cab for 170 km) is simply not offered; the traveller
            # didn't ask for it to be explained, and the plan shows the transport that was chosen.
            modes, _ = FE.distance_capped_modes(modes, km)
            try:
                options = self.p.transport.intercity_options(origin, dest, inp.start.date(), PS.return_day(inp),
                                                             inp.start, inp.end, modes, inp.timezone, inp.currency)
            except ProviderUnavailable:
                self.warn("Route information is temporarily unavailable. Travel times are estimates.")
                options = PS.estimated_transport(origin, dest, inp, modes, PS.return_day(inp))
            PS.choose_station_access(options, inp, origin)
            # Only a mode that could realistically run here is "missing": no train runs Delhi -> Istanbul, so not
            # finding one isn't news (and must not be blamed for the plan).
            missing = [m for m in ("train", "bus", "flight") if m in modes and FE.mode_realistic(m, km)
                       and not any(t.mode == m for t in options)]
            self.priced_modes = sorted({t.mode for t in options})
            rail_issue = getattr(self.p.transport, "last_rail_issue", None)
            if missing:
                self.no_timetable = missing
                why = f" ({rail_issue})" if rail_issue and "train" in missing else ""
                self.warn(f"No {' or '.join(missing)} timetable was available for {origin.name} to {dest.name}"
                          f"{why}. TripAI never invents services.")
            combos = FE.rank_combos(FE.build_combos(options, inp, origin, dest), inp)
            if not combos and missing:
                # Nothing the traveller chose is available: say so, and offer to widen the choice
                r.done("routes", f"No {' or '.join(missing)} found for these dates", status="failed")
                # Only offer ways of travelling that make sense for this distance: a cab for 800 km, or a flight
                # for 50 km, would just be refused again with nothing left to suggest.
                offer = [m for m in ("train", "bus", "cab", "flight") if m not in inp.transport_modes
                         and (m not in FE.DISTANCE_CAPS or km <= FE.DISTANCE_CAPS[m]) and (m != "flight" or km > 350)]
                adjustments = [{"key": f"allow_{m}", "label": f"Also allow {m}",
                                "description": f"Plan with {m} as well, where it fits your time and budget.",
                                "apply": {"transport_modes": sorted(set(inp.transport_modes) | {m})}}
                               for m in offer]
                adjustments.append({"key": "nearby_destination", "label": "Pick a closer destination",
                                    "description": f"Somewhere nearer to {origin.name} has more connections.",
                                    "apply": {"action": "change_destination"}})
                return self._infeasible(trip, {
                    "feasible": False, "kind": "no_service",
                    "message": f"No {' or '.join(missing)} was found from {origin.name} to {dest.name} "
                               f"on these dates.",
                    "main_conflict": (f"Reason: {rail_issue}." if rail_issue else
                                      "The timetable had nothing that fits your dates and times."),
                    "adjustments": adjustments,
                })
            if not combos:
                r.done("routes", "No connection fits your time window", status="failed")
                return self._infeasible(trip, FE.time_infeasible(inp, options, modes, dest))
            best = combos[0]
            counts = {m: sum(1 for t in options if t.mode == m) for m in ("train", "bus", "flight", "cab", "own_car")}
            found = ", ".join(f"{n} {m.replace('own_car', 'car')}" for m, n in counts.items() if n)
            # no "best fit" here: the final choice is made later, against hotels and sights ("train both ways" was
            # shown above a plan that went out by bus)
            r.done("routes", f"{len(options)} options compared ({found})")

        # 3. Checking places ------------------------------------------------------------------------------
        r.start("places")
        day_trip = inp.end.date() == inp.start.date() or (PS.return_day(inp) == inp.start.date())
        act_allow = inp.budget * Decimal("0.15" if day_trip else "0.10")
        meal_pp = inp.budget * Decimal("0.25") / Decimal(3 * max(1, inp.travellers))
        # The age hint goes first: with children or older travellers the pool should hold places that suit them (still at most
        # three extra searches, so a trip's worst-case cost is unchanged; it displaces the third style/favourite hint, if any)
        found_places = self.p.places.attractions(dest, [*age_fit.search_hints(inp.age_groups), *inp.styles, *inp.interests])
        found_restaurants = self.p.places.restaurants(dest, inp.effective_food, "premium" in inp.styles)
        found_places = self._with_named_places(found_places, found_restaurants, inp, dest)
        attractions = PS.attraction_candidates(found_places, inp, act_allow, city=dest.name.split(",")[0])
        restaurants = PS.restaurant_candidates(found_restaurants, inp, meal_pp)
        if not attractions:
            raise AppError(f"We couldn't find sights in {dest.name} right now. Please try again shortly.",
                           code="no_places", status_code=503)
        if brownout.active(3):  # busy: compare fewer options (labelled on the plan, ADR 0008)
            attractions, restaurants = _smaller(attractions, 8), _smaller(restaurants, 6)
        hotels: list[Candidate] = []
        may_stay = inp.stay != "no_stay" and PS.return_day(inp) > inp.start.date()
        if may_stay:
            centre = PS.centroid(attractions[:6], (dest.lat, dest.lng))
            try:
                hotels = PS.hotel_candidates(self.p.hotels.hotels(dest, inp.stay), inp, centre)
            except ProviderUnavailable:
                self.warn("Hotel information is temporarily unavailable; the stay cost is an estimate.")
            if not hotels:
                hotels = [PS.estimated_hotel(dest, inp.stay, inp.currency)]
        PS.upsert_places(self.db, attractions + restaurants + hotels)
        # Commit now: holding this write open while the rest of the plan waits on providers blocked every other
        # session's write (response cache, saved timetables) for SQLite's 30 s busy timeout, 90% of plan time (ADR 0001).
        self.db.commit()
        r.done("places", f"{len(attractions)} sights, {len(restaurants)} restaurants"
                         + (f", {len(hotels)} hotels" if hotels else ""))

        # 4. Checking opening hours (web verification) ------------------------------------------------------
        r.start("hours")
        plan_day = combos[0].dest_start.date()
        top = [c for c in attractions if c.pinned] + [c for c in attractions if not c.pinned]
        to_verify = top[:max(1, self.settings.web_verify_max_places)]
        results, search_ok = verify_candidates(self.p.search, to_verify, plan_day, PS.provider_state(dest))
        if not search_ok and self.p.search is not None:
            self.warn("Some current information could not be verified.")  # a configured check failed
        self._store_sources(trip, attractions, results)
        attractions.sort(key=lambda c: -c.score)
        official = sum(1 for v in results if v.state == "verified_official")
        uncertain = sum(1 for v in results if v.state == "uncertain")
        r.done("hours", f"{len(results)} places checked on the web: {official} confirmed by official sources, "
                        f"{uncertain} need confirmation" if results else "Using provider opening hours")

        # 5. Comparing prices --------------------------------------------------------------------------------
        r.start("prices")
        hotel = hotels[0] if hotels else None
        rooms = F.stay_units(inp.stay, inp.travellers)
        cheapest = min(combos, key=lambda c: c.cost)
        mc = self._min_cost(inp, cheapest, hotel, rooms, restaurants, dest)
        if mc.total > inp.budget:
            r.done("prices", f"Minimum realistic cost {fmt_money(mc.total, inp.currency)} exceeds {fmt_money(inp.budget, inp.currency)}", status="failed")
            any_mode = self._cheapest_any_mode(inp, origin, dest, options, modes) if not local else None
            cheapest_stay = min((to_currency(F.stay_nightly(t), inp.currency) * F.stay_units(t, inp.travellers) for t in FE.STAY_ORDER),
                                default=Decimal(0)) * mc.nights if hotel else Decimal(0)
            acts = sum((c.group_cost(inp.travellers) for c in attractions[:4]), Decimal(0))
            return self._infeasible(trip, FE.budget_infeasible(inp, mc, any_mode, cheapest_stay, acts))
        r.done("prices", f"Transport from {fmt_money(cheapest.cost, inp.currency)}" +
                         (f", stay {fmt_money(hotel.unit_cost, inp.currency)}/night at {hotel.place.name}" if hotel and mc.nights else ""))

        # 6. Optimising the route -------------------------------------------------------------------------
        r.start("optimize")
        tried = list(dict.fromkeys(combos[:TRY_COMBOS] + [cheapest]))
        if any(c.pinned for c in attractions) and not local:
            # Must-sees need daylight at the destination, and the best-ranked journeys are often the cheap ones
            # that leave the least of it (a 15:41 bus home). Also try the cheapest of the roomiest options.
            roomiest = max(FE.explore_minutes(c.dest_start, c.dest_end) for c in combos)
            roomy = [c for c in combos if FE.explore_minutes(c.dest_start, c.dest_end) >= 0.85 * roomiest]
            tried = list(dict.fromkeys(tried + sorted(roomy, key=lambda c: c.cost)[:2]))
        attempts = [a for a in (self._attempt(inp, c, hotel, rooms, attractions, restaurants, dest) for c in tried) if a]
        if not attempts:
            r.done("optimize", "Nothing fits the budget", status="failed")
            any_mode = self._cheapest_any_mode(inp, origin, dest, options, modes) if not local else None
            return self._infeasible(trip, FE.budget_infeasible(inp, mc, any_mode, Decimal(0), Decimal(0)))
        within = [a for a in attempts if a.breakdown.status != "over"] or attempts
        best = max(within, key=lambda a: a.value)
        verified_keys = {v.key for v in results}
        extra = [s.cand for f in best.frames for s in f.stops
                 if s.kind in ("activity", "sunset") and s.cand.key not in verified_keys][:6]
        if extra:
            more, _ = verify_candidates(self.p.search, extra, plan_day, PS.provider_state(dest))
            self._store_sources(trip, extra, more, clear=False)
            results = results + more
        route_state, failures, dropped = self._real_routes(best)
        if dropped and not failures:
            # The real routes were slower than the estimates the day was built on, so stops had to go. Build the day
            # again knowing the real times (measured legs kept, the rest stretched to match) rather than ending up
            # with whatever was left over: the stops that matter most are chosen again, with room for the travel.
            scale = best.travel.calibration()
            if scale > 1.05:
                retry = self._attempt(inp, best.combo, hotel, rooms, attractions, restaurants, dest, scale=scale,
                                      known=best.travel.real)
                if retry is not None:
                    r_state, r_failures, r_dropped = self._real_routes(retry)
                    if len(r_dropped) < len(dropped) and retry.breakdown.status != "over":
                        best, route_state, failures, dropped = retry, r_state, r_failures, r_dropped
        self.warnings.extend(w for w in best.sched.warnings if w not in self.warnings)
        planned_keys = {s.cand.key for f in best.frames for s in f.stops if s.cand}
        left_out = [c.place.name for c in attractions if c.pinned and c.key not in planned_keys]
        if left_out:
            names = _join(left_out)
            self.warn(f"{names} {'was' if len(left_out) == 1 else 'were'} on your must-see list but couldn't fit in "
                      f"your time. Allow a later return or add a day to include {'it' if len(left_out) == 1 else 'them'}.")
            missed = [n for n in left_out if change_note and f"added {n}" in change_note]
            if missed:  # the change note must not say "added X" when the plan then couldn't fit it
                change_note = (f"{change_note} But {_join(missed)} didn't fit your time; a later return or an extra day "
                               f"would make room for {'it' if len(missed) == 1 else 'them'}.")
        if not local:  # a train the traveller named that the plan couldn't use: say which, and why
            unmet = FE.unmet_train_preferences(best.combo.outbound, best.combo.ret, options, inp, origin.name, dest.name)
            for n in unmet:
                self.warn(n)
            if unmet:
                change_note = " ".join([change_note or "", *unmet]).strip()
        quality = Q.review(best.frames, (dest.lat, dest.lng),
                           None if local else best.combo.start_point,
                           best.hotel.point if best.hotel else None, local, dest.name)
        if not quality.ok and quality.only_empty_days:
            # Empty days usually mean the money ran out after tickets, stay and meals, not a lack of places.
            left = max(Decimal(0), inp.budget - best.breakdown.subtotal)
            per_day = Decimal(250 * inp.travellers + 600)  # entry tickets + local rides for one day
            if left < per_day * quality.empty_days:
                r.done("optimize", "Too little left for sightseeing", status="failed")
                need = inp.budget + per_day * quality.empty_days - left
                need = (need / 500).to_integral_value(rounding="ROUND_CEILING") * 500
                days = "day" if quality.empty_days == 1 else "days"
                adjustments = [{"key": "higher_budget", "label": f"Raise budget to {fmt_money(need, inp.currency)}",
                                "description": "Adds enough for entry tickets and local travel.",
                                "apply": {"budget": int(need)}}]
                if best.frames[-1].day > best.frames[0].day:
                    adjustments.append({"key": "shorter_trip", "label": "Make it a day trip",
                                        "description": "Come back the same evening and skip the hotel.",
                                        "apply": {"end_date": inp.start.date().isoformat(), "stay": "no_stay"}})
                if best.hotel and inp.stay not in ("hostel", "no_stay"):
                    adjustments.append({"key": "cheaper_hotel", "label": "Stay in a hostel",
                                        "description": "Frees money for sightseeing.",
                                        "apply": {"stay": "hostel"}})
                return self._infeasible(trip, {
                    "feasible": False, "kind": "budget",
                    "message": f"The requested trip is not feasible within {fmt_money(inp.budget, inp.currency)}.",
                    "main_conflict": (f"After tickets, the stay and meals, only about {fmt_money(left, inp.currency)} is left for "
                                      f"{quality.empty_days} full {days} of sightseeing, which isn't enough for "
                                      f"entry tickets and local travel."),
                    "adjustments": adjustments,
                })
        if quality.all_travel:
            r.done("optimize", "No time left to explore", status="failed")
            info = FE.time_infeasible(inp, options if not local else [], modes, dest)
            info["main_conflict"] = (f"{quality.blocking[0]} {best.combo.label()} gets you to {dest.name} at "
                                     f"{best.combo.dest_start:%H:%M} on {best.combo.dest_start:%a} and you'd have to "
                                     f"leave again by {best.combo.dest_end:%H:%M} on {best.combo.dest_end:%a}.")
            return self._infeasible(trip, info)
        if not quality.ok:
            r.done("optimize", "The plan didn't pass our checks", status="failed")
            return self._infeasible(trip, {
                "feasible": False, "kind": "quality",
                "message": "We couldn't build a plan we'd trust for this trip.",
                "main_conflict": " ".join(dict.fromkeys(quality.blocking)),
                "adjustments": [{"key": "nearby_destination", "label": "Choose a specific city",
                                 "description": "Plan around one city or town, then add nearby places to it.",
                                 "apply": {"action": "change_destination"}}],
            })
        for n in quality.notes:
            self.warn(n)
        chosen = [t for t in (best.combo.outbound, best.combo.ret) if t is not None]
        if any(t.meta.get("estimated_rail") for t in chosen):
            issue = getattr(self.p.transport, "last_rail_issue", None)
            self.warn("No live train timetable was available" + (f" ({issue})" if issue else "") + ", so the train "
                      "leg is estimated from the rail network between the stations shown. Check the exact train "
                      "and its times before booking.")
        saved = next((t.meta["saved_timetable"] for t in chosen if t.meta.get("saved_timetable")), None)
        if saved:
            self.warn(f"The live train timetable didn't answer, so trains come from the copy saved on {saved}. "
                      "Timetables rarely change, but confirm the train before booking.")
        stops_n = sum(len(f.stops) for f in best.frames)
        km = sum((s.leg.distance_m for f in best.frames for s in f.stops if s.leg), 0) / 1000
        official = sum(1 for v in results if v.state == "verified_official")
        r.done("optimize", f"{stops_n} stops ordered, {km:.1f} km of local travel"
                           + (f", {len(extra)} more places verified" if extra else "")
                           + (" (routes estimated)" if route_state == "estimated" else ""))

        # 7. Checking budget --------------------------------------------------------------------------------
        r.start("budget")
        breakdown = self._breakdown(inp, best.combo, best.frames, best.hotel, rooms)
        best.breakdown = breakdown
        for w in FE.member_warnings(inp, breakdown.planned):
            self.warn(w)
        if breakdown.status == "over":
            pinned = bool(inp.pinned or inp.pinned_transport)
            if not pinned:
                # Meals and rides alone exceed the budget: say so plainly instead of showing an over-budget plan.
                r.done("budget", f"Needs {fmt_money(breakdown.planned, inp.currency)}, over {fmt_money(inp.budget, inp.currency)}", status="failed")
                over = -breakdown.remaining
                tot = breakdown.category_totals()
                mc = FE.MinCost(best.combo.cost, tot["stay"], tot["food"], tot["local"], best.combo.nights, rooms,
                                best.combo.outbound.mode if best.combo.outbound else None)
                any_mode = self._cheapest_any_mode(inp, origin, dest, options, modes) if not local else None
                cheapest_stay = min((to_currency(F.stay_nightly(t), inp.currency) * F.stay_units(t, inp.travellers) for t in FE.STAY_ORDER),
                                    default=Decimal(0)) * mc.nights if best.hotel else Decimal(0)
                acts = sum((c.group_cost(inp.travellers) for c in attractions[:4]), Decimal(0))
                info = FE.budget_infeasible(inp, mc, any_mode, cheapest_stay, acts)
                # The minimum above leaves out paid sights, so its "raise the budget" figure can land at or below what the
                # traveller already has (a plan a few rupees over). Always offer an amount that covers the plan we built.
                want = FE.ceil_to(breakdown.planned * (1 + buffer_pct(inp.styles)))
                raise_to = next((a for a in info["adjustments"] if a["key"] == "higher_budget"), None)
                if raise_to is None:
                    raise_to = {"key": "higher_budget", "description": "Keeps every preference as it is, with a small buffer."}
                    info["adjustments"].append(raise_to)
                if int(raise_to.get("apply", {}).get("budget", 0)) < want:
                    raise_to["label"] = f"Raise budget to {fmt_money(want, inp.currency)}"
                    raise_to["apply"] = {"budget": want}
                info["main_conflict"] = (f"With meals and local rides included, this plan needs about "
                                         f"{fmt_money(breakdown.planned, inp.currency)}, which is {fmt_money(over, inp.currency)} more than "
                                         f"your budget.")
                return self._infeasible(trip, info)
            self.warn(f"Your pinned choices take the plan {fmt_money(-breakdown.remaining, inp.currency)} over budget.")
        r.done("budget", f"{fmt_money(breakdown.planned, inp.currency)} of {fmt_money(breakdown.budget, inp.currency)} planned, "
                         f"{fmt_money(breakdown.remaining, inp.currency)} left")

        # 8. Finding alternatives ------------------------------------------------------------------------------
        r.start("alternatives")
        if brownout.active(2):  # busy: no alternatives this time (labelled on the plan, ADR 0008)
            alts = {}
            r.done("alternatives", brownout.LABELS[2])
        else:
            alts = self._alternatives(inp, best, options, attractions, restaurants, hotels, rooms, breakdown)
            n_alts = sum(len(v) for v in alts.values())
            r.done("alternatives", f"{n_alts} checked alternatives for {len([k for k, v in alts.items() if v])} items")

        # 9. Creating the itinerary ---------------------------------------------------------------------------
        r.start("itinerary")
        weather = self._weather(dest, plan_day)
        self.warnings[:] = Q.consolidate_warnings(self.warnings)[:4]
        drafts = self._drafts(inp, best, origin, dest, breakdown, rooms)
        narration, llm_status = self._narrate(trip, inp, best, drafts, breakdown, weather, dest)
        itinerary = self._persist(trip, inp, best, options, drafts, alts, narration, llm_status, breakdown, weather,
                                  attractions, restaurants, change_note, origin, dest)
        self._label_brownout(itinerary)
        r.done("itinerary", "Written by AI from verified data" if llm_status in ("ai", "partial")
               else ("Trip data was collected, but AI explanation is temporarily unavailable."
                     if llm_status == "fallback" else "Itinerary assembled"))
        itinerary.pipeline_trace = list(r.trace)
        self.db.flush()
        return PlanResult("ready", trip.feasibility or {}, str(itinerary.id), self.warnings)

    # ------------------------------------------------------------------------------------------------
    def _resolve(self, trip: Trip) -> tuple[PlaceData, PlaceData]:
        places = self.p.places
        origin = places.resolve(trip.origin_name, trip.origin_place_id)
        if origin is None and trip.origin_lat is not None:
            origin = places.reverse(trip.origin_lat, trip.origin_lng)
        dest = places.resolve(trip.destination_name, trip.destination_place_id)
        if self.p.mode == "demo" and (origin is None or dest is None):
            raise AppError("Demo mode has sample data for trips from Gurugram, New Delhi, Noida or Faridabad to Jaipur. "
                           "Add GOOGLE_MAPS_SERVER_KEY to plan any other trip.", code="demo_destination_unsupported",
                           status_code=422)
        if origin is None:
            raise AppError(f"We couldn't locate \u201c{trip.origin_name}\u201d. Try a city or landmark name.",
                           code="origin_not_found", status_code=422)
        if dest is None:
            raise AppError(f"We couldn't locate \u201c{trip.destination_name}\u201d. Try a city name.",
                           code="destination_not_found", status_code=422)
        for label, place in (("destination", dest), ("starting point", origin)):
            if Q.too_broad(place.types):
                raise AppError(f"\u201c{place.name}\u201d is a whole country. Pick a city as your {label}, "
                               f"for example its capital or the city you'll fly into.",
                               code="place_too_broad", status_code=422)
        trip.origin_place_id, trip.origin_lat, trip.origin_lng = origin.provider_place_id, origin.lat, origin.lng
        trip.destination_place_id, trip.destination_lat, trip.destination_lng = dest.provider_place_id, dest.lat, dest.lng
        return origin, dest

    def _store_sources(self, trip: Trip, cands: list[Candidate], results, clear: bool = True) -> None:
        by_key = {c.key: c for c in cands}
        if clear:
            self.db.execute(delete(WebSource).where(WebSource.trip_id == trip.id))
        for v in results:
            c = by_key.get(v.key)
            for s in v.sources:
                if not s.get("url"):
                    continue
                self.db.add(WebSource(place_id=c.db_id if c else None, trip_id=trip.id, field=s.get("field", "opening_hours"),
                                      url=s["url"][:1000], title=(s.get("title") or "")[:500], domain=s.get("domain"),
                                      trust_tier=s["tier"], snippet=s.get("snippet"), extracted_value=s.get("value"),
                                      status=s.get("status", "unverified"),
                                      verified_at=datetime.fromisoformat(s["verified_at"])))
        # Commit at once, like upsert_places: a write left open here blocks every other session's write (response
        # cache, saved timetables) for the rest of the plan (ADR 0001/0002).
        self.db.commit()

    def _min_cost(self, inp: PlanInput, combo: FE.Combo, hotel: Candidate | None, rooms: int,
                  restaurants: list[Candidate], dest: PlaceData) -> FE.MinCost:
        nights = combo.nights if hotel else 0
        stay = hotel.unit_cost * rooms * nights if hotel else Decimal(0)
        frames = PS.build_frames(combo, hotel, dest)
        food = Decimal(0)
        from app.services.ranking import food_ok, serves
        ok = [c for c in restaurants if food_ok(c, inp.effective_food)]
        for f in frames:
            used_today: set[str] = set()  # the same restaurant isn't used twice in one day
            for meal in _meal_stops(f, inp.styles):
                options = sorted((c for c in ok if serves(c, meal.meal) and c.key not in used_today),
                                 key=lambda c: c.unit_cost)
                if options:
                    used_today.add(options[0].key)
                food += (options[0].unit_cost if options else Decimal(F.DEFAULT_MEAL_COST)) * inp.travellers
        mode = FE.local_mode(inp, combo.outbound)
        # at least one ride to reach each meal, plus the ride back at the end of each day
        rides = sum(len(_meal_stops(f, inp.styles)) + 1 for f in frames)
        local = F.local_leg_cost(3000, mode, inp.travellers) * rides
        return FE.MinCost(combo.cost, round_rupees(stay), round_rupees(food), round_rupees(local), nights, rooms,
                          combo.outbound.mode if combo.outbound else None)

    def _with_named_places(self, places: list[PlaceData], restaurants: list[PlaceData], inp: PlanInput,
                           dest: PlaceData) -> list[PlaceData]:
        """Places the traveller picked or named (pinned) that the area search didn't return — the Taj Mahal chosen in
        the wizard, "also visit Fatehpur Sikri" typed later — are looked up one by one, so a pin is never lost just
        because a search query ranked the place out of its top results."""
        have = {p.provider_place_id for p in places} | {p.provider_place_id for p in restaurants}
        extra = []
        for pid in inp.pinned:
            if pid in have or pid in inp.excluded:
                continue
            try:
                place = self.p.places.attraction(pid, dest)
            except (AppError, ProviderUnavailable):
                place = None
            if place is not None:
                extra.append(place)
        return [*places, *extra]

    def _cheapest_any_mode(self, inp, origin, dest, options, modes) -> Decimal | None:
        km = haversine_m(origin.lat, origin.lng, dest.lat, dest.lng) / 1000
        # only modes that could run at this distance: "Allow train and bus" for Delhi -> Istanbul helps nobody
        extra = [m for m in ("train", "bus") if m not in modes and FE.mode_realistic(m, km)]
        pool = list(options)
        if extra:
            try:
                more = self.p.transport.intercity_options(origin, dest, inp.start.date(), PS.return_day(inp),
                                                          inp.start, inp.end, extra, inp.timezone, inp.currency)
                PS.choose_station_access(more, inp, origin)
                pool += more
            except (ProviderUnavailable, AppError):
                pass
        relaxed = PlanInput(**{**inp.__dict__, "pinned_transport": {}})
        combos = FE.build_combos(pool, relaxed, origin, dest)
        return min((c.cost for c in combos), default=None)

    def _attempt(self, inp, combo, hotel, rooms, attractions, restaurants, dest, scale: float = 1.0,
                 known: dict | None = None) -> Attempt | None:
        hotel_here = hotel if combo.nights > 0 else None
        stay = hotel_here.unit_cost * rooms * combo.nights if hotel_here else Decimal(0)
        pct = buffer_pct(inp.styles)
        allowance = inp.budget / (1 + pct) - combo.cost - stay
        # If the safety buffer would leave too little for meals and rides, use part of it (the plan shows as tight)
        mc = self._min_cost(inp, combo, hotel, rooms, restaurants, dest)
        essentials = mc.food + mc.local
        if allowance < essentials:
            allowance = min(inp.budget - combo.cost - stay, essentials)
        if allowance <= 0:
            return None
        frames = PS.build_frames(combo, hotel_here, dest)
        travel = Travel(FE.local_mode(inp, combo.outbound), inp.travellers, scale, known,
                        walk_max_m=age_fit.walk_limit_m(inp.age_groups, F.WALK_MAX_M))
        sched = DayScheduler(inp, travel, attractions, restaurants, set(), allowance)
        spent = Decimal(0)
        for i, f in enumerate(frames):
            f.stops, spent = sched.build(f, spent, frames[i + 1:])
            simulate(f, f.stops, travel)
        breakdown = self._breakdown(inp, combo, frames, hotel_here, rooms)
        # the same day scoring the optimiser uses: sights, meals (lunch/dinner count most), sunset, idle gaps
        value = combo.score + sum(max(-50.0, sched.day_value(f, f.stops, Decimal(0))) for f in frames)
        # Places the traveller asked for by name are worth far more than day_value's per-sight cap lets them count:
        # a short day that fits 2 of their 5 must-sees must not tie with a longer one that fits all 5.
        visited = {s.cand.key for f in frames for s in f.stops if s.cand}
        value += PINNED_VISIT_VALUE * sum(1 for c in attractions if c.pinned and c.key in visited)
        if breakdown.status == "over":
            value -= 100
        return Attempt(combo, frames, sched, travel, round(value, 4), breakdown, hotel_here)

    def _real_routes(self, a: Attempt) -> tuple[str, int, list[str]]:
        """Time the chosen stops with real routes. Returns (route state, failed lookups, names of stops that no longer
        fit because the real times were longer than the estimates)."""
        try:
            state, failures = apply_real_routes(self.p.maps, a.frames, a.travel)
        except Exception:  # never let routing break the plan
            log.warning("real routing failed", exc_info=True)
            state, failures = "estimated", 1
        if failures:
            self.warn("Route information is temporarily unavailable for some legs; those times are estimates.")
        dropped: list[str] = []
        for f in a.frames:
            before = {s.name for s in f.stops}
            f.stops = a.sched._repair(f, f.stops)
            simulate(f, f.stops, a.travel)
            dropped += sorted(before - {s.name for s in f.stops})
        return state, failures, dropped

    def _breakdown(self, inp, combo: FE.Combo, frames: list[DayFrame], hotel: Candidate | None,
                   rooms: int) -> BudgetBreakdown:
        lines: list[BudgetLine] = combo.budget_lines(inp.travellers, inp.currency)
        if hotel and combo.nights > 0:
            lines.append(BudgetLine("stay", f"{hotel.place.name} — {combo.nights} night{'s' if combo.nights > 1 else ''}"
                                            f" × {rooms} {'bed' if inp.stay == 'hostel' else 'room'}{'s' if rooms > 1 else ''}",
                                    round_rupees(hotel.unit_cost * rooms * combo.nights), hotel.cost_state))
        for f in frames:
            local = Decimal(0)
            rides = 0
            for s in f.stops:
                if s.cand:
                    cat = "food" if s.kind == "meal" else "activities"
                    label = f"{MEAL_LABEL.get(s.meal, '')}: {s.name}" if s.kind == "meal" else s.name
                    if s.cand.group_cost(inp.travellers) > 0 or cat == "food":
                        lines.append(BudgetLine(cat, label, round_rupees(s.cand.group_cost(inp.travellers)),
                                                s.cand.cost_state))
                if s.leg and s.leg.cost:
                    local += s.leg.cost
                    rides += 1
            if f.final_leg and f.final_leg.cost:
                local += f.final_leg.cost
                rides += 1
            if local:
                mode = {"cab": "cab", "transit": "metro/bus", "own_car": "fuel & parking"}.get(
                    next((s.leg.mode for s in f.stops if s.leg and s.leg.mode != "walk"), "cab"), "cab")
                day = f" (day {max(f.index, (f.day - inp.start.date()).days) + 1})" if len(frames) > 1 else ""
                lines.append(BudgetLine("local", f"Local {mode}, {rides} ride{'s' if rides > 1 else ''}{day}",
                                        round_rupees(local), "estimated"))
        return compute(inp.budget, inp.travellers, lines, inp.styles)

    def _alternatives(self, inp, a: Attempt, options, attractions, restaurants, hotels, rooms,
                      breakdown: BudgetBreakdown) -> dict[str, list[ALT.AltResult]]:
        remaining = max(Decimal(0), breakdown.remaining)
        used = set(a.sched.used) | {s.cand.key for f in a.frames for s in f.stops if s.cand}
        out: dict[str, list[ALT.AltResult]] = {}
        for f in a.frames:
            for i, s in enumerate(f.stops):
                pool = restaurants if s.kind == "meal" else attractions
                out[f"stop:{f.index}:{i}"] = ALT.place_alternatives(f, f.stops, i, pool, used, a.travel, inp.travellers,
                                                                    inp.effective_food, remaining, inp.currency)
        if a.combo.outbound:
            out["transport:outbound"] = ALT.transport_alternatives(options, a.combo, "outbound", inp, remaining, a.frames)
            out["transport:return"] = ALT.transport_alternatives(options, a.combo, "return", inp, remaining, a.frames)
        if a.hotel:
            centre = PS.centroid([s.cand for f in a.frames for s in f.stops if s.cand], a.hotel.point)
            out["hotel"] = ALT.hotel_alternatives(hotels, a.hotel, a.combo.nights, rooms, centre, remaining, inp.currency)
        return out

    def _weather(self, dest: PlaceData, day) -> dict | None:
        if not self.p.weather:
            return None
        try:
            w = self.p.weather.forecast(dest.lat, dest.lng, day)
        except ProviderUnavailable:
            return None
        if not w:
            return None
        return {"summary": w.summary, "max_c": w.temp_max_c, "min_c": w.temp_min_c,
                "rain_chance": w.precipitation_probability, "state": w.state, "source": w.source}

    # --- itinerary items ------------------------------------------------------------------------------
    def _reasons(self, inp: PlanInput, s: Stop, prev_name: str, frame: DayFrame) -> list[dict]:
        c = s.cand
        out = []
        if c.pinned:
            out.append({"key": "pinned", "text": "the one you asked for"})
        cost = c.group_cost(inp.travellers)
        out.append({"key": "budget", "text": "within your budget" if cost else "free to visit, which helps your budget"})
        o, cl = PS.hours_display(c, frame.day, s.start)
        if c.hours_state == "uncertain":
            out.append({"key": "hours", "text": "planned inside the confirmed part of its opening hours"})
        elif o:
            out.append({"key": "hours", "text": f"open during your planned visit ({o}–{cl})"})
        if c.age_note:
            out.append({"key": "age", "text": c.age_note})
        if s.leg and s.leg.distance_m <= 2500:
            out.append({"key": "nearby", "text": f"close to your previous stop ({s.leg.distance_m / 1000:.1f} km)"})
        elif s.leg:
            out.append({"key": "route", "text": f"the most efficient next stop after {prev_name}"})
        if s.kind == "sunset" and frame.sunset:
            out.append({"key": "sunset", "text": f"one of the best places to watch the sunset at {frame.sunset:%H:%M}"})
        if s.kind == "meal" and c.place.restaurant:
            if inp.effective_food == "veg":
                out.append({"key": "food", "text": "pure vegetarian, matching your preference" if c.place.restaurant.veg_only
                            else "serves vegetarian food, matching your preference"})
            elif inp.effective_food == "non_veg" and c.place.restaurant.serves_non_veg:
                out.append({"key": "food", "text": "serves non-vegetarian food as you prefer"})
        if c.matched_interests:
            out.append({"key": "favourite", "text": f"one of your favourites: {_join(c.matched_interests)}"})
        general = [m for m in c.matched if m not in c.matched_interests]
        if general:
            out.append({"key": "preference", "text": f"matches your interest in {_join(general[:2])}"})
        if c.place.rating:
            count = f" by {c.place.rating_count:,} visitors" if c.place.rating_count else ""
            out.append({"key": "rating", "text": f"rated {c.place.rating}{count}"})
        return out

    @staticmethod
    def _why(reasons: list[dict]) -> str:
        texts = [r["text"] for r in reasons if r["key"] != "rating"][:4]
        return f"Selected because it is {_join(texts)}." if texts else ""

    def _drafts(self, inp: PlanInput, a: Attempt, origin: PlaceData, dest: PlaceData, breakdown: BudgetBreakdown,
                rooms: int) -> list[dict]:
        drafts: list[dict] = []
        combo = a.combo
        tv = inp.travellers

        def add(**kw):
            kw.setdefault("ref", f"i{len(drafts)}")
            kw.setdefault("meta", {})
            kw.setdefault("reasons", [])
            drafts.append(kw)
            return kw

        def transport_item(t: TransportData, day_index: int, leg: dict, alt_key: str):
            fare, access = FE.group_cost(t, tv)
            reason = FE.combo_reason(combo, [combo], inp)
            subtitle = f"{t.from_name} to {t.to_name}"
            if t.meta.get("legs"):  # a change of train: say where, and how long there is to make it
                subtitle += f", changing at {t.meta.get('change_at')} ({t.meta.get('change_minutes')} min to connect)"
            elif t.meta.get("airport_minutes"):
                subtitle += f", including about {_hours(t.meta['airport_minutes'])} at the airports (check-in, security)"
            add(kind="transport", day_index=day_index, start=t.depart_at, end=t.arrive_at,
                title=FE.transport_label(t),
                subtitle=subtitle, cost=fare, cost_state=t.verification_state,
                recommended_minutes=t.duration_minutes, hours_state=t.meta.get("schedule_state", t.verification_state),
                transport=t, alt_key=alt_key, why=reason, **leg,
                reasons=[{"key": "transport", "text": reason},
                         {"key": "time", "text": f"fits your time window (leaves {FE.at(t)}, arrives {FE.at(t, t.arrive_at)})"}],
                sources=[{"label": t.source, "state": t.verification_state}] if t.source else [],
                meta={"mode": t.mode, "service_class": t.service_class, "from": [t.from_lat, t.from_lng],
                      "to": [t.to_lat, t.to_lng], "from_name": t.from_name, "to_name": t.to_name,
                      "distance_m": t.distance_m, "polyline": t.polyline, "per_vehicle": t.per_vehicle,
                      "cost_per_person": float(t.cost_per_person), "comfort": t.comfort, "key": FE.option_key(t),
                      "number": t.meta.get("number"), "operator": t.meta.get("operator"),
                      "changes": t.meta.get("changes"), "legs": t.meta.get("legs"),
                      # provenance (ADR 0002): the serializer redacts train facts unless this is a timetable source
                      "source_kind": t.meta.get("source_kind"), "observed_at": t.meta.get("observed_at"),
                      "freshness": t.meta.get("freshness"), "rail_source": t.meta.get("rail_source")})

        def station_transfer(t: TransportData, day_index: int, outbound: bool) -> bool:
            """The ride between home and the station as a step of its own ("Metro to New Delhi (NDLS)"), so a
            journey with no direct train reads as what the traveller actually does: ride to the hub, then train.
            Short hops stay a travel line on the journey itself. Returns whether a step was added."""
            if not t.access_minutes or (t.access_distance_m or 0) < PS.STATION_TRANSFER_MIN_M:
                return False
            _, access = FE.group_cost(t, tv)
            metro = t.meta.get("access_mode") == "transit"
            how, mode = ("Metro", "transit") if metro else ("Cab", "cab")
            buffer = timedelta(minutes=0 if t.flexible else FE.BOARDING_BUFFER)
            station = (t.from_name if outbound else t.to_name) or "the station"
            spt = [t.from_lat, t.from_lng] if outbound else [t.to_lat, t.to_lng]
            home = [origin.lat, origin.lng]
            if outbound:
                start = t.depart_at - buffer - timedelta(minutes=t.access_minutes)
                title, sub = f"{how} to {station}", f"From {origin.name} to catch the {t.mode} leaving {FE.at(t)}"
                frm, to = home, spt
            else:
                start = t.arrive_at
                title, sub = f"{how} home from {station}", f"Back to {origin.name}"
                frm, to = spt, home
            km = t.access_distance_m / 1000
            add(kind="transport", day_index=day_index, start=start, end=start + timedelta(minutes=t.access_minutes),
                title=title, subtitle=sub, cost=access, cost_state="estimated", recommended_minutes=t.access_minutes,
                reasons=[{"key": "transfer", "text": f"{station} has the connection, {km:.0f} km from {origin.name}"},
                         {"key": "how", "text": "metro is cheaper and avoids traffic" if metro
                          else "a cab door to door is the simplest way with luggage"}],
                why=(f"No better train leaves from {origin.name} itself, so the plan starts with a "
                     f"{how.lower()} ride to {station}." if outbound else
                     f"The train ends at {station}, so the last part home is by {how.lower()}."),
                meta={"mode": mode, "transfer": True, "from": frm, "to": to, "distance_m": t.access_distance_m,
                      "from_name": origin.name if outbound else station, "to_name": station if outbound else origin.name,
                      "per_vehicle": not metro})
            return True

        if combo.outbound:
            o = combo.outbound
            _, access = FE.group_cost(o, tv)
            if station_transfer(o, 0, outbound=True):
                leg = {}  # shown as its own step just above
            else:
                leg = {"distance_m": o.access_distance_m or None, "travel_minutes": o.access_minutes or None,
                       "travel_mode": "cab" if o.access_minutes else None, "travel_cost": access,
                       "leg_state": "estimated" if o.access_minutes else None}
            transport_item(o, 0, leg, "transport:outbound")
        for f in a.frames:
            # The day's number comes from its date, not its position: after an overnight train the first day here is
            # the arrival day, and the departure day holds only the journey.
            di = max(f.index, (f.day - inp.start.date()).days)
            prev_name, prev_end = f.start_name, f.start
            if f.index > 0 and a.hotel:
                checkout = f is a.frames[-1] and not f.stops
                leave = f.start
                if checkout and f.final_leg:  # leave in time to make the departure, by the real route if known
                    leave = min(leave, f.end - timedelta(minutes=f.final_leg.minutes + 5))
                    prev_end = leave
                add(kind="stay", day_index=di, start=leave, end=leave,
                    title=f"Check out: {a.hotel.place.name}" if checkout else f"Start from {a.hotel.place.name}",
                    subtitle="Start of the day", cost=Decimal(0), meta={"lat": a.hotel.place.lat, "lng": a.hotel.place.lng,
                                                                         "marker": False})
            for i, s in enumerate(f.stops):
                c = s.cand
                leave = s.start - timedelta(minutes=s.leg.minutes if s.leg else 0)
                if (leave - prev_end) >= timedelta(minutes=30):
                    add(kind="free", day_index=di, start=prev_end, end=leave, title=f"Free time near {prev_name}",
                        subtitle="Unscheduled — wander, rest or grab a chai", cost=Decimal(0))
                reasons = self._reasons(inp, s, prev_name, f)
                o_, cl_ = PS.hours_display(c, f.day, s.start)
                meal = s.meal
                sub = {"meal": f"{MEAL_LABEL.get(meal, 'Meal')}" + (f" — {', '.join(c.place.restaurant.cuisines[:2])}"
                                                                   if c.place.restaurant and c.place.restaurant.cuisines else ""),
                       "sunset": f"Sunset at {f.sunset:%H:%M}" if f.sunset else "Sunset"}.get(s.kind, c.place.description)
                add(kind=s.kind, day_index=di, start=s.start, end=s.end, title=c.place.name, subtitle=sub,
                    cand=c, cost=round_rupees(c.group_cost(tv)), cost_state=c.cost_state,
                    recommended_minutes=s.duration, opening_time=o_, closing_time=cl_, hours_state=c.hours_state,
                    hours_note=c.hours_note, sources=c.sources, reasons=reasons, why=self._why(reasons),
                    distance_m=s.leg.distance_m if s.leg else None, travel_minutes=s.leg.minutes if s.leg else None,
                    travel_mode=s.leg.mode if s.leg else None, travel_cost=s.leg.cost if s.leg else Decimal(0),
                    leg_state=s.leg.state if s.leg else None, polyline=s.leg.polyline if s.leg else None,
                    alt_key=f"stop:{f.index}:{i}",
                    meta={"lat": c.place.lat, "lng": c.place.lng, "address": c.place.address, "rating": c.place.rating,
                          "rating_count": c.place.rating_count, "tags": c.place.tags[:5], "meal": meal,
                          "veg_only": bool(c.place.restaurant and c.place.restaurant.veg_only),
                          "website": c.place.website, "key": c.key, "arrive": s.arrive.strftime("%H:%M") if s.arrive else None,
                          "per_person": float(c.unit_cost), "leave_prev_at": leave.strftime("%H:%M")})
                prev_name, prev_end = c.place.name, s.end
            fl = f.final_leg
            leg = {"distance_m": fl.distance_m if fl else None, "travel_minutes": fl.minutes if fl else None,
                   "travel_mode": fl.mode if fl else None, "travel_cost": fl.cost if fl else Decimal(0),
                   "leg_state": fl.state if fl else None, "polyline": fl.polyline if fl else None}
            last = f.index == len(a.frames) - 1
            if not last and a.hotel:
                arrive = prev_end + timedelta(minutes=fl.minutes if fl else 0)
                first_night = f.index == 0
                stay_cost = round_rupees(a.hotel.unit_cost * rooms * combo.nights) if first_night else Decimal(0)
                add(kind="stay", day_index=di, start=arrive, end=arrive,
                    title=(f"Check in: {a.hotel.place.name}" if first_night else f"Back to {a.hotel.place.name}"),
                    subtitle=(f"{combo.nights} night{'s' if combo.nights > 1 else ''}, {rooms} "
                              f"{'bed' if inp.stay == 'hostel' else 'room'}{'s' if rooms > 1 else ''}"
                              if first_night else "Overnight"),
                    cand=a.hotel, cost=stay_cost, cost_state=a.hotel.cost_state if first_night else None,
                    alt_key="hotel" if first_night else None, **leg,
                    reasons=[{"key": "stay", "text": f"a {FE.STAY_LABEL.get(inp.stay, inp.stay)} stay as you asked"},
                             {"key": "nearby", "text": "central to the places you're visiting"}] if first_night else [],
                    meta={"lat": a.hotel.place.lat, "lng": a.hotel.place.lng, "key": a.hotel.key,
                          "nightly": float(a.hotel.unit_cost), "stars": a.hotel.place.hotel.stars if a.hotel.place.hotel else None})
            elif last and combo.ret:
                r = combo.ret
                if (r.depart_at - timedelta(minutes=(fl.minutes if fl else 0) + 35)) - prev_end >= timedelta(minutes=45):
                    add(kind="free", day_index=di, start=prev_end,
                        end=r.depart_at - timedelta(minutes=(fl.minutes if fl else 0) + 20),
                        title=f"Free time near {prev_name}", subtitle="Unscheduled before heading back", cost=Decimal(0))
                transport_item(r, di, leg, "transport:return")
                _, access = FE.group_cost(r, tv)
                home = r.arrive_at + timedelta(minutes=r.access_minutes)
                if station_transfer(r, di, outbound=False):
                    add(kind="arrival", day_index=di, start=home, end=home, title=f"Back in {origin.name}",
                        subtitle="Trip complete", cost=Decimal(0), meta={"lat": origin.lat, "lng": origin.lng})
                else:
                    add(kind="arrival", day_index=di, start=home, end=home, title=f"Back in {origin.name}",
                        subtitle="Trip complete", cost=Decimal(0), distance_m=r.access_distance_m or None,
                        travel_minutes=r.access_minutes or None, travel_mode="cab" if r.access_minutes else None,
                        travel_cost=access, leg_state="estimated" if r.access_minutes else None,
                        meta={"lat": origin.lat, "lng": origin.lng})
            elif last:
                end_at = prev_end + timedelta(minutes=fl.minutes if fl else 0)
                add(kind="arrival", day_index=di, start=end_at, end=end_at, title=f"Back at {f.end_name}",
                    subtitle="Trip complete", cost=Decimal(0), **leg, meta={"lat": f.end_point[0], "lng": f.end_point[1]})
        return drafts

    def _narrate(self, trip: Trip, inp: PlanInput, a: Attempt, drafts: list[dict], b: BudgetBreakdown,
                 weather: dict | None, dest: PlaceData) -> tuple[dict, str]:
        days = len(a.frames)
        who = {1: "one", 2: "two", 3: "three", 4: "four"}.get(inp.travellers, str(inp.travellers))
        title = f"{dest.name} {'day trip' if days == 1 else f'in {days} days'} for {who}"
        stops = [d for d in drafts if d["kind"] in ("activity", "sunset")]
        highlights = _join([d["title"] for d in stops[:3]])
        out = next((d for d in drafts if d.get("transport") is not None), None)
        transfer = next((d for d in drafts if d["meta"].get("transfer")), None)
        if out:
            lead = FE.transport_lead(out["transport"])
            if transfer and transfer["start"] <= out["start"]:  # "Metro to New Delhi (NDLS) at 06:05, then take ..."
                lead = f"{transfer['title']} at {transfer['start']:%H:%M}, then {lead[0].lower()}{lead[1:]}"
        else:
            lead = f"Start at {a.frames[0].start:%H:%M}"
        sunset = next((d for d in drafts if d["kind"] == "sunset"), None)
        summary = (f"{lead}, then explore {highlights}" + (f", ending with sunset at {sunset['title']}" if sunset else "")
                   + f". Planned {fmt_money(b.planned, inp.currency)} of your {fmt_money(b.budget, inp.currency)} budget, leaving {fmt_money(b.remaining, inp.currency)}.")
        tips = []
        if any(d.get("hours_state") == "uncertain" for d in drafts):
            tips.append("A couple of opening times disagree between sources — confirm them on the day.")
        if weather and (weather.get("rain_chance") or 0) >= 50:
            tips.append("Rain is likely, so keep an umbrella handy and swap an outdoor stop if needed.")
        elif weather and (weather.get("max_c") or 0) >= 34:
            tips.append("It will be hot in the afternoon — carry water and plan shade breaks.")
        if any(d.get("kind") == "transport" and d["cost_state"] in ("estimated", "demo") for d in drafts):
            tips.append("Fares shown are estimates; check the live fare before booking.")
        tips.append("Tap Change on any stop to see alternatives that still fit your time and budget.")
        template = {"title": title, "summary": summary, "tips": tips[:4],
                    "whys": {d["ref"]: d.get("why", "") for d in drafts if d.get("why")}}
        payload = {
            "trip": {"origin": trip.origin_name, "destination": dest.name, "date": inp.start.date().isoformat(),
                     "travellers": inp.travellers, "traveller_type": inp.traveller_type, "food": inp.effective_food,
                     "styles": inp.styles},
            "budget": {"budget": fmt_money(b.budget, inp.currency), "planned": fmt_money(b.planned, inp.currency),
                       "remaining": fmt_money(b.remaining, inp.currency),
                       "categories": {k: fmt_money(v, inp.currency) for k, v in b.category_totals().items()}},
            "weather": weather,
            "items": [{"item_id": d["ref"], "kind": d["kind"], "time": _clock(d, "start"), "end": _clock(d, "end"),
                       "title": d["title"], "subtitle": d.get("subtitle"),
                       "distance_from_previous": f"{d['distance_m'] / 1000:.1f} km" if d.get("distance_m") else None,
                       "travel_time": f"{d['travel_minutes']} min" if d.get("travel_minutes") else None,
                       "cost": fmt_money(d["cost"], inp.currency) if d.get("cost") else None,
                       "opening_hours": f"{d['opening_time']}–{d['closing_time']}" if d.get("opening_time") else None,
                       "hours_state": d.get("hours_state"), "reasons": [r["text"] for r in d.get("reasons", [])]}
                      for d in drafts if d["kind"] not in ("free",)],
        }
        if brownout.active(1):  # busy: the template text, no AI call (labelled on the plan, ADR 0008)
            return template, "template"
        narration, status, _ = llm_service.narrate(self.p.llm, payload, template, db=self.db, user_id=self.user_id,
                                                   trip_id=trip.id)
        if status == "fallback":
            self.warn("Trip data was collected, but AI explanation is temporarily unavailable.")
        if a.combo.nights > 0 and re.search(r"\bday[- ]trip\b", narration.get("title") or "", re.I):
            narration["title"] = title  # "A Day Trip to Agra" on a plan with a night in Agra: use ours
        return narration, status

    def _persist(self, trip: Trip, inp: PlanInput, a: Attempt, options: list[TransportData], drafts: list[dict],
                 alts: dict, narration: dict, llm_status: str, b: BudgetBreakdown, weather, attractions, restaurants,
                 change_note: str | None, origin: PlaceData, dest: PlaceData) -> Itinerary:
        db = self.db
        db.execute(delete(TransportOption).where(TransportOption.trip_id == trip.id))
        opt_ids: dict[str, object] = {}
        chosen_keys = {FE.option_key(t) for t in (a.combo.outbound, a.combo.ret) if t}
        for t in options:
            fare, access = FE.group_cost(t, inp.travellers)
            row = TransportOption(trip_id=trip.id, direction=t.direction, mode=t.mode, name=t.name[:200],
                                  service_class=t.service_class, depart_at=t.depart_at, arrive_at=t.arrive_at,
                                  from_name=t.from_name, to_name=t.to_name, from_lat=t.from_lat, from_lng=t.from_lng,
                                  to_lat=t.to_lat, to_lng=t.to_lng, duration_minutes=t.duration_minutes,
                                  distance_m=t.distance_m, cost_per_person=t.cost_per_person, cost_total=fare + access,
                                  comfort=t.comfort, verification_state=t.verification_state, source=t.source,
                                  is_selected=FE.option_key(t) in chosen_keys, polyline=t.polyline,
                                  meta={"key": FE.option_key(t), "access_minutes": t.access_minutes,
                                        "access_cost": float(t.access_cost), "per_vehicle": t.per_vehicle,
                                        "flexible": t.flexible})
            db.add(row)
            opt_ids[FE.option_key(t)] = row
        db.flush()
        if a.combo.outbound:
            trip.selected_transport_id = opt_ids[FE.option_key(a.combo.outbound)].id

        version = (db.scalar(select(Itinerary.version).where(Itinerary.trip_id == trip.id)
                             .order_by(Itinerary.version.desc()).limit(1)) or 0) + 1
        db.execute(update(Itinerary).where(Itinerary.trip_id == trip.id).values(is_current=False))
        it = Itinerary(trip_id=trip.id, version=version, is_current=True, title=narration["title"][:200],
                       summary=narration["summary"], tips=narration.get("tips", []), total_cost=b.planned,
                       remaining=b.remaining, budget_breakdown=b.to_dict(len(a.frames) > 1), warnings=self.warnings,
                       pipeline_trace=list(self.r.trace), weather=weather, llm_status=llm_status,
                       change_note=change_note,
                       candidate_pool={"places": [{"id": c.key, "name": c.place.name, "kind": c.kind}
                                                  for c in attractions + restaurants],
                                       "transport": {"outbound": FE.option_key(a.combo.outbound) if a.combo.outbound else None,
                                                     "return": FE.option_key(a.combo.ret) if a.combo.ret else None},
                                       "hotel": a.hotel.key if a.hotel else None,
                                       "local_mode": a.travel.local_mode, "data_mode": self.p.mode})
        db.add(it)
        db.flush()
        whys = narration.get("whys", {})
        # I6 (ADR 0005): when the plan used Google places, every non-transport item carries Google content (a name,
        # coordinates, a place's rating or hours) and is stamped with when it was fetched
        stamp = datetime.now(UTC) if any(d.get("cand") and d["cand"].place.provider == "google" for d in drafts) else None
        for pos, d in enumerate(drafts):
            cand: Candidate | None = d.get("cand")
            t: TransportData | None = d.get("transport")
            item = ItineraryItem(
                itinerary_id=it.id, position=pos, day_index=d["day_index"], kind=d["kind"], start_at=d["start"],
                end_at=d["end"], title=d["title"][:255], subtitle=(d.get("subtitle") or "")[:500] or None,
                place_id=cand.db_id if cand else None,
                transport_option_id=opt_ids[FE.option_key(t)].id if t else None,
                distance_from_prev_m=d.get("distance_m"), travel_minutes_from_prev=d.get("travel_minutes"),
                travel_mode=d.get("travel_mode"), travel_cost=d.get("travel_cost") or 0, leg_state=d.get("leg_state"),
                polyline=d.get("polyline"), cost=d.get("cost") or 0, cost_state=d.get("cost_state"),
                recommended_minutes=d.get("recommended_minutes"), opening_time=d.get("opening_time"),
                closing_time=d.get("closing_time"), hours_state=d.get("hours_state"), hours_note=d.get("hours_note"),
                sources=d.get("sources") or [], reasons=d.get("reasons") or [],
                explanation=whys.get(d["ref"]) or d.get("why"), meta=d.get("meta") or {},
                google_fetched_at=stamp if d["kind"] != "transport" else None)
            db.add(item)
            db.flush()
            for rank, alt in enumerate(alts.get(d.get("alt_key") or "", [])):
                db.add(Alternative(
                    itinerary_item_id=item.id, place_id=alt.cand.db_id if alt.cand else None,
                    transport_option_id=opt_ids[FE.option_key(alt.transport)].id if alt.transport else None,
                    kind=alt.kind, title=alt.title[:255], rank=rank, score=alt.score, cost=alt.cost,
                    cost_delta=alt.cost_delta, time_delta_minutes=alt.time_delta, distance_from_prev_m=alt.distance_m,
                    reason=alt.reason, checks=alt.checks,
                    meta={**alt.meta, "key": alt.cand.key if alt.cand else alt.meta.get("key"),
                          "lat": alt.cand.place.lat if alt.cand else None, "lng": alt.cand.place.lng if alt.cand else None,
                          "rating": alt.cand.place.rating if alt.cand else None},
                    google_fetched_at=stamp if alt.cand and alt.cand.place.provider == "google" else None))
        combos_reason = FE.combo_reason(a.combo, [a.combo], inp)
        trip.feasibility = {"feasible": True, "status": b.status, "transport": combos_reason,
                            "checks": {"budget": b.status != "over", "time_window": True,
                                       "opening_hours": not any(d.get("hours_state") == "uncertain" for d in drafts)}}
        trip.status = "ready"
        trip.data_mode = self.p.mode
        trip.plan_count = (trip.plan_count or 0) + 1
        return it

    def _infeasible(self, trip: Trip, info: dict) -> PlanResult:
        if self.no_timetable and info.get("main_conflict") and info.get("kind") != "no_service":
            # Say what WAS priced: "only road options" was added even when the cheapest way was a flight.
            priced = [FE.MODE_LABEL.get(m, m) for m in self.priced_modes]
            road_only = bool(self.priced_modes) and set(self.priced_modes) <= {"cab", "own_car"}
            info["main_conflict"] += (f" No {' or '.join(self.no_timetable)} timetable was available for this route, "
                                      + ("so only road options were priced." if road_only else
                                         f"so prices are for {_join(priced)}." if priced else "so nothing could be priced."))
        trip.feasibility = info
        trip.status = "draft" if trip.current_itinerary is None else "ready"
        if trip.current_itinerary is None:
            trip.data_mode = self.p.mode  # otherwise a live trip shows the "Demo mode" badge
        self.db.flush()
        return PlanResult("infeasible", info, None, self.warnings)