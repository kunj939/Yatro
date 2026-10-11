"""Profile one real plan in-process (Phase 0, ADR 0001): where does the CPU time go?

    python scripts/profile_plan.py --db path/to/copy-of-tripai.db --from Jaipur --to "New Delhi" [--days 1]

Run it against a COPY of a database (warm caches, few API calls). Thread pools are made serial for the run so every
function shows up in one profile.
"""
from __future__ import annotations

import argparse
import cProfile
import os
import pstats
import sys
from concurrent.futures import Future
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SerialPool:
    """Stand-in for ContextThreadPool that runs every task inline, so cProfile sees it."""

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, fn, *args, **kwargs):
        f: Future = Future()
        try:
            f.set_result(fn(*args, **kwargs))
        except Exception as e:  # noqa: BLE001
            f.set_exception(e)
        return f

    def map(self, fn, *iterables):
        return [fn(*a) for a in zip(*iterables)]

    def shutdown(self, *a, **k):
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--from", dest="origin", default="Jaipur")
    ap.add_argument("--to", dest="dest", default="New Delhi")
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--top", type=int, default=30)
    a = ap.parse_args()
    os.environ.update({"DATABASE_URL": f"sqlite:///{Path(a.db).resolve()}", "FREE_TRIP_PLANS": "500", "LOG_LEVEL": "WARNING",
                       "TELEMETRY_ENABLED": "true"})
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)

    from app.db.session import SessionLocal
    from app.models.user import User
    from app.providers import google, rail_chain
    from app.providers.registry import get_providers
    from app.schemas.trip import TripCreate
    from app.services import quota_service, route_optimizer, verification
    from app.services import trip_service as TS
    for mod in (route_optimizer, verification, google, rail_chain):
        mod.ContextThreadPool = SerialPool

    d = date.today() + timedelta(days=14)
    d += timedelta(days=(5 - d.weekday()) % 7)
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "profile@example.com").first()
        if user is None:
            user = User(email="profile@example.com", name="Profile", password_hash="x")
            db.add(user)
            db.commit()
        trip = TS.create_trip(db, user, TripCreate(**{
            "origin": {"name": a.origin}, "destination": {"name": a.dest}, "start_date": d.isoformat(),
            "end_date": (d + timedelta(days=a.days - 1)).isoformat(), "start_time": "05:00", "end_time": "23:30",
            "budget": 8000 * a.days, "travellers": 2, "traveller_type": "friends", "transport_modes": ["train", "bus", "metro"],
            "food": "both", "trip_styles": ["balanced"], "stay": "budget" if a.days > 1 else "no_stay"}))
        quota_service.reserve(db, user, trip)
        db.commit()
        prof = cProfile.Profile()
        prof.enable()
        result = TS.run_plan(db, get_providers(), user, trip)
        prof.disable()
        print("status:", result.status)
    st = pstats.Stats(prof).strip_dirs()
    print("\n=== by cumulative time ===")
    st.sort_stats("cumulative").print_stats(a.top)
    print("\n=== by own time ===")
    st.sort_stats("tottime").print_stats(a.top)


if __name__ == "__main__":
    main()
