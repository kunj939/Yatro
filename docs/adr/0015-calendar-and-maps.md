# ADR 0015: The plan in the traveller's calendar and maps

Date: 2026-10-14. Status: accepted.

## Context

Once the trip is planned, people want it where they will look during the trip: their calendar and Google Maps.

- **Calendar.** Writing to Google Calendar through its API needs OAuth with a sensitive scope and Google's app
  verification, which takes weeks, and users would see a permission screen. A calendar *subscription* (iCalendar over
  HTTPS) works in Google, Apple and Outlook calendars with no sign-in.
- **Maps.** Google offers no API to add places to someone's Google Maps saved lists. What it does support: Maps URLs
  for directions, including multi-stop routes, and KML import in Google My Maps.

## Decision

- **Calendar feed.** `POST /api/trips/{id}/calendar` gives the trip a secret `calendar_token` (migration 0017) and
  returns subscribe links for Google (`calendar.google.com/calendar/render?cid=webcal://…`), Apple (`webcal://`) and
  Outlook.
  - `GET /api/cal/{token}.ics` serves the current plan with no sign-in and is rate-limited like share links.
  - `DELETE` turns the feed off; the old URL then answers 404.
  - `GET /api/trips/{id}/calendar.ics` is a one-off download for the owner.
- **Events.** Events are built from the same gated item output as the trip page, so I1 (train facts) and I6 (Google
  content) hold.
  - A train without a timetable becomes an all-day "timing estimated" event, never a made-up time.
  - Times are local wall-clock times, converted to UTC with the time zone of the item's own place. A train departs in
    its origin's time zone.
  - Each event has a location (name and address), a GEO point, a Directions link and a link to the full plan.
  - Reminders: an hour before a train, bus or flight, and half an hour before check-in.
  - Free-time blocks are left out.
- **Live updates.** The feed asks for an hourly refresh (`REFRESH-INTERVAL`). Apple Calendar mostly follows that.
  Google Calendar refreshes subscribed calendars on its own schedule (often several hours apart). A changed plan
  reaches the calendar without the user doing anything, but not instantly.
- **Maps.**
  - Each day header has "Day route in Maps": a Google Maps directions URL through that day's places in order, capped
    at 11 places by the URL's 9-waypoint limit.
  - Each stop's map button gives directions to its coordinates, instead of a name search that can land on another
    branch.
  - "Save to Google My Maps" downloads a KML file (a folder and colour per day, numbered pins, a line per day) and
    opens My Maps for the import.

## Consequences

- Anyone with the feed URL can read the plan, the same as a share link. "Stop syncing" revokes it.
- No new Google API calls and no new cost. The feed is generated on request from the stored plan.
