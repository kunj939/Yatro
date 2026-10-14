"""Invite a friend (ADR 0012), on the pattern the big consumer apps settled on (Dropbox, Uber, Airbnb):

- both sides get something: the friend straight away (extra free plans, and a discount on their first month of Plus),
  the inviter when the friend has actually used Yaitro (their first plan is ready), not when they merely sign up;
- one inviter per account, ever (a unique key), and a monthly cap per inviter, so rewards can't be farmed;
- rewards are trip credits, the same thing a "one trip" purchase gives, so nothing else in the quota changes;
- every number, and the whole programme, is a live admin setting (/admin -> Controls).
"""
from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import runtime
from app.core.config import get_settings
from app.core.killswitch import killed
from app.models.referral import Referral
from app.models.user import Subscription, User

log = logging.getLogger("tripai.referrals")
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I, so a code read aloud or typed from a screenshot works


def enabled() -> bool:
    return not killed("referrals")


def rewards() -> dict:
    s = get_settings()
    return {"friend_plans": runtime.number("referral_reward_friend", s.referral_reward_friend),
            "inviter_plans": runtime.number("referral_reward_inviter", s.referral_reward_inviter),
            "plus_discount_pct": runtime.number("referral_plus_discount_pct", s.referral_plus_discount_pct),
            "monthly_cap": runtime.number("referral_monthly_cap", s.referral_monthly_cap)}


def code_for(db: Session, user: User) -> str:
    """The user's own code, made the first time it's asked for: their first name's first letters and four random ones
    (PRIYA7K3Q), easy to say and type."""
    if user.referral_code:
        return user.referral_code
    stem = "".join(ch for ch in (user.name or "").upper() if ch.isascii() and ch.isalpha())[:5] or "YAITRO"
    for _ in range(8):
        code = stem + "".join(secrets.choice(ALPHABET) for _ in range(4))
        if db.scalar(select(User.id).where(User.referral_code == code)) is None:
            user.referral_code = code
            try:
                db.flush()
                return code
            except IntegrityError:  # someone took it in the same instant: try another
                db.rollback()
    raise RuntimeError("could not make a referral code")


def normalise(code: str | None) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())[:16]


def _credit(db: Session, user_id, n: int) -> None:
    if n <= 0:
        return
    sub = db.scalar(select(Subscription).where(Subscription.user_id == user_id))
    if sub is None:
        sub = Subscription(user_id=user_id, plan="free", status="active", trip_credits=0, provider="mock")
        db.add(sub)
        db.flush()
    db.execute(update(Subscription).where(Subscription.id == sub.id).values(trip_credits=Subscription.trip_credits + n))


def attach(db: Session, friend: User, code: str | None) -> Referral | None:
    """At sign-up: link the new account to whoever invited it and give the friend their welcome bonus. A wrong, own or
    paused code is simply ignored — sign-up never fails because of a referral code."""
    code = normalise(code)
    if not code or not enabled():
        return None
    inviter = db.scalar(select(User).where(User.referral_code == code))
    if inviter is None or inviter.id == friend.id or not inviter.is_active:
        return None
    r = rewards()
    ref = Referral(inviter_id=inviter.id, friend_id=friend.id, code=code, friend_credits=r["friend_plans"],
                   discount_pct=r["plus_discount_pct"])
    db.add(ref)
    try:
        db.flush()
    except IntegrityError:  # this account already has an inviter
        db.rollback()
        return None
    _credit(db, friend.id, r["friend_plans"])
    return ref


def on_first_plan(db: Session, friend: User) -> None:
    """The friend's first plan is ready: now the inviter earns their reward, within this month's cap."""
    ref = db.scalar(select(Referral).where(Referral.friend_id == friend.id, Referral.status == "joined"))
    if ref is None or not enabled():
        return
    r = rewards()
    month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    this_month = db.scalar(select(func.count()).select_from(Referral).where(
        Referral.inviter_id == ref.inviter_id, Referral.status == "rewarded", Referral.rewarded_at >= month_start)) or 0
    now = datetime.now(UTC)
    if r["monthly_cap"] and this_month >= r["monthly_cap"]:
        ref.status, ref.rewarded_at = "capped", now  # counted, not paid: the cap is the anti-farming limit
    else:
        ref.status, ref.rewarded_at, ref.inviter_credits = "rewarded", now, r["inviter_plans"]
        _credit(db, ref.inviter_id, r["inviter_plans"])
    db.flush()


def plus_discount(db: Session, user: User) -> int:
    """Percent off this user's first month of Plus, if they joined through a friend and haven't used it yet."""
    if not enabled():
        return 0
    ref = db.scalar(select(Referral).where(Referral.friend_id == user.id))
    return ref.discount_pct if ref is not None and ref.discount_used_at is None else 0


def use_plus_discount(db: Session, user: User) -> None:
    db.execute(update(Referral).where(Referral.friend_id == user.id, Referral.discount_used_at.is_(None))
               .values(discount_used_at=datetime.now(UTC)))


def summary(db: Session, user: User) -> dict:
    """What the Invite card shows: the code and link, the offer, and how it's going."""
    code = code_for(db, user)
    rows = db.scalars(select(Referral).where(Referral.inviter_id == user.id)).all()
    s = get_settings()
    return {"enabled": enabled(), "code": code, "link": f"{s.public_app_url.rstrip('/')}/register?ref={code}",
            "rewards": rewards(), "invited": len(rows), "rewarded": sum(1 for x in rows if x.status == "rewarded"),
            "plans_earned": sum(x.inviter_credits for x in rows),
            "invited_by": _inviter_name(db, user)}


def _inviter_name(db: Session, user: User) -> str | None:
    ref = db.scalar(select(Referral).where(Referral.friend_id == user.id))
    if ref is None:
        return None
    inviter = db.get(User, ref.inviter_id)
    return inviter.name.split(" ")[0] if inviter else None


def admin_list(db: Session, limit: int = 200) -> list[dict]:
    rows = db.scalars(select(Referral).order_by(Referral.created_at.desc()).limit(limit)).all()
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_({x.inviter_id for x in rows} | {x.friend_id for x in rows})))}
    out = []
    for x in rows:
        inv, fr = users.get(x.inviter_id), users.get(x.friend_id)
        out.append({"id": str(x.id), "created_at": x.created_at.isoformat(), "code": x.code, "status": x.status,
                    "inviter": {"id": str(x.inviter_id), "name": inv.name if inv else None, "email": inv.email if inv else None},
                    "friend": {"id": str(x.friend_id), "name": fr.name if fr else None, "email": fr.email if fr else None},
                    "friend_credits": x.friend_credits, "inviter_credits": x.inviter_credits,
                    "discount_pct": x.discount_pct, "discount_used": x.discount_used_at is not None,
                    "rewarded_at": x.rewarded_at.isoformat() if x.rewarded_at else None})
    return out
