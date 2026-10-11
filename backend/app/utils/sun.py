"""NOAA sunrise/sunset approximation — the 'Sunset' stop is scheduled from math, not from the LLM."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta


def sun_times(day: date, lat: float, lng: float, utc_offset_minutes: int = 330) -> tuple[datetime, datetime]:
    """Return local (sunrise, sunset) as naive datetimes."""
    n = day.timetuple().tm_yday
    gamma = 2 * math.pi / 365 * (n - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma) - 0.006758 * math.cos(2 * gamma)
            + 0.000907 * math.sin(2 * gamma) - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    lat_r = math.radians(lat)
    cos_ha = (math.cos(math.radians(90.833)) / (math.cos(lat_r) * math.cos(decl))) - math.tan(lat_r) * math.tan(decl)
    cos_ha = max(-1.0, min(1.0, cos_ha))
    ha = math.degrees(math.acos(cos_ha))
    base = datetime(day.year, day.month, day.day)
    sunrise_utc_min = 720 - 4 * (lng + ha) - eqtime
    sunset_utc_min = 720 - 4 * (lng - ha) - eqtime
    return (base + timedelta(minutes=sunrise_utc_min + utc_offset_minutes),
            base + timedelta(minutes=sunset_utc_min + utc_offset_minutes))
