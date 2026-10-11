# ADR 0008 — Phase 5: data layer and execution

Status: **Accepted and implemented** (approved 2026-10-07 with the decisions below). Date: 2026-10-07.

> Read first: what was built and what was **not verified** are in "Results" and "Not verified" at the end. Everything that needs PostgreSQL, Redis, gunicorn or Docker is listed in `VERIFY.md` with the exact commands.

## Context

**How a plan runs today** (`api/routes/trips.py::stream_plan`): the request starts a daemon thread inside the API process, and the response
relays that thread's events from an in-process `queue.Queue`. Consequences:

| Fact | Consequence |
|---|---|
| Nothing bounds the number of plans running in a process | Each plan also starts pools of up to 6 to 8 threads |
| The thread dies with the process (deploy, crash, `--reload`) | The trip stays `planning` and **the quota stays consumed** (Phase 0 risk 7, invariant I4) until the 2-minute guard lapses; nothing refunds it |
| Quota reserve (DB) and "start the work" (memory) are two separate steps | A crash between them loses the plan and keeps the quota |
| Events live in one process's memory | A second worker cannot relay another worker's plan |
| Entrypoint is `uvicorn --workers 2`; no gunicorn; no `/readyz`; SQLite is accepted in production | See "Execution" below |
| Response cache is `api_cache` in the database only; TTLs are literals spread over six files | Fine today (see measurement), but hard to tune |

### Measured this session (free: simulated providers, SQLite, one uvicorn worker, real HTTP streaming)

Script `scripts/load_check.py`; reports `docs/perf/load-2026-10-07.md` and `load-2026-10-07-200ms.md`.

| Plans at once | No added latency: p95 s / first event p95 s | 200 ms on every provider call: p95 s / first event p95 s | Peak threads (200 ms run) |
|---:|---|---|---:|
| 1 | 0.33 / 0.24 | 2.3 / 0.4 | 15 |
| 5 | 0.96 / 0.89 | not run | not run |
| 10 | 1.8 / 1.4 | 3.3 / 1.0 | 94 |
| 20 | 3.6 / 3.3 | not run | not run |
| 25 | not run | 6.4 / **2.1** | 110 |
| 50 | not run | 11.5 / **9.5** | 183 |

What this tells us:

1. **Throughput is not the problem.** One process finishes about 200 to 300 simulated plans a minute with **no failures at 50 at once**. The
   plan cap (`DAILY_PLAN_CAP=25` cold plans a day, ADR 0007) is two orders of magnitude below that. **A separate queue and worker fleet is not
   justified by load.** This confirms the Phase 0 note ("re-measure first").
2. **Bursts hurt the first progress event, not completion.** The SLO is first event p95 < 2 s (docs/SLO.md). It holds up to about 25 plans at once
   and fails at 50 (9.5 s): the planner's own work competes for the one Python interpreter lock. Bounding concurrent plans fixes this; a queue
   is just the way to say "wait your turn" politely.
3. **Simulated providers skip the gateway.** In production, many parallel plans meet the Google bulkhead (16 slots, 5 s wait, ADR 0007), and a
   call that waits too long becomes a degraded plan. So the number of plans allowed to run at once must be set **with** the bulkheads, not
   independently. Not measured live (it would cost money); stated as a risk.
4. **Not measured:** memory (`psutil` is not installed here; peak threads is recorded instead), PostgreSQL behaviour, and anything involving Redis.
   **This machine has no Docker, `psql` or Redis**, so none of the PostgreSQL or Redis paths below can be run here. See question 3.

The real, justified problems are therefore **I4 (a crash keeps the quota)**, **plan work lost on deploy**, **no cap on simultaneous plans**, and
**production being allowed to start on SQLite**. Those are the build items. Everything that only matters at larger scale is an interface with a
no-op default.

## Decision

**COMPLEXITY GUARD:** built for real where a measurement or an invariant demands it; otherwise an interface behind a flag, with the metric that
would justify turning it on.

| Item | Evidence | Decision |
|---|---|---|
| **Quota saga** with a durable job row: reserve → running → commit, or compensating refund on failure, timeout or crash | I4; Phase 0 risk 7 | **Build, on** |
| **Transactional outbox**: the job row is written in the same transaction as the quota change | Crash between reserve and start | **Build, on.** The `plan_jobs` table *is* the outbox |
| **Idempotency key** per plan request | A double tap or a retry must not start a second plan or charge twice | **Build, on** |
| **Bounded plan runner** with priority lanes (replans and paid users before new free plans) | First event p95 9.5 s at 50 at once; bulkhead interplay | **Build, on.** In-process, no new infrastructure |
| **Admission control**: past a limit "High demand — you're #N" with an ETA; past a hard limit, a polite refusal | Same | **Build, on** |
| **Reaper** (finds jobs whose heartbeat stopped, refunds, restores the trip) | Crash and deploy | **Build, on** |
| **Brownout L1–L4** as flags, applied in order, labelled to the user | Needs a lever before an overload, not after | **Build; manual level on; automatic trigger is an interface, off** (`BROWNOUT_AUTO=false`) |
| `/healthz`, `/readyz` (DB; Redis and queue only if configured) | Deploys need it | **Build, on** |
| **Production refuses SQLite** | The SQLite lock lesson (ADR 0002) | **Build, on** |
| gunicorn with uvicorn workers from env; DB pool sized to workers | `uvicorn --workers 2` hard-coded | **Build, on** |
| Expand/contract migration rule + a test that checks it | Rolling deploys | **Build, on** |
| Expired-row cleanup for both rail tiers | Check what `retention.py` covers today; add what is missing | **Build, on** |
| `scripts/restore_drill.py` and a backup runbook | Brief | **Build; cannot be run on this machine** |
| **Redis** as an optional service (queue transport, event relay, shared budget counters, cache tier, Phase 6 buckets) | Shared state across workers (ADR 0007 "approximate across workers") | **Interface only; needs your approval** (new infrastructure). `REDIS_URL` unset means in-process fallback |
| **arq job queue** on Redis, separate worker process | Not justified by load (above) | **Interface, off** (`PLAN_EXECUTOR=inprocess` default; `arq` accepted only when Redis is configured) |
| **Cache tiers** LRU → Redis → PostgreSQL → provider | DB lookups are about 1 ms of a plan that takes seconds; no measurement shows they matter | **Interface; the in-process LRU is built but sized 0 (off); Redis tier off** |
| **Stale-while-revalidate** | Already done for rail Tier 2 | **Rail: as is. Place details: flag, off** |
| **Pre-warming** nightly | RailRadar allows about 30 calls a day, 10 already go to refresh | **Interface, off** (`PREWARM_ENABLED=false`), with its own cap of a share of the daily budget |
| **Point-in-time recovery** | Needs WAL archiving: a managed database or new storage | **Your decision** (question 5) |

### 1. Durable plan jobs (the saga and the outbox)

New table **`plan_jobs`** (migration 0009): `id`, `trip_id`, `user_id`, `idempotency_key` (unique), `kind` (`new` | `replan`), `lane`,
`state` (`pending` | `running` | `done` | `failed` | `refunded` | `refused`), `quota_source`, `attempt`, `created_at`, `started_at`,
`heartbeat_at`, `finished_at`, `error_code`. Indexes on `(state, lane, created_at)` and the unique key.

The request transaction does, in one commit: reserve quota (existing atomic conditional `UPDATE`) **and** insert the job as `pending`. Either both
exist or neither. A runner then claims a job with a conditional `UPDATE ... WHERE state='pending'` (works on SQLite and PostgreSQL; no
`SKIP LOCKED` needed).

States and compensation:

| Event | Result |
|---|---|
| Plan finishes with an itinerary | `done`; quota stays consumed |
| Infeasible or provider unavailable (existing paths) | `failed`; quota refunded (existing `quota_service.refund`, now called from one place) |
| Process dies mid-plan | Heartbeat stops (written every 15 s by one thread per process, short session, never inside the planner path: ADR 0002 lesson). The reaper marks the job `refunded`, refunds once, restores the trip's status |
| Deploy restarts the process | On start, `pending` jobs younger than 5 minutes run again (idempotent); older ones are refunded |
| Plan runs past its 60 s deadline plus margin | Same as a crash |
| Same request sent twice | Same `idempotency_key` returns the same job's stream; no second reserve |

Refund is exactly-once because `refunded` is reached by a conditional `UPDATE ... WHERE state IN ('pending','running')`; the loser of a race
changes nothing. This is the invariant tested hardest (I4).

Idempotency key: the `Idempotency-Key` header if the client sends one; otherwise derived from `trip_id`, `plan_count` and a hash of the change
text, so a double tap collapses without any frontend change.

### 2. Runner, lanes and admission

`PlanRunner` interface with one implementation now, **`InProcessRunner`**: a bounded pool (`MAX_CONCURRENT_PLANS` per process) pulling from a
priority queue. Lanes, in order: **replan**, **paid** (Plus or credit), **free new plan**. (Phase 6 adds a lowest lane for new unverified accounts.)
A lane cannot starve the one below it for more than `LANE_MAX_WAIT_SECONDS` (aging), so free plans always move.

| Setting | Proposed default | Why |
|---|---|---|
| `MAX_CONCURRENT_PLANS` | 12 per process | Below the Google bulkhead of 16, so plans do not queue behind a bulkhead and degrade; first event stays inside 2 s (measured fine to 25 on simulated providers) |
| `PLAN_QUEUE_LIMIT` | 40 waiting | Past it the user sees "High demand — you're #N" |
| `PLAN_QUEUE_HARD_LIMIT` | 100 waiting | Past it, new free plans are politely refused; replans and paid still enter |
| ETA | position × recent median plan time ÷ concurrency | Shown as a range, never as a promise |

What the user sees: the existing stream gains `{"type":"queue","position":N,"eta_seconds":S}` events, relayed unchanged; the frontend turns them
into one calm line in the existing progress screen (no banner). The hard limit reuses the **typed `unavailable` answer** from ADR 0007 (HTTP 200,
`reason: "high_demand"`, `retry_at`, `quota_used: false`, trip details saved). Nothing ever times out silently.

### 3. Brownout (flags, applied in order, labelled)

| Level | What changes | Label in Plan notes |
|---|---|---|
| L1 | Template text instead of AI narration | "Short descriptions today: we're busy." |
| L2 | Skip alternatives | "Alternatives are off while we're busy." |
| L3 | Smaller optimiser candidate sets | "Slightly fewer options were compared." |
| L4 | Rail limited to saved timetables and station guidance | "Live rail lookups are paused; showing saved timetables." |

`BROWNOUT_LEVEL=0..4` is a manual lever (env, no restart needed if read per plan). `BROWNOUT_AUTO=false`: the automatic trigger exists as a pure
function of queue wait and depth, **off** until a measurement shows a need. Labels go into the collapsed Plan notes card, not a banner. L1 to L4
change *effort*, never a fact: the I1 to I10 gates sit after them.

### 4. Production safety and health

- `ENVIRONMENT=production` with a SQLite `DATABASE_URL` stops the process at startup with a clear message. Tests and development are unaffected.
- `/healthz`: the process is alive (no dependencies). `/readyz`: database reachable; Redis reachable **only if `REDIS_URL` is set**; runner
  accepting work; returns 503 with the failing part so a load balancer drains the instance. (`/health` stays for compatibility.)
- `docker-entrypoint.sh`: gunicorn with `uvicorn.workers.UvicornWorker`; `WEB_CONCURRENCY`, `WORKER_TIMEOUT`, `GRACEFUL_TIMEOUT` from env.
  Graceful shutdown lets running plans finish up to the timeout; the rest are refunded by the reaper at the next start.
- Database pool: `DB_POOL_SIZE` and `DB_MAX_OVERFLOW` default from `WEB_CONCURRENCY × MAX_CONCURRENT_PLANS`, with a startup log line and a
  warning if the total can exceed PostgreSQL `max_connections` (the product of workers, pool size and replicas is the usual outage).

### 5. Data layer

- **One TTL table** (`providers/ttl.py`), values copied from today's literals (autocomplete 24 h, routes 6 h, place details 3 d, reverse 7 d,
  transit 12 h, weather 3 h, search 12 h, IRCTC1 station 1 d), each overridable by env. No behaviour change.
- **`CacheTier` chain**: `[LRU (size 0), Redis (off), DB]` behind `providers/cache.py::cached`. The default is the DB alone, exactly as today.
  **Constraints that bind every tier (not negotiable):** a tier never holds a provider that `storage_allowed` forbids (I7); a tier holding Google
  content expires no later than the DB row (≤ 7 days, within I6's 30-day coordinate limit); a Redis tier keeps no personal data.
- **Rail Tier 1/2:** confirm cleanup covers both (expired Tier 2 rows, negative results past their TTL, Tier 1 rows for retired sources); add a
  test for each table. Add an index only where `EXPLAIN` on PostgreSQL shows a scan — which cannot be done on this machine.
- **Pre-warm**: `PREWARM_ENABLED=false`. When on: nightly, top `PREWARM_TOP_N` routes by Tier 1 request count, stale before aging, inside
  `PREWARM_BUDGET_SHARE` of the RailRadar daily budget, background priority (never taking from users, ADR 0007).
- **Backups:** `scripts/restore_drill.py` (dump → restore into a scratch database → `alembic current`, row counts, a sample trip loads) and
  `docs/runbooks/db-restore.md`. PITR itself depends on question 5.

### 6. Migrations

Expand/contract: a release may add columns, tables and indexes (expand); a later release removes what nothing reads (contract). A test scans new
migrations for `drop_column`, `drop_table`, `alter_column` that narrows a type, or a `NOT NULL` without a default, and fails unless the migration
carries an explicit `# contract: <release that stopped reading it>` marker. Migration 0009 is expand-only.

## Flags

| Flag | Default | Off means |
|---|---|---|
| `PLAN_JOBS_ENABLED` | true | Today's behaviour: thread per request, no job row |
| `PLAN_EXECUTOR` | `inprocess` | (`arq` only with Redis) |
| `MAX_CONCURRENT_PLANS`, `PLAN_QUEUE_LIMIT`, `PLAN_QUEUE_HARD_LIMIT`, `LANE_MAX_WAIT_SECONDS` | 12, 40, 100, 120 | |
| `BROWNOUT_LEVEL` / `BROWNOUT_AUTO` | 0 / false | |
| `REDIS_URL` | unset | In-process fallback everywhere |
| `CACHE_LRU_ENTRIES` | 0 | No in-process tier |
| `PREWARM_ENABLED` | false | |
| `REAPER_ENABLED`, `REAPER_STALE_SECONDS` | true, 180 | |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | derived | |
| `WEB_CONCURRENCY`, `WORKER_TIMEOUT`, `GRACEFUL_TIMEOUT` | 2, 120, 30 | |

## Tests (all offline)

- **Saga:** reserve and job in one commit; simulated crash after reserve, mid-plan, and after success; the reaper refunds **once** under 10
  racing reapers; a refunded job never runs; duplicate request returns the same job; duplicate with Plus/credit sources; infeasible and
  unavailable paths refund once.
- **Runner:** lane order; aging; `#N` and ETA; hard limit refuses free plans, admits replans; concurrency never exceeds the limit under 100
  submissions; events relayed unchanged; the existing stream tests pass untouched.
- **Brownout:** each level changes exactly its stage and writes its label; level 0 is byte-identical to today's output; no level alters a train
  fact, fare or name (I1).
- **Production guard**, `/healthz`, `/readyz` (healthy, DB down → 503), migration lint, pool sizing arithmetic, cache tier constraints (I6/I7).
- **Regression:** the full suite (567), and `scripts/load_check.py` re-run to show first event p95 inside 2 s at 50 plans at once with the runner on.
- **Not testable here:** PostgreSQL concurrency, Redis, gunicorn on Linux, the restore drill. They will be reported as **not verified**, not as passed.

## Risks

- **A job row per plan adds 3 short writes.** SQLite lock lesson: they happen outside the planner's transaction, in their own sessions.
- **In-process runner state is per process.** With 2 workers, `#N` is per worker and the cap is per worker (so 2× in total). Accurate global
  position needs Redis. Stated in the UI as approximate.
- **Reaper false positives:** a slow but alive plan could be refunded and then finish. Guard: the finishing plan's commit is conditional on
  `state='running'`; if it lost the race it discards its result instead of double-delivering.
- **Re-running a job after a deploy spends provider calls again** (bounded by the gateway budgets, ADR 0007).

## Decisions (approved 2026-10-07)

| # | Decision |
|---|---|
| Overall | No job queue yet. Built: the job table, refund-once, duplicate-tap protection, the concurrency cap, manual brownout levels and the housekeeping items. Everything else is an off-by-default interface with the metric that would justify it. |
| 1 Redis | Yes, as an **optional free container** in docker-compose (`--profile redis`). Everything works in-process when `REDIS_URL` is unset. No managed Redis without asking. |
| 2 Queue | arq/RQ skipped. If a worker is added later: **arq**. |
| 3 Verification | Build now; every PostgreSQL, Redis, gunicorn and restore-drill path is **not verified**; `VERIFY.md` has the commands to run once Docker Desktop is installed. gunicorn does not run on Windows, so its path can only be verified in Docker. |
| 4 Numbers | 12 concurrent plans per process, queue limit 40, hard limit 100, **all env vars**. "You're #N" shows an estimated wait made from **recent real plan durations**, never a constant. With no finished plan to estimate from, it says "We'll start your plan as soon as there's room." |
| 5 PITR | Waits for Phase 9. Until then: a daily `pg_dump` with 14-day retention (compose `--profile backup`), the restore drill and a runbook that works for either choice. |
| 6 Brownout | Four levels as proposed, plain wording (below), and each label sits **next to the part it affects**, not only in Plan notes (I2). |
| Crash safety | The client sends an idempotency key per Plan tap; the refund is idempotent (a refund record keyed by job id, unique); a test kills a real process mid-plan and proves exactly one refund. |
| Open | Google terms (ADR 0005): still under the user's review; the conservative reading stays. |

### The words the user sees

| Level | Text | Where it shows |
|---|---|---|
| L1 | Trip description simplified to keep things fast | Under the trip summary; Plan notes |
| L2 | Alternatives aren't available right now | Under the summary, with the Swap icon; Plan notes |
| L3 | Fewer options were compared to keep things fast | Under the summary; Plan notes |
| L4 | Train times are from saved timetables only right now | On each train's ticket, in the rail section itself; Plan notes |

A test fails if a label contains a digit, a rupee sign or anything that could pass for a fact (I1).

## Results

**Measured** (`scripts/load_check.py`, simulated providers with a realistic latency mix: 94% 300-800 ms, 5% 3-5 s, 1% a 6 s timeout;
one process, SQLite; the test client shares the process, which overstates the server's cost). `psutil` is now in `requirements-dev.txt`.

| Plans at once | No runner: first event p95 | No runner: peak threads / RSS | **Runner (12 at once)**: first event p95 | First stage p95, plans that did not wait | Waited in line | Plan time p95 | Peak threads / RSS |
|---:|---:|---|---:|---:|---:|---:|---|
| 1 | 0.21 s | 15 / 249 MB | 0.22 s | 0.22 s | 0 | 10.4 s | 17 / 250 MB |
| 10-12 | 0.69 s | 82 / 267 MB | 0.98 s | 0.98 s | 0 | 14.9 s | 87 / 275 MB |
| 25 | **1.98 s** | 179 / 298 MB | 2.1 s | 1.9 s | 13 | 22.4 s | 108 / 301 MB |
| 50 | 3.56 s | 304 / 336 MB | 3.2 s | 2.7 s | 38 | 38.5 s | 138 / 328 MB |
| 100 | not run | not run | 7.7 s | 3.9 s | 88 | 77.1 s | 207 / 389 MB |

What this says:

- **The 2 s first-event target does not break before 25 plans at once**, with or without the runner; it is at the edge at 25 and fails at 50
  (3.2 to 3.6 s). Memory is about 2 MB per extra plan (about 250 MB for the process and the client).
- **The runner's value is not speed.** It bounds threads (138 against 304 at 50 at once), keeps a plan's own time near its 10 s while it
  runs, orders the line fairly, and turns waiting into something the person sees. The price is that the 50th plan waits its turn (38 s in
  all). With `DAILY_PLAN_CAP=25` cold plans a day, this range is theoretical; it is what a viral link would do.
- **Found by this run, and fixed:** with a plain (sync) relay, every open stream held one of FastAPI's 40 request threads, so 100 at once
  gave a first event p95 of **21.7 s**. The relay is now async (`Ticket.subscribe_async`): a waiting stream holds no thread, and the same
  run gave 7.7 s. What remains at 50+ is the request handling itself (SQLite writes, and the test client in the same interpreter); that cannot be
  separated on this machine, and it is the first thing `VERIFY.md` asks you to re-measure on PostgreSQL with 2 workers.

**Built and tested** (new tests: `test_plan_jobs.py` 17, `test_plan_runner.py` 14, `test_brownout.py` 16, `test_platform.py` 29; the existing
567 run through the new path unchanged, because the job path is on by default):

- **Quota saga and outbox.** `plan_jobs` row written in the same commit as the quota change; `plan_refunds.job_id` unique makes a refund
  exactly-once; every state change is a conditional UPDATE. Heartbeat every 15 s from one thread per process (own short session, never in the
  planner path). A reaper (every 30 s, safe in every process at once) re-runs a young interrupted plan once and refunds an older one; a
  process that shuts down hands the jobs it still holds in its queue to the reaper at once (a plan that is mid-run when the worker is finally killed is handled as a crash: re-run once if young, otherwise refunded).
- **The kill test.** A real second process holds a running plan and is killed (`proc.kill()`, no cleanup); the reaper then refunds it:
  `free_plans_left` goes 1 to 2, one `plan_refunds` row, trip back to `draft`, and three more reaper passes change nothing. Ten racing reapers
  in threads also produce exactly one refund.
- **Idempotency.** A key from the client (one per tap) is exact, even long after the plan ended: the retry gets the same result and no second
  charge. Without a key, an identical request while that plan is pending or running is the same plan; once it has ended, the same request is
  a new plan, so a retry after a failure really runs again (tested both ways). A lost race between two identical requests rolls back the
  second one's quota change before it was committed. The frontend sends a fresh UUID per tap and reconnects once with the **same** key if
  the connection drops.
- **Runner and admission.** Lanes (replan, paid, free), aging (`LANE_MAX_WAIT_SECONDS`), a burst of 100 never runs more than the limit,
  `#N` and the estimate update as plans ahead finish, hard limit refuses new free plans with the typed `unavailable` answer (`reason:
  "high_demand"`, HTTP 200, `quota_used: false`, nothing created) while replans and paid plans still enter.
- **Brownout.** Level read live from the environment, fixed for one plan; each level does exactly its one thing; no level changes a train
  name, number, time or fare (a test compares them across levels).
- **Housekeeping.** `/healthz`, `/readyz` (503 with the failing part), production refuses SQLite, DB pool sized from the plans a worker runs
  with a startup warning if workers x pool can exceed `POSTGRES_MAX_CONNECTIONS`, one TTL table (values unchanged; Google capped at 7 days),
  expired-row cleanup for both rail tiers plus anything from a source that may not be stored, old plan jobs purged after 30 days, an
  expand/contract migration lint (`scripts/migration_lint.py`, run as a test).
- **Interfaces, off.** LRU cache tier (`CACHE_LRU_ENTRIES=0`), Redis cache tier (`CACHE_REDIS_ENABLED=false`), pre-warm
  (`PREWARM_ENABLED=false`, with its own share of the day's RailRadar calls), `BROWNOUT_AUTO=false`, `PLAN_EXECUTOR=arq` (accepted, not wired).
  I7 is tested at the cache: an IRCTC1 answer reaches no tier unless it is on the allow-list.
- **Admin view.** `/api/system/plans` (404 for everyone but `ADMIN_EMAILS`): running, waiting by lane, limits, real median plan time, wait p95,
  the brownout level and what the numbers would suggest.
- **Screens.** "High demand — you're #N" with the wait estimate (desktop and 390 px checked), the "very busy" refusal, and the brownout labels
  next to the description, the alternatives and each train's ticket.

## What differs from the proposal

| Proposed | Built | Why |
|---|---|---|
| A finishing plan whose job was refunded discards its result | **Decided 2026-10-07: it keeps it and stays refunded.** The late finish is logged and counted (`plan_jobs.late_finish_at`, migration 0010; shown as `late_finishes` today/total in `/api/system/plans`) | Discarding means throwing away a finished plan the person is waiting for. The leak is bounded: only a plan whose process stopped heartbeating for 180 s, then came back, ends up refunded and delivered |
| Stale-while-revalidate for place details behind a flag | **Not built** | No measurement supports it, and a flag with no code is a stub. Rail Tier 2 already does it |
| Heartbeat for running jobs | Running **and waiting** jobs | A job waiting in this process's queue must not look orphaned |
| Sync stream relay | Async relay | See Results: it held a request thread per open stream |
| Table `plan_jobs` only | `plan_jobs` and `plan_refunds` | The refund record keyed by job id was asked for |

## Verification without Docker (2026-10-07; the start of Phase 9's CI)

Docker Desktop would not install on the owner's laptop, so `.github/workflows/verify.yml` runs every VERIFY.md step on GitHub's `ubuntu-latest`
with PostgreSQL 16 and Redis 7 as service containers: migrations up/down/up, the full suite on PostgreSQL, the API under gunicorn with 2 workers
(through the real `docker-entrypoint.sh`), the Redis paths (including stopping and starting the Redis container), the backup script with its
retention rule and the restore drill, the load check with the latency mix, and the crash test (the whole gunicorn process group is SIGKILLed
mid-plan; one scenario asserts exactly one refund, the other that a young plan is re-run once). It never reads `.env`, blanks every key, forces
demo mode, and runs every step with `ci/netguard` on `PYTHONPATH`, which refuses any connection that is not to the machine itself; the last
step fails the run if one was attempted. Each step prints PASS/FAIL/SKIP lines; the run uploads `verify-results` (results.md, results.json, logs,
junit.xml). **The workflow has not run yet:** locally the logic was exercised on SQLite with uvicorn (the crash scenarios with a really killed
process, the burst, the load levels 1 and 12, the guard, and the whole suite under the guard: 644 passed, no connection attempted).
This list is updated from the run's `results.md`.

## Not verified (nothing in this list was run)

- **PostgreSQL:** the migration on PostgreSQL, `SKIP`-free claiming under real concurrency, the pool arithmetic against a real server, index needs
  (`EXPLAIN` on hot queries: the lookups by `trip_id`, the reaper's `state, heartbeat` scan), and the load figures above on PostgreSQL.
- **Redis:** the Redis client, `/readyz` with a live Redis, the Redis cache tier (it is tested with a fake client only).
- **gunicorn:** `gunicorn.conf.py` and the new `docker-entrypoint.sh` (gunicorn does not run on Windows), graceful shutdown handing jobs to the reaper.
- **Backups:** the compose `backup` service and `scripts/restore_drill.py` (written against `docker compose exec db`; never executed).
- **Two workers:** per-worker queues and limits, the DB-poll path of `_respond_job` when another worker holds the job.
- **Real providers:** nothing in this phase called one.

`VERIFY.md` has the commands and what a pass looks like.

## Not in this phase

Per-user and service-wide spend caps, kill switches, token buckets and the low-priority lane for unverified accounts (Phase 6); OpenTelemetry
traces across queue and worker, SLO dashboards and alerts (Phase 7); Hypothesis and chaos tests (Phase 8); point-in-time recovery (Phase 9).

**Still approximate:** daily budget counts across workers (ADR 0007). `app/core/redis_client.py` exists and `/readyz` checks it, but nothing keeps shared
budget counters in Redis yet; that waits for VERIFY.md step 6 and for Phase 6, which needs the same shared state for rate-limit buckets.
