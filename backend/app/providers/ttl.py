"""One table of response-cache lifetimes (ADR 0008). The values are exactly the ones that used to be literals in six files.

Overrides: CACHE_TTL_OVERRIDES='{"google-routes": 3600}' (seconds). Anything Google is capped at 7 days, whatever the override
says: place content may only be a temporary cache, and coordinates live at most 30 days (I6, ADR 0005).
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

from app.core.config import get_settings

log = logging.getLogger("tripai.cache")

H, D = 3600, 86400
DEFAULTS: dict[str, int] = {
    "google-routes": 6 * H, "google-matrix": 24 * H, "google-places-search": 24 * H, "google-autocomplete": 24 * H,
    "google-place-details": 3 * D, "google-reverse": 7 * D, "google-transit": 12 * H,
    "open-meteo": 3 * H, "search": 12 * H,
    "irctc1-station": 1 * D, "irctc1-between": 12 * H, "railradar": 12 * H,
}
GOOGLE_MAX = 7 * D


def seconds(name: str) -> int:
    s = DEFAULTS[name]
    raw = get_settings().cache_ttl_overrides
    if raw:
        try:
            s = int(json.loads(raw).get(name, s))
        except (ValueError, TypeError, AttributeError):
            log.warning("CACHE_TTL_OVERRIDES is not valid JSON of seconds; using the defaults")
    if name.startswith("google-"):
        s = min(s, GOOGLE_MAX)
    return max(0, s)


def get(name: str) -> timedelta:
    return timedelta(seconds=seconds(name))
