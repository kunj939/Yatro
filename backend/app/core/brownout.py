"""Brownout levels (ADR 0008): ways to do less work when the service is busy, applied in order and always labelled.

    L1 template text instead of AI narration      L3 smaller optimiser candidate sets
    L2 no alternatives                            L4 rail limited to saved timetables (and station guidance)

A level changes how much effort a plan takes, never a fact: the train, fare and Google-content gates sit after it (I1, I6).
`BROWNOUT_LEVEL` is read from the environment on every plan, so an operator can change it without a restart. One plan
sees one level from start to end (`plan_level`). `BROWNOUT_AUTO` is an interface: `suggest()` is a pure function of queue
wait and depth, exported with the runner's numbers; nothing applies it until a measurement says it should.
"""
from __future__ import annotations

import contextlib
import contextvars
import os

from app.core import killswitch, runtime
from app.core.config import get_settings

# The wording the user sees, next to the part of the plan each level changes (and in Plan notes).
LABELS = {
    1: "Trip description simplified to keep things fast",
    2: "Alternatives aren't available right now",
    3: "Fewer options were compared to keep things fast",
    4: "Train times are from saved timetables only right now",
}
PART = {1: "description", 2: "alternatives", 3: "options", 4: "rail"}

_plan_level: contextvars.ContextVar[int | None] = contextvars.ContextVar("brownout_level", default=None)


def base_level() -> int:
    """The level an operator chose: the admin console first (live, 5 s), then the environment (read on every plan), then the setting."""
    chosen = runtime.get("brownout_level")
    if isinstance(chosen, int) and not isinstance(chosen, bool):
        return max(0, min(4, chosen))
    raw = os.environ.get("BROWNOUT_LEVEL")
    if raw is not None and raw.strip() != "":
        try:
            return max(0, min(4, int(raw)))
        except ValueError:
            pass
    return get_settings().brownout_level


def configured() -> int:
    """The level in force right now. Switching off AI descriptions (kill switch `ai_narration`) is level 1 at least."""
    lv = base_level()
    return max(lv, 1) if killswitch.killed("ai_narration") else lv


def level() -> int:
    v = _plan_level.get()
    return configured() if v is None else v


def active(n: int) -> bool:
    return level() >= n


@contextlib.contextmanager
def plan_scope():
    """Fix the level for one plan, so its stages agree with each other."""
    token = _plan_level.set(configured())
    try:
        yield level()
    finally:
        _plan_level.reset(token)


def labels() -> dict[str, str]:
    """{part: text} for every level that is on, for the plan's own sections."""
    return {PART[n]: LABELS[n] for n in range(1, level() + 1)}


def snapshot() -> dict:
    """What the plan carries: the level and the label for each affected part (empty at level 0)."""
    lv = level()
    return {"level": lv, "labels": labels()} if lv else {}


def suggest(wait_p95_seconds: float | None, waiting: int, limit: int, hard_limit: int) -> int:
    """The level the numbers call for. Pure; the thresholds are proposals, shown but not applied (BROWNOUT_AUTO=false)."""
    if hard_limit and waiting >= hard_limit:
        return 4
    if limit and waiting >= limit:
        return 3 if (wait_p95_seconds or 0) > 60 else 2
    if (wait_p95_seconds or 0) > 45:
        return 1
    return 0
