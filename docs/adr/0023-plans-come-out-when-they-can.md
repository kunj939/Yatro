# ADR 0023: A plan comes out whenever it reasonably can

Date: 2026-10-16. Status: accepted.

**Context.** A Jaipur → Rishikesh, 3-day plan for 3 kept showing "We're a little busy right now" (reopens 10:33, then
10:40). The Google key was fine (a place search answered at once). Three things in the design turned a slow plan on
Render's free instance into a "busy" screen for everyone:

1. Finding sights made 5–8 Google searches **one after another**, and restaurants 3 more. **Any one** failing raised,
   and the whole plan became "unavailable", although the other searches had found plenty.
2. Late in a slow plan each call only gets what is left of the 60 s plan deadline. Those calls timed out and were
   **counted against Google** by the circuit breaker (5 in a row, or half of the last 10, opens it).
3. With the Google Places circuit open, **every** new plan was turned away until it closed, and the next slow plan
   opened it again.

## Decision
- **Searches run together and fail alone** (`GooglePlacesProvider._searches`): up to 4 at once, in the order asked, so
  results stay deterministic. A failed search gives no rows; only when every search fails is the error raised.
- **Wikidata fallback** (`WikiAwarePlaces`): when Google's sights can't be had and Wikidata knows at least 4 famous
  sights there, the plan is made from those.
- **Restaurants failing** no longer stops a plan: meal stops are left to the traveller, with a note, and the food
  budget is kept aside.
- **Our own deadline is not the provider's fault** (`gateway/core.py`): a timeout on a call whose time was cut to the
  plan's remaining time is neutral for the circuit breaker. Timeouts at the provider's own limit still count.
- **An open Google circuit no longer turns new plans away** (`capacity.check_new_plan`). The plan runs, skips what it
  can't reach and falls back as above. New plans are still stopped by the money caps (daily plan cap, Google budget)
  and an admin pause, and when nothing at all can be found (Google down and no Wikidata sights) the honest "busy"
  answer remains, with no free plan used.

## Consequences
- Far fewer "busy" screens; a plan made in a bad moment may have fewer sights or no restaurant picks, and says so.
- Faster plans: the sight searches take about as long as the slowest one instead of the sum of all of them.
- Tests: `tests/test_plan_resilience.py`.
