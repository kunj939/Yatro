"""Opening-hours model: {"weekly": {"0": [["09:30","17:00"]], ...}} where 0 = Monday."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    h = int(h)
    return time(23, 59) if h >= 24 else time(h, int(m))


def intervals_for(hours: dict | None, day: date) -> list[tuple[datetime, datetime]] | None:
    """Return open intervals on `day`, [] if closed, None if unknown."""
    if not hours:
        return None
    special = (hours.get("special") or {}).get(day.isoformat())
    spans = special if special is not None else (hours.get("weekly") or {}).get(str(day.weekday()))
    if spans is None:
        return None
    out = []
    for o, c in spans:
        start = datetime.combine(day, parse_hhmm(o))
        end = datetime.combine(day, parse_hhmm(c))
        if end <= start:  # crosses midnight
            end += timedelta(days=1)
        out.append((start, end))
    return out


# When a place publishes no hours we only schedule it in ordinary daytime hours (never a market at 7 am).
UNKNOWN_OPEN, UNKNOWN_CLOSE = time(9, 0), time(21, 30)
# A monument, fort, garden or museum with no published hours closes around sunset, not at 21:30: the Taj Mahal was
# planned for 18:51-20:36 because its hours weren't listed. Markets, food streets and nightlife stay open later.
SIGHT_CLOSE = time(18, 0)
LATE_TAGS = {"market", "nightlife", "street_food", "food"}


def assumed_close(tags: list[str] | None) -> time:
    """When a sight with these tags is assumed to close if it publishes no hours."""
    return UNKNOWN_CLOSE if set(tags or []) & LATE_TAGS else SIGHT_CLOSE


def _assumed(day: date, close: time = UNKNOWN_CLOSE) -> list[tuple[datetime, datetime]]:
    return [(datetime.combine(day, UNKNOWN_OPEN), datetime.combine(day, close))]


def fits(hours: dict | None, day: date, start: datetime, end: datetime,
         close: time = UNKNOWN_CLOSE) -> tuple[bool, tuple[datetime, datetime] | None]:
    """Is [start, end] inside a single open interval? Unknown hours use a conservative daytime window, ending at
    `close` (see assumed_close() for sights)."""
    spans = intervals_for(hours, day)
    if spans is None:
        o, c = _assumed(day, close)[0]
        return (o <= start and end <= c), None
    for o, c in spans:
        if o <= start and end <= c:
            return True, (o, c)
    return False, None


def next_open(hours: dict | None, day: date, at: datetime,
              close: time = UNKNOWN_CLOSE) -> tuple[datetime, datetime] | None:
    spans = intervals_for(hours, day)
    if spans is None:
        spans = _assumed(day, close)
    for o, c in spans:
        if c > at:
            return (max(o, at), c)
    return None


def span_for_display(hours: dict | None, day: date, at: datetime | None = None) -> tuple[str | None, str | None]:
    spans = intervals_for(hours, day)
    if not spans:
        return None, None
    chosen = spans[0]
    if at is not None:
        for o, c in spans:
            if o <= at < c:
                chosen = (o, c)
                break
    return chosen[0].strftime("%H:%M"), chosen[1].strftime("%H:%M")


_TIME = r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?"
_RANGE = re.compile(_TIME + r"\s*(?:-|–|—|to|till|until)\s*" + _TIME, re.IGNORECASE)


def _to_24(h: str, m: str | None, ampm: str | None, fallback_pm: bool = False) -> str | None:
    hh, mm = int(h), int(m or 0)
    if hh > 24 or mm > 59:
        return None
    tag = (ampm or "").lower().replace(".", "")
    if tag == "pm" and hh < 12:
        hh += 12
    elif tag == "am" and hh == 12:
        hh = 0
    elif not tag and fallback_pm and hh < 8:
        hh += 12
    return f"{hh:02d}:{mm:02d}"


def extract_hours_from_text(text: str) -> tuple[str, str] | None:
    """Pull the first plausible opening range out of a search snippet, e.g. '9:00 AM to 5:00 PM'."""
    for m in _RANGE.finditer(text or ""):
        h1, m1, a1, h2, m2, a2 = m.groups()
        if not (a1 or a2 or m1 or m2):
            continue  # bare "9-5" style numbers are too ambiguous (could be dates/prices)
        o = _to_24(h1, m1, a1 or (a2 if a2 and a2.lower().startswith("a") else None))
        c = _to_24(h2, m2, a2, fallback_pm=not a2)
        if o and c and o < c:
            return o, c
    return None