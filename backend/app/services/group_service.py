"""Plan together (ADR 0022): the invite link, the replies, the results, and "Make the plan".

The link is the group's id plus a keyed signature, so the organiser can always see and re-share it while a guessed or
altered link opens nothing. Friends need no account: the browser keeps a random key, and only a keyed hash of it is
stored, so one browser has one reply that it can edit.
"""
from __future__ import annotations

import hashlib
import hmac
import uuid
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.models.group import GroupPlan, GroupPlanOption, GroupPlanReply
from app.models.trip import TripMember
from app.models.user import User
from app.schemas.trip import LocationIn, TripCreate
from app.services import trip_service as TS
from app.utils.locale import locale_for

MAX_REPLIES = 20
MAX_OPEN = 10
LINK_DAYS = 14
KEEP_DAYS = 30  # replies are kept this long after the plan is made or the link runs out


# ------------------------------------------------------------------------------------------------------ the link

def _sign(group_id: uuid.UUID) -> str:
    return hmac.new(get_settings().jwt_secret.encode(), f"group:{group_id}".encode(), hashlib.sha256).hexdigest()[:20]


def token_for(g: GroupPlan) -> str:
    return f"{g.id.hex}{_sign(g.id)}"


def public_url(g: GroupPlan) -> str:
    return f"{get_settings().public_app_url.rstrip('/')}/g/{token_for(g)}"


def find_by_token(db: Session, token: str) -> GroupPlan:
    try:
        gid = uuid.UUID(hex=token[:32])
    except (ValueError, TypeError):
        raise NotFound("This group link doesn't work. Ask for a new one.") from None
    if not hmac.compare_digest(token[32:], _sign(gid)):
        raise NotFound("This group link doesn't work. Ask for a new one.")
    g = load(db, gid)
    return g


def device_hash(g: GroupPlan, key: str) -> str:
    return hmac.new(get_settings().jwt_secret.encode(), f"group-device:{g.id}:{key}".encode(), hashlib.sha256).hexdigest()


def load(db: Session, gid: uuid.UUID, owner: User | None = None) -> GroupPlan:
    g = db.scalar(select(GroupPlan).where(GroupPlan.id == gid)
                  .options(selectinload(GroupPlan.options), selectinload(GroupPlan.replies))
                  .execution_options(populate_existing=True))
    if g is None or (owner is not None and g.owner_id != owner.id):
        raise NotFound("Group plan not found.")
    return g


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def is_open(g: GroupPlan, now: datetime | None = None) -> bool:
    return g.status == "open" and _aware(g.expires_at) > (now or datetime.now(UTC))


# ------------------------------------------------------------------------------------------------------ create

def create(db: Session, user: User, data) -> GroupPlan:
    today = datetime.now(UTC).date()
    if data.window_start < today:
        raise AppError("Pick dates from today on.", code="past_dates", status_code=422)
    if data.window_end < data.window_start or (data.window_end - data.window_start).days > 90:
        raise AppError("The date window should be at most three months.", code="bad_window", status_code=422)
    if (data.window_end - data.window_start).days + 1 < data.days:
        raise AppError(f"A {data.days}-day trip doesn't fit between those dates.", code="bad_window", status_code=422)
    open_now = db.scalar(select(func.count()).select_from(GroupPlan)
                         .where(GroupPlan.owner_id == user.id, GroupPlan.status == "open",
                                GroupPlan.expires_at > datetime.now(UTC))) or 0
    if open_now >= MAX_OPEN:
        raise AppError("You have 10 group plans waiting for replies. Make or close one first.", code="too_many_groups",
                       status_code=429)
    names = [o.name.strip() for o in data.options]
    if len({n.lower() for n in names}) != len(names):
        raise AppError("Each place can be an option only once.", code="duplicate_option", status_code=422)
    last = datetime.combine(data.window_end, time(23, 59), tzinfo=UTC)
    g = GroupPlan(owner_id=user.id, title=(data.title or f"{_first(user.name)}'s group trip")[:120],
                  origin_name=data.origin.name, origin_place_id=data.origin.place_id, origin_lat=data.origin.lat,
                  origin_lng=data.origin.lng, window_start=data.window_start, window_end=data.window_end, days=data.days,
                  budget_hint=data.budget_hint, stay=data.stay, note=(data.note or None),
                  status="open", expires_at=min(datetime.now(UTC) + timedelta(days=LINK_DAYS), last))
    g.options = [GroupPlanOption(position=n, name=o.name.strip()[:255], place_id=o.place_id, lat=o.lat, lng=o.lng)
                 for n, o in enumerate(data.options)]
    db.add(g)
    db.flush()
    return g


def _first(name: str) -> str:
    return (name or "").strip().split(" ")[0] or "A friend"


# ------------------------------------------------------------------------------------------------------ replies

def upsert_reply(db: Session, g: GroupPlan, data, user: User | None) -> GroupPlanReply:
    if not is_open(g):
        raise AppError("This group plan isn't taking replies any more.", code="group_closed", status_code=409)
    dh = device_hash(g, data.device)
    mine = next((r for r in g.replies if r.device_hash == dh), None)
    if mine is None and len(g.replies) >= MAX_REPLIES:
        raise AppError("This group already has 20 replies.", code="group_full", status_code=409)
    valid_days = {(g.window_start + timedelta(days=n)).isoformat() for n in range((g.window_end - g.window_start).days + 1)}
    dates = sorted({d.isoformat() for d in data.dates if d.isoformat() in valid_days})
    option_ids = {str(o.id) for o in g.options}
    votes = [v for v in dict.fromkeys(str(v) for v in data.votes) if v in option_ids]
    if len(g.options) == 1:
        votes = [str(g.options[0].id)]
    is_owner = user is not None and user.id == g.owner_id
    if mine is None:
        mine = GroupPlanReply(group_id=g.id, device_hash=dh)
        db.add(mine)
        g.replies.append(mine)
    mine.name, mine.budget, mine.food = data.name.strip()[:60], data.budget, data.food
    mine.dates, mine.votes, mine.is_owner = dates, votes, is_owner or mine.is_owner
    db.flush()
    return mine


def remove_reply(db: Session, g: GroupPlan, reply_id: uuid.UUID) -> None:
    r = next((x for x in g.replies if x.id == reply_id), None)
    if r is None:
        raise NotFound("Reply not found.")
    g.replies.remove(r)
    db.delete(r)


# ------------------------------------------------------------------------------------------------------ results

def best_dates(g: GroupPlan, top: int = 3) -> list[dict]:
    """Every start day whose whole trip fits in the window, scored by how many people can do all of it."""
    with_dates = [set(r.dates or []) for r in g.replies if r.dates]
    out = []
    span = (g.window_end - g.window_start).days + 1
    for s in range(span - g.days + 1):
        start = g.window_start + timedelta(days=s)
        need = {(start + timedelta(days=k)).isoformat() for k in range(g.days)}
        fits = sum(1 for ds in with_dates if need <= ds)
        out.append({"start": start.isoformat(), "end": (start + timedelta(days=g.days - 1)).isoformat(),
                    "fits": fits, "of": len(with_dates)})
    out.sort(key=lambda x: (-x["fits"], x["start"]))
    return [x for x in out if x["fits"] > 0][:top] if with_dates else []


def day_counts(g: GroupPlan) -> list[dict]:
    """How many people can do each day of the window: the heat strip."""
    c = Counter(d for r in g.replies for d in (r.dates or []))
    span = (g.window_end - g.window_start).days + 1
    return [{"date": (g.window_start + timedelta(days=n)).isoformat(),
             "count": c.get((g.window_start + timedelta(days=n)).isoformat(), 0)} for n in range(span)]


def tally(g: GroupPlan) -> list[dict]:
    votes = Counter(v for r in g.replies for v in (r.votes or []))
    return [{"id": str(o.id), "name": o.name, "votes": votes.get(str(o.id), 0), "lat": o.lat, "lng": o.lng}
            for o in g.options]


def winner(g: GroupPlan) -> GroupPlanOption:
    counts = {t["id"]: t["votes"] for t in tally(g)}
    return max(g.options, key=lambda o: (counts.get(str(o.id), 0), -o.position))


def food_for(g: GroupPlan) -> str:
    """Veg if everyone is; otherwise both, which keeps a vegetarian choice at every meal stop."""
    foods = [r.food for r in g.replies]
    if foods and all(f == "veg" for f in foods):
        return "veg"
    if foods and all(f == "non_veg" for f in foods):
        return "non_veg"
    return "both"


def group_budget(g: GroupPlan) -> dict:
    """The lowest budget anyone gave, so nobody is priced out (ADR 0022, decision 1)."""
    given = [Decimal(r.budget) for r in g.replies if r.budget]
    people = max(1, len(g.replies) + (0 if any(r.is_owner for r in g.replies) else 1))
    if given:
        each = min(given)
        return {"per_person": float(each), "people": people, "total": float(each * people), "from": "lowest",
                "gave": len(given)}
    if g.budget_hint:
        return {"per_person": float(g.budget_hint), "people": people, "total": float(Decimal(g.budget_hint) * people),
                "from": "organiser", "gave": 0}
    return {"per_person": None, "people": people, "total": None, "from": None, "gave": 0}


def _summary(g: GroupPlan) -> dict:
    foods = Counter(r.food for r in g.replies)
    b = group_budget(g)
    return {"id": str(g.id), "title": g.title, "origin": g.origin_name, "window_start": g.window_start.isoformat(),
            "window_end": g.window_end.isoformat(), "days": g.days, "status": g.status if is_open(g) or g.status != "open" else "expired",
            "expires_at": _aware(g.expires_at).isoformat(), "note": g.note, "stay": g.stay,
            "options": tally(g), "replies_count": len(g.replies), "max_replies": MAX_REPLIES,
            "best_dates": best_dates(g), "days_heat": day_counts(g),
            "food": {"veg": foods.get("veg", 0), "non_veg": foods.get("non_veg", 0), "both": foods.get("both", 0)},
            # how many gave a budget, never the amount: the lowest is one person's own budget (ADR 0022, decision 2)
            "budgets_given": b["gave"], "trip_id": str(g.trip_id) if g.trip_id else None,
            "currency": currency_of(g)}


def currency_of(g: GroupPlan) -> str:
    o = g.options[0] if g.options else None
    return locale_for(o.lat, o.lng)[1] if o and o.lat is not None else "INR"


def owner_view(g: GroupPlan) -> dict:
    """Everything, budgets included: only the organiser sees each person's budget (ADR 0022, decision 2)."""
    return {**_summary(g), "url": public_url(g), "budget": group_budget(g),
            "budget_hint": float(g.budget_hint) if g.budget_hint else None,
            "replies": [{"id": str(r.id), "name": r.name, "budget": float(r.budget) if r.budget else None, "food": r.food,
                         "dates": r.dates or [], "votes": r.votes or [], "is_owner": r.is_owner,
                         "updated_at": _aware(r.updated_at).isoformat()} for r in g.replies]}


def public_view(g: GroupPlan, device: str | None, owner_name: str) -> dict:
    """What a friend sees: names and votes, never a budget, a device key or anything about other people's money."""
    dh = device_hash(g, device) if device else None
    mine = next((r for r in g.replies if dh and r.device_hash == dh), None)
    return {**_summary(g), "organiser": _first(owner_name),
            "names": [r.name for r in g.replies],
            "mine": {"name": mine.name, "budget": float(mine.budget) if mine.budget else None, "food": mine.food,
                     "dates": mine.dates or [], "votes": mine.votes or []} if mine else None,
            "budget_hint": float(g.budget_hint) if g.budget_hint else None}


def card(g: GroupPlan) -> dict:
    return {"id": str(g.id), "title": g.title, "status": _summary(g)["status"], "replies_count": len(g.replies),
            "options": [o.name for o in g.options], "window_start": g.window_start.isoformat(),
            "window_end": g.window_end.isoformat(), "days": g.days, "trip_id": str(g.trip_id) if g.trip_id else None,
            "created_at": _aware(g.created_at).isoformat()}


def list_mine(db: Session, user: User) -> list[GroupPlan]:
    return list(db.scalars(select(GroupPlan).where(GroupPlan.owner_id == user.id).order_by(GroupPlan.created_at.desc())
                           .options(selectinload(GroupPlan.options), selectinload(GroupPlan.replies)).limit(30)))


def invite_text(g: GroupPlan, owner_name: str) -> str:
    """The WhatsApp message. Plain text, no emoji (WhatsApp Desktop on Windows breaks emoji in wa.me text)."""
    opts = ", ".join(o.name for o in g.options)
    when = f"{g.window_start.strftime('%d %b')} to {g.window_end.strftime('%d %b')}"
    lines = [f"{_first(owner_name)} is planning a {g.days}-day trip and wants your say.",
             f"Where: {opts}" if len(g.options) > 1 else f"Where: {opts}",
             f"When: sometime {when}",
             "Add your budget, your dates and veg / non-veg, and vote. Takes 30 seconds, no sign-up:",
             public_url(g)]
    return "\n".join(lines)


def wa_link(text: str) -> str:
    return "https://wa.me/?text=" + quote(text, safe="")


# ------------------------------------------------------------------------------------------------------ make the plan

def make_trip(db: Session, user: User, g: GroupPlan, option_id: uuid.UUID | None, start: date | None):
    if g.status == "made" and g.trip_id:
        return g.trip_id  # idempotent: the second tap opens the same trip
    if not g.replies:
        raise AppError("Wait for at least one reply first.", code="no_replies", status_code=409)
    opt = next((o for o in g.options if o.id == option_id), None) if option_id else winner(g)
    if opt is None:
        raise AppError("That option isn't in this group plan.", code="bad_option", status_code=422)
    if start is None:
        best = best_dates(g, 1)
        start = date.fromisoformat(best[0]["start"]) if best else g.window_start
    if start < g.window_start or start + timedelta(days=g.days - 1) > g.window_end:
        raise AppError("Those dates are outside the group's window.", code="bad_dates", status_code=422)
    b = group_budget(g)
    if not b["total"]:
        raise AppError("Nobody has given a budget yet. Add a suggested budget or wait for one.", code="no_budget",
                       status_code=409)
    people = min(20, b["people"])
    data = TripCreate(
        origin=LocationIn(name=g.origin_name, place_id=g.origin_place_id, lat=g.origin_lat, lng=g.origin_lng),
        destination=LocationIn(name=opt.name, place_id=opt.place_id, lat=opt.lat, lng=opt.lng),
        start_date=start, end_date=start + timedelta(days=g.days - 1), budget=Decimal(str(b["total"])),
        travellers=people, traveller_type="friends" if people > 2 else ("couple" if people == 2 else "solo"),
        food=food_for(g), stay="no_stay" if g.days == 1 else g.stay, title=g.title)
    trip = TS.create_trip(db, user, data)
    owner_member = next(m for m in trip.members if m.is_owner)
    for r in g.replies:
        if r.is_owner:
            owner_member.budget, owner_member.food_pref = r.budget, r.food
            continue
        trip.members.append(TripMember(name=r.name[:80], budget=r.budget, food_pref=r.food, is_owner=False))
    g.status, g.trip_id = "made", trip.id
    db.flush()
    return trip.id


def purge_old(db: Session, now: datetime | None = None) -> int:
    """Retention (ADR 0022): replies go 30 days after the plan was made or the link ran out."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=KEEP_DAYS)
    old = list(db.scalars(select(GroupPlan).where(
        ((GroupPlan.status == "made") & (GroupPlan.updated_at < cutoff)) | (GroupPlan.expires_at < cutoff))))
    for g in old:
        db.delete(g)
    return len(old)
