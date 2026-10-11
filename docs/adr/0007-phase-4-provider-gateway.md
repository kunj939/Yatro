# ADR 0007 — Phase 4: provider gateway

Status: **Accepted and implemented** (approved 2026-10-06 with the decisions below). Date: 2026-10-06.

## Context

**Today** every outbound call goes through one of two paths:
- `providers/http.py::request`. It has one fixed retry (0.6 s, no jitter) on any timeout, 5xx **and 429**, for GET and POST alike.
  Google, OpenRouter, search and weather use it.
- `railradar._get` and `indian_rail._get`. They call `http.client()` directly, with their own 20 s timeout, their own 429 handling and a
  `quota_used_up` flag that lives on one provider object and dies with it.

There is **no circuit breaker, no concurrency limit, no spend limit, no deadline** and no shared view of a provider's health.
Phase 0 and Phase 2 measured what that costs:

| Evidence | Source | What it shows |
|---|---|---|
| IRCTC1's monthly quota is used up, and **every Mussoorie search still makes the failing call** (the one warm call in the rail baseline) | rail-baseline-2026-10-06 | No memory that a provider is exhausted |
| `autocomplete` max **11.1 s**, 2 of 11 failed; first progress event **26.6 s** on one route | SLO.md, Phase 0 | One slow call has no timeout of its own |
| OpenRouter p50 1.8 s, **max 11.4 s** | Phase 0 | Same |
| Plan completion median 80 s (mostly SQLite locks, fixed in ADR 0002), SLO p95 < 45 s | SLO.md | There is no per-plan deadline to enforce |
| Google costs **$0.22–0.35 per cold plan**; nothing bounds the day's spend | COST.md | I5 is not met today |
| RailRadar's free tier is about 33 calls a day, and Phase 2's refresh worker spends from the same pool | COST.md, ADR 0003 | Background work can starve users |
| **When the places provider is down, a plan returns HTTP 503** (measured this session with a simulated outage). The quota is refunded, so I4 holds, but I3 does not | this ADR | A Google outage or a Google budget stop would be a 5xx |
| Routes down: plan still completes (HTTP 200, estimated legs) | this ADR | Optional sources already degrade correctly |

**Not measured.** Duplicate concurrent identical calls, behaviour under concurrent plans, and the real rate-limit headers RailRadar and
IRCTC1 send. Phase 0 ran one plan at a time. This shapes the decision below.

## Decision

One module, **`app/gateway/`**, sits **below the response caches** and **above `httpx`**. A cache hit never reaches it, so only real calls
are counted, charged and limited. Every provider call goes through `gateway.call(...)`. `http.request` becomes a thin wrapper, and
RailRadar and IRCTC1 stop using `http.client()` directly. A call is identified by provider and endpoint template (the existing
`telemetry.provider_of` and `endpoint_template`).

**COMPLEXITY GUARD:** each part is built for real only where Phase 0/2 data or an SLO risk justifies it. The rest is an interface with a no-op default
behind a flag, built so its own metrics produce the justification.

| Item | Evidence | Decision |
|---|---|---|
| Per-endpoint timeouts, **deadline propagation** | 11 s outliers, 26.6 s first event, SLO breach | **Build, on** |
| Retries: jittered, idempotent and retryable only, **retry budget** | Today's retry also retries 429 and re-sends paid POSTs | **Build, on** |
| **Circuit breaker** per provider + endpoint; quota-exhausted opens immediately | IRCTC1 failing on every call | **Build, on** |
| **Daily budgets**, cost ledger, warn 80% / stop 100% | I5; $0.25 per plan, unbounded | **Build, on** |
| Quota from response headers | Quota exhaustion seen; header names unverified | **Build; headers discovered from the ledger, then used** |
| **Bulkheads** (static limit per provider) | RailRadar 429 "rate limited" path exists; thread pools of 6 to 8 per plan multiply per concurrent plan | **Build, on** |
| **Graceful provider-down plan result** | Measured HTTP 503 | **Build, on** |
| Single-flight coalescing | Not measured | **Interface, no-op default (`GATEWAY_SINGLE_FLIGHT=false`)**. The ledger counts identical in-flight calls (`coalescible`), which is the number that would justify it. |
| Adaptive concurrency (AIMD) | Not measured under load | **Interface, off (`GATEWAY_AIMD=false`)** |
| Health-scored routing | Only two live rail sources; the breaker already skips a failing one | **Interface, no-op (`GATEWAY_HEALTH_ROUTING=false`)** that orders sources by score; the default order is unchanged. The score is computed and exported either way. |
| Hedged or duplicate calls to paid APIs | Forbidden by the brief | **Never** |

### 1. Timeouts and deadlines

- Per-endpoint timeouts in a table, from the measured p50 and max, overridable by env (`GATEWAY_TIMEOUTS` JSON):

| Endpoint | p50 / max (ms) | Timeout |
|---|---|---:|
| Google `autocomplete` | 470 / 11,063 | 3 s |
| Google `searchText` | 351 / 3,847 | 6 s |
| Google `computeRoutes`, route matrix | 161 / 7,232 | 6 s |
| Google place details | 123 / 296 | 4 s |
| RailRadar | 96 / 454 | 8 s |
| IRCTC1 | 391 / 406 | 6 s |
| OpenRouter (LLM) | 1,777 / 11,390 | 15 s |
| Open-Meteo, search providers | 793 / 814 | 4 s |

- **A plan deadline** (`PLAN_DEADLINE_SECONDS`, default 60: between the SLO's p95 of 45 s and p99 of 90 s) is set when planning starts and
  travels in a contextvar. `ContextThreadPool` already carries contextvars into worker threads.
- Each call's timeout is `min(endpoint timeout, time left)`. If less than 1 s is left, the call is not made and raises
  `DeadlineExceeded`. The planner then uses its existing fallbacks (estimated legs, template text).

### 2. Retries

- Retry only **idempotent** calls (GETs, and POSTs that are reads: search, autocomplete, routes), and only on connect errors, timeouts, 502, 503, 504 and
  429 **without** quota wording. Never on 500, 4xx or quota 429s.
- Never retried: the LLM (non-idempotent as billed; the template fallback already exists), and anything marked `idempotent=False`.
- At most one retry, with jittered exponential backoff (0.3 s base, 2 s cap, full jitter), and only if the deadline allows it. `Retry-After` is honoured.
- **Retry budget** per provider: every call adds 0.1 tokens, a retry costs 1, the cap is 10. A retry storm cannot double the spend.
- A timed-out paid call is charged its full price (the provider may have billed it). A 5xx is not.

### 3. Circuit breaker (per provider + endpoint)

- **Closed → open** after 5 consecutive failures, or at least 50% failures over the last 20 calls with at least 10 calls. **Open → half-open** after 30 s, then 60 s,
  120 s, up to 5 min. A half-open breaker admits **one** probe call.
- **Quota exhausted** (a 429 with quota or limit wording, or a remaining-quota header at 0) **opens at once** for `BREAKER_QUOTA_OPEN_MINUTES` (default 360), then one probe.
- Quota-open state is **persisted** (migration 0008, table `provider_circuit`: provider, endpoint, open_until, reason, so a restart or another
  worker doesn't spend a call to rediscover it. With `--reload` in dev, that happens on every edit today.
- An open breaker raises `CircuitOpen` immediately, with no network call.

### 4. Bulkheads

A semaphore per provider: Google 16, RailRadar 4, IRCTC1 2, LLM 4, others 4 (env-tunable). The wait counts against the deadline; a call that would
wait past it fails fast with `BulkheadFull`. AIMD would adjust these limits (flag, off).

### 5. Budgets and the cost ledger (I5)

- **Price per call** moves from `telemetry.DEFAULT_PRICES` to `gateway/prices.py`. Rail providers count quota units; Google, search and the LLM count dollars.
- **Reserve, then settle:** the call's estimated price is checked against the day's budget before it is sent and settled afterwards.
- **Warn at 80%** (a log line and a flag in the budgets endpoint), **stop at 100%.** A stop raises `BudgetExhausted`, a `ProviderUnavailable`,
  so the existing fall-through works: RailRadar → IRCTC1 → station guidance for rail.
- **Foreground first:** background work (the rail refresh worker, and later pre-warming) may use only the first **50%** of a day's budget, so it can't starve users.
- **Counting is in process** and flushed to `daily_counters` every 10 s, which also reads other workers' totals. With N workers the overshoot is bounded by about
  N × (spend per 10 s). Redis (Phase 5) makes it exact. I'm not adding infrastructure now.
- **No per-call database write.** Phase 0 showed what that does on SQLite.
- Each ledger entry records the provider, endpoint, outcome, retries, cost, latency and **user id**, so Phase 6 can add per-user and service-wide caps without
  changing the gateway. Those caps, kill switches and anomaly alerts are Phase 6, not here.
- `GET /api/system/budgets` returns today's usage, budget, breaker states and quota headers seen. This reads from the in-process ledger.

**Proposed daily defaults** (you must confirm; see the questions):

| Provider | Unit | Daily budget | Why |
|---|---|---:|---|
| Google (Places, Routes, Geocoding) | USD | **10** | about 40 cold plans a day |
| LLM (OpenRouter) | USD | 1 | about 2,000 plans at today's tokens |
| Search providers | USD | 1 | |
| RailRadar | calls | **30** | the free tier's 1,000 a month ÷ 30, plus a little headroom |
| IRCTC1 | calls | 10 | plan unknown, and its monthly quota is already exhausted |
| Open-Meteo | none (free) | no budget; breaker only | |

### 6. Quota from response headers

The gateway records any `x-ratelimit-*`, `ratelimit-*`, `x-rapidapi-*` and `retry-after` header it sees, per provider, in the ledger. It acts on the common
remaining-quota names (opens the breaker at 0, warns at 20% left). I believe RapidAPI sends `x-ratelimit-requests-remaining`, but I **haven't verified** it, and I don't know RailRadar's
headers. The plan is: observe first, then act on what is real. No test calls are made to find out.

### 7. I3: a provider failure never produces a 5xx

- **The gap** (measured): the places provider down means a plan returns 503.
- **The fix:** when a **core** source (places, or all of them) can't answer, the plan endpoint and the SSE stream return **HTTP 200** with
  `status: "unavailable"`, the reason, and a retry time:
  - "Our place data is busy right now. Nothing was used from your free plans. Try again in a few minutes."
  - The quota is refunded (I4 already works here).
  - The frontend shows this as a plain message in the plan view, **not a warning banner**.
- A Google budget stop uses the same path.
- Optional sources already degrade correctly (routes down gives a 200 with estimated legs).

### Telemetry

Gateway outcomes (ok, retried, breaker open, budget stop, deadline, bulkhead) go into the Phase 0 `PlanTrace`, so the baseline script shows them per plan.
`/api/system/budgets` is the first operational view; the full metrics are Phase 7.

## Flags (every part can be switched off)

| Flag | Default | Off means |
|---|---|---|
| `GATEWAY_ENABLED` | true | the old `http.request` behaviour, exactly |
| `GATEWAY_BREAKERS` | true | no breaker |
| `GATEWAY_RETRY_BUDGET` | true | the old retry rule |
| `GATEWAY_BULKHEADS` | true | unlimited concurrency |
| `BUDGET_MODE` | `enforce` | `warn` (log only) or `off` |
| `GATEWAY_SINGLE_FLIGHT` | **false** | in-process coalescing of identical concurrent calls |
| `GATEWAY_AIMD` | **false** | adaptive bulkhead limits |
| `GATEWAY_HEALTH_ROUTING` | **false** | health-ordered live sources |
| `PLAN_DEADLINE_SECONDS` | 60 | 0 = no deadline |

## Files

| File | Change |
|---|---|
| `app/gateway/` (new): `core.py`, `breaker.py`, `retry.py`, `bulkhead.py`, `budget.py`, `prices.py`, `quota_headers.py`, `singleflight.py`, `routing.py` | the gateway and its parts |
| `providers/http.py` | `request` delegates to the gateway; one client |
| `providers/railradar.py`, `indian_rail.py` | `_get` goes through the gateway; the 429 and `quota_used_up` logic moves into it |
| `providers/llm.py` | marks the call non-idempotent; the longer timeout |
| `services/rail_refresh.py` | `priority="background"` |
| `services/trip_service.py`, `api/routes/trips.py`, `services/planner.py` | the core-source-down path returns `status: "unavailable"` |
| `core/telemetry.py` | uses `gateway/prices.py`; records gateway outcomes |
| `core/config.py`, `models/`, migration 0008 | flags, budgets, `provider_circuit` |
| `api/routes/system.py` | `GET /system/budgets` |
| frontend plan view | a plain "busy right now" message for `status: "unavailable"` |
| `scripts/gateway_check.py` (new) | a simulated-failure drill, with no real calls |

## Tests

- **Breaker:** a state machine on a fake clock (closed, open, half-open, one probe, growing backoff); quota opens at once and survives a restart; per-endpoint isolation.
- **Retries:**
  - idempotent calls retried and non-idempotent not;
  - 500 and 4xx not retried;
  - the retry budget caps a failure storm;
  - jitter is bounded;
  - the deadline stops a retry;
  - `Retry-After` is honoured.
- **Deadline:** the call timeout is min(endpoint, remaining); under 1 s left means no call and a fallback; the contextvar reaches pool threads.
- **Bulkhead:** the limit holds under 50 threads; a wait past the deadline fails fast.
- **Budgets:**
  - warn at 80% and stop at 100%;
  - no call is sent after a stop;
  - background gets only 50%;
  - failed calls aren't charged and timeouts are;
  - the flush is exact within a process;
  - the ledger records the user id.
- **Fall-through:** RailRadar budget gone → IRCTC1 → station guidance, and **no 5xx** (I3); the order is right with the breaker open.
- **I3, end to end:** places down, budget exhausted, and every provider down each give HTTP 200 with `status: "unavailable"` and a refunded quota (I4), on both plan and stream.
- **No duplicate calls:** counting requests, a retried paid call is at most one extra, and never when the budget or the breaker says no.
- **Flags:** each switches its part off; the disabled gateway matches today's behaviour (the full existing suite passes both ways).
- **Interfaces:** single-flight, AIMD and health routing are tested on **and** off, and the `coalescible` counter is right.
- **Header parsing:** the common names, case-insensitive, malformed values ignored.
- **Migration 0008:** upgrade and downgrade on SQLite.
- **Drill:** `scripts/gateway_check.py` runs the failure scenarios against fake servers, with no real call.

## Consequences

- **Cost control (I5).** Google's spend per day becomes a hard number. A budget stop means no new plans until midnight UTC, with a clear message. The budget is therefore also
  a **product limit**, which is why the numbers are your decision.
- **Budgets are approximate across workers** until Redis (Phase 5). The overshoot is small and bounded, and stated.
- **The deadline can degrade a plan** (estimated legs instead of real ones) when providers are slow. That is the intended trade for the p95 SLO, and the plan says which source it used (I2).
- **IRCTC1** stops being asked while its quota is out, which removes the 2-of-2 failing station searches.
- **Not in this phase:** per-user and service-wide caps, kill switches and anomaly alerts (Phase 6); Redis and the queue (Phase 5); dashboards and alerts (Phase 7).

## Questions for you

1. **Daily budgets.** Are the defaults above right? In particular Google at **$10/day**: a hard stop for about 40 cold plans a day. Raise it for launch day, or start lower?
2. **Plan deadline of 60 s**, after which remaining calls are skipped and fallbacks are used. OK, or longer?
3. **IRCTC1.** Do you have another plan, or should it be treated as out until the monthly quota resets?
4. **System endpoints are open to any signed-in user** (`/system/rail-stats` today, and `/system/budgets` would be the same). I propose an `ADMIN_EMAILS` allow-list from the environment for both. OK?
5. **The "busy right now" message** for a provider outage or a budget stop: should it offer something (email me when it's back, or the sample trip), or just ask them to try again later?
6. **Phase 3's open items** are separate and still yours: the Google terms list in ADR 0005, and the 6-digit train number rule from the rail baseline.

## Decisions (2026-10-06) and what changed from the plan above

0. **The 503 is fixed in this phase.** When a required provider is down or out of budget, the plan endpoint (and the stream) answers
   **HTTP 200** with `{"state": "unavailable", "status": "unavailable", "reason": "provider_busy" | "daily_cap",
   "retry_at": "<ISO time>", "quota_used": false, "message": ..., "trip": {...}}`. The free plan is untouched: the checks run before the
   quota is reserved, and a failure mid-plan refunds it. The frontend shows a normal screen, not an error. Tested for the places outage
   (plan and stream), a Google budget stop (before planning and mid-plan), the daily cap, a replan, and with the gateway off.
1. **Budgets are env vars:** `GOOGLE_DAILY_USD`, `RAILRADAR_DAILY_CALLS`, `IRCTC1_DAILY_CALLS`, `LLM_DAILY_USD`, `SEARCH_DAILY_USD`
   (defaults as proposed). **The day resets at midnight in `BUDGET_TZ`, default `Asia/Kolkata`.**
   - **The plan cap and the Google budget agree.** The worst measured cold plan is $0.345 (New Delhi to Mussoorie), set as
     `COLD_PLAN_WORST_USD=0.35`.

     | Setting | Value | Why |
     |---|---:|---|
     | `GOOGLE_DAILY_USD` | $10.00 | |
     | Cold plans $10 pays for in the worst case | 28 | $10 ÷ $0.35 |
     | **`DAILY_PLAN_CAP`** | **25** | 3 below, so people meet the polite cap first |
     | Worst-case Google cost of 25 cold plans | $8.75 | |
     | Left for replans | $1.25 | about 25 replans at $0.05 |
     | Typical cost of 25 plans ($0.22 to $0.30) | $5.50 to $7.50 | |

   - A startup check logs a warning if the two settings stop agreeing, and a test checks the defaults.
   - **The cap applies only while planning uses live Google data**, and it is service-wide. Demo mode is not capped.
2. **Plan deadline 60 s** (`PLAN_DEADLINE_SECONDS`). When it is hit, remaining calls are skipped and the fallbacks are used. **Every degraded
   part is listed in the plan's Plan notes** ("Some live data took too long, so these parts are estimated or left out: travel times between
   stops. Everything else was checked as usual."). The stage that hit it is logged. Rail is not listed there: the source chain labels its own.
3. **IRCTC1 is treated as exhausted** (`IRCTC1_ASSUME_EXHAUSTED=true`, the default). Its circuit starts open and **at most one probe call a day**
   goes out to detect the reset. A good answer closes it; a quota answer keeps it open another 24 h. While it is open nothing about it reaches
   the traveller (a test checks this), and the rail chain doesn't count it. Setting the flag to false clears the assumption.
4. **`ADMIN_EMAILS`** (comma-separated, case-insensitive) gates `/system/rail-stats` and the new `/system/budgets`. **Non-admins, signed in
   or not, get a plain 404.** With the variable empty, nobody is an admin.
5. **The "busy right now" screen** shows the exact time new plans reopen in the person's own time zone ("tonight at midnight" when it is
   midnight), "None of your free plans were used", and the trip's details (kept server-side as a draft, so planning is one tap). It has a
   "Plan this trip" button that enables at that time, and a link to Your trips. It has **no sample-trip link** and **no "email me" option**.
   `BUSY_NOTIFY_EMAIL` is reserved and never read.
6. **Train numbers** (carried over from the rail baseline): Tier 2 validation accepts 4 to 6 digits and logs anything that isn't 5 digits;
   the AI output guard rejects any 4 to 6 digit number that isn't in the verified data.
7. **Google terms** (ADR 0005): still yours to confirm; the conservative reading stays in force.

**Differences from the plan above:**
- **Files.** The gateway is `context`, `errors`, `breaker`, `budget`, `ledger`, `retry`, `limits`, `singleflight`, `routing`, `capacity` and
  `core`. There is no `prices.py` (the telemetry price table is used) and no `quota_headers.py` (the header handling is in `ledger.py`).
- **LLM retries.** The LLM call is no longer retried at all (it was retried once before). It is billed per answer, and the template text is the fallback.
- **RailRadar and IRCTC1 retries.** Their own private retry loops are gone. The gateway's single budgeted retry replaces them, which also ends the
  up-to-four-attempts case. With `GATEWAY_ENABLED=false` they make one plain call with none of the protections.
- **Header check.** A remaining-quota header at 0 on a successful answer returns that answer and opens the circuit for the next call.
- **Breaker bug found in testing.** A slow read of stored state could overwrite another thread's "probe claimed" marker and let two probes
  through (a real race, found by the one-probe-a-day test). It is fixed with a generation counter, and the test passes on repeated runs.

## Results

**Tests.** 567 backend tests pass, 61 of them new in `tests/test_gateway.py`, plus 2 in `test_rail_storage.py`. The frontend type check is clean.
- **Breaker.** The five-failure open, the 50% rate open, half-open with exactly one probe, longer back-offs, per-endpoint isolation, and
  4xx answers not counting.
- **Quota.** A quota answer opens the provider at once with no further network calls and survives a restart; one probe, and a good answer
  closes it. The header-at-0 rule. IRCTC1: nothing sent on day one, exactly one probe a day under 5 concurrent threads, the day-two reset,
  and the flag turning the assumption off.
- **Retries.** Only idempotent calls and only on 502, 503, 504 and a non-quota 429. 500s, 4xx and quota answers are never retried. The
  retry budget holds a 100-call failure storm to about 120 calls, not 200. Jitter is bounded and `Retry-After` is honoured up to 2 s.
- **Deadline.** The call timeout is min(endpoint, time left). With under 1 s left no call is made, the stage is logged and the part is
  recorded. The deadline reaches pool threads.
- **Bulkheads and AIMD.** The concurrency limit holds under 24 threads. A wait past the deadline fails fast. AIMD does nothing when off.
- **Budgets.** Warn at 80%, stop at 100%, no call after a stop. Warn mode and off mode. Background gets half the day. A 5xx is not charged
  and a timeout is. The route matrix is priced per element. Flushes are exact and read back other workers' totals. The day rolls over at
  midnight in Kolkata and the zone is configurable. The ledger records the user id. All budgets and the plan cap come from env.
- **Fall-through.** A rail source over budget hands over to the next, and no call is sent to it.
- **No duplicate calls.** A paid call is never sent when the budget or breaker refuses it.
- **Flags.** Each switches its part off. With the gateway off the old behaviour returns, and the unavailable answer still holds.
- **Single-flight** shares one answer when on and only counts duplicates when off. **Health routing** keeps the order when off and orders by
  score when on.
- **Admin gate.** Both views 404 for everyone but admins, in every variation.
- **Migration 0008.** Upgrade and downgrade on SQLite. It is applied to the dev database (backup at `%TEMP%\tripai-before-0008.db`).

**Drill.** `scripts/gateway_check.py` runs the failure scenarios with no real call: 10 calls to a failing endpoint send 5 and then open the circuit;
a quota answer sends 1 of 5; a 503 then success sends 2; a $0.10 budget lets 3 of 6 searches through; and a call after the deadline sends none.

**On screen** (headless Chrome, fixtures): the busy screen for the daily cap on desktop and for a provider outage on a phone.

## Not done, and not verified

- **Real headers.** I have not seen the real quota headers from RailRadar or IRCTC1. The gateway records them and acts on the usual names
  (`x-ratelimit-requests-remaining` and similar). Check `/system/budgets` ("headers_seen") after real traffic.
- **Budgets across workers** are approximate until Redis (Phase 5): the overshoot is bounded by about N workers x one flush interval of spend.
  `/system/budgets` shows this worker's ledger; the budget totals include the others once flushed.
- **The plan cap** can overshoot by a plan or two under a race. The Google budget is the hard stop.
- **Breakers:** only the places provider's circuit stops a new plan before it starts. A routes outage degrades the plan instead (and is noted).
- **Still Phase 6:** per-user and service-wide spend caps, kill switches and anomaly alerts. The ledger already records the user id.
- **Not run against the live dev server.** It would cost real calls, so the live behaviour is verified by the simulated tests and the drill.
