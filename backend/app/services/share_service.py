"""Share links, the public share view and the WhatsApp text (ADR 0006, Part A).

I8: the public view is built from an allow-list (a fresh dict of named fields), never by deleting fields from the owner's
view, so a field added later cannot leak. It holds no emails, phones, member budgets, UPI IDs or trip budget (the budget
only when the owner turned "Show budget" on, and the split only when they chose "Plan + split").

I6: everything goes through the same gates as the owner's view, so Google content older than 30 days is never in a share
page or message. The WhatsApp text is generated at the moment of sharing and is not stored.
I9: nothing here sends a message. The user sends the text from their own WhatsApp.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import quote

from sqlalchemy import select

from app.core.config import get_settings
from app.models.share import ShareLink  # noqa: F401 (re-exported to the routes)
from app.services import expense_service, google_content
from app.services import trip_service as TS
from app.utils.money import fmt_money

EXPIRY_AFTER_TRIP = timedelta(days=30)
ATTRIBUTION = "Place details from Google Maps"
TEXT_LIMIT = 3500  # WhatsApp takes far more; this keeps a message readable
_ITEM_KEYS = ("id", "day_index", "kind", "start_at", "end_at", "time", "end_time", "duration_minutes", "title", "subtitle",
              "cost", "cost_state", "travel_mode", "travel_minutes_from_prev", "distance_from_prev_m", "travel_cost",
              "opening_time", "closing_time", "explanation")
_META_KEYS = ("mode", "service_class", "number", "operator", "from_name", "to_name", "changes", "change_at",
              "change_minutes", "when", "fare_label", "fare_note", "provenance_label", "provenance_warning", "freshness",
              "google_expired", "maps_url", "address", "rating", "rating_count", "meal", "why_go", "source", "wiki_links",
              "depart_local", "arrive_local")
_LEG_KEYS = ("name", "number", "service_class", "from_name", "to_name", "depart_at", "arrive_at", "duration_minutes",
             "depart_local", "arrive_local")


# --- Links ------------------------------------------------------------------------------------------------------------

def _hash(token: str) -> str:
    return hmac.new(get_settings().jwt_secret.encode(), f"share:{token}".encode(), hashlib.sha256).hexdigest()


def public_url(token: str) -> str:
    return f"{get_settings().public_app_url.rstrip('/')}/s/{token}"


def default_expiry(trip) -> datetime:
    """30 days after the trip's last day (end of that day, UTC): by then nobody needs the plan, and the Google details
    behind it would cost money to refresh for nothing."""
    return datetime.combine(trip.end_date, time(23, 59, 59), tzinfo=UTC) + EXPIRY_AFTER_TRIP


def _aware(dt: datetime | None) -> datetime | None:
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=UTC))


def create_link(db, trip, user, *, show_budget: bool, include_split: bool, never_expires: bool) -> tuple[ShareLink, str]:
    token = secrets.token_urlsafe(32)  # 256 bits; only its keyed hash is stored
    link = ShareLink(trip_id=trip.id, created_by=user.id, token_hash=_hash(token), token_prefix=token[:6],
                     show_budget=show_budget, include_split=include_split,
                     expires_at=None if never_expires else default_expiry(trip))
    db.add(link)
    db.commit()
    return link, token


def is_active(link: ShareLink, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    return link.revoked_at is None and (link.expires_at is None or _aware(link.expires_at) > now)


def find_active(db, token: str, now: datetime | None = None) -> ShareLink | None:
    """The link for a token, or None: unknown, revoked and expired are indistinguishable to the caller."""
    if not token or len(token) > 128:
        return None
    link = db.scalar(select(ShareLink).where(ShareLink.token_hash == _hash(token)))
    return link if link is not None and is_active(link, now) else None


def link_out(link: ShareLink) -> dict:
    return {"id": str(link.id), "prefix": link.token_prefix, "created_at": link.created_at.isoformat(),
            "expires_at": _aware(link.expires_at).isoformat() if link.expires_at else None,
            "show_budget": link.show_budget, "include_split": link.include_split, "view_count": link.view_count,
            "last_viewed_at": _aware(link.last_viewed_at).isoformat() if link.last_viewed_at else None,
            "active": is_active(link)}


def active_links(db, trip) -> list[ShareLink]:
    rows = db.scalars(select(ShareLink).where(ShareLink.trip_id == trip.id, ShareLink.revoked_at.is_(None))
                      .order_by(ShareLink.created_at.desc())).all()
    return [r for r in rows if is_active(r)]


def extend(link: ShareLink, days: int, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    base = max(now, _aware(link.expires_at) or now)
    link.expires_at = base + timedelta(days=days)


# --- The public view ----------------------------------------------------------------------------------------------------

def _leg(leg: dict) -> dict:
    return {k: leg.get(k) for k in _LEG_KEYS}  # no coordinates


def _item(i: dict) -> dict:
    out = {k: i.get(k) for k in _ITEM_KEYS}
    meta = {k: i["meta"][k] for k in _META_KEYS if k in (i.get("meta") or {})}
    if (i.get("meta") or {}).get("legs"):
        meta["legs"] = [_leg(leg) for leg in i["meta"]["legs"]]
    out["meta"] = meta
    return out


_SENTENCE = re.split


def _without_budget(text: str | None, trip, remaining: float | None) -> str | None:
    """The plan summary says "Planned ₹4,584 of your ₹7,321 budget, leaving ₹2,737." Without "Show budget" that sentence
    (and any other that mentions the budget or either amount) is dropped, so the link can't reveal what someone can spend."""
    if not text:
        return text
    amounts = [fmt_money(v, trip.currency) for v in (trip.budget, remaining) if v is not None]
    keep = [s for s in _SENTENCE(r"(?<=[.!?])\s+", text)
            if not re.search(r"budget|leaving|left\b|remaining", s, re.IGNORECASE) and not any(a in s for a in amounts)]
    return " ".join(keep) or None


def share_view(trip, link: ShareLink) -> dict:
    """The public view of a trip, from an allow-list. `trip` must be loaded and (if stale) refreshed."""
    it = trip.current_itinerary
    full = TS.itinerary_out(it, trip)  # already through the I1 and I6 gates
    days = [{"index": d["index"], "date": d["date"], "label": d["label"], "items": [_item(i) for i in d["items"]]}
            for d in full["days"]]
    stamped = [i.google_fetched_at for i in it.items if i.google_fetched_at is not None]
    valid_until = google_content.valid_until(min(stamped, key=google_content._aware)) if stamped else None
    n = max(1, trip.travellers)
    summary, tips = full["summary"], full["tips"]
    if not link.show_budget:
        summary = _without_budget(summary, trip, full["remaining"])
        tips = [t for t in (_without_budget(t, trip, full["remaining"]) for t in tips) if t]
    view = {
        "trip": {"title": trip.title, "destination": trip.destination_name, "start_date": trip.start_date.isoformat(),
                 "end_date": trip.end_date.isoformat(), "travellers": trip.travellers, "currency": trip.currency},
        "itinerary": {"title": full["title"], "summary": summary, "tips": tips, "days": days,
                      "total_cost": full["total_cost"], "cost_per_person": round((full["total_cost"] or 0) / n, 2)},
        "google": {"attribution": ATTRIBUTION if stamped else None,
                   "valid_until": valid_until.isoformat() if valid_until else None},
    }
    if link.show_budget:
        view["budget"] = {"budget": float(trip.budget), "remaining": full["remaining"]}
    if link.include_split:
        s = expense_service.summary(trip)
        names = {m["member_id"]: m["name"] for m in s["members"]}
        view["split"] = {
            "total": s["total"],
            "members": [{"name": m["name"], "paid": m["paid"], "share": m["share"], "net": m["net"]} for m in s["members"]],
            "settlements": [{"from": x["from_name"], "to": x["to_name"], "amount": x["amount"]} for x in s["settlements"]],
            "expenses": [{"description": e.description, "amount": float(e.amount), "paid_by": names.get(str(e.paid_by_member_id))}
                         for e in trip.expenses]}
    return view


# --- WhatsApp text ----------------------------------------------------------------------------------------------------

def _estimated(item: dict) -> bool:
    label = (item.get("meta") or {}).get("fare_label")
    if label:
        return label != "Verified fare"
    return item.get("cost_state") not in ("verified_official", "verified_provider")


def _money_tag(item: dict, currency: str) -> str:
    if not item.get("cost"):
        return ""
    if item.get("cost_state") == "demo":
        return f" · {fmt_money(item['cost'], currency)} (sample)"
    return f" · {fmt_money(item['cost'], currency)}" + (" (est.)" if _estimated(item) else "")


_MODE_EMOJI = {"train": "🚆", "bus": "🚌", "flight": "✈️", "cab": "🚕", "own_car": "🚗", "metro": "🚇", "walk": "🚶"}
_MEAL_EMOJI = {"breakfast": "🥞", "lunch": "🍛", "dinner": "🍽️", "snack": "☕"}
_KIND_EMOJI = {"activity": "📍", "meal": "🍽️", "sunset": "🌇", "stay": "🏨", "arrival": "🏠", "free": "☕", "transport": "🚆"}
# a fitting emoji for a sight, from its name: forts and palaces, temples, museums, gardens, lakes, markets
_PLACE_EMOJI = (("🏰", ("fort", "qila", "palace", "mahal", "castle")), ("🛕", ("temple", "mandir", "gurudwara", "church", "mosque", "masjid", "dargah")),
                ("🏛️", ("museum", "gallery", "memorial", "monument")), ("🌳", ("garden", "bagh", "park")), ("🌊", ("lake", "jheel", "beach", "ghat", "river")),
                ("🛍️", ("market", "bazaar", "bazar", "mall", "chowk")), ("🦁", ("zoo", "safari", "sanctuary")), ("⛰️", ("hill", "point", "peak", "trek")))


def item_emoji(i: dict) -> str:
    """One emoji for a stop, so a day reads at a glance in WhatsApp or an email."""
    meta = i.get("meta") or {}
    if i["kind"] == "transport":
        return _MODE_EMOJI.get(meta.get("mode") or "", _KIND_EMOJI["transport"])
    if i["kind"] == "meal":
        return _MEAL_EMOJI.get(meta.get("meal") or "", _KIND_EMOJI["meal"])
    if i["kind"] == "activity":
        name = (i.get("title") or "").lower()
        for emoji, words in _PLACE_EMOJI:
            if any(w in name for w in words):
                return emoji
    return _KIND_EMOJI.get(i["kind"], "📍")


def _item_line(i: dict, currency: str) -> str:
    when = i.get("time") or (i.get("meta") or {}).get("when") or ""
    title = i["title"]
    if when and title.startswith(f"{when} "):
        title = title[len(when) + 1:]  # "06:40 train to Jaipur" under "06:40": the time is already in front
    if i["kind"] == "transport" and i.get("subtitle"):
        title = f"{title} — {i['subtitle']}"
    return f"{item_emoji(i)} {when}  {title}{_money_tag(i, currency)}".strip()


def _day_label(d: dict) -> str:
    return f"{d['label']} · {date.fromisoformat(d['date']):%a %d %b}"


def _short(text: str, n: int = 130) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def share_text(view: dict, url: str, limit: int = TEXT_LIMIT) -> str:
    """WhatsApp-formatted text from the public view (so it carries exactly what the share page carries): a bold title, a banner per
    day, an emoji per stop, the total and a few tips. Place names are included, from data refreshed within the 30 days. No AI, nothing
    stored. A long trip is shortened to fit `limit`: tips go first, then the last days become one line each."""
    t, it = view["trip"], view["itinerary"]
    cur = t["currency"]
    d0, d1 = date.fromisoformat(t["start_date"]), date.fromisoformat(t["end_date"])
    span = f"{d0:%a %d %b}" if d0 == d1 else f"{d0:%d %b}–{d1:%d %b}"
    who = f"{t['travellers']} traveller{'s' if t['travellers'] != 1 else ''}"
    head = [f"*🧳 {it['title'] or t['title']}* · {span} · {who}", "_Planned with Yaitro_ ✨", ""]
    items = [i for d in it["days"] for i in d["items"]]
    tips = [_short(x) for x in (it.get("tips") or [])[:3]]

    def tail_for(with_tips: bool) -> list[str]:
        tail = []
        if it["total_cost"]:
            each = f" ({fmt_money(it['cost_per_person'], cur)} each)" if t["travellers"] > 1 else ""
            tail.append(f"💰 *Total ≈ {fmt_money(it['total_cost'], cur)}*{each}")
            if any(i["cost"] and _estimated(i) for i in items):
                tail.append("_Costs marked (est.) are estimates._")
        if "budget" in view:
            tail.append(f"👛 Budget {fmt_money(view['budget']['budget'], cur)} · left {fmt_money(view['budget']['remaining'], cur)}")
        if with_tips and tips:
            tail += ["", "💡 *Good to know*", *[f"• {x}" for x in tips]]
        tail += ["", f"🔗 Full plan: {url}"]
        if view["google"]["attribution"]:
            tail.append(f"_{view['google']['attribution']}_")
        return tail

    def render(collapsed_from: int, first_day_limit: int | None = None, with_tips: bool = True) -> str:
        body = []
        for n, d in enumerate(it["days"]):
            shown = [i for i in d["items"] if i["kind"] != "free"]
            if n >= collapsed_from:
                body += [f"📅 *{_day_label(d)}* — {len(shown)} stops, see the link", ""]
                continue
            more = []
            if n == 0 and first_day_limit is not None and len(shown) > first_day_limit:
                more = [f"…and {len(shown) - first_day_limit} more, see the link"]
                shown = shown[:first_day_limit]
            body += [f"━━━━━━━━━━━━━━", f"📅 *{_day_label(d)}*", "━━━━━━━━━━━━━━", *[_item_line(i, cur) for i in shown], *more, ""]
        return "\n".join([*head, *body, *tail_for(with_tips)]).strip()
    text = render(len(it["days"]))
    if len(text) > limit:
        text = render(len(it["days"]), with_tips=False)  # tips are the first thing to go
    cut = len(it["days"])
    while len(text) > limit and cut > 1:  # a long trip: the last days become one line each
        cut -= 1
        text = render(cut, with_tips=False)
    first = it["days"][0]["items"] if it["days"] else []
    shown = len([i for i in first if i["kind"] != "free"])
    while len(text) > limit and shown > 1:  # even one day is too long: its last stops become "and N more"
        shown = int(shown * 0.8)
        text = render(cut, shown, with_tips=False)
    return text

def card_text(view: dict, url: str) -> str:
    """The WhatsApp message: two lines and the link. WhatsApp turns the link into a card (the share page's preview image,
    app/s/[token]/opengraph-image.tsx: title, dates, cost and a colour strip of the days), so the chat shows a card to
    tap, not a wall of text. The full day-by-day text is still there for copying (share_text).

    No emoji at all: WhatsApp Desktop on Windows turns every emoji in a pre-filled wa.me message into "�" (📅 and ✈ alike),
    while plain punctuation (· – → ₹) comes through. The card carries the colour."""
    t, it = view["trip"], view["itinerary"]
    d0, d1 = date.fromisoformat(t["start_date"]), date.fromisoformat(t["end_date"])
    span = f"{d0:%a %d %b}" if d0 == d1 else f"{d0:%d %b} – {d1:%d %b}"
    facts = [span, f"{t['travellers']} traveller{'s' if t['travellers'] != 1 else ''}"]
    if it["total_cost"]:
        facts.append(fmt_money(it["total_cost"], t["currency"]))
    return "\n".join([f"*{it['title'] or t['title']}*", " · ".join(facts), "", f"Full day-by-day plan → {url}"])


def wa_link(text: str, phone: str | None = None) -> str:
    """wa.me link with the text ready to send. Without a number the person picks the chat. With a number (only ever the signed-in
    person's OWN saved mobile) it opens their chat with that number, so the plan is one tap from being in their own WhatsApp.
    Either way it only OPENS WhatsApp with the message typed: the person taps Send, and Yaitro sends nothing itself (I9)."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    return (f"https://wa.me/{digits}" if digits else "https://wa.me/") + "?text=" + quote(text, safe="")
