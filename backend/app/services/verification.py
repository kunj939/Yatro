"""Web verification of current information (opening hours, closures).

Source priority: 1) official  2) trusted structured provider/API  3) reputable secondary  4) generic.
Rules:
* An official source that states hours wins -> `verified_official` (disagreements are noted).
* Structured provider hours with no disagreement -> `verified_provider`.
* Any unresolved disagreement -> `uncertain`: we schedule inside the narrower window and ask the user to confirm.
* A recent source mentioning a closure -> `uncertain` with a closure note (and a ranking penalty).
Every source consulted is returned with its URL and a verification timestamp so it can be stored in web_sources.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from urllib.parse import urlparse

from app.core.telemetry import ContextThreadPool
from app.core.errors import ProviderUnavailable
from app.providers.base import WebSearchProvider
from app.services.engine_types import Candidate
from app.utils import hours as H

log = logging.getLogger("tripai.verify")

OFFICIAL_SUFFIXES = (".gov.in", ".nic.in", ".gov", ".gov.uk", ".gouv.fr", ".museum")
STRUCTURED_DOMAINS = ("google.com", "maps.google.com", "openstreetmap.org", "wikidata.org")
REPUTABLE_DOMAINS = ("tripadvisor.", "lonelyplanet.com", "wikipedia.org", "timesofindia.", "thehindu.com",
                     "incredibleindia.org", "tourism.rajasthan.gov.in", "hindustantimes.com", "indianexpress.com",
                     "britannica.com", "asi.nic.in", "makemytrip.com", "holidify.com", "cntraveller.in")
CLOSURE = re.compile(r"(temporarily closed|closed for (?:renovation|restoration|repairs|maintenance)|closed (?:till|until)"
                     r"|shut (?:till|until|for)|remain(?:s)? closed)", re.IGNORECASE)
TIER_RANK = {"official": 0, "structured": 1, "reputable": 2, "generic": 3}


@dataclass
class Verification:
    key: str
    state: str
    note: str | None = None
    sources: list[dict] = field(default_factory=list)
    closure: bool = False
    override: tuple[str, str] | None = None  # (open, close) to use for scheduling on the trip day


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def trust_tier(url: str, website: str | None) -> str:
    d = domain_of(url)
    site = domain_of(website) if website else ""
    if site and (d == site or d.endswith("." + site) or site.endswith("." + d)):
        return "official"
    if d.endswith(OFFICIAL_SUFFIXES):
        return "official"
    if any(d == s or d.endswith("." + s) for s in STRUCTURED_DOMAINS):
        return "structured"
    if any(r in d for r in REPUTABLE_DOMAINS):
        return "reputable"
    return "generic"


def _fmt(span: tuple[str, str] | None) -> str:
    return f"{span[0]}–{span[1]}" if span else "unknown"


def verify_place(search: WebSearchProvider, cand: Candidate, day: date, provider_state: str) -> Verification:
    place = cand.place
    hours = place.opening_hours
    spans = H.intervals_for(hours, day)
    provider_span = (spans[0][0].strftime("%H:%M"), spans[0][1].strftime("%H:%M")) if spans else None
    now = datetime.now(UTC).isoformat()
    query = f"{place.name} {place.city or ''} opening hours".strip()
    try:
        results = search.search(query, num=5)
    except ProviderUnavailable as exc:
        log.info("search unavailable for %s: %s", place.name, exc)
        return Verification(cand.key, provider_state if provider_span else "unavailable",
                            "Some current information could not be verified.")
    sources, found = [], []
    closure_note = None
    for r in results:
        tier = trust_tier(r.url, place.website)
        value = H.extract_hours_from_text(r.snippet)
        closed = CLOSURE.search(r.snippet or "")
        src = {"url": r.url, "title": r.title, "domain": domain_of(r.url), "tier": tier, "snippet": r.snippet[:400],
               "value": {"open": value[0], "close": value[1]} if value else None, "verified_at": now,
               "field": "closure" if closed else "opening_hours"}
        if closed and not closure_note:
            closure_note = f"{domain_of(r.url)} mentions a closure (\u201c{closed.group(0)}\u201d). Please confirm before visiting."
        if value:
            found.append((tier, value, src))
        sources.append(src)
    if place.opening_hours and provider_span:
        sources.insert(0, {"url": place.website or "", "title": "Map data (structured provider)", "domain": place.provider,
                           "tier": "structured", "value": {"open": provider_span[0], "close": provider_span[1]},
                           "verified_at": now, "field": "opening_hours", "snippet": None})

    found.sort(key=lambda f: TIER_RANK[f[0]])
    official = [f for f in found if f[0] == "official"]
    v = Verification(cand.key, provider_state if provider_span else "unavailable", sources=sources)
    if official:
        span = official[0][1]
        v.state = "verified_official"
        if provider_span and span != provider_span:
            v.note = f"Official site lists {_fmt(span)}; map data says {_fmt(provider_span)}. Using the official hours."
            v.override = span
        for f in found:
            f[2]["status"] = "confirmed" if f[1] == span else "conflict"
    elif found:
        others = {f[1] for f in found}
        if provider_span is None:
            span = found[0][1]
            if len(others) == 1:
                v.state, v.override = "estimated", span
                v.note = f"Hours taken from {found[0][2]['domain']} ({_fmt(span)}); not confirmed by an official source."
            else:
                v.state = "uncertain"
                narrow = (max(o for o, _ in others), min(c for _, c in others))
                v.override = narrow if narrow[0] < narrow[1] else span
                v.note = "Sources disagree on opening hours. Please confirm before visiting."
        elif others == {provider_span}:
            for f in found:
                f[2]["status"] = "confirmed"
        else:
            v.state = "uncertain"
            conflict = next(f for f in found if f[1] != provider_span)
            v.note = (f"Sources disagree: map data says {_fmt(provider_span)}, {conflict[2]['domain']} says "
                      f"{_fmt(conflict[1])}. Planned inside the narrower window — please confirm.")
            narrow = (max(provider_span[0], conflict[1][0]), min(provider_span[1], conflict[1][1]))
            if narrow[0] < narrow[1] and narrow != provider_span:
                v.override = narrow
            for f in found:
                f[2]["status"] = "confirmed" if f[1] == provider_span else "conflict"
    elif provider_span is None:
        v.note = "Opening hours are not published. Please confirm locally."
    if closure_note:
        v.state, v.closure = "uncertain", True
        v.note = closure_note if not v.note else f"{closure_note} {v.note}"
    for s in sources:
        s.setdefault("status", "unverified")
    return v


def apply(cand: Candidate, v: Verification, day: date) -> None:
    cand.hours_state = v.state
    cand.hours_note = v.note
    cand.sources = v.sources
    if v.override and cand.place.opening_hours is not None:
        spans = H.intervals_for(cand.place.opening_hours, day) or []
        rest = [[o.strftime("%H:%M"), c.strftime("%H:%M")] for o, c in spans[1:]]
        special = dict(cand.place.opening_hours.get("special") or {})
        special[day.isoformat()] = [[v.override[0], v.override[1]], *rest]
        cand.place.opening_hours = {**cand.place.opening_hours, "special": special}
    elif v.override:
        cand.place.opening_hours = {"weekly": {}, "special": {day.isoformat(): [[v.override[0], v.override[1]]]},
                                    "source": "web"}
    if v.closure:
        cand.score = round(cand.score - 0.3, 4)


def verify_candidates(search: WebSearchProvider | None, cands: list[Candidate], day: date,
                      provider_state: str) -> tuple[list[Verification], bool]:
    """Verify in parallel. Returns (results, search_available)."""
    if search is None:
        for c in cands:
            has = bool(H.intervals_for(c.place.opening_hours, day))
            c.hours_state = provider_state if has else "unavailable"
            c.hours_note = None if has else "Opening hours are not published. Please confirm locally."
        return [], False
    with ContextThreadPool(max_workers=6) as pool:
        results = list(pool.map(lambda c: verify_place(search, c, day, provider_state), cands))
    for c, v in zip(cands, results):
        apply(c, v, day)
    available = not all(v.note == "Some current information could not be verified." for v in results) if results else True
    return results, available
