"""Candidate ranking: deterministic, explainable scores (the reasons feed 'Why this?')."""
from __future__ import annotations

import math
from decimal import Decimal

from app.services import age_fit
from app.services.engine_types import Candidate, PlanInput

STYLE_TAGS: dict[str, dict[str, float]] = {
    "sightseeing": {"landmark": 1, "heritage": 1, "palace": 1, "fort": 1, "museum": .8, "architecture": .6, "photography": .5},
    "romantic": {"sunset": 1, "viewpoint": .9, "lake": .9, "garden": .8, "romantic": 1, "palace": .5, "rooftop": .7,
                 "fine_dining": .6, "cafe": .4},
    "adventure": {"fort": .8, "adventure": 1, "nature": .8, "viewpoint": .5, "zoo": .5},
    "food": {"street_food": 1, "market": .9, "food": 1, "local_favourite": 1, "cafe": .6},
    "relaxed": {"garden": 1, "lake": .9, "cafe": .8, "park": .8, "relaxed": 1, "spiritual": .5},
    "premium": {"palace": .8, "fine_dining": 1, "premium": 1, "heritage": .5, "royal": .8},
    "budget": {}, "balanced": {"landmark": .5, "heritage": .5},
}
TRAVELLER_TAGS: dict[str, dict[str, float]] = {
    "couple": {"romantic": .6, "sunset": .5, "lake": .3, "garden": .3},
    "partner": {"romantic": .7, "sunset": .6, "lake": .4, "garden": .4, "cafe": .3},
    "friends": {"adventure": .5, "street_food": .5, "market": .4, "fort": .3},
    "family": {"family": .6, "museum": .4, "park": .4, "garden": .3, "temple": .2},
    "solo": {"heritage": .4, "cafe": .3, "museum": .3, "photography": .3},
}
TAG_LABELS = {"sunset": "sunset views", "viewpoint": "views", "palace": "palace", "fort": "fort", "museum": "museum",
              "heritage": "heritage", "landmark": "landmark", "lake": "lakeside", "garden": "garden", "romantic": "romantic",
              "market": "market", "street_food": "street food", "adventure": "adventure", "temple": "temple",
              "cafe": "cafe", "fine_dining": "fine dining", "local_favourite": "local favourite", "royal": "royal",
              "spiritual": "spiritual sites", "nature": "nature", "photography": "photo spots", "nightlife": "nightlife"}

# An explicit pick (the wizard's "favourites" chips, or "I'd love to see some forts" in chat) is weighted
# above anything inferred from trip style or traveller type, whose weights top out at 1.0 in STYLE_TAGS /
# TRAVELLER_TAGS above: someone who says they love forts should see more forts than the "romantic" style alone
# would otherwise suggest.
EXPLICIT_INTEREST_WEIGHT = 1.6
EXPLICIT_INTEREST_BONUS = 0.16  # added straight to the score, so a match surfaces even if other scores are average
AVOID_PENALTY = 0.9  # scores run roughly 0..1.3, so this puts a place the traveller asked to avoid at the bottom


def _weights(inp: PlanInput) -> dict[str, float]:
    w: dict[str, float] = {}
    for style in inp.styles or ["balanced"]:
        for tag, v in STYLE_TAGS.get(style, {}).items():
            w[tag] = max(w.get(tag, 0), v)
    for tag, v in TRAVELLER_TAGS.get(inp.traveller_type, {}).items():
        w[tag] = max(w.get(tag, 0), v)
    for tag in inp.interests:
        w[tag] = EXPLICIT_INTEREST_WEIGHT
    return w


def _quality(rating: float | None, count: int | None) -> float:
    if not rating:
        return 0.35
    base = max(0.0, min(1.0, (rating - 3.5) / 1.5))
    confidence = min(1.0, math.log10((count or 1) + 1) / 4.5)
    return base * (0.55 + 0.45 * confidence)


def famous(count: int | None) -> float:
    """0..1 by how many people reviewed the place: 100k reviews = 1.0, 10k = 0.8, 500 = 0.54.
    A 4.5-star landmark visited by millions beats a 4.8-star spot with 300 reviews."""
    return min(1.0, math.log10((count or 0) + 1) / 5)


def score_attraction(c: Candidate, inp: PlanInput, activity_allowance: Decimal) -> None:
    w = _weights(inp)
    tags = set(c.place.tags)
    matched = sorted((t for t in tags if t in w), key=lambda t: -w[t])
    pref = min(1.0, sum(w[t] for t in matched) / 1.6) if w else 0.5
    quality = _quality(c.place.rating, c.place.rating_count)
    cost = c.group_cost(inp.travellers)
    cost_fit = 1.0 if cost == 0 else max(0.0, 1 - float(cost / max(activity_allowance, Decimal(1))))
    if "budget" in inp.styles:
        cost_fit = cost_fit ** 0.5 + (0.3 if cost == 0 else 0)
    fame = famous(c.place.rating_count)
    must_see = 0.3 if (c.place.rating_count or 0) >= 20_000 else 0.0  # Hawa Mahal, Amber Fort...
    picked = tags & set(inp.interests)  # tags matching the traveller's own explicit picks, not just inferred style
    avoided = (tags & inp.avoid_tags) and not c.pinned  # "no temples": ranked last unless asked for by name
    ages = getattr(inp, "age_groups", None) or ()  # who is travelling (services/age_fit.py); nothing changes without it
    age_delta, _ = age_fit.fit(ages, tags)
    c.score = round(0.35 * pref + 0.25 * quality + 0.25 * fame + 0.15 * cost_fit + must_see
                    + (EXPLICIT_INTEREST_BONUS if picked else 0) + (10 if c.pinned else 0)
                    - (AVOID_PENALTY if avoided else 0) + age_delta, 4)
    c.age_note = age_fit.reason(ages, tags)
    c.matched = [TAG_LABELS.get(t, t) for t in matched[:3]]
    c.matched_interests = [TAG_LABELS.get(t, t) for t in sorted(picked, key=lambda t: -w.get(t, 0))[:2]]
    if must_see:
        c.reasons.append(f"one of the best-known sights here ({c.place.rating_count:,} reviews)")


def score_restaurant(c: Candidate, inp: PlanInput, meal_allowance_pp: Decimal) -> None:
    r = c.place.restaurant
    tags = set(c.place.tags)
    w = _weights(inp)
    matched = sorted((t for t in tags if t in w), key=lambda t: -w[t])
    pref = min(1.0, sum(w[t] for t in matched) / 1.2) if w else 0.4
    if inp.effective_food == "veg" and r and r.veg_only:
        pref = min(1.0, pref + 0.25)
    quality = _quality(c.place.rating, c.place.rating_count)
    ratio = float(c.unit_cost / max(meal_allowance_pp, Decimal(1)))
    cost_fit = 1.0 if ratio <= 1 else max(0.0, 1.6 - ratio)
    if "premium" in inp.styles:
        cost_fit = min(1.0, 0.5 + ratio / 3)
    avoided = (tags & inp.avoid_tags) and not c.pinned
    c.score = round(0.35 * pref + 0.4 * quality + 0.25 * cost_fit + (10 if c.pinned else 0)
                    - (AVOID_PENALTY if avoided else 0), 4)
    c.matched = [TAG_LABELS.get(t, t) for t in matched[:2]]


def food_ok(c: Candidate, food: str) -> bool:
    r = c.place.restaurant
    if not r:
        return False
    if food == "veg":
        return r.serves_veg
    if food == "non_veg":
        return r.serves_non_veg
    return True


def serves(c: Candidate, meal: str) -> bool:
    r = c.place.restaurant
    if not r:
        return False
    if meal == "snack":
        return "snack" in r.meal_types or "breakfast" in r.meal_types
    return meal in r.meal_types