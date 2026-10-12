"""Phase 0 measurement: what one plan costs, end to end (ADR 0001).

A `PlanTrace` lives in a context variable for the duration of one plan and records:

- every outgoing HTTP attempt (via `TracingTransport` on the shared client): provider, endpoint template, status,
  latency, estimated list-price cost
- response-cache hits and misses (`providers/cache.py`), and saved-timetable hits (rail)
- every rail station pair tried, and what it returned: trains / empty / quota / timeout / error
- planner stages (`StageReporter`) and the time to the first progress event
- database queries (count and time)
- LLM tokens and cost

Spans are plain dicts shaped like OpenTelemetry spans (name, start, end, attributes) so Phase 7 can export the same
data without touching call sites. Nothing here changes behaviour: with no active trace, or with TELEMETRY_ENABLED
off, every function is a cheap no-op. Traces hold provider names, endpoint templates and timings only: no query
text, coordinates, user ids or emails.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("tripai.telemetry")

_current: contextvars.ContextVar[PlanTrace | None] = contextvars.ContextVar("plan_trace", default=None)
_lock = threading.Lock()
RECENT: deque[dict] = deque(maxlen=50)  # finished trace summaries, newest last (read by scripts/baseline.py)

# --- What a call costs ----------------------------------------------------------------------------------------------
# List-price estimates in USD per request, for the cost model only; verify against real billing. Overridable with the
# TELEMETRY_PRICES env var (JSON: {"<provider> <endpoint>": usd}). Rail APIs are counted in quota units, not dollars.
DEFAULT_PRICES: dict[str, float] = {
    "google-places POST /v1/places:searchText": 0.032,       # Text Search (Pro fields)
    "google-places GET /v1/places/{id}": 0.017,              # Place Details (Pro fields)
    "google-places POST /v1/places:autocomplete": 0.00283,
    "google-routes POST /directions/v2:computeRoutes": 0.005,
    "google-routes POST /distanceMatrix/v2:computeRouteMatrix": 0.005,  # per element; see _elements()
    "google-geocoding GET /maps/api/geocode/json": 0.005,
    "brave GET /res/v1/web/search": 0.003,
    "serper POST /search": 0.001,
    "tavily POST /search": 0.008,
}
QUOTA_PROVIDERS = {"railradar", "irctc1"}

HOSTS = {
    "places.googleapis.com": "google-places", "routes.googleapis.com": "google-routes",
    "maps.googleapis.com": "google-geocoding", "openrouter.ai": "openrouter", "api.railradar.in": "railradar",
    "railradar.in": "railradar", "irctc1.p.rapidapi.com": "irctc1", "api.open-meteo.com": "open-meteo",
    "api.search.brave.com": "brave", "google.serper.dev": "serper", "api.tavily.com": "tavily",
    "query.wikidata.org": "wikidata",
}

_ID = re.compile(r"/(?:[A-Za-z0-9_-]{20,}|\d{3,}|[A-Z]{2,5})(?=/|$)")


def endpoint_template(path: str) -> str:
    """/v1/places/ChIJ...xyz -> /v1/places/{id}; /trains/between/JP/NDLS -> /trains/between/{id}/{id}. Never the
    query string, so no search text or coordinates are kept."""
    return _ID.sub("/{id}", path)


def provider_of(host: str) -> str:
    for h, name in HOSTS.items():
        if host == h or host.endswith("." + h):
            return name
    return host


def _prices() -> dict[str, float]:
    from app.core.config import get_settings
    raw = get_settings().telemetry_prices
    if not raw:
        return DEFAULT_PRICES
    try:
        return {**DEFAULT_PRICES, **json.loads(raw)}
    except ValueError:
        log.warning("TELEMETRY_PRICES is not valid JSON; using the defaults")
        return DEFAULT_PRICES


def _elements(request: httpx.Request) -> int:
    """A route matrix is billed per origin x destination element."""
    try:
        body = json.loads(request.content or b"{}")
        return max(1, len(body.get("origins", [])) * len(body.get("destinations", [])))
    except (ValueError, TypeError):
        return 1


# --- The trace -----------------------------------------------------------------------------------------------------

@dataclass
class PlanTrace:
    label: str
    t0: float = field(default_factory=time.perf_counter)
    calls: list[dict] = field(default_factory=list)
    cache: list[dict] = field(default_factory=list)
    pairs: list[dict] = field(default_factory=list)
    stages: list[dict] = field(default_factory=list)
    llm: list[dict] = field(default_factory=list)
    gateway: list[dict] = field(default_factory=list)  # retries, breaker/budget/deadline skips (ADR 0007)
    db_queries: int = 0
    db_ms: float = 0.0
    first_event_ms: float | None = None
    end_ms: float | None = None
    _open_stage: dict | None = None

    def ms(self) -> float:
        return round((time.perf_counter() - self.t0) * 1000, 1)

    def add(self, bucket: str, item: dict) -> None:
        with _lock:
            getattr(self, bucket).append(item)

    def summary(self) -> dict:
        calls = [c for c in self.calls]
        by: dict[str, dict] = {}
        for c in calls:
            k = f"{c['provider']} {c['endpoint']}"
            b = by.setdefault(k, {"calls": 0, "errors": 0, "ms": [], "usd": 0.0})
            b["calls"] += 1
            b["errors"] += 0 if c["ok"] else 1
            b["ms"].append(c["ms"])
            b["usd"] += c.get("usd", 0.0)
        endpoints = {k: {"calls": v["calls"], "errors": v["errors"], "usd": round(v["usd"], 4),
                         "p50_ms": _pct(v["ms"], 50), "max_ms": max(v["ms"])} for k, v in sorted(by.items())}
        pair_outcomes: dict[str, int] = {}
        for p in self.pairs:
            pair_outcomes[p["outcome"]] = pair_outcomes.get(p["outcome"], 0) + 1
        hits = sum(1 for c in self.cache if c["hit"])
        return {
            "label": self.label,
            "total_ms": self.end_ms,
            "first_event_ms": self.first_event_ms,
            "stages": self.stages,
            "http_calls": len(calls),
            "http_errors": sum(1 for c in calls if not c["ok"]),
            "endpoints": endpoints,
            "usd_google_and_search": round(sum(c.get("usd", 0.0) for c in calls), 4),
            "rail_quota_units": sum(1 for c in calls if c["provider"] in QUOTA_PROVIDERS),
            "cache": {"hits": hits, "misses": len(self.cache) - hits,
                      "by_provider": _cache_by_provider(self.cache)},
            "rail_pairs": {"tried": len(self.pairs), "outcomes": pair_outcomes, "detail": self.pairs},
            "gateway": {"events": len(self.gateway), "not_ok": [g for g in self.gateway if g.get("outcome") != "ok"][:40]},
            "llm": {"calls": len(self.llm), "tokens": sum(x["tokens"] for x in self.llm),
                    "usd": round(sum(x["usd"] for x in self.llm), 5)},
            "db": {"queries": self.db_queries, "ms": round(self.db_ms, 1)},
        }


def _pct(xs: list[float], p: int) -> float:
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(len(s) * p / 100))], 1) if s else 0.0


def _cache_by_provider(rows: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for r in rows:
        b = out.setdefault(r["provider"], {"hits": 0, "misses": 0})
        b["hits" if r["hit"] else "misses"] += 1
    return out


def enabled() -> bool:
    from app.core.config import get_settings
    return get_settings().telemetry_on


def current() -> PlanTrace | None:
    return _current.get()


@contextmanager
def plan_trace(label: str):
    """Record everything one plan does. Yields the trace (or None when telemetry is off)."""
    if not enabled():
        yield None
        return
    tr = PlanTrace(label=label)
    token = _current.set(tr)
    try:
        yield tr
    finally:
        tr.end_ms = tr.ms()
        _current.reset(token)
        s = tr.summary()
        RECENT.append(s)
        log.info("plan trace %s: %.0f ms, %d HTTP calls (%d errors), ~$%.3f, %d rail pairs %s, %d DB queries",
                 label, s["total_ms"], s["http_calls"], s["http_errors"], s["usd_google_and_search"],
                 s["rail_pairs"]["tried"], s["rail_pairs"]["outcomes"], s["db"]["queries"])


# --- Recording helpers (all no-ops without an active trace) ---------------------------------------------------------

def record_cache(provider: str, hit: bool) -> None:
    tr = _current.get()
    if tr:
        tr.add("cache", {"provider": provider, "hit": hit})


def record_pair(source: str, frm: str, to: str, outcome: str, trains: int = 0, kind: str = "direct") -> None:
    """outcome: trains | empty | quota | timeout | error. `kind`: direct or connection leg."""
    tr = _current.get()
    if tr:
        tr.add("pairs", {"source": source, "pair": f"{frm}-{to}", "outcome": outcome, "trains": trains, "kind": kind})


def stage_start(key: str) -> None:
    from app.gateway import context as gateway_context  # which stage a skipped call belongs to (deadline notes)
    gateway_context.set_stage(key)
    tr = _current.get()
    if tr:
        if tr.first_event_ms is None:
            tr.first_event_ms = tr.ms()
        tr._open_stage = {"name": f"stage.{key}", "start_ms": tr.ms(), "db0": tr.db_queries, "http0": len(tr.calls)}


def stage_end(key: str, status: str) -> None:
    tr = _current.get()
    if tr and tr._open_stage:
        st = tr._open_stage
        tr.add("stages", {"name": st["name"], "start_ms": st["start_ms"], "end_ms": tr.ms(), "status": status,
                          "db_queries": tr.db_queries - st["db0"], "http_calls": len(tr.calls) - st["http0"]})
        tr._open_stage = None


def record_llm(tokens: int, usd: float | None) -> None:
    tr = _current.get()
    if tr:
        tr.add("llm", {"tokens": int(tokens or 0), "usd": float(usd or 0.0)})


# --- HTTP: one transport sees every attempt from every provider ---------------------------------------------------

class TracingTransport(httpx.HTTPTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        tr = _current.get()
        if tr is None:
            return super().handle_request(request)
        t = time.perf_counter()
        provider, endpoint = provider_of(request.url.host), f"{request.method} {endpoint_template(request.url.path)}"
        try:
            resp = super().handle_request(request)
        except Exception as exc:
            tr.add("calls", {"provider": provider, "endpoint": endpoint, "status": type(exc).__name__, "ok": False,
                             "ms": round((time.perf_counter() - t) * 1000, 1), "usd": 0.0})
            raise
        price = _prices().get(f"{provider} {endpoint}", 0.0)
        if "computeRouteMatrix" in endpoint:
            price *= _elements(request)
        ok = resp.status_code < 400
        tr.add("calls", {"provider": provider, "endpoint": endpoint, "status": resp.status_code, "ok": ok,
                         "ms": round((time.perf_counter() - t) * 1000, 1), "usd": price if ok else 0.0})
        return resp


# --- Thread pools that carry the trace into their worker threads ----------------------------------------------------

class ContextThreadPool(ThreadPoolExecutor):
    """A ThreadPoolExecutor whose tasks run in the submitting thread's context, so calls made from pool threads
    (parallel transit lookups, opening-hours checks) are still attributed to the plan. Otherwise identical."""

    def submit(self, fn, /, *args, **kwargs):
        ctx = contextvars.copy_context()
        return super().submit(ctx.run, fn, *args, **kwargs)


# --- Database: count queries while a trace is active ----------------------------------------------------------------

def install_db_listener(engine: Any) -> None:
    from sqlalchemy import event

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        if _current.get() is not None:
            conn.info.setdefault("_tel_t", []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        tr = _current.get()
        stack = conn.info.get("_tel_t")
        if tr is not None and stack:
            t = stack.pop()
            with _lock:
                tr.db_queries += 1
                tr.db_ms += (time.perf_counter() - t) * 1000
