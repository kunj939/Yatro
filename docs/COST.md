# Cost model

From the Phase 0 live baseline (`docs/perf/baseline-live-2026-10-06.md`).

- **Prices:** list-price estimates per call, set in `app/core/telemetry.py` (`DEFAULT_PRICES`) and overridable with
  `TELEMETRY_PRICES`. Verify them against the Google Cloud billing report before using them for decisions.
- **Free usage:** Google's free monthly usage per SKU is not subtracted here.

## Cost per plan, by route type (cold cache)

| Route type | Google + search $ | LLM $ | Rail quota units | External calls |
|---|---:|---:|---:|---:|
| Local (same city) | 0.248 | 0.0005 | 0 | 26 |
| Short rail (Jaipur → New Delhi) | 0.218 | 0.0005 | 4 | 26 |
| Hill station via railhead (New Delhi → Mussoorie) | 0.345 | 0.0007 | 7 | 49 |
| Domestic flight + rail (Mumbai → Goa) | 0.255 | 0.0008 | 4 | 26 |
| Refused early (Udaipur → Rishikesh, New Delhi → Istanbul) | 0.07–0.19 | 0 | 0–4 | 8–11 |
| Replan ("start at 9am") | 0.045 | 0.0003 | 0 | 11 |

**Typical:**

| Plan | Cost | Rail calls | LLM |
|---|---:|---:|---:|
| New trip, cold cache | about **$0.22–0.35** | about 4 | under $0.001 |
| Replan | about **$0.05** | — | — |

The warm pass cost $0.01–0.13 per plan. It should be lower once the SQLite lock problem (docs/SLO.md) stops cache
writes from failing.

## Where the money goes

| Endpoint | Share of calls | Share of est. $ | Notes |
|---|---:|---:|---|
| Google Routes `computeRoutes` | 63% (145/229) | 43% | One call per leg of the day (`apply_real_routes`), plus transit lookups |
| Google Places `searchText` | 10% | 46% | Most expensive per call (attractions, restaurants, hotels) |
| Google Places details | 4% | 9% | |
| Autocomplete | 5% | 2% | 2 of 11 failed |
| OpenRouter | 4% | ~0% | p50 1.8 s, max 11.4 s |
| RailRadar | 8% | quota | 0 errors |
| IRCTC1 `searchStation` | 1% | quota | **2 of 2 failed** |

## Throughput ceilings implied by quotas

These are rough; plan sizes vary.

| Provider | Quota | Use per cold plan | Ceiling |
|---|---|---|---|
| **RailRadar** | Free tier 1,000 requests/month (their public page; confirm your plan) | ~4 calls | **~250 rail plans/month (~8/day)** before the source chain falls back to IRCTC1 or estimates. This is the tightest limit by far. |
| **Google** | Pay-as-you-go | ~$0.25 | **~4,000 cold plans per $1,000/month**. Free monthly usage per SKU raises this; check the billing console. |
| **OpenRouter** | — | under $0.001 | Not a constraint. |

## Levers, in order of value

1. **Fix cache writes on SQLite** (Phase 1 hotfix). This makes warm plans actually warm.
2. **Two-tier rail storage** (Phase 2). Tier 2 removes the ~4 rail calls on repeat routes; Tier 1 skips the
   17% of station pairs that return nothing (4 of 24 in the baseline). Blocked by I7 until providers confirm storage
   is allowed.
3. **Route legs via one route matrix** instead of one `computeRoutes` call per leg. Cheaper per element, and fewer
   round trips. Optimiser data only; no change to what is shown.
4. **Per-provider daily budgets** (Phase 4, done: ADR 0007). `GOOGLE_DAILY_USD` $10, `DAILY_PLAN_CAP` 25. Per-user and
   service-wide caps are Phase 6.
