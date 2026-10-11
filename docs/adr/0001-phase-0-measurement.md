# ADR 0001 — Phase 0: measure one plan end to end, set SLOs, model cost

Status: **Accepted and implemented** (2026-10-06). Results are at the end.
Date: 2026-10-06

## Context

Every later phase (two-tier rail storage, provider gateway, job queue, brownouts) is only justified if it fixes a
measured problem or a concrete SLO risk. Today we have no numbers: no per-call latency, no count of external
calls per plan, no cost per plan, and no record of how many rail calls are spent on station pairs that return
nothing.

What the code already gives us to build on:

- **One HTTP chokepoint.** Google (Places, Routes, Geocoding), the LLM, web search and weather all go through
  `app/providers/http.py:request()`. The rail providers (`railradar.py`, `indian_rail.py`) call through their own
  `_get()`, wrapped by `providers/cache.py:cached()`.
- **Stage events.** `StageReporter` in the planner already emits the stage events the SSE stream relays.
- **LLM usage.** `LLMUsage` rows already record tokens and cost per call.
- **Station pair loop.** `rail_common.search_station_pairs()` is the one place every station pair is tried.

### Existing invariant conflicts found while reading the code (to fix in later phases, not Phase 0)

| # | Invariant | What the code does today | Where |
|---|-----------|--------------------------|-------|
| 1 | I1 (≤45 days) | When a live rail API fails, a saved timetable of **any age** is served (`timetable_any_age` → `saved_or_raise`), labelled with its date but not capped | `rail_common.py:303-333` |
| 2 | I6 (Google storage) | Google place content (name, rating, hours, coordinates) is upserted into `places` with **no expiry**; `api_cache` keeps place details 3 days and reverse geocodes 7 days | `planning_support.upsert_places`, `google.py` |
| 3 | I7 (rail terms) | RailRadar and IRCTC1 (RapidAPI) responses are stored 14 days (`rail_timetables`) and 90 days (`rail_station_lookups`). **I have not verified either provider's terms allow this.** | `rail_common.py:24-25` |
| 4 | I4 (refund) | Reserve/refund exists, but a plan running in a background thread that dies with the process (deploy, crash) leaves quota consumed with no plan | `quota_service.py`, `trip_service.py:508-524` |
| 5 | I5 (spend bounds) | No per-provider, per-user or service-wide daily spend cap; rate limiting is in-process only | `core/middleware.py` |

Conflict 1 is a live honesty risk and is cheap to fix. I recommend fixing it at the start of Phase 1 rather than waiting
for Phase 2. Conflicts 2 and 3 need a decision from you (see "Questions").

## Options

**A. OpenTelemetry now** (SDK, collector, exporter).
Gives standard traces, but adds dependencies and infrastructure before we know what we need. It is Phase 7's job.

**B. A small in-process trace recorder + a baseline script** (no new dependencies).
- **Recording:** a `PlanTrace` held in a `contextvar` records every external call, cache hit or miss, station-pair
  result, stage time and DB query count for one plan.
- **Shape:** spans are recorded as plain dicts matching the OpenTelemetry span model (name, start, end, attributes),
  so Phase 7 can emit the same data through OpenTelemetry without changing call sites.
- **Off switch:** behind `TELEMETRY_ENABLED` (default on in development and test, off in production until Phase 7).

**C. Log scraping.** Parse existing logs. Fragile, misses timings and cache hits, can't attribute calls to a plan.

## Decision

Option **B**.

### What gets recorded per plan

- **External calls:** provider, endpoint (path template, not full URL), status, latency, bytes, cache hit/miss,
  estimated cost (from a static price table in config, overridable by env), and retries.
- **Rail station pairs:** each pair tried, whether it returned trains, returned empty, or failed (quota, timeout,
  error). This is the "wasted calls" number.
- **Stages:** wall time per stage, and time to the first SSE event.
- **Database:** query count and total time per stage, via a SQLAlchemy `before/after_cursor_execute` listener
  attached only while a trace is active.
- **LLM:** tokens and cost, joined from the existing `LLMUsage` rows.

### Baseline run

`scripts/baseline.py` plans a fixed set of route types and writes `docs/perf/baseline-<date>.md` and a JSON copy.
The route types:

1. Local (same city)
2. Short rail (Jaipur → Delhi)
3. No-direct rail (connection needed)
4. Hill station via railhead (Mussoorie)
5. Long domestic flight (Mumbai → Dehradun)
6. International (Delhi → Istanbul)
7. A replan of route 2

It runs in two modes:

- **Offline mode** (simulated providers, already used by the test suite): call counts, stage structure and
  DB queries. Free; runs in CI.
- **Live mode** (real APIs, only when you approve): real latency, cache behaviour and cost. Run once against
  an empty cache, then again warm.

### Phase 0 also produces

- `docs/SLO.md`: proposed SLOs with the measured baseline next to each, plus an error-budget policy.
- `docs/COST.md`: cost per plan by route type; the throughput ceiling each provider's quota implies (plans per
  day before RailRadar, IRCTC1, Google or OpenRouter runs out); the cost of a cache miss vs a hit.
- **Risk ranking:** top risks ranked by likelihood × impact, and a revised order for Phases 1–9.

### SLO proposal (to be confirmed or corrected by the measurements)

| SLO | Target | Window |
|-----|--------|--------|
| Trip view availability (`GET /trips/{id}`, share page) | 99.5% non-5xx | 30 days |
| Time to first progress event (SSE) | p95 < 2 s | 30 days |
| Plan completion | p95 < 45 s, p99 < 90 s | 30 days |
| Plans that end in a 5xx | 0 (every provider failure degrades, I3) | 30 days |

**Error-budget policy:** when a 30-day budget is half spent, feature work on the affected path pauses for
reliability work. When it is fully spent, only fixes ship until it recovers.

## Files to touch

| File | Change |
|------|--------|
| `app/core/telemetry.py` (new) | `PlanTrace`, `span()`, `record_call()`, DB-query listener, flag check; no-ops when off |
| `app/core/config.py` | `TELEMETRY_ENABLED`, static price table (overridable per env var) |
| `app/providers/http.py` | record every call (provider, endpoint template, status, latency, retries) |
| `app/providers/cache.py` | record cache hit or miss per provider |
| `app/providers/rail_common.py` | record each station pair's outcome (trains / empty / quota / timeout / error) |
| `app/providers/railradar.py`, `indian_rail.py` | record rail calls if they bypass `http.request` |
| `app/services/planner.py` (`StageReporter`) | stage spans |
| `app/api/routes/trips.py` | start and finish a trace around `stream_plan`; time to first event |
| `scripts/baseline.py` (new) | runs the route set, writes the report |
| `tests/test_telemetry.py` (new) | trace captures calls, pair outcomes and stages; flag off records nothing; no behaviour change |
| `docs/perf/`, `docs/SLO.md`, `docs/COST.md` (new) | outputs |

No changes to algorithms, the SSE contract or the UI (I10). No new dependencies.

## Consequences

- **Overhead:** a few microseconds per call while tracing is on, and nothing when it's off.
- **Privacy:** traces hold provider names, endpoint templates and timings, but never query text, coordinates, user
  ids or emails. Baseline reports use the fixed test routes only.
- **Cost:** offline mode costs nothing. Live mode makes real paid calls (see "Questions") and uses up RailRadar and
  IRCTC1 monthly quota.
- **What we learn:** whether Phase 2's Tier 1 is worth building, from the count of calls spent on station pairs that
  return nothing; and whether Phase 5's job queue is needed now or can wait, from p95 completion time and
  concurrency.

## Questions for you

1. **Live baseline: approve or skip?** About 14 plans: 7 routes, cold and warm cache.
   - **Rough cost** (Phase 0 will measure it exactly): roughly 30–80 Google calls per cold plan, which should stay
     within Google's monthly free usage. Also roughly 5–25 RailRadar or IRCTC1 calls per cold plan from your monthly
     quota, and a few cents of OpenRouter.
   - **If you skip:** I run offline mode only, which gives call counts but not latency or real cost.
2. **I7 (rail terms).** I can't confirm RailRadar's or IRCTC1's terms allow storing timetables for 14 days, or 45
   days as Phase 2 proposes. Please check both providers' terms, or tell me to read them (that means fetching their
   public terms pages). Until then I'll treat Phase 2 storage as blocked by I7.
3. **I6 (Google storage).** The `places` table keeps Google place content with no expiry. Phase 1 can change it to
   keep only place IDs indefinitely and drop or refresh everything else after 30 days. Approve as part of Phase 1?
4. **I1 fix early.** Approve capping `timetable_any_age` at 45 days at the start of Phase 1 (one small change plus
   tests), since it is serving uncapped old timetables today?

---

## Results (2026-10-06)

### What was built

| Piece | Where |
|---|---|
| Plan trace (behind `TELEMETRY_ENABLED`; on by default outside production) | `app/core/telemetry.py` |
| Tracing HTTP transport | `providers/http.py` |
| Cache hit/miss and saved-timetable hits | `providers/cache.py`, `rail_common.RailCache.timetable` |
| Station-pair outcomes | `rail_common.search_station_pairs`, `connecting_trains` |
| Stage spans | `StageReporter` |
| DB query listener | `db/session.py` |
| LLM tokens and cost | `llm_service.py` |
| `ContextThreadPool` (carries the trace into the planner's 4 thread pools) | `core/telemetry.py` |
| Baseline and profiling scripts | `scripts/baseline.py`, `scripts/profile_plan.py` |
| Tests | `tests/test_telemetry.py` (6 tests) |

No algorithm, response or UI changes. Price-table defaults are list-price estimates; override them with `TELEMETRY_PRICES`.

### Approved I1 hotfix (shipped with this phase)

`rail_common.MAX_SAVED_TIMETABLE_AGE = 45 days`: `timetable_any_age` no longer serves older copies. Tested in
`tests/test_saved_timetable_age.py`. The seasonal 30-day cap arrives with Phase 2.

### Measurements

Reports: `docs/perf/baseline-{offline,live}-2026-10-06.md`. Summary in `docs/SLO.md` and `docs/COST.md`.

| | Measured |
|---|---|
| Wall time | median **80.6 s**, max **183 s** on SQLite |
| Where the time went (profiled) | **194 of 215 s waiting on SQLite locks**: a cross-session write lock held from `upsert_places` to the end of the plan, waiting out `busy_timeout=30000` |
| First progress event | 0–4 s, but **26.6 s** when place or station resolution is slow (it happens before the first event) |
| Cost per cold plan | about $0.22–0.35 Google, under $0.001 LLM, ~4 RailRadar calls |
| Rail station pairs | 24 tried, 4 empty (**17%**): what Tier 1 would save |
| Provider failures | 6 errors (2 autocomplete, 2 IRCTC1 station search, 2 others), **0 5xx to the traveller** |

### I7 findings (provider terms, read 2026-10-06)

- **RailRadar.** The public terms (railradar.in/terms) do **not** address API customers caching, storing or
  redistributing responses. They only describe RailRadar's own on-device caching and disclaim schedule accuracy.
  Search snippets mention a free tier of 1,000 requests/month.
  - **Result:** storage terms unknown. We need written permission, or the developer agreement from their dashboard,
    before Phase 2 stores anything for 45 days.
- **IRCTC1 (RapidAPI).** It describes itself as **not affiliated with IRCTC or Indian Railways and unofficial**. I could
  not retrieve RapidAPI's terms page.
  - **Result:** a commercial-use and provenance risk independent of caching. Both its station-search calls failed in
    the baseline.
- **Recommendation:** treat Phase 2 storage as **blocked** until RailRadar confirms in writing. Evaluate an
  official dataset (Indian Railways timetable on data.gov.in) as the `official_dataset` source in Phase 1.
  Consider dropping IRCTC1 from production.

### Top risks (likelihood × impact)

| # | Risk | Evidence | Mitigation (phase) |
|---|------|----------|--------------------|
| 1 | Plans take 1–3 min on SQLite (every dev and demo machine) and cache writes silently fail | profile: 90% lock wait | Commit after `upsert_places`; keep transactions short (**Phase 1, first item**) |
| 2 | RailRadar quota (~8 cold rail plans/day on the free tier) runs out | 4 calls/plan, 1,000/month | Budgets and fallback (Phase 4); Tier 1/2 once I7 is cleared (Phase 2) |
| 3 | Rail data rights unclear (I7); IRCTC1 unofficial | terms review above | Provider confirmation; official dataset (Phase 1/2) |
| 4 | Google content stored without expiry (I6) | `places` table | Keep IDs only, expire the rest within 30 days (Phase 1) |
| 5 | First progress event slow when resolution is slow | 26.6 s observed | Emit the first stage before any provider call (Phase 1) |
| 6 | No spend bounds (I5) | nothing caps Google spend | Daily budgets per provider and user (Phase 4/6) |
| 7 | Quota stuck consumed if the process dies mid-plan (I4) | background thread | Reservation saga (Phase 5) |

### Revised phase plan

- **Phase 1** gains three items:
  - the SQLite transaction fix (risk 1);
  - early first event (risk 5);
  - Google storage expiry (risk 4, approved).
- **Phase 2** waits on RailRadar's written confirmation (I7). **Phase 4** (gateway, budgets) moves ahead of it. Of
  Phase 4, only budgets, circuit breakers and quota tracking are justified by the measurements. AIMD and health-scored
  routing stay as no-op interfaces behind flags, per the complexity guard.
- **Phase 5:** a job queue is not yet justified by load (one process handled every plan). Once risk 1 is fixed, its
  main justification is I4 (quota saga) and surviving deploys. Re-measure first.

**Update 2026-10-06:** RailRadar confirmed to the user that storing timetables is allowed. Phase 2 is unblocked for RailRadar results; IRCTC1 is still unconfirmed. Risk 1 (SQLite lock) was fixed in Phase 1: 215.6 s → 16.8 s (ADR 0002).
