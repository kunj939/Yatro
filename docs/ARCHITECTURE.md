# TripAI architecture

## Principle

The LLM is never the source of truth for numbers. Prices, times, distances, opening hours and fares come from providers
(or labelled estimates) and pass through deterministic code. The LLM has only three jobs:

- understand intent (it picks from candidate IDs the engine offers);
- write explanations;
- turn plain-language changes into structured patches.

Every sentence it writes passes a **fact guard** before it is shown. The guard rejects any rupee amount, clock time or
distance that isn't in the plan, and falls back to templates if the guard fails.

## Planning pipeline (`backend/app/services/planner.py`)

| # | Stage | What happens | Code |
|---|---|---|---|
| 1 | Understand | Normalise input, derive time window, rooms, food and style weights, interests from notes. | `planning_support.plan_input`, `llm_service.interests_from_notes` |
| 2 | Routes | Build every intercity combination (outbound × return × mode) that fits the time window, with boarding and exit buffers; rank by cost, comfort and time at the destination. | `feasibility.build_combos`, `rank_combos` |
| 3 | Places | Sights, restaurants and hotels from the places provider; food filter; stored in `places`. | `providers/*`, `planning_support.upsert_places` |
| 4 | Hours | Opening hours from structured data, cross-checked on the web by trust tier (official > structured > reputable > generic). Conflicts become **Needs check**, and the plan uses the narrower window. Closure notices lower the score. | `verification.py` |
| 5 | Prices | Fares, tickets, meals and hotel nightly rates, each tagged with a verification state. | `fare_model.py`, providers |
| 6 | Optimise | Multi-start greedy build plus repair. It respects open hours, meal windows, sunset, the day end, and money reserved for pending meals; it never schedules a closed place. | `route_optimizer.py` |
| 7 | Budget | Categories, safety buffer by style, per-person share, status (within, tight, over), member budget warnings. | `budget_engine.py` |
| 8 | Alternatives | For each major item, substitutes are re-simulated in the real day (hours, next stop, remaining budget); the top 3 are kept, with checks and cost and time deltas. | `alternatives.py` |
| 9 | Itinerary | Timeline items with travel legs, free time, stay and return, narration (LLM or templates), persisted as a new version. | `planner.py`, `llm_service.narrate` |

**Feasibility first.** Before optimising, the engine computes the cheapest possible version of the trip.

- If even that exceeds the budget, or no transport fits the time window, planning stops. The user sees
  "The requested trip is not feasible within ₹X", the main conflict (the requirement driving the overshoot) and concrete
  adjustments.
- Each adjustment carries an `apply` patch that re-runs the engine; nothing is guessed.

**Streaming.** Every stage reports progress through `StageReporter`. The API streams these as NDJSON from a worker thread
with its own database session, and the frontend reveals them at a readable pace.

## Providers (`backend/app/providers/`)

Each capability is an interface in `base.py`: maps/routes, places, hotels, transport, search, weather and LLM.
`registry.py` picks an implementation per capability:

| Capability | Real implementation | Demo implementation |
|---|---|---|
| Maps, places, geocoding | Google Places (New), Routes, Geocoding (`google.py`) | `demo.py` |
| Web verification | Brave, Serper or Tavily (`search.py`) | `demo.py` |
| Weather | Open-Meteo (`weather.py`) | `demo.py` |
| LLM | OpenRouter, or any OpenAI-compatible server such as vLLM/Qwen (`llm.py`) | Deterministic templates |

HTTP calls share `http.py`, which handles timeouts, retries and a small TTL cache (`cache.py`). A failing provider
degrades gracefully instead of breaking the plan:

- "Route information is temporarily unavailable." The plan falls back to estimated legs.
- "Some current information could not be verified." The affected items are marked accordingly.
- "Trip data was collected, but AI explanation is temporarily unavailable." The plan falls back to templates.

**Swapping to a self-hosted model** is configuration only: set `LLM_PROVIDER=openai_compatible` and
`OPENAI_COMPAT_BASE_URL`. The engine talks to the `LLMProvider` interface with strict JSON schemas, so the
model can change without engine changes.

## Verification states

Each item and line carries one of these states, and the UI shows it as a badge:

| State | Badge | Meaning |
|---|---|---|
| `verified_official` | Official | Confirmed by an official source. |
| `verified_provider` | Verified | From a structured data provider. |
| `estimated` | Estimated | A calculated estimate. |
| `uncertain` | Needs check | Sources disagree. |
| `unavailable` | Not available | No reliable information found. |
| `demo` | Sample data | Illustrative demo data. |

## Data model (17 tables, `backend/app/models/`, migration `alembic/versions/0001_initial_schema.py`)

- **Accounts:** `users`, `subscriptions`
- **Trips:** `trips`, `trip_preferences`, `trip_members`
- **Plans:** `itinerary` (versioned), `itinerary_items`, `alternatives`, `transport_options`
- **Catalogue and verification:** `places`, `restaurants`, `hotels`, `web_sources`, `api_cache`
- **Money and logs:** `expenses`, `expense_splits`, `llm_usage`



## Quota and payments (`services/quota_service.py`)

- Free accounts get **2 lifetime trip plans**. PLUS (₹99/month) is unlimited, and ONE TRIP (₹49) adds a single credit.
- A plan is reserved atomically when a trip is first planned, in this order: PLUS, then free plans, then credits.
  It is refunded if planning fails or the trip turns out infeasible.
- Re-plans, swaps and adjustments of an already-planned trip are free, capped by `MAX_REPLANS_PER_TRIP`.
- Payments are mocked (`PAYMENTS_MODE=mock`). Replace `/subscription/upgrade` with a verified payment webhook before launch.

## Expenses (`services/expense_service.py`)

Splits are computed in paise, so there are no rounding leaks: ₹1,450 ÷ 4 = ₹362.50 each. Supported splits are equal,
selected people, custom amounts and one person. Balances are paid minus share, and settlements use a greedy
minimal-transfer algorithm.

## Security

- Passwords are hashed with bcrypt; JWTs are signed with `JWT_SECRET`, and production refuses the dev secret.
- Every trip query is scoped to its owner.
- Inputs are validated by Pydantic with length and range limits.
- Request bodies are size-limited, with per-route rate limits.
- CORS uses an allow-list, security headers are set, and the Google server key never reaches the browser.
- The LLM output schema only allows candidate IDs the engine offered, and web content is treated as data, never as instructions.

## Frontend (`frontend/`)

Next.js App Router, TypeScript and Tailwind (design tokens in `tailwind.config.ts`).

| Area | Location |
|---|---|
| Landing page | `components/landing/*`, `PlanningAnimation`, `PricingSection` |
| 8-step wizard | `components/wizard/*` |
| Live planning progress | `components/trip/TripLoading`, `lib/usePlanStream` |
| Trip dashboard | `components/trip/*`: `TripDashboard`, `Timeline`, `TimelineItem`, `AlternativeDrawer`, `BudgetPanel`, `MapPanel`, `GroupMembers`, `ExpenseSplitter`, `InfeasibleView` |
| API client and auth | `lib/api.ts`, `lib/auth.tsx` |

The map uses Google Maps when a browser key is set; otherwise it draws a route sketch from the plan's own coordinates.
