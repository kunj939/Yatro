"""Plan together (ADR 0022): the organiser's group plans, and the public link friends reply on (no account needed)."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user, optional_user
from app.core.middleware import limit
from app.db.session import get_db
from app.models.user import User
from app.schemas.trip import LocationIn
from app.services import group_service as G

router = APIRouter(prefix="/groups", tags=["groups"])


class GroupCreate(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    origin: LocationIn
    options: list[LocationIn] = Field(min_length=1, max_length=4)
    window_start: date
    window_end: date
    days: int = Field(ge=1, le=14)
    budget_hint: Decimal | None = Field(default=None, gt=0, le=Decimal("99999999"))
    stay: Literal["premium", "tier1", "tier2", "budget", "hostel"] = "tier2"
    note: str | None = Field(default=None, max_length=300)


class ReplyIn(BaseModel):
    device: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=60)
    budget: Decimal | None = Field(default=None, gt=0, le=Decimal("99999999"))
    food: Literal["veg", "non_veg", "both"] = "both"
    dates: list[date] = Field(default_factory=list, max_length=92)
    votes: list[uuid.UUID] = Field(default_factory=list, max_length=4)


class MakeIn(BaseModel):
    option_id: uuid.UUID | None = None
    start_date: date | None = None


def _bundle(g, user: User) -> dict:
    text = G.invite_text(g, user.name)
    return {"group": G.owner_view(g), "text": text, "wa_url": G.wa_link(text)}


@router.post("", status_code=201, dependencies=[Depends(limit("group-create", 10))])
def create_group(body: GroupCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    g = G.create(db, user, body)
    db.commit()
    return _bundle(G.load(db, g.id, user), user)


@router.get("")
def my_groups(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"groups": [G.card(g) for g in G.list_mine(db, user)]}


@router.get("/{group_id}")
def get_group(group_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _bundle(G.load(db, group_id, user), user)


@router.delete("/{group_id}", status_code=204)
def delete_group(group_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(G.load(db, group_id, user))
    db.commit()


@router.delete("/{group_id}/replies/{reply_id}")
def remove_reply(group_id: uuid.UUID, reply_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    g = G.load(db, group_id, user)
    G.remove_reply(db, g, reply_id)
    db.commit()
    return _bundle(G.load(db, group_id, user), user)


@router.post("/{group_id}/make", dependencies=[Depends(limit("group-make", 6))])
def make_plan(group_id: uuid.UUID, body: MakeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Creates the trip with every reply as a member. The client then plans it as usual (that uses one plan)."""
    g = G.load(db, group_id, user)
    trip_id = G.make_trip(db, user, g, body.option_id, body.start_date)
    db.commit()
    return {"trip_id": str(trip_id)}


# ------------------------------------------------------------------------------------------------------ public

@router.get("/join/{token}", dependencies=[Depends(limit("group-view", 60))])
def view_link(token: str, device: str | None = Query(default=None, max_length=64), db: Session = Depends(get_db)):
    g = G.find_by_token(db, token)
    owner = db.get(User, g.owner_id)
    return G.public_view(g, device, owner.name if owner else "")


@router.post("/join/{token}", dependencies=[Depends(limit("group-reply", 10))])
def reply(token: str, body: ReplyIn, user: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    g = G.find_by_token(db, token)
    G.upsert_reply(db, g, body, user)
    db.commit()
    g = G.load(db, g.id)
    owner = db.get(User, g.owner_id)
    return G.public_view(g, body.device, owner.name if owner else "")
