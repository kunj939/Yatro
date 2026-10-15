# ADR 0018: Local cab fares like app cabs, by the hour and the city

Date: 2026-10-15. Status: accepted.

**Context.**
- Local rides were priced at ₹60 + ₹17 a km at every hour and in every city. App cabs (Uber Go, Ola Mini, Rapido
  Cab) cost about ₹10-12 a km off-peak, surge in the office rush, and add night charges.
- Some places have no app cabs at all: Goa's taxis are union-run, and hill-station taxis cost more.
- A Goa trip showed ₹3,000+ of cab a day, with the same rate morning and night.

**Decision.**

- **Base fare** (`fare_model.local_leg_cost`): ₹50 + ₹12 a km per car, at least ₹80. Six people need two cars, as
  before.
- **Time of day** (`ride_factor`), from when the ride leaves:

  | When the ride leaves | Factor |
  |---|---|
  | 08:00-11:00 and 17:00-21:00 (rush) | 1.5 |
  | 23:00-06:00 (night charges) | 1.25 |
  | otherwise | 1.0 |
- **The city** (`city_cab_factor`), matched on the destination's name:

  | Destination | Factor | About |
  |---|---|---|
  | Goa and its towns | 1.75 | ₹21 a km |
  | Hill stations (Manali, Shimla, Darjeeling, Ooty, Munnar, Leh...) | 1.4 | |
  | everywhere else | 1.0 | |
- **Where it applies.** `price_rides` runs as each plan's budget is drawn up and prices every cab leg of every day
  from its distance, time and place. It recomputes from the distance each time, so running it twice never
  compounds. The legs shown on the timeline and the "Local cab, N rides (day N)" budget lines are the same numbers.
- **Cheapest workable cost.** The cheapest-workable estimate (used when a budget is too small) uses the city factor
  too, so Goa's floor isn't quoted at big-city rates.
- **Rejected: a cab for the day** (8 h / 80 km, about ₹2,200). At app-cab rates that works out to about ₹27 a km,
  so it is cheaper than separate rides only when the car waits for hours, and waiting time isn't modelled. Offering
  it would have meant showing a saving that the numbers don't support.
- **Display fix.** The budget's "more than usual" amount is rounded ("₹11,425", not "₹11,425.18").

**Consequences.**
- A city sightseeing day costs less than before, but more in the rush hours than at midday.
- Goa and hill stations cost more than big cities, as they do in practice.
- Every local fare is still labelled Estimated.
