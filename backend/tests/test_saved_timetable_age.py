"""Invariant I1: when every live rail source is down, a saved timetable is used only while it is at most 45 days old.
Older copies are never shown as train facts; the plan falls back to the station-network estimate instead."""
from datetime import UTC, datetime, timedelta

from app.db.session import SessionLocal
from app.models.rail import RailTimetable
from app.providers.rail_common import MAX_SAVED_TIMETABLE_AGE, RailCache


def _save(frm: str, age_days: int) -> None:
    with SessionLocal() as db:
        db.add(RailTimetable(source="railradar", from_code=frm, to_code="NDLS", weekday=5, rows=[{"train": "x"}],
                             fetched_at=datetime.now(UTC) - timedelta(days=age_days)))
        db.commit()


def test_a_saved_timetable_within_45_days_can_stand_in_for_a_down_api():
    _save("AGE40", 40)
    got = RailCache("railradar").timetable_any_age("AGE40", "NDLS", 5)
    assert got is not None and got[0] == [{"train": "x"}]


def test_a_saved_timetable_older_than_45_days_is_never_used():
    assert MAX_SAVED_TIMETABLE_AGE == timedelta(days=45)
    _save("AGE46", 46)
    assert RailCache("railradar").timetable_any_age("AGE46", "NDLS", 5) is None
