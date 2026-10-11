"""Health-scored routing of live sources (ADR 0007).

The score (0 to 1) combines success rate, latency and cost over a provider's last 50 calls. It is computed and exported either
way; GATEWAY_HEALTH_ROUTING (off) decides whether it reorders sources. With two live rail sources and a breaker that already
skips a failing one, the default order is unchanged until the scores say otherwise."""
from __future__ import annotations

from app.core.config import get_settings
from app.gateway.ledger import ledger

LATENCY_GOOD_MS, LATENCY_BAD_MS = 500.0, 5000.0


def health_score(provider: str) -> float:
    calls = list(ledger.recent.get(provider, ()))
    if not calls:
        return 1.0  # nothing known: don't penalise
    ok_rate = sum(1 for ok, _ in calls if ok) / len(calls)
    lat = sorted(ms for _, ms in calls)[len(calls) // 2]
    lat_score = 1.0 if lat <= LATENCY_GOOD_MS else max(0.0, 1 - (lat - LATENCY_GOOD_MS) / (LATENCY_BAD_MS - LATENCY_GOOD_MS))
    return round(0.7 * ok_rate + 0.3 * lat_score, 4)


def order(providers: list) -> list:
    """The sources in the order to try them. Unchanged unless GATEWAY_HEALTH_ROUTING is on (then best score first; ties and
    equal scores keep the configured order)."""
    if not get_settings().gateway_health_routing:
        return list(providers)
    return sorted(providers, key=lambda p: -health_score(getattr(p, "name", "")))
