# VERIFY.md — what Phase 5 could not check on this machine, and how you check it

> **Docker is no longer needed.** `.github/workflows/verify.yml` runs every step below automatically on GitHub (PostgreSQL 16, Redis 7, gunicorn with 2 workers), with every key blanked and
> a network guard that refuses any connection that is not to the machine itself. See [docs/GITHUB.md](docs/GITHUB.md) for putting the project on GitHub and reading the results.
> The commands below are the same checks by hand, for anyone who does have Docker; the step numbers match the workflow's step names.

Phase 5 was built and tested on Windows with SQLite. These paths were **never run**: PostgreSQL, Redis, gunicorn, the
backup service and the restore drill. This file gives the exact commands and says what a pass or a fail looks like. Run them in
order in **PowerShell, from `C:\tripai`**, and paste the output of each step back to me.

**Nothing below can call or bill a real provider.** Every step uses `docker-compose.verify.yml`, which blanks every API key and turns demo mode
on. (Your real `.env` has live keys; the override wins over it. Don't run plain `docker compose up` for these checks.)

Shorthand used below (paste once per PowerShell window):

```powershell
$v = "-f","docker-compose.yml","-f","docker-compose.verify.yml"
```

---

## 0. Docker works

```powershell
docker --version
docker compose version
```

**Pass:** both print a version. **Fail:** "not recognized" means Docker Desktop is not installed or not on PATH; open it once and wait for the whale icon to stop moving.

## 1. The stack starts, on PostgreSQL, under gunicorn

```powershell
docker compose @v up --build -d
docker compose @v ps
docker compose @v logs backend | Select-String "Running upgrade|Booting worker|Traceback|ERROR"
docker compose @v exec backend python -c "from app.providers.registry import get_providers; print(get_providers().mode)"
```

**Pass:**
- `ps` shows `db` as `healthy` and `backend` as `running` (it turns `healthy` after about 20 s).
- The log lines include `Running upgrade 0008 -> 0009` and **two** `Booting worker` lines (that is gunicorn with 2 workers).
- No `Traceback`.
- The last command prints `demo`.

**Fail:** a traceback from `alembic` means migration 0009 does not apply on PostgreSQL (paste it to me: this is the most likely real finding).
If the log shows `Uvicorn running` and no `Booting worker`, `SERVER=uvicorn` is set somewhere. If the last command prints `live`, **stop and tell me**: the override did not blank the keys.

## 2. The schema is right on PostgreSQL

```powershell
docker compose @v exec db psql -U tripai -d tripai -c "select version_num from alembic_version"
docker compose @v exec db psql -U tripai -d tripai -c "\d plan_jobs"
docker compose @v exec db psql -U tripai -d tripai -c "\d plan_refunds"
```

**Pass:** `0010`; `plan_jobs` lists the unique `uq_plan_job_key (user_id, idempotency_key)` and the index `ix_plan_jobs_state_lane`; `plan_refunds` lists a
unique constraint on `job_id`. **Fail:** anything missing.

## 3. The whole stack, from outside: ids, queue, two workers

From `C:\tripai\backend` (so the venv's Python is used):

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\verify_stack.py basic
cd ..
```

**Pass:** every line says `PASS` (10 lines). That covers: `/healthz` and `/readyz`; one plan with an `Idempotency-Key` sent twice costs exactly one free plan;
a streamed plan; 10 plans at once, with only 2 allowed to run, all finish, some are told "you're #N", and each user pays one plan (with **2 gunicorn workers**, so
this also exercises per-worker queues); and `/api/system/plans` is hidden from the public.
**Fail:** any `FAIL` line: paste it with the line above it.

## 4. The whole test suite, on PostgreSQL

```powershell
docker compose exec db createdb -U tripai tripai_test
docker compose run --rm --no-deps --entrypoint sh -v "${PWD}/backend:/app" -w /app `
  -e TEST_DATABASE_URL=postgresql+psycopg://tripai:tripai@db:5432/tripai_test -e FREE_TRIP_PLANS=2 backend `
  -c "pip install -q -r requirements-dev.txt && python -m pytest -q -p no:cacheprovider"
```

**Pass:** the last line is `N passed` and no failures (on SQLite it is 644 tests, all passing locally). This runs the real-process kill test, the racing reapers and
the concurrency tests against PostgreSQL's locking, which SQLite cannot show.
**Fail:** paste the `FAILED` lines. A failure here that passes on SQLite is a PostgreSQL difference, which is exactly what this step is for.

## 5. Pool arithmetic and the production guard

```powershell
docker compose run --rm --no-deps --entrypoint python -e WEB_CONCURRENCY=4 backend -c "import app.db.session"
docker compose run --rm --no-deps --entrypoint python -e ENVIRONMENT=production -e DATABASE_URL=sqlite:///x.db -e JWT_SECRET=0123456789012345678901234567890123456789 backend -c "from app.core.config import get_settings; get_settings()"
```

**Pass:** the first prints a warning saying `4 workers x up to 27 connections = 108` is more than 80% of `POSTGRES_MAX_CONNECTIONS=100`.
The second ends with `ValueError: DATABASE_URL points at SQLite, which production refuses to use`. **Fail:** no warning, or no error.

## 6. Redis (optional, free container)

```powershell
docker compose @v --profile redis up -d redis
$env:REDIS_URL = "redis://redis:6379/0"; $env:CACHE_REDIS_ENABLED = "true"
docker compose @v up -d backend
Start-Sleep 20
curl.exe -s http://localhost:8000/api/readyz
docker compose @v exec backend python -c "from app.providers.cache import cached; from datetime import timedelta; print(cached('verify', ['x'], timedelta(hours=1), lambda: {'v': 1}))"
docker compose @v exec redis redis-cli --scan --pattern "cache:*"
docker compose @v stop redis
curl.exe -s -o NUL -w "%{http_code}`n" http://localhost:8000/api/readyz
docker compose @v start redis
Start-Sleep 5
curl.exe -s -o NUL -w "%{http_code}`n" http://localhost:8000/api/readyz
$env:REDIS_URL = ""; $env:CACHE_REDIS_ENABLED = "false"
docker compose @v up -d backend
```

**Pass:** first `readyz` shows `"redis": {"ok": true, "detail": "ok"}`; the `cached` call prints `{'v': 1}`; the scan prints one `cache:...` key; with Redis stopped `readyz`
answers `503`, and `200` again once it is back. (The last two lines put the stack back to "no Redis".)
**Fail:** `readyz` stays 200 with Redis stopped (the check is not real), or no `cache:` key (the tier does not write).
Note: the demo providers do not use the response cache, so the Redis cache tier is only exercised by this one direct call; it is otherwise tested with a fake client.

## 7. The crash test: kill the backend in the middle of a plan

This is the one that matters most. It proves a plan lost to a crash gives the free plan back, once.

```powershell
$env:PLAN_TEST_DELAY_SECONDS = "30"
docker compose @v up -d backend
Start-Sleep 20
cd backend
.\.venv\Scripts\python.exe scripts\verify_stack.py crash-start
```

It prints "NOW, in another terminal: docker compose kill backend". **In a second PowerShell window**, within 20 seconds:

```powershell
cd C:\tripai
docker compose kill backend
```

Back in the first window, once it says the connection dropped:

```powershell
$env:PLAN_TEST_DELAY_SECONDS = "0"
cd C:\tripai
docker compose @v up -d backend
Start-Sleep 25
cd backend
.\.venv\Scripts\python.exe scripts\verify_stack.py crash-check
```

Then run the two `psql` commands it prints at the end.

**Pass:**
- `crash-check` prints `PASS` for every line, including "a plan that was lost is refunded: the free plan is back".
- The first `psql` shows one row with `state = refunded` and `error_code = stale`.
- The second `psql` shows `count = 1`.

**Fail:** a free plan that stays used, a count of 2 (a double refund), or a job stuck in `running` or `pending` after about 90 seconds.

*(A graceful stop, `docker compose @v stop`, is not the same test: a worker finishes the plans it is running up to `GRACEFUL_TIMEOUT` and is then killed, so what is left is handled by the reaper exactly as in a crash. The job hand-over on shutdown only helps plans that are still waiting in line.)*

## 8. Backups and the restore drill

```powershell
docker compose @v --profile backup up -d backup
Start-Sleep 10
docker compose @v logs backup
docker compose @v exec backup ls -l /backups
cd backend
.\.venv\Scripts\python.exe scripts\restore_drill.py --via-docker
cd ..
```

**Pass:** the log says `backup written: /backups/tripai-<date>.dump`; `ls` shows a non-empty file; and the drill ends with `RESTORE DRILL: PASS` and lines marked `[ok]` for: the dump taken,
restored into a scratch database, schema at the current migration (`0010`), row counts match, no refund without its plan job, a saved trip loads with its itinerary.
**Fail:** any `[FAIL]` line, or an exit code other than 0 (`echo $LASTEXITCODE`).

To prove a *file* from the backup volume restores, not just a fresh dump:

```powershell
docker compose @v cp backup:/backups/$(docker compose @v exec -T backup sh -c "ls /backups | tail -1").Trim() .\verify.dump
cd backend; .\.venv\Scripts\python.exe scripts\restore_drill.py --via-docker --dump ..\verify.dump; cd ..
```

(If the `cp` line fights PowerShell quoting, run `docker compose @v exec backup ls /backups`, then `docker compose @v cp backup:/backups/<that name> .\verify.dump`.)
Retention (14 days) is a one-line `find -mtime` in `docker-compose.yml`; it cannot be shown in one day, so it is not part of the check. The restore
drill's steps are written to work for a managed PostgreSQL's own backups too (restore to a new instance, then run the drill with `--host`).

## 9. Load on PostgreSQL

```powershell
docker compose exec db createdb -U tripai tripai_load
docker compose run --rm --no-deps --entrypoint sh -v "${PWD}/backend:/app" -w /app `
  -e LOAD_DATABASE_URL=postgresql+psycopg://tripai:tripai@db:5432/tripai_load backend `
  -c "pip install -q psutil && python scripts/load_check.py --levels 1,12,25,50,100 --latency-mix"
```

It prints one line per level (the report file is written inside the container and is lost, so paste the printed lines).

**Pass:** `failed` is 0 at every level, and `first_event_p95_s` is **2 or under at 25 at once**. Compare 50 and 100 with the SQLite numbers in ADR 0008
(3.2 s and 7.7 s): PostgreSQL should be the same or better. **Fail:** any `failed`, or a first event above 2 s at 25. That would be the real answer to "is a single worker enough".

## Put it back

```powershell
docker compose @v --profile redis --profile backup down
```

(`down -v` would also delete the database volume and the backups; leave it off unless you want them gone.)

## What to paste back

The output of steps 1, 2, 3, 4 (last 15 lines), 5, 6, 7 (both `crash-check` and both `psql`), 8 and 9. I will mark each item in ADR 0008's "Not verified" list as verified, or fix what failed.
