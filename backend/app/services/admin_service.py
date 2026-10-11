"""Everything the admin console reads and changes (ADR 0010).

Rules that hold for every change made here:

* it is attributed to a named admin and written to `admin_audit` in the SAME transaction as the change, so there is never a change
  without a trail, nor a trail for a change that did not happen;
* nobody can lock the owners out: an owner (ADMIN_EMAILS) cannot be suspended, deleted or made a non-admin from the console, and nobody
  can suspend, delete or demote themselves;
* only an owner can make or remove an admin, or suspend / delete another admin;
* nothing here reads a secret, a password hash, a verification code or a provider key.
"""
from __future__ import annotations

import statistics
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import is_owner
from app.core import brownout, killswitch, runtime
from app.core.config import get_settings
from app.core.errors import AppError, Forbidden, NotFound
from app.models.admin import AdminAudit, RuntimeSetting
from app.models.plan_job import PlanJob, PlanRefund
from app.models.trip import Trip
from app.models.user import Subscription, User
from app.services import quota_service
from app.services import trip_service as TS
from app.services.verification_codes import ip_hash

PAGE_MAX = 100
TZ = ZoneInfo("Asia/Kolkata")  # the same "day" the budgets use


def _aware(d: datetime | None) -> datetime | None:
    return d if d is None or d.tzinfo else d.replace(tzinfo=UTC)


def _iso(d: datetime | None) -> str | None:
    d = _aware(d)
    return d.isoformat() if d else None


def _page(page: int, size: int) -> tuple[int, int]:
    return max(1, page), max(1, min(PAGE_MAX, size))


def _like(text: str) -> str:
    """A case-insensitive "contains" pattern with the wildcard characters escaped (the search box is not a pattern language)."""
    t = text.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{t}%"


# --- the trail -------------------------------------------------------------------------------------------------------------

def audit(db: Session, actor: User, action: str, *, target_type: str | None = None, target_id: Any = None,
          target_label: str | None = None, detail: dict | None = None, reason: str | None = None, ip: str | None = None) -> None:
    """Add a row to the trail (the caller commits it together with the change)."""
    db.add(AdminAudit(created_at=datetime.now(UTC), actor_id=actor.id, actor_email=actor.email, action=action,
                      target_type=target_type, target_id=str(target_id) if target_id is not None else None,
                      target_label=target_label[:255] if target_label else None, detail=detail or None,
                      reason=reason or None, ip_hash=ip_hash(ip)))


def audit_out(a: AdminAudit) -> dict:
    return {"id": str(a.id), "at": _iso(a.created_at), "actor": a.actor_email, "action": a.action, "target_type": a.target_type,
            "target_id": a.target_id, "target_label": a.target_label, "detail": a.detail, "reason": a.reason}


def list_audit(db: Session, *, action: str | None, q: str | None, page: int, size: int) -> dict:
    page, size = _page(page, size)
    where = []
    if action:
        where.append(AdminAudit.action.startswith(action, autoescape=True))  # "user" matches user.update and user.delete
    if q:
        pat = _like(q)
        where.append(or_(func.lower(AdminAudit.actor_email).like(pat, escape="\\"),
                         func.lower(func.coalesce(AdminAudit.target_label, "")).like(pat, escape="\\")))
    total = db.scalar(select(func.count()).select_from(AdminAudit).where(*where)) or 0
    rows = db.scalars(select(AdminAudit).where(*where).order_by(AdminAudit.created_at.desc()).offset((page - 1) * size).limit(size))
    return {"items": [audit_out(a) for a in rows], "total": total, "page": page, "page_size": size}


# --- users -----------------------------------------------------------------------------------------------------------------

def _plus_now():
    """SQL for "has an active Plus plan right now" (the same rule as quota_service.plus_active)."""
    now = datetime.now(UTC)
    return and_(Subscription.plan == "plus", Subscription.status == "active",
                or_(Subscription.current_period_end.is_(None), Subscription.current_period_end > now))


def _user_dict(u: User, sub: Subscription | None, trips: int, last_trip: datetime | None) -> dict:
    free_total = runtime.free_trip_plans()
    plus = bool(sub and quota_service.plus_active(sub))
    return {
        "id": str(u.id), "name": u.name, "email": u.email, "phone": u.phone, "is_active": u.is_active,
        "is_owner": is_owner(u), "is_admin": bool(u.is_admin), "email_verified": u.email_verified, "phone_verified": u.phone_verified,
        "created_at": _iso(u.created_at), "plan": "plus" if plus else "free", "plan_status": sub.status if sub else "active",
        "plan_ends": _iso(sub.current_period_end) if sub and plus else None, "trip_credits": sub.trip_credits if sub else 0,
        "free_plans_used": u.free_plans_used, "free_plans_left": max(0, free_total - u.free_plans_used),
        "trips": trips, "last_trip_at": _iso(last_trip)}


def _user_query():
    trips = select(func.count(Trip.id)).where(Trip.owner_id == User.id).correlate(User).scalar_subquery()
    last = select(func.max(Trip.created_at)).where(Trip.owner_id == User.id).correlate(User).scalar_subquery()
    return select(User, Subscription, trips.label("trips"), last.label("last_trip")).outerjoin(Subscription, Subscription.user_id == User.id)


def list_users(db: Session, *, q: str | None, status: str | None, plan: str | None, sort: str, page: int, size: int) -> dict:
    page, size = _page(page, size)
    where = []
    if q and q.strip():
        pat = _like(q)
        where.append(or_(func.lower(User.email).like(pat, escape="\\"), func.lower(User.name).like(pat, escape="\\"),
                         func.coalesce(User.phone, "").like(pat, escape="\\")))
    if status == "active":
        where.append(User.is_active.is_(True))
    elif status == "suspended":
        where.append(User.is_active.is_(False))
    elif status == "admins":
        where.append(or_(User.is_admin.is_(True), func.lower(User.email).in_(get_settings().admin_email_set or {""})))
    elif status == "unverified":
        where.append(User.email_verified_at.is_(None))  # the mobile is not code-checked unless REQUIRE_PHONE_CODE is on
    if plan == "plus":
        where.append(_plus_now())
    elif plan == "free":
        where.append(or_(Subscription.id.is_(None), ~_plus_now()))
    base = _user_query().where(*where)
    total = db.scalar(select(func.count()).select_from(User).outerjoin(Subscription, Subscription.user_id == User.id).where(*where)) or 0
    order = {"oldest": User.created_at.asc(), "name": func.lower(User.name).asc()}.get(sort, User.created_at.desc())
    if sort == "trips":
        order = base.selected_columns.trips.desc()
    rows = db.execute(base.order_by(order, User.id).offset((page - 1) * size).limit(size)).all()
    return {"items": [_user_dict(u, s, int(n or 0), lt) for u, s, n, lt in rows], "total": total, "page": page, "page_size": size}


def load_user(db: Session, user_id) -> User:
    u = db.get(User, user_id)
    if u is None:
        raise NotFound("That account doesn't exist.")
    return u


def user_detail(db: Session, user_id) -> dict:
    row = db.execute(_user_query().where(User.id == user_id)).first()
    if row is None:
        raise NotFound("That account doesn't exist.")
    u, sub, n, lt = row
    trips = db.scalars(select(Trip).where(Trip.owner_id == u.id).order_by(Trip.created_at.desc()).limit(25)).all()
    jobs = db.scalars(select(PlanJob).where(PlanJob.user_id == u.id).order_by(PlanJob.created_at.desc()).limit(10)).all()
    return {**_user_dict(u, sub, int(n or 0), lt),
            "recent_trips": [{"id": str(t.id), "title": t.title, "destination": t.destination_name, "status": t.status,
                              "start_date": t.start_date.isoformat(), "end_date": t.end_date.isoformat(), "budget": float(t.budget),
                              "currency": t.currency, "plan_count": t.plan_count, "created_at": _iso(t.created_at)} for t in trips],
            "recent_plans": [_job_dict(j) for j in jobs]}


def update_user(db: Session, actor: User, target: User, patch, ip: str | None) -> dict:
    """Apply an admin's edit to an account. `patch` is the validated UserPatch; only fields that really change are applied and logged."""
    me_owner = is_owner(actor)
    target_owner = is_owner(target)
    is_self = target.id == actor.id
    changes: dict[str, list] = {}

    def set_field(obj, attr: str, new, label: str | None = None):
        old = getattr(obj, attr)
        if old != new:
            changes[label or attr] = [old if not isinstance(old, datetime) else _iso(old), new if not isinstance(new, datetime) else _iso(new)]
            setattr(obj, attr, new)

    if patch.is_active is not None and patch.is_active != target.is_active:
        if not patch.is_active:
            if is_self:
                raise Forbidden("You can't suspend your own account.", code="self_action")
            if target_owner:
                raise Forbidden("Owners can't be suspended from the console.", code="owner_protected")
            if (target.is_admin) and not me_owner:
                raise Forbidden("Only an owner can suspend an admin.", code="owner_only")
        set_field(target, "is_active", patch.is_active)
    if patch.is_admin is not None and bool(patch.is_admin) != bool(target.is_admin):
        if not me_owner:
            raise Forbidden("Only an owner can make or remove an admin.", code="owner_only")
        if is_self or target_owner:
            raise Forbidden("An owner's access is set on the server (ADMIN_EMAILS), not here.", code="owner_protected")
        set_field(target, "is_admin", patch.is_admin)
    if patch.name is not None:
        set_field(target, "name", patch.name.strip())
    if patch.free_plans_used is not None:
        set_field(target, "free_plans_used", patch.free_plans_used)
    now = datetime.now(UTC)
    if patch.email_verified is not None and patch.email_verified != target.email_verified:
        set_field(target, "email_verified_at", now if patch.email_verified else None, "email_verified")
    if patch.phone_verified is not None and patch.phone_verified != target.phone_verified:
        set_field(target, "phone_verified_at", now if patch.phone_verified else None, "phone_verified")
    if patch.plan is not None or patch.trip_credits is not None or patch.plus_days is not None:
        sub = quota_service.get_subscription(db, target)
        if patch.plus_days is not None and patch.plan != "plus":
            raise AppError("Choose the Plus plan to set how many days it lasts.", code="plus_days_needs_plus", status_code=422)
        was_plus = quota_service.plus_active(sub)
        if patch.plan == "plus" and (not was_plus or patch.plus_days is not None):  # Plus again, without days, changes nothing
            end = now + timedelta(days=patch.plus_days or 30)
            changes["plan"] = ["plus" if was_plus else "free", f"plus until {end.date().isoformat()}"]
            sub.plan, sub.status, sub.current_period_end = "plus", "active", end
            sub.provider, sub.provider_ref = "admin", f"admin:{actor.email}"[:120]
        elif patch.plan == "free" and (was_plus or sub.plan != "free"):
            changes["plan"] = ["plus" if was_plus else "free", "free"]
            sub.plan, sub.status, sub.current_period_end = "free", "active", None
            sub.provider, sub.provider_ref = "admin", f"admin:{actor.email}"[:120]
        if patch.trip_credits is not None:
            set_field(sub, "trip_credits", patch.trip_credits)
    if not changes:
        return user_detail(db, target.id)
    audit(db, actor, "user.update", target_type="user", target_id=target.id, target_label=target.email, detail=changes,
          reason=patch.reason, ip=ip)
    db.commit()
    return user_detail(db, target.id)


def delete_user(db: Session, actor: User, target: User, confirm_email: str, ip: str | None, reason: str | None) -> None:
    if target.id == actor.id:
        raise Forbidden("You can't delete your own account here.", code="self_action")
    if is_owner(target):
        raise Forbidden("Owners can't be deleted from the console.", code="owner_protected")
    if target.is_admin and not is_owner(actor):
        raise Forbidden("Only an owner can delete an admin.", code="owner_only")
    if (confirm_email or "").strip().lower() != target.email.strip().lower():
        raise AppError("Type the account's email address to confirm.", code="confirm_mismatch", status_code=422)
    trips = db.scalar(select(func.count(Trip.id)).where(Trip.owner_id == target.id)) or 0
    audit(db, actor, "user.delete", target_type="user", target_id=target.id, target_label=target.email,
          detail={"trips_deleted": int(trips), "name": target.name}, reason=reason, ip=ip)
    db.delete(target)  # trips, plans, share links, expenses and the subscription go with it (ON DELETE CASCADE)
    db.commit()


# --- trips -----------------------------------------------------------------------------------------------------------------

def _trip_row(t: Trip, owner: User) -> dict:
    it = t.current_itinerary
    return {"id": str(t.id), "title": t.title, "status": t.status, "origin": t.origin_name, "destination": t.destination_name,
            "start_date": t.start_date.isoformat(), "end_date": t.end_date.isoformat(), "travellers": t.travellers,
            "budget": float(t.budget), "currency": t.currency, "plan_count": t.plan_count, "replan_count": t.replan_count,
            "estimated_cost": float(it.total_cost) if it else None, "feasible": (t.feasibility or {}).get("feasible"),
            "data_mode": t.data_mode, "created_at": _iso(t.created_at),
            "owner": {"id": str(owner.id), "name": owner.name, "email": owner.email}}


def list_trips(db: Session, *, q: str | None, status: str | None, owner_id: str | None, page: int, size: int) -> dict:
    page, size = _page(page, size)
    where = []
    if q and q.strip():
        pat = _like(q)
        where.append(or_(func.lower(Trip.title).like(pat, escape="\\"), func.lower(Trip.destination_name).like(pat, escape="\\"),
                         func.lower(Trip.origin_name).like(pat, escape="\\"), func.lower(User.email).like(pat, escape="\\")))
    if status:
        where.append(Trip.status == status)
    if owner_id:
        where.append(Trip.owner_id == owner_id)
    base = select(Trip, User).join(User, User.id == Trip.owner_id).where(*where)
    total = db.scalar(select(func.count()).select_from(Trip).join(User, User.id == Trip.owner_id).where(*where)) or 0
    rows = db.execute(base.order_by(Trip.created_at.desc()).offset((page - 1) * size).limit(size)).all()
    return {"items": [_trip_row(t, u) for t, u in rows], "total": total, "page": page, "page_size": size}


def trip_detail(db: Session, trip_id) -> dict:
    trip = TS.load_trip(db, trip_id)
    owner = db.get(User, trip.owner_id)
    return {**_trip_row(trip, owner), "trip": TS.trip_out(trip, read_only=True)}


def delete_trip(db: Session, actor: User, trip_id, ip: str | None, reason: str | None) -> None:
    trip = TS.load_trip(db, trip_id)
    owner = db.get(User, trip.owner_id)
    audit(db, actor, "trip.delete", target_type="trip", target_id=trip.id, target_label=trip.title,
          detail={"owner": owner.email if owner else None, "destination": trip.destination_name, "plans": trip.plan_count},
          reason=reason, ip=ip)
    db.delete(trip)
    db.commit()


# --- plan jobs -------------------------------------------------------------------------------------------------------------

def _job_dict(j: PlanJob) -> dict:
    return {"id": str(j.id), "trip_id": str(j.trip_id), "kind": j.kind, "state": j.state, "result": j.result_status,
            "error": j.error_code, "seconds": round(j.duration_ms / 1000, 1) if j.duration_ms else None,
            "late_finish": bool(j.late_finish_at), "created_at": _iso(j.created_at)}


def list_plan_jobs(db: Session, *, state: str | None, limit: int) -> dict:
    where = [PlanJob.state == state] if state else []
    rows = db.execute(select(PlanJob, User.email).join(User, User.id == PlanJob.user_id).where(*where)
                      .order_by(PlanJob.created_at.desc()).limit(max(1, min(200, limit)))).all()
    return {"items": [{**_job_dict(j), "user": email} for j, email in rows]}


# --- overview --------------------------------------------------------------------------------------------------------------

def _per_day(times: list[datetime], days: int, today: date) -> list[dict]:
    counts: dict[date, int] = {}
    for t in times:
        d = _aware(t).astimezone(TZ).date()
        counts[d] = counts.get(d, 0) + 1
    return [{"date": (today - timedelta(days=days - 1 - i)).isoformat(), "count": counts.get(today - timedelta(days=days - 1 - i), 0)}
            for i in range(days)]


def overview(db: Session) -> dict:
    now = datetime.now(UTC)
    day, week = now - timedelta(days=1), now - timedelta(days=7)
    count = lambda *where: int(db.scalar(select(func.count()).select_from(User).where(*where)) or 0)  # noqa: E731
    users = {"total": count(), "new_24h": count(User.created_at >= day), "new_7d": count(User.created_at >= week),
             "verified": count(User.email_verified_at.is_not(None)),
             "suspended": count(User.is_active.is_(False)), "admins": count(User.is_admin.is_(True)),
             "owners": len(get_settings().admin_email_set),
             "plus": int(db.scalar(select(func.count()).select_from(Subscription).where(_plus_now())) or 0)}
    tcount = lambda *where: int(db.scalar(select(func.count()).select_from(Trip).where(*where)) or 0)  # noqa: E731
    trips = {"total": tcount(), "planned": tcount(Trip.plan_count > 0), "new_24h": tcount(Trip.created_at >= day)}
    states = dict(db.execute(select(PlanJob.state, func.count()).where(PlanJob.created_at >= day).group_by(PlanJob.state)).all())
    durations = [d / 1000 for (d,) in db.execute(select(PlanJob.duration_ms).where(PlanJob.state == "done", PlanJob.duration_ms.is_not(None))
                                                  .order_by(PlanJob.created_at.desc()).limit(200)).all() if d]
    plans = {"last_24h": sum(states.values()), "by_state": states,
             "refunds_24h": int(db.scalar(select(func.count()).select_from(PlanRefund).where(PlanRefund.created_at >= day)) or 0),
             "late_finishes_24h": int(db.scalar(select(func.count()).select_from(PlanJob).where(PlanJob.late_finish_at >= day)) or 0),
             "median_seconds": round(statistics.median(durations), 1) if durations else None}
    today = datetime.now(TZ).date()
    since = now - timedelta(days=15)
    signups = [t for (t,) in db.execute(select(User.created_at).where(User.created_at >= since).limit(50_000)).all()]
    planned = [t for (t,) in db.execute(select(PlanJob.created_at).where(PlanJob.created_at >= since, PlanJob.kind == "new").limit(50_000)).all()]
    from app.gateway.budget import budgets
    from app.services.plan_runner import runner
    recent = db.scalars(select(AdminAudit).order_by(AdminAudit.created_at.desc()).limit(8)).all()
    return {"users": users, "trips": trips, "plans": plans, "runner": runner.stats(), "budgets": budgets.status(),
            "series": {"signups": _per_day(signups, 14, today), "plans": _per_day(planned, 14, today)},
            "switches_on": [n for n in runtime.SWITCHES if killswitch.killed(n)],
            "recent_audit": [audit_out(a) for a in recent], "generated_at": now.isoformat()}


# --- live controls ---------------------------------------------------------------------------------------------------------

def _row(db: Session, key: str) -> RuntimeSetting | None:
    return db.get(RuntimeSetting, key)


def controls(db: Session) -> dict:
    s = get_settings()
    switches = []
    for sw in runtime.SWITCHES.values():
        row = _row(db, f"switch:{sw.name}")
        switches.append({"name": sw.name, "label": sw.label, "effect": sw.effect, "people_see": sw.people_see,
                         "on": killswitch.killed(sw.name), "source": killswitch.source(sw.name),
                         "reason": row.reason if row else None, "by": row.updated_by if row else None,
                         "at": _iso(row.updated_at) if row else None})
    defaults = {"brownout_level": s.brownout_level, "free_trip_plans": s.free_trip_plans, "daily_plan_cap": s.daily_plan_cap}
    effective = {"brownout_level": brownout.base_level(), "free_trip_plans": runtime.free_trip_plans(),
                 "daily_plan_cap": runtime.number("daily_plan_cap", s.daily_plan_cap)}
    numbers = []
    for n in runtime.NUMBERS.values():
        row = _row(db, n.key)
        numbers.append({"key": n.key, "label": n.label, "hint": n.hint, "low": n.low, "high": n.high, "value": effective[n.key],
                        "default": defaults[n.key], "source": "admin" if row else "default",
                        "reason": row.reason if row else None, "by": row.updated_by if row else None,
                        "at": _iso(row.updated_at) if row else None})
    return {"switches": switches, "numbers": numbers}


def set_switch(db: Session, actor: User, name: str, on: bool, reason: str | None, ip: str | None) -> dict:
    if name not in runtime.SWITCHES:
        raise NotFound("That switch doesn't exist.")
    before = killswitch.killed(name)
    runtime.put(db, f"switch:{name}", bool(on), reason, actor.email)
    audit(db, actor, "control.switch", target_type="switch", target_id=name, target_label=runtime.SWITCHES[name].label,
          detail={"from": before, "to": bool(on)}, reason=reason, ip=ip)
    db.commit()
    runtime.invalidate()
    return controls(db)


def set_number(db: Session, actor: User, key: str, value: int, reason: str | None, ip: str | None) -> dict:
    spec = runtime.NUMBERS.get(key)
    if spec is None:
        raise NotFound("That setting doesn't exist.")
    if not (spec.low <= value <= spec.high):
        raise AppError(f"{spec.label} must be between {spec.low} and {spec.high}.", code="out_of_range", status_code=422)
    before = controls(db)
    old = next(n["value"] for n in before["numbers"] if n["key"] == key)
    runtime.put(db, key, int(value), reason, actor.email)
    audit(db, actor, "control.setting", target_type="setting", target_id=key, target_label=spec.label,
          detail={"from": old, "to": int(value)}, reason=reason, ip=ip)
    db.commit()
    runtime.invalidate()
    return controls(db)


def clear_control(db: Session, actor: User, key: str, ip: str | None) -> dict:
    """Give a control back to the environment / built-in default."""
    name = key.removeprefix("switch:")
    if (key.startswith("switch:") and name not in runtime.SWITCHES) or (not key.startswith("switch:") and key not in runtime.NUMBERS):
        raise NotFound("That control doesn't exist.")
    if runtime.remove(db, key):
        audit(db, actor, "control.clear", target_type="switch" if key.startswith("switch:") else "setting", target_id=name,
              target_label=(runtime.SWITCHES.get(name) or runtime.NUMBERS.get(name)).label, ip=ip)
        db.commit()
        runtime.invalidate()
    return controls(db)
