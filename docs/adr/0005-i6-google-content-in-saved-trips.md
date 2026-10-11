# ADR 0005 — I6: Google content in saved trips, share pages and offline copies

Status: **Accepted and implemented** (user decision 2026-10-06, with the exceptions under "Not done"). The terms questions at the end
are still yours to confirm: this is my reading, not a legal conclusion. Date: 2026-10-06.

## Decision so far (the user's rule, 2026-10-06)

I6 applies to saved itineraries. Our data is ours; Google's content is not.

| Ours: keep for as long as the trip exists | Google's: refreshable, never shown once expired |
|---|---|
| stop order, our planned times, our cost estimates, budget, user notes, and the Google `place_id` of each stop | place name, address, rating, opening hours, photos, coordinates |

- **Coordinates** are never older than 30 days.
- **Other details:** when a trip is opened and its snapshot is more than 30 days old, they are re-fetched from the
  `place_id`, using the cheapest field mask that works.
- **If the re-fetch fails or the budget is spent,** the stop is shown as "Details unavailable — open in Google Maps", with
  a link built from the `place_id`. Expired Google content is never shown as current.
- **Share pages and any offline/PWA copy** follow the same rule. They render refreshed data from within the 30-day
  window, never an old snapshot.

## What a saved trip holds today

Phase 1 (ADR 0002) scrubs the `places` table after 30 days. Saved trips, though, **copy Google content into their own
rows** when planned, and nothing expires those copies:

| Row | Google content it copies |
|---|---|
| `itinerary_items` | `title` (place name); `meta.lat/lng/address/rating/rating_count/tags`; `opening_time`, `closing_time`, `hours_state`, `hours_note`; `polyline` (Routes API); `reasons` (e.g. "rated 4.6 by 12,000 visitors"); `explanation` (AI narration, which names places) |
| `alternatives` | `meta.lat/lng/rating` |
| `transport_options` and transport items | Google transit results: line names, times, stops, coordinates, polylines |
| `trips` | `origin_lat/lng`, `destination_lat/lng` (geocoded) |
| `places` | already scrubbed at 30 days, keeping `provider_place_id` |

No photos are fetched today (none are in `PLACE_FIELDS`). There is no PWA or offline copy yet.

## Proposal

### 1. Time-stamp each item's Google content

- A new column `itinerary_items.google_fetched_at` (migration 0006, nullable; backfilled from the plan's creation time).
- The same column on `alternatives` and `transport_options`.
- Google fields stay where they are, so planning code is unchanged (I10). The timestamp says how old they are.

### 2. A serializer gate: `google_gate` in `trip_service.item_out`, after the I1 gates

For an item whose Google content is more than 30 days old and wasn't refreshed:

| | Fields |
|---|---|
| **Kept (ours)** | kind, day, `start_at`/`end_at` (our planned times), cost and its fare label, `place_id`, position, user notes |
| **Replaced** | title → "Details unavailable — open in Google Maps"; `meta.maps_url` → `https://www.google.com/maps/search/?api=1&query=<trip destination as typed>&query_place_id=<place_id>` |
| **Removed** | coordinates, address, rating and rating reasons, opening hours and hours note, polyline, AI explanation, and the coordinates and rating on alternatives |
| **Transit legs** | Google line names and times become "Transit leg (details expired)", with our cost kept. |

The frontend already copes with an item that has no coordinates (the map skips it).

### 3. Refresh when a trip is opened

On `GET /trips/{id}` and on the share page:
- **Collect.** Gather the distinct `place_id`s whose content is more than 30 days old.
- **Fetch.** Get Place Details in parallel with the field mask below, within a **3-second** budget.
- **Write back.** Update the item snapshot and `google_fetched_at`. Whatever didn't refresh in time goes through the
  gate as "unavailable".

**Field mask:** `id,displayName,formattedAddress,location,regularOpeningHours,businessStatus,rating,userRatingCount`.
- I believe this is billed as Place Details **Enterprise**, about $0.020 per stop, because of `regularOpeningHours` and
  `rating`. Rating is included because it doesn't raise the SKU.
- Dropping hours and rating would make it **Pro**, about $0.017. That small saving isn't worth losing the hours check.
- Today's planning mask also bills the **Atmosphere** fields (`servesVegetarianFood`, `editorialSummary`). The refresh
  doesn't need them.

**Expected cost and caps:**
- A 10-stop trip reopened after 30 days costs about $0.20.
- Refreshes are bounded by a service-wide daily cap, `GOOGLE_REFRESH_DAILY_CAP` (default 200 stops/day, about $4/day;
  0 turns it off), and a per-trip cap of 20 stops.
- Phase 4 replaces these with real budgets (I5).

**Not refreshed:**
- Polylines aren't refreshed. They come back only on a replan, which recomputes routes; until then the map joins the
  refreshed stop coordinates with straight lines.
- Google transit legs aren't refreshed. A transit timetable from more than 30 days ago is meaningless for a past date,
  and for a future date the traveller replans.
- Trips whose end date has passed aren't refreshed automatically. Only our data is shown, with "Details unavailable"
  stops. One "Refresh details" button re-fetches them, still within the caps.

### 4. Retention at rest: extend the Phase 1 scrub

Every 24 hours, Google fields more than 30 days old are cleared from the stored rows themselves, not just hidden by the
gate:
- `itinerary_items`, `alternatives`, `transport_options`;
- the trip's geocoded coordinates.

The database then never holds expired content, and a refresh fills it back in. `place_id` and our fields are never
touched. This uses the existing `RETENTION_ENABLED` flag.

### 5. Share pages and offline copies

- **Share pages (Phase 3)** use the same `item_out` → gate path, so they can't serve an old snapshot. The response says
  `google_valid_until` (the earliest expiry among its stops).
- **Offline copies.** None today. When a PWA is built, its service worker must evict a saved trip at
  `google_valid_until` and refetch it, never serve it after that time. This is written here so that phase inherits it.

### Flags

| Flag | Default | Controls |
|---|---|---|
| `GOOGLE_REFRESH_DAILY_CAP` | 200 | daily refresh cap (0 = off, so expired stops show as unavailable) |
| `RETENTION_ENABLED` | existing | the scrub at rest |

The gate itself has **no off switch**: it is an invariant.

### Files

| File | Change |
|---|---|
| `models/trip.py`, migration 0006 | the `google_fetched_at` columns and backfill |
| `services/google_content.py` (new) | `google_gate`, `refresh_trip_places`, and the maps link |
| `trip_service.item_out` and `alt_out`; trip GET route | call the gate; refresh on open |
| `providers/google.py` | `place_details(place_id, mask)` with the refresh mask |
| `services/retention.py` | scrub the item, alternative, option and trip fields |
| frontend `TimelineItem`, `MapPanel` | the "Details unavailable — open in Google Maps" stop; the map skips stops without coordinates |

### Tests

- content at 29 days is shown and at 31 days is gated;
- after a successful refresh, the stop is shown with fresh content and a new timestamp;
- a failed refresh, the daily cap or the per-trip cap gives the "unavailable" stop with a working maps link;
- our fields are unchanged by the gate and by the scrub;
- the scrub clears every Google field at rest and keeps `place_id`;
- a share page never shows content more than 30 days old;
- the refresh never runs past its caps;
- `google_valid_until` is correct.

## Questions on the Google Maps Platform terms

**These are my readings, not legal conclusions. Please confirm each one.**

1. **Caching of names, addresses, ratings and hours.**
   - My reading of the Places API policies: `place_id` may be stored indefinitely and latitude/longitude for up to 30
     days; other Places content may **not be pre-fetched, cached or stored** except as the terms allow.
   - If "as the terms allow" means no caching at all, even 30 days is too long. Each trip view would then need a
     re-fetch, at about $0.20 per view of a 10-stop trip.
   - This proposal assumes a 30-day cache is acceptable for all fields, which is your rule. Is that right?
2. **When the 30 days start.** I assume the clock starts at fetch time and a re-fetch restarts it.
3. **Routes API content.**
   - Polylines, durations and distances from the Routes API: are they under the same 30-day rule?
   - Our **planned times are computed from Google travel durations.** Are they "ours", as your rule says, or derived
     Google content?
4. **Google content without a Google map.** The terms restrict showing Google content with a non-Google map, and
   require attribution where there's no map:
   - **Map sketch.** Without a browser key, `MapPanel` draws stops on **our own SVG sketch from Google coordinates**.
     I think that needs to change: either use the Google map, or no coordinates-based map at all when the key is absent.
   - **Attribution.** The timeline, share page and WhatsApp text show place names with no Google attribution. Do they
     need a "Google Maps" attribution?
5. **WhatsApp text (Phase 3).** Shared text containing place names leaves our control and lives in chats forever. Is that
   allowed? The alternative is WhatsApp text with only our data (times, costs, stop numbers) plus the link, with place
   names shown on the share page only. **This decides the Phase 3 text format.**
6. **Sending Google content to an LLM.** Narration sends place names and ratings to a third-party LLM (OpenRouter).
   Newer terms restrict using Google content with machine-learning models. Is narration allowed?
7. **Cost estimates from Google price data.** Restaurant cost estimates come from Google `priceLevel`/`priceRange`. Are
   they "our cost estimates", as your rule says, or derived Google content?
8. **Maps link.** The link needs a `query` parameter alongside `query_place_id`. I plan to use the trip's destination as
   the user typed it, which is our data. OK?
9. **Pricing.** The field-mask SKU tiers (Essentials / Pro / Enterprise / Atmosphere), their prices, and any monthly
   free usage per SKU need checking against your billing account. The figures above are list-price estimates.
10. **Region.** I assume the Google Maps Platform EEA terms don't apply (the billing account is in India). Confirm?

## Decisions (2026-10-06)

- I6 applies to saved itineraries, share pages and shared text, as proposed.
- Place names **may** go into WhatsApp text, generated at the moment of sharing from data refreshed within the 30 days. Yatro
  does not store the message (answers question 5).
- Attribution is required when Places content is shown without a Google map (see "Attribution" below).

## Results

**What was built:**
- **Stamp.** `itinerary_items.google_fetched_at` and `alternatives.google_fetched_at` (migration 0006).
  - The planner stamps every non-transport item of a plan that used Google places.
  - Existing rows were backfilled from their creation time, including "Free time near …" rows.
  - Demo and sample data stay NULL and are never gated.
- **Gate.** `google_content.gate_item` and `gate_alternative` run in `trip_service.item_out` and `alt_out`, after the I1 gates.
  - A stop older than 30 days shows "Details unavailable — open in Google Maps", with a Maps link built from its `place_id` and the
    destination as typed.
  - Stays and free time keep our words ("Check in: your stay", "Free time").
  - Our own fields are untouched: times, cost, `place_id`, position, day.
  - Removed: coordinates, address, rating and its reason, opening hours, route line, explanation and sources.
  - The plan summary and tips go too (`summary_expired`), because they were written from that content.
  - The alternatives endpoint also returned the item's raw title; it now goes through the same gate. A test caught that.
- **Refresh.** `refresh_trip` runs when a trip is opened (owner view, share page, share creation).
  - It re-fetches stale places in parallel by `place_id` with the field mask `id,displayName,formattedAddress,location,regularOpeningHours,businessStatus,rating,userRatingCount`.
  - The stale content is cleared first, so nothing old survives next to the new.
  - Caps: 20 places per trip, `GOOGLE_REFRESH_DAILY_CAP` (default 200, 0 = off) per day, 3 seconds of waiting.
  - The spend is recorded before the calls go out. A failed or slow call leaves the stop "unavailable", never stale.
- **At rest.** The 24-hourly retention run clears the same fields from stored rows older than 30 days, keeping `place_id` and our fields.
  It is idempotent, and opening the trip fills a scrubbed stop back in.

**Tests** (`tests/test_google_content.py`, 19): content shown at 29 days and gated at 31; our fields kept; stays and free time;
narration; alternatives; refresh from the place id; a failed refresh; per-trip and daily caps; cap 0; a slow refresh; the scrub;
fresh content never scrubbed; share page and text refresh first and never show expired content; `valid_until`; demo data never
gated; and migrations 0006/0007 (upgrade, backfill, downgrade).

## Attribution (checked against Google's Places API Policies page, 2026-10-06)

The page says: "When displaying Places API data without a Google Map, you must include the Google logo, adhering to the provided
style guidelines." It also says: "Attribution should take the form of the Google Maps logo whenever possible. In cases where space
is limited, the text **Google Maps** is acceptable", and that the text must not change capitalisation or wrap across lines.

- **Done.** The share page footer and the WhatsApp text carry "Place details from Google Maps", and only when the plan has Google
  content. On the page, "Google Maps" is a non-wrapping link.
- **Not done.** The Google logo. I did not add the logo file.

## Not done

- **Transit and transport.** Google transit legs and other Routes content on transport items and the `transport_options` table are
  not stamped, gated or scrubbed.
- **Trip coordinates.** `trips.origin_*` and `trips.destination_*` coordinates (geocoded) are not scrubbed. The owner's view still returns them.
- **After a refresh:**
  - Activity subtitles (Google's editorial summary) do not come back, because that field is outside the cheap mask.
  - The explanation and the summary are not regenerated.
- **Own-view map and attribution.** The owner's trip page still draws the route sketch from Google coordinates when there is no
  Google key, and still shows no attribution. Both are open under question 4.
- **Offline copy.** There is none yet. When a PWA exists, its service worker must evict a trip at `google_valid_until`.

## Terms: what I could and could not confirm

Confirmed from the Places API Policies page: the attribution rule above, and that the place ID "is exempt from the caching
restrictions", while other content must not be "pre-fetched, cached, or stored beyond the allowed exceptions".
**Not confirmed:** the Service Specific Terms page was too long for my fetch, so I could not read the 30-day latitude/longitude
rule, the machine-learning rule or the non-Google-map rule myself. Please check these against the full terms:
- (a) the 30-day figure and which fields it covers;
- (b) whether the 30-day clock restarts on re-fetch;
- (c) whether the logo is required in a share page footer, or the text is enough (the text is only for "limited space");
- (d) whether text in a WhatsApp message, which the recipient keeps forever, is an allowed use;
- (e) whether our planned times, derived from Google travel durations, count as "ours";
- (f) whether sending place names and ratings to a third-party AI to write the narration is allowed;
- (g) whether showing Google content to signed-out people on a share page is allowed;
- (h) the Place Details SKU and price for the refresh mask on your billing account.
