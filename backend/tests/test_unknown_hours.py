"""Regression: the Taj Mahal (no published hours) was planned for 18:51-20:36. A sight with unknown hours is assumed
to close at 18:00; a market or food street keeps the later window."""
from datetime import date, datetime

from app.utils import hours as H

DAY = date(2026, 10, 10)


def test_a_monument_with_unknown_hours_is_not_planned_after_dark():
    close = H.assumed_close(["heritage", "landmark"])
    assert not H.fits(None, DAY, datetime(2026, 10, 10, 18, 51), datetime(2026, 10, 10, 20, 36), close)[0]
    assert H.fits(None, DAY, datetime(2026, 10, 10, 15, 0), datetime(2026, 10, 10, 17, 0), close)[0]


def test_a_market_with_unknown_hours_can_still_be_an_evening_stop():
    close = H.assumed_close(["market"])
    assert H.fits(None, DAY, datetime(2026, 10, 10, 19, 0), datetime(2026, 10, 10, 20, 0), close)[0]


def test_published_hours_always_win():
    hours = {"weekly": {str(DAY.weekday()): [["06:00", "22:00"]]}}
    assert H.fits(hours, DAY, datetime(2026, 10, 10, 19, 0), datetime(2026, 10, 10, 20, 30), H.SIGHT_CLOSE)[0]
