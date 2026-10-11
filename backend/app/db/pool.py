"""Database pool sizing (ADR 0008).

The usual outage is arithmetic: workers x pool x replicas past what PostgreSQL allows. Each plan holds one long-lived session and
touches a few short ones (response cache, rail storage, gateway state), and the request threads need some too. So the pool is sized
from MAX_CONCURRENT_PLANS, and the total across workers is checked against POSTGRES_MAX_CONNECTIONS at startup.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PLAN_EXTRA = 6  # request threads, background threads, short sessions beside the plans


@dataclass
class PoolPlan:
    size: int
    overflow: int
    per_worker: int
    total: int
    warnings: list[str] = field(default_factory=list)


def plan(settings) -> PoolPlan:
    size = settings.db_pool_size if settings.db_pool_size is not None else max(5, settings.max_concurrent_plans + PLAN_EXTRA)
    overflow = settings.db_max_overflow if settings.db_max_overflow is not None else max(5, size // 2)
    per_worker = size + overflow
    total = per_worker * max(1, settings.web_concurrency)
    warnings: list[str] = []
    if not settings.is_sqlite and total > 0.8 * settings.postgres_max_connections:
        warnings.append(f"{settings.web_concurrency} workers x up to {per_worker} connections = {total}, which is more than 80% of "
                        f"POSTGRES_MAX_CONNECTIONS={settings.postgres_max_connections}. Lower DB_POOL_SIZE, DB_MAX_OVERFLOW or "
                        "WEB_CONCURRENCY, or raise the server limit; every extra replica multiplies this again.")
    return PoolPlan(size, overflow, per_worker, total, warnings)
