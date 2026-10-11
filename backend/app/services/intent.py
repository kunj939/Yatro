"""Understanding a traveller's "change the plan" request, whatever they type.

Two readers share the work. A language model (when one is configured) reads the request in any language. A
rule-based reader — English and Hinglish, no network — covers the same ground when there is no model, when it
fails, and fills in anything the model leaves out. Their answers are merged.

Neither is allowed to fail loudly. What can be understood is applied; what can't is reported back in plain words
(`Intent.ignored`, `Intent.unsupported`, `Intent.reply`) — a request is never rejected as an error, and nothing is
silently dropped.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.errors import ProviderUnavailable
from app.providers.base import LLMProvider
from app.providers.types import LLMMessage
from app.services.llm_service import FACT_RULE, log_usage

log = logging.getLogger("tripai.intent")

STYLES = {"premium", "balanced", "budget", "romantic", "adventure", "sightseeing", "food", "relaxed"}
STAYS = {"premium", "tier1", "tier2", "budget", "oyo", "hostel", "no_stay"}
MODES = {"train", "bus", "metro", "cab", "own_car", "flight", "mixed"}
PACES = {"relaxed", "normal", "packed"}
TRAVELLER_TYPES = {"solo", "couple", "partner", "friends", "family"}
# The place tags the planner ranks by (ranking.py): "more shopping" or "no temples" is translated into these.
INTEREST_TAGS = {"fort", "palace", "royal", "temple", "spiritual", "nature", "lake", "garden", "park", "street_food",
                 "food", "cafe", "museum", "gallery", "photography", "market", "shopping", "adventure", "nightlife",
                 "heritage", "landmark", "viewpoint", "sunset", "romantic", "family", "zoo", "fine_dining"}

INTEREST_WORDS: list[tuple[str, list[str], str]] = [
    (r"shopping|shops?|bazaars?|bazars?|markets?|malls?|souvenirs?|handicrafts?",
     ["market", "shopping"], "shopping & markets"),
    (r"museums?|art galler(?:y|ies)|galler(?:y|ies)|exhibitions?", ["museum", "gallery"], "museums & art"),
    (r"temples?|mandirs?|spiritual|religious|churches|church|mosques?|masjids?|gurudwaras?|ghats?|aartis?|pilgrimage",
     ["temple", "spiritual"], "temples & spiritual sites"),
    (r"forts?|palaces?|castles?|qilas?|royal", ["fort", "palace", "royal"], "forts & palaces"),
    (r"heritage|history|historical|monuments?|old city|ruins?", ["heritage", "landmark"], "heritage & history"),
    (r"street food|local food|local cuisine|food places|food spots|foodie|chaat|famous food|cafes?|coffee",
     ["street_food", "food", "cafe"], "local food & cafes"),
    (r"nature|lakes?|gardens?|parks?|waterfalls?|greenery|botanical", ["nature", "lake", "garden"],
     "nature & gardens"),
    (r"adventure|treks?|trekking|hikes?|hiking|rafting|bungee|paragliding|zip ?line|camping", ["adventure"],
     "adventure"),
    (r"sunsets?|sunrises?|view ?points?|views?", ["sunset", "viewpoint"], "viewpoints & sunsets"),
    (r"photography|photos?|photo spots?|instagram|instagrammable|photogenic", ["photography"], "photo spots"),
    (r"nightlife|night life|pubs?|clubs?|bars?|parties|party", ["nightlife"], "nightlife"),
    (r"kids|children|child friendly|family friendly|zoo|amusement|water park", ["family", "zoo"],
     "family-friendly places"),
]
_INTEREST_RES = [(re.compile(rf"\b(?:{w})\b"), tags, label) for w, tags, label in INTEREST_WORDS]
_INTEREST_ANY = re.compile(r"\b(?:" + "|".join(w for w, _, _ in INTEREST_WORDS) + r")\b")


# --------------------------------------------------------------------------------------------------------
# What was understood
# --------------------------------------------------------------------------------------------------------

@dataclass
class Intent:
    budget_change: dict | None = None  # {"mode": "set|increase|decrease", "amount": number}
    days_total: int | None = None  # "make it a 3-day trip"
    extend_days: int = 0  # "add one more day" = 1
    pace: str | None = None
    add_styles: list[str] = field(default_factory=list)
    remove_styles: list[str] = field(default_factory=list)
    food: str | None = None
    stay: str | None = None
    transport_only: list[str] = field(default_factory=list)  # "go by train": these, instead of the current ones
    transport_add: list[str] = field(default_factory=list)  # "also allow a flight"
    transport_remove: list[str] = field(default_factory=list)  # "no buses"
    start_time: str | None = None  # "HH:MM"
    end_time: str | None = None
    start_shift: int = 0  # minutes: "start an hour later" = 60
    end_shift: int = 0
    travellers: int | None = None  # set to this many ...
    travellers_add: int = 0  # ... or this many more
    traveller_type: str | None = None
    include_place_ids: list[str] = field(default_factory=list)
    exclude_place_ids: list[str] = field(default_factory=list)
    add_place_names: list[str] = field(default_factory=list)  # to look up: not in the candidate list
    train_names: list[str] = field(default_factory=list)  # specific trains asked for: "double decker", "12985"
    train_direction: str | None = None  # "outbound" | "return" | None (both ways)
    unknown_skips: list[str] = field(default_factory=list)  # "skip X", where X isn't something we know of
    interests: list[str] = field(default_factory=list)  # place tags to favour
    avoid_tags: list[str] = field(default_factory=list)  # place tags to see less of
    interest_labels: dict = field(default_factory=dict)  # tag -> "shopping & markets", for the summary
    needs: list[str] = field(default_factory=list)  # "Tell me how much" — understood, but incomplete
    unsupported: list[str] = field(default_factory=list)  # understood, but the planner can't do it
    ignored: list[str] = field(default_factory=list)  # parts of the text nothing recognised
    reply: str | None = None  # the model's own one-line answer, shown when nothing could be changed
    source: str = "rules"

    def has_change(self) -> bool:
        return bool(self.budget_change or self.days_total or self.extend_days or self.pace or self.add_styles
                    or self.remove_styles or self.food or self.stay or self.transport_only or self.transport_add
                    or self.transport_remove or self.start_time or self.end_time or self.start_shift or self.end_shift
                    or self.travellers
                    or self.travellers_add or self.traveller_type or self.include_place_ids or self.exclude_place_ids
                    or self.add_place_names or self.interests or self.avoid_tags or self.train_names)

    def is_empty(self) -> bool:
        return not (self.has_change() or self.unknown_skips or self.needs or self.unsupported)

    def as_dict(self) -> dict:
        """The shape the first, keyword-only version returned; kept for callers that still read it."""
        return {"summary": "", "budget_change": self.budget_change, "pace": self.pace,
                "add_styles": self.add_styles, "remove_styles": self.remove_styles, "food": self.food,
                "stay": self.stay, "transport_modes": [*self.transport_only, *self.transport_add],
                "exclude_place_ids": self.exclude_place_ids, "include_place_ids": self.include_place_ids,
                "interests": self.interests, "extend_days": self.extend_days}


def _dedupe(items) -> list:
    return list(dict.fromkeys(items))


# --------------------------------------------------------------------------------------------------------
# Rule-based reader
# --------------------------------------------------------------------------------------------------------

_NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
             "ten": 10, "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6,
             "chhah": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10}
_WORDNUM = "|".join(sorted(_NUMWORDS, key=len, reverse=True))
_CLAUSE_SPLIT = re.compile(r"(?:[,;!?\n|]+|\.(?=\s|$)|\s+(?:and|but|plus|then|aur|lekin|phir)\s+)", re.IGNORECASE)

_NEG_CORE = re.compile(r"\b(?:skip|remove|drop|avoid|without|exclude|delete|cancel|hata\w*|nikal\w*|nahi|nahin|nhi|"
                       r"mat|no|not|don'?t|dont|never|ignore|leave out|chhod\w*|enough|bored of|tired of)\b")
_NEG_REDUCE = re.compile(r"\b(?:less|lesser|fewer|kam|too many|too much)\b")
_POS = re.compile(r"\b(?:add|include|visit|see|go to|going to|want|wanna|must|dekh\w*|jana|jaana|ghoom\w*|chahiye|"
                  r"need|put|keep|also|bhi|show|take (?:me|us)|let'?s|cover|check out|explore|love|like)\b")


def _norm(text: str) -> str:
    t = (text or "").lower()
    for a, b in (("’", "'"), ("‘", "'"), ("–", "-"), ("—", "-"), ("“", '"'), ("”", '"')):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", t).strip()


def _clauses(message: str) -> list[tuple[str, str]]:
    """[(original text, normalised text)] — a request cut at commas, "and", "but", "aur"..."""
    orig = re.sub(r"\s+", " ", message or "").strip()
    return [(p.strip(), _norm(p)) for p in _CLAUSE_SPLIT.split(orig) if p and p.strip()]


def _num(word: str) -> int | None:
    return int(word) if word.isdigit() else _NUMWORDS.get(word.lower())


def _day_change(t: str) -> int:
    """'add one more day', 'extend by 2 days', 'ek din aur', 'one day less', 'ek din kam' -> +1/+2/+1/-1/-1."""
    m = re.search(rf"(\d+|{_WORDNUM}|a|an)\s*(?:more\s*)?(?:day|days|din)\b", t)
    if not m:
        return 0
    n = int(m.group(1)) if m.group(1).isdigit() else (_NUMWORDS.get(m.group(1)) or (1 if m.group(1) in ("a", "an") else 0))
    if re.search(r"\b(?:less|fewer|shorten|remove|reduce|cut|kam|ghata\w*)\b", t):
        return -n
    if re.search(r"\b(?:add|more|extend|extra|longer|aur|badha\w*|stay)\b", t):
        return n
    return 0


# --- trip length -----------------------------------------------------------------------------------------

def _read_length(t: str, it: Intent) -> None:
    n = _day_change(t)
    if n:
        it.extend_days = n
        return
    m = (re.search(rf"\b(?P<n>\d+|{_WORDNUM})\s*[- ]?\s*(?:day|days|din)\s*(?:trip|tour|plan|ka|ki|ke|only|total)\b", t)
         or re.search(rf"\b(?:make it|make this|change (?:it )?to|convert (?:it )?to|for|total|exactly|poora)\s+"
                      rf"(?:a\s+)?(?P<n>\d+|{_WORDNUM})\s*[- ]?\s*(?:day|days|din)\b", t))
    if m:
        total = _num(m.group("n"))
        if total and 1 <= total <= 8:
            it.days_total = total
    elif re.search(r"\b(?:day trip|daytrip|same day|one day trip|sirf ek din|ek din ka)\b", t):
        it.days_total = 1


# --- budget ----------------------------------------------------------------------------------------------

_MONEY_UNITS = {"k": 1_000, "hazar": 1_000, "hazaar": 1_000, "hajar": 1_000, "thousand": 1_000, "lakh": 100_000,
                "lakhs": 100_000, "lac": 100_000, "lacs": 100_000}
_NOT_MONEY_NEXT = {"am", "pm", "baje", "bje", "day", "days", "din", "night", "nights", "raat", "people", "person",
                   "persons", "pax", "friends", "log", "hours", "hour", "hrs", "hr", "ghante", "ghanta", "km", "kms",
                   "star", "stars", "min", "mins", "minutes", "stops", "places", "members", "adults", "kids",
                   "children", "percent", "%"}
_AMOUNT = re.compile(r"(?P<pre>(?:₹|\brs\.?|\binr)\s?)?(?P<num>\d[\d,]*(?:\.\d+)?|" + _WORDNUM + r")\s*"
                     r"(?P<unit>k(?![a-z])|hazaa?r|hajar|thousand|lakhs?|lacs?)?")
_UP = r"increase|raise|add|extra|more|up by|badha\w*|zyada|jyada|plus|top ?up|bump"
_DOWN = r"decrease|reduce|lower|cut|less|down by|kam|ghata\w*|minus|drop"
_SET = r"under|within|upto|up to|at most|max(?:imum)?|not more than|tak|total|set|make it|change (?:it )?to|exactly|only"
_BUDGET_CUE = re.compile(r"\bbudget\b|₹|\brs\b|\binr\b|\brupee|\brupaye|\bpaisa|\bpaise|\bkharcha|\bkharch\b|\bspend|"
                         r"\bcost\b|\bafford|\bunder\b|\bwithin\b|\bupto\b|\bup to\b|\bat most\b|\bmax(?:imum)?\b|"
                         r"\bhazaa?r\b|\blakh|\blac\b|\d\s?k\b|\bper (?:person|head|pax)\b|\bpp\b")


def _read_budget(t: str, it: Intent, ctx: dict) -> None:
    has_dir = bool(re.search(rf"\b(?:{_UP}|{_DOWN})\b", t))
    if not (_BUDGET_CUE.search(t) or has_dir):
        return
    best = None
    for m in _AMOUNT.finditer(t):
        raw = m.group("num")
        unit = (m.group("unit") or "").lower()
        if raw.isalpha():  # a number word ("do hazar") only counts with a unit; "one more day" isn't money
            if not unit or raw not in _NUMWORDS:
                continue
            value = float(_NUMWORDS[raw])
        else:
            value = float(raw.replace(",", ""))
        value *= _MONEY_UNITS.get(unit, 1)
        nxt = re.match(r"\s*([a-z%]+)", t[m.end():])
        if nxt and nxt.group(1) in _NOT_MONEY_NEXT:
            continue
        if value < 100:
            continue
        before = t[max(0, m.start() - 20):m.start()]
        score = (3 if m.group("pre") else 0) + (2 if unit else 0) + (1 if value >= 1000 else 0)
        if re.search(r"(?:budget|under|within|upto|up to|at most|max(?:imum)?|total|spend(?:ing)?|cost|kharcha|rs|"
                     r"rupees?)\W*(?:of|is|to|at|by|:)?\W*$", before):
            score += 3
        if re.match(r"\s*(?:rs|rupees?|rupaye|inr|total|only|budget|tak|per person|pp|each)\b", t[m.end():]):
            score += 2
        if re.search(rf"\b(?:{_UP}|{_DOWN})\b\W*(?:\w+\W+){{0,2}}$", before):
            score += 2
        if best is None or score > best[0]:
            best = (score, value, m)
    if best is None or best[0] < 3:
        if has_dir and re.search(r"\bbudget\b|\bkharcha\b|\bpaisa\b", t):
            it.needs.append("how much to change the budget by — for example “budget 8000” or “raise budget by 1000”")
        return
    _, value, m = best
    before = t[max(0, m.start() - 24):m.start()]
    after = t[m.end():m.end() + 24]
    if re.search(rf"\b(?:{_SET})\W*$", before[-14:]):
        mode = "set"
    elif re.search(rf"\b(?:{_UP})\b", before) or re.match(rf"\W*(?:{_UP})\b", after):
        mode = "increase"
    elif re.search(rf"\b(?:{_DOWN})\b", before) or re.match(rf"\W*(?:{_DOWN})\b", after):
        mode = "decrease"
    else:
        mode = "set"
    if re.search(r"\b(?:per person|per head|pp|each|per pax|a head|prati vyakti)\b", t):
        value *= max(1, int(ctx.get("travellers") or 1))
    it.budget_change = {"mode": mode, "amount": value}


# --- start and end times ---------------------------------------------------------------------------------

_PER_WORDS = "subah|savere|morning|dopahar|afternoon|shaam|sham|evening|raat|rat|night"
_CLOCK = (r"(?:(?P<per>" + _PER_WORDS + r")\s*(?:ko|ke|me|mein|ka)?\s*)?(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?"
          r"\s*(?P<ap>a\.?m\.?|p\.?m\.?|baje|bje|o'?clock)?")
_FILL = (r"the|my|our|day|trip|journey|at|from|by|before|after|till|until|around|home|back|to|is|should be|ko|se|pe|"
         r"par|time|me|mein|exactly|only|latest|no later than|hai|early|late")
_START_CUE = r"start(?:ing)?|begin(?:ning)?|leave|leaving|depart(?:ing)?|set off|shuru|nikal\w*|get going|go out"
_END_CUE = (r"end|ending|finish(?:ing)?|done|back|return(?:ing)?|reach(?:ing)? (?:home|back)|get(?:ting)? (?:home|back)|"
            r"come(?:ing)? back|home|wapas|vapas|khatam|ghar")
_START_RES = [re.compile(r"\b(?:" + _START_CUE + r")\b(?:\s+(?:" + _FILL + r"))*\s*" + _CLOCK),
              re.compile(_CLOCK + r"\s*(?:se|pe|par|onwards?)?\s*(?:start|shuru|nikal\w*|begin)")]
_END_RES = [re.compile(r"\b(?:" + _END_CUE + r")\b(?:\s+(?:" + _FILL + r"))*\s*" + _CLOCK),
            re.compile(r"\b(?:till|until|upto|up to|stay(?:ing)? (?:out )?(?:till|until)|late till)\s*" + _CLOCK),
            re.compile(_CLOCK + r"\s*(?:tak|tk)\b")]
_NOT_TIME_NEXT = {"days", "day", "din", "people", "persons", "hours", "hour", "hrs", "hr", "min", "mins", "minute",
                  "minutes", "ghante", "ghanta", "rupees", "rs", "k", "km", "stops", "places", "nights", "star",
                  "stars", "friends", "log", "pax"}
_PERIOD_AMPM = {"subah": "am", "savere": "am", "morning": "am", "dopahar": "pm", "afternoon": "pm", "shaam": "pm",
                "sham": "pm", "evening": "pm", "raat": "pm", "rat": "pm", "night": "pm"}


def _clock(h: str, m: str | None, ap: str | None, per: str | None, role: str) -> str | None:
    hour, minute = int(h), int(m or 0)
    if hour > 24 or minute > 59 or (hour == 24 and minute):
        return None
    tag = (ap or "").lower().replace(".", "")
    if tag in ("", "baje", "bje", "o'clock", "oclock"):
        tag = _PERIOD_AMPM.get(per or "", "")
        if per in ("raat", "rat", "night") and 1 <= hour <= 4:
            tag = "am"  # "raat 1 baje" is 1 am
    if tag == "pm" and hour < 12:
        hour += 12
    elif tag == "am" and hour == 12:
        hour = 0
    elif not tag:
        if role == "start" and 1 <= hour <= 3:
            hour += 12  # "start at 2" can only mean the afternoon
        elif role == "end" and 1 <= hour <= 11:
            hour += 12  # "back by 9" means 9 pm
    return f"{hour % 24:02d}:{minute:02d}"


def _find_time(clause: str, patterns: list[re.Pattern], role: str, taken: list[tuple[int, int]]) -> str | None:
    for pat in patterns:
        for m in pat.finditer(clause):
            span = m.span()
            if any(span[0] < e and s < span[1] for s, e in taken):
                continue
            nxt = re.match(r"\s*([a-z]+)", clause[m.end():])
            if nxt and nxt.group(1) in _NOT_TIME_NEXT:
                continue
            value = _clock(m.group("h"), m.group("m"), m.group("ap"), m.group("per"), role)
            if value:
                taken.append(span)
                return value
    return None


def _read_times(clauses: list[tuple[str, str]], t: str, it: Intent) -> None:
    for _, c in clauses:
        taken: list[tuple[int, int]] = []
        start = _find_time(c, _START_RES, "start", taken)
        end = _find_time(c, _END_RES, "end", taken)
        it.start_time = it.start_time or start
        it.end_time = it.end_time or end
    _read_shifts(t, it)
    if not it.start_time and re.search(r"\b(?:early start|start early|early morning start|jaldi (?:start|shuru|nikal)\w*)\b", t):
        it.start_time = "06:00"
    if not it.end_time and re.search(r"\b(?:stay (?:out )?late|late night|come back late|return late|late return|raat tak)\b", t):
        it.end_time = "23:00"


_SHIFT = re.compile(r"\b(?P<what>" + _START_CUE + "|" + _END_CUE + r"|stay(?:ing)?|extend(?:ing)?)\b[^.,;]{0,20}?\b"
                    r"(?P<n>\d+(?:\.\d+)?|half(?: an?)?|an?|one|two|three|four|ek|do|teen)\s*(?:hours?|hrs?|hr|ghante|ghanta)\s*"
                    r"(?P<dir>later|earlier|late|early|longer|extra|baad|pehle)")


_EXTEND = re.compile(r"\bextend\w*\s+(?:it |the trip |the day )?by\s+(?P<n>\d+(?:\.\d+)?|an?|one|two|three)\s*(?:hours?|hrs?|hr)\b")


def _hours(raw: str) -> float:
    return 0.5 if raw.startswith("half") else float(raw) if raw[0].isdigit() else float(_NUMWORDS.get(raw, 1))


def _read_shifts(t: str, it: Intent) -> None:
    for m in _SHIFT.finditer(t):
        minutes = int(_hours(m.group("n")) * 60) * (1 if m.group("dir") in ("later", "late", "longer", "extra", "baad") else -1)
        if re.fullmatch(r"(?:" + _START_CUE + ")", m.group("what")):
            it.start_shift = it.start_shift or minutes
        else:
            it.end_shift = it.end_shift or minutes
    m = _EXTEND.search(t)
    if m and not it.end_shift:
        it.end_shift = int(_hours(m.group("n")) * 60)


# --- who is travelling -----------------------------------------------------------------------------------

_PEOPLE = r"(?:people|persons?|pax|members?|friends|log|logon|travell?ers|adults|of us|insaan|banda|bande)"


def _read_travellers(t: str, it: Intent) -> None:
    more = (re.search(rf"\b(?:add|aur|extra|another|plus|bring)\s+(?P<n>\d{{1,2}}|{_WORDNUM})\s*(?:more\s+)?{_PEOPLE}\b", t)
            or re.search(rf"\b(?P<n>\d{{1,2}}|{_WORDNUM})\s+(?:more|aur)\s*{_PEOPLE}?\b", t))
    if more and (_num(more.group("n")) or 0) > 0 and re.search(_PEOPLE, t[more.start():more.end() + 14] or ""):
        it.travellers_add = min(19, _num(more.group("n")) or 0)
    else:
        m = (re.search(rf"\b(?:we are|we're|there are|there will be|group of|party of|total of)\s+(?P<n>\d{{1,2}}|{_WORDNUM})\b"
                       rf"(?!\s*(?:days?|din|hours?|nights?|am|pm|baje|k\b))", t)
             or re.search(rf"\b(?:for|hum|with)\s+(?P<n>\d{{1,2}}|{_WORDNUM})\s*{_PEOPLE}\b", t)
             or re.search(rf"\b(?P<n>\d{{1,2}})\s*{_PEOPLE}\b", t))
        n = _num(m.group("n")) if m else None
        if n and 1 <= n <= 20:
            it.travellers = n
    if re.search(r"\b(?:just me|only me|solo|alone|akela|akele|by myself)\b", t):
        it.travellers, it.traveller_type = 1, "solo"
    elif re.search(r"\b(?:couple|husband|wife|honeymoon|hubby|spouse|pati|patni)\b", t):
        it.traveller_type = "couple"
        it.travellers = it.travellers or (2 if not it.travellers_add else None)
    elif re.search(r"\b(?:girlfriend|boyfriend|partner|gf|bf)\b", t):
        it.traveller_type = "partner"
        it.travellers = it.travellers or (2 if not it.travellers_add else None)
    elif re.search(r"\b(?:family|kids|parents|bachon?|children)\b", t) and not re.search(r"family[- ]friendly", t):
        it.traveller_type = "family"
    elif re.search(r"\b(?:friends|dosto|group)\b", t):
        it.traveller_type = "friends"


# --- style, pace, food, stay, transport ------------------------------------------------------------------

_CHEAP = (r"cheap(?:er|est)?|sasta|saste|sasti|economical|save (?:some )?money|saving|low[- ]cost|lower[- ]cost|"
          r"kam kharcha|kam paisa|kam budget|budget[- ]friendly|affordable|pocket[- ]friendly|tight budget|"
          r"inexpensive|less expensive|bachat|budget (?:is )?(?:tight|low|limited)|low budget|limited budget|paisa kam|paise kam")
_LUXE = r"luxur\w*|premium|upscale|fancy|splurge|high[- ]end|fine dining|best hotel|comfort(?:able)?|aaramdayak|shandar"
_RELAXED = (r"relax\w*|slow(?:er|ly)?|unhurried|leisurely|not rushed|no rush|chill\w*|aaram\w*|araam\w*|"
            r"fewer (?:stops|places)|less (?:walking|rushed|packed|hectic)|kam jagah|too much walking|zyada walking|"
            r"jyada walking|bahut (?:walking|chalna)|walking kam|kam walking|thak\w*|tiring|exhausting")
_PACKED = (r"packed|busy|more (?:stops|places|things)|see more|as much as|jyada jagah|zyada jagah|"
           r"zyada ghoom\w*|jyada ghoom\w*|cover more|action[- ]packed|tight schedule")
_STYLE_WORDS = {"romantic": r"romantic|romance|date night|honeymoon|candle ?light", "adventure": r"adventur\w*|thrill\w*",
                "sightseeing": r"sight[- ]?seeing|landmarks?", "food": r"foodie|food lover|food tour|food focused",
                "relaxed": r"relax\w*|leisurely", "balanced": r"balanced", "premium": _LUXE}
_MODE_WORDS = {"train": r"trains?|railway|rail", "bus": r"buses|bus|volvo|roadways", "cab": r"cabs?|taxi|uber|ola",
               "own_car": r"my car|own car|our car|self[- ]drive|drive myself", "flight": r"flights?|fly|plane|airplane|aeroplane",
               "metro": r"metro"}


def _read_style_pace_food_stay(t: str, clauses: list[tuple[str, str]], it: Intent) -> None:
    if re.search(rf"\b(?:{_RELAXED})\b", t):
        it.pace = "relaxed"
    elif re.search(rf"\b(?:{_PACKED})\b", t):
        it.pace = "packed"
    if re.search(rf"\b(?:{_CHEAP})\b", t) and not it.budget_change:
        it.add_styles.append("budget")
    for _, c in clauses:
        negative = bool(_NEG_CORE.search(c))
        for style, words in _STYLE_WORDS.items():
            if re.search(rf"\b(?:{words})\b", c) and not (style == "relaxed" and it.pace == "relaxed" and not negative):
                (it.remove_styles if negative else it.add_styles).append(style)
        if re.search(rf"\b(?:{_LUXE})\b", c) and "premium" not in it.add_styles and not negative:
            it.add_styles.append("premium")
    it.add_styles = [s for s in _dedupe(it.add_styles) if s in STYLES]
    it.remove_styles = [s for s in _dedupe(it.remove_styles) if s in STYLES]
    # food
    if re.search(r"\b(?:non[- ]?veg\w*|chicken|mutton|meat|fish|egg|eggs|anda|seafood)\b", t):
        it.food = "non_veg" if re.search(r"\b(?:only|sirf|just)\b", t) else "both"
    elif re.search(r"\b(?:veg|vegetarian|jain|shakahari|pure veg)\b", t) and not re.search(r"\bnot veg\b", t):
        it.food = "veg"
    elif re.search(r"\b(?:anything|everything|any food|sab kuch|koi bhi khana|no food restriction)\b", t):
        it.food = "both"
    # stay
    if re.search(r"\b(?:no (?:hotel|stay)|without (?:a )?(?:hotel|stay)|(?:don'?t|dont) (?:need|want) (?:a )?(?:hotel|stay)|"
                 r"hotel (?:nahi|nahin|mat)|stay (?:nahi|nahin)|no overnight)\b", t):
        it.stay = "no_stay"
    elif re.search(r"\b(?:hostels?|dorm(?:itory)?)\b", t):
        it.stay = "hostel"
    elif re.search(r"\boyo\b", t):
        it.stay = "oyo"
    else:
        star = re.search(r"\b([1-5]|five|four|three|two)[- ]?stars?\b", t)
        if star and re.search(r"\b(?:hotels?|stay|rooms?|resorts?|staying)\b", t):
            n = {"five": 5, "four": 4, "three": 3, "two": 2}.get(star.group(1)) or int(star.group(1))
            it.stay = {5: "premium", 4: "tier1", 3: "tier2", 2: "budget", 1: "oyo"}[n]
        elif re.search(r"\b(?:luxury|premium|best) (?:hotel|stay|room)s?\b", t):
            it.stay = "premium"
        elif re.search(r"\b(?:budget|cheap|cheaper|sasta|economy) (?:hotel|stay|room)s?\b|\bhomestay\b|\bguest ?house\b", t):
            it.stay = "budget"
        elif re.search(r"\b(?:mid[- ]?range|decent|comfortable) (?:hotel|stay)\b", t):
            it.stay = "tier2"


_NEG_BEFORE_MODE = re.compile(r"(?:no|not|avoid|without|skip|don'?t|dont|except|hata\w*|nahi|nahin|mat)\W+(?:\w+\W+){0,2}$")
_ADD_WORDS = re.compile(r"\b(?:also|too|as well|bhi|add|include|allow|plus|or)\b")


def _read_transport(clauses: list[tuple[str, str]], it: Intent) -> None:
    for _, c in clauses:
        found = []
        for mode, words in _MODE_WORDS.items():
            for m in re.finditer(rf"\b(?:{words})\b", c):
                before, after = c[:m.start()], c[m.end():]
                neg = bool(_NEG_BEFORE_MODE.search(before)) or bool(re.match(r"\W*(?:nahi|nahin|mat|hata)\b", after))
                found.append((mode, "remove" if neg else ("add" if _ADD_WORDS.search(c) else "only")))
        for mode, how in found:
            {"remove": it.transport_remove, "add": it.transport_add, "only": it.transport_only}[how].append(mode)
    if re.search(r"\b(?:any mode|any transport|whatever (?:is )?(?:best|cheapest)|let you choose|koi bhi (?:mode|transport)|you choose)\b", " ".join(c for _, c in clauses)):
        it.transport_only.append("mixed")
    it.transport_only = _dedupe(it.transport_only)
    it.transport_add = [m for m in _dedupe(it.transport_add) if m not in it.transport_only]
    it.transport_remove = _dedupe(it.transport_remove)


# --- a specific train: "take the double decker", "by Shatabdi", "train 12985" ---------------------------------------

_TRAIN_BRANDS = (r"double[- ]?deckers?|jan[- ]?shatabdi|shatabdi|rajdhani|duronto|garib[- ]?rath|vande[- ]?bharat|"
                 r"tejas|humsafar|gatimaan|sampark[- ]?kranti|antyodaya|uttaranchal|ashram|mandor|marudhar")
_TRAIN_BRAND_RE = re.compile(rf"\b({_TRAIN_BRANDS})\b(?:\s+express)?")
_TRAIN_NUMBER_RE = [re.compile(r"\b(?:train|express|no\.?|number|#)\s*(\d{5})\b"), re.compile(r"\b(\d{5})\s*(?:train|express)\b")]
_TRAIN_NAMED_RE = re.compile(r"\b(?:take|use|prefer|board|catch|book|travel (?:by|on)|go (?:by|on)|by|on)\s+(?:the\s+)?"
                             r"(?P<n>[a-z][a-z\- ]{2,30}?)\s+(?:express|exp|mail|special|superfast)\b")


def _place_alternatives(name: str) -> str:
    n = _norm(name).split(",")[0].strip()
    words = n.split()
    alts = {n, words[-1]} if words else set()
    return "|".join(re.escape(a) for a in sorted(alts, key=len, reverse=True) if a)


def _train_direction(clause: str, ctx: dict) -> str | None:
    o, d = _place_alternatives(ctx.get("origin") or ""), _place_alternatives(ctx.get("destination") or "")
    if o and d:
        if re.search(rf"\bfrom\s+(?:{o})\b.*?\bto\s+(?:{d})\b", clause):
            return "outbound"
        if re.search(rf"\bfrom\s+(?:{d})\b.*?\bto\s+(?:{o})\b", clause):
            return "return"
    if re.search(r"\b(?:return|returning|way back|coming back|back home|on the way back|wapas|vapas)\b", clause):
        return "return"
    if re.search(r"\b(?:going|outbound|onward|jaate|jate)\b", clause):
        return "outbound"
    return None


def _read_trains(clauses: list[tuple[str, str]], it: Intent, ctx: dict) -> None:
    """A named train is a preference for that train, not a switch to "trains only" (the other ways of travelling stay
    allowed, so the plan can still fall back when the train can't be used)."""
    directions: list[str | None] = []
    for _, c in clauses:
        found = [re.sub(r"[- ]+", " ", m.group(1)).rstrip("s") if m.group(1).startswith("double") else re.sub(r"[- ]+", " ", m.group(1))
                 for m in _TRAIN_BRAND_RE.finditer(c)]
        for rx in _TRAIN_NUMBER_RE:
            found += [m.group(1) for m in rx.finditer(c)]
        m = _TRAIN_NAMED_RE.search(c)
        if m and not found and m.group("n").strip() not in _STOP_NAME:
            found.append(m.group("n").strip() + " express")
        if not found:
            continue
        before = c[:_TRAIN_BRAND_RE.search(c).start()] if _TRAIN_BRAND_RE.search(c) else ""
        if _NEG_BEFORE_MODE.search(before):
            it.unsupported.append("I can't rule out a particular train yet. Tell me the one you'd rather take instead, "
                                  "for example “take the Shatabdi”.")
            continue
        it.train_names += found
        directions.append(_train_direction(c, ctx))
    it.train_names = _dedupe(it.train_names)[:3]
    if it.train_names:
        # naming a train says nothing about dropping the other ways of travelling
        it.transport_only = [m for m in it.transport_only if m != "train"]
        it.transport_add = [m for m in it.transport_add if m != "train"]
        known = {d for d in directions if d}
        it.train_direction = known.pop() if len(known) == 1 else None


# --- places and interests --------------------------------------------------------------------------------

_FILLER = {"the", "of", "and", "in", "at", "ka", "ki", "ke", "a", "an"}
_GENERIC = {"fort", "palace", "temple", "mandir", "tomb", "gate", "park", "garden", "lake", "bagh", "mahal", "museum",
            "market", "bazaar", "bazar", "ghat", "masjid", "mosque", "church", "gurudwara", "beach", "hill", "point",
            "view", "road", "street", "square", "chowk", "circle", "national", "old", "new", "city", "grand", "royal",
            "main", "restaurant", "cafe", "hotel", "kitchen", "house"}
_STOP_NAME = {"more", "some", "something", "anything", "everything", "it", "this", "that", "them", "those", "these",
              "one", "two", "three", "places", "place", "things", "stuff", "time", "day", "days", "night", "hotel",
              "hotels", "food", "lunch", "dinner", "breakfast", "snacks", "shopping", "budget", "cheap", "cheaper",
              "relaxing", "relax", "fun", "sightseeing", "extra", "another", "other", "new", "good", "best", "nice",
              "famous", "local", "popular", "early", "late", "earlier", "later", "less", "fewer", "stops", "stop",
              "hour", "hours", "minutes", "free", "rest", "sleep", "metro", "bus", "train", "cab", "taxi", "flight",
              "car", "walk", "walking", "sunrise", "sunset", "morning", "evening", "afternoon", "today", "tomorrow",
              "restaurants", "restaurant", "cafe", "cafes", "plan", "trip", "itinerary", "stay", "people", "me", "us",
              "my", "our", "you", "i", "we", "the", "a", "an", "to", "of", "and"}
_NON_PLACE = {"veg", "nonveg", "non", "khana", "kharcha", "paisa", "budget", "hotel", "stay", "train", "bus", "cab",
              "metro", "flight", "lunch", "dinner", "breakfast", "relaxed", "hai", "hain", "se", "ko", "ka", "ki", "ke",
              "me", "mein", "pe", "par", "tak", "jagah", "jagahen", "instead", "more", "less", "zyada", "jyada", "kam",
              "sasta", "aaram", "karo", "kar", "do", "dena", "dijiye", "kijiye", "lena", "lo", "ho", "hoga", "na", "to",
              "hi", "bhi", "sights", "sight", "tourist", "attractions", "landmarks", "spots", "chahiye", "plan", "trip"}
_ADD_CUE = (r"add|include|visit|see|seeing|go to|going to|goto|want to (?:see|visit|go to|go)|wanna (?:see|visit|go to)|"
            r"dekho|put|take (?:me|us) to|let'?s (?:go to|visit|see)|check out|explore|cover")
_NAME = r"(?P<name>[a-z0-9][a-z0-9'\-\. ]{1,48}?)"
_NAME_END = (r"(?=\s+(?:also|too|please|pls|plz|bhi|ko|in|on|at|for|near|around|during|after|before|from|if|so|because|"
             r"as|when|with|there|here|today|tomorrow)\b|\s*$)")
_NAME_AFTER_CUE = re.compile(rf"\b(?:{_ADD_CUE})\s+(?:the\s+|a\s+|an\s+)?{_NAME}{_NAME_END}")
_NAME_BEFORE_CUE = re.compile(rf"^(?:the\s+)?{_NAME}\s+(?:bhi\s+)?(?:dekhna|dekhni|dekhne|jana|jaana|jaane|ghoomna|"
                              rf"add\s*kar\w*|include\s*kar\w*|chahiye|dikha\w*)\b")
_NAME_AFTER_PLACEWORD = re.compile(rf"\b(?:at|near|around)\s+(?:the\s+)?{_NAME}{_NAME_END}")
_NAME_AFTER_SKIP = re.compile(rf"\b(?:skip|remove|drop|avoid|exclude|delete|cancel|hata\w*)\s+(?:the\s+)?{_NAME}{_NAME_END}")


def _name_tokens(name: str) -> list[str]:
    t = _norm(name)
    t = re.sub(r"\(.*?\)", " ", t).split(",")[0].split(" - ")[0]
    return [w.replace("'s", "").strip("'") for w in re.findall(r"[a-z0-9']+", t) if w not in _FILLER]


def _present(tok: str, words: list[str], wordset: set[str]) -> bool:
    if tok in wordset or tok + "s" in wordset or tok.rstrip("s") in wordset:
        return True
    if len(tok) >= 5:  # tolerate spelling variants: "itimad" for "itmad", "daulah" for "daula"
        return any(abs(len(w) - len(tok)) <= 2 and difflib.SequenceMatcher(None, tok, w).ratio() >= 0.84 for w in words)
    return False


def _match_places(clause: str, candidates: list[dict], uniq: dict[str, int], dest_tokens: set[str]) -> list[dict]:
    words = re.findall(r"[a-z0-9']+", clause)
    wordset = set(words)
    hits: list[tuple[float, int, dict]] = []
    for c in candidates:
        core = _name_tokens(c.get("name", ""))
        if not core:
            continue
        matched = [t for t in core if _present(t, words, wordset)]
        if len(matched) == len(core):
            hits.append((1.0, len(core), c))
            continue
        distinct = [t for t in matched if t not in _GENERIC and t not in dest_tokens and len(t) >= 3]
        if distinct and any(uniq.get(t, 0) == 1 for t in distinct):
            hits.append((len(matched) / len(core), len(core), c))
    if not hits:
        return []
    best = max(h[0] for h in hits)
    top = [h for h in hits if h[0] == best]
    fewest = min(h[1] for h in top) if best == 1.0 else 0  # exact matches: the shortest name is the closest
    return [h[2] for h in top if best < 1.0 or h[1] == fewest]


def _clean_name(raw: str, dest_tokens: set[str]) -> str | None:
    name = re.sub(r"\b(?:place|places|trip|plan|too|please)\b$", "", raw.strip(" .'-")).strip(" .'-")
    toks = re.findall(r"[a-z0-9']+", name)
    if not 1 <= len(toks) <= 5 or all(t in _STOP_NAME or t.isdigit() for t in toks) or set(toks) & _NON_PLACE:
        return None
    if _INTEREST_ANY.fullmatch(name) or set(toks) <= dest_tokens:
        return None
    if re.search(r"\d\s*(?:am|pm|baje|k\b|km|days?|people)", name) or _BUDGET_CUE.search(name):
        return None
    return name


def _means_something_else(orig: str, c: str, ctx: dict) -> bool:
    """Is this clause a time, an amount, a style... rather than the name of a place?"""
    probe = Intent()
    one = [(orig, c)]
    _read_times(one, c, probe)
    _read_budget(c, probe, ctx)
    _read_length(c, probe)
    _read_travellers(c, probe)
    _read_style_pace_food_stay(c, one, probe)
    _read_transport(one, probe)
    return probe.has_change() or bool(probe.needs)


def _read_places_and_interests(clauses: list[tuple[str, str]], candidates: list[dict], dest_tokens: set[str],
                               it: Intent, ctx: dict) -> None:
    uniq: dict[str, int] = {}
    for c in candidates:
        for tok in set(_name_tokens(c.get("name", ""))):
            uniq[tok] = uniq.get(tok, 0) + 1
    carried = None
    for orig, c in clauses:
        cue_neg, cue_pos = bool(_NEG_CORE.search(c)), bool(_POS.search(c))
        pol = "skip" if cue_neg else ("add" if cue_pos else (carried or "add"))
        if cue_neg or cue_pos:
            carried = pol
        hits = _match_places(c, candidates, uniq, dest_tokens)
        if not hits and not (cue_neg or cue_pos) and carried == "add" and it.add_place_names \
                and not _INTEREST_ANY.search(c) and not _means_something_else(orig, c, ctx):
            follow = _clean_name(c, dest_tokens)  # "...Sikandra and Mathura": no verb of its own, still a place
            if follow:
                it.add_place_names.append(follow)
                continue
        if hits:
            (it.exclude_place_ids if pol == "skip" else it.include_place_ids).extend(h["id"] for h in hits)
            continue
        named = False
        if pol == "skip":
            m = _NAME_AFTER_SKIP.search(c)
            name = _clean_name(m.group("name"), dest_tokens) if m else None
            if name:
                it.unknown_skips.append(name)
                continue
        else:
            names = []
            for pat in (_NAME_AFTER_CUE, _NAME_BEFORE_CUE):
                m = pat.search(c)
                if m:
                    names.append(m.group("name"))
            if names and _NAME_AFTER_CUE.search(c):  # "add shopping at Sadar Bazaar": the place follows "at"
                m = _NAME_AFTER_PLACEWORD.search(c)
                if m:
                    names.append(m.group("name"))
            cleaned = [n for n in (_clean_name(raw, dest_tokens) for raw in names) if n]
            for name in cleaned:
                if not any(o != name and o in name for o in cleaned):  # "visit sikandra" is just "sikandra"
                    it.add_place_names.append(name)
                    named = True
            for name in cleaned:  # "Sikandra Fort" is a place; the word "fort" in it is not a wish for more forts
                c = c.replace(name, " ")
            if named and not _INTEREST_ANY.search(c):
                continue
        swap = re.search(r"\b(?:swap|replace|change)\b.*?\b(?:for|with|by|to)\b", c)
        for rx, tags, label in _INTEREST_RES:
            found = rx.search(c)
            if found:
                if swap:
                    gone = found.start() < re.search(r"\b(?:for|with|by|to)\b", c[swap.start():]).start() + swap.start()
                    (it.avoid_tags if gone else it.interests).extend(tags)
                elif cue_neg or _NEG_REDUCE.search(c):
                    it.avoid_tags.extend(tags)
                else:
                    it.interests.extend(tags)
                for tag in tags:
                    it.interest_labels[tag] = label
    it.include_place_ids = _dedupe(it.include_place_ids)
    it.exclude_place_ids = [i for i in _dedupe(it.exclude_place_ids) if i not in it.include_place_ids]
    it.add_place_names = _dedupe(it.add_place_names)[:3]
    it.unknown_skips = _dedupe(it.unknown_skips)[:3]
    it.interests = [t for t in _dedupe(it.interests) if t not in it.avoid_tags]
    it.avoid_tags = _dedupe(it.avoid_tags)


# --- asked for, but not something the planner can do -----------------------------------------------------

def _read_unsupported(t: str, it: Intent, ctx: dict) -> None:
    dest = ctx.get("destination") or "this destination"
    if re.search(r"\b(?:change|switch|different|another|badal\w*)\b.{0,12}\b(?:destination|city|place to go)\b", t) \
            or re.search(r"\binstead of (?:going to )?" + re.escape(_norm(dest).split(",")[0]) + r"\b", t) \
            or re.search(r"\b(?:go|going|travel|travelling|trip|jana|jaana)\b.{0,25}\binstead\b", t):
        it.unsupported.append(f"This trip is planned for {dest}. For a different destination, start a new trip with "
                              "“Plan a trip” — changes here keep the same place.")
    if re.search(r"\b(?:skip|no|without|nahi|cancel|remove)\s+(?:the\s+)?(?:lunch|dinner|breakfast|meals?)\b", t):
        it.unsupported.append("I can't take meals out of the plan yet; I can make them cheaper (“make it cheaper”) or "
                              "pick different places (“swap my lunch”).")
    if re.search(r"\b(?:book|booking|reserve|reservation|buy tickets?|pay)\b", t):
        it.unsupported.append("I plan the trip but can't book tickets or hotels; the plan shows each price and where "
                              "it comes from so you can book it yourself.")
    if re.search(r"\b(?:undo|revert|go back to (?:the )?(?:previous|old|earlier|first)|pehle jaisa|purana wala)\b", t):
        it.unsupported.append("I can't undo a change yet. Tell me what to set back — for example “budget 5000” or "
                              "“add Agra Fort”.")


# --- the whole reader ------------------------------------------------------------------------------------

_FILLER_CLAUSE = re.compile(r"^(?:please|pls|plz|ok|okay|thanks?|thank you|thx|hi|hello|hey|can you|could you|"
                            r"would you|kindly|just|so|well|actually|now|then|yes|yeah|sure|bhai|yaar|bro|sir|ji)\W*$")


def rule_intent(message: str, candidates: list[dict] | None = None, ctx: dict | None = None) -> Intent:
    """Read a request with plain rules. `candidates` are the places in the current plan's pool ({id, name, kind});
    `ctx` carries destination, travellers... so "per person" and "the destination" make sense."""
    it = _rule_core(message, candidates or [], ctx or {})
    # What did nothing recognise? Say so, instead of pretending the whole request was understood.
    for orig, c in _clauses(message):
        if len(c.split()) < 3 or _FILLER_CLAUSE.match(c):
            continue
        part = _rule_core(orig, candidates or [], ctx or {})
        if part.is_empty():
            it.ignored.append(orig[:80])
    it.ignored = _dedupe(it.ignored)[:2]
    return it


def _rule_core(message: str, candidates: list[dict], ctx: dict) -> Intent:
    it = Intent(source="rules")
    t = _norm(message)
    if not t:
        return it
    clauses = _clauses(message)
    dest_tokens = set(re.findall(r"[a-z0-9']+", _norm(ctx.get("destination") or "")))
    _read_length(t, it)
    _read_budget(t, it, ctx)
    _read_times(clauses, t, it)
    _read_travellers(t, it)
    _read_style_pace_food_stay(t, clauses, it)
    _read_transport(clauses, it)
    _read_trains(clauses, it, ctx)
    _read_places_and_interests(clauses, candidates, dest_tokens, it, ctx)
    _read_unsupported(t, it, ctx)
    return it


def keyword_intent(message: str, candidates: list[dict], ctx: dict | None = None) -> dict:
    """The rule-based reading as a plain dict (the shape the first version returned)."""
    return rule_intent(message, candidates, ctx).as_dict()


def interests_from_notes(notes: str | None) -> list[str]:
    """Interest tags in the free-text notes field (used for ranking weights); things the notes ask to avoid don't count."""
    if not notes:
        return []
    return rule_intent(notes, []).interests


# --------------------------------------------------------------------------------------------------------
# Language-model reader
# --------------------------------------------------------------------------------------------------------

INTENT_SYSTEM = f"""You read a traveller's request to change a trip that is already planned and turn it into a
structured change set. The request may be in English, Hindi or Hinglish, informal, short or vague. Always do your best:
a request can hold several changes; fill in every field that applies and leave the rest null or empty.
- Budget changes are {{"mode": "set|increase|decrease", "amount": number}} exactly as the traveller stated them; the
  backend does the arithmetic. "per person" amounts: multiply by the number of travellers yourself.
- Place ids may only come from candidate_places. To add a place that is NOT in that list ("also visit Fatehpur Sikri"),
  put its name in add_place_names. To skip a place, use its id in exclude_place_ids.
- interests / avoid_interests use only these tags: {", ".join(sorted(INTEREST_TAGS))}.
- start_time / end_time are 24-hour "HH:MM" (end_time is when the traveller wants to be back).
- travellers is the new total number of people. extend_days adds whole days ("one more day" = 1, "a day less" = -1);
  days_total sets the whole trip length ("make it 3 days").
- transport_modes lists the modes meant; transport_action says whether they replace the current ones ("only"), join
  them ("add") or are taken away ("remove").
- train_names: specific trains asked for by name or number ("Double Decker", "Shatabdi", "12985"); train_direction says
  which way ("outbound", "return", or null for both). Asking for a named train is NOT a reason to restrict
  transport_modes: leave the other modes alone.
- Allowed styles: premium, balanced, budget, romantic, adventure, sightseeing, food, relaxed. Allowed stay: premium,
  tier1, tier2, budget, oyo, hostel, no_stay. Allowed pace: relaxed, normal, packed. Allowed transport: train, bus,
  metro, cab, own_car, flight, mixed. Allowed food: veg, non_veg, both.
- If part of the request can't be expressed with these fields, still apply the rest. If none of it can, leave every
  field empty and put one friendly sentence in reply saying what you can change instead.
{FACT_RULE} Respond with JSON only."""


def _nullable(schema: dict) -> dict:
    return {"anyOf": [{"type": "null"}, schema]}


_STR_LIST = {"type": "array", "items": {"type": "string"}}
INTENT_SCHEMA = {
    "title": "trip_change", "type": "object", "additionalProperties": False,
    "required": ["budget_change", "days_total", "extend_days", "pace", "add_styles", "remove_styles", "food", "stay",
                 "transport_modes", "transport_action", "start_time", "end_time", "travellers", "traveller_type",
                 "exclude_place_ids", "include_place_ids", "add_place_names", "interests", "avoid_interests", "reply",
                 "train_names", "train_direction"],
    "properties": {
        "budget_change": _nullable({"type": "object", "additionalProperties": False, "required": ["mode", "amount"],
                                    "properties": {"mode": {"type": "string", "enum": ["set", "increase", "decrease"]},
                                                   "amount": {"type": "number"}}}),
        "days_total": _nullable({"type": "integer"}),
        "extend_days": {"type": "integer"},
        "pace": _nullable({"type": "string", "enum": ["relaxed", "normal", "packed"]}),
        "add_styles": _STR_LIST, "remove_styles": _STR_LIST,
        "food": _nullable({"type": "string", "enum": ["veg", "non_veg", "both"]}),
        "stay": _nullable({"type": "string"}),
        "transport_modes": _STR_LIST,
        "transport_action": _nullable({"type": "string", "enum": ["only", "add", "remove"]}),
        "start_time": _nullable({"type": "string"}), "end_time": _nullable({"type": "string"}),
        "travellers": _nullable({"type": "integer"}),
        "traveller_type": _nullable({"type": "string", "enum": ["solo", "couple", "partner", "friends", "family"]}),
        "exclude_place_ids": _STR_LIST, "include_place_ids": _STR_LIST, "add_place_names": _STR_LIST,
        "interests": _STR_LIST, "avoid_interests": _STR_LIST,
        "train_names": _STR_LIST,
        "train_direction": _nullable({"type": "string", "enum": ["outbound", "return", "both"]}),
        "reply": {"type": "string"},
    },
}

_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _from_model(data: dict, candidates: list[dict]) -> Intent:
    """A model's JSON, checked field by field: anything outside the allowed vocabulary is dropped."""
    ids = {c["id"] for c in candidates}
    it = Intent(source="ai")
    bc = data.get("budget_change")
    if isinstance(bc, dict) and bc.get("mode") in ("set", "increase", "decrease"):
        try:
            amount = float(bc.get("amount"))
            if 0 < amount < 10_000_000:
                it.budget_change = {"mode": bc["mode"], "amount": amount}
        except (TypeError, ValueError):
            pass
    for name, lo, hi in (("days_total", 1, 8), ("travellers", 1, 20)):
        try:
            v = int(data.get(name)) if data.get(name) is not None else None
            if v is not None and lo <= v <= hi:
                setattr(it, name, v)
        except (TypeError, ValueError):
            pass
    try:
        it.extend_days = max(-6, min(6, int(data.get("extend_days") or 0)))
    except (TypeError, ValueError):
        it.extend_days = 0
    if data.get("pace") in PACES:
        it.pace = data["pace"]
    it.add_styles = [s for s in data.get("add_styles") or [] if s in STYLES]
    it.remove_styles = [s for s in data.get("remove_styles") or [] if s in STYLES]
    if data.get("food") in ("veg", "non_veg", "both"):
        it.food = data["food"]
    if data.get("stay") in STAYS:
        it.stay = data["stay"]
    modes = [m for m in data.get("transport_modes") or [] if m in MODES]
    action = data.get("transport_action") or "only"
    {"add": it.transport_add, "remove": it.transport_remove}.get(action, it.transport_only).extend(modes)
    for name in ("start_time", "end_time"):
        v = str(data.get(name) or "").strip()
        m = _HHMM.match(v)
        if m:
            setattr(it, name, f"{int(m.group(1)):02d}:{m.group(2)}")
    if data.get("traveller_type") in TRAVELLER_TYPES:
        it.traveller_type = data["traveller_type"]
    it.exclude_place_ids = [i for i in data.get("exclude_place_ids") or [] if i in ids]
    it.include_place_ids = [i for i in data.get("include_place_ids") or [] if i in ids]
    it.add_place_names = [str(n).strip()[:80] for n in data.get("add_place_names") or [] if str(n).strip()][:3]
    it.train_names = [str(n).strip()[:60] for n in data.get("train_names") or [] if str(n).strip()][:3]
    it.train_direction = data["train_direction"] if data.get("train_direction") in ("outbound", "return") else None
    it.interests = [t for t in data.get("interests") or [] if t in INTEREST_TAGS]
    it.avoid_tags = [t for t in data.get("avoid_interests") or [] if t in INTEREST_TAGS]
    it.reply = (str(data.get("reply") or "").strip()[:300]) or None
    return it


def _merge(ai: Intent, rules: Intent) -> Intent:
    """The model's reading wins field by field; the rules fill in whatever it left empty."""
    out = Intent(source="ai")
    for name in ("budget_change", "days_total", "pace", "food", "stay", "start_time", "end_time", "travellers",
                 "traveller_type", "train_direction"):
        setattr(out, name, getattr(ai, name) if getattr(ai, name) is not None else getattr(rules, name))
    out.start_shift = rules.start_shift if not ai.start_time else 0  # "start an hour later": only the rules read it
    out.end_shift = rules.end_shift if not ai.end_time else 0
    out.extend_days = ai.extend_days or rules.extend_days
    out.travellers_add = rules.travellers_add if not ai.travellers else 0
    for name in ("add_styles", "remove_styles", "transport_only", "transport_add", "transport_remove",
                 "include_place_ids", "exclude_place_ids", "add_place_names", "unknown_skips", "interests",
                 "avoid_tags", "needs", "unsupported", "train_names"):
        setattr(out, name, _dedupe([*getattr(ai, name), *getattr(rules, name)]))
    out.interest_labels = {**rules.interest_labels, **ai.interest_labels}
    out.exclude_place_ids = [i for i in out.exclude_place_ids if i not in out.include_place_ids]
    out.interests = [t for t in out.interests if t not in out.avoid_tags]
    out.reply = ai.reply
    out.ignored = rules.ignored if not ai.has_change() else []
    return out


def understand(llm: LLMProvider, message: str, ctx: dict, candidates: list[dict], *, db: Session | None = None,
               user_id=None, trip_id=None) -> Intent:
    """Read `message`. Never raises: a model that is missing, slow or confused just means the rules answer alone."""
    rules = rule_intent(message, candidates, ctx)
    if not llm.live:
        return rules
    payload = {"request": message[:1000], "current_trip": ctx,
               "candidate_places": [{"id": c["id"], "name": c["name"], "kind": c.get("kind"),
                                     "in_plan": bool(c.get("planned"))} for c in candidates[:60]]}
    t0 = time.perf_counter()
    try:
        result = llm.complete([LLMMessage("system", INTENT_SYSTEM),
                               LLMMessage("user", json.dumps(payload, ensure_ascii=False, default=str))],
                              json_schema=INTENT_SCHEMA, temperature=0.0, max_tokens=700)
    except ProviderUnavailable as exc:
        log_usage(db, llm, "intent", user_id=user_id, trip_id=trip_id, error=str(exc),
                  latency_ms=int((time.perf_counter() - t0) * 1000))
        return rules
    except Exception:  # noqa: BLE001 - a model problem must never turn a request into an error
        log.warning("intent model call failed", exc_info=True)
        return rules
    log_usage(db, llm, "intent", user_id=user_id, trip_id=trip_id, result=result)
    if not result.data:
        return rules
    return _merge(_from_model(result.data, candidates), rules)
