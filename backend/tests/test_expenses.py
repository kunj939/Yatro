from decimal import Decimal

from app.services.expense_service import settle, split_equal


def test_equal_split_example_from_spec():
    shares = split_equal(Decimal("1450"), ["a", "b", "c", "d"])
    assert all(v == Decimal("362.50") for v in shares.values())


def test_equal_split_distributes_remainder_paise():
    shares = split_equal(Decimal("100"), ["a", "b", "c"])
    assert sorted(shares.values(), reverse=True) == [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")]
    assert sum(shares.values()) == Decimal("100.00")


def test_settlement_minimal_transfers():
    bal = {"a": {"net": Decimal("600")}, "b": {"net": Decimal("-200")}, "c": {"net": Decimal("-400")},
           "d": {"net": Decimal("0")}}
    out = settle(bal)
    assert {(t["from"], t["to"], t["amount"]) for t in out} == {("c", "a", Decimal("400")), ("b", "a", Decimal("200"))}


def test_expense_api_flow(client, auth):
    from tests.conftest import trip_payload
    trip = client.post("/api/trips", json=trip_payload(), headers=auth).json()
    owner = trip["members"][0]["id"]
    ids = [owner]
    for name in ("Riya", "Kabir", "Isha"):
        r = client.post(f"/api/trips/{trip['id']}/members", json={"name": name}, headers=auth)
        assert r.status_code == 201
        ids.append(r.json()["id"])
    r = client.post(f"/api/trips/{trip['id']}/expenses", headers=auth,
                    json={"description": "Dinner", "amount": 1450, "paid_by_member_id": owner, "split_type": "equal"})
    assert r.status_code == 201, r.text
    summary = r.json()["summary"]
    me = next(m for m in summary["members"] if m["member_id"] == owner)
    assert me["paid"] == 1450 and me["share"] == 362.5 and me["net"] == 1087.5
    assert len(summary["settlements"]) == 3 and all(s["amount"] == 362.5 for s in summary["settlements"])
    # custom split must add up
    bad = client.post(f"/api/trips/{trip['id']}/expenses", headers=auth,
                      json={"description": "Tickets", "amount": 500, "paid_by_member_id": ids[1], "split_type": "custom",
                            "custom": [{"member_id": ids[0], "amount": 100}, {"member_id": ids[1], "amount": 100}]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "split_mismatch"
    one = client.post(f"/api/trips/{trip['id']}/expenses", headers=auth,
                      json={"description": "Gift", "amount": 300, "paid_by_member_id": ids[2], "split_type": "one_person"})
    assert one.status_code == 201
    sel = client.post(f"/api/trips/{trip['id']}/expenses", headers=auth,
                      json={"description": "Chai", "amount": 90, "paid_by_member_id": ids[3], "split_type": "selected",
                            "member_ids": [ids[2], ids[3]]})
    assert sel.status_code == 201
    s = client.get(f"/api/trips/{trip['id']}/settlement", headers=auth).json()
    assert round(sum(m["net"] for m in s["members"]), 2) == 0
