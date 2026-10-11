"""Chooses which Indian Railways timetable source(s) to use, and lets Google handle everything else
(buses, flights, and trains outside India).

Tries each configured rail source in order and returns the first one that finds trains. If a source is
rate-limited, out of quota, or errors, the next configured source is tried automatically — the traveller
never sees which source answered, only whether a train was found and, if none were, why not.

If no source has a timetable — none configured, all down, quota used up and nothing saved — the trip still gets
a train: an estimate between the best-connected stations at each end (rail_common.estimated_rail_options), so
Gurugram -> Rishikesh becomes "ride to New Delhi, train to Haridwar, road to Rishikesh" rather than no train at
all, or a made-up train running from Gurugram's own coordinates.
"""
from __future__ import annotations

import logging

from app.core.telemetry import ContextThreadPool
from app.core.config import get_settings
from app.core.provenance import SourceKind
from app.gateway import routing
from app.gateway.breaker import breakers
from app.providers.rail_common import estimated_rail_options, in_india
from app.providers.rail_sources import AiGuidance, NoAiGuidance, NoOfficialTimetable, OfficialTimetable

log = logging.getLogger("tripai.rail_chain")

# No Indian train averages more than about 95 km/h over a whole journey (the fastest Vande Bharat and Rajdhani runs).
# Distances here are road estimates, which can be a little longer than the line, so the bar sits well above that.
MAX_TRAIN_KMH = 115


def plausible(t) -> bool:
    """False for a timetable row that can't be a real journey between these two places: a short local train matched
    to a long route (a Hanumangarh passenger train listed Jaipur -> Madgaon, 1,900 km in nine hours), or an arrival
    before the departure. Such a row would put the traveller at the destination a day early, sightseeing at 3 am."""
    if getattr(t, "mode", None) != "train":
        return True
    minutes, km = t.duration_minutes or 0, (t.distance_m or 0) / 1000
    if minutes <= 0:
        return False
    return km / (minutes / 60) <= MAX_TRAIN_KMH


class RailChain:
    """Tries each rail provider in order; returns the first with trains, or empty with the best explanation."""

    def __init__(self, providers: list):
        self.providers = providers
        self.last_issue: str | None = None
        self.name = "+".join(p.name for p in providers) or "rail-estimate"

    def intercity(self, origin, dest, depart_day, return_day):
        issues = []
        for provider in routing.order(self.providers):
            try:
                out = provider.intercity(origin, dest, depart_day, return_day)
            except Exception as exc:  # noqa: BLE001 - one source failing must never break the others
                log.warning("%s failed unexpectedly, trying the next source: %s", provider.name, exc)
                issues.append(f"{provider.name} had an unexpected problem")
                continue
            dropped = [x for x in out if not plausible(x)]
            if dropped:
                log.warning("%s: dropped %d train(s) %s -> %s that would beat %d km/h (e.g. %s %s)", provider.name,
                            len(dropped), origin.name, dest.name, MAX_TRAIN_KMH, dropped[0].meta.get("number"), dropped[0].name)
                out = [x for x in out if plausible(x)]
            if out:
                self.last_issue = None
                return out
            # A source whose circuit is open (IRCTC1 with its quota used up) is not a reason to tell the traveller anything
            if provider.last_issue and not breakers.is_open(provider.name):
                issues.append(provider.last_issue)
        self.last_issue = "; also, ".join(dict.fromkeys(issues)) if issues else None
        return []


class RailAwareTransport:
    """Google for buses, flights and trains abroad; the rail chain (or, failing that, a station-network estimate)
    for trains inside India."""

    def __init__(self, base, chain: RailChain, official: OfficialTimetable | None = None, ai: AiGuidance | None = None):
        self.base, self.chain = base, chain
        self.official = official or NoOfficialTimetable()  # source chain link 3 (ADR 0002)
        self.ai = ai or NoAiGuidance()  # link 5, behind AI_RAIL_GUIDANCE
        self.name = f"{base.name}+{chain.name}"

    def __getattr__(self, item):
        return getattr(self.base, item)

    def intercity_options(self, origin, dest, depart_day, return_day, window_start, window_end, modes,
                          tz: str = "Asia/Kolkata", currency: str = "INR"):
        india = in_india(origin) and in_india(dest)
        wants_train = "train" in modes or "mixed" in modes
        if not (india and wants_train):
            return self.base.intercity_options(origin, dest, depart_day, return_day, window_start, window_end,
                                               modes, tz, currency)
        has_source = bool(self.chain.providers)
        # With a timetable source, Google is only asked for the other modes (it has no Indian Railways timetable,
        # and never add a cab the traveller didn't ask for); without one, it's asked for trains too, in case its
        # transit data has them. India has one timezone and rupee fares, so the rail side needs neither tz nor
        # currency — only the non-rail modes (cab/bus/flight, via Google) do.
        google_modes = [m for m in modes if m != "train"] if has_source else list(modes)
        # The two lookups are independent network calls: run them side by side instead of one after the other.
        with ContextThreadPool(max_workers=2, thread_name_prefix="transport") as pool:
            g = pool.submit(self.base.intercity_options, origin, dest, depart_day, return_day, window_start,
                            window_end, google_modes, tz, currency) if google_modes else None
            r = pool.submit(self.chain.intercity, origin, dest, depart_day, return_day) if has_source else None
            google = g.result() if g else []
            rail = r.result() if r else []
        def is_train(t, scheduled=False):
            return getattr(t, "mode", None) == "train" and not (scheduled and getattr(t, "flexible", False))
        if not any(is_train(t, scheduled=True) for t in google + rail):
            rail = self.official.intercity(origin, dest, depart_day, return_day) or rail  # 3. official dataset
        if window_start and window_end and not any(is_train(t, scheduled=True) for t in google + rail):
            # No real timetable anywhere. Google's own fallback is a straight-line "train" from the exact start
            # point, which ignores where the stations are; replace it with one routed through real stations.
            est = estimated_rail_options(origin, dest, depart_day, return_day, window_start, window_end)  # 4.
            if not est:  # 5. AI guidance: stations only, each one checked against the gazetteer
                o_st, d_st = self.ai.stations(origin, dest)
                if o_st and d_st:
                    est = estimated_rail_options(origin, dest, depart_day, return_day, window_start, window_end,
                                                 stations=(o_st[0], d_st[0]), source=SourceKind.AI_GUIDANCE)
            if est:
                google = [t for t in google if not is_train(t)]
                rail = est
                log.info("No train timetable for %s -> %s; using a station-network estimate (%s to %s)",
                         origin.name, dest.name, est[0].from_name, est[0].to_name)
        return google + rail

    @property
    def last_rail_issue(self) -> str | None:
        return self.chain.last_issue


def build(base_transport, places):
    """Wraps `base_transport` (Google) with whichever Indian Railways timetable sources have a key configured,
    RailRadar first (a larger free quota) then IRCTC1 (which also, when configured, helps RailRadar look up
    station codes for cities outside the built-in list — see rail_common.KNOWN_STATIONS). With neither, it still
    wraps Google, so Indian train journeys fall back to the station-network estimate."""
    s = get_settings()
    irctc = None
    if s.irctc_rapidapi_key:
        from app.providers.indian_rail import IndianRailProvider
        irctc = IndianRailProvider(places, s.irctc_rapidapi_key, s.irctc_rapidapi_host)

    providers = []
    if s.railradar_api_key:
        from app.providers.railradar import RailRadarProvider
        station_search = irctc._station_rows if irctc else None  # noqa: SLF001 - a deliberate, documented reuse
        providers.append(RailRadarProvider(places, s.railradar_api_key, station_search=station_search))
    if irctc:
        providers.append(irctc)

    if s.ai_rail_guidance:
        # Interface only (ADR 0002): no AI station-guidance implementation is wired yet, so this stays a no-op.
        log.warning("AI_RAIL_GUIDANCE is on, but no AI guidance implementation is configured; skipping that source")
    return RailAwareTransport(base_transport, RailChain(providers))
