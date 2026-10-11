"""The interest chips a traveller can pick ("Forts & palaces", "Street food"...), optional everywhere they
appear. This is the single source of truth: it's used to validate the `interests` field on trip creation and
preferences updates, and the same chip ids and tags are mirrored in the frontend
(frontend/lib/interests.ts) for the wizard's chip labels. Keep the two lists in step if either changes.

A picked chip expands to one or more of the place tags used in app.services.ranking (fort, temple, ...),
so it both re-ranks the places already found and (via app.providers.google.attractions) broadens the search
itself, rather than only reordering whatever the default search happened to return.
"""
from __future__ import annotations

INTEREST_CHIPS: dict[str, list[str]] = {
    "forts_palaces": ["fort", "palace", "royal"],
    "temples_spiritual": ["temple", "spiritual"],
    "nature_lakes": ["nature", "lake", "garden"],
    "street_food": ["street_food", "food"],
    "museums_art": ["museum", "photography"],
    "markets_shopping": ["market"],
    "adventure": ["adventure"],
    "nightlife": ["nightlife"],
    "heritage_history": ["heritage", "landmark"],
    "viewpoints_sunsets": ["viewpoint", "sunset"],
}

INTEREST_LABELS: dict[str, str] = {
    "forts_palaces": "forts & palaces", "temples_spiritual": "temples & spiritual sites",
    "nature_lakes": "nature & lakes", "street_food": "street food", "museums_art": "museums & art",
    "markets_shopping": "markets & shopping", "adventure": "adventure", "nightlife": "nightlife",
    "heritage_history": "heritage & history", "viewpoints_sunsets": "viewpoints & sunsets",
}

MAX_INTEREST_CHIPS = 6  # more than this stops being a useful signal and just means "show me everything"


def expand_chips(chip_ids: list[str] | None) -> list[str]:
    """Chip ids (as picked in the wizard) -> the underlying place tags, deduplicated. Unknown ids are
    dropped rather than rejected, so an older frontend build never breaks a request."""
    tags: list[str] = []
    for chip in (chip_ids or [])[:MAX_INTEREST_CHIPS]:
        tags += INTEREST_CHIPS.get(chip, [])
    return list(dict.fromkeys(tags))