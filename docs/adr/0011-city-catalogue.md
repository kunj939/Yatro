# ADR 0011 — City catalogue: fetch a city once, plan from the stored copy

Status: **Proposed** (2026-10-12). Step 0 below is built; the catalogue itself (phases 1–4) is not.

## Step 0, built 2026-10-12: Wikidata sights and the 7-day place cache

- `providers/wikidata.py`: one SPARQL query per destination (30 km), kept 30 days (CC0, ours to keep) and shared by every
  traveller. Fame comes from how many Wikipedias have a page (`wiki_links`), mapped onto the review scale
  (`fame_count`: 1 page ≈ 300 reviews, 85 ≈ 30,000). `providers/wiki_places.py` merges it with Google's results (one
  entry per place: Google's rating and hours, Wikidata's fame and tags) and passes it to the Google search as `known`,
  which then skips the interest searches Wikidata covers (forts, temples, museums, lakes…) and the "places near" search.
- Measured live: Bharatpur 21 sights (Fatehpur Sikri 85, Keoladeo 56, no neighbourhood parks), Manali 34 (Rohtang Pass,
  Solang Valley, Deo Tibba, Bhrigu Lake), Goa 40 (churches, forts, two beaches; Wikidata is thin on Goa's beaches, so the
  character search still asks Google for them). First call 3–13 s, then 2–3 ms from the cache. A failure or timeout
  returns nothing and the plan uses Google alone.
- Google place searches and details are now cached the full 7 days I6 allows (were 24 h and 3 days).
- OpenStreetMap (Overpass) was measured too and left out for now: 30–43 s per query on the public server, and nearly empty
  for small towns (13 places around Bharatpur).
- Switch: `WIKIDATA_ENABLED` (on).

## Problem

Every new plan searches Google again: "top tourist attractions in Goa", up to three interest searches ("beaches in
Goa", "forts in Goa"…), the character and nearby searches (added 2026-10-12), three or four restaurant
searches and a hotel search per tier, then routes for every leg. The response cache (ADR 0008) keys on the exact
request and keeps place searches for 24 hours, so a second traveller going to Goa tomorrow, or today with different
interests, pays again. A cold plan measured $0.35 (ADR 0001). Google's free monthly allowance at the tier these
searches fall into is small, so only a few plans a day stay free.

Most of that work is the same for everyone going to the same place: Goa's beaches, forts, restaurants and hotels do
not depend on who is asking.

## Decision (proposed)

Split "what is in this city" from "what this traveller should do", and fetch the first only once per city per month.

```
                       once per city, refreshed within 30 days
 Google (fixed searches) ─────────────────────────────────────► city catalogue (database)
                                                                  │  sights, restaurants, hotels,
                                                                  │  tags, character, fame
 traveller's request ─► engine ─ filters + ranks locally ◄────────┘  (no search per plan)
                          │
                          └─► routes: road estimate while choosing; real routes only for the chosen day's
                              legs, cached per place pair
```

### 1. The catalogue

One build per destination, from a **fixed** set of searches, so every traveller's plan for that city reads the same
rows:

| Search | Results kept |
|---|---|
| top tourist attractions | up to 20 |
| the character search (best beaches / mountain viewpoints / landmarks and museums / nature spots) | up to 20 |
| famous places near the city (small towns only) | up to 20 |
| one search per common interest that the city has (forts, temples, markets, lakes, museums, nightlife…) | up to 12 each, at most 4 |
| restaurants, pure-veg restaurants, cafés, famous local food | up to 20 each |
| hotels, one search per tier | up to 10 each |

About 12 to 15 searches per city, once. Interests are no longer separate searches at plan time: the catalogue
already holds the city's forts, lakes and markets, and the engine filters by tag.

Tables: `city_catalogue` (destination `place_id`, built_at, refreshed_at, character, search list used) and
`catalogue_places` (destination, Google `place_id`, category, the Google fields the engine needs, `fetched_at`).

### 2. Google's storage rules (I6 stays as it is)

- Google content (name, rating, hours, coordinates…) is kept **at most 30 days** (ADR 0005). The daily scrub clears
  it; a city whose content expires is rebuilt on its next request (or by the pre-warm in step 4).
- The `place_id` is kept indefinitely, as Google allows, so a rebuild can use cheaper Place Details calls for places
  already known instead of new searches.
- Our own data about a place is kept indefinitely: our visit length, our cost estimate, whether travellers removed
  it from plans, our planned times.
- The response-cache cap for Google (7 days) is unchanged; the catalogue is a separate store under the 30-day rule.

### 3. Fewer fields, cheaper tier

Catalogue searches ask only for what ranking needs (id, name, types, location, rating, review count). Opening hours,
price range, "serves breakfast" and the editorial summary move to a Place Details call for the **places that end up
in a plan**, about 10 to 15 per trip, cached in the catalogue for 30 days. Searches then fall into a cheaper Google
tier with a larger free allowance.

### 4. Pre-warm (optional, off by default)

A nightly job rebuilds, within a budget share, catalogues that are about to expire and were planned in the last 30
days (same pattern as the rail pre-warm, ADR 0008). Popular cities are then always warm and no traveller waits for a
build.

### 5. Free base layer (later, separate decision)

OpenStreetMap (beaches, viewpoints, peaks, forts, museums; licence allows permanent storage with attribution) and
Wikidata (how notable a place is) could seed a catalogue with no Google cost at all, with Google only filling in
ratings and hours for the chosen places. Bigger change; its own ADR if wanted.

## Expected effect (to be measured, ADR 0001 method)

| | Today | With the catalogue |
|---|---|---|
| First plan for a city | about $0.35–0.40 | about $0.40–0.50 (the build) |
| Every later plan for that city within 30 days | about $0.25–0.35 | about $0.03–0.08 (details for new places + routes) |
| 25 plans a day across 5 popular cities | about $8 | about $3 |

The daily plan cap (25) and the Google budget ($10) could then go up together without new spend.

## Phases

1. Catalogue tables, build from the fixed searches, engine reads the catalogue (interests filtered locally), scrub
   and rebuild at 30 days. Tests: two plans for one city make the searches once; an expired city rebuilds.
2. Slim search fields + Place Details for the chosen places. Measure the cost per plan before and after.
3. Route cache per place pair and real routes only for the final day order.
4. Pre-warm job (off by default), admin view of catalogues (city, places, age, last build cost), "rebuild now".

## Risks

- A catalogue built once can miss a place a traveller names: a named place ("add Fatehpur Sikri") still triggers a
  single lookup, added to the catalogue.
- Hours and closures can change inside 30 days: the chosen places' details are refreshed when a plan uses them if
  they are older than 7 days.
- Google can change prices or tiers: the measurement in each phase is what decides the caps, not this table.
