"""Deterministic geometry helpers used as a fallback when the Routes API is unavailable."""
from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_000
ROAD_FACTOR = 1.35  # typical road distance / straight-line distance in Indian cities


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def estimate_road(lat1: float, lng1: float, lat2: float, lng2: float, mode: str = "drive") -> tuple[int, int]:
    """Return (distance_m, duration_s) estimated from straight-line distance."""
    straight = haversine_m(lat1, lng1, lat2, lng2)
    if straight < 30:
        return 0, 0
    road = straight * ROAD_FACTOR
    if mode == "walk":
        speed = 4.5
    else:
        km = road / 1000
        speed = 20 if km < 15 else (38 if km < 60 else 55)  # city traffic vs highway
    return int(road), int(road / 1000 / speed * 3600) + (0 if mode == "walk" else 240)


def encode_polyline(points: list[tuple[float, float]]) -> str:
    """Google encoded polyline (precision 5) — lets the fallback path render like a real route."""
    out, prev_lat, prev_lng = [], 0, 0
    for lat, lng in points:
        ilat, ilng = int(round(lat * 1e5)), int(round(lng * 1e5))
        for v in (ilat - prev_lat, ilng - prev_lng):
            v = ~(v << 1) if v < 0 else (v << 1)
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        prev_lat, prev_lng = ilat, ilng
    return "".join(out)
