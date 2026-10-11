"""Kill switches (hotfix 2026-10-07; the admin console, ADR 0010, is where they are flipped).

A switch that is on stops the thing it names, and the caller answers calmly (never with a 5xx). Order of precedence for
`killed("sms")`:

  1. an admin's choice in the console (`runtime_settings`, key `switch:sms`), live within 5 seconds, no restart;
  2. the environment, `KILL_SMS=true`, read LIVE on every check (so it works the moment it is set where the process can see it);
  3. the setting `kill_sms` in the app's configuration.

Clearing the admin's choice in the console hands the switch back to 2 and 3. Names are lower-case; the environment variable is
upper-cased.
"""
from __future__ import annotations

import os

from app.core import runtime
from app.core.config import get_settings

_TRUE = {"1", "true", "yes", "on"}


def source(name: str) -> str:
    """Where the current answer for this switch comes from: "admin", "env" or "default"."""
    if runtime.get(f"switch:{name.lower()}") is not None:
        return "admin"
    raw = os.environ.get(f"KILL_{name.upper()}")
    if raw is not None and raw.strip() != "":
        return "env"
    return "default"


def killed(name: str) -> bool:
    chosen = runtime.get(f"switch:{name.lower()}")
    if chosen is not None:
        return bool(chosen)
    raw = os.environ.get(f"KILL_{name.upper()}")
    if raw is not None and raw.strip() != "":
        return raw.strip().lower() in _TRUE
    return bool(getattr(get_settings(), f"kill_{name.lower()}", False))
