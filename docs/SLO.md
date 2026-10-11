# Service level objectives

Proposed in ADR 0001 (Phase 0). The numbers in "Measured" come from `docs/perf/baseline-live-2026-10-06.md`:
14 live plans, SQLite, one process, cold then warm cache.

| SLO | Target | Window | Measured (Phase 0) | Status |
|---|---|---|---|---|
| Trip view availability (`GET /trips/{id}`, share page) | 99.5% non-5xx | 30 days | not yet measured (no production traffic) | — |
| Time to first progress event (SSE) | p95 < 2 s | 30 days | 0–4.1 s for 13 of 14 plans; **26.6 s** for Udaipur → Rishikesh | **At risk** |
| Plan completion | p95 < 45 s, p99 < 90 s | 30 days | median 80.6 s, max 183 s | **Breached (on SQLite)** |
| Plans ending in a 5xx because a provider failed | 0 (I3) | 30 days | 0 of 14, despite 6 failed provider calls | Met |
| Free plans lost to a crash or deploy (I4) | 0 | 30 days | a killed plan is refunded exactly once (real-process test, ADR 0008); not yet measured in production | Met in test |

## What drives the breaches

1. **Plan completion is lost to SQLite lock waits** (194 of 215 s in a profiled plan).
   - **Cause:** the planner's session keeps a write transaction open from the places stage to the end of the plan,
     so each response-cache write from another session waits 30 s (`busy_timeout`) and then fails.
   - **Fix:** commit after `upsert_places`. That's a one-line change, proposed as the first item of Phase 1.
   - **Production:** PostgreSQL does not block this way, but the long transaction is still poor practice there.
2. **First progress event:** place and station resolution happens before the first stage event is emitted.
   - **Seen:** a slow autocomplete call (up to 11 s) and failing IRCTC1 station searches delayed the first event to 26.6 s.
   - **Fix:** emit the first stage before any provider call.

## Error-budget policy

- **Budget:** 30-day rolling, per SLO; for 99.5% availability that is about 3.6 h of failed requests.
- **50% spent:** feature work on the affected path pauses; the next change on that path must be a reliability fix.
- **100% spent:** only fixes and rollbacks ship until the 30-day window recovers.
- **Single incidents:** any one incident spending more than 20% of a budget gets a written post-mortem with a runbook update.
- **Owner:** reviewed weekly against the Phase 7 dashboards. Until those exist, use the Phase 0 trace logs
  (`tripai.telemetry` "plan trace" lines).

## Phase 5 update (2026-10-07, ADR 0008)

Measured with simulated providers and a realistic latency mix (`scripts/load_check.py`), one process, SQLite, `MAX_CONCURRENT_PLANS=12`:

| Plans at once | First event p95 | Plan completion p95 |
|---:|---:|---:|
| 1 | 0.22 s | 10.4 s |
| 12 | 0.98 s | 14.9 s |
| 25 | **2.1 s** | 22.4 s (13 waited in line) |
| 50 | 3.2 s | 38.5 s (38 waited in line) |
| 100 | 7.7 s | 77.1 s (88 waited in line) |

- **First event**: the target holds up to about 25 plans at once and fails above that on this setup. The first thing to try for more is more workers
  (gunicorn) on PostgreSQL; that is not measured yet (VERIFY.md step 9). With `DAILY_PLAN_CAP=25` the range above 25 is a viral-link case.
- **Plan completion**: a plan that does not have to wait finishes in about 10 s here. The completion SLO counts waiting; at 50 at once the 50th plan waits its turn.
- **Not measured**: real providers, PostgreSQL, more than one worker.
