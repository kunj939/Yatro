"""What kind of place a destination is, read from the sights found there: beaches (Goa, Bali), mountains (Manali),
a big city's landmarks (Paris, Dubai) or nature (Bharatpur's bird park, waterfalls).

The answer does two things. The place search adds one query for the destination's own character ("best beaches in
Goa", so the beaches are there to choose from), and ranking lifts the places that match it, so a beach trip is
planned around beaches rather than around whatever a generic "top attractions" search happened to return.
Read from tags only, so it works the same for every provider (Google, the demo data, the test simulator).
"""
from __future__ import annotations

import math

from app.providers.types import PlaceData

# A kind, the place tags that show it, and the search that finds more of it.
KINDS: dict[str, tuple[set[str], str]] = {
    "beach": ({"beach"}, "best beaches"),
    "mountain": ({"mountain"}, "mountain viewpoints, valleys and treks"),
    "city": ({"museum", "gallery", "observation"}, "famous landmarks and museums"),
    "nature": ({"national_park", "waterfall"}, "waterfalls, lakes and nature spots"),
}
# Heritage and temples are left out on purpose: they are what a "top attractions" search returns for most Indian towns
# anyway, so lifting them would only crowd out everything else. These four are the characters a generic search misses.
STRONG = 0.25  # a kind with at least this share of the (fame-weighted) sights shapes the plan
MAX_KINDS = 2


def _weight(p: PlaceData) -> float:
    return 1.0 + math.log10((p.rating_count or 0) + 1)  # a sight 10,000 people reviewed counts 5x a quiet one


def profile(places: list[PlaceData]) -> dict[str, float]:
    """{kind: share} for the kinds that stand out, strongest first (at most two). Empty when nothing does."""
    total = sum(_weight(p) for p in places)
    if not total:
        return {}
    share = {}
    for kind, (tags, _) in KINDS.items():
        hit = sum(_weight(p) for p in places if tags & set(p.tags))
        if hit / total >= STRONG:
            share[kind] = round(hit / total, 3)
    return dict(sorted(share.items(), key=lambda kv: -kv[1])[:MAX_KINDS])


def queries(kinds: dict[str, float]) -> list[str]:
    """The extra searches for a destination's character, strongest first."""
    return [KINDS[k][1] for k in kinds]


def boost(kinds: dict[str, float], tags: set[str]) -> tuple[float, str | None]:
    """Score lift for a sight that matches the destination's character: up to 0.3 for the strongest kind."""
    best, why = 0.0, None
    for n, (kind, share) in enumerate(kinds.items()):
        if KINDS[kind][0] & tags:
            lift = (0.3 if n == 0 else 0.18) * min(1.0, 0.5 + share)
            if lift > best:
                best, why = lift, kind
    return round(best, 4), why


REASON = {"beach": "one of the beaches this coast is known for", "mountain": "the mountains are why people come here",
          "city": "one of the city's signature sights", "nature": "the nature this place is known for"}
