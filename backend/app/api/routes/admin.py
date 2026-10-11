"""The admin console's API (ADR 0010). Everything here is for admins only: anyone else, signed in or not, gets the same plain 404
as for any route that does not exist. Every change is attributed and written to the audit trail in the same transaction.

    GET    /admin/me                         who you are here (owner or admin)
    GET    /admin/overview                   numbers for the front page
    GET    /admin/users, /admin/users/{id}   find, inspect
    PATCH  /admin/users/{id}                 suspend, plans and credits, verification, name, admin access (owners)
    POST   /admin/users/{id}/delete          delete an account and everything under it (type the email to confirm)
    GET    /admin/trips, /admin/trips/{id}   find, inspect (the plan as the traveller sees it, read-only)
    POST   /admin/trips/{id}/delete
    GET    /admin/plans                      recent plan jobs: running, failed, refunded
    GET    /admin/controls                   the switches and numbers an admin can change live
    PUT    /admin/controls/switches/{name}   stop or restart a part of the service
    PUT    /admin/controls/settings/{key}    busy-mode level, free plans per account
    DELETE /admin/controls/{key}             give a control back to the server's own setting
    GET    /admin/audit                      who did what
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import admin_user, is_owner
from app.core.middleware import client_ip
from app.db.session import get_db
from app.models.user import User
from app.schemas.admin import DeleteTrip, DeleteUser, NumberSet, SwitchSet, UserPatch
from app.services import admin_service as A


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"  # personal data: never kept by a browser cache or a proxy


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(_no_store)])


@router.get("/me")
def me(admin: User = Depends(admin_user)):
    return {"id": str(admin.id), "name": admin.name, "email": admin.email, "role": "owner" if is_owner(admin) else "admin"}


@router.get("/overview")
def overview(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    return A.overview(db)


# --- users -----------------------------------------------------------------------------------------------------------------

@router.get("/users")
def users(q: str | None = Query(None, max_length=100), status: str | None = Query(None, pattern="^(active|suspended|admins|unverified)$"),
          plan: str | None = Query(None, pattern="^(free|plus)$"), sort: str = Query("newest", pattern="^(newest|oldest|name|trips)$"),
          page: int = Query(1, ge=1, le=100_000), page_size: int = Query(25, ge=1, le=A.PAGE_MAX),
          _: User = Depends(admin_user), db: Session = Depends(get_db)):
    return A.list_users(db, q=q, status=status, plan=plan, sort=sort, page=page, size=page_size)


@router.get("/users/{user_id}")
def user(user_id: uuid.UUID, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    return A.user_detail(db, user_id)


@router.patch("/users/{user_id}")
def patch_user(user_id: uuid.UUID, body: UserPatch, request: Request, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    return A.update_user(db, admin, A.load_user(db, user_id), body, client_ip(request))


# Deleting is a POST with the confirmation in the body: some proxies drop the body of a DELETE, and this one must never lose it.
@router.post("/users/{user_id}/delete", status_code=204)
def delete_user(user_id: uuid.UUID, body: DeleteUser, request: Request, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    A.delete_user(db, admin, A.load_user(db, user_id), body.confirm_email, client_ip(request), body.reason)


# --- trips -----------------------------------------------------------------------------------------------------------------

@router.get("/trips")
def trips(q: str | None = Query(None, max_length=100), status: str | None = Query(None, pattern="^(draft|planning|ready|active|completed)$"),
          owner: uuid.UUID | None = None, page: int = Query(1, ge=1, le=100_000), page_size: int = Query(25, ge=1, le=A.PAGE_MAX),
          _: User = Depends(admin_user), db: Session = Depends(get_db)):
    return A.list_trips(db, q=q, status=status, owner_id=owner, page=page, size=page_size)


@router.get("/trips/{trip_id}")
def trip(trip_id: uuid.UUID, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    return A.trip_detail(db, trip_id)


@router.post("/trips/{trip_id}/delete", status_code=204)
def delete_trip(trip_id: uuid.UUID, request: Request, body: DeleteTrip | None = None, admin: User = Depends(admin_user),
                db: Session = Depends(get_db)):
    A.delete_trip(db, admin, trip_id, client_ip(request), body.reason if body else None)


@router.get("/plans")
def plans(state: str | None = Query(None, pattern="^(pending|running|done|failed|refunded)$"), limit: int = Query(50, ge=1, le=200),
          _: User = Depends(admin_user), db: Session = Depends(get_db)):
    return A.list_plan_jobs(db, state=state, limit=limit)


# --- live controls ---------------------------------------------------------------------------------------------------------

@router.get("/controls")
def controls(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    return A.controls(db)


@router.put("/controls/switches/{name}")
def set_switch(name: str, body: SwitchSet, request: Request, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    return A.set_switch(db, admin, name, body.on, body.reason, client_ip(request))


@router.put("/controls/settings/{key}")
def set_setting(key: str, body: NumberSet, request: Request, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    return A.set_number(db, admin, key, body.value, body.reason, client_ip(request))


@router.delete("/controls/{key}")
def clear_control(key: str, request: Request, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    return A.clear_control(db, admin, key, client_ip(request))


# --- the trail -------------------------------------------------------------------------------------------------------------

@router.get("/audit")
def audit(action: str | None = Query(None, max_length=40), q: str | None = Query(None, max_length=100),
          page: int = Query(1, ge=1, le=100_000), page_size: int = Query(30, ge=1, le=A.PAGE_MAX),
          _: User = Depends(admin_user), db: Session = Depends(get_db)):
    return A.list_audit(db, action=action, q=q, page=page, size=page_size)
