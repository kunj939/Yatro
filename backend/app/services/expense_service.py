"""Group expenses: splits in paise-exact Decimal arithmetic and a greedy minimal settlement."""
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.expense import Expense, ExpenseSplit
from app.models.trip import Trip, TripMember
from app.utils.money import ZERO, D

PAISA = Decimal("0.01")


def split_equal(amount: Decimal, member_ids: list) -> dict:
    """₹1,450 / 4 = ₹362.50 each; leftover paise go to the first members so the total is exact."""
    n = len(member_ids)
    if n == 0:
        raise AppError("Choose at least one person.", code="no_members", status_code=422)
    amount = D(amount)
    base = (amount / n).quantize(PAISA, rounding=ROUND_DOWN)
    remainder = int(((amount - base * n) / PAISA).to_integral_value())
    return {m: base + (PAISA if i < remainder else ZERO) for i, m in enumerate(member_ids)}


def compute_shares(trip: Trip, amount: Decimal, split_type: str, paid_by, member_ids: list | None,
                   custom: list | None) -> dict:
    members = {m.id for m in trip.members}
    if paid_by not in members:
        raise AppError("The payer must be a member of this trip.", code="invalid_member", status_code=422)
    if split_type == "equal":
        return split_equal(amount, [m.id for m in trip.members])
    if split_type == "selected":
        ids = list(dict.fromkeys(member_ids or []))
        if any(i not in members for i in ids):
            raise AppError("Everyone in the split must be a trip member.", code="invalid_member", status_code=422)
        return split_equal(amount, ids)
    if split_type == "one_person":
        who = (member_ids or [paid_by])[0]
        if who not in members:
            raise AppError("That person isn't a member of this trip.", code="invalid_member", status_code=422)
        return {who: D(amount)}
    if split_type == "custom":
        shares: dict = {}
        for row in custom or []:
            if row.member_id not in members:
                raise AppError("Everyone in the split must be a trip member.", code="invalid_member", status_code=422)
            shares[row.member_id] = shares.get(row.member_id, ZERO) + D(row.amount)
        total = sum(shares.values(), ZERO)
        if total != D(amount):
            raise AppError(f"Custom shares add up to ₹{total}, but the expense is ₹{D(amount)}.",
                           code="split_mismatch", status_code=422)
        return {k: v for k, v in shares.items() if v > 0}
    raise AppError("Unknown split type.", code="invalid_split", status_code=422)


def add_expense(db: Session, trip: Trip, user_id, data) -> Expense:
    shares = compute_shares(trip, data.amount, data.split_type, data.paid_by_member_id, data.member_ids, data.custom)
    exp = Expense(trip_id=trip.id, description=data.description, category=data.category, amount=D(data.amount),
                  currency=trip.currency, paid_by_member_id=data.paid_by_member_id, split_type=data.split_type,
                  created_by=user_id)
    exp.splits = [ExpenseSplit(member_id=m, amount=a) for m, a in shares.items()]
    db.add(exp)
    db.flush()
    return exp


def balances(trip: Trip) -> dict:
    """net > 0: the member should receive money; net < 0: they owe."""
    paid = {m.id: ZERO for m in trip.members}
    owed = {m.id: ZERO for m in trip.members}
    for e in trip.expenses:
        paid[e.paid_by_member_id] = paid.get(e.paid_by_member_id, ZERO) + D(e.amount)
        for s in e.splits:
            owed[s.member_id] = owed.get(s.member_id, ZERO) + D(s.amount)
    return {m: {"paid": paid.get(m, ZERO), "share": owed.get(m, ZERO), "net": paid.get(m, ZERO) - owed.get(m, ZERO)}
            for m in paid}


def settle(bal: dict) -> list[dict]:
    """Greedy settlement: largest debtor pays largest creditor. At most n-1 transfers."""
    creditors = sorted([[m, v["net"]] for m, v in bal.items() if v["net"] > 0], key=lambda x: -x[1])
    debtors = sorted([[m, -v["net"]] for m, v in bal.items() if v["net"] < 0], key=lambda x: -x[1])
    out = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        amount = min(debtors[i][1], creditors[j][1])
        if amount > 0:
            out.append({"from": debtors[i][0], "to": creditors[j][0], "amount": amount})
        debtors[i][1] -= amount
        creditors[j][1] -= amount
        if debtors[i][1] == 0:
            i += 1
        if creditors[j][1] == 0:
            j += 1
    return out


def summary(trip: Trip) -> dict:
    names = {m.id: m.name for m in trip.members}
    bal = balances(trip)
    total = sum((D(e.amount) for e in trip.expenses), ZERO)
    by_cat: dict[str, Decimal] = {}
    for e in trip.expenses:
        by_cat[e.category] = by_cat.get(e.category, ZERO) + D(e.amount)
    return {
        "total": float(total),
        "by_category": {k: float(v) for k, v in by_cat.items()},
        "members": [{"member_id": str(m), "name": names.get(m, "?"), "paid": float(v["paid"]),
                     "share": float(v["share"]), "net": float(v["net"])} for m, v in bal.items()],
        "settlements": [{"from_member_id": str(s["from"]), "from_name": names.get(s["from"], "?"),
                         "to_member_id": str(s["to"]), "to_name": names.get(s["to"], "?"), "amount": float(s["amount"])}
                        for s in settle(bal)],
    }


def ensure_owner_member(db: Session, trip: Trip, name: str, user_id) -> TripMember:
    for m in trip.members:
        if m.is_owner:
            return m
    m = TripMember(trip_id=trip.id, user_id=user_id, name=name[:80], is_owner=True)
    trip.members.append(m)
    db.flush()
    return m
