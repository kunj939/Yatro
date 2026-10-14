"""Phase 4 drill (ADR 0007): the gateway against simulated provider failures. No real call is made and nothing touches the
app's database: it runs against its own throwaway one.

    python scripts/gateway_check.py

Prints what the gateway did in each scenario: calls actually sent, what was refused and why, and the day's budget use."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.update({"DATABASE_URL": f"sqlite:///{Path(tempfile.mkdtemp(prefix='yaitro-gw-')) / 'gw.db'}", "LOG_LEVEL": "ERROR",
                   "ENVIRONMENT": "test", "IRCTC1_ASSUME_EXHAUSTED": "false"})
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import httpx  # noqa: E402

import app.models  # noqa: E402,F401
from app.core.config import get_settings  # noqa: E402
from app.core.errors import ProviderUnavailable  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.gateway import budgets, breakers, context, ledger, request  # noqa: E402
from app.providers import http  # noqa: E402

Base.metadata.create_all(engine)
s = get_settings()
s.budget_mode = "enforce"
time.sleep = lambda x: None  # no waiting
SEARCH = "https://places.googleapis.com/v1/places:searchText"
RAIL = "https://api.railradar.in/v1/trains/between/NDLS/AGC"
sent: list[str] = []


def fresh():
    """Forget breaker state, including the stored quota state (it survives restarts on purpose)."""
    from sqlalchemy import delete

    from app.db.session import SessionLocal
    from app.models.circuit import ProviderCircuit
    breakers.reset()
    with SessionLocal() as db:
        db.execute(delete(ProviderCircuit))
        db.commit()


def wire(handler):
    sent.clear()
    http._client = httpx.Client(transport=httpx.MockTransport(lambda r: (sent.append(str(r.url)), handler(r))[1]))


def attempt(label, fn, n=1):
    out = []
    for _ in range(n):
        try:
            fn()
            out.append("ok")
        except ProviderUnavailable as exc:
            out.append(type(exc).__name__)
    print(f"{label:<58} sent={len(sent):<3} {', '.join(sorted(set(out)))}")


print("-- breaker: a provider that keeps failing")
wire(lambda r: httpx.Response(500))
attempt("10 calls to a failing endpoint (500)", lambda: request("GET", RAIL, provider="RailRadar", retries=0), 10)
print("-- quota: a 429 that says the quota is used up")
fresh()
wire(lambda r: httpx.Response(429, text="You have exceeded the MONTHLY quota"))
attempt("5 calls after a quota answer", lambda: request("GET", RAIL, provider="RailRadar"), 5)
print("-- retry: one 503, then fine")
fresh()
answers = iter([httpx.Response(503), httpx.Response(200, json={})])
wire(lambda r: next(answers))
attempt("an idempotent call", lambda: request("GET", RAIL, provider="RailRadar"))
print("-- budget: Google at $0.10 a day, $0.032 a search")
s.google_daily_usd = 0.10
budgets.reset()
wire(lambda r: httpx.Response(200, json={}))
attempt("6 text searches", lambda: request("POST", SEARCH, provider="Google Places", json={}), 6)
print(f"   used ${budgets.used('google'):.3f} of ${s.google_daily_usd}; resets at {budgets.reset_at().isoformat()}")
print("-- deadline: a plan with half a second left")
wire(lambda r: httpx.Response(200, json={}))
with context.plan_scope(0.5) as scope:
    context.set_stage("routes")
    attempt("a call made after the deadline", lambda: request("POST", SEARCH, provider="Google Places", json={}))
print(f"   deadline reached during stage {scope.deadline_stage!r}; degraded parts recorded: {len(scope.degraded)}")
print("-- ledger")
for row in ledger.snapshot()["endpoints"]:
    print(f"   {row['provider']:<14} {row['endpoint']:<44} calls={int(row['calls'])} errors={int(row['errors'])} "
          f"retries={int(row['retries'])} breaker/budget/deadline skips={int(row['circuit_skips'])}/{int(row['budget_stops'])}/{int(row['deadline_skips'])}")
