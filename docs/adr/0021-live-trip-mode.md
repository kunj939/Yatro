# ADR 0021: Live trip mode

Date: 2026-10-16. Status: accepted and built (2026-10-16).

**Context.** Yaitro stops helping at the moment travellers need it most: once the trip starts. On the day, people want
to know what is next, when to leave, and what to do when the train is two hours late. Today the plan is a static list,
and the only way to change it is a full re-plan, which costs provider calls and one of the trip's re-plans.

**What we have to work with.**
- Every stop already carries what a live view needs: `start_at` / `end_at`, `travel_minutes_from_prev`,
  `opening_time` / `closing_time`, a priority, coordinates and a maps link.
- Trains and flights are fixed-time tickets; everything else at the destination can move.
- RailRadar is the only rail source we may store (ADR 0003). Its free tier is about 1,000 calls a month, which
  already goes on timetables. I found no public RailRadar live-status API: **open question 1.**
- There is no push notification channel yet (no service worker). The site has a web manifest, so it can be installed.

## Decision

### 1. The Today view (frontend)
- On the trip's dates, by the destination's clock (`trip.timezone`), the trip page opens on a **Today** card above
  the tabs. Outside those dates nothing changes.
- **Now / Next:** the current stop with a progress bar, and the next stop with a countdown and **"Leave by 14:05"**.
  Leave-by = next `start_at` − `travel_minutes_from_prev` − 5 minutes slack (15 for a train, `check_in_minutes` for a
  flight).
- **Heads-ups**, computed from the plan only: "City Palace closes at 17:30; you'll reach at 17:10". "Last metro
  23:00" when the plan already knows it.
- **Done:** tap a stop to tick it off. Stored on the device (localStorage, per trip): a convenience, not data we keep.
- One tap: **Directions** (Google Maps link, as now) and **Call / book** links where the plan has them.
- Optional **keep screen on** (Wake Lock API) and, while the page is open, a browser notification 10 minutes
  before leave-by (Notification API, asked for only when the person taps "Remind me").

### 2. "Running late" re-flow (backend, no provider calls)
- `POST /api/trips/{id}/live/late {minutes, from_item_id?}` with buttons **+15 min / +30 / +1 h / custom**. The trip
  chat also understands "train 2 hours late" / "45 min late ho gaye".
- A deterministic re-flow of **the rest of today only** (`services/live_reflow.py`):
  1. shift every movable stop after `from_item_id` (default: the current stop) by the delay;
  2. a stop that would now be closed, or past the day's end time, is dropped, lowest priority first, never a meal
     unless the day's end is reached;
  3. tickets (trains, flights, checked-in hotel) never move. If the delay makes a ticket impossible to reach, the
     answer says so plainly ("You'll miss the 18:40 Shatabdi") and offers a full re-plan instead of guessing.
- No Google, rail or flight calls, so it doesn't touch the daily caps, and **it doesn't use a re-plan** (open
  question 2). It is saved as a new itinerary version with the note "Running 45 min late", so **Undo** is just the
  previous version.
- Dropped stops are listed with an **"Add back"** that swaps out the next-lowest-priority stop instead.

### 3. Live train status (only if question 1 allows it)
- An on-demand **"Check my train"** button on the day's train, never polling.
- Cached 5 minutes per train and date; capped at 6 checks per trip per day and a global daily cap through the gateway
  (ADR 0007), so it can never eat the timetable budget.
- Shown as its own fact with its source and time ("Running 1 h 10 m late · RailRadar, 10:42"). The timetable itself is
  unchanged (I1): a live delay never rewrites the scheduled times; it only offers "Re-flow my day for this delay".
- Without a live source, the button is a link to NTES ("Check on NTES") and the "Running late" buttons do the rest.

### 4. Not now
- Web Push (works on iPhone only for an installed app; needs a service worker and keys): its own ADR later.
- Sharing a live "where we are now" with family: question 3.
- Automatic location tracking: not wanted; the traveller says where they are by ticking stops off.

## Consequences
- The plan is useful on the trip, not just before it, with no extra provider cost for the common case (a delay).
- Undo works through the version history that already exists.
- Without a live rail source, train delays depend on the traveller telling us. That is honest and still covers the
  most common problem.

## Tests
- `live_reflow`: shift only after the given stop; drop closed or overflowing stops by priority; meals kept; tickets
  never move; a missed ticket returns the "you'll miss" answer and changes nothing; the day-end limit; timezones.
- API: ownership, read-only links refused, a version is written, no provider is called (sim providers assert zero calls),
  re-plans left unchanged.
- Frontend: the leave-by sums, the Today card appears only on trip dates in the destination's timezone.

## Decisions (the owner asked me to decide, 2026-10-16)
1. **No live train status calls in v1.** RailRadar's live endpoint and quota are unconfirmed; v1 ships "Running late"
   and a "Live status on NTES" link for the day's train. Section 3 stays the plan for when a live source is confirmed.
2. **"Running late" uses no re-plan.** `replan_count` is untouched (tested).
3. **No family live view in v1.**

## As built
- `services/live_reflow.py`, `POST /api/trips/{id}/live/late` and `/live/undo` (rate-limited 20/min). One extra rule
  found while testing on a real plan: **a sunset is dropped rather than pushed more than 20 minutes late**.
- `components/trip/TodayCard.tsx` on the trip page during the trip's dates (destination clock; `?now=YYYY-MM-DDTHH:MM`
  previews it), plus a "starts in N days" line in the week before.
- Tests: `tests/test_live_reflow.py` (12), including the API, ownership and undo.
