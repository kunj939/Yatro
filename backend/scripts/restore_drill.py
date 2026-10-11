"""Restore drill (ADR 0008): prove a backup can be restored, and that what comes back is usable.

    python scripts/restore_drill.py --via-docker                       # dump the compose database now, restore it, check it
    python scripts/restore_drill.py --via-docker --dump backup.dump    # restore a dump file (e.g. from the pgbackups volume)
    python scripts/restore_drill.py --host db.example.com --user tripai --database tripai   # PostgreSQL tools on this machine

It never touches the source database except to read it. The restore goes into a scratch database that is dropped at the end.
Exit code 0 and "RESTORE DRILL: PASS" mean: the dump restored, the schema is at the current migration, row counts match the
source (when the dump was made by this run), no refund points at a missing plan job, and a saved trip loads with its itinerary.

The same checks work whichever backup you use later (a daily pg_dump, or a managed host's point-in-time restore: restore to a
new instance, then run this with --host pointing at it and no --dump).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY_TABLES = ["users", "trips", "itinerary", "itinerary_items", "plan_jobs", "plan_refunds", "share_links", "rail_timetables"]


def head_revision() -> str:
    revs = sorted(p.name.split("_")[0] for p in (ROOT / "alembic" / "versions").glob("[0-9]*.py"))
    return revs[-1]


class Pg:
    def __init__(self, a: argparse.Namespace):
        self.a = a
        self.env = {**os.environ, "PGPASSWORD": a.password or os.environ.get("PGPASSWORD", os.environ.get("POSTGRES_PASSWORD", "tripai"))}

    def cmd(self, tool: str, *args: str) -> list[str]:
        if self.a.via_docker:
            return ["docker", "compose", "exec", "-T", "-e", f"PGPASSWORD={self.env['PGPASSWORD']}", "db", tool, "-U", self.a.user, *args]
        return [tool, "-h", self.a.host, "-p", str(self.a.port), "-U", self.a.user, *args]

    def run(self, tool: str, *args: str, data: bytes | None = None) -> bytes:
        r = subprocess.run(self.cmd(tool, *args), input=data, capture_output=True, env=self.env, cwd=ROOT.parent)
        if r.returncode != 0:
            raise RuntimeError(f"{tool} failed: {r.stderr.decode(errors='replace')[:400]}")
        return r.stdout

    def scalar(self, db: str, sql: str) -> str:
        return self.run("psql", "-d", db, "-At", "-c", sql).decode().strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--via-docker", action="store_true", help="run pg_dump/pg_restore/psql inside the compose `db` container")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--user", default="tripai")
    ap.add_argument("--password", default=None)
    ap.add_argument("--database", default="tripai", help="the source database")
    ap.add_argument("--dump", help="restore this dump file instead of dumping the source now")
    a = ap.parse_args()
    pg = Pg(a)
    scratch = f"restore_drill_{int(time.time())}"
    checks: list[tuple[str, bool, str]] = []
    try:
        if a.dump:
            dump = Path(a.dump).read_bytes()
            source_counts = None
            checks.append(("dump file read", len(dump) > 0, f"{len(dump)} bytes"))
        else:
            dump = pg.run("pg_dump", "-Fc", "-d", a.database)
            source_counts = {t: pg.scalar(a.database, f"select count(*) from {t}") for t in KEY_TABLES}
            checks.append(("dump of the source taken", len(dump) > 0, f"{len(dump)} bytes"))
        pg.run("psql", "-d", "postgres", "-c", f'create database "{scratch}"')
        pg.run("pg_restore", "-d", scratch, "--no-owner", "--exit-on-error", data=dump)
        checks.append(("restored into a scratch database", True, scratch))
        version = pg.scalar(scratch, "select version_num from alembic_version")
        checks.append(("schema at the current migration", version == head_revision(), f"restored {version}, code expects {head_revision()}"))
        counts = {t: pg.scalar(scratch, f"select count(*) from {t}") for t in KEY_TABLES}
        if source_counts is not None:
            bad = {t: (source_counts[t], counts[t]) for t in KEY_TABLES if source_counts[t] != counts[t]}
            checks.append(("row counts match the source", not bad, f"{bad or counts}"))
        else:
            checks.append(("tables present", all(re.fullmatch(r"\d+", v) for v in counts.values()), f"{counts}"))
        orphans = pg.scalar(scratch, "select count(*) from plan_refunds r left join plan_jobs j on j.id = r.job_id where j.id is null")
        checks.append(("no refund without its plan job", orphans == "0", f"{orphans} orphans"))
        loaded = pg.scalar(scratch, "select count(*) from (select t.id from trips t join itinerary i on i.trip_id = t.id "
                                    "join itinerary_items it on it.itinerary_id = i.id limit 1) x")
        trips = counts["trips"]
        checks.append(("a saved trip loads with its itinerary", loaded == "1" or trips == "0",
                       "ok" if loaded == "1" else ("no trips in this backup" if trips == "0" else "no trip had an itinerary")))
    except Exception as exc:  # noqa: BLE001
        checks.append(("drill ran", False, str(exc)))
    finally:
        try:
            pg.run("psql", "-d", "postgres", "-c", f'drop database if exists "{scratch}"')
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: could not drop {scratch}: {exc}")
    for name, ok, detail in checks:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}: {detail}")
    passed = all(ok for _, ok, _ in checks)
    print("RESTORE DRILL:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
