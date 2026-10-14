"""The trip chat (ADR 0016): one conversation per trip, about that trip only.

Each message is either a change ("add one more day", "budget 8000", "skip the fort") or a question ("when is my
train?", "kitna bacha hai?"). A change is handed back to the client, which sends it through the normal re-plan (the
same intent reader and engine as before, so nothing new can change a plan). A question is answered from the plan
itself: by the model when one is configured, told to use only the trip's data, and by plain rules when not.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date

from sqlalchemy.orm import Session

from app.core.errors import ProviderUnavailable
from app.models.trip import Trip
from app.providers.registry import Providers
from app.providers.types import LLMMessage
from app.services import intent as intent_service
from app.services.llm_service import log_usage
from app.utils.money import fmt_money

log = logging.getLogger("tripai.chat")

QUESTION = re.compile(
    r"\?\s*$|^\s*(what|when|where|which|who|how|why|is|are|do|does|did|can i|should|tell me|show me|"
    r"kya|kab|kahan|kaha|kaun|kitna|kitne|kitni|kaise|kyu|kyun|kyon|batao|bata do|bata)\b", re.I)

CHAT_SYSTEM = """You are the Yaitro assistant for ONE planned trip. The trip is given to you as data.

Decide what the traveller wants:
- action "change": they want something in the plan changed (budget, dates, number of days, start or end time,
  places to add or skip, pace, food, stay, transport, number of people). Leave "answer" empty: the planner does it.
- action "answer": anything else. Answer in at most 4 short sentences, using ONLY the trip data. Quote times,
  places and prices exactly as the data gives them. If the data doesn't say, say so plainly and suggest what they
  could ask instead. Never invent trains, timings, prices, phone numbers or bookings.
If the message is not about this trip (general knowledge, other trips, coding, news), answer that you can only
help with this trip, in one friendly sentence.
Reply in the traveller's own style: English, Hindi, or Hinglish written in Latin letters."""

CHAT_SCHEMA = {
    "title": "trip_chat", "type": "object", "additionalProperties": False, "required": ["action", "answer"],
    "properties": {"action": {"type": "string", "enum": ["answer", "change"]}, "answer": {"type": "string"}},
}

ONLY_THIS_TRIP = ("I can only help with this trip: ask me things like “when is my train?”, “what's on day 2?”, "
                  "“how much is left?”, or tell me a change like “add one more day”.")


def _days(trip: Trip) -> list[dict]:
    from app.services import trip_service as TS  # the trip page's own gated output (I1, I6)
    it = trip.current_itinerary
    return TS.itinerary_out(it, trip)["days"] if it else []


def trip_brief(trip: Trip, limit: int = 9000) -> str:
    """The plan as compact text for the model: one line per stop, money, stay, weather, notes."""
    it = trip.current_itinerary
    money = lambda v: fmt_money(v, trip.currency)  # noqa: E731
    lines = [f"Trip: {trip.title}. From {trip.origin_name} to {trip.destination_name}, {trip.start_date:%a %d %b %Y} to "
             f"{trip.end_date:%a %d %b %Y}, {trip.travellers} traveller(s). Budget {money(trip.budget)}."]
    if it:
        lines.append(f"Planned cost {money(it.total_cost)}, left {money(it.remaining)}. Prices are for the whole group.")
    for n, d in enumerate(_days(trip)):
        stops = []
        for i in d["items"]:
            if i["kind"] == "free":
                continue
            cost = (i.get("cost") or 0) + (i.get("travel_cost") or 0)
            when = i.get("time")
            bits = [i["title"]] if when and i["title"].startswith(when) else [when or "time to be confirmed", i["title"]]
            if i.get("opening_time") and i.get("closing_time"):
                bits.append(f"open {i['opening_time']}-{i['closing_time']}")
            if cost:
                bits.append(money(cost))
            stops.append(" ".join(bits))
        lines.append(f"Day {n + 1} ({d['date']}): " + "; ".join(stops))
    if it and it.weather:
        lines.append(f"Weather: {it.weather.get('summary')}")
    for w in (it.warnings or [])[:4] if it else []:
        lines.append(f"Note: {w}")
    for tip in (it.tips or [])[:4] if it else []:
        lines.append(f"Tip: {tip}")
    text = "\n".join(lines)
    return text if len(text) <= limit else text[:limit] + "\n(the rest of the plan is left out)"


def respond(db: Session, providers: Providers, user, trip: Trip, message: str, history: list[dict]) -> dict:
    from app.services import trip_service as TS
    message = message.strip()[:1000]
    ctx, candidates = TS.intent_inputs(trip)
    rules = intent_service.rule_intent(message, candidates, ctx)
    if rules.has_change() and not QUESTION.search(message):
        return {"action": "change"}
    llm = providers.llm
    if llm.live:
        convo = [LLMMessage("system", CHAT_SYSTEM), LLMMessage("system", "TRIP DATA:\n" + trip_brief(trip))]
        for h in history[-6:]:
            role = "assistant" if h.get("role") == "assistant" else "user"
            convo.append(LLMMessage(role, str(h.get("text", ""))[:600]))
        convo.append(LLMMessage("user", message))
        t0 = time.perf_counter()
        try:
            result = llm.complete(convo, json_schema=CHAT_SCHEMA, temperature=0.2, max_tokens=350)
            log_usage(db, llm, "chat", user_id=user.id, trip_id=trip.id, result=result)
            data = result.data or {}
            if data.get("action") == "change":
                return {"action": "change"}
            if data.get("answer"):
                return {"action": "answer", "text": data["answer"].strip(), "by": "ai"}
        except ProviderUnavailable as exc:
            log_usage(db, llm, "chat", user_id=user.id, trip_id=trip.id, error=str(exc),
                      latency_ms=int((time.perf_counter() - t0) * 1000))
        except Exception:  # noqa: BLE001 - a model problem falls back to the rules, never to an error
            log.warning("chat model call failed", exc_info=True)
    # No model: a question the rules can answer ("when is my train?" mentions a train but asks, not changes) is
    # answered; anything else the rules read as a change goes to the planner.
    answer = rule_answer(trip, message)
    if rules.has_change() and not (QUESTION.search(message) and answer != ONLY_THIS_TRIP):
        return {"action": "change"}
    return {"action": "answer", "text": answer, "by": "rules"}


# --- answers without a model ------------------------------------------------------------------------------------------
_DAY = re.compile(r"\b(?:day|din)\s*(\d{1,2})\b|\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:day|din)\b", re.I)


def rule_answer(trip: Trip, message: str) -> str:
    m = message.lower()
    days = _days(trip)
    it = trip.current_itinerary
    money = lambda v: fmt_money(v, trip.currency)  # noqa: E731
    if not it or not days:
        return "This trip doesn't have a plan yet. Plan it first, then ask me anything about it."

    def line(i: dict) -> str:
        when = i.get("time")
        title = i["title"]
        if when and title.startswith(when):  # a ticket's title already starts with its time
            return title
        return f"{when or 'time to be confirmed'} {title}"

    def nice(iso: str) -> str:
        return date.fromisoformat(iso).strftime("%a %d %b").replace(" 0", " ")

    hit = _DAY.search(m)
    if hit:
        n = int(hit.group(1) or hit.group(2))
        if 1 <= n <= len(days):
            d = days[n - 1]
            stops = [line(i) for i in d["items"] if i["kind"] in ("transport", "activity", "meal", "sunset", "stay")]
            cost = sum((i.get("cost") or 0) + (i.get("travel_cost") or 0) for i in d["items"])
            return f"Day {n} ({nice(d['date'])}): " + "; ".join(stops[:8]) + f". About {money(cost)} that day."
        return f"This trip has {len(days)} days."
    if re.search(r"train|flight|bus|depart|leave|nikal|ticket|station|airport|gaadi|gadi", m):
        legs = [f"Day {n + 1}: {line(i)}" for n, d in enumerate(days) for i in d["items"]
                if i["kind"] == "transport" and not (i.get("meta") or {}).get("transfer")]
        return ("Your journeys: " + "; ".join(legs[:6]) + ".") if legs else "There's no train, bus or flight in this plan."
    if re.search(r"hotel|stay|room|check.?in|ruk|rukna|kamra", m):
        stays = [f"Day {n + 1}: {line(i)}" for n, d in enumerate(days) for i in d["items"] if i["kind"] == "stay"]
        return ("Where you stay: " + "; ".join(dict.fromkeys(stays))[:600] + ".") if stays else "This plan has no hotel stay."
    if re.search(r"budget|cost|price|spend|left|kitna|kharch|bach|paise|money|total|paisa", m):
        return (f"The plan costs {money(it.total_cost)} of your {money(trip.budget)} budget, so {money(it.remaining)} is "
                f"left. Prices are for the whole group of {trip.travellers}.")
    if re.search(r"weather|rain|mausam|barish|temperature|garmi|thand", m):
        w = it.weather or {}
        return f"Forecast: {w['summary']}" if w.get("summary") else "There's no forecast for these dates yet."
    if re.search(r"lunch|dinner|breakfast|food|eat|khana|khaana|nashta", m):
        meals = [f"Day {n + 1}: {line(i)}" for n, d in enumerate(days) for i in d["items"] if i["kind"] == "meal"]
        return ("Meals: " + "; ".join(meals[:8]) + ".") if meals else "No meal stops are planned."
    return ONLY_THIS_TRIP
