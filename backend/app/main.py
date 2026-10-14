"""Yaitro API — FastAPI application factory."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import admin, auth, calendar, demo, places, share, subscription, system, trips
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware

log = logging.getLogger("tripai")


@asynccontextmanager
async def lifespan(_: FastAPI):
    from app.providers.registry import get_providers
    settings = get_settings()
    from app.gateway import capacity
    from app.services.plan_runner import runner as plan_runner
    plan_runner.start()  # take plans (ADR 0008)
    capacity.check_consistency()  # DAILY_PLAN_CAP must sit below what GOOGLE_DAILY_USD can pay for (ADR 0007)
    log.info("%s API ready (environment=%s, data mode=%s)", settings.app_name, settings.environment,
             get_providers().mode)
    if settings.environment != "test":  # in the background: the server accepts requests straight away
        import threading

        from app.utils.locale import warm_up
        threading.Thread(target=warm_up, name="locale-warmup", daemon=True).start()

        from app.services.retention import start_retention_thread
        start_retention_thread()  # I6: Google content older than 30 days is cleared (ADR 0002)

        from app.services.rail_refresh import start_refresh_thread
        start_refresh_thread()  # Tier 2 refresh within RAIL_REFRESH_DAILY_CAP (ADR 0003)
        from app.gateway.budget import start_flush_thread
        start_flush_thread()  # daily budget counts are written out every few seconds (ADR 0007)
        from app.services.plan_runner import start_background_threads
        start_background_threads()  # the plan-job reaper: refunds or re-runs plans whose process died (ADR 0008)
    yield
    if settings.environment != "test":  # (tests start and stop several apps in one process)
        plan_runner.shutdown()  # hand the plans this process holds to the reaper straight away


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()
    app = FastAPI(
        lifespan=lifespan,
        title=f"{settings.app_name} API",
        version="1.0.0",
        description="Budget-aware, verification-first trip planning. Deterministic engines own every number; "
                    "the LLM only explains.",
        docs_url="/api/docs", redoc_url="/api/redoc", openapi_url="/api/openapi.json",
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "X-Request-Id", "Idempotency-Key"],
                       expose_headers=["X-Request-Id"])
    register_error_handlers(app)
    for r in (system.router, admin.router, auth.router, trips.router, share.router, calendar.router, places.router,
              subscription.router, demo.router):
        app.include_router(r, prefix=settings.api_prefix)

    return app


app = create_app()
