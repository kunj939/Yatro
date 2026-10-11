"""The VERIFY.md steps, run by machines (GitHub Actions: .github/workflows/verify.yml). One function per step, each printing
`PASS`, `FAIL` or `SKIP` lines and appending them to verify-results/results.jsonl; `summary` turns that into results.md and fails the
run if anything failed. No step needs Docker Desktop, a real key, or a real provider.

    python scripts/ci_verify.py safety|migrations|tests|gunicorn|config|proxy|redis|backup|load|crash|summary

Safety, in this order of trust: (1) every API key is blanked and demo mode forced here, whatever the job's environment says;
(2) steps that run our code put ci/netguard on PYTHONPATH, which refuses any connection that is not to this machine, and the
`summary` step fails if even one was attempted; (3) `.env` is never read (the checkout has none; `safety` fails if one appears).

Local dry run (Windows, no PostgreSQL/Redis/Docker): VERIFY_SQLITE=1 VERIFY_SKIP_DOCKER=1 VERIFY_ALLOW_ENV=1 python scripts/ci_verify.py <step>.
That exercises the logic of the steps, not PostgreSQL, gunicorn or Redis.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # backend/
REPO = ROOT.parent
OUT = Path(os.environ.get("VERIFY_OUT") or REPO / "verify-results")
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = OUT / "results.jsonl"
PORT = int(os.environ.get("VERIFY_PORT", "8000"))
BASE = f"http://127.0.0.1:{PORT}"
SQLITE = os.environ.get("VERIFY_SQLITE") == "1"
SKIP_DOCKER = os.environ.get("VERIFY_SKIP_DOCKER") == "1"
PG = os.environ.get("VERIFY_PG_URL", "postgresql+psycopg://tripai:tripai@localhost:5432")  # no database name on the end
REDIS = os.environ.get("VERIFY_REDIS_URL", "redis://localhost:6379/0")
GUARD_LOG = OUT / "netguard.log"
sys.path.insert(0, str(Path(__file__).resolve().parent))

KEYS = ["GOOGLE_MAPS_SERVER_KEY", "OPENROUTER_API_KEY", "OPENAI_COMPAT_API_KEY", "IRCTC_RAPIDAPI_KEY", "RAILRADAR_API_KEY", "WEB_SEARCH_API_KEY",
        "TWILIO_AUTH_TOKEN", "TWILIO_ACCOUNT_SID", "FAST2SMS_API_KEY", "SMTP_HOST", "SMTP_PASSWORD"]
SAFE = {**{k: "" for k in KEYS}, "ENVIRONMENT": "development", "DEMO_MODE": "true", "LLM_PROVIDER": "template", "SMS_PROVIDER": "console",
        "OTP_RESEND_SECONDS": "0", "RATE_LIMIT_ENABLED": "false", "BUDGET_MODE": "off", "RAIL_REFRESH_DAILY_CAP": "0", "LOG_LEVEL": "WARNING"}
# the stack settings of docker-compose.verify.yml: small, so a queue forms and a reaper acts within a minute
STACK = {"WEB_CONCURRENCY": "2", "MAX_CONCURRENT_PLANS": "2", "PLAN_QUEUE_LIMIT": "4", "PLAN_QUEUE_HARD_LIMIT": "8", "FREE_TRIP_PLANS": "50",
         "JOB_HEARTBEAT_SECONDS": "2", "REAPER_STALE_SECONDS": "10", "JOB_REQUEUE_MAX_AGE_SECONDS": "0", "OTP_PER_IP_HOUR": "0"}


# --- results -----------------------------------------------------------------------------------------------------------------------

def record(step: str, name: str, status: bool | str, detail: str = "") -> bool:
    st = status if isinstance(status, str) else ("PASS" if status else "FAIL")
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"step": step, "name": name, "status": st, "detail": detail[:400], "t": time.time()}) + "\n")
    print(f"{st:<4} {step}: {name}" + (f"  ({detail[:300]})" if detail else ""), flush=True)
    return st != "FAIL"


# --- environment, databases, processes -----------------------------------------------------------------------------------------------

def db_url(name: str) -> str:
    if SQLITE:
        return f"sqlite:///{(OUT / ('tmp-' + name + '.db')).as_posix()}"
    return f"{PG}/{name}"


def fresh_db(name: str) -> str:
    """An empty database called `name` (PostgreSQL: dropped and created; SQLite: the file removed)."""
    if SQLITE:
        for suffix in ("", "-wal", "-shm"):
            Path(str(OUT / f"tmp-{name}.db") + suffix).unlink(missing_ok=True)
        return db_url(name)
    from sqlalchemy import create_engine, text
    eng = create_engine(f"{PG}/postgres", isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{name}"'))
    eng.dispose()
    return db_url(name)


def scalar(url: str, sql: str, **params):
    from sqlalchemy import create_engine, text
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            return c.execute(text(sql), params).scalar()
    finally:
        eng.dispose()


def child_env(extra: dict | None = None, guard: bool = True) -> dict:
    env = {**os.environ, **SAFE, **(extra or {})}
    if guard:
        env["PYTHONPATH"] = str(ROOT / "ci" / "netguard") + os.pathsep + os.environ.get("PYTHONPATH", "")
        env["NETGUARD_LOG"] = str(GUARD_LOG)
    return env


def run(cmd: list[str], env: dict, log: str | None = None, timeout: int = 600, cwd: Path = ROOT) -> tuple[int, str]:
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    if log:
        (OUT / log).write_text(out, encoding="utf-8")
    return r.returncode, out


def py(code: str, env: dict, timeout: int = 120) -> tuple[int, str]:
    return run([sys.executable, "-c", code], env, timeout=timeout)


def http(path: str, timeout: float = 10) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:  # noqa: BLE001 - connection refused and the like
        return 0, type(e).__name__


class Server:
    """The API as the Docker image runs it: docker-entrypoint.sh (migrations, then gunicorn with uvicorn workers) on Linux.
    On Windows (a dry run only) it is alembic, then uvicorn."""

    def __init__(self, name: str, database: str, extra: dict):
        self.name, self.database = name, database
        self.env = child_env({**STACK, "DATABASE_URL": database, "PORT": str(PORT), **extra})
        self.log = OUT / f"server-{name}.log"
        self.proc: subprocess.Popen | None = None
        self.gunicorn = os.name != "nt" and shutil.which("sh") is not None

    def start(self) -> None:
        import socket
        for _ in range(30):  # a server we stopped a moment ago may take a few seconds to let go of the port
            with socket.socket() as probe:
                probe.settimeout(1)
                if probe.connect_ex(("127.0.0.1", PORT)) != 0:
                    break
            time.sleep(1)
        else:  # never run against whatever answers on this port (it could be a live dev server)
            raise RuntimeError(f"port {PORT} is already in use: refusing to start (and to test) anything there; set VERIFY_PORT")
        self.logf = self.log.open("ab")
        if self.gunicorn:
            cmd = ["sh", "docker-entrypoint.sh"]
        else:
            subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=self.env, capture_output=True, check=True)
            trusted = self.env.get("TRUSTED_PROXY_IPS", "").strip()
            cmd = [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT)] + (
                ["--proxy-headers", f"--forwarded-allow-ips={trusted}"] if trusted else ["--no-proxy-headers"])
        self.proc = subprocess.Popen(cmd, cwd=ROOT, env=self.env, stdout=self.logf, stderr=subprocess.STDOUT,
                                     start_new_session=os.name != "nt")

    def wait_ready(self, timeout: int = 150) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.proc and self.proc.poll() is not None:
                return False
            if http("/api/readyz")[0] == 200:
                return True
            time.sleep(1)
        return False

    def _signal(self, sig) -> None:
        if not self.proc or self.proc.poll() is not None:
            return
        if os.name != "nt":
            os.killpg(os.getpgid(self.proc.pid), sig)  # the master AND its workers: this is what a container kill does
        else:  # Windows dry run: the launcher stub has child processes; take down the whole tree
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True)

    def kill9(self) -> None:
        self._signal(signal.SIGKILL if os.name != "nt" else None)
        if self.proc:
            self.proc.wait(30)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self._signal(signal.SIGTERM if os.name != "nt" else None)
            try:
                self.proc.wait(45)
            except subprocess.TimeoutExpired:
                self.kill9()
        if getattr(self, "logf", None):
            self.logf.close()

    def text(self) -> str:
        return self.log.read_text(encoding="utf-8", errors="replace") if self.log.exists() else ""


def head_revision() -> str:
    return sorted(p.name.split("_")[0] for p in (ROOT / "alembic" / "versions").glob("[0-9]*.py"))[-1]


def stack_checks(step: str, lines: str) -> None:
    """Turn verify_stack.py's [PASS]/[FAIL] lines into results."""
    for m in re.finditer(r"^\[(PASS|FAIL)\] (.+?)(?:  \((.*)\))?$", lines, re.M):
        record(step, m.group(2), m.group(1), m.group(3) or "")


# --- steps -------------------------------------------------------------------------------------------------------------------------------

def step_safety() -> None:
    s = "safety"
    blank = [k for k in KEYS if os.environ.get(k, "") != ""]
    record(s, "no API key is set in the job's environment", not blank, ", ".join(blank))
    record(s, "demo mode is forced", SAFE["DEMO_MODE"] == "true" and not blank)
    env_files = [str(p) for p in (REPO / ".env", ROOT / ".env") if p.exists()]
    record(s, "there is no .env file to read", not env_files or os.environ.get("VERIFY_ALLOW_ENV") == "1", ", ".join(env_files))
    rc, out = py("from app.providers.registry import get_providers; print('MODE=' + get_providers().mode)", child_env({"DATABASE_URL": db_url("safety")}))
    record(s, "the app starts in demo mode with every key blank", "MODE=demo" in out, out.strip().splitlines()[-1] if out.strip() else "")
    GUARD_LOG.unlink(missing_ok=True)
    rc, out = py("import socket\ntry:\n    socket.create_connection(('93.184.216.34', 443), timeout=3)\n    print('CONNECTED')\nexcept OSError as e:\n    print(e)",
                 child_env())
    record(s, "the network guard refuses a connection to a public address", "blocked by the CI network guard" in out, out.strip()[:120])
    rc, out = py("import socket\ntry:\n    socket.create_connection(('127.0.0.1', 9), timeout=2)\nexcept OSError as e:\n    print(type(e).__name__, e)", child_env())
    record(s, "the guard lets this machine's own services through", "blocked" not in out, out.strip()[:120])
    GUARD_LOG.unlink(missing_ok=True)  # those two probes were on purpose; from here on, any entry is real


def step_migrations() -> None:
    s = "migrations"
    url = fresh_db("tripai_migrate")
    env = child_env({"DATABASE_URL": url}, guard=False)
    alembic = lambda *a: run([sys.executable, "-m", "alembic", *a], env, log=f"alembic-{'-'.join(a)}.log")  # noqa: E731
    rc, out = alembic("upgrade", "head")
    head = head_revision()
    record(s, f"upgrade to head ({head}) on a fresh database", rc == 0, out.strip().splitlines()[-1] if rc else "")
    if rc:
        return
    record(s, "alembic_version is the newest revision", scalar(url, "select version_num from alembic_version") == head)
    tables = lambda: set(scalar(url, "select string_agg(name, ',') from (select table_name as name from information_schema.tables where table_schema = 'public') t").split(",")) if not SQLITE else set(scalar(url, "select group_concat(name) from sqlite_master where type='table'").split(","))  # noqa: E731
    record(s, "plan_jobs and plan_refunds exist", {"plan_jobs", "plan_refunds"} <= tables())
    if not SQLITE:
        uq = scalar(url, "select count(*) from pg_constraint c join pg_class t on t.oid = c.conrelid where t.relname = 'plan_jobs' and c.conname = 'uq_plan_job_key' and c.contype = 'u'")
        ix = scalar(url, "select count(*) from pg_indexes where tablename = 'plan_jobs' and indexname = 'ix_plan_jobs_state_lane'")
        rf = scalar(url, "select count(*) from pg_constraint c join pg_class t on t.oid = c.conrelid where t.relname = 'plan_refunds' and c.contype = 'u'")
        record(s, "plan_jobs has the unique idempotency key and the state/lane index", uq == 1 and ix == 1, f"unique={uq} index={ix}")
        record(s, "plan_refunds is unique on job_id", rf == 1, f"unique constraints={rf}")
        col = scalar(url, "select count(*) from information_schema.columns where table_name = 'plan_jobs' and column_name = 'late_finish_at'")
        record(s, "plan_jobs.late_finish_at exists (0010)", col == 1)
    rc, out = alembic("downgrade", "0008")
    record(s, "downgrade to 0008 works", rc == 0, out.strip().splitlines()[-1] if rc else "")
    gone = "plan_jobs" not in tables() and "plan_refunds" not in tables() and "provider_circuit" in tables()
    record(s, "after the downgrade the Phase 5 tables are gone and Phase 4's remain", gone)
    rc, out = alembic("upgrade", "head")
    record(s, "upgrade to head again", rc == 0 and scalar(url, "select version_num from alembic_version") == head)
    rc, out = run([sys.executable, "scripts/migration_lint.py"], env)
    record(s, "every new migration is expand-only (or marked)", rc == 0, out.strip()[:200])


def step_tests() -> None:
    s = "tests"
    url = fresh_db("tripai_test")
    junit = OUT / "junit.xml"
    env = child_env({"TEST_DATABASE_URL": url, "FREE_TRIP_PLANS": "2"})
    env.pop("DATABASE_URL", None)
    t0 = time.time()
    rc, out = run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"], env, log="pytest.log", timeout=3000)
    try:
        suite = ET.parse(junit).getroot()
        suite = suite if suite.tag == "testsuite" else suite[0]
        n, bad = int(suite.get("tests", 0)), int(suite.get("failures", 0)) + int(suite.get("errors", 0))
        failed = [f"{c.get('classname')}::{c.get('name')}" for c in suite.iter("testcase") if c.find("failure") is not None or c.find("error") is not None]
    except Exception as exc:  # noqa: BLE001
        n, bad, failed = 0, 1, [f"no junit report ({type(exc).__name__})"]
    record(s, "the full suite on " + ("SQLite (dry run)" if SQLITE else "PostgreSQL 16"), rc == 0 and bad == 0 and n >= 600,
           f"{n} tests, {bad} failed, {time.time() - t0:.0f}s" + (": " + "; ".join(failed[:5]) if failed else ""))


def step_gunicorn() -> None:
    s = "gunicorn"
    url = fresh_db("tripai")
    srv = Server("main", url, {})
    srv.start()
    ready = srv.wait_ready()
    record(s, "the API starts and /readyz is ready", ready, "" if ready else srv.text()[-400:])
    if not ready:
        srv.stop()
        return
    log = srv.text()
    if srv.gunicorn:
        record(s, "the entrypoint ran the migrations", "Running upgrade" in log or "Will assume" in log, "" if "Running upgrade" in log else "no 'Running upgrade' line")
        n = len(re.findall(r"Booting worker", log))
        record(s, "gunicorn started two workers", n == 2, f"{n} 'Booting worker' lines")
    else:
        record(s, "the entrypoint ran the migrations and gunicorn started two workers", "SKIP", "dry run on Windows: alembic then uvicorn")
    rc, out = run([sys.executable, "scripts/verify_stack.py", "basic", "--base", BASE], child_env(), log="verify_stack-basic.log", timeout=600)
    stack_checks(s, out)
    active = scalar(url, "select count(*) from plan_jobs where state in ('pending', 'running')")
    record(s, "no plan job is left pending or running after the burst", active == 0, f"{active} left")
    done = scalar(url, "select count(*) from plan_jobs where state = 'done'")
    refunds = scalar(url, "select count(*) from plan_refunds")
    record(s, "every plan finished and none needed a refund", done >= 12 and refunds == 0, f"{done} done, {refunds} refunds")
    srv.stop()


def step_config() -> None:
    s = "config"
    pgurl = db_url("tripai") if not SQLITE else "postgresql+psycopg://tripai:tripai@localhost:5432/tripai"
    rc, out = py("import app.db.session", child_env({"DATABASE_URL": pgurl, "WEB_CONCURRENCY": "4"}), 60)
    record(s, "4 workers x the pool is flagged against POSTGRES_MAX_CONNECTIONS", "108" in out and "POSTGRES_MAX_CONNECTIONS=100" in out, out.strip()[-200:])
    strong = "ci-placeholder-not-a-secret-0123456789abcdef"
    rc, out = py("from app.core.config import get_settings; get_settings()", child_env({"ENVIRONMENT": "production", "DATABASE_URL": "sqlite:///x.db", "JWT_SECRET": strong}), 60)
    record(s, "production refuses to start on SQLite", rc != 0 and "SQLite" in out, out.strip().splitlines()[-1][:160] if out.strip() else "")
    rc, out = py("from app.core.config import get_settings; get_settings()",
                 child_env({"ENVIRONMENT": "production", "DATABASE_URL": "postgresql+psycopg://u:p@h:5432/d", "JWT_SECRET": strong, "PLAN_TEST_DELAY_SECONDS": "5"}), 60)
    record(s, "production refuses the verification delay hook", rc != 0 and "PLAN_TEST_DELAY_SECONDS" in out, out.strip().splitlines()[-1][:160] if out.strip() else "")
    rc, out = py("from app.core.config import get_settings; get_settings(); print('OK')",
                 child_env({"ENVIRONMENT": "production", "DATABASE_URL": "postgresql+psycopg://u:p@h:5432/d", "JWT_SECRET": strong,
                            "PUBLIC_APP_URL": "https://app.example.com"}), 60)
    record(s, "production starts on PostgreSQL with a strong secret and a public app address", rc == 0 and "OK" in out, out.strip()[-160:])
    rc, out = py("from app.core.config import get_settings; get_settings()",
                 child_env({"ENVIRONMENT": "production", "DATABASE_URL": "postgresql+psycopg://u:p@h:5432/d", "JWT_SECRET": strong, "PUBLIC_APP_URL": ""}), 60)
    record(s, "production refuses to start without PUBLIC_APP_URL", rc != 0 and "PUBLIC_APP_URL" in out, out.strip().splitlines()[-1][:160] if out.strip() else "")


def _redis_container() -> str | None:
    for flt in (["--filter", "publish=6379"], ["--filter", "ancestor=redis:7"]):
        rc, out = run(["docker", "ps", "-q", *flt], child_env(guard=False))
        if rc == 0 and out.strip():
            return out.split()[0]
    return None


def step_redis() -> None:
    s = "redis"
    if SKIP_DOCKER:
        return void_skip(s, "Redis paths", "dry run: no Redis")
    import redis as redis_lib
    r = redis_lib.Redis.from_url(REDIS, socket_connect_timeout=3)
    try:
        record(s, "the Redis service answers", r.ping() is True)
    except Exception as exc:  # noqa: BLE001
        record(s, "the Redis service answers", False, type(exc).__name__)
        return
    for k in r.scan_iter("cache:*"):
        r.delete(k)
    url = fresh_db("tripai_redis")
    extra = {"REDIS_URL": REDIS, "CACHE_REDIS_ENABLED": "true", "WEB_CONCURRENCY": "1"}
    srv = Server("redis", url, extra)
    srv.start()
    if not srv.wait_ready():
        record(s, "the API starts with REDIS_URL set", False, srv.text()[-400:])
        srv.stop()
        return
    code, body = http("/api/readyz")
    record(s, "/readyz reports Redis as ok", code == 200 and '"redis":{"ok":true,"detail":"ok"}' in body.replace(" ", ""), body[:200])
    env = child_env({"DATABASE_URL": url, **extra})
    snippet = ("from app.providers.cache import cached; from datetime import timedelta\n"
               "print(cached('verify', ['x'], timedelta(hours=1), lambda: {'v': 1}))\n"
               "print(cached('verify', ['x'], timedelta(hours=1), lambda: {'v': 2}))")
    rc, out = py(snippet, env)
    record(s, "the cache tier fetches once and then answers from Redis", rc == 0 and out.strip().splitlines()[-2:] == ["{'v': 1}", "{'v': 1}"], out.strip()[-120:])
    keys = list(r.scan_iter("cache:*"))
    ttl = r.ttl(keys[0]) if keys else -2
    record(s, "one cache:* key was written, and it lives no longer than the one hour it was given", len(keys) == 1 and 0 < ttl <= 3600, f"keys={len(keys)} ttl={ttl}")
    auth_rc, auth_out = run([sys.executable, "scripts/verify_stack.py", "basic", "--base", BASE], child_env(), log="verify_stack-redis.log", timeout=600)
    plans_ok = "[PASS] a plan with a key is ready" in auth_out and "[PASS] a streamed plan ends with a result and no error" in auth_out
    record(s, "plans still run normally with Redis configured", plans_ok)
    cid = _redis_container()
    if not cid:
        record(s, "found the Redis container to stop", False, "docker ps found none")
    else:
        run(["docker", "stop", cid], child_env(guard=False))
        end, code, body = time.time() + 30, 200, ""
        while time.time() < end:
            code, body = http("/api/readyz")
            if code == 503:
                break
            time.sleep(1)
        record(s, "/readyz answers 503 and names Redis while it is stopped", code == 503 and '"redis":{"ok":false' in body.replace(" ", ""), f"{code} {body[:160]}")
        run(["docker", "start", cid], child_env(guard=False))
        end, code = time.time() + 60, 0
        while time.time() < end:
            code = http("/api/readyz")[0]
            if code == 200:
                break
            time.sleep(1)
        record(s, "/readyz is 200 again when Redis is back", code == 200, str(code))
    srv.stop()


def void_skip(step: str, name: str, why: str) -> None:
    record(step, name, "SKIP", why)


def step_backup() -> None:
    s = "backup"
    if SQLITE:
        return void_skip(s, "pg_dump backup and restore drill", "dry run: needs PostgreSQL")
    url = db_url("tripai")
    trips = scalar(url, "select count(*) from trips")
    record(s, "the database has data to back up (from the gunicorn step)", trips > 0, f"{trips} trips")
    rc, out = run(["pg_dump", "--version"], child_env(guard=False))
    major = int(re.search(r"(\d+)", out.split("pg_dump")[-1]).group(1)) if rc == 0 else 0
    record(s, "pg_dump is version 16 or newer (same as the server)", major >= 16, out.strip())
    d = Path(tempfile.mkdtemp(prefix="yatro-backups-"))
    old, recent = d / "tripai-20200101-000000.dump", d / "tripai-20260101-000000.dump"
    for f, days in ((old, 20), (recent, 3)):
        f.write_bytes(b"x")
        t = time.time() - days * 86400
        os.utime(f, (t, t))
    env = child_env({"PGHOST": "localhost", "PGPORT": "5432", "PGUSER": "tripai", "PGPASSWORD": "tripai", "PGDATABASE": "tripai",
                     "BACKUP_DIR": str(d), "BACKUP_RETENTION_DAYS": "14"}, guard=False)
    rc, out = run(["sh", "scripts/pg_backup.sh"], env, log="pg_backup.log")
    new = sorted(p for p in d.glob("tripai-*.dump") if p not in (old, recent))
    record(s, "the backup script writes a dump", rc == 0 and len(new) == 1 and new[0].stat().st_size > 1000, out.strip().splitlines()[0][:120] if out.strip() else "")
    record(s, "retention: a 20-day-old dump is deleted, a 3-day-old one is kept", not old.exists() and recent.exists())
    if new:
        rc, out = run(["pg_restore", "--list", str(new[0])], env)
        record(s, "pg_restore can read the dump", rc == 0 and "plan_jobs" in out)
        for label, args in (("a backup file", ["--dump", str(new[0])]), ("a fresh dump of the live database", [])):
            rc, out = run([sys.executable, "scripts/restore_drill.py", "--host", "localhost", "--user", "tripai", "--password", "tripai",
                           "--database", "tripai", *args], child_env(guard=False), log=f"restore_drill-{label.split()[1]}.log")
            record(s, f"restore drill from {label}", rc == 0 and "RESTORE DRILL: PASS" in out, out.strip().splitlines()[-1][:160] if out.strip() else "")
            for line in out.splitlines():
                if "[FAIL]" in line:
                    record(s, "drill detail", False, line.strip())
    shutil.rmtree(d, ignore_errors=True)  # a backup holds data; it never goes into the results


def step_load() -> None:
    s = "load"
    url = fresh_db("tripai_load")
    env = child_env({"LOAD_TAG": "ci" if not SQLITE else "dryrun", **({"LOAD_DATABASE_URL": url} if not SQLITE else {})})  # never overwrites the committed reports
    env.pop("DATABASE_URL", None)
    levels = os.environ.get("VERIFY_LOAD_LEVELS", "1,12,25,50,100")
    rc, out = run([sys.executable, "scripts/load_check.py", "--levels", levels, "--latency-mix"], env, log="load_check.log", timeout=1500)
    rows = []
    for line in out.splitlines():
        if line.startswith("{'concurrent'"):
            rows.append(ast.literal_eval(line))
    record(s, "the load check ran every level", rc == 0 and len(rows) == len(levels.split(",")), f"{len(rows)} levels, exit {rc}")
    for r in rows:
        record(s, f"{r['concurrent']} plans at once: none failed", r["failed"] == 0,
               f"ok {r['ok']}, unavailable {r['unavailable']} (simulated timeouts), p95 plan {r['p95_s']}s, first event p95 {r['first_event_p95_s']}s, "
               f"{r['waited_in_line']} waited in line, threads {r['peak_threads']}, RSS {r['peak_rss_mb']} MB")
    at25 = next((r for r in rows if r["concurrent"] == 25), None)
    if at25:
        record(s, "first event p95 is 2 s or under at 25 plans at once", at25["first_event_p95_s"] <= 2.0, f"{at25['first_event_p95_s']}s (SQLite, one process: 2.1s)")
    for f in (REPO / "docs" / "perf").glob("load-*-mix-ci.md"):
        shutil.copy(f, OUT / f.name)


def _crash(label: str, requeue_age: int, expect: str) -> None:
    import httpx

    import verify_stack as V
    s = f"crash-{label}"
    url = fresh_db(f"tripai_crash_{label}")
    extra = {"JOB_REQUEUE_MAX_AGE_SECONDS": str(requeue_age)}
    s1 = Server(f"crash-{label}-1", url, {**extra, "PLAN_TEST_DELAY_SECONDS": "30"})
    s1.start()
    if not s1.wait_ready():
        record(s, "the API starts", False, s1.text()[-400:])
        s1.stop()
        return
    c = httpx.Client(base_url=BASE, timeout=60)
    auth = V.register(c, "crash")
    trip = V.new_trip(c, auth)
    before = V.left(c, auth)
    key = "ci-crash-" + uuid.uuid4().hex
    threading.Thread(target=lambda: _quiet(lambda: V.stream(httpx.Client(base_url=BASE, timeout=600), auth, trip, key)), daemon=True).start()
    running = False
    for _ in range(60):
        if scalar(url, "select state from plan_jobs where idempotency_key = :k", k=key) == "running":
            running = True
            break
        time.sleep(0.5)
    record(s, "the plan is running (held mid-way) when the backend is killed", running)
    s1.kill9()
    gone = http("/api/healthz")[0] == 0
    record(s, "the backend, master and workers, is gone (SIGKILL, no shutdown hooks)", gone)
    s2 = Server(f"crash-{label}-2", url, {**extra, "PLAN_TEST_DELAY_SECONDS": "0"})
    s2.start()
    record(s, "the backend comes back", s2.wait_ready(), "")
    ev: list = []
    end = time.time() + 180
    while time.time() < end:  # the same tap again, with the same key
        try:
            ev = V.stream(c, auth, trip, key)
            if ev and ev[-1]["type"] == "result":
                break
        except Exception:  # noqa: BLE001 - the server may still be starting its workers
            pass
        time.sleep(3)
    last = ev[-1] if ev else {}
    record(s, "the same key gets an answer, not an error", last.get("type") == "result", last.get("state") or last.get("status") or "")
    after = V.left(c, auth)
    job = scalar(url, "select state || '/' || coalesce(error_code, '') || '/' || attempt from plan_jobs where idempotency_key = :k", k=key)
    jobs = scalar(url, "select count(*) from plan_jobs where idempotency_key = :k", k=key)
    refunds = scalar(url, "select count(*) from plan_refunds r join plan_jobs j on j.id = r.job_id where j.idempotency_key = :k", k=key)
    status = scalar(url, "select status from trips where id = :t", t=trip if not SQLITE else trip.replace("-", ""))
    record(s, "there is exactly one job for that key", jobs == 1, f"{jobs}")
    if expect == "refund":
        record(s, "the answer is a calm 'unavailable', not a result from nowhere", last.get("state") == "unavailable" and last.get("quota_used") is False)
        record(s, "the free plan is back", after == before, f"{before} -> {after}")
        record(s, "the job was refunded by the reaper", job == "refunded/stale/1", job)
        record(s, "exactly one refund record", refunds == 1, f"{refunds}")
        record(s, "the trip is back to draft, ready to plan again", status == "draft", str(status))
        time.sleep(35)  # at least one more reaper pass
        again = scalar(url, "select count(*) from plan_refunds r join plan_jobs j on j.id = r.job_id where j.idempotency_key = :k", k=key)
        record(s, "later reaper passes change nothing: still one refund, same free plans", again == 1 and V.left(c, auth) == before, f"refunds {again}")
    else:
        record(s, "the plan was run again once and finished", last.get("status") == "ready", str(last.get("status")))
        record(s, "it used exactly one free plan", after == before - 1, f"{before} -> {after}")
        record(s, "the job ran twice and ended done", job == "done//2", job)
        record(s, "no refund", refunds == 0, f"{refunds}")
    s2.stop()


def _quiet(fn) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001 - the connection dropping is the point
        pass


def step_crash() -> None:
    _crash("refund", 0, "refund")
    _crash("rerun", 300, "rerun")


def step_proxy() -> None:
    """Hotfix 2026-10-07 under the real server: X-Forwarded-For is never believed unless the request comes from a trusted proxy,
    and send-code is limited, India-only and does not say whether a number has an account."""
    import httpx
    s = "proxy"
    base = {"RATE_LIMIT_ENABLED": "true", "WEB_CONCURRENCY": "1"}  # one worker: the in-process limit is then exact

    def logins(headers_for) -> list[int]:
        c = httpx.Client(base_url=BASE, timeout=30)
        return [c.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x" * 9}, headers=headers_for(i)).status_code for i in range(14)]

    # A. nothing trusted: a different header every time must not buy a new bucket
    srv = Server("proxy-a", fresh_db("tripai_proxy_a"), {**base, "TRUSTED_PROXY_IPS": ""})
    srv.start()
    if not srv.wait_ready():
        record(s, "the API starts with no trusted proxy", False, srv.text()[-300:])
        srv.stop()
        return
    codes = logins(lambda i: {"X-Forwarded-For": f"203.0.113.{i}"})
    record(s, "with TRUSTED_PROXY_IPS empty, 14 different X-Forwarded-For values are still one client: refused from the 11th", codes[:10] == [401] * 10 and 429 in codes[10:], str(codes))
    srv.stop()
    # A2. send-code, with the per-minute limiter off so only the code limits are in play
    # Phone codes are off by default (email-only sign-up); this check is about their limits, so it switches them on.
    srv = Server("proxy-a2", fresh_db("tripai_proxy_a2"), {"RATE_LIMIT_ENABLED": "false", "WEB_CONCURRENCY": "1", "TRUSTED_PROXY_IPS": "",
                                                          "REQUIRE_PHONE_CODE": "true"})
    srv.start()
    if not srv.wait_ready():
        record(s, "the API starts for the send-code checks", False, srv.text()[-300:])
        srv.stop()
        return
    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.post("/api/auth/send-code", json={"channel": "phone", "target": "+44 7700 900123"})
    record(s, "send-code refuses a non-Indian number (HTTP 422, nothing sent)", r.status_code == 422 and "Indian" in r.text, f"{r.status_code}")
    phone = f"+9170{uuid.uuid4().int % 10**8:08d}"
    got = [c.post("/api/auth/send-code", json={"channel": "phone", "target": phone}, headers={"X-Forwarded-For": f"198.51.100.{i}"}).status_code for i in range(4)]
    record(s, "send-code: 3 codes an hour per number, the 4th is a calm 429 (a spoofed header changes nothing)", got == [200, 200, 200, 429], str(got))
    msg = c.post("/api/auth/send-code", json={"channel": "phone", "target": phone}).json().get("error", {}).get("message", "")
    record(s, "the refusal says nothing about whether the number has an account", "account" not in msg.lower() and "several codes" in msg, msg[:100])
    srv.stop()
    # B. our own proxy (127.0.0.1, which is where this script connects from) is trusted: real clients behind it get their own buckets
    srv = Server("proxy-b", fresh_db("tripai_proxy_b"), {**base, "TRUSTED_PROXY_IPS": "127.0.0.1"})
    srv.start()
    if not srv.wait_ready():
        record(s, "the API starts with a trusted proxy", False, srv.text()[-300:])
        srv.stop()
        return
    many = logins(lambda i: {"X-Forwarded-For": f"198.51.100.{i}"})
    record(s, "behind a trusted proxy, 14 different clients are not one client (no 429)", 429 not in many, str(many))
    one = logins(lambda i: {"X-Forwarded-For": "6.6.6.6, 198.51.100.200"})
    record(s, "...and one client is one client, whatever it puts on the left (refused from the 11th)", one[:10] == [401] * 10 and 429 in one[10:], str(one))
    srv.stop()


def step_summary() -> None:
    rows = [json.loads(line) for line in RESULTS.read_text(encoding="utf-8").splitlines() if line.strip()] if RESULTS.exists() else []
    blocked = GUARD_LOG.read_text(encoding="utf-8").splitlines() if GUARD_LOG.exists() else []
    rows.append({"step": "safety", "name": "no outbound connection was attempted by any step", "status": "PASS" if not blocked else "FAIL",
                 "detail": "; ".join(blocked[:3])})
    steps = list(dict.fromkeys(r["step"] for r in rows))
    failed = [r for r in rows if r["status"] == "FAIL"]
    md = ["# Verification results", "", f"**{'FAIL' if failed else 'PASS'}**: {sum(r['status'] == 'PASS' for r in rows)} passed, {len(failed)} failed, "
          f"{sum(r['status'] == 'SKIP' for r in rows)} skipped.", ""]
    for st in steps:
        part = [r for r in rows if r["step"] == st]
        md += [f"## {st}", "", "| | Check | Detail |", "|---|---|---|"]
        md += [f"| {r['status']} | {r['name']} | {r['detail'].replace('|', '/')} |" for r in part]
        md.append("")
    (OUT / "results.md").write_text("\n".join(md), encoding="utf-8")
    (OUT / "results.json").write_text(json.dumps({"failed": len(failed), "rows": rows}, indent=1), encoding="utf-8")
    print("\n".join(md))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("\n".join(md) + "\n")
    print("\nVERIFY:", "FAIL" if failed else "PASS")
    sys.exit(1 if failed else 0)


STEPS = {"safety": step_safety, "migrations": step_migrations, "tests": step_tests, "gunicorn": step_gunicorn, "config": step_config,
         "proxy": step_proxy, "redis": step_redis, "backup": step_backup, "load": step_load, "crash": step_crash, "summary": step_summary}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=list(STEPS))
    name = ap.parse_args().step
    t0 = time.time()
    try:
        STEPS[name]()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - a crashed step is a FAIL line, never a silent pass
        import traceback
        traceback.print_exc()
        record(name, "the step itself ran to the end", False, f"{type(exc).__name__}: {exc}")
    print(f"-- {name} took {time.time() - t0:.0f}s", flush=True)
    mine = [json.loads(line) for line in (RESULTS.read_text(encoding="utf-8").splitlines() if RESULTS.exists() else [])]
    sys.exit(1 if any(r["status"] == "FAIL" and r["t"] >= t0 and r["step"].split("-")[0] == name for r in mine) else 0)
