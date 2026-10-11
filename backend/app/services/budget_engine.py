"""Authoritative budget arithmetic. The LLM never computes or changes any of these numbers."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.utils.money import ZERO, D, round_rupees

CATEGORIES = [("transport", "Transport"), ("stay", "Hotel"), ("food", "Food"), ("activities", "Activities"),
              ("local", "Local travel"), ("buffer", "Buffer")]
BUFFER_PCT = {"budget": Decimal("0.05"), "balanced": Decimal("0.07"), "premium": Decimal("0.10")}
# Guideline shares used for feasibility hints and the budget panel's "typical" markers.
TARGET_SHARE = {
    "day": {"transport": .40, "stay": 0, "food": .25, "activities": .15, "local": .12, "buffer": .08},
    "multi": {"transport": .25, "stay": .35, "food": .18, "activities": .10, "local": .07, "buffer": .05},
}


def buffer_pct(styles: list[str]) -> Decimal:
    if "premium" in styles:
        return BUFFER_PCT["premium"]
    if "budget" in styles:
        return BUFFER_PCT["budget"]
    return BUFFER_PCT["balanced"]


@dataclass
class BudgetLine:
    category: str
    label: str
    amount: Decimal
    state: str = "estimated"


@dataclass
class BudgetBreakdown:
    budget: Decimal
    travellers: int
    lines: list[BudgetLine] = field(default_factory=list)
    buffer_target_pct: Decimal = Decimal("0.07")
    buffer: Decimal = ZERO
    subtotal: Decimal = ZERO
    planned: Decimal = ZERO
    remaining: Decimal = ZERO
    status: str = "within"  # within | tight | over

    def category_totals(self) -> dict[str, Decimal]:
        totals = {k: ZERO for k, _ in CATEGORIES}
        for line in self.lines:
            totals[line.category] += line.amount
        totals["buffer"] = self.buffer
        return totals

    def to_dict(self, multi_day: bool) -> dict:
        totals = self.category_totals()
        shares = TARGET_SHARE["multi" if multi_day else "day"]
        return {
            "budget": float(self.budget), "planned": float(self.planned), "remaining": float(self.remaining),
            "subtotal": float(self.subtotal), "status": self.status, "travellers": self.travellers,
            "per_person": float(D(self.planned / max(1, self.travellers))),
            "buffer_pct": float(self.buffer_target_pct * 100),
            "categories": [{"key": k, "label": label, "amount": float(totals[k]),
                            "typical": float(D(self.budget * Decimal(str(shares[k])))),
                            "items": [{"label": ln.label, "amount": float(ln.amount), "state": ln.state}
                                      for ln in self.lines if ln.category == k]}
                           for k, label in CATEGORIES],
        }


def compute(budget: Decimal, travellers: int, lines: list[BudgetLine], styles: list[str]) -> BudgetBreakdown:
    """Sum the verified line items, then size the buffer to what the budget can still hold."""
    b = BudgetBreakdown(budget=D(budget), travellers=travellers, lines=lines, buffer_target_pct=buffer_pct(styles))
    b.subtotal = D(sum((ln.amount for ln in lines), ZERO))
    target = D(round_rupees(b.subtotal * b.buffer_target_pct))
    headroom = b.budget - b.subtotal
    b.buffer = D(round_rupees(max(ZERO, min(target, headroom))))
    if b.buffer > max(ZERO, headroom):
        b.buffer = D(max(ZERO, headroom))
    b.planned = D(b.subtotal + b.buffer)
    b.remaining = D(b.budget - b.planned)
    if b.subtotal > b.budget:
        b.status = "over"
    elif b.buffer < target:
        b.status = "tight"
    return b
