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

## Addendum: the scheduler plans at the same fares

**What went wrong.** The first version priced the city and the rush only when the budget was drawn up, after the
days had been built. The scheduler still chose stops at big-city fares. On a Goa trip whose hotel was 35 km from the
sights, the rides came out about ₹10,000 dearer than it had allowed:
- a plan with pinned choices went over budget (₹49,126 to ₹59,190, after "make it cheaper");
- a plan without pins would have been refused instead.

**Fix.** `Travel` now prices every cab leg it plans with at the city's rate times a rush allowance of 1.2
(`planning_cab_factor`; about two rides in five fall in the rush). This covers estimated legs, measured routes, the
reserve kept for later days, and the typical short ride. The cheapest-workable estimate uses the same factor.
`price_rides` then sets each ride's exact price for its hour, and the day still fits.

The test is a 4-day Goa trip. It must plan, and stay within budget, at ₹30,000 and ₹45,000. Without this fix it was
refused at ₹30,000 for being ₹1,123 over.

**Also.**
- **Made-up budget changes.** A live model read "make it cheaper" as a budget cut to nothing. A model's budget
  change is now dropped when the message names no amount.
- **Chat reply.** When a change leaves a plan over budget because of pinned choices, the chat says so with the
  amount, instead of only "Done".

## Addendum: no surge on fixed-rate taxis, and hotels where the sights are

**What was seen.** A Goa plan had ₹16,018 of local travel for 24 rides, about ₹667 a ride. Two things stacked:
- The ride price multiplied Goa's taxi factor (1.75) by the rush surge (1.5), which comes to about ₹31 a km. App cabs
  surge, but Goa's union taxis and hill-station taxis charge fixed rates.
- The hotel was about 35 km from the day's sights, so each day had a long ride out and a long ride back. Hotels were
  looked for around the average of the top sights. With sights in North and South Goa, that average falls inland,
  far from both.

**Decision.**
- **Surge.** The rush surge applies only where app cabs run (city factor 1). Fixed-rate places pay their city
  factor, plus night charges. The scheduler's allowance matches: 1.2 for app-cab cities, none elsewhere.
- **Goa's factor** goes from 1.75 to 1.5, about ₹18 a km, close to the GoaMiles app.
- **Where hotels are looked for** (`planning_support.sights_hub`): the sight with the least total distance to the
  other top 8 (a medoid), instead of their average. It sits in the cluster where most sights are, and hotels are
  still kept within 6 km of it when there are any.

**Consequences.**
- A Goa ride of 10 km costs ₹255 at any hour (₹50 + 10 km × ₹12, × 1.5).
- New Goa plans stay in one cluster instead of commuting between both. A hotel the traveller pinned is kept where
  it is, and its distance is still paid for in rides.
