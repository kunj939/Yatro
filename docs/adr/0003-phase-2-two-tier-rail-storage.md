# ADR 0003 — Phase 2: two-tier rail storage

Status: **Accepted** (approved 2026-10-06 with the proposed defaults; implemented). Date: 2026-10-06.

## Context

- **Calls per plan.** Phase 0 measured about 4 RailRadar calls per new rail plan. Of 24 station pairs tried, 4 (17%)
  returned no trains. RailRadar's free tier (1,000 calls/month) allows about 8 new rail plans a day. Every repeat of a
  popular route pays again after 14 days.
- **What is stored today** (`app/models/rail.py`, `rail_common.RailCache`):
  - `rail_timetables`: raw rows per (from, to, weekday, source), **saved without validation**, read back for 14 days
    (45 days only when the API is down, since ADR 0001).
  - `rail_station_lookups`: raw station-search rows for 90 days. Only IRCTC1 has a station search.
  - `api_cache`: raw RailRadar and IRCTC1 responses for 12 hours.
  - Nothing remembers which station pair works for a city pair, or which returned nothing.
- **I7.** RailRadar has confirmed storage is allowed (user, 2026-10-06). IRCTC1 has not, and calls itself unofficial,
  yet its timetables, station lookups and responses are being stored today.
- **I6.** Google results are never written to rail storage today: only the rail providers call `RailCache`. Nothing
  enforces that, though.

## Options

**A. Make the existing table smarter (TTL 45 days, add validation).** Mixes "what to look up" with "what to show", and
has nowhere to put negative results.

**B. Two tiers with separate jobs** (chosen, as specified):
- **Tier 1** is internal route knowledge that decides what to look up and is never shown to a traveller.
- **Tier 2** is validated timetable facts that are shown to travellers, with freshness and a 45-day maximum.

**C. A separate timetable service or store** (Redis, a search index). Not justified: the measured volume is a few
hundred rows. That belongs to Phase 5 if ever.

## Decision

### Tier 1 — route knowledge (internal only)

Two new tables:

| Table | Key | Holds | TTL |
|---|---|---|---|
| `rail_city_routes` | (source, origin_key, dest_key). City keys are normalised place names, e.g. "jaipur", "new delhi" | the station pairs that last returned trains, best first | 365 days |
| `rail_pair_results` | (source, from_code, to_code, **weekday**) | outcome `trains` or `empty`; train numbers seen; checked_at | trains: 365 days; empty: 60 days |

- **Ordering.** `search_station_pairs()` tries known-working pairs first and skips pairs with an unexpired `empty`
  result for that weekday.
- **Negative results** are recorded only from a successful response with zero rows. Never from quota errors, timeouts,
  failures, or rows we couldn't read.
- **Contradictions.** A newer result that contradicts Tier 1 overwrites it at once.
- **Never shown.** Tier 1 never supplies a train name, number or time to anything that reaches a traveller. The
  train-number list is used only to prioritise refreshes.
- **Deviation: weekday-scoped negatives.** The brief says "no trains between X and Y". RailRadar answers per date, and
  many trains don't run daily. A pair-wide negative from one empty Tuesday would wrongly skip the pair on Saturday, so
  negatives are recorded per weekday.

### Tier 2 — timetable facts (shown; maximum 45 days)

**Table.** The existing `rail_timetables` is extended:

| Column | Holds |
|---|---|
| `expires_at` | when the row may no longer be shown |
| `last_verified_at` | last time the source confirmed it |
| `validation` | rows kept / dropped, and why |
| `needs_refresh` | refresh flag |
| `refresh_priority` | 0 = none, 1 = aging, 2 = stale, 3 = reported |
| `reported_at` | last traveller report |

**Validation before saving.** `validate_rows()` keeps a row only if:
- it has a non-empty name;
- its number is 4–5 digits;
- departure and arrival times parse;
- its station codes match the request;
- its duration is positive and its average speed is 10–180 km/h for the distance;
- no duplicate train number in the same result has different times.

Failing rows are dropped and logged. **If more than half fail, nothing is saved for that route.** A genuine empty
result goes to Tier 1, not Tier 2.

**Freshness, shown to the traveller.** Freshness is computed at read time by `provenance.freshness`:

| Age | State | What the traveller sees |
|---|---|---|
| 0–7 days | fresh | "Saved timetable · checked N days ago" |
| 8–21 days | aging | the same label; queue a background refresh |
| 22–45 days | stale | the label plus "Timings may have changed — confirm on IRCTC before booking"; refresh at higher priority |
| over 45 days | expired | never used; fetch live |

**Seasonal cap.** From 1 December to 28 February the maximum is 30 days (`RAIL_SEASONAL_CAP`, default on). This applies
everywhere the 45 days applies, including the "API down" fallback and `TrainService`.

**Stale-while-revalidate.** Saved data is served immediately when not expired. This changes the normal read window
from 14 days to the freshness rules above.

**Refresh.** A daemon worker refreshes queued routes, stale before aging and reported first, within a **daily call
cap** (`RAIL_REFRESH_DAILY_CAP`). The proposed default is **10 calls/day**, about a third of the free tier; 0 turns it off.
Proper budgets come in Phase 4.

**Disagreement.** When a newer live result differs from the saved one (trains added or removed, times changed), it
replaces it and the difference is logged.

**Keys and sources.** Rows stay keyed by (from_code, to_code, weekday, source), with per-source separation kept.

**Reports.** `POST /api/trips/{id}/report-train` with an item id and `didnt_run` or `wrong_time`:
- marks that route `needs_refresh` with top priority and stamps `reported_at`;
- stores no personal data beyond the trip owner's id;
- the UI is a small "Report a problem with this train" link on the train ticket.

### Which sources may write

- **Allow-list.** Both tiers accept writes only from an allow-list: `RAIL_STORAGE_SOURCES`, default `railradar`.
  `RailCache` refuses any other source, with a test.
- **Google.** Google transit results never reach either tier (I6). They aren't written by the rail providers, and the
  allow-list rejects them anyway.
- **IRCTC1.** IRCTC1 stops writing to Tier 1, Tier 2, `rail_station_lookups` and `api_cache` until its terms are confirmed
  (I7). The migration **deletes the IRCTC1 rows already stored** (needs your approval; see Questions).

### Migration 0005

Works on SQLite and PostgreSQL, upgrade and downgrade:
- adds the Tier 2 columns;
- creates `rail_city_routes`, `rail_pair_results`, `rail_train_reports` and `rail_daily_stats`;
- backfills `expires_at = fetched_at + 45 days`;
- marks existing rows `validation = {"status": "legacy"}`; legacy rows are validated on read and ignored if they fail;
- deletes stored IRCTC1 rows (if approved).

### Metrics

Recorded per plan in the Phase 0 trace, and summed per day in `rail_daily_stats`:
- **Tier 2:** hits and misses, and the freshness mix (fresh / aging / stale).
- **Tier 1 savings:** empty pairs skipped, plans that went straight to the working pair, and calls avoided.
- **Refreshes:** calls made.

`GET /api/system/rail-stats` returns the last 30 days.

### Files

| File | Change |
|---|---|
| `app/models/rail.py` | Tier 2 columns; new Tier 1, report and stats tables |
| `alembic/versions/0005_two_tier_rail_storage.py` | the migration above |
| `app/providers/rail_storage.py` (new) | `validate_rows`, Tier 1 read/write, Tier 2 save/read with freshness, allow-list, seasonal cap, disagreement diff |
| `app/providers/rail_common.py` | `RailCache` delegates to `rail_storage`; `search_station_pairs` uses Tier 1 ordering and skipping; record outcomes |
| `app/providers/railradar.py`, `indian_rail.py` | save through validation; IRCTC1 storage off |
| `app/core/provenance.py` | max age respects the seasonal cap |
| `app/services/rail_refresh.py` (new) | refresh worker with the daily cap |
| `app/api/routes/trips.py`, `system.py` | report endpoint; stats endpoint |
| `app/services/planner.py` | freshness label and the stale warning on train items |
| frontend `TimelineItem.tsx`, `lib/api.ts` | saved-timetable label and stale note on the ticket; "Report a problem" link |
| tests | below |

### Tests (from Phase 8's rail list, done now)

- each validation rule, and "most rows fail, so save nothing";
- freshness transitions at 7, 21 and 45 days, and the seasonal 30-day cap;
- expired rows never used;
- replacement on disagreement, with the diff logged;
- the Tier 1 working-pair shortcut and the empty-pair skip;
- negatives never created from quota errors, timeouts or unreadable rows;
- Google and IRCTC1 never written to either tier;
- a report marks the route for refresh;
- the refresh worker respects the daily cap and the priority order;
- the migration upgrades and downgrades on SQLite.

## Consequences

- **Cost.** A repeat route costs no rail calls for up to 45 days, down from 14. About 17% of pair lookups are skipped by
  Tier 1. With the free tier, rail capacity rises from about 8 new rail plans a day to roughly 8 *new routes* a day,
  and repeats are free.
- **Honesty.** Saved timetables are validated before anyone sees them, labelled with their age, and stale ones carry the
  "confirm on IRCTC" warning (I2). Nothing over 45 days (30 in winter) is shown (I1).
- **Privacy.** Reports store the trip id and the train item only. No new personal data.
- **Risk.** Weekday-scoped negatives mean slightly fewer skips than pair-wide ones, in exchange for never hiding a train
  that runs on other days.

## Answers (2026-10-06)

1. **IRCTC1 data:** deleted by migration 0005, and IRCTC1 stores nothing new (its live lookups still work).
2. **Refresh cap:** 10 RailRadar calls/day (`RAIL_REFRESH_DAILY_CAP=10`; 0 turns it off).
3. **Negatives:** per weekday.
4. **Still open from Phase 1:** estimated train fares under I1, and Google content in saved trips under I6.

## Results

**Tests.** The backend suite has 444 tests, all passing; 43 of them are new in `tests/test_rail_storage.py`. They cover
the whole list above plus:
- legacy rows validated on read;
- flat, IRCTC1-shaped rows;
- a refresh that fails, which still counts against the cap and leaves the route queued.

The frontend type check is clean.

**Migration 0005 on the dev database.** Applied after a backup (`%TEMP%\tripai-before-0005.db`):
- IRCTC1 rows are gone from `rail_timetables`, `rail_station_lookups` and `api_cache`;
- the 33 RailRadar timetables are kept, marked `legacy`, with `expires_at` backfilled;
- the four new tables exist.

The test suite also runs upgrade → downgrade → upgrade on a fresh SQLite file.

**Validation on real data.** Run over every saved RailRadar timetable: of 107 rows, 106 were kept and 1 dropped (a bad
train number on Jaipur → Delhi Sarai Rohilla). The rules don't reject genuine RailRadar data.

**Bugs found while testing, and fixed:**
- **Ended copies still served.** A refresh that found no trains set `expires_at`, but reads looked only at the copy's
  age, so the old copy was still shown. Reads now honour `expires_at` too.
- **Stringified field.** A nested `from`/`to` object without a station code was turned into a string and compared as
  if it were a code. Only flat fields are used as fallbacks now.
- **Test phone clash.** Generated test phone numbers (`+9198…`) could collide with numbers hardcoded in the signup
  tests once enough users were registered. They now come from an unused range (`+9170…`).

**Where the freshness label lives.** It is added in the response gate (`train_fact_gate.label_item`), not in
`planner.py`. It is therefore worked out when a trip is viewed, so a trip opened weeks later shows the right age and
warning.

**Not measured yet.** Tier 1 savings and the Tier 2 hit rate on live traffic will appear in
`GET /api/system/rail-stats` as plans run. Re-running the Phase 0 live baseline would confirm the call reduction; it
costs real RailRadar and Google calls, so it waits for your go-ahead.
