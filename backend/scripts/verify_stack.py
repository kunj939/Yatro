"""Checks a RUNNING stack from outside (VERIFY.md). Uses only the public API, in demo mode; nothing here calls a real provider.

    python scripts/verify_stack.py basic  [--base http://localhost:8000]
    python scripts/verify_stack.py crash-start   # holds a plan open; then kill the backend container (VERIFY.md step 7)
    python scripts/verify_stack.py crash-check   # after the backend is back: was the plan finished or refunded, exactly once?

Prints one line per check, "PASS" or "FAIL", and exits 1 if any failed.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

import httpx

STATE = Path(tempfile.gettempdir()) / "yaitro-verify-crash.json"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""), flush=True)
    return ok


def register(c: httpx.Client, tag: str) -> dict:
    email, phone = f"verify-{tag}-{uuid.uuid4().hex[:6]}@example.com", f"+9171{uuid.uuid4().int % 10**8:08d}"
    # Sign-up verifies the email only; the mobile number is stored (unique) without a code (ADR 0010).
    code = c.post("/api/auth/send-code", json={"channel": "email", "target": email}).json()["dev_code"]
    r = c.post("/api/auth/register", json={"name": "Verify", "email": email, "phone": phone, "password": "verify12345", "password_confirm": "verify12345",
                                           "email_code": code})
    r.raise_for_status()
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def new_trip(c: httpx.Client, auth: dict) -> str:
    d = date.today() + timedelta(days=21)
    body = {"origin": {"name": "Gurugram"}, "destination": {"name": "Jaipur"}, "start_date": d.isoformat(), "start_time": "05:00", "end_time": "23:30",
            "budget": 5000, "travellers": 2, "traveller_type": "couple", "transport_modes": ["train", "cab"], "food": "veg",
            "trip_styles": ["balanced"], "stay": "no_stay"}
    r = c.post("/api/trips", json=body, headers=auth)
    r.raise_for_status()
    return r.json()["id"]


def left(c: httpx.Client, auth: dict) -> int:
    return c.get("/api/auth/me", headers=auth).json()["subscription"]["free_plans_left"]


def stream(c: httpx.Client, auth: dict, trip: str, key: str) -> list[dict]:
    events: list[dict] = []
    with c.stream("POST", f"/api/trips/{trip}/plan?stream=true", headers={**auth, "Idempotency-Key": key}, json={"stream": True}, timeout=600) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line.strip():
                events.append(json.loads(line))
    return events


def require_demo(base: str) -> None:
    """Refuse to touch a server that could call a real provider. These checks make plans; on a live server each costs money."""
    try:
        mode = httpx.get(base + "/api/config", timeout=15).json().get("mode")
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"Nothing answered at {base} ({type(exc).__name__}). Start the verification stack first.")
    if mode != "demo":
        sys.exit(f"REFUSING: the server at {base} is in '{mode}' mode, not 'demo'. These checks would make real plans and could bill Google. "
                 "Use the verification stack (docker-compose.verify.yml), which blanks every key.")


def basic(base: str) -> None:
    require_demo(base)
    c = httpx.Client(base_url=base, timeout=60)
    r = c.get("/api/healthz")
    check("healthz answers", r.status_code == 200 and r.json() == {"status": "ok"})
    r = c.get("/api/readyz")
    body = r.json()
    check("readyz is ready", r.status_code == 200 and body["status"] == "ready", json.dumps(body["checks"]))

    auth = register(c, "a")
    trip = new_trip(c, auth)
    before = left(c, auth)
    key = "verify-" + uuid.uuid4().hex
    one = c.post(f"/api/trips/{trip}/plan", headers={**auth, "Idempotency-Key": key}, json={})
    two = c.post(f"/api/trips/{trip}/plan", headers={**auth, "Idempotency-Key": key}, json={})
    check("a plan with a key is ready", one.status_code == 200 and one.json().get("status") == "ready", one.text[:120])
    check("the same key again is the same plan", two.status_code == 200 and two.json().get("status") == "ready")
    check("and cost exactly one free plan", left(c, auth) == before - 1, f"{before} -> {left(c, auth)}")

    trip2 = new_trip(c, auth)
    ev = stream(c, auth, trip2, "verify-" + uuid.uuid4().hex)
    check("a streamed plan ends with a result and no error", ev[-1]["type"] == "result" and not [e for e in ev if e["type"] == "error"])

    n = 10
    out: list[list[dict]] = [[] for _ in range(n)]
    users = [register(c, f"b{i}") for i in range(n)]
    trips = [new_trip(c, users[i]) for i in range(n)]

    def go(i):
        with httpx.Client(base_url=base, timeout=600) as cc:
            out[i] = stream(cc, users[i], trips[i], "verify-" + uuid.uuid4().hex)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    t0 = time.time()
    [t.start() for t in ts]
    [t.join() for t in ts]
    queued = [e for evs in out for e in evs if e["type"] == "queue"]
    check(f"{n} plans at once all finish", all(evs and evs[-1]["type"] == "result" and evs[-1].get("status") == "ready" for evs in out),
          f"{time.time() - t0:.1f}s")
    check("some waited in line, and were told their place", bool(queued) and all(e["position"] >= 1 for e in queued),
          f"{len(queued)} queue events, e.g. {queued[0] if queued else None}")
    check("each user paid exactly one plan", all(left(c, a) == 49 for a in users), "50 free plans in this stack")
    r = c.get("/api/system/plans")
    check("the plans view is hidden from the public", r.status_code == 404)


def crash_start(base: str) -> None:
    require_demo(base)
    c = httpx.Client(base_url=base, timeout=60)
    auth = register(c, "crash")
    trip = new_trip(c, auth)
    key = "crash-" + uuid.uuid4().hex
    STATE.write_text(json.dumps({"auth": auth, "trip": trip, "key": key, "left_before": left(c, auth)}))
    print(f"state saved to {STATE}\nPlan started with key {key}. NOW, in another terminal:  docker compose kill backend\n"
          "(this needs the stack started with PLAN_TEST_DELAY_SECONDS=30)", flush=True)
    try:
        ev = stream(c, auth, trip, key)
        print("stream ended with", ev[-1] if ev else None, "- the plan finished before it was killed; use a longer PLAN_TEST_DELAY_SECONDS")
    except Exception as exc:  # the connection dropping is the point of this step
        print("connection dropped as expected:", type(exc).__name__)
    print("Now: docker compose up -d backend   then wait 20 seconds, then:  python scripts/verify_stack.py crash-check")


def crash_check(base: str) -> None:
    s = json.loads(STATE.read_text())
    c = httpx.Client(base_url=base, timeout=60)
    after_restart = left(c, s["auth"])
    check("the backend is back and the user can still sign in", after_restart in (s["left_before"], s["left_before"] - 1), f"free plans {s['left_before']} -> {after_restart}")
    deadline = time.time() + 90
    ev: list[dict] = []
    while time.time() < deadline:  # the same tap, sent again with the same key: it must find its plan, not start a new one
        ev = stream(c, s["auth"], s["trip"], s["key"])
        if ev and ev[-1]["type"] == "result":
            break
        time.sleep(3)
    last = ev[-1] if ev else {}
    check("the same key gets an answer, not an error", last.get("type") == "result", last.get("state") or last.get("status") or str(last)[:100])
    final = left(c, s["auth"])
    if last.get("state") == "unavailable":
        check("a plan that was lost is refunded: the free plan is back", final == s["left_before"], f"{s['left_before']} -> {final}")
    else:
        check("a plan that finished used exactly one free plan", final == s["left_before"] - 1, f"{s['left_before']} -> {final}")
    print("\nNow confirm in the database that there is exactly one job and at most one refund:\n"
          f"  docker compose exec db psql -U tripai -d tripai -c \"select state, attempt, error_code from plan_jobs where idempotency_key = '{s['key']}'\"\n"
          f"  docker compose exec db psql -U tripai -d tripai -c \"select count(*) from plan_refunds r join plan_jobs j on j.id = r.job_id where j.idempotency_key = '{s['key']}'\"")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["basic", "crash-start", "crash-check"])
    ap.add_argument("--base", default="http://localhost:8000")
    a = ap.parse_args()
    {"basic": basic, "crash-start": crash_start, "crash-check": crash_check}[a.step](a.base)
    sys.exit(1 if any(not ok for _, ok, _ in results) else 0)
