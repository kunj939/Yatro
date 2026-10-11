from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import APIModel

Category = Literal["transport", "stay", "food", "activities", "local", "shopping", "other"]


class CustomShare(APIModel):
    member_id: uuid.UUID
    amount: Decimal = Field(ge=0, le=Decimal("10000000"))


class ExpenseCreate(APIModel):
    description: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(gt=0, le=Decimal("10000000"))
    category: Category = "other"
    paid_by_member_id: uuid.UUID
    split_type: Literal["equal", "selected", "custom", "one_person"] = "equal"
    member_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)  # for "selected" / "one_person"
    custom: list[CustomShare] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _check(self) -> ExpenseCreate:
        if self.split_type == "selected" and not self.member_ids:
            raise ValueError("Choose who shares this expense.")
        if self.split_type == "one_person" and self.member_ids and len(self.member_ids) != 1:
            raise ValueError("Choose exactly one person for this expense (or leave it to the payer).")
        if self.split_type == "custom" and not self.custom:
            raise ValueError("Enter each person's share.")
        return self
