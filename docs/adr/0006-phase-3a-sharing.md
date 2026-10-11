# ADR 0006 — Phase 3, Part A: share links, WhatsApp text, native share, UPI links

Status: **Accepted and implemented** (user decisions 2026-10-06). Date: 2026-10-06. **Part B (WhatsApp Business API) is out of scope** and is not
planned or built.

## Context

- **No sharing exists today.** A trip is visible only to its owner, after signing in.
- **Data that must never leak (I8).** Trips hold members' emails, member budgets, the trip budget, and expenses (who
  paid what). User accounts hold email, phone and password hash.
- **Expenses already settle up.** `expense_service.summary` returns a minimal list of "A pays B ₹X" transfers, which is
  the natural place for a UPI link.
- **Rate limiting** is the in-memory per-IP limiter in `core/middleware.py`.
- **I6.** ADR 0005 governs Google content on share pages and in shared text. Question 5 there, on whether place names
  may go into WhatsApp text, changes the text format below.
- **I9.** Nothing in Part A sends a message. Every WhatsApp message is composed and sent by the user from their own
  WhatsApp, so no message ever comes from Yatro's number.

## Decision

### 1. Share links

**Table `share_links`** (migration 0007):

| Column | Holds |
|---|---|
| `id` | |
| `trip_id` | |
| `created_by` | |
| `token_hash` | |
| `token_prefix` | first 6 characters, for the owner's list |
| `created_at` | |
| `revoked_at` | |
| `expires_at` | nullable |
| `last_viewed_at` | |
| `view_count` | |

**Token:**
- 32 random bytes from `secrets.token_urlsafe` (256 bits). Only an HMAC-SHA256 of it, keyed with the app secret, is
  stored, the same pattern as the OTP codes.
- The full link is shown to the owner **once, when created**, and is kept in their browser. If it's lost, the owner
  makes a new link and revokes the old one.
- A database leak therefore doesn't open shared trips.

**Endpoints:**

| Endpoint | Who | Does |
|---|---|---|
| `POST /api/trips/{id}/share` | owner | creates a link and returns the URL once |
| `GET /api/trips/{id}/shares` | owner | lists links: prefix, created, views, revoked |
| `DELETE /api/trips/{id}/shares/{share_id}` | owner | revokes; the next request to that link is 404 |
| `GET /api/share/{token}` | anyone | the public view below. 404 for unknown, revoked or expired links, the same response for all three. Its own rate limit (30/min per IP). `Cache-Control: no-store`. `X-Robots-Tag: noindex`. |

**Public view model (I8), built by allow-list, not by redaction.** A new `share_view(trip)` constructs a fresh dict
containing only:
- trip title, destination as typed, dates, number of travellers, currency;
- per day: items with kind, our times, title, cost per item and their fare/verification labels, walking/transit legs,
  and the "Plan notes" text;
- the itinerary total cost, and cost per person.

**Never included:**
- emails, phones, member names, member budgets, the trip budget and remaining budget, expenses and settlements;
- owner or user ids, preferences and notes, pipeline traces;
- internal ids other than item ids;
- alternatives and the replan history.

Items pass through the same gates as the owner's view: I1 train facts, I1 fares, and the I6 Google gate with refresh
(ADR 0005).

**Share page.** Frontend route `/s/[token]`:
- read-only timeline and map (the same components with editing off);
- the "Plan your own with Yatro" call to action;
- Open Graph title "Jaipur · 17 Oct · 2 travellers", with no personal data;
- `noindex` set.

### 2. WhatsApp-formatted text and wa.me links

**Text formatter.** `share_text(trip, link)` is deterministic, on the backend so the I8 tests cover it. It is returned
by `GET /api/trips/{id}/share-text` (owner only). It uses WhatsApp formatting (`*bold*`, `_italic_`, lists):

```
*Jaipur day trip* · Sat 17 Oct · 2 travellers
_Planned with Yatro_

*Day 1*
05:40  Train · New Delhi → Jaipur (Shatabdi Express 12015)
10:10  Stop 1 · Hawa Mahal
...
Total ≈ ₹4,720 (₹2,360 each) · fares estimated

Full plan: https://yatro.app/s/abc…
```

**Constraints:**
- No AI text.
- Train names and times only where the I1 gate allows them; otherwise "Train, morning (timing estimated)".
- Fares carry the word "estimated" when they are.
- The text stays under about 3,500 characters; longer trips are shortened to "Day 3: 5 stops — see the link".
- **Place names depend on ADR 0005 question 5.** Behind the flag `SHARE_TEXT_PLACE_NAMES`, default **off**:
  - Off: the text says "Stop 1, Stop 2 …" with our times and costs, and the link carries the names.
  - On: the names are included, once you confirm that's allowed.

**wa.me link.** `https://wa.me/?text=<url-encoded text>`, with no phone number, so the user chooses the chat. It opens
WhatsApp, or WhatsApp Web on desktop. Yatro sends nothing (I9).

### 3. Native share

On the trip page, a **Share** button:
- `navigator.share({title, text, url})` where the browser supports it (most phones);
- otherwise a small sheet with: Copy link · Copy text · WhatsApp (wa.me) · Revoke link.

Making a link requires an explicit tap. Nothing is shared automatically.

### 4. UPI payment links (optional)

**The link.** In the expense settle-up, each "A pays B ₹X" row can show **Pay via UPI**:
`upi://pay?pa=<B's UPI ID>&pn=<B's name>&am=<X>&cu=INR&tn=<Yatro: trip title>`.
- Only for INR trips, and only on mobile.
- It opens the payer's UPI app with the fields filled in. **Yatro never touches the money.**

**Where B's UPI ID comes from** (personal data, so opt-in and minimal):
- **The owner's own UPI ID** is an optional field in account settings, stored on `users.upi_id`. It is used for rows where
  the owner is paid.
- **Anyone else's UPI ID** is typed when tapping "Pay via UPI" and **not stored**.
- UPI IDs are checked against `^[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z]{2,64}$`.
- They **never appear on share pages or in share text** (I8). A settle-up text for the group is a separate, explicit "Copy
  settle-up" action by the owner.

**Known risk.** Some UPI apps refuse or warn on `upi://` links paying a personal (non-merchant) UPI ID, for fraud
reasons. So the row always offers **Copy UPI ID** as a fallback, and the feature is behind a flag.

### Flags (all can be switched off)

| Flag | Default | Off means |
|---|---|---|
| `SHARE_LINKS_ENABLED` | true | no links can be made; existing links return 404 |
| `SHARE_TEXT_PLACE_NAMES` | false | stop numbers instead of names (see ADR 0005 question 5) |
| `UPI_LINKS_ENABLED` | true | no "Pay via UPI" rows |
| `SHARE_RATE_LIMIT_PER_MINUTE` | 30 | (the limit for the public share endpoint) |

### Files

| File | Change |
|---|---|
| `models/share.py`, migration 0007 | `share_links`; `users.upi_id` |
| `services/share_service.py` (new) | token create/verify/revoke, `share_view`, `share_text` |
| `api/routes/share.py` (new); `trips.py` | public share endpoint; owner endpoints |
| `core/middleware.py` | rate limit for the share path |
| `schemas/auth.py`, `routes/auth.py` | optional `upi_id` on the profile |
| frontend `app/s/[token]/page.tsx` (new) | public share page |
| frontend `components/trip/ShareSheet.tsx` (new); `TripDashboard.tsx` | Share button and sheet |
| frontend `ExpenseSplitter.tsx`; `lib/upi.ts` (new) | Pay via UPI and Copy UPI ID |
| frontend `lib/api.ts`, `lib/types.ts` | endpoints and types |

### Tests

**I8 (the core):**
- The public view and the share text contain none of the following, checked by searching the serialized output for
  each value from a seeded trip:
  - the owner's email, phone and password hash;
  - members' emails and budgets, the trip budget and remaining budget;
  - expense descriptions and amounts, settlements, and the UPI ID.
- `share_view` keys match an exact allow-list snapshot, so a new field can't leak silently.
- A revoked link → 404. An expired link → 404. An unknown token → 404, with the same body.
- Only the owner can create, list or revoke links. Another user gets 404.
- The token is not stored in plain text; the database holds only the hash.

**Other behaviour:**
- **Gates on share pages:**
  - an untimetabled train shows no name, number or time;
  - an estimated fare is labelled;
  - Google content more than 30 days old is gated (once ADR 0005 is in).
- **Share text:**
  - deterministic for a given trip, with no AI;
  - stays under the length cap;
  - with `SHARE_TEXT_PLACE_NAMES` off, no place names appear.
- **wa.me link:** has no phone number and is URL-encoded correctly; it round-trips non-ASCII text and line breaks.
- **UPI link:**
  - exact `upi://pay` format and amount;
  - rejected for non-INR trips and invalid UPI IDs;
  - never present on share pages.
- **Rate limit:** the share endpoint limits per IP.
- **Flags:** each flag switches its feature off.

## Consequences

- **Spread.** Trips can spread by WhatsApp with no message from Yatro's number (I9) and no new infrastructure or paid
  service.
- **Lost links.** Hashed tokens mean a lost link can't be re-shown, only replaced. That's the safer trade for links that
  open someone's trip.
- **Refresh cost.** Share pages can trigger Google refreshes (ADR 0005). Each view of an old trip can cost about $0.20,
  bounded by the daily cap. A link going viral is capped by the share rate limit plus the refresh cap.

## Questions for you

1. **Costs on the share page.** Show item costs and the total (default: yes, with labels), or hide all money?
2. **Link expiry.** Links last until revoked (default), or expire after N days?
3. **UPI IDs.** Store only the owner's own UPI ID, opt-in, and type others' each time (default)? Or store a UPI ID per
   member, entered by the owner?
4. **Place names in WhatsApp text.** This depends on ADR 0005 question 5. Until you confirm, the default is stop numbers.

## Decisions (2026-10-06) and what changed from the plan above

1. **Costs on share pages.** Per-stop costs and the total are shown, each with its label. The owner's budget and "left" are hidden
   unless the owner turns on **Show budget** (when sharing, or later from Shared links). The expense split appears only for
   **Plan + split**.
   - *Found while testing:* the plan summary says "Planned ₹4,584 of your ₹7,321 budget, leaving ₹2,737". Without Show budget the
     share drops every summary or tip sentence that mentions the budget or either amount.
2. **Expiry.** The default is 30 days after the trip's last day. The owner can extend by 30 days, remove the expiry, put the default back,
   or revoke. The trip page has a **Shared links** card listing each active link with Revoke. It shows the first 6 characters
   of the token, never the link, because only a hash is stored.
3. **UPI IDs.**
   - Only the signed-in person can save their own (`PATCH /auth/me/upi`, account page). It is never in the share view or text.
   - When the owner pays, they may type the payee's ID. It is held in browser state only and goes nowhere.
   - Otherwise settle-up shows "Ask <name> for their UPI ID" and Copy amount. When the owner is paid, it offers "Copy payment request"
     with their own saved ID.
   - **Member invite flow: not built.** Trips are owner-only and members have no accounts, so no other person can enter their own UPI
     ID yet. This needs member accounts or an invite flow, which is a separate piece of work.
4. **Place names in WhatsApp text.** Included, with no stop numbers and no `SHARE_TEXT_PLACE_NAMES` flag. The text is made when the
   person taps Share, after refreshing Google details that are over 30 days old, and is stored nowhere. A **Google Maps attribution**
   line is added (ADR 0005).

**Other deviations from the plan above:**
- The share view has no origin and no coordinates (so no map). Titles can still contain the origin's name (for example "Back in Gurugram").
- Plan warnings are left out of the share.
- The owner endpoints are `POST /trips/{id}/share` (creates the link and returns the text),
  `POST /trips/{id}/share-text` (the text again, for a link the browser already holds), `GET /trips/{id}/shares` (the list),
  `PATCH` (extend, expiry, show budget, plan + split) and `DELETE` (revoke) on `/trips/{id}/shares/{id}`.
- The `Cache-Control: no-store` and `X-Robots-Tag: noindex` headers are set in the middleware, so the 404 and 429 answers carry them too.
- `/api/config` now reports `share_links_enabled` and `upi_links_enabled`, so the UI hides what is switched off.

## Results

**Tests.** 504 backend tests pass, 32 of them new in `tests/test_sharing.py`, and the frontend type check is clean.
- **I8.** A seeded trip has an owner with a UPI ID, a member with an email and a budget, and an expense. The public view and text must
  contain none of: the owner's email, phone, id, password or hash; the member's email or budget; the UPI ID; the expense; and the budget
  or remaining amount (searched as raw digits, as `₹7,321`, and as the digits with commas). The view's keys equal an exact allow-list.
  There are no coordinates and no route lines.
- **Links.** Unknown, revoked and expired links are indistinguishable (same status and body). Default expiry, extend, never and default
  again are covered. A stranger can't create, list, change, revoke or use a link. A link can't be used through another trip.
  The token is not stored. Responses are no-store and noindex. The rate limit returns 429. Each flag switches its feature off.
- **Text.** It has real place names and the link. It is made fresh, with no column that could store it. The `wa.me` link has no phone
  number and round-trips the text. A long trip is trimmed to fit.
- **UPI.** Valid and invalid IDs; nobody else can set one; no member field or settlement field carries one; the switch works.

**Checked on screen** (headless Chrome with the API answered from fixtures; nothing real was called):
- The Share button, the share sheet, the created link with its message and the Share / WhatsApp / Copy buttons, and the Shared links card.
- The public share page, fresh on desktop and expired on a phone.
- The settle-up rows, including "Pay via UPI" on a phone.
- **A layout bug fixed:** the Group tab was wider than a phone screen. It was already like that before this phase; the fix is one class.

**Not verified:** a real tap on "Share…" (the native share sheet), WhatsApp opening from the `wa.me` link, and a UPI app opening from
the `upi://` link. I could only check the links' text. Some UPI apps refuse payments to personal IDs from a link, which is why Copy
amount is always offered.

## Questions for you

1. The Google terms questions in ADR 0005, above all (c) logo versus text, (d) WhatsApp text, (f) the AI narration, and (g) signed-out viewers.
2. **Member UPI IDs** need member accounts. Do you want an invite flow as its own phase?
3. **Production.** `PUBLIC_APP_URL` must be set so links point at the real site (it defaults to localhost:3000).
