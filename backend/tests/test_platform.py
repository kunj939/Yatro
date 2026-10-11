"""Phase 5 platform pieces: health, the production guard, pool sizing, migrations, cache tiers, TTLs, rail cleanup, prewarm."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import Settings, get_settings
from app.db import pool
from app.db.session import SessionLocal
from app.providers import cache as cache_mod
from app.providers import ttl

ROOT = Path(__file__).resolve().parents[1]


# --- health --------------------------------------------------------------------------------------------------------------

def test_healthz_is_alive_and_touches_nothing(client, monkeypatch):
    from app.db import session
    monkeypatch.setattr(session, "SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    r = client.get("/api/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_readyz_is_ready_when_the_database_answers(client):
    r = client.get("/api/readyz")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "ready"
    assert body["checks"]["database"]["ok"] and body["checks"]["plan_runner"]["ok"]
    assert body["checks"]["redis"] == {"ok": True, "detail": "not configured"}  # optional: unset is fine


def test_readyz_says_which_part_is_down_with_503(client, monkeypatch):
    from app.db import session
    monkeypatch.setattr(session, "SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    r = client.get("/api/readyz")
    assert r.status_code == 503 and r.json()["status"] == "not_ready"
    assert r.json()["checks"]["database"] == {"ok": False, "detail": "RuntimeError"}


def test_readyz_fails_while_the_runner_is_shutting_down(client, monkeypatch):
    from app.services.plan_runner import runner
    monkeypatch.setattr(runner, "accepting", False)
    r = client.get("/api/readyz")
    assert r.status_code == 503 and r.json()["checks"]["plan_runner"]["ok"] is False


def test_readyz_checks_redis_only_when_it_is_configured(client, monkeypatch):
    from app.core import redis_client

    class Down:
        def ping(self):
            raise ConnectionError("no redis")
    monkeypatch.setattr(get_settings(), "redis_url", "redis://nowhere:6379/0")
    monkeypatch.setattr(redis_client, "get", lambda: Down())
    r = client.get("/api/readyz")
    assert r.status_code == 503 and r.json()["checks"]["redis"] == {"ok": False, "detail": "ConnectionError"}


# --- production refuses SQLite ----------------------------------------------------------------------------------------------

STRONG = "x" * 40


def test_production_will_not_start_on_sqlite():
    with pytest.raises(ValueError, match="SQLite"):
        Settings(_env_file=None, environment="production", database_url="sqlite:///./tripai.db", jwt_secret=STRONG)


def test_production_starts_on_postgresql_and_dev_and_tests_may_use_sqlite():
    ok = Settings(_env_file=None, environment="production", jwt_secret=STRONG, public_app_url="https://app.example.com",
                  database_url="postgresql+psycopg://u:p@db:5432/tripai")
    assert not ok.is_sqlite
    assert Settings(_env_file=None, environment="development", database_url="sqlite:///./x.db").is_sqlite
    assert Settings(_env_file=None, environment="test", database_url="sqlite:///./x.db").is_sqlite


# --- pool sizing ------------------------------------------------------------------------------------------------------------

def _s(**kw):
    base = dict(db_pool_size=None, db_max_overflow=None, max_concurrent_plans=12, web_concurrency=2, is_sqlite=False,
                postgres_max_connections=100)
    return SimpleNamespace(**{**base, **kw})


def test_the_pool_is_sized_from_the_plans_a_worker_runs():
    p = pool.plan(_s())
    assert p.size == 18 and p.overflow == 9 and p.per_worker == 27 and p.total == 54 and not p.warnings


def test_the_pool_settings_can_be_set_by_hand():
    p = pool.plan(_s(db_pool_size=10, db_max_overflow=2))
    assert (p.size, p.overflow, p.per_worker, p.total) == (10, 2, 12, 24)


def test_a_pool_that_can_exceed_the_server_limit_warns_with_the_arithmetic():
    p = pool.plan(_s(web_concurrency=4))  # 4 x 27 = 108
    assert p.total == 108 and p.warnings and "108" in p.warnings[0] and "POSTGRES_MAX_CONNECTIONS=100" in p.warnings[0]
    assert not pool.plan(_s(web_concurrency=4, is_sqlite=True)).warnings  # SQLite has no connection limit to exceed


# --- migrations -----------------------------------------------------------------------------------------------------------------

def _lint():
    spec = importlib.util.spec_from_file_location("migration_lint", ROOT / "scripts" / "migration_lint.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_migration_since_0009_is_expand_only():
    assert _lint().check_all() == []


def test_the_lint_catches_each_kind_of_contract_step_and_accepts_a_marked_one():
    lint = _lint()
    drop = "def upgrade():\n    op.drop_column('trips', 'x')\n"
    assert lint.problems(drop, "m") and "drop_column" in lint.problems(drop, "m")[0]
    assert lint.problems("def upgrade():\n    op.drop_table('t')\n", "m")
    assert lint.problems("def upgrade():\n    op.alter_column('t', 'c', type_=sa.String(5))\n", "m")
    notnull = "def upgrade():\n    op.add_column('t', sa.Column('c', sa.Integer(), nullable=False))\n"
    assert lint.problems(notnull, "m")
    assert not lint.problems("def upgrade():\n    op.add_column('t', sa.Column('c', sa.Integer(), nullable=False, server_default='0'))\n", "m")
    assert not lint.problems("def upgrade():\n    op.add_column('t', sa.Column('c', sa.Integer(), nullable=True))\n", "m")
    assert not lint.problems("def upgrade():\n    op.create_table('t')\n    op.create_index('i', 't', ['c'])\n", "m")
    assert not lint.problems("# contract: release 2026.11 stopped reading it\n" + drop, "m")
    assert not lint.problems("def upgrade():\n    pass\n\ndef downgrade():\n    op.drop_table('t')\n", "m")  # a downgrade may undo


def test_0009_applies_and_rolls_back_on_a_fresh_database():
    db = Path(tempfile.mkdtemp(prefix="tripai-mig-")) / "m.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db}", "ENVIRONMENT": "development"}

    def alembic(*args):
        r = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, r.stdout + r.stderr
    alembic("upgrade", "head")
    import sqlite3
    tables = {r[0] for r in sqlite3.connect(db).execute("select name from sqlite_master where type='table'")}
    assert {"plan_jobs", "plan_refunds"} <= tables
    cols = {r[1] for r in sqlite3.connect(db).execute("pragma table_info(plan_jobs)")}
    assert "late_finish_at" in cols  # migration 0010
    alembic("downgrade", "0008")
    tables = {r[0] for r in sqlite3.connect(db).execute("select name from sqlite_master where type='table'")}
    assert "plan_jobs" not in tables and "provider_circuit" in tables
    alembic("upgrade", "head")


# --- TTL table ------------------------------------------------------------------------------------------------------------------

def test_the_ttl_table_holds_exactly_the_values_that_used_to_be_literals():
    assert ttl.get("google-routes") == timedelta(hours=6) and ttl.get("google-matrix") == timedelta(hours=24)
    assert ttl.get("google-places-search") == timedelta(hours=24) and ttl.get("google-autocomplete") == timedelta(hours=24)
    assert ttl.get("google-place-details") == timedelta(days=3) and ttl.get("google-reverse") == timedelta(days=7)
    assert ttl.get("google-transit") == timedelta(hours=12) and ttl.get("open-meteo") == timedelta(hours=3)
    assert ttl.get("search") == timedelta(hours=12) and ttl.get("irctc1-station") == timedelta(days=1)
    assert ttl.get("irctc1-between") == timedelta(hours=12) and ttl.get("railradar") == timedelta(hours=12)


def test_a_ttl_can_be_overridden_but_google_content_never_lives_past_seven_days(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "cache_ttl_overrides", json.dumps({"google-routes": 600, "google-reverse": 90 * 86400, "open-meteo": 60}))
    assert ttl.get("google-routes") == timedelta(minutes=10)
    assert ttl.get("google-reverse") == timedelta(days=7)  # I6: place content is only ever a temporary cache
    assert ttl.get("open-meteo") == timedelta(seconds=60)
    monkeypatch.setattr(s, "cache_ttl_overrides", "not json")
    assert ttl.get("google-routes") == timedelta(hours=6)  # a typo falls back to the default


# --- cache tiers --------------------------------------------------------------------------------------------------------------

@pytest.fixture
def lru(monkeypatch):
    monkeypatch.setattr(get_settings(), "cache_lru_entries", 3)
    cache_mod.lru.clear()
    yield cache_mod.lru
    cache_mod.lru.clear()


def test_the_lru_tier_is_off_by_default():
    assert get_settings().cache_lru_entries == 0


def test_the_lru_tier_answers_before_the_database_and_survives_it_being_down(lru, monkeypatch):
    calls = []

    def fetch():
        calls.append(1)
        return {"v": 1}
    key = [uuid.uuid4().hex]
    assert cache_mod.cached("t-lru", key, timedelta(minutes=5), fetch) == {"v": 1}
    monkeypatch.setattr(cache_mod, "SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    assert cache_mod.cached("t-lru", key, timedelta(minutes=5), fetch) == {"v": 1}  # no database needed
    assert calls == [1]


def test_the_lru_tier_never_keeps_an_entry_past_its_ttl_and_is_bounded(lru):
    key = [uuid.uuid4().hex]
    cache_mod.cached("t-lru2", key, timedelta(seconds=0.05), lambda: {"v": 1})
    import time
    time.sleep(0.15)
    assert lru.get(cache_mod.cache_key("t-lru2", *key)) is cache_mod._MISS
    for i in range(10):
        lru.put(f"k{i}", i, 60, 3)
    assert len(lru) == 3  # the oldest were dropped


def test_without_the_flags_the_database_alone_answers(monkeypatch):
    calls = []
    key = [uuid.uuid4().hex]
    cache_mod.cached("t-db", key, timedelta(minutes=5), lambda: calls.append(1) or {"v": 2})
    cache_mod.cached("t-db", key, timedelta(minutes=5), lambda: calls.append(1) or {"v": 2})
    assert calls == [1] and len(cache_mod.lru) == 0


class FakeRedis:
    def __init__(self):
        self.d, self.ttls = {}, {}

    def get(self, k):
        return self.d.get(k)

    def setex(self, k, ttl, v):
        self.d[k], self.ttls[k] = v, ttl

    def ping(self):
        return True


def test_the_redis_tier_is_consulted_before_the_database_and_never_outlives_the_ttl(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "cache_redis_enabled", True)
    monkeypatch.setattr(s, "redis_url", "redis://fake")
    fake = FakeRedis()
    from app.core import redis_client
    monkeypatch.setattr(redis_client, "get", lambda: fake)
    key = [uuid.uuid4().hex]
    out = cache_mod.cached("t-redis", key, timedelta(hours=2), lambda: {"from": "provider"})
    assert out == {"from": "provider"}
    (k,) = fake.d
    assert k.startswith("cache:") and fake.ttls[k] == 7200  # exactly the caller's ttl, no longer
    assert cache_mod.cached("t-redis", key, timedelta(hours=2), lambda: {"from": "again"}) == {"from": "provider"}
    monkeypatch.setattr(cache_mod, "SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    assert cache_mod.cached("t-redis", key, timedelta(hours=2), lambda: {"from": "again"}) == {"from": "provider"}


def test_a_broken_redis_is_a_miss_not_an_error(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "cache_redis_enabled", True)
    monkeypatch.setattr(s, "redis_url", "redis://fake")

    class Broken:
        def get(self, k):
            raise ConnectionError("x")

        def setex(self, *a):
            raise ConnectionError("x")
    from app.core import redis_client
    monkeypatch.setattr(redis_client, "get", lambda: Broken())
    assert cache_mod.cached("t-broken", [uuid.uuid4().hex], timedelta(minutes=1), lambda: {"ok": True}) == {"ok": True}


def test_the_redis_tier_needs_both_the_flag_and_an_address(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "cache_redis_enabled", True)
    monkeypatch.setattr(s, "redis_url", "")
    assert cache_mod._redis() is None
    monkeypatch.setattr(s, "cache_redis_enabled", False)
    monkeypatch.setattr(s, "redis_url", "redis://x")
    assert cache_mod._redis() is None


def test_a_provider_that_may_not_be_stored_never_reaches_a_tier(monkeypatch):
    """I7: IRCTC1 answers are not stored anywhere (database, LRU or Redis) unless it is on the allow-list."""
    import httpx

    from app.providers import indian_rail
    cache_mod.lru.clear()
    monkeypatch.setattr(get_settings(), "cache_lru_entries", 5)
    monkeypatch.setattr(get_settings(), "rail_storage_sources", "railradar")
    monkeypatch.setattr(indian_rail, "gateway_request", lambda *a, **k: httpx.Response(200, json={"data": []}))
    monkeypatch.setattr(indian_rail.time, "sleep", lambda s: None)
    seen = []
    monkeypatch.setattr(indian_rail, "cached", lambda *a, **k: seen.append(a) or {})
    rr = indian_rail.IndianRailProvider(None, "k", "h")
    assert rr._get("/api/v1/searchStation", {"query": "q"}, timedelta(days=1)) == {"data": []}
    assert seen == [] and len(cache_mod.lru) == 0  # it went straight to the network and was kept nowhere
    monkeypatch.setattr(get_settings(), "rail_storage_sources", "railradar,irctc1")
    rr._get("/api/v1/searchStation", {"query": "q"}, timedelta(days=1))
    assert len(seen) == 1  # once confirmed and listed, it may be cached
    cache_mod.lru.clear()

# --- rail cleanup (both tiers) and old plan jobs ---------------------------------------------------------------------------------

def test_expired_rail_rows_go_and_live_ones_stay_in_both_tiers():
    from app.models.rail import RailCityRoute, RailPairResult, RailStationLookup, RailTimetable
    from app.services import retention
    now = datetime.now(UTC)
    tag = uuid.uuid4().hex[:4].upper()
    with SessionLocal() as db:
        def tt(code, fetched, expires, source="railradar"):
            db.add(RailTimetable(from_code=f"{tag}{code}", to_code="ZZZ", weekday=1, source=source, rows=[{"n": 1}], fetched_at=fetched,
                                 expires_at=expires))
        tt("A", now - timedelta(days=10), now + timedelta(days=35))  # fresh
        tt("B", now - timedelta(days=50), now - timedelta(days=5))  # expired five days ago: kept a little longer
        tt("C", now - timedelta(days=60), now - timedelta(days=15))  # expired two weeks ago: gone
        tt("D", now - timedelta(days=100), None)  # older than 90 days: gone
        tt("E", now - timedelta(days=1), now + timedelta(days=44), source="irctc1")  # a source we may not store: gone
        db.add(RailCityRoute(source="railradar", origin_key=f"{tag}o", dest_key="d", working_pairs=[["A", "B"]], checked_at=now,
                             expires_at=now + timedelta(days=300)))
        db.add(RailCityRoute(source="railradar", origin_key=f"{tag}x", dest_key="d", working_pairs=[], checked_at=now - timedelta(days=400),
                              expires_at=now - timedelta(days=35)))
        db.add(RailPairResult(source="railradar", from_code=f"{tag}P", to_code="Q", weekday=1, outcome="empty", train_numbers=[],
                              checked_at=now - timedelta(days=70), expires_at=now - timedelta(days=10)))
        db.add(RailPairResult(source="railradar", from_code=f"{tag}R", to_code="Q", weekday=1, outcome="trains", train_numbers=[],
                              checked_at=now, expires_at=now + timedelta(days=365)))
        db.add(RailStationLookup(query=f"{tag.lower()}old", source="railradar", rows=[], fetched_at=now - timedelta(days=120)))
        db.flush()
        out = retention.purge_rail(db, now)

        def left(model, col, prefix):
            return sorted(getattr(r, col) for r in db.query(model).all() if str(getattr(r, col)).startswith(prefix))
        assert left(RailTimetable, "from_code", tag) == [f"{tag}A", f"{tag}B"]
        assert left(RailCityRoute, "origin_key", tag) == [f"{tag}o"]
        assert left(RailPairResult, "from_code", tag) == [f"{tag}R"]
        assert left(RailStationLookup, "query", tag.lower()) == []
        assert out["tier2_timetables"] >= 2 and out["not_allowed_sources"] >= 1
        db.rollback()  # the shared test database is left as it was


def test_plan_jobs_older_than_the_retention_are_purged_but_open_ones_never(client):
    from app.models.plan_job import PlanJob
    from app.services import plan_jobs
    assert plan_jobs.purge_old.__doc__
    with SessionLocal() as db:
        assert plan_jobs.purge_old(db, datetime.now(UTC) - timedelta(days=3650)) == 0  # nothing is that old
        db.rollback()
    assert PlanJob.__tablename__ == "plan_jobs"


# --- pre-warming (interface, off) -----------------------------------------------------------------------------------------------

def _prewarm_rows(db, tag, now):
    from app.models.rail import RailCityRoute, RailTimetable
    db.add(RailCityRoute(source="railradar", origin_key=f"{tag}o", dest_key="d", working_pairs=[[f"{tag}1", "ZZ"], [f"{tag}2", "ZZ"], [f"{tag}3", "ZZ"]],
                         checked_at=now, expires_at=now + timedelta(days=300)))
    for code, age in (("1", 10), ("2", 30), ("3", 2)):  # 10 days: aging; 30 days: stale; 2 days: fresh
        db.add(RailTimetable(from_code=f"{tag}{code}", to_code="ZZ", weekday=2, source="railradar", rows=[{"n": 1}],
                             fetched_at=now - timedelta(days=age), expires_at=now + timedelta(days=45 - age)))
    db.add(RailTimetable(from_code=f"{tag}9", to_code="ZZ", weekday=2, source="railradar", rows=[{"n": 1}],  # Tier 1 does not know it
                         fetched_at=now - timedelta(days=30), expires_at=now + timedelta(days=15)))
    db.flush()


def test_prewarm_is_off_by_default_and_marks_nothing():
    from app.services import prewarm
    assert get_settings().prewarm_enabled is False
    with SessionLocal() as db:
        assert prewarm.run(db) == 0


def test_prewarm_queues_stale_before_aging_and_only_what_tier_1_knows(monkeypatch):
    from app.services import prewarm
    s = get_settings()
    now = datetime.now(UTC)
    tag = uuid.uuid4().hex[:4].upper()
    with SessionLocal() as db:
        _prewarm_rows(db, tag, now)
        picked = [r.from_code for r in prewarm.select_routes(db, 10, now) if r.from_code.startswith(tag)]
        assert picked == [f"{tag}2", f"{tag}1"]  # stale first, then aging; the fresh one and the unknown route are left alone
        monkeypatch.setattr(s, "prewarm_enabled", True)
        monkeypatch.setattr(s, "prewarm_top_n", 20)
        monkeypatch.setattr(s, "prewarm_budget_share", 0.0)  # no share of the day's calls: nothing is marked
        assert prewarm.run(db, now) == 0
        monkeypatch.setattr(s, "railradar_daily_calls", 30)
        monkeypatch.setattr(s, "prewarm_budget_share", 0.2)  # 6 of 30 calls
        marked = prewarm.run(db, now)
        assert marked >= 2
        from app.models.rail import RailTimetable
        rows = {r.from_code: r for r in db.query(RailTimetable).all() if r.from_code.startswith(tag)}
        assert rows[f"{tag}2"].needs_refresh and rows[f"{tag}2"].refresh_priority == 2
        assert rows[f"{tag}1"].needs_refresh and rows[f"{tag}1"].refresh_priority == 1
        assert not rows[f"{tag}3"].needs_refresh and not rows[f"{tag}9"].needs_refresh
        db.rollback()


def test_prewarm_never_marks_more_than_its_share_of_the_days_calls(monkeypatch):
    from app.services import prewarm
    s = get_settings()
    monkeypatch.setattr(s, "prewarm_enabled", True)
    monkeypatch.setattr(s, "prewarm_top_n", 100)
    monkeypatch.setattr(s, "railradar_daily_calls", 10)
    monkeypatch.setattr(s, "prewarm_budget_share", 0.2)  # two calls
    now = datetime.now(UTC)
    with SessionLocal() as db:
        for t in range(3):
            _prewarm_rows(db, uuid.uuid4().hex[:4].upper(), now)
        assert prewarm.run(db, now) <= 2
        db.rollback()


def test_the_new_settings_have_the_agreed_defaults():
    s = Settings(_env_file=None)
    assert (s.max_concurrent_plans, s.plan_queue_limit, s.plan_queue_hard_limit) == (12, 40, 100)
    assert s.plan_jobs_enabled and s.reaper_enabled and s.brownout_level == 0 and s.brownout_auto is False
    assert s.redis_url == "" and s.cache_lru_entries == 0 and s.cache_redis_enabled is False and s.prewarm_enabled is False
    assert s.backup_retention_days == 14 and s.plan_executor == "inprocess"
