# ADR 0009 — Phase 6: abuse and cost governance

Status: **Proposed. Plan only, except the two hotfixes in "Update 2026-10-07" below, which are built.** Per your instruction, building starts only after (1) the GitHub Actions run passes and (2) ADR 0008's "Not verified" list is updated from that run, and then only after you answer the questions at the end. Date: 2026-10-07.

## Context: what the code does today (read from the code, not measured under attack)

| # | Today | Why it matters |
|---|---|---|
| 1 | `middleware.client_key` takes the **first** value of `X-Forwarded-For`, and `gunicorn.conf.py` trusts `forwarded_allow_ips="*"` | Anyone can send `X-Forwarded-For: <random>` and get a fresh rate-limit bucket every request. **Every IP limit today is bypassable.** Equally, behind a proxy with no setting, everyone would share the proxy's address |
| 2 | Rate limits are a process-local sliding window, per IP, per bucket (`plan` 6/min, `auth` 10/min, `share` 30/min, `share-create` 20/min) | With 2 gunicorn workers the real limit is about double; with more replicas it is N times. No per-user and no per-device limit |
| 3 | `POST /auth/send-code` is limited only per IP (10/min), plus `OTP_RESEND_SECONDS` per target | One IP can ask for 600 SMS an hour, to **any phone number**. Each SMS is a paid message and harassment of a stranger. SMS and email are **not** in the ADR 0007 budgets |
| 4 | ADR 0007 caps spend per provider per day (Google $10, LLM $1, search $1, RailRadar 30, IRCTC1 10 calls) and plans per day (25). `ledger.add_user` records USD per user in memory | There is **no per-user spend cap**, and the ledger's per-user figures vanish on restart |
| 5 | `DAILY_PLAN_CAP=25` is shared by everybody | **One abuser with many throw-away accounts can use all 25 plans a day and lock every real user out.** The cap protects spend, but not fairness. This is the concrete SLO risk in this phase |
| 6 | Kill switches exist only as scattered env flags (`SHARE_LINKS_ENABLED`, `GATEWAY_ENABLED`, `BROWNOUT_LEVEL`), most needing a restart | During an incident "stop spending on X now" has no single, audited, instant lever |
| 7 | Sign-up needs a verified email and phone code (`email_verified_at`, `phone_verified_at`), so every account is "verified" at creation | The brief's "accounts without verified email use the lowest lane" has nobody to apply to; only **account age** can separate a fresh account from an established one |
| 8 | Share pages, share-text (the WhatsApp text) and UPI pay links are rate-limited per IP only | A hot share link is limited per visitor, not per link; creating links is not limited per user |

**Not measured:** any real abuse traffic (there is none yet). So the designs below are justified by reading the code, and each one says what number would show it was needed. Phase 8 adds an abuse simulation (one attacker with 100 fresh accounts against 10 real users) to the CI run; the lane and fairness choices should be judged against it.

## Decision

**COMPLEXITY GUARD:** built for real where the code reading above shows a hole or a cost; otherwise an interface behind a flag, with the metric that would turn it on.

| Item | Evidence | Decision |
|---|---|---|
| **Trusted client IP**: use the socket address, and read `X-Forwarded-For` only through `TRUSTED_PROXY_HOPS` (the n-th address from the right) | Row 1 | **Build, on.** Production logs a loud warning if a forwarded header arrives while hops is 0, because then everyone behind the proxy shares one address |
| **Token buckets**, one implementation of the arithmetic, two stores: in-process (default) and **Redis with an atomic Lua script** when `REDIS_URL` is set | Row 2 | **Build both.** In-process stays the default and the fallback if Redis is down (fail open to the local bucket, never closed: I3). The Redis path is verifiable now, because the CI workflow has a Redis service |
| **Per-user buckets** for plan, replan, share-create and share-text (WhatsApp text); **per-token** bucket on share views | Rows 2, 8 | **Build, on** |
| **Per-device bucket** (`X-Device-Id`, a random UUID the browser makes and keeps) | Rotatable by an attacker, so it can only add a limit, never be the only one | **Interface, off** (`DEVICE_LIMITS=false`). Turn on if denials by IP and user miss a pattern the device id would catch |
| **Send-code limits**: per target (email/phone) 5 an hour, per IP per day, and a **daily SMS/email budget** in the ADR 0007 budget system | Row 3 | **Build, on** |
| **Per-user daily spend cap** (persisted in `daily_counters`, key `u:<user>`) and **one service-wide daily number** that can be lowered during an incident | Row 4 | **Build, on** |
| **Anomaly alerts**: rules evaluated every minute in the existing flush thread; a pluggable sink | Needs a place to send them | **Rules built; log sink on; webhook and email sinks off** until you choose a channel (question 1). Dashboards and paging are Phase 7 |
| **Kill switches**: stored in the database, changed instantly through an admin endpoint, every flip audited, env var as the boot-time default | Row 6 | **Build, on** (list below) |
| **Lowest lane** for accounts younger than `NEW_ACCOUNT_MINUTES` | Rows 5, 7 | **Build, on**, with one extra rule: that lane is refused at the queue limit (40), not the hard limit (100), so a flood is shed from the bottom |
| **Reserved share of the daily plan cap for established accounts** | Row 5 | **Interface, off** (`NEW_ACCOUNT_PLAN_SHARE=1.0` = no reservation). On a launch day every user is new, so a reservation would refuse real people; turn it down only if the Phase 8 simulation or real traffic shows the cap being drained by fresh accounts |
| **Sign-ups per IP per day** | Row 5 | **Build, on** (`SIGNUPS_PER_IP_PER_DAY=5`) |
| CAPTCHA or proof-of-work | Not needed for any row above | **Not planned.** Revisit only if measured abuse gets past the limits |
| WhatsApp Part B (Business API) switch | Part B does not exist and is not planned | **The switch name is reserved** so it exists on the day Part B might; no code |

### 1. Client identity

`client_ip(request)`: the socket peer, unless `TRUSTED_PROXY_HOPS = n > 0`, in which case the n-th address from the right of `X-Forwarded-For` (the one our own proxy appended; anything to its left is client-controlled and ignored). `gunicorn.conf.py` stops using `forwarded_allow_ips="*"` and takes `FORWARDED_ALLOW_IPS` from the environment. The right value depends on where we host: **Phase 9 decides it**; until then the default is 0 and a spoofed header does nothing.

User identity is the signed-in user id (limits that matter come after authentication). The device id is optional and only ever tightens.

### 2. Token buckets

A bucket is `(capacity, refill per second)` per key. The arithmetic is one pure function, `take(tokens, last_ts, now, capacity, refill, n)`, used by both stores, so they cannot drift.

| Bucket | Key | Capacity / refill | Replaces |
|---|---|---|---|
| `plan` | user | 3, one per 20 s | new; the per-IP `plan` 6/min stays |
| `plan` | IP | 12, one per 5 s | today's IP limit, now un-spoofable |
| `share-create`, `share-text` | user | 10, one per 6 s | new |
| `share-view` | token | 120 per minute | new: a hot link stops being a free load test |
| `share-view` | IP | 30 per minute | today's limit |
| `send-code` | target (email or phone, hashed) | 5 per hour | new |
| `send-code` | IP | 10 per minute and 40 per day | adds the daily cap |
| `signup` | IP | 5 per day | new |

Numbers are starting values, all in env. A denial is HTTP 429 with `retry_after`, as today, and the frontend shows the existing calm message.

**Redis:** one Lua script (read tokens and timestamp, refill, take, write with a TTL, return allowed and the wait) is atomic by construction. Failure of Redis never blocks a request: the call falls back to the in-process bucket and a counter records it (`/readyz` already reports Redis down).

**Honest limit:** without Redis each worker has its own buckets, so the real limit is `workers x` the number above. Stated in the admin view.

### 3. Spend

- **SMS and email** join the budget system as groups `sms` and `email` in calls (`SMS_DAILY_CALLS`, `EMAIL_DAILY_CALLS`), warn 80%, stop 100%. At the stop, a send-code request gets the typed calm answer ("we can't send codes right now, try later", HTTP 200 state, no 5xx), not an error.
- **Per user per day**: `USER_DAILY_USD` (proposed **1.50**: two cold plans at the measured worst $0.35 and about sixteen replans at $0.05; an honest day is far below it). Reserved and settled through the same `reserve`/`settle` path, key `u:<id>`, persisted so a restart does not reset it. At the cap the person gets the calm "daily limit reached for your account, resets at midnight" answer in the `unavailable` shape, with their trip saved and **no free plan used** (I4).
- **Service-wide**: `SERVICE_DAILY_USD`, default the sum of the provider budgets ($12). One number to lower in an incident.
- All three are shown in `/api/system/budgets` and the plans view. Midnight is `Asia/Kolkata`, as before.

### 4. Kill switches

Stored in `kill_switches` (name, on, reason, set by, set at) with a 5-second in-process cache and an append-only `kill_switch_events` audit. Changed by `PUT /api/system/switches/{name}` (admin only, 404 to everyone else), with an env default `KILL_<NAME>=true` read at boot so a switch can also be set from the host. No restart is needed to flip one.

| Switch | When on | What the person sees |
|---|---|---|
| `provider:google-places`, `google-routes`, `google-geocoding`, `railradar`, `irctc1`, `openrouter`, `search`, `weather` | The gateway refuses the call with a new `Killed` error (a `GatewayError`, so every existing fallback applies) | Places down: the calm "busy" screen, free plan kept (ADR 0007). Routes, rail, AI, search, weather: the plan completes with those parts estimated or left out and says so in Plan notes |
| `ai_narration` | Forces brownout level 1 for new plans | "Trip description simplified to keep things fast" |
| `sharing` | Creating links and viewing them is off (the existing `SHARE_LINKS_ENABLED` behaviour) | The share button is hidden; an old link shows "not found" as today |
| `signups` | `send-code` for a new account and `register` answer HTTP 403 `signups_paused` with a calm sentence; sign-in is unaffected | "We're not taking new sign-ups for a little while" |
| `whatsapp_part_b` | Reserved. No code exists behind it | n/a |

### 5. Alerts

Rules, evaluated once a minute, each with a de-duplication window so one cause is one alert:

1. any budget group above 50% of its limit before 12:00 local, or above 80% at any time;
2. a user reaching `USER_DAILY_USD`;
3. a breaker open for over 10 minutes;
4. refunds above 20% of plans in the last hour (with at least 10 plans);
5. sign-ups above 20 an hour from one IP, or above 100 an hour overall;
6. any `late_finishes` (ADR 0008) or reaper refunds;
7. rate-limit denials above 100 an hour from one key;
8. a kill switch flipped (always).

Sinks: **log** (an `ALERT` line at ERROR level, also listed in `/api/system/alerts`) is on; **webhook** (any URL; Slack, Discord or ntfy all work and are free) and **email** (through the existing SMTP, to `ADMIN_EMAILS`) are built behind `ALERT_WEBHOOK_URL` and `ALERT_EMAIL=true` and stay off until you choose.

### 6. Lanes

`LANE_NEW = 3` after replan, paid and free. A free plan from an account younger than `NEW_ACCOUNT_MINUTES` (proposed 10) joins it; the aging rule (ADR 0008) still promotes a waiting plan, so nobody starves. In `runner.admit`, lane 3 is refused at `PLAN_QUEUE_LIMIT`, lane 2 at `PLAN_QUEUE_HARD_LIMIT`. Honest weakness: an attacker can wait ten minutes. The lane orders and sheds; the bucket on sign-ups per IP and the per-user spend cap are what bound the damage.

## Flags

| Flag | Default | Off means |
|---|---|---|
| `TRUSTED_PROXY_HOPS` | 0 | the socket address is the client |
| `RATE_LIMIT_BACKEND` | `auto` (Redis if `REDIS_URL`, else in-process) | `memory` forces local buckets |
| `DEVICE_LIMITS` | false | no device buckets |
| `USER_DAILY_USD`, `SERVICE_DAILY_USD`, `SMS_DAILY_CALLS`, `EMAIL_DAILY_CALLS` | 1.50, sum of providers, 200, 500 | 0 = no cap on that one |
| `NEW_ACCOUNT_MINUTES`, `NEW_ACCOUNT_PLAN_SHARE` | 10, 1.0 | share 1.0 = no reservation |
| `SIGNUPS_PER_IP_PER_DAY` | 5 | 0 = no limit |
| `KILL_<NAME>` | unset | the database value rules |
| `ALERT_WEBHOOK_URL`, `ALERT_EMAIL` | unset, false | log sink only |

## Tests (all offline; the Redis ones run in the CI Redis service)

- **Buckets:** over any window the number allowed never exceeds capacity plus refill x time (randomised sequences); 50 threads on one bucket never exceed capacity; the in-process and Redis stores give the same answers for the same sequence; Redis down falls back to local and never blocks.
- **Identity:** a forged `X-Forwarded-For` does not change the key at hops 0; at hops 1 it takes the right-most address; production warns when a forwarded header arrives at hops 0.
- **Send-code:** the sixth request for one phone in an hour is refused; the SMS budget stop answers calmly (HTTP 200 state), never 5xx.
- **Spend:** a user at `USER_DAILY_USD` gets the calm answer and keeps every free plan; the cap survives a restart; the service number lowers every provider's headroom.
- **Kill switches:** each switch changes exactly what the table says, in under 5 seconds, no 5xx on any path (I3), no quota used (I4); every flip leaves an audit row; non-admins get 404.
- **Lanes:** replan, paid, free, new in that order; the new lane is refused at the queue limit; aging promotes it.
- **Alerts:** each rule fires once per cause and not again inside its window.
- **Migration 0011** (kill switches, audit) is expand-only (the lint test).
- **Phase 8 hook:** one attacker with 100 fresh accounts against 10 real users, recorded in the CI results.

## Risks

- **A wrong `TRUSTED_PROXY_HOPS`** either lets a spoofed header through (too high) or makes everyone share one address (0 behind a proxy). The warning above is the guard; Phase 9 sets the value with the host.
- **Kill-switch reads add a lookup to every provider call.** A 5-second in-process cache keeps it off the hot path; a flip takes up to 5 seconds per process.
- **Per-user spend needs a write per charge.** It reuses the 10-second delta flush (ADR 0007), so no new per-call write in the planner path (the SQLite lock lesson).
- **Limits multiply by workers until Redis is in production.** Redis is optional and free as a container; a managed one is a paid service and I will not buy it without asking.

## Questions for you

1. **Alert channel.** Log only for now, or also a free webhook (Slack/Discord/ntfy: you give me the URL as an env var, never in code) and/or email to `ADMIN_EMAILS`?
2. **Numbers.** `USER_DAILY_USD=1.50`, 5 sign-ups per IP per day, 200 SMS and 500 emails a day, 5 codes per target per hour, `NEW_ACCOUNT_MINUTES=10`. OK as starting values?
3. **Sign-ups paused wording.** "We're not taking new sign-ups for a little while. Please try again later." Fine?
4. **Device id.** A random id kept in the browser, used only to tighten limits and never to identify or track a person across sites. OK to add the `X-Device-Id` header (the limit stays off by default)?
5. **Reserved share for established accounts.** Keep it off until the simulation or real traffic shows a need?
6. **Where it will run.** `TRUSTED_PROXY_HOPS` and Redis in production both depend on the host. Are you happy to settle them in Phase 9 and run with hops 0 and in-process buckets until then?

## Update 2026-10-07: two hotfixes shipped, and your principles applied

**Shipped now (before the first upload), because they cost money:**

- **Client address.** `X-Forwarded-For` is never believed by default. `TRUSTED_PROXY_IPS` (empty by default; IPs or networks) names our own proxies; only for a
  request that comes from one of them is the header read, from the **right**: the right-most address that is not one of ours is the client, and anything to its
  left is a claim and is ignored. gunicorn's `forwarded_allow_ips` (no more `"*"`) and uvicorn's proxy handling (off unless the list is non-empty) come from the
  same setting. This replaces the `TRUSTED_PROXY_HOPS` idea above. Tests: a spoofed header from an untrusted client is ignored; the right address is chosen behind one
  and two proxies; networks, IPv6, ports and garbage; a changing header does not buy a new rate-limit bucket.
- **Send-code (paid SMS).** 3 codes per number per hour and 5 per day; 10 per address per hour (counted from the table, with only a keyed hash of the address
  stored: migration 0011); 60 s between codes to the same number; one live code per number; **Indian `+91` mobile numbers only**; texts counted in the budget ledger
  (`sms`, `SMS_DAILY_LIMIT`, default 50; a failed send gives its count back; the free console provider is not counted); `KILL_SMS` (live) stops texts. A stop is a calm
  HTTP 429 sentence, never a 5xx, and no code is left behind. The answer is identical whether or not a number or address has an account: `send-code` no longer looks it up
  (the sign-up now checks the codes first, so "already on an account" is said only to someone holding valid codes for it).

**What your principles changed in this plan:**

| Was | Now |
|---|---|
| "New account" = younger than 10 minutes | **Younger than 24 hours**, for both the plan limit and the lane |
| Per-user plan limit: none (only spend) | **New accounts (under 24 h): 1 new plan a day. Verified older accounts: 3 a day. Replans of an existing trip do not count.** Over it: the calm `unavailable` answer, trip saved, no free plan used |
| Reserved share for established accounts: off (1.0) | **0.7**: fresh accounts together cannot take the last 30% of `DAILY_PLAN_CAP` (at most 17 of 25 new plans a day) |
| Planning needs a verified account | Already true (sign-up needs both codes). Also checked in the plan route (verified email **or** phone), so it holds if sign-up ever changes |
| Per-user spend cap `USER_DAILY_USD=1.50` | Kept: 12.5% of the $12 service total, under your ~20%. A startup warning fires if it is set above 20% of `SERVICE_DAILY_USD` |
| IP-based limits | Only ever on `client_ip()` (the corrected address) |
| Send-code numbers (5 an hour, 40 a day per address) | Superseded by the hotfix numbers above |

## The six questions, with my recommended default (nothing is built until the Actions gate)

| # | Question | Recommended default | Status |
|---|---|---|---|
| 1 | Where do alerts go? | Log sink on, plus **email to `ADMIN_EMAILS`** through the existing SMTP; a free webhook (Slack, Discord or ntfy) only if you give me its URL as an env var | Not covered by your principles: the default stands unless you say otherwise |
| 2 | Starting numbers | Per your principles: 1 plan a day under 24 h, 3 for older accounts, user spend cap 1.50 (12.5%), reserved 30%. Also: 5 sign-ups per address per day, 500 emails a day, SMS 50 a day (shipped) | **Approved by your principles** |
| 3 | Wording when sign-ups are paused | "We're not taking new sign-ups for a little while. Please try again later." (HTTP 403, `signups_paused`; sign-in unaffected) | Not covered: default stands |
| 4 | Device id (`X-Device-Id`) | Accept the header (a random id the browser keeps; used only to tighten limits, never to identify anyone); the limit stays **off** | Not covered: default stands |
| 5 | Reserved share for established accounts | **0.7** (was: off) | **Approved by your principle** (I changed my default to match it) |
| 6 | `TRUSTED_PROXY_IPS` and Redis in production | Empty list and in-process buckets until Phase 9 picks the host (limits then multiply by the worker count; stated in the admin view) | Hotfix 3a is the approved half; Redis stays Phase 9 |

**Where my recommendation goes against your principles (waiting for you):**

1. **Releasing the reserve late in the day.** On a launch day every user is under 24 h old, so the 30% reserve (8 of 25 plans) would sit unused while people are refused. I recommend releasing the unused reserve to fresh accounts at 18:00 local. That loosens "can't take the last 30%", so it is **not** in the plan until you say yes. Without it the rule is exactly as you wrote it.
2. **A heads-up, not a conflict:** "1 new plan a day under 24 h" means a new user cannot use both of their "2 free plans" on the first day. That is your rule and I will build it; the landing and pricing copy ("2 free plans") needs a sentence to say so, and the refusal needs to read as a calm "tomorrow", not a limit. Tell me if you want different copy.

## Not in this phase

OpenTelemetry, dashboards and paging (Phase 7); Hypothesis and chaos tests (Phase 8); the hosting-specific values and point-in-time recovery (Phase 9); WhatsApp Part B.
