# ADR 0010 — Admin console and age groups

Status: **Accepted, built.** Date: 2026-10-11. Migrations 0012 and 0013 (both expand-only).

Two requests, one ADR: places that suit the party's ages, and an `/admin` console with control over users, trips and the live service.

## 1. Age groups

**Ask.** The wizard's "Budget and group" step asks *How old is everyone?* as four bands, multi-select: Kids 0–12, Teens 13–17, Adults 18–59, Seniors 60+ (a solo traveller picks one). A sentence typed on the home page fills it in ("with 2 kids", "my parents", "5 year old"). Bands, not exact ages: nothing personal is stored, and a band is all the engine needs.

**Stored** on `trip_preferences.age_groups` (nullable JSON; migration 0012). Empty or missing means "not asked" and **nothing changes**, so every existing trip and every older client behaves exactly as before.

**What it changes** (`app/services/age_fit.py`, all deterministic and explained in "Why this?"):

| Rule | Effect |
|---|---|
| Adults-only places (night clubs, casinos, bars that are not also restaurants) | Never planned when anyone is under 18, unless the traveller pinned that exact place. A restaurant that lists "bar" among its types is **not** treated as a bar |
| Ranking | Per-tag adjustment per group, bounded to −0.5 … +0.4 (scores run roughly 0 … 1.3). **The strictest member rules**: if a place suits one group badly the whole party's score drops; otherwise the best fit lifts it. A pinned must-see (+10) is never demoted out |
| Pace | Kids or seniors in the party: `normal` becomes `relaxed` (3 stops a day instead of 5, 15% longer at each). A pace the traveller chose (`relaxed`, `packed`) stands |
| Walking | Kids or seniors: 500 m between stops before a ride is used (900 m otherwise) |
| Search | One extra place-search hint in front of the trip's own ("family friendly attractions", "gardens and lakes"). Still at most three extra searches per plan, so the worst-case Google cost is unchanged; it displaces a third style or favourite hint when there are three |
| Tags | `night_club`, `bar`, `casino` now map to the `nightlife` tag (before, a bar was tagged "landmark", so the Nightlife favourite did little) |

**Checked** with the simulated providers and a real browser: a Gurugram → Jaipur day for kids + seniors + adults gave 3 stops (palace, museum, temple, each labelled "easy-going, suits older travellers"); the same trip for adults gave 5, including the fort climb.

## 2. Admin console

**Who.** *Owners* are the emails in `ADMIN_EMAILS` (set on the server, never from the console). An owner can make other people *admins* from the console (`users.is_admin`, migration 0013). Anyone else, signed in or not, gets the same plain 404 as for any address that does not exist; a suspended admin loses access at once.

**What an admin can do** (`/admin`, API under `/api/admin`):

| Area | Actions |
|---|---|
| Overview | Users, Plus subscribers, trips, plans in 24 h with refunds, 14-day charts, planner queue and busy level, today's spend against each budget, switches that are on, recent activity |
| Users | Search, filter (status, plan), sort, page. Per user: suspend / reactivate, give Plus for N days or end it, set free plans used (or give them all back), set one-trip credits, mark email or mobile verified, rename, make or remove admin (owners), delete (type the email to confirm) |
| Trips | Search, filter, open (the plan as the traveller sees it, read-only), delete |
| Plans | Recent plan runs: running, waiting, failed, refunded, late |
| Controls | Five switches: new sign-ups, new plans and re-plans, SMS codes, share links, AI-written descriptions. Two numbers: busy-mode level (0–4) and free plans per account |
| Audit log | Who did what, to whom, from what to what, and why |

**Guard rails** (all tested): nobody can suspend, delete or demote themselves; owners cannot be suspended, deleted or demoted from the console; only an owner can make or remove an admin or suspend / delete another admin; deleting needs the typed email; every change writes an audit row **in the same transaction**; the audit table is append-only (no route edits or deletes it) and has no foreign keys, so it outlives the people and trips it mentions; admin answers carry `Cache-Control: no-store`; nothing returns a password hash, code or key.

**Live controls** (`app/core/runtime.py`, table `runtime_settings`): a row means "an admin chose this"; no row means the environment or built-in default applies, so *Reset* hands a control back. Reads are cached 5 s per process (a change reaches every worker within 5 s, and no hot path queries per call). Reading **never raises**: a missing table or slow database returns the default, so a lever can never become an outage (I3). Precedence for a switch: admin's choice, then `KILL_<NAME>` in the environment, then the setting.

What each switch does, and what people see:

| Switch | Effect | People see |
|---|---|---|
| `signups` | `send-code` and `register` answer 403 `signups_paused`; sign-in unaffected | "We're not taking new sign-ups for a little while…" |
| `plans` | New plans and re-plans answer `unavailable` / `paused`, HTTP 200, no free plan used (I3, I4) | The calm "Planning is paused" screen with the trip saved |
| `sms` | No texts (the existing `KILL_SMS`) | The existing "can't send text codes" sentence |
| `sharing` | Creating and opening share links off | Share button says it is off; old links show "not found" |
| `ai_narration` | Brownout level at least 1 | "Trip description simplified to keep things fast" |

## Relationship to ADR 0009 (Phase 6)

ADR 0009 plans database-backed kill switches with an audit trail and an admin endpoint. This is that part, built on request, with two table names that differ from the proposal: `runtime_settings` (switches **and** numbers) and `admin_audit`, instead of `kill_switches` and `kill_switch_events`. **Nothing else from Phase 6 is built**: token buckets, per-user spend caps, new-account lanes, alerts and the `provider:*` switches are still the plan only. The ADR 0009 gate (the Actions run, the ADR 0008 list, its six questions) is unchanged for those.

## Tests

`tests/test_age_fit.py` (21) and `tests/test_admin.py` (21): the gate, every guard rail, every switch's real effect, precedence over the environment, database-down resilience, the audit trail, CORS preflight for PUT / PATCH / POST / DELETE, and both migrations up and down. In a real browser against an isolated stack (demo providers, throwaway database): stop and restart sign-ups, give Plus, suspend and reactivate, a non-admin sees the 404 page, the age step pre-fills from a typed sentence.

**Found by the browser, not by the tests:** the CORS allow-list lacked `PUT`, so every switch button did nothing from a real page while the API tests passed. Fixed, with a regression test.

## Not verified / open

- Four existing tests fail on today's date and are not touched by this change: three in `test_gateway.py` compare a retry time from the test's own frozen 7 Oct clock with the real clock (they cannot pass after 8 Oct); `test_multiday.py::test_budget_that_cannot_feed_everyone_is_infeasible` lands ₹4 over its threshold with today's demo dates. Not confirmed against a pre-change baseline (no git here).
- PostgreSQL: both migrations were run on SQLite only (the Actions workflow in `docs/GITHUB.md` is where Postgres is checked).
- The backend must be restarted to load the new routes; the dev database is already migrated to 0013 (backup: `%TEMP%\tripai-before-0012.db`).
- Age bands change ranking and pace, not safety: a place is not "certified" for children. The Google tag mapping is a best effort from provider types.

## Update 2026-10-11: sign-up verifies the email only

Decision (owner): no SMS provider yet, and no wish to pay for one before launch. **Sign-up now proves the email with a code; the mobile number is collected for WhatsApp trip sharing later.**

- `POST /auth/register` needs the email code only. `phone_code` is optional and ignored unless **`REQUIRE_PHONE_CODE=true`**, which restores the old two-code sign-up for when an SMS provider exists. Accounts made without it have `phone_verified = false` (the admin console shows "On file", and "unverified" now means the email).
- `POST /auth/send-code` with `channel: "phone"` answers 400 `phone_code_off` while the flag is off, so nothing can trigger a paid text.
- **One number, one account.** The number is normalised to `+91XXXXXXXXXX` (so `98765 43210`, `098765 43210` and `+91 98765-43210` are the same number) and refused with 409 `phone_taken`, which the form shows under the mobile field. The unique key on `users.phone` backs it up for two sign-ups at the same instant. Trade-off, stated: because the number is no longer proven by a code, someone who holds a valid code for their own email can learn whether a given number is already registered; the code limits (per address, per number, per connection) bound how often.
- In **development only**, a mail server that is unreachable or misconfigured falls back to showing the code on screen, so a blocked network cannot lock a developer out of sign-up. Production refuses honestly (`delivery_failed`).
- `scripts/smtp_check.py` logs in to the mail server and says which problem you have (settings missing, a normal Gmail password instead of an app password, or the network cutting the connection). It sends nothing.

## Update 2026-10-11: a shorter plan view, and "send to my WhatsApp"

- **Compact plan (default).** The itinerary is one line per stop (time, what, a line of detail with the travel time from the last stop, the cost); tapping a line opens the full card (hours, Why this?, Swap, sources). A Compact / Detailed switch is remembered in the browser. For trips of more than one day, day chips show one day at a time or all days; the day overview cards jump to that day.
- **Send to my WhatsApp.** Share responses carry `wa_me_url`: `https://wa.me/<the signed-in person's own saved number>?text=<the plan message>` (null if they have no number). The plan page has a one-tap card: it makes (or reuses) a private link with the budget hidden and opens WhatsApp on that number with the message typed in. **The person taps Send in WhatsApp; Yatro sends nothing.** Sending by itself would need the paid WhatsApp Business API (Part B), which is not used and not planned; invariant I9 (opt-in) is kept because the tap is the opt-in and no number but the person's own is ever put in the link. The message holds no phone number; the number is only in the chat address.
- Tests: three in `test_sharing.py` (own-number link and its exact message, no link without a number and nobody else's number leaks, digits-only formatting). Browser-checked: compact rows, expand, Detailed, day chips, the WhatsApp link opens the right chat, no sideways overflow at 320 / 390 / 768.

## Update 2026-10-11: the plan by email, automatically; a richer WhatsApp message

- **Automatic email.** When a **new** plan is ready (the first successful plan of a trip, never a re-plan) it is emailed to the owner's own verified address: a real HTML email (header, cost cards, the summary, a banner per day, an emoji and the cost per stop, tips, an "Open your plan" button and a "Send to my WhatsApp" button) plus a plain-text part. It is built from the same allow-listed view as the share page, so train and Google facts go through the same gates (I1, I6) and nothing about anyone else can appear (I8); everything typed by a person is HTML-escaped. "Open your plan" points at the private trip page (it needs sign-in), so no public link is created behind the person's back.
- **It can never hurt a plan (I3).** It runs after the plan is saved, in a background thread, and every failure is only logged. Skipped, quietly, when no mail server is configured, when the person switched it off, or when an admin stopped it.
- **Controls.** `users.email_plans` (default on; migration 0014) with a switch on the Account page; the admin console switch `plan_emails`; `/api/config` reports `plan_emails` so the pages only promise it when it is real. "Email me this plan" sends the current plan on demand (rate-limited like sharing).
- **WhatsApp stays one tap.** Sending a WhatsApp message by itself would need the paid WhatsApp Business API (Part B), which is not used; a web page also cannot open a new tab without a tap. So the plan page has the one-tap "Send to my WhatsApp" (their own number, message typed in), and the email carries the same button. The **message** is the richer part: `🧳` title, `━━` day banners, an emoji per stop (🚆 🚌 ✈️ 🍛 🏰 🛕 🏛️ 🌇 …), `💰` total, `💡` tips (dropped first when a trip is long), `🔗` link.
- Tests: `tests/test_plan_email.py` (19): sent once for a new plan and not for a re-plan, the opt-out, the admin switch, no mail server, a mail failure leaving the plan intact, content and escaping, no one else's data, the on-demand endpoint's refusals, the sender (headers, TLS, login) and migration 0014. Browser-checked on an isolated stack (the mail server pointed at a dead address).