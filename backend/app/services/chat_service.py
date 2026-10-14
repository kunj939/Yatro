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
import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ProviderUnavailable
from app.models.trip import ItineraryItem, Trip
from app.providers import wikipedia
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
- A price for ONE item ("this cab is ₹358, make it 200") can't be set by hand: answer with what that price is
  based on and the changes that would lower it ("make it cheaper", "no cab", a budget for the whole trip).
- action "change": they want something in the plan changed (budget, dates, number of days, start or end time,
  places to add or skip, pace, food, stay, transport, number of people). Leave "answer" empty: the planner does it.
- action "answer": anything else. Answer in at most 4 short sentences, using ONLY the trip data. Quote times,
  places and prices exactly as the data gives them. If the data doesn't say, say so plainly and suggest what they
  could ask instead. Never invent trains, timings, prices, phone numbers or bookings.
When an ABOUT THIS STOP section is given, the question is about that stop. You may also use its Wikipedia text to
explain what the place is, its history and why it is known, in your own words; add nothing that isn't in it, and
mention that it's from Wikipedia. Then say how it fits this trip (day, time, hours, cost) from the trip data.
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


def respond(db: Session, providers: Providers, user, trip: Trip, message: str, history: list[dict],
            item_id: str | None = None) -> dict:
    """`item_id`: the stop the traveller tapped "Ask" on; follow-up questions keep it until they ask about another."""
    from app.services import trip_service as TS
    message = message.strip()[:1000]
    ctx, candidates = TS.intent_inputs(trip)
    # Text pasted from the plan ("Cab to Jaipur Jn (JP) / catch the train leaving at 20:10") is what the message is
    # about, not a request: only the traveller's own words can change the plan.
    copied, own = pasted_items(trip, message)
    rules = intent_service.rule_intent(own, candidates, ctx) if own else intent_service.Intent()
    if copied and rules.budget_change and not intent_service._TRIP_MONEY.search(own.lower()):
        rules.budget_change, rules.item_price = None, rules.item_price or "item"  # "₹358, make it under 200"
    if not copied:
        rules.item_price = rules.item_price or intent_service.rule_intent(message, candidates, ctx).item_price
    if rules.item_price and not rules.has_change():  # "the cab is ₹358, make it 200": one item, not the budget
        item = copied[0] if copied else None
        return {"action": "answer", "text": item_price_answer(trip, rules.item_price, message, item), "by": "rules"}
    focus = _locate(trip, item_id) if item_id else None
    if focus and _SKIP_THIS.search(own or message) and focus[1]["kind"] in PLACE_KINDS:
        return {"action": "change", "message": f"skip {focus[1]['title']}"}  # "skip it", said about a tapped stop
    if rules.has_change() and not QUESTION.search(own):
        return {"action": "change", "message": own} if copied else {"action": "change"}
    if focus is None:
        focus = _locate(trip, copied[0]["id"]) if copied else _named_stop(trip, message)
    if focus:
        return about_stop(db, providers, user, trip, focus, own or message, history)
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
            if data.get("action") == "change" and (own or not copied):  # pasted text alone is a question
                return {"action": "change", "message": own} if copied else {"action": "change"}
            if data.get("answer"):
                return {"action": "answer", "text": data["answer"].strip(), "by": "ai"}
        except ProviderUnavailable as exc:
            log_usage(db, llm, "chat", user_id=user.id, trip_id=trip.id, error=str(exc),
                      latency_ms=int((time.perf_counter() - t0) * 1000))
        except Exception:  # noqa: BLE001 - a model problem falls back to the rules, never to an error
            log.warning("chat model call failed", exc_info=True)
    # No model: a question the rules can answer ("when is my train?" mentions a train but asks, not changes) is
    # answered; anything else the rules read as a change goes to the planner.
    if copied and not rules.has_change():
        return {"action": "answer", "text": describe(trip, copied[0]), "by": "rules"}
    answer = rule_answer(trip, own or message)
    if rules.has_change() and not (QUESTION.search(own) and answer != ONLY_THIS_TRIP):
        return {"action": "change", "message": own} if copied else {"action": "change"}
    return {"action": "answer", "text": answer, "by": "rules"}


PLACE_KINDS = ("activity", "sunset", "meal", "stay")
_SKIP_THIS = re.compile(r"\b(?:skip|remove|drop|hata\w*|nikal\w*)\b.{0,12}\b(?:it|this|this one|ise|isko|ye|yeh)\b|"
                        r"\b(?:ise|isko|ye|yeh)\b.{0,12}\b(?:hata\w*|skip|nikal\w*)\b", re.I)


def _locate(trip: Trip, item_id: str) -> tuple[int, dict] | None:
    for n, d in enumerate(_days(trip)):
        for i in d["items"]:
            if i["id"] == str(item_id):
                return n, i
    return None


def _named_stop(trip: Trip, message: str) -> tuple[int, dict] | None:
    """"Why is Hawa Mahal famous?": the stop the question names, the longest name first."""
    m = message.lower()
    best = None
    for n, d in enumerate(_days(trip)):
        for i in d["items"]:
            t = i["title"].lower()
            if i["kind"] in PLACE_KINDS and len(t) >= 4 and t in m and (best is None or len(t) > len(best[1]["title"])):
                best = (n, i)
    return best


def _wiki(db: Session, trip: Trip, item: dict) -> dict | None:
    if item["kind"] not in ("activity", "sunset"):
        return None  # a restaurant or hotel rarely has an article, and the wrong one would mislead
    if not get_settings().wikipedia_enabled:
        return None
    row = db.get(ItineraryItem, uuid.UUID(item["id"])) if item.get("id") else None
    place = row.place if row is not None else None
    name = place.name if place is not None and place.name else item["title"]
    qid = ((place.meta or {}).get("wikidata") if place is not None else None) or None
    return wikipedia.about(name, (trip.destination_name or "").split(",")[0], qid)


def _sentences(text: str, limit: int = 650) -> str:
    out = ""
    for s in re.split(r"(?<=[.!?])\s+", text):
        if out and len(out) + len(s) > limit:
            break
        out = f"{out} {s}".strip()
    return out


def _mins(n: int | None) -> str:
    if not n:
        return ""
    h, m = divmod(int(n), 60)
    return f"{h} h {m} min" if h and m else f"{h} h" if h else f"{m} min"


def _in_plan(trip: Trip, n: int, i: dict) -> str:
    when = f"{i['time']}–{i['end_time']}" if i.get("time") and i.get("duration_minutes") else (i.get("time") or "")
    bits = [f"Day {n + 1}{', ' + when if when else ''}" + (f" ({_mins(i['duration_minutes'])})" if i.get("duration_minutes") else "")]
    if i.get("opening_time") and i.get("closing_time"):
        bits.append(f"open {i['opening_time']}–{i['closing_time']}")
    if i.get("cost"):
        bits.append(f"{fmt_money(i['cost'], trip.currency)} for the group")
    elif i["kind"] in ("activity", "sunset") and i.get("cost") == 0:
        bits.append("free entry")
    return "In your plan: " + ", ".join(bits) + "."


def about_answer(trip: Trip, n: int, i: dict, info: dict | None, message: str) -> str:
    """A question about one stop, answered without a model: the plan's facts, and Wikipedia's introduction."""
    m = message.lower()
    plan = _in_plan(trip, n, i)
    if re.search(r"how long|how much time|kitna time|kitni der|kitne ghante|time chahiye|spend", m):
        rec = i.get("recommended_minutes")
        extra = f" Most people spend about {_mins(rec)} there." if rec and rec != i.get("duration_minutes") else ""
        return plan + extra
    if re.search(r"\bopen|timing|close|closing|band|khula|khulta", m):
        if i.get("opening_time"):
            return f"{i['title']} is open {i['opening_time']}–{i['closing_time']} that day. {plan}"
        return f"Opening hours for {i['title']} aren't confirmed, so check before you go. {plan}"
    if re.search(r"entry|ticket|fee|price|cost|kitne ka|kitna paisa", m):
        return plan
    if info:
        return f"{_sentences(info['extract'])} (Wikipedia)\n\n{plan}"
    if i["kind"] not in ("activity", "sunset"):
        return describe(trip, i)
    why = i.get("meta", {}).get("why_go") or i.get("explanation") or i.get("subtitle")
    return ((f"{why.rstrip('.')}. " if why else "") + f"I couldn't find an article about {i['title']} to tell you more, "
            f"so here is how it fits your trip. {plan}")


def about_stop(db: Session, providers: Providers, user, trip: Trip, focus: tuple[int, dict], message: str,
               history: list[dict]) -> dict:
    n, i = focus
    info = _wiki(db, trip, i)
    source = {"label": "Wikipedia", "title": info["title"], "url": info["url"]} if info and info.get("url") else None
    out = {"action": "answer", "item_id": i["id"], "item_title": i["title"], "source": source}
    llm = providers.llm
    if llm.live:
        stop = f"Day {n + 1}: {describe(trip, i)} {_in_plan(trip, n, i)}"
        if i.get("explanation"):
            stop += f" Why it's in the plan: {i['explanation']}"
        if info:
            stop += f"\nWikipedia, \"{info['title']}\": {info['extract']}"
        convo = [LLMMessage("system", CHAT_SYSTEM), LLMMessage("system", "TRIP DATA:\n" + trip_brief(trip, 6000)),
                 LLMMessage("system", "ABOUT THIS STOP:\n" + stop)]
        for h in history[-6:]:
            convo.append(LLMMessage("assistant" if h.get("role") == "assistant" else "user", str(h.get("text", ""))[:600]))
        convo.append(LLMMessage("user", message))
        t0 = time.perf_counter()
        try:
            result = llm.complete(convo, json_schema=CHAT_SCHEMA, temperature=0.3, max_tokens=450)
            log_usage(db, llm, "chat", user_id=user.id, trip_id=trip.id, result=result)
            text = ((result.data or {}).get("answer") or "").strip()
            if text:
                return {**out, "text": text, "by": "ai"}
        except ProviderUnavailable as exc:
            log_usage(db, llm, "chat", user_id=user.id, trip_id=trip.id, error=str(exc),
                      latency_ms=int((time.perf_counter() - t0) * 1000))
        except Exception:  # noqa: BLE001 - a model problem falls back to the rules, never to an error
            log.warning("chat model call failed", exc_info=True)
    text = about_answer(trip, n, i, info, message)
    return {**out, "text": text, "by": "rules", "source": source if "(Wikipedia)" in text else None}  # linked when quoted


_CARD_LABELS = re.compile(r"(?im)^\s*(?:travel|stay|food|meal|activity|sunset|why this\??|swap|directions|day \d+[^\n]*)\s*$")


def pasted_items(trip: Trip, message: str) -> tuple[list[dict], str]:
    """Plan items whose text is in the message, and what is left of the message without it."""
    rest, hits = message, []
    for d in _days(trip):
        for i in d["items"]:
            texts = [i["title"], i.get("subtitle"), i.get("explanation"), *(i.get("reasons") or [])]
            for s in sorted({s.strip() for s in texts if isinstance(s, str) and len(s.strip()) >= 8}, key=len, reverse=True):
                if s in rest:
                    rest = rest.replace(s, " ")
                    if all(h["id"] != i["id"] for h in hits):
                        hits.append(i)
    if not hits:
        return [], message
    rest = _CARD_LABELS.sub(" ", rest)
    return hits, re.sub(r"\s+", " ", rest).strip(" .,-\n")


def _cost(i: dict) -> float:
    return (i.get("cost") or 0) + (i.get("travel_cost") or 0)


def _price_basis(i: dict) -> str:
    estimated = "estimated" in (i.get("cost_state") or "") or i.get("leg_state") == "estimated"
    return "an estimate for the distance, as there's no live fare for it" if estimated else "the listed fare"


def describe(trip: Trip, i: dict) -> str:
    """One plan item in a sentence or two: when, what, what it costs and why."""
    when = i.get("time")
    head = i["title"] if when and i["title"].startswith(when) else f"{when or 'Time to be confirmed'}: {i['title']}"
    bits = [head + (f", {nice(i['start_at'][:10])}" if i.get("start_at") else "") + "."]
    if i.get("subtitle"):
        bits.append(i["subtitle"].rstrip(".") + ".")
    if _cost(i):
        bits.append(f"{fmt_money(_cost(i), trip.currency)} for the group ({_price_basis(i)}).")
    bits.append("Ask me anything about it, or tell me what to change.")
    return " ".join(bits)


def nice(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%a %d %b").replace(" 0", " ")


# --- answers without a model ------------------------------------------------------------------------------------------
_KIND_WORDS = {"cab": ("cab", "taxi", "auto", "rickshaw"), "ticket": ("train", "flight", "bus", "express"),
               "train": ("train", "express", "rail"), "flight": ("flight", "air"), "bus": ("bus",),
               "hotel": ("hotel", "stay", "check-in", "room"), "meal": ("lunch", "dinner", "breakfast", "cafe"),
               "entry": ("entry", "ticket")}
_FIX = {"cab": "“no cab” to plan the trip without cabs",
        "ticket": "“by train” or “by bus”, or a different train by name",
        "train": "a different train by name, or “by bus”",
        "flight": "“by train” or “by bus”, or travel on another date",
        "bus": "“by train”, or a different time",
        "hotel": "“budget hotel”, “hostel” or “oyo”",
        "meal": "“make it cheaper” for simpler places to eat",
        "entry": "“skip <the place>” to leave it out"}


def item_price_answer(trip: Trip, kind: str, message: str, item: dict | None = None) -> str:
    """Name the item the traveller means, say where its price comes from, and how to get it lower."""
    if item is None:
        words = _KIND_WORDS.get(kind, ())
        for d in _days(trip):
            for i in d["items"]:
                if _cost(i) and any(w in i["title"].lower() or w == (i.get("travel_mode") or "") for w in words):
                    item = item or i
    if item is not None and kind == "item":
        t = f"{item['title']} {item.get('travel_mode') or ''} {item['kind']}".lower()
        kind = next((k for k, ws in _KIND_WORDS.items() if k != "ticket" and any(w in t for w in ws)), "item")
    head = f"{item['title']} is {fmt_money(_cost(item), trip.currency)} in the plan: {_price_basis(item)}. " if item else ""
    return (head + "I can't set one item's price by hand, because the plan has to stay true to what it really costs. "
            f"To pay less, tell me {_FIX.get(kind, '“make it cheaper”')}, or “make it cheaper” for the whole trip.")


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
