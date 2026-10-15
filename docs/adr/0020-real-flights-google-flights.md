# ADR 0020: Real flights from Google Flights (through SerpApi)

Date: 2026-10-16. Status: accepted.

**Context.** Every flight was an estimate. The fare came from the distance, the departure was placed where it suited
the trip, and there was no airline, no flight number and no layover. A Ranchi → London trip showed "Flight to London
(Economy, timing estimated)". Travellers want the real choice: IndiGo 6E 2134 to Delhi, three hours there, Air India
AI 111 to Heathrow, landing at 16:25 London time, then the hotel.

**Options.**
- **Amadeus Self-Service** was shut down on 17 July 2026.
- **Duffel** gives real airline offers and could book them, but needs an account verified for live use.
- **Google has no public flights API.** SerpApi's `google_flights` engine returns Google Flights results as JSON. The
  free plan has 250 searches a month.
- **Decision:** SerpApi now (the owner's choice), behind one module, so Duffel can replace it without touching the
  planner.

**Decision.**

- **Search** (`providers/google_flights.py`). When `SERPAPI_API_KEY` is set and flights are allowed:
  - one search per direction between the airports serving the two places, up to two each (IXR → LHR,LGW);
  - one-way, economy, one adult, so the price is per person;
  - the trip's currency, and the home country as the market;
  - cached 6 hours (`serpapi-flights`), so re-plans of a trip reuse it.
- **Results.** Up to 8 per direction become journeys. Each keeps:
  - the airline and flight number, from the first leg;
  - for a change, each flight as a leg, plus where the change is and how long;
  - Google's price, labelled "Google Flights price" and shown as live;
  - whether it is international, and an overnight arrival.

  Results with no price or more than two stops are left out.
- **Clocks.** Every time is the airport's own local clock, as on a ticket: Ranchi 06:10, London 16:25. The plan already
  reads this way: the start ("leave at 07:00") is the traveller's home clock, and days at the destination run on the
  destination's clock. The real elapsed time is `duration_minutes`, and the ticket on the timeline shows that, not
  the difference between the two clocks.
- **At the airport.** A scheduled flight needs the traveller there 2 hours before (3 hours abroad):
  `meta.check_in_minutes`. After landing it takes 30 minutes (1 hour abroad) to be on the way: `meta.exit_minutes`.
  - `feasibility.boarding_minutes` and `exit_minutes` replace the fixed 15 and 10 minutes wherever a departure or
    arrival is used: leaving home, the window at the destination, the cab to the airport, the metro check.
  - Trains and buses keep 15 and 10.
- **Fallbacks.**
  - No key, no airport we know at either end, or SerpApi failing: the estimated flight as before.
  - Google Flights has only one direction: the other keeps its estimate, so the trip still plans.
- **Display.**
  - Title: "Flight: IndiGo 6E 2134, then Air India AI 111 (Economy, change at Delhi…)".
  - Subtitle: the change and its length, both local times, and how early to be at the airport.
  - The ticket shows each flight in its airports' local times, under "Local time at each airport".
  - The share view and the PDF carry the local times too.
- **Gateway.** Provider `serpapi`, with a 15 s timeout and its cost counted.

**Consequences.**
- About two searches per planned trip with flights: around 125 such trips a month on the free plan, more with the
  cache.
- These are Google Flights results read by a third party. The price can change before booking, so the plan says
  "Google Flights price" and the traveller checks before buying.
- To book inside Yaitro later, Duffel is the path.
