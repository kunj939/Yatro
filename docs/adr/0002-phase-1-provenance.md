# ADR 0002 — Phase 1: provenance as a type, the source chain, and the Phase 0 fixes

Status: **Accepted** (user approval 2026-10-06: "we can go ahead"). Date: 2026-10-06.

## Context

ADR 0001 measured the planner and found three things to fix before anything else:

1. **Lock waits.** On SQLite, ~90% of plan time is spent waiting for a write lock. The planner holds a write
   transaction from `upsert_places` to the end of the plan, so every other session's write (response cache, saved
   timetables) waits `busy_timeout` (30 s) and then fails silently.
2. **Late first event.** The first progress event comes after `_sync_locale`, which can make slow provider calls
   (26.6 s observed).
3. **Google storage (I6).** Google place content is stored in `places` with no expiry.

The brief's Phase 1 adds:

- **Fact types.** `Fact[T]` and `SourceKind`.
- **TrainService gate.** A `TrainService` that can only be built from real timetable sources.
- **Serializer gate.** A response serializer that redacts train facts from non-timetable options, independently of the
  factory.
- **Source chain.** saved_timetable → live_api → official_dataset → station_guidance → ai_guidance.
- **AI output guard.**

**I7 update.** RailRadar confirmed to the user (2026-10-06) that storing timetables is fine. IRCTC1 is still
undecided: it describes itself as unofficial. That leaves Phase 2 storage cleared for RailRadar results only.

## What the code does today (relevant to I1)

- **Real trains.** RailRadar/IRCTC1 and Google transit produce trains with names, numbers and real times.
  RailRadar/IRCTC1 rows can come from the live API or from the saved timetable (≤14 days on the normal path, ≤45 days
  on the "API down" path since ADR 0001).
- **Estimated trains.** `estimated_rail_options` (the station-network estimate) and Google's no-timetable "train" window
  produce trains **without names but with clock times**: departure placed at the start of the traveller's window. They
  are labelled "timing estimated", but under I1 those times may not reach the traveller.
- **Fares.** Every train fare is computed by `fare_model` (class × distance), even for real trains.

## Options

**A. Convert every option to `Fact`-typed fields now.** Large blast radius: `TransportData` is used across the engine,
and the algorithms and tests depend on it. Risks I10.

**B. Keep `TransportData` for the engine; attach provenance and gate at two boundaries** (chosen).
- Providers stamp each option with `meta.source_kind` and `meta.observed_at`.
- Anything that presents a train as fact goes through `TrainService.from_option()`, which raises unless the source
  is real and fresh.
- The response serializer (`item_out` / `alt_out`) independently redacts names, numbers and clock times from any
  train item whose source is not a timetable source, whatever the planner wrote.

## Decision

### 1. Phase 0 fixes

- **Lock.** Commit right after `upsert_places`, the only mid-plan write. The planner then holds no write transaction
  while it waits on providers.
- **First event.** Start the `understand` stage before `_sync_locale`.
- **I6.**
  - A retention job (`app/services/retention.py`) scrubs Google content in `places` older than 30 days. It keeps the
    row and `provider_place_id` (I6 allows IDs indefinitely) and clears name, coordinates, address, rating, hours,
    website and description. Rows are never deleted, because saved itineraries reference them.
  - It also deletes expired `api_cache` rows. Google response TTLs there are already ≤ 7 days.
  - It runs at startup and every 24 h (`RETENTION_ENABLED`, default on).

### 2. Provenance types (`app/core/provenance.py`)

- **`SourceKind`:** `live_api | saved_timetable | official_dataset | station_guidance | ai_guidance`, plus `sample`
  (demo mode only). `sample` is a deliberate addition: demo mode ships fixed sample data behind a visible "Demo mode:
  sample data" badge, and it's what the tests and the public demo run on. Sample trains have no names or numbers.
- **`Fact[T]`:** `value, source, observed_at, freshness`, where freshness is `fresh` (≤7 d), `aging` (≤21 d),
  `stale` (≤45 d), `expired`, or `n/a` for non-timetable sources.
- **`TrainService(name, number, depart_at, arrive_at)`:**
  - Each field is a `Fact`.
  - `__post_init__` raises `ProvenanceError` unless every source is in {live_api, saved_timetable, official_dataset}
    (or `sample` in demo mode) and none is expired.
  - `TrainService.from_option(t)` is the only constructor the serializer uses.

### 3. Sources stamped where options are made

| Where | Source |
|---|---|
| RailRadar / IRCTC1 `_options` | `live_api` (now) or `saved_timetable` (the saved row's `fetched_at`) |
| Connections (two legs) | the weaker source of the two legs, and the older `observed_at` |
| Google transit (`_scheduled`) | `live_api` |
| Google's no-timetable train window | `station_guidance` |
| `estimated_rail_options` | `station_guidance` |
| Demo provider | `sample` |

### 4. Final gate in the serializer

For transport items whose mode is train and whose source is not a train-fact source (or is missing):

- **Clock times:** `time`, `end_time`, `start_at` and `end_at` are replaced by a part of day (`meta.when`: "Early
  morning", "Morning", "Afternoon", "Evening", "Night").
- **Train identity:** `meta.number` is removed and `title` is rebuilt as "Train to <station>" without a name.
- **Free text:** any HH:MM in `subtitle` or `reasons` is replaced by the part of day.

The same gate applies to transport alternatives. Text the engine writes about such trains (summary lead, combo reason,
station-transfer subtitle, infeasibility messages) uses part-of-day phrasing at the source too, via `FE.when(t)`.

### 5. Source chain

- **saved_timetable → live_api** (already the order inside each rail provider, per route, then provider order).
- **official_dataset:** an `OfficialTimetable` interface with a no-op default. No dataset is imported.
- **station_guidance:** the existing `estimated_rail_options`.
- **ai_guidance:** an interface behind `AI_RAIL_GUIDANCE` (default **off**), per the complexity guard. Phase 0 found no
  case where station guidance yielded nothing for an Indian route. Every station it returns must be in the gazetteer
  (`rail_stations.STATIONS`); others are dropped.
- **Recording:** which source answered goes on each option and into the trace.

### 6. AI output guard

`llm_service.violations_in` also rejects, unless present in the verified payload:

- 4–5 digit numbers (train numbers);
- train-type words used as names (Express, Shatabdi, Rajdhani, Vande Bharat, Duronto, Garib Rath, Mail, Jan Shatabdi,
  Humsafar, Tejas, Gatimaan).

The existing guard already rejects unverified clock times and money amounts.

### Interpretation of I1 "fare"

Train fares are model estimates even for real trains, and are shown with cost state **Estimated** (I2). I1 is applied to
name, number and clock times. A fare is never presented as a timetable fact. **Open question for the user:** if I1 must
also forbid estimated train fares, budgets need a fare source first.

### Not in this phase

**Open question for the user:** itinerary items in saved trips keep place names and coordinates. Whether I6 applies to a
user's saved trip (rather than to our catalog) is a policy question for you. It isn't changed here.

## Consequences

- **Plan time.** On SQLite this should drop from minutes to roughly the provider time (re-measured below).
- **Itinerary display.** When no timetable exists, a train in the itinerary shows "Morning · ≈ 4 h 40 min" instead of
  a clock time. That's a deliberate, required UI change for those items only; the frontend shows `meta.when` when
  `time` is null.
- **Privacy.** No new data stored. I6 retention reduces what is stored.
- **Cost.** None directly. Working cache writes reduce repeat Google calls.

## Results (2026-10-06)

### Built

| Area | Where |
|---|---|
| Types: `SourceKind`, `Fact`, `TrainService`, freshness by whole days (7/21/45) | `app/core/provenance.py` |
| Final serializer gate | `app/services/train_fact_gate.py`, applied in `trip_service.item_out` / `alt_out` |
| Source stamping | RailRadar/IRCTC1 (rows tagged live or saved), connections (weaker leg), Google transit (live), Google train window and `estimated_rail_options` (station guidance), demo (sample) |
| Source chain links 3 and 5 | `app/providers/rail_sources.py`, wired in `rail_chain.RailAwareTransport` |
| AI output guards | `rail_text_ok`, `validated_stations`; narration guard extended with train numbers and train-type names (`llm_service.TRAIN_WORDS`) |
| I6 retention | `app/services/retention.py`: startup plus every 24 h, `RETENTION_ENABLED` |
| Engine text | uses `FE.at(t)` ("at 07:40" or "in the morning") for summary, reasons, transfers, refusals and alternatives |
| Frontend | shows `meta.when` and a short "no timetable" note when `time` is null |

### Phase 0 fixes

- **Write locks.** A second mid-plan write was found and fixed alongside `upsert_places`: `_store_sources` (verification
  sources) also opened a write held to the end of the plan. Both now commit immediately.
- **Result.** The profiled live Jaipur → New Delhi plan went from **215.6 s to 16.8 s** (same database copy, same route).
  SQLite no longer appears in the profile; what remains is network time, serialised for profiling.
- **First progress event.** Now emitted before `_sync_locale` (tested with a 600 ms resolver: first event < 300 ms).

### Tests

`tests/test_provenance.py`, 31 cases:
- freshness boundaries;
- TrainService accepts the three timetable sources and rejects guidance, AI, unknown and expired;
- the gate strips name, number and every HH:MM even from a deliberately leaky item, and passes timetabled trains and
  other modes untouched;
- alternatives are gated;
- the narration guard catches an invented "12049 Gatimaan Express";
- the AI rail-text guard and gazetteer validation;
- RailRadar options stamped live vs saved (10-day-old copy reads "aging");
- I6 scrub keeps the ID and clears the content after 30 days, and expired cache rows are deleted;
- end to end, a station-guidance plan shows no train times, and timetabled plans keep theirs;
- first event before a slow resolver;
- no write lock during a plan (each route lookup writes the cache from another session).

Existing tests updated, each for a stated reason:
- `test_named_trains` fixtures marked as RailRadar rows (`source_kind: live_api`);
- the scenario checker skips order checks for trains that now correctly carry no clock time.

### Flags

| Flag | Default | Effect |
|---|---|---|
| `AI_RAIL_GUIDANCE` | false | Interface only; no implementation wired (complexity guard) |
| `RETENTION_ENABLED` | true | I6 retention job |
| `TELEMETRY_ENABLED` | auto | from ADR 0001 |

### I7

RailRadar confirmed to the user (2026-10-06) that storing timetables is allowed, so Phase 2 storage may proceed for
RailRadar results. IRCTC1 remains unconfirmed and unofficial; exclude it from Tier 2 until confirmed.

### Still open for the user

1. **I1 and estimated fares.** Should I1 also cover estimated train fares? Today they are shown, labelled Estimated.
2. **I6 and saved trips.** Should I6 apply to place names and coordinates inside a user's saved itineraries?