"""Who is travelling, by age, and what that changes in a plan.

The wizard asks which age groups are in the party (kids 0-12, teens 13-17, adults 18-59, seniors 60+). Nothing is stored per
person: a group of ages is enough to decide what to show, and it is never a reason to refuse a plan.

What it changes, all of it deterministic and explainable (the reasons feed "Why this?"):

* **Hard rule.** Places for adults only (night clubs, bars, casinos) are never planned for a party with anyone under 18, unless the
  traveller pinned that exact place by name.
* **Ranking.** Each kind of place (a tag such as zoo, garden, temple, adventure) gets a small adjustment per age group. The strictest
  member rules: if a place suits one group badly (a trek for a 70-year-old) the whole party's score drops; otherwise the best fit
  among the groups lifts it. The sums are small next to the ranking's own terms (scores run roughly 0..1.3), so age re-orders
  close calls and demotes bad fits; it does not override a must-see the traveller pinned.
* **Pace and walking.** Children and older travellers get a relaxed pace (fewer stops a day, longer at each) unless the traveller chose
  a pace themselves, and shorter walks between stops before a ride is used instead.
"""
from __future__ import annotations

from collections.abc import Iterable

AGE_GROUPS: tuple[str, ...] = ("kids", "teens", "adults", "seniors")

# What the traveller sees in the wizard and in the plan.
LABELS = {"kids": "children", "teens": "teenagers", "adults": "adults", "seniors": "older travellers"}
BANDS = {"kids": "0-12", "teens": "13-17", "adults": "18-59", "seniors": "60+"}

# Places that are not for under-18s (tags from providers.google.TYPE_TAGS and the traveller's "nightlife" favourite).
ADULTS_ONLY_TAGS = frozenset({"nightlife"})
ADULTS_ONLY_TYPES = frozenset({"night_club", "casino"})
# A "bar" that is also one of these is a restaurant that happens to serve drinks (most Indian restaurants list "bar"), not a bar.
NOT_JUST_A_BAR = frozenset({"restaurant", "cafe", "food", "meal_takeaway", "meal_delivery", "bakery", "tourist_attraction"})

# Adjustment to a place's score, per tag, per group. Positive = suits them well; negative = a poor fit.
FIT: dict[str, dict[str, float]] = {
    "kids": {"zoo": .35, "family": .30, "park": .25, "garden": .15, "lake": .10, "museum": .05, "nature": .05, "cafe": .05,
             "fine_dining": -.15, "nightlife": -.50},
    "teens": {"adventure": .25, "market": .10, "street_food": .10, "cafe": .10, "photography": .10, "museum": .05, "zoo": -.05,
              "nightlife": -.50},
    "adults": {},
    "seniors": {"garden": .25, "temple": .20, "spiritual": .15, "park": .15, "lake": .15, "museum": .10, "heritage": .05,
                "fort": -.15, "adventure": -.45, "nightlife": -.50},
}
MAX_LIFT, MAX_DROP = 0.4, -0.5

# What each group adds to the search itself (keys of providers.google.GooglePlacesProvider.attractions' hints), so a trip with
# children actually has family places in the pool to rank, not just whatever the default query returned.
SEARCH_HINT = {"kids": "family", "seniors": "relaxed", "teens": "adventure"}

# Why a place was chosen, for the person who asked for age-aware plans. Only when the adjustment is a real lift.
WHY = {"kids": "good for children", "teens": "popular with teenagers", "seniors": "easy-going, suits older travellers"}
LIFT_TO_EXPLAIN = 0.1


def clean(groups: Iterable[str] | None) -> list[str]:
    """Known groups only, in a fixed order, no repeats. Unknown values are dropped rather than rejected (like interest chips)."""
    have = set(groups or ())
    return [g for g in AGE_GROUPS if g in have]


def has_minors(groups: Iterable[str] | None) -> bool:
    g = set(groups or ())
    return bool(g & {"kids", "teens"})


def adults_only(tags: Iterable[str], types: Iterable[str] = ()) -> bool:
    """Is this place meant for adults only? Judged from provider types first (a restaurant with a bar is not a bar), then tags."""
    t, ty = set(tags or ()), set(types or ())
    if ty & ADULTS_ONLY_TYPES:
        return True
    if "bar" in ty and not (ty & NOT_JUST_A_BAR):
        return True
    return bool(t & ADULTS_ONLY_TAGS) and not (ty & NOT_JUST_A_BAR)


def allowed(groups: Iterable[str] | None, tags: Iterable[str], types: Iterable[str] = (), pinned: bool = False) -> bool:
    """False for a place that must not be planned for this party. A place the traveller pinned by name is always allowed."""
    return pinned or not (has_minors(groups) and adults_only(tags, types))


def fit(groups: Iterable[str] | None, tags: Iterable[str]) -> tuple[float, str | None]:
    """(score adjustment, group it favours or None) for a place with these tags. 0 with no age information or adults only."""
    gs = clean(groups)
    if not gs:
        return 0.0, None
    t = set(tags or ())
    per = {g: max(MAX_DROP, min(MAX_LIFT, sum(v for k, v in FIT[g].items() if k in t))) for g in gs}
    worst = min(per.values())
    if worst < 0:  # the strictest member rules
        return round(worst, 4), None
    best_group = max(per, key=lambda g: per[g])
    best = per[best_group]
    return (round(best, 4), best_group) if best > 0 else (0.0, None)


def reason(groups: Iterable[str] | None, tags: Iterable[str]) -> str | None:
    delta, group = fit(groups, tags)
    return WHY.get(group) if group and delta >= LIFT_TO_EXPLAIN else None


def effective_pace(pace: str | None, groups: Iterable[str] | None) -> str:
    """The pace the plan is built at. "normal" (the default, never asked in the wizard) becomes "relaxed" with children or older
    travellers; "relaxed" and "packed" were chosen by the traveller and stay."""
    pace = pace or "normal"
    return "relaxed" if pace == "normal" and set(groups or ()) & {"kids", "seniors"} else pace


def walk_limit_m(groups: Iterable[str] | None, default: int) -> int:
    """How far a party walks between stops before a ride is used: shorter with children or older travellers."""
    return min(default, 500) if set(groups or ()) & {"kids", "seniors"} else default


def search_hints(groups: Iterable[str] | None) -> list[str]:
    """Search-hint keys to put in front of the trip's own, so family or easy-going places are in the pool being ranked."""
    gs = clean(groups)
    for g in ("kids", "seniors", "teens"):  # one extra search at most, for the group that narrows the choice the most
        if g in gs:
            return [SEARCH_HINT[g]]
    return []


def describe(groups: Iterable[str] | None) -> str:
    """"with children and older travellers" for the planning summary; empty when adults only or unknown."""
    gs = [g for g in clean(groups) if g != "adults"]
    if not gs:
        return ""
    names = [LABELS[g] for g in gs]
    return "with " + (names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1])
