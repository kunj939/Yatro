# ADR 0013 — Plans: free every month, Plus monthly or yearly, a trip pass

Status: **Accepted**, built 2026-10-14.

## Decision

| Plan | Price | What | Pattern it follows |
|---|---|---|---|
| Free | ₹0 | **1 new trip plan every month** (was 7 for life), unlimited changes, PDF and WhatsApp sharing | Spotify / Duolingo: a free tier that renews brings people back |
| Plus monthly | ₹99/month | Unlimited plans, priority when busy (the paid queue lane, ADR 0008), group split | |
| **Plus yearly** | **₹799/year** (about ₹67 a month, 33% off) | Same as Plus, highlighted as best value | Spotify / YouTube Premium: the year is the better deal |
| Trip pass | ₹49 | One more trip plan | Pay-per-use for people who plan one trip a year |

Invite-a-friend (ADR 0012) stays: free plans for both sides and 50% off the friend's first month of Plus.

## How

- Free plans per month: `users.free_period` ("2026-10") records which month `free_plans_used` counts; the first quota
  check or plan in a new month starts the count again (`quota_service.roll_period`). Migration 0016, expand-only.
  `FREE_TRIP_PLANS` default 1 (chosen 2026-10-14; first proposed as 3), still a live admin number ("Free plans per month").
- Plus yearly: product `plus_yearly`, 365 days; `PLUS_YEARLY_PRICE_INR` (799). The quota response carries
  `prices.plus_yearly_inr` and `free_plans_reset` (the 1st of next month).
- Site: Pricing has a Monthly / Yearly switch (yearly selected); Account offers yearly first; copy says
  "1 free plan a month". Prices on the site come from `lib/brand.ts`; checkout uses the backend settings.

Tests: `tests/test_plans_monthly.py`.
