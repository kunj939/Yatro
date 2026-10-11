"""Per-plan and per-call context for the gateway (ADR 0007): the plan's deadline, the stage it is in, who is paying,
foreground or background, and which parts of the plan ended up degraded. No imports from the rest of the app, so
telemetry and the planner can use it without cycles."""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

log = logging.getLogger("tripai.gateway")

now = time.time  # wall clock; tests replace it
mono = time.monotonic


@dataclass
class PlanScope:
    deadline: float | None = None  # monotonic
    degraded: list[dict] = field(default_factory=list)
    deadline_stage: str | None = None  # the stage that first met the deadline


_scope: ContextVar[PlanScope | None] = ContextVar("gateway_scope", default=None)
_stage: ContextVar[str | None] = ContextVar("gateway_stage", default=None)
_priority: ContextVar[str] = ContextVar("gateway_priority", default="foreground")
_user: ContextVar[str | None] = ContextVar("gateway_user", default=None)


@contextmanager
def plan_scope(seconds: float | None, user_id=None):
    """Everything inside this block (including pool threads, via ContextThreadPool) shares one deadline."""
    scope = PlanScope(deadline=mono() + seconds if seconds and seconds > 0 else None)
    t1, t2 = _scope.set(scope), _user.set(str(user_id) if user_id else None)
    try:
        yield scope
    finally:
        _scope.reset(t1)
        _user.reset(t2)


@contextmanager
def background():
    """Calls made inside are background work: they may use only a share of a day's budget (BACKGROUND_BUDGET_SHARE)."""
    token = _priority.set("background")
    try:
        yield
    finally:
        _priority.reset(token)


def scope() -> PlanScope | None:
    return _scope.get()


def set_stage(key: str | None) -> None:
    _stage.set(key)


def stage() -> str | None:
    return _stage.get()


def priority() -> str:
    return _priority.get()


def user_id() -> str | None:
    return _user.get()


def remaining() -> float | None:
    """Seconds left before the plan's deadline, or None when there is no deadline."""
    s = _scope.get()
    return None if s is None or s.deadline is None else s.deadline - mono()


def note_degraded(group: str, endpoint: str, reason: str, provider: str | None = None) -> None:
    """A call that did not happen (or failed): remembered so the plan can say which parts are degraded."""
    s = _scope.get()
    if s is None:
        return
    st = stage()
    s.degraded.append({"group": group, "endpoint": endpoint, "reason": reason, "stage": st, "provider": provider})
    if reason == "deadline" and s.deadline_stage is None:
        s.deadline_stage = st or "unknown"
        log.warning("plan deadline reached during stage %r (first skipped call: %s %s)", st, group, endpoint)
