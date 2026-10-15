# ADR 0019: Local public transport with lines and stops, and a "make it cheaper" that does something

Date: 2026-10-16. Status: accepted.

## Lines and stops for buses, metros and local trains

**Context.** Travellers on a small budget get around by bus, metro and local train. A plan that "got around by metro /
bus" showed only a flat estimate (₹25 a person a ride), with no line and no stop: the traveller still had to work out
which bus to take. Google Routes answers TRANSIT requests with the lines, the stops and often the fare. Until now we
used that only for intercity trains.

**Decision.**

- **When it's asked for.** On a plan whose local mode is `transit`, `apply_real_routes` asks Google Routes in TRANSIT
  mode for every hop that isn't a walk. That is a plan with metro or bus chosen and no cab, or a cheaper plan
  without cabs. The departure is 10:00 on that day, which is enough to read the timetable's lines.
- **What is kept.** `google.local_transit` turns the route into steps: vehicle (metro, bus, local train, tram,
  ferry), line, the stop to get on at and the stop to get off at, the number of stops, and the direction. The fare is
  kept when Google gives one.
  - A stop name in a script an English reader can't read is left out, as with intercity trains.
  - A route that is all walking gives no steps.
- **The leg.** It uses Google's door-to-door time plus 5 minutes to find the stop. The fare is Google's fare times
  the travellers, or the ₹25-a-person estimate when Google has none.
- **Fallback.** With no transit route, or the call failing, the previous estimate stands. Cab plans never make
  transit calls.
- **I6.** The steps are Google content. They are stored in the stop's `meta.transit` (and on "Back to the hotel"),
  listed in `GOOGLE_META_KEYS`, and gated and scrubbed after 30 days like the place details.
- **On the timeline.**
  - The leg before a stop lists each ride: "Bus AC 7 towards Amer, Sindhi Camp Bus Stand → Choti Chaupar · 6
    stops", with an icon for each vehicle.
  - A compact row has one line: "Bus AC 7 from Sindhi Camp Bus Stand, then Metro Pink Line".

**Consequences.**
- A transit plan makes one Google Routes call per hop, about 20-40 a plan. They are cached 12 hours
  (`google-transit`) and go through the gateway like every other call.
- Demo data has no timetable, so demo plans keep the estimate.

## "Make it cheaper" when the plan is already cheap

**Context.** On a plan already in the cheaper style, "make it cheaper" answered "It already leans cheaper, so there's
no more I can tighten there" and changed nothing. That happened even while local cabs were costing ₹13,000.

**Decision.** Asked again on such a plan, it takes the next real steps:
- **A hotel one level cheaper.** It goes no lower than an economy room for families, and only solo travellers and
  friends get a hostel. A hotel the traveller chose themselves is left alone.
- **Buses instead of cabs to get around.** The plan then gets the lines and stops above.

If neither is possible, it says what would help: a higher budget, fewer days, or skipping paid sights.

A 4-day Goa test plan in the cheaper style:
- the stay drops to a budget hotel;
- cabs go, and local travel comes to ₹700 for the trip;
- the total falls.

The day's budget line names the mode of its rides, counting the ride back at the end of the day as well. A day of
buses no longer says "Local cab".
