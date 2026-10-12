"""Google's places, with Wikidata's famous sights added (providers/wikidata.py).

Wikidata is asked first: it is free, kept for 30 days and shared by everyone going to the same place. Its answer goes to
the Google search as `known`, which then skips the searches Wikidata already covers. The two lists are merged so a
place both know appears once, with Google's rating and hours and Wikidata's fame; a place only Wikidata knows (Deeg
Palace, a Fatehpur Sikri gate) is added with our estimates. Everything else (restaurants, autocomplete, details) is
Google's, unchanged.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.providers import wikidata
from app.providers.types import PlaceData
from app.utils.geo import haversine_m

SAME_SPOT_M = 300      # this close, two entries are the same place whatever they're called
SAME_NAME_M = 2500     # a big site's pin differs by source (park centre vs gate): similar names this close are one place
_WORDS = re.compile(r"[^\w\s]")


def _norm(name: str) -> str:
    return " ".join(_WORDS.sub(" ", name.lower()).split())


def same_place(a: PlaceData, b: PlaceData) -> bool:
    d = haversine_m(a.lat, a.lng, b.lat, b.lng)
    if d <= SAME_SPOT_M:
        return True
    if d > SAME_NAME_M:
        return False
    x, y = _norm(a.name), _norm(b.name)
    return x in y or y in x or SequenceMatcher(None, x, y).ratio() >= 0.75  # "Kevladev National Park" ~ "Keoladeo …"


def merge(google: list[PlaceData], wiki: list[PlaceData], limit: int = 40) -> list[PlaceData]:
    out = list(google)
    for w in wiki:
        twin = next((g for g in google if same_place(g, w)), None)
        if twin:
            twin.meta = {**(twin.meta or {}), "wiki_links": w.meta.get("wiki_links"), "wikidata": w.meta.get("wikidata")}
            twin.description = twin.description or w.description
            for tag in w.tags:  # Wikidata knows it's a national park even when Google only says "tourist attraction"
                if tag not in twin.tags and tag != "landmark":
                    twin.tags.append(tag)
        else:
            out.append(w)
    return out[:limit]


class WikiAwarePlaces:
    """A places provider: Google's, with attractions() widened by Wikidata.

    Free plans: Wikidata first, and Google only for what it lacks (fewer paid searches). Plus plans (`thorough`): every
    Google search as well, with Wikidata's extras merged in, since they cost nothing and only add famous places."""

    supports_thorough = True

    def __init__(self, base):
        self.base = base
        self.name = f"{base.name}+wikidata"
        self.live = getattr(base, "live", False)

    def __getattr__(self, item):
        return getattr(self.base, item)

    def attractions(self, city: PlaceData, interests: list[str], thorough: bool = False) -> list[PlaceData]:
        wiki = wikidata.sights(city)
        google = self.base.attractions(city, interests) if thorough else self.base.attractions(city, interests, known=wiki)
        return merge(google, wiki)
