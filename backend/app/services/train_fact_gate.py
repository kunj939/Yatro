"""The final gate for train facts (ADR 0002, invariant I1).

Runs in the response serializer (trip_service.item_out / alt_out) on every itinerary item and alternative. Whatever
the planner wrote, a train whose source is not a timetable (live API, saved timetable ≤45 days, official dataset, or
demo sample data) leaves the API with:

- no name and no number: the title is rebuilt as "Train to <station> (timing estimated)";
- no clock times: `time`/`end_time` are null, `start_at`/`end_at` keep only the date, and a part of day
  (`meta.when`) is given instead;
- no HH:MM anywhere in its subtitle, reasons or explanation.

Independent of `provenance.TrainService`: if a provider forgets to stamp an option, the default is to redact.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

from app.core.provenance import SourceKind, freshness, is_train_fact_source, part_of_day

_CLOCK = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b")


def _is_untimetabled_train(meta: dict) -> bool:
    return meta.get("mode") == "train" and not is_train_fact_source(meta.get("source_kind"), meta.get("observed_at"))


def _scrub(text: str | None, part: str) -> str | None:
    return _CLOCK.sub(part.lower(), text) if text else text


def redact_item(out: dict) -> dict:
    meta = out.get("meta") or {}
    if out.get("kind") != "transport" or not _is_untimetabled_train(meta):
        return out
    part = part_of_day(datetime.fromisoformat(out["start_at"]))
    station = meta.get("to_name") or "your destination"
    return {
        **out,
        "title": f"Train to {station} (timing estimated)",
        "subtitle": _scrub(out.get("subtitle"), part),
        "time": None, "end_time": None,
        "start_at": out["start_at"][:10], "end_at": out["end_at"][:10],
        "explanation": _scrub(out.get("explanation"), part),
        "reasons": [{**r, "text": _scrub(r.get("text"), part)} for r in out.get("reasons") or []],
        "meta": {**meta, "number": None, "legs": None, "when": part},
    }


def redact_alternative(out: dict) -> dict:
    meta = out.get("meta") or {}
    if out.get("kind") != "transport" or not _is_untimetabled_train(meta):
        return out
    dep = meta.get("depart_at")
    part = part_of_day(datetime.fromisoformat(dep)) if dep else "Day"
    return {
        **out,
        "title": f"Train, {part.lower()} (timing estimated)",
        "reason": _scrub(out.get("reason"), part),
        "meta": {k: v for k, v in meta.items() if k not in ("depart_at", "arrive_at", "number")} | {"when": part},
    }


VERIFIED_FARE = ("verified_official", "verified_provider")
FARE_VERIFIED, FARE_ESTIMATED = "Verified fare", "Estimated fare"
FARE_APPROX = "Approx. train fare for this distance"
FARE_DOWNGRADED = "This fare was confirmed for a specific train that can no longer be shown, so it is now an estimate."


def fare_item(out: dict) -> dict:
    """I1 (amended 2026-10-06, ADR 0004): a fare is either a real provider fare, labelled verified, or an estimate from
    the deterministic fare model, labelled "Estimated fare". The label comes from the fare's own state here, at the
    last step, so nothing upstream can style an estimate as verified. A verified fare is downgraded only with a note
    saying so: when its train was redacted (no timetable fact may be shown, so neither may "this train's verified fare")."""
    if out.get("kind") != "transport" or not out.get("cost"):
        return out
    meta = dict(out.get("meta") or {})
    state = out.get("cost_state")
    no_train = meta.get("mode") == "train" and out.get("time") is None  # station guidance, or redacted by the gate
    for k in ("fare_label", "fare_note"):  # only this gate writes these
        meta.pop(k, None)
    if state in VERIFIED_FARE and no_train:
        state, meta["fare_note"] = "estimated", FARE_DOWNGRADED
    if state in VERIFIED_FARE:
        meta["fare_label"] = FARE_VERIFIED
    elif state == "demo":
        meta["fare_label"] = "Sample fare"
    else:
        meta["fare_label"] = FARE_APPROX if no_train else FARE_ESTIMATED
    return {**out, "cost_state": state, "meta": meta}


STALE_WARNING = "Timings may have changed — confirm on IRCTC before booking."


def label_item(out: dict, now: datetime | None = None) -> dict:
    """A train shown from a saved timetable says how old that timetable is, worked out when the trip is viewed (I2):
    "Saved timetable · checked 12 days ago", plus the stale warning from day 22 (ADR 0003)."""
    meta = out.get("meta") or {}
    if out.get("kind") != "transport" or meta.get("mode") != "train" or meta.get("source_kind") != "saved_timetable":
        return out
    try:
        observed = datetime.fromisoformat(meta["observed_at"])
    except (KeyError, TypeError, ValueError):
        return out
    now = now or datetime.now(UTC)
    days = max(0, (now - (observed if observed.tzinfo else observed.replace(tzinfo=UTC))).days)
    fresh = freshness(SourceKind.SAVED_TIMETABLE, observed, now)
    ago = "today" if days == 0 else ("yesterday" if days == 1 else f"{days} days ago")
    extra = {"freshness": fresh, "provenance_label": f"Saved timetable · checked {ago}"}
    if fresh == "stale":
        extra["provenance_warning"] = STALE_WARNING
    return {**out, "meta": {**meta, **extra}}
