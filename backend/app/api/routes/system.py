from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import admin_user, providers_dep
from app.core import killswitch, runtime
from app.core.config import get_settings
from app.db.session import get_db
from app.providers.registry import Providers
from app.services import mailer

router = APIRouter(tags=["system"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/healthz")
def healthz():
    """Liveness: the process is up. Touches nothing else, so a slow database never gets a healthy process restarted."""
    return {"status": "ok"}


@router.get("/readyz")
def readyz():
    """Readiness: can this instance take work? Database always; Redis only if REDIS_URL is set; the plan runner. A failing
    part comes back with HTTP 503 and its name, so a load balancer stops sending traffic here."""
    from fastapi.responses import JSONResponse

    from app.core import redis_client
    from app.db.session import SessionLocal
    from app.services.plan_runner import runner
    checks: dict[str, dict] = {}
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        checks["database"] = {"ok": False, "detail": type(exc).__name__}
    ok, detail = redis_client.ping()
    checks["redis"] = {"ok": ok, "detail": detail}
    checks["plan_runner"] = {"ok": runner.accepting, "detail": "accepting plans" if runner.accepting else "shutting down"}
    ready = all(c["ok"] for c in checks.values())
    return JSONResponse({"status": "ready" if ready else "not_ready", "checks": checks}, status_code=200 if ready else 503)


@router.get("/system/plans")
def plans_view(_admin=Depends(admin_user)):
    """The plan runner right now: running, waiting by lane, limits, real median plan time, brownout level and what the numbers
    would suggest (ADR 0008)."""
    from app.services.plan_runner import runner
    return runner.stats()


@router.get("/config")
def public_config(p: Providers = Depends(providers_dep)):
    """Public, secret-free configuration for the frontend (never includes keys)."""
    s = get_settings()
    status = p.status()
    return {"app_name": s.app_name, "mode": status["mode"], "providers": status,
            "free_trip_plans": runtime.free_trip_plans(), "max_replans_per_trip": s.max_replans_per_trip,
            "prices": {"plus_monthly_inr": s.plus_price_inr, "one_trip_inr": s.one_trip_price_inr},
            "payments_mode": s.payments_mode, "currency": s.default_currency,
            "share_links_enabled": runtime.share_links_enabled(), "upi_links_enabled": s.upi_links_enabled,
            "signups_open": not killswitch.killed("signups"), "plans_open": not killswitch.killed("plans"),
            "plan_emails": mailer.configured() and not killswitch.killed("plan_emails")}


@router.get("/system/rail-stats")
def rail_stats(_admin=Depends(admin_user)):
    """Last 30 days of two-tier rail storage metrics (ADR 0003): Tier 2 hits and freshness mix, calls Tier 1 saved,
    refresh calls. Counts only; no personal data."""
    from app.providers.rail_storage import daily_stats
    return {"days": daily_stats(30)}


@router.get("/system/budgets")
def budgets_view(_admin=Depends(admin_user)):
    """Today's provider spend against its budget, the plan cap, breaker states, quota headers seen, and the cost ledger
    (ADR 0007). Read from this process's in-memory ledger; the totals include other workers once flushed."""
    from app.gateway import limits
    from app.gateway.breaker import breakers
    from app.gateway.budget import budgets
    from app.gateway.ledger import ledger
    s = get_settings()
    return {
        "day": budgets.day_key(), "timezone": s.budget_tz, "resets_at": budgets.reset_at().isoformat(),
        "mode": s.budget_mode, "budgets": budgets.status(), "breakers": breakers.snapshot(), "bulkheads": limits.snapshot(),
        "flags": {"gateway": s.gateway_enabled, "breakers": s.gateway_breakers, "retry_budget": s.gateway_retry_budget,
                  "bulkheads": s.gateway_bulkheads, "single_flight": s.gateway_single_flight, "aimd": s.gateway_aimd,
                  "health_routing": s.gateway_health_routing, "plan_deadline_seconds": s.plan_deadline_seconds},
        "plan_cap": {"cap": s.daily_plan_cap, "cold_plan_worst_usd": s.cold_plan_worst_usd,
                     "google_pays_for": int(s.google_daily_usd // s.cold_plan_worst_usd) if s.cold_plan_worst_usd else None},
        "ledger": ledger.snapshot(),
    }
