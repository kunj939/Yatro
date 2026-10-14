"""Everything the product asks of a language model, behind the provider-neutral LLMProvider interface.

The LLM only (1) writes human-friendly text from verified structured data and (2) interprets free-text
requests into a structured change set. It never produces an authoritative number: a fact guard rejects any
price, distance, duration or clock time in its output that is not present in the supplied data, and the
deterministic template text is used instead.
"""
from __future__ import annotations

import json
import logging
import re
import time
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core import telemetry
from app.core.errors import ProviderUnavailable
from app.models.user import LLMUsage
from app.providers.base import LLMProvider
from app.providers.types import LLMMessage, LLMResult

log = logging.getLogger("tripai.llm")

FACT_RULE = ("Use only the supplied verified data for factual values. Do not invent prices, distances, opening hours "
             "or travel times.")

NARRATE_SYSTEM = f"""You are the writer for Yaitro, a budget-aware travel planner.
You receive a trip that has ALREADY been planned by deterministic software from verified data.
Your job is only to phrase it for a traveller: a short title, a 2–3 sentence summary, one "why" sentence per item,
and up to 4 practical tips.
{FACT_RULE}
Never write a price, fare, cost or any amount of money: the app shows every amount itself, labelled. Never change, round
differently or recompute any other number; copy values exactly as given (e.g. "2.1 km", "12 min", "09:30"). If a value
is marked uncertain or estimated, say so. Do not add places, restaurants or transport that are not
in the data. Keep a warm, confident, concise tone. No emojis. Respond with JSON only."""

NARRATE_SCHEMA = {
    "title": "trip_narration", "type": "object", "additionalProperties": False,
    "required": ["title", "summary", "items", "tips"],
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "items": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                             "required": ["item_id", "why"],
                                             "properties": {"item_id": {"type": "string"}, "why": {"type": "string"}}}},
        "tips": {"type": "array", "items": {"type": "string"}},
    },
}

# --------------------------------------------------------------------------------------------------------
# Usage accounting
# --------------------------------------------------------------------------------------------------------

def log_usage(db: Session | None, llm: LLMProvider, purpose: str, *, user_id=None, trip_id=None,
              result: LLMResult | None = None, error: str | None = None, violations: int = 0,
              latency_ms: int = 0) -> None:
    if db is None or not llm.live:
        return
    try:
        if result:
            telemetry.record_llm(result.prompt_tokens + result.completion_tokens, result.cost_usd)
        db.add(LLMUsage(user_id=user_id, trip_id=trip_id, provider=llm.name, model=result.model if result else llm.model,
                        purpose=purpose, prompt_tokens=result.prompt_tokens if result else 0,
                        completion_tokens=result.completion_tokens if result else 0,
                        total_tokens=(result.prompt_tokens + result.completion_tokens) if result else 0,
                        cost_usd=Decimal(str(result.cost_usd)) if result and result.cost_usd is not None else None,
                        latency_ms=result.latency_ms if result else latency_ms, success=error is None,
                        guard_violations=violations, error=(error or "")[:500] or None))
        db.flush()
    except Exception:  # accounting must never break planning
        log.warning("could not record llm usage", exc_info=True)


# --------------------------------------------------------------------------------------------------------
# Fact guard
# --------------------------------------------------------------------------------------------------------

_NUM = r"\d[\d,]*(?:\.\d+)?"
_PATTERNS = [
    ("km", re.compile(r"(" + _NUM + r")\s?km\b", re.IGNORECASE)),
    ("min", re.compile(r"(" + _NUM + r")\s?(?:min|mins|minutes)\b", re.IGNORECASE)),
    ("hours", re.compile(r"(" + _NUM + r")\s?(?:h|hr|hrs|hours?)\b", re.IGNORECASE)),
    ("clock", re.compile(r"\b(\d{1,2}):(\d{2})\s?(am|pm)?\b", re.IGNORECASE)),
    ("clock", re.compile(r"(?<![:\d])(\d{1,2})\s?(am|pm)\b", re.IGNORECASE)),
]


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def allowed_facts(payload) -> set:
    """Every number and clock time present in the verified payload, plus harmless derived forms."""
    text = json.dumps(payload, ensure_ascii=False, default=str)
    allowed: set = set()
    for m in re.finditer(r"-?\d[\d,]*(?:\.\d+)?", text):
        try:
            v = abs(_num(m.group(0)))
        except ValueError:
            continue
        allowed |= {("n", round(v, 2)), ("n", round(v)), ("n", round(v, 1))}
        allowed.add(("n", round(v / 1000, 1)))  # metres -> km
        allowed.add(("n", round(v / 60, 1)))  # minutes -> hours
        allowed.add(("n", round(v / 60)))
    for m in re.finditer(r"\b(\d{1,2}):(\d{2})\b", text):
        allowed.add(("t", int(m.group(1)) % 24, int(m.group(2))))
    for m in TRAIN_WORDS.finditer(text):  # train names the verified data actually contains (I1)
        allowed.add(("w", m.group(0).lower()))
    return allowed


# A train type used as a name ("Shatabdi", "Vande Bharat") may only appear if the verified data has it (I1: AI never
# originates a train name); a bare 4-6 digit number is checked like any other number, so an invented train number fails.
TRAIN_WORDS = re.compile(r"\b(?:express|shatabdi|jan shatabdi|rajdhani|vande bharat|duronto|garib rath|mail|humsafar|"
                         r"tejas|gatimaan|superfast|double decker)\b", re.IGNORECASE)


# I1 (amended, ADR 0004): AI may never produce or modify a fare, so AI text may contain no amount of money at all, even one
# copied from the data. Currency symbols and codes before a number, or currency words after it.
MONEY = re.compile(r"(?:₹|\$|€|£|¥|₩|₫|฿|\b(?:rs|inr|usd|eur|gbp|aed|sgd|thb|jpy|aud|cad)\b\.?)\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:k|lakh|crore)\b)?"
                   r"|\b\d[\d,]*(?:\.\d+)?\s?(?:k\s)?(?:rupees?|rs\b|inr\b|dollars?|euros?|pounds?|dirhams?|baht|yen)\b|\b\d[\d,]*/-",
                   re.IGNORECASE)


def violations_in(text: str, allowed: set) -> list[str]:
    bad = []
    bad += [m.group(0).strip() for m in MONEY.finditer(text or "")]  # I1: AI never writes an amount, even a copied one
    for m in re.finditer(r"(?<![\d,.])\d{4,6}(?![\d,.])", text or ""):  # a train number the data doesn't have
        if not any(("n", round(float(m.group(0)), p)) in allowed for p in (0, 1, 2)):
            bad.append(m.group(0))
    for m in TRAIN_WORDS.finditer(text or ""):
        if ("w", m.group(0).lower()) not in allowed:
            bad.append(m.group(0))
    for kind, rx in _PATTERNS:
        for m in rx.finditer(text or ""):
            if kind == "clock":
                g = m.groups()
                if len(g) == 3:
                    h, mi, ap = int(g[0]), int(g[1]), (g[2] or "").lower()
                else:
                    h, mi, ap = int(g[0]), 0, g[1].lower()
                if ap == "pm" and h < 12:
                    h += 12
                if ap == "am" and h == 12:
                    h = 0
                if ("t", h % 24, mi) not in allowed:
                    bad.append(m.group(0))
            else:
                v = _num(m.group(1))
                if not any(("n", round(v, p)) in allowed for p in (0, 1, 2)):
                    bad.append(m.group(0))
    return bad


# --------------------------------------------------------------------------------------------------------
# Narration
# --------------------------------------------------------------------------------------------------------

def narrate(llm: LLMProvider, payload: dict, template: dict, *, db: Session | None = None, user_id=None,
            trip_id=None) -> tuple[dict, str, int]:
    """Returns (narration, status, guard_violations). status: ai | partial | template | fallback."""
    if not llm.live:
        return template, "template", 0
    messages = [LLMMessage("system", NARRATE_SYSTEM),
                LLMMessage("user", "Verified trip data (JSON):\n" + json.dumps(payload, ensure_ascii=False, default=str))]
    t0 = time.perf_counter()
    try:
        result = llm.complete(messages, json_schema=NARRATE_SCHEMA, temperature=0.4)
    except ProviderUnavailable as exc:
        log_usage(db, llm, "narration", user_id=user_id, trip_id=trip_id, error=str(exc),
                  latency_ms=int((time.perf_counter() - t0) * 1000))
        return template, "fallback", 0
    data = result.data or {}
    allowed = allowed_facts(payload)
    out = dict(template)
    violations = 0
    for field in ("title", "summary"):
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            bad = violations_in(value, allowed)
            violations += len(bad)
            if not bad:
                out[field] = value.strip()[:600 if field == "summary" else 120]
    whys = dict(template.get("whys", {}))
    for row in data.get("items") or []:
        if not isinstance(row, dict):
            continue
        iid, why = str(row.get("item_id", "")), row.get("why")
        if iid in whys and isinstance(why, str) and why.strip():
            bad = violations_in(why, allowed)
            violations += len(bad)
            if not bad:
                whys[iid] = why.strip()[:400]
    out["whys"] = whys
    tips = []
    for tip in (data.get("tips") or [])[:4]:
        if isinstance(tip, str) and tip.strip():
            bad = violations_in(tip, allowed)
            violations += len(bad)
            if not bad:
                tips.append(tip.strip()[:240])
    out["tips"] = tips or template.get("tips", [])
    log_usage(db, llm, "narration", user_id=user_id, trip_id=trip_id, result=result, violations=violations)
    status = "ai" if violations == 0 else "partial"
    if violations:
        log.info("fact guard replaced %d LLM values with template text", violations)
    return out, status, violations
