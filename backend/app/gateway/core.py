"""The provider gateway (ADR 0007): every outbound call goes through `request()`.

Order of checks for one attempt:
  plan deadline -> circuit breaker (incl. the quota circuit) -> bulkhead -> budget reservation -> send with
  min(endpoint timeout, time left) -> classify the answer -> settle the budget, feed the breaker, record the ledger ->
  retry once, with jitter, if the call is idempotent, the answer is retryable, the retry budget and the deadline allow it.

Anything refused or failed raises a ProviderUnavailable subclass (errors.py), so each caller's own fallback applies.
There are no hedged or duplicate calls to paid APIs: the only second call is a budgeted retry of an idempotent one.
"""
from __future__ import annotations

import json
import logging
import time
from urllib.parse import urlparse

import httpx

from app.core import telemetry
from app.core.config import get_settings
from app.core.errors import ProviderUnavailable
from app.gateway import context as C
from app.gateway import limits
from app.gateway.breaker import breakers
from app.gateway.budget import GROUP_OF, UNITS, budgets
from app.gateway.errors import BudgetExhausted, BulkheadFull, CircuitOpen, DeadlineExceeded, GatewayError, QuotaExhausted
from app.gateway.ledger import ledger
from app.gateway.retry import RETRYABLE_STATUS, backoff, parse_retry_after, retry_budget
from app.gateway.singleflight import key_of, single_flight

log = logging.getLogger("tripai.gateway")
MIN_TIME_LEFT = 1.0  # below this much time left in the plan, a call is not worth starting
SLOT_WAIT = 5.0
# Timeouts in seconds, from the Phase 0/2 measurements (ADR 0007). Exact "provider endpoint" first, then the provider.
TIMEOUTS = {
    "google-places POST /v1/places:autocomplete": 3, "google-places POST /v1/places:searchText": 6,
    "google-places GET /v1/places/{id}": 4, "google-routes POST /directions/v2:computeRoutes": 6,
    "google-routes POST /distanceMatrix/v2:computeRouteMatrix": 6, "google-geocoding": 4, "railradar": 8, "irctc1": 6,
    "openrouter": 15, "open-meteo": 4, "brave": 4, "serper": 4, "tavily": 4,
}
READ_PROVIDERS = {"google-places", "google-routes", "google-geocoding", "brave", "serper", "tavily", "open-meteo", "railradar", "irctc1"}
QUOTA_WORDS = {"railradar": ("quota", "limit"), "irctc1": ("quota",), "google-places": ("quota", "resource_exhausted"),
               "google-routes": ("quota", "resource_exhausted"), "google-geocoding": ("quota", "over_query_limit")}


def _http_client() -> httpx.Client:
    from app.providers import http
    return http.client()


def timeout_for(provider: str, endpoint: str) -> float:
    s = get_settings()
    table = dict(TIMEOUTS)
    if s.gateway_timeouts:
        try:
            table.update({k: float(v) for k, v in json.loads(s.gateway_timeouts).items()})
        except (ValueError, TypeError):
            log.warning("GATEWAY_TIMEOUTS is not valid JSON of seconds; using the defaults")
    return table.get(f"{provider} {endpoint}", table.get(provider, s.http_timeout_seconds))


def price_for(provider: str, endpoint: str, group: str | None, kwargs: dict) -> float:
    """What one call is expected to cost: quota units for rail providers, list-price dollars for the rest."""
    if group is None:
        return 0.0
    if UNITS.get(group) == "calls":
        return 1.0
    if provider == "openrouter":
        return 0.001  # settled to the reported cost
    p = telemetry._prices().get(f"{provider} {endpoint}", 0.0)  # noqa: SLF001 - one price table (ADR 0001)
    if "computeRouteMatrix" in endpoint:
        body = kwargs.get("json") or {}
        p *= max(1, len(body.get("origins", [])) * len(body.get("destinations", [])))
    return p


def _trace(**event) -> None:
    tr = telemetry.current()
    if tr is not None:
        tr.add("gateway", event)


def _skip(kind: str, provider: str, endpoint: str, group: str | None, exc: GatewayError):
    ledger.add(provider, endpoint, **{f"{kind}_skips" if kind != "budget" else "budget_stops": 1})
    C.note_degraded(group or provider, endpoint, exc.reason, provider)
    _trace(provider=provider, endpoint=endpoint, outcome=exc.reason, stage=C.stage())
    raise exc


def request(method: str, url: str, *, provider: str, retries: int | None = None, timeout: float | None = None,
            idempotent: bool | None = None, group: str | None = None, raw: bool = False, **kwargs) -> httpx.Response:
    """Send one call through the gateway. `provider` is the caller's display name (used in messages). `raw`: return 4xx
    answers (and quota 429s) to the caller instead of raising, for providers that read error bodies themselves."""
    s = get_settings()
    if not s.gateway_enabled:  # GATEWAY_ENABLED=false: one plain call, no protections
        return _http_client().request(method, url, timeout=timeout if timeout is not None else 20, **kwargs)
    parsed = urlparse(url)
    prov = telemetry.provider_of(parsed.hostname or provider)
    endpoint = f"{method} {telemetry.endpoint_template(parsed.path)}"
    grp = group or GROUP_OF.get(prov)
    if idempotent is None:
        idempotent = method == "GET" or prov in READ_PROVIDERS
    key = key_of(method, url, kwargs)
    return single_flight.run(key, prov, endpoint, lambda: _send(method, url, provider, prov, endpoint, grp, retries, timeout,
                                                                idempotent, raw, kwargs), enabled=s.gateway_single_flight)


def _send(method, url, display, prov, endpoint, grp, retries, timeout, idempotent, raw, kwargs) -> httpx.Response:
    s = get_settings()
    legacy_retry = not s.gateway_retry_budget  # GATEWAY_RETRY_BUDGET=false: the old rule (retry timeouts, 5xx and 429, any method)
    allowed = (1 if retries is None else min(retries, 1)) if (idempotent or legacy_retry) else 0
    price = price_for(prov, endpoint, grp, kwargs)
    attempt = 0
    last_failure = "unavailable"
    while True:
        rem = C.remaining()
        if rem is not None and rem < MIN_TIME_LEFT:
            _skip("deadline", prov, endpoint, grp, DeadlineExceeded(f"{display}: the plan ran out of time.", provider=prov, endpoint=endpoint))
        token = None
        if s.gateway_breakers:
            try:
                token = breakers.before(prov, endpoint)
            except CircuitOpen as exc:
                _skip("circuit", prov, endpoint, grp, exc)
        lim = limits.limiter_for(grp or prov) if s.gateway_bulkheads else None
        if lim is not None and not lim.acquire(min(SLOT_WAIT, rem - MIN_TIME_LEFT) if rem is not None else SLOT_WAIT):
            breakers.release(prov, endpoint, token)
            _skip("bulkhead", prov, endpoint, grp, BulkheadFull(f"{display} is busy.", provider=prov, endpoint=endpoint))
        ok_for_limiter: bool | None = None
        reserved = 0.0
        try:
            try:
                reserved = budgets.reserve(grp, price, C.priority())
            except BudgetExhausted as exc:
                breakers.release(prov, endpoint, token)
                _skip("budget", prov, endpoint, grp, exc)
            eff = timeout if timeout is not None else timeout_for(prov, endpoint)
            if rem is not None:
                eff = min(eff, max(0.5, rem - 0.2))
            if attempt == 0:
                retry_budget.on_call(prov)
            t0 = time.perf_counter()
            resp: httpx.Response | None = None
            outcome = "ok"
            try:
                resp = _http_client().request(method, url, timeout=eff, **kwargs)
            except httpx.TimeoutException:
                outcome = "timeout"
            except httpx.TransportError:
                outcome = "transport"
            ms = (time.perf_counter() - t0) * 1000
            quota = quota_header = False
            status = resp.status_code if resp is not None else 0
            if resp is not None:
                remaining, limit = ledger.see_headers(prov, resp.headers)
                text = resp.text[:300].lower() if status == 429 else ""
                quota_header = remaining is not None and remaining <= 0
                quota = status == 429 and (any(w in text for w in QUOTA_WORDS.get(prov, ("quota",))) or quota_header)
                if quota:
                    outcome = "quota"
                elif status == 429 or status in RETRYABLE_STATUS or (legacy_retry and status >= 500):
                    outcome = "retryable"
                elif status >= 500:
                    outcome = "server_error"
                elif status in (401, 403):
                    outcome = "auth"
                elif status >= 400:
                    outcome = "client_error"
            # --- ledger, breaker, budget --------------------------------------------------------------------------------------
            good = outcome in ("ok", "client_error")
            ledger.add(prov, endpoint, calls=1, ok=1 if good else 0, errors=0 if good else 1, latency_ms=ms,
                       retries=1 if attempt else 0, quota_hits=1 if quota else 0)
            ledger.outcome(prov, good, ms)
            if s.gateway_breakers:
                breakers.record(prov, endpoint, ok=good, token=token, quota=quota)
                if resp is not None and good and quota_header:  # this answer was fine, but it was the last one: stop asking
                    breakers.set_quota(prov, "remaining quota header is 0")
            if outcome in ("timeout",):  # the provider may have billed it: keep the reservation
                actual = reserved
            elif good:
                actual = reserved
            else:
                actual = 0.0  # a refusal or a 5xx is not billed
            budgets.settle(grp, reserved, actual)
            if UNITS.get(grp or "") == "usd":
                ledger.add(prov, endpoint, usd=actual)
                ledger.add_user(C.user_id(), actual)
            ok_for_limiter = True if good else (False if outcome in ("timeout", "retryable", "quota") else None)
            _trace(provider=prov, endpoint=endpoint, outcome=outcome, status=status, ms=round(ms), attempt=attempt, stage=C.stage())
        finally:
            if lim is not None:
                lim.release(ok_for_limiter)

        if outcome == "ok":
            return resp
        if outcome == "client_error":
            if raw:
                return resp
            log.warning("%s %s -> %s %s", display, url.split("?")[0], status, resp.text[:300])
            raise ProviderUnavailable(f"{display} rejected the request ({status}).")
        if outcome == "auth":
            if raw:
                return resp
            log.warning("%s rejected our credentials (%s)", display, status)
            raise ProviderUnavailable(f"{display} rejected the request ({status}).")
        if outcome == "quota":
            C.note_degraded(grp or prov, endpoint, "quota", prov)
            if raw:
                return resp  # the caller reads the body and raises its own message
            raise QuotaExhausted(f"{display} quota is used up.", provider=prov, endpoint=endpoint,
                                 retry_at=_retry_at(breakers.open_until(prov)))
        # a failure: timeout, transport error, 5xx, 429 without quota wording
        last_failure = outcome
        retry_after = parse_retry_after(resp.headers.get("retry-after")) if resp is not None else None
        can_retry = attempt < allowed and outcome in ("timeout", "transport", "retryable") and (
            legacy_retry or retry_budget.take(prov))
        if can_retry:
            delay = backoff(attempt, retry_after)
            left = C.remaining()
            if left is not None and left < delay + MIN_TIME_LEFT:
                can_retry = False  # no time for another try: the fallback is better than a late answer
            if can_retry and breakers.is_open(prov) and s.gateway_breakers:
                can_retry = False
            if can_retry:
                time.sleep(delay) if not legacy_retry else time.sleep(0.6 * (attempt + 1))
                attempt += 1
                continue
        log.warning("%s unavailable (%s)", display, last_failure)
        C.note_degraded(grp or prov, endpoint, "failed", prov)
        raise ProviderUnavailable(f"{display} is temporarily unavailable.")


def _retry_at(epoch: float | None):
    from datetime import UTC, datetime
    return datetime.fromtimestamp(epoch, UTC) if epoch else None


def adjust_cost(provider_or_group: str, delta_usd: float) -> None:
    """Add or take back dollars after the fact (the LLM reports its real cost in the answer)."""
    group = GROUP_OF.get(provider_or_group, provider_or_group)
    budgets.settle(group, 0.0, delta_usd)
    ledger.add_user(C.user_id(), delta_usd)
