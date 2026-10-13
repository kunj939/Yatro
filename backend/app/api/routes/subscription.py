from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import QuotaOut
from app.services import quota_service

router = APIRouter(prefix="/subscription", tags=["subscription"])


@router.get("/referrals")
def my_referrals(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The Invite friends card: my code and link, the offer, and how many friends joined (ADR 0012)."""
    from app.services import referral_service
    out = referral_service.summary(db, user)
    db.commit()  # the code is made the first time it's asked for
    return out


class UpgradeRequest(BaseModel):
    product: Literal["plus", "one_trip"]


@router.get("", response_model=QuotaOut)
def get_subscription(user: User = Depends(current_user), db: Session = Depends(get_db)):
    out = quota_service.quota_status(db, user)
    db.commit()
    return out


@router.post("/upgrade", response_model=QuotaOut)
def upgrade(body: UpgradeRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """MOCK checkout (PAYMENTS_MODE=mock). Replace with a real payment provider's webhook before launch."""
    out = quota_service.upgrade(db, user, body.product)
    db.commit()
    return out


@router.post("/cancel", response_model=QuotaOut)
def cancel(user: User = Depends(current_user), db: Session = Depends(get_db)):
    out = quota_service.cancel(db, user)
    db.commit()
    return out
