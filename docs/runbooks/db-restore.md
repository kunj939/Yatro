# Runbook: restore the database

Written to work for either backup choice. Phase 9 decides where we host; many managed PostgreSQL hosts include point-in-time recovery.
Until then the backup is a daily `pg_dump` kept 14 days (`docker compose --profile backup up -d backup`, files in the `pgbackups` volume).

## When to use this

- The database is lost, corrupted, or a bad migration or bug destroyed data.
- A restore drill is due (do one **after every migration that adds a table**, and at least monthly).

## 0. Stop the damage

1. Put the app in a quiet state: stop the backend (`docker compose stop backend`), or on a managed host scale the API to 0.
2. Do **not** run `alembic downgrade` to "fix" data. A restore is the tool for that.
3. Note the time the problem started. The restore must come from before it.

## 1. Choose the source

| Source | When | How |
|---|---|---|
| Latest daily dump | Data lost within the last day is acceptable | `docker compose cp backup:/backups/<file> ./restore.dump` |
| An older dump (up to 14 days) | The damage went unnoticed | same, pick the newest file **before** the problem |
| Managed point-in-time restore (after Phase 9) | You need a time between dumps | Use the host's restore-to-time into a **new** instance; do not restore over the live one |

## 2. Prove it first: the drill

Never restore over the live database until the backup has passed the drill.

```powershell
# a dump file
cd backend; .\.venv\Scripts\python.exe scripts\restore_drill.py --via-docker --dump ..\restore.dump
# or a new managed instance already restored from a point in time
.\.venv\Scripts\python.exe scripts\restore_drill.py --host <new-host> --user <user> --database <db>
```

It restores into a scratch database, drops it afterwards, and prints `RESTORE DRILL: PASS` only if: the schema is at the current migration, row counts match
(when it made the dump itself), no refund points at a missing plan job, and a saved trip loads with its itinerary. **A FAIL means this backup is not safe to restore from.**

## 3. Restore

1. Create the target: a new database (or a new instance on a managed host). Prefer restoring **next to** the old one and switching the URL; keep the damaged one until you are sure.
2. `pg_restore --no-owner --exit-on-error -d <new database> restore.dump`
3. Point `DATABASE_URL` at it.
4. If the dump is from an older release: `alembic upgrade head` (migrations are expand-only, so an older schema upgrades cleanly).
5. Start the backend. `/api/readyz` must answer 200.

## 4. After the restore

- **Plans in flight at the time are gone from the database but not from the users' minds.** A free plan used for a plan that no longer exists is not refunded
  automatically if the loss was a restore (the refund record went with it). Check `users.free_plans_used` against `trips.quota_consumed` and fix by hand if a user reports it.
- **Share links** created after the dump are gone; the people who made them can make new ones. Links in the dump still work (revoked ones stay revoked).
- **Money and quota counters** (`daily_counters`) reset to the dump's values: today's spend may be under-counted for a few hours. Lower `GOOGLE_DAILY_USD` for the day if unsure.
- Re-run the drill against the restored database's own next backup within a day.

## 5. Write it down

Add what happened, the source used, the time restored to, and what was lost to the post-mortem (ADR-level incidents need one; see docs/SLO.md).
