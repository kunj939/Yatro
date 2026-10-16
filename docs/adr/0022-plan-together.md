# ADR 0022: Plan together (one link in the group chat)

Date: 2026-10-16. Status: accepted and built (2026-10-16).

**Context.** Group trips die in the group chat: "bro, let's go somewhere", forty messages, no plan. Yaitro already
handles a group once the trip exists (members with their own budget and food choice, Settle Up), but someone has to
collect everyone's budget, dates and veg/non-veg by hand first, and the destination is often not decided at all.
One link that collects all of it, and turns the answers into a plan, finishes that story. Every friend who opens it
also meets Yaitro, which is the growth loop.

## Decision

### 1. The organiser starts a group plan
- From the planner ("Plan with friends") or the landing page. The organiser (signed in) gives:
  - 1 to 4 **destination options** (or "Let the group suggest"); where from; a **date window** (for example any
    weekend in November) and the trip length; an optional suggested budget per person.
- They get an **invite link** and a ready WhatsApp message (`wa.me` text, Phase 3 Part A only: no WhatsApp Business API,
  I9). The link token is stored hashed, like share links (ADR 0006). It expires after 14 days or when the plan is made;
  at most 20 replies.

### 2. Friends reply, no account needed
- The link opens a light page, built for phones: name, **their budget** (optional), food (veg / non-veg / any), the
  **days in the window they can do** (tap dates on a small calendar), and a **vote** for one or more destinations.
- One reply per device (a signed cookie); they can edit it until the plan is made. Rate-limited per IP; the organiser
  can remove a reply.
- After replying they see the live result: votes, best dates, how many have replied. **Not** anyone's budget
  (question 2).

### 3. The organiser sees it come together
- Votes per destination; a date heat strip ("Fri 14 – Sun 16 works for 5 of 6"); food needs ("2 veg"); a budget line
  ("₹8,000 each fits everyone who gave a budget", question 1).
- **"Make the plan"** creates the trip with the winning destination, the best dates, the agreed budget, veg options at
  every meal if anyone is veg (already how the planner works), and every reply as a trip member with their own budget
  and food choice, so Settle Up is ready. It uses **one plan from the organiser's quota**, like any new trip.
- The group link then shows "Plan ready" and the trip's normal share link (budget hidden unless the organiser turns it
  on, I8).

### 4. Data
- New tables (one migration): `group_plans` (owner, origin, window, length, status, token hash, expiry, trip_id once
  made), `group_plan_options` (destination), `group_plan_replies` (name, budget, food, available dates, votes, device
  key hash, created/updated).
- Replies are personal data from people without accounts: deleted 30 days after the plan is made or the link expires,
  and with the trip if it is deleted. No phone numbers or emails are asked for.

### 5. Not now
- Friends suggesting new destinations (question 3).
- Friend accounts and merging a reply into a signed-up user later.
- Polls inside WhatsApp itself (would need the Business API, which we have ruled out).

## Consequences
- A group can go from "let's go" to a real plan from one link, and the trip starts with its members, budgets and food
  choices already filled in.
- New, anonymous write traffic: it needs the abuse limits above and the Phase 6 rules (ADR 0009) for new accounts.
- The organiser's plan quota is unchanged: collecting replies is free; making the plan uses one plan.

## Tests
- Token hashing, expiry, the 20-reply cap, one reply per device, edit until made, organiser removal, rate limits.
- Results: vote counts, date overlap (ties, nobody overlaps, partial replies), budget rule, veg if anyone is veg.
- "Make the plan": creates one trip with every member, uses exactly one plan, can't run twice (idempotent), and the
  group link stops taking replies.
- Privacy: the public results never contain a budget, a device key or anything the organiser didn't choose to show.

## Decisions (the owner asked me to decide, 2026-10-16)
1. **The plan uses the lowest budget anyone gave**, times the number of people (everyone who answered, plus the
   organiser if they didn't). With no budgets, the organiser's suggested budget per person.
2. **Only the organiser sees each budget.** Friends see how many gave a budget, never an amount: testing showed that
   even "fits everyone" is the lowest person's own budget, so it is not shown to friends.
3. **Friends vote on the organiser's options only** in v1.

## As built
- Migration `0018` (three tables); `services/group_service.py`; `api/routes/groups.py`.
- The link is the group id plus a keyed signature (not stored), so the organiser can always re-share it; replies are
  keyed by a random browser key, stored only as a keyed hash.
- Pages: `/together/new` (start), `/together/[id]` (the organiser's dashboard, refreshes every 20 s), `/g/[token]`
  (friends, no account). Entry points: My trips ("Plan with friends", open group plans) and the landing page's
  Group trips section.
- "Make the plan" creates the trip with every reply as a member (budgets and food), then opens it with `?plan=1`,
  which plans it the normal way (one plan). Retention: group plans are deleted 30 days after.
- Tests: `tests/test_groups.py` (12).
