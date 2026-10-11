# TripAI API

Base URL: `http://localhost:8000/api`. Interactive docs: `/api/docs` (Swagger) and `/api/redoc`.

**Auth:** `Authorization: Bearer <access_token>` from register/login.
**Money:** rupee amounts as numbers (up to 2 decimals). **Times:** local to the trip (`Asia/Kolkata` by default).

**Errors** always look like this:

```json
{ "error": { "code": "quota_exhausted", "message": "Your 2 free trip plans are used.", "details": {} } }
```

Common codes and statuses:

| Status | Code | Meaning |
|---|---|---|
| 401 | `unauthorized` | Missing or bad token. |
| 402 | `quota_exhausted` | No free plan, credit or PLUS left. |
| 404 | `not_found`, `demo_not_ready` | Resource missing, or the sample trip isn't built yet. |
| 409 | `plan_in_progress`, `duplicate_member`, `member_has_expenses` | Conflicting state. |
| 422 | `validation_error`, `no_change` | `details` lists `{field, message}` for validation errors. |
| 429 | `rate_limited` | Too many requests. |

## System

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | `{"status":"ok"}` (kept for compatibility; checks the database) |
| GET | `/healthz` | Liveness: the process is up. Touches nothing else. |
| GET | `/readyz` | Readiness: `{status, checks:{database, redis, plan_runner}}`; HTTP 503 and the failing part when this instance should not get traffic. Redis counts only if `REDIS_URL` is set. |
| GET | `/system/plans` | **Admin only (404 for everyone else):** running and waiting plans by lane, limits, real median plan time, brownout level (ADR 0008). |
| GET | `/config` | Mode (`demo`/`live`/`mixed`), active providers, prices, free plan count. |

## Auth

| Method | Path | Body |
|---|---|---|
| POST | `/auth/register` | `{name, email, password}` (password ≥ 8 chars with a number or symbol) → `{access_token, user, subscription}` |
| POST | `/auth/login` | `{email, password}` → same as register |
| GET | `/auth/me` | → `{user, subscription}` |

## Trips

| Method | Path | Notes |
|---|---|---|
| GET | `/trips` | Your trips (cards). |
| POST | `/trips` | Create (does not plan). Body below. |
| GET | `/trips/{id}` | Full trip: preferences, feasibility, current itinerary, members, expenses, versions. |
| PATCH | `/trips/{id}` | `{title}` |
| DELETE | `/trips/{id}` | 204 |
| PATCH | `/trips/{id}/preferences` | Partial preferences (same fields as `changes` below). |
| POST | `/trips/{id}/plan` | Plan the trip. The **first** successful plan uses one plan from the quota; later calls are re-plans. Body `{changes?, stream?}`; `?stream=true` also streams. |
| POST | `/trips/{id}/replan` | Change an existing plan. Body needs at least one of `message`, `changes`, `swap`. Never uses quota. |
| GET | `/trips/{id}/alternatives?item_id=` | Ranked alternatives per itinerary item. |
| GET | `/trips/{id}/budget` | `{budget, breakdown, expenses}` |

Create body:

```json
{
  "origin": {"name": "Gurugram", "place_id": null, "lat": 28.4595, "lng": 77.0266},
  "destination": {"name": "Jaipur"},
  "start_date": "2026-10-03", "end_date": "2026-10-04",
  "start_time": "05:00", "end_time": "01:30",
  "budget": 5000, "travellers": 2,
  "traveller_type": "couple",
  "transport_modes": ["train", "cab"],
  "food": "veg",
  "trip_styles": ["romantic", "sightseeing"],
  "stay": "no_stay",
  "pace": "normal",
  "notes": "Want to see the sunset"
}
```

Allowed values:

- `traveller_type`: `solo | couple | partner | friends | family`
- `transport_modes`: `train | bus | metro | cab | own_car | flight | mixed`
- `food`: `veg | non_veg | both`
- `trip_styles`: up to 4 of `premium | balanced | budget | romantic | adventure | sightseeing | food | relaxed`
- `stay`: `premium | tier1 | tier2 | budget | oyo | hostel | no_stay`
- `pace`: `relaxed | normal | packed`

Limits: budget ≥ ₹100, 1–20 travellers, trips up to 7 days. An end time earlier than the start on the same date means the next day.

`changes` (used by `/plan`, `/replan` and the infeasibility buttons) accepts any of these: `budget`, `travellers`,
`start_date`, `end_date`, `start_time`, `end_time`, `traveller_type`, `transport_modes`, `food`, `trip_styles`, `stay`,
`pace`, `notes`, `exclude_place_ids`, `include_place_ids`, `reset_pins`.

Replan examples:

```json
{ "swap": { "item_id": "<itinerary item id>", "alternative_id": "<alternative id>" } }
{ "message": "make it cheaper and skip Hawa Mahal" }
{ "changes": { "pace": "relaxed" } }
```

### Plan responses

Non-streaming responses are `{status: "ready" | "infeasible", trip, feasibility}`.

With streaming, the response is `application/x-ndjson`, one JSON object per line:

```
{"type":"stage","key":"routes","label":"Finding routes","status":"running"}
{"type":"stage","key":"routes","label":"Finding routes","status":"done","detail":"14 options compared; best fit: train both ways"}
...
{"type":"result","status":"ready","trip":{...}}
```

A plan that has to wait its turn (more than `MAX_CONCURRENT_PLANS` running) starts with place-in-line events instead, and then the stages:

```
{"type":"queue","position":3,"waiting":7,"eta_seconds":40,"eta_range":[30,60]}
```

`eta_seconds` is made from the durations of recent real plans and is `null` (with `eta_range` `null`) until a plan has finished to estimate from. Only the latest position matters; it is re-sent when it changes.

The stage keys are, in order: `understand`, `routes`, `places`, `hours`, `prices`, `optimize`, `budget`, `alternatives`, `itinerary`.
Errors arrive as `{"type":"error","error":{...}}`, and `{"type":"ping"}` keep-alives can be ignored.

### Not now: `unavailable`

A plan that cannot be made right now is a normal answer (HTTP 200), never an error, and never uses a free plan:

```json
{"state":"unavailable","status":"unavailable","reason":"provider_busy","retry_at":"2026-10-07T09:40:00+00:00","quota_used":false,"message":"...","trip":{...}}
```

`reason` is `provider_busy` (a required provider is down or out of budget), `daily_cap` (today's limit on new plans) or `high_demand` (the queue is past `PLAN_QUEUE_HARD_LIMIT`; replans and paid plans still get in).

### Idempotency

`POST /trips/{id}/plan` and `/replan` accept `Idempotency-Key: <up to 100 chars of letters, digits, - _ . :>`. Send one per tap of the Plan button. The same key is the same plan: a retry after a dropped connection follows the plan that is already running (or returns its result), and never starts a second one or spends a second free plan. Without a key, an identical request while the same plan is still pending or running is treated the same way; after it has ended it is a new plan.

### Plans made under load

`itinerary.brownout` is `null`, or `{level, labels:{description?, alternatives?, options?, rail?}}` with the plain words for each part that was done with less effort (ADR 0008). A train affected carries `meta.brownout`.

### Infeasible result

```json
{
  "feasible": false, "kind": "budget",
  "message": "The requested trip is not feasible within ₹1,500.",
  "main_conflict": "Even the most economical version ... needs about ₹2,140 for 2 travellers.",
  "minimum": {"transport": 930, "food": 700, "local": 510, "total": 2140, "over_by": 640},
  "adjustments": [
    {"key": "higher_budget", "label": "Raise budget to ₹2,300", "description": "...", "apply": {"budget": 2300}},
    {"key": "nearby_destination", "label": "Pick a closer destination", "apply": {"action": "change_destination"}}
  ]
}
```

To use an adjustment, send its `apply` object as `changes` to `POST /trips/{id}/plan`.
`{"action": "change_destination"}` is a client-side hint to send the user back to the planner.

## Group members and expenses

| Method | Path | Body / notes |
|---|---|---|
| POST | `/trips/{id}/members` | `{name, email?, budget?, food_pref?}` |
| PATCH | `/trips/{id}/members/{mid}` | Partial |
| DELETE | `/trips/{id}/members/{mid}` | Owner and members with expenses can't be removed. |
| GET | `/trips/{id}/expenses` | `{expenses, summary}` |
| POST | `/trips/{id}/expenses` | `{description, amount, category, paid_by_member_id, split_type, member_ids?, custom?}` |
| DELETE | `/trips/{id}/expenses/{eid}` | 204 |
| GET | `/trips/{id}/settlement` | `{total, by_category, members[{paid, share, net}], settlements[{from_name, to_name, amount}]}` |

Split types:

- `equal`: all members; the paise remainder goes to the first members.
- `selected`: requires `member_ids`.
- `custom`: requires `custom: [{member_id, amount}]`, and the amounts must add up to `amount`.
- `one_person`: `member_ids: [id]`, or the payer if left out.

Categories: `transport | stay | food | activities | local | shopping | other`.

## Places

| Method | Path | Notes |
|---|---|---|
| GET | `/places/autocomplete?q=jai` | `{results: [{place_id, label, secondary, lat, lng}]}` |
| GET | `/places/reverse?lat=&lng=` | `{result}`, for "use my location". |

## Subscription

| Method | Path | Notes |
|---|---|---|
| GET | `/subscription` | Quota: `plan`, `free_plans_left`, `trip_credits`, `can_plan`, `prices`, `payments_mode`. |
| POST | `/subscription/upgrade` | `{product: "plus" \| "one_trip"}`. Mock checkout when `PAYMENTS_MODE=mock`. |
| POST | `/subscription/cancel` | Cancel PLUS. |

## Demo

| Method | Path | Notes |
|---|---|---|
| GET | `/demo/trip` | The cached sample trip (read-only), or 404 `demo_not_ready`. |
| POST | `/demo/plan` | Build the sample trip (`?stream=true` supported). No auth needed; cached for 6 hours. |
