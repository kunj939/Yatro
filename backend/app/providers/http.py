"""Shared HTTP client; every call goes through the provider gateway (app/gateway, ADR 0007)."""
from __future__ import annotations

import logging
import time

import httpx

from app.core.config import get_settings
from app.core.errors import ProviderUnavailable
from app.core.telemetry import TracingTransport

log = logging.getLogger("tripai.http.out")
_client: httpx.Client | None = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        s = get_settings()
        # TracingTransport records every attempt while a plan is being measured; otherwise a plain transport
        _client = httpx.Client(transport=TracingTransport(), timeout=httpx.Timeout(s.http_timeout_seconds, connect=5.0),
                               headers={"User-Agent": f"{s.app_name}/0.1"})
    return _client


def request(method: str, url: str, *, provider: str, retries: int = 1, timeout: float | None = None,
            **kwargs) -> httpx.Response:
    """Every provider call. With the gateway on (the default) it adds deadlines, retries under a budget, circuit breakers,
    bulkheads and daily budgets (ADR 0007); GATEWAY_ENABLED=false is the plain behaviour below."""
    if get_settings().gateway_enabled:
        from app.gateway import core
        return core.request(method, url, provider=provider, retries=retries, timeout=timeout, **kwargs)
    kwargs.pop("group", None), kwargs.pop("idempotent", None), kwargs.pop("raw", None)
    return _legacy_request(method, url, provider=provider, retries=retries, timeout=timeout, **kwargs)


def _legacy_request(method: str, url: str, *, provider: str, retries: int = 1, timeout: float | None = None,
                    **kwargs) -> httpx.Response:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = client().request(method, url, timeout=timeout, **kwargs)
            if resp.status_code >= 500 or resp.status_code == 429:
                raise httpx.HTTPStatusError(f"{resp.status_code}", request=resp.request, response=resp)
            if resp.status_code >= 400:
                log.warning("%s %s -> %s %s", provider, url.split("?")[0], resp.status_code, resp.text[:300])
                raise ProviderUnavailable(f"{provider} rejected the request ({resp.status_code}).")
            return resp
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
    log.warning("%s unavailable: %s", provider, last)
    raise ProviderUnavailable(f"{provider} is temporarily unavailable.")
