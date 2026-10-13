# ADR 0012 — Invite a friend

Status: **Accepted**, built 2026-10-13.

## Decision

A two-sided referral programme on the pattern the large consumer apps converged on:

| Pattern | Where it comes from | Here |
|---|---|---|
| Both sides are rewarded | Dropbox (space for both), Uber and Airbnb (credit for both) | The friend gets free trip plans; so does the inviter |
| The newcomer's reward is immediate | Airbnb / Uber first-booking credit | The friend's plans are added at sign-up |
| The inviter's reward waits for real use | Uber pays after the friend's first ride | The inviter is paid when the friend's **first plan is ready** |
| A cap per inviter | every programme that was farmed | At most N rewards per inviter per month; friends can still join |
| A first-purchase discount | Swiggy / Zomato first-order offers | The friend's first month of Plus is discounted once |

Rewards are trip credits (`subscriptions.trip_credits`), the same thing a "one trip" purchase gives, so the quota rules
are unchanged. A wrong, paused or own code is ignored: sign-up never fails because of a referral code. One inviter per
account, enforced by a unique key on `referrals.friend_id`.

## Controls (all live, /admin -> Controls)

- Switch `referrals`: off stops new rewards; credits already given stay.
- `referral_reward_friend` (default 2), `referral_reward_inviter` (2), `referral_plus_discount_pct` (50),
  `referral_monthly_cap` (10; 0 = no cap). Each is also an environment setting of the same name.
- /admin -> Referrals lists who invited whom, what each got and the status (joined / rewarded / capped).

## Data

Migration 0015 (expand-only): `users.referral_code` (unique, made on first use: first letters of the name + four
characters without 0/O/1/I) and the `referrals` table (inviter, friend, code, status, credits each side, discount and
when it was used, when the inviter was rewarded).

## Where

`services/referral_service.py` (rules), `api/routes/auth.py` (sign-up applies the code), `services/trip_service.py`
(first plan rewards the inviter), `services/quota_service.py` (discounted first month in `prices.plus_first_month_inr`,
used on upgrade), `GET /api/subscription/referrals`, `GET /api/admin/referrals`. Frontend: the invite field at sign-up
(prefilled from `?ref=`), the Invite friends card on Account, the Referrals admin page. Tests: `tests/test_referrals.py`.
