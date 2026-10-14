"""A trip as a calendar (ADR 0015): every stop of the current plan as an event, served as an iCalendar feed.

The feed URL carries a secret token (like a share link) so Google Calendar, Apple Calendar or Outlook can subscribe
without signing in. They re-fetch it on their own schedule, so a re-planned trip updates in the calendar by itself.
Events are built from the same gated item output as the trip page, so the train and Google content rules (I1, I6)
hold here too: a train with no timetable becomes an all-day "timing estimated" event, never a made-up time.
"""
from __future__ import annotations

import secrets
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.models.trip import Trip
from app.utils.locale import locale_for
from app.utils.money import fmt_money

KIND_LABEL = {"transport": "", "activity": "", "meal": "", "sunset": "Sunset: ", "stay": "", "arrival": ""}
SKIP = {"free"}  # free time between stops is not an event


def ensure_token(trip: Trip) -> str:
    if not trip.calendar_token:
        trip.calendar_token = secrets.token_urlsafe(24)
    return trip.calendar_token


def links(feed_url: str) -> dict:
    """Ways to add the feed: subscribe (Google, Apple/iPhone, Outlook), or the plain https URL to paste anywhere."""
    webcal = "webcal://" + feed_url.split("://", 1)[1]
    return {"feed_url": feed_url, "webcal_url": webcal,
            "google_url": "https://calendar.google.com/calendar/render?cid=" + quote(webcal, safe=""),
            "outlook_url": "https://outlook.live.com/calendar/0/addfromweb?url=" + quote(feed_url, safe="")
                           + "&name=" + quote("Yaitro trip", safe="")}


def _esc(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545: lines longer than 75 octets continue on the next line after a space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n ".join(out)


def _utc(local: datetime, lat, lng, fallback_tz: str) -> str:
    """Item times are local wall-clock time where they happen; the place's own time zone turns them into UTC."""
    tz = locale_for(lat, lng)[0] if lat is not None and lng is not None else fallback_tz
    try:
        zone = ZoneInfo(tz)
    except Exception:  # an unknown zone name: the trip's own zone is the best guess
        zone = ZoneInfo(fallback_tz)
    return local.replace(tzinfo=zone).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _point(meta: dict, key: str | None = None):
    if key:
        v = meta.get(key)
        return (v[0], v[1]) if isinstance(v, list) and len(v) == 2 else (None, None)
    return meta.get("lat"), meta.get("lng")


def _description(item: dict, trip: Trip, app_url: str) -> str:
    meta = item.get("meta") or {}
    lines = []
    if item.get("subtitle"):
        lines.append(item["subtitle"])
    if item.get("opening_time") and item.get("closing_time"):
        lines.append(f"Open {item['opening_time']}–{item['closing_time']}")
    cost = (item.get("cost") or 0) + (item.get("travel_cost") or 0)
    if cost:
        lines.append(f"Cost: {fmt_money(cost, trip.currency)} for the group ({item.get('cost_state') or 'estimated'})")
    if meta.get("address"):
        lines.append(meta["address"])
    lat, lng = _point(meta)
    if lat is not None:
        lines.append(f"Directions: https://www.google.com/maps/dir/?api=1&destination={lat},{lng}")
    if meta.get("source") == "google":
        lines.append("Place details from Google Maps")
    lines.append(f"Full plan: {app_url}/trips/{trip.id}")
    return "\n".join(lines)


def ics(trip: Trip) -> str:
    from app.services import trip_service as TS  # the trip page's own gated output (I1, I6)

    s = get_settings()
    app_url = s.public_app_url.rstrip("/")
    it = trip.current_itinerary
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Yaitro//Trip plan//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
           f"X-WR-CALNAME:{_esc(f'{trip.title} (Yaitro)')}", f"X-WR-TIMEZONE:{trip.timezone or 'Asia/Kolkata'}",
           f"X-WR-CALDESC:{_esc(f'{trip.origin_name} to {trip.destination_name}, planned with Yaitro')}",
           "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
    days = TS.itinerary_out(it, trip)["days"] if it else []
    for day in days:
        for item in day["items"]:
            if item["kind"] in SKIP:
                continue
            meta = item.get("meta") or {}
            ev = ["BEGIN:VEVENT", f"UID:{item['id']}@yaitro", f"DTSTAMP:{stamp}"]
            if item.get("time"):
                start, end = datetime.fromisoformat(item["start_at"]), datetime.fromisoformat(item["end_at"])
                if end <= start:
                    end = start + timedelta(minutes=15)
                if item["kind"] == "transport":
                    (a_lat, a_lng), (b_lat, b_lng) = _point(meta, "from"), _point(meta, "to")
                else:
                    (a_lat, a_lng) = (b_lat, b_lng) = _point(meta)
                ev += [f"DTSTART:{_utc(start, a_lat, a_lng, trip.timezone)}", f"DTEND:{_utc(end, b_lat, b_lng, trip.timezone)}"]
            else:  # a time we can't stand behind (I1): the day only
                d = date.fromisoformat(item["start_at"][:10])
                ev += [f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}"]
            meal = meta.get("meal") if item["kind"] == "meal" else None
            label = f"{meal.capitalize()}: " if isinstance(meal, str) and meal else KIND_LABEL.get(item["kind"], "")
            ev.append(f"SUMMARY:{_esc(label + item['title'])}")
            # the place's name with its address, so a tap on the location in the calendar finds the right spot
            address = meta.get("address")
            if item["kind"] == "transport":
                where = meta.get("from_name") or item["title"]
            elif address and not address.startswith(item["title"]):
                where = f"{item['title']}, {address}"
            else:
                where = address or item["title"]
            ev.append(f"LOCATION:{_esc(where)}")
            lat, lng = _point(meta)
            if lat is not None:
                ev.append(f"GEO:{lat};{lng}")
            ev.append(f"DESCRIPTION:{_esc(_description(item, trip, app_url))}")
            ev.append(f"URL:{app_url}/trips/{trip.id}")
            # a reminder where missing it costs most: an hour before a train, bus or flight; half an hour before check-in
            alarm = "-PT60M" if item["kind"] == "transport" and not meta.get("transfer") else "-PT30M" if item["kind"] == "stay" else None
            if alarm and item.get("time"):
                ev += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(item['title'])}", f"TRIGGER:{alarm}", "END:VALARM"]
            ev.append("END:VEVENT")
            out += ev
    out.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in out) + "\r\n"
