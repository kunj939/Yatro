# ADR 0014: Trips up to 30 nights, shown by week; prices in the home currency

Date: 2026-10-14. Status: accepted.

## Context

Trips were capped at 7 nights. People asked for longer trips. They also wanted to read a foreign plan (Delhi to
Dubai) in their own money. Planning a 12-day trip showed two engine problems:

- **Crash.** The scheduler added a stop, re-timed the day, and kept the stop even when re-timing failed (a sunset
  spot closed by then, or a meal window missed). The next step then read a missing time or leg and crashed
  (`'NoneType' has no attribute 'cost'`). The same crash also happened on some short trips with a large budget.
- **Empty days.** The sights were used up in the first few days, so most of the later days were empty and the
  quality gate refused the trip.

## Decision

- `MAX_DAYS = 30` nights (`schemas/trip.py`; the wizard checks the same limit).
- **The scheduler takes back stops that don't fit.** `DayScheduler._fit` adds a stop and re-times the day. If the
  stop doesn't fit, it removes it, re-times the day again, and the stop is not tried again that day. Every planned
  stop now keeps real times.
- **Spreading.** On trips longer than a week, the sights are spread over the days, at least two a day
  (`DayScheduler.spread`).
- **A bigger search.** For trips longer than a week, the place search adds "day trips and nearby getaways" and keeps
  up to 48 places instead of 32.
- **Free days.** On trips longer than a week, empty days become a Plan note ("N days are kept free to rest or explore
  on your own"). They are never a reason to refuse the trip.
- **By week.** The trip page shows trips longer than a week week by week: one overview card per week, filters for
  each week, and a "Week N" header with dates, stops and cost.
- **Home currency.** `trip_out` returns `home_currency` (the starting point's currency) and `home_rate`, a rough
  rate taken from the same `APPROX_INR_TO` table as the estimates. The trip header has a toggle between the two
  currencies. While the home currency is shown, `money()` converts every amount in the trip's currency, and the
  header says "≈ approximate". The plan is still costed in the destination's currency.

## Consequences

- Longer trips cost no more Google calls per plan: there is the same set of searches, plus at most one.
- With the home currency on, prices are approximate (a fixed table, not live rates). The header says so.
- With simulated data, restaurant variety is low, so long trips often miss a dinner. That comes from the simulated
  data; the scenario sweep's meal rules still pass.
