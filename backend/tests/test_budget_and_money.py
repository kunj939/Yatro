from decimal import Decimal

from app.services.budget_engine import BudgetLine, compute
from app.utils.money import fmt_inr


def test_budget_buffer_and_remaining():
    b = compute(Decimal(5000), 2, [BudgetLine("transport", "Train", Decimal(1480)),
                                   BudgetLine("food", "Lunch", Decimal(900))], ["balanced"])
    assert b.subtotal == Decimal("2380.00")
    assert b.buffer == Decimal("167.00")  # 7% rounded to whole rupees
    assert b.planned + b.remaining == Decimal("5000.00")
    assert b.status == "within"


def test_budget_tight_and_over():
    tight = compute(Decimal(1000), 1, [BudgetLine("food", "x", Decimal(990))], ["balanced"])
    assert tight.status == "tight" and tight.remaining == 0
    over = compute(Decimal(1000), 1, [BudgetLine("food", "x", Decimal(1200))], ["balanced"])
    assert over.status == "over" and over.remaining < 0 and over.buffer == 0


def test_inr_formatting():
    assert fmt_inr(Decimal("4720")) == "₹4,720"
    assert fmt_inr(Decimal("125000.5")) == "₹1,25,000.50"
    assert fmt_inr(Decimal("362.5")) == "₹362.50"
