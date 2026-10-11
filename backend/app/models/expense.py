from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedMixin, IdMixin

SPLIT_TYPES = ("equal", "selected", "custom", "one_person")


class Expense(IdMixin, CreatedMixin, Base):
    __tablename__ = "expenses"

    trip_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(20), default="other")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    paid_by_member_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("trip_members.id", ondelete="CASCADE"))
    split_type: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))

    trip = relationship("Trip", back_populates="expenses")
    splits: Mapped[list[ExpenseSplit]] = relationship(back_populates="expense", cascade="all, delete-orphan")


class ExpenseSplit(IdMixin, Base):
    __tablename__ = "expense_splits"

    expense_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("expenses.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("trip_members.id", ondelete="CASCADE"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    expense: Mapped[Expense] = relationship(back_populates="splits")
