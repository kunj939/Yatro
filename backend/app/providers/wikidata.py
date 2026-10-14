"""Famous sights from Wikidata: free, and CC0, so the results may be stored (unlike Google content, I6).

One SPARQL query per destination, kept for 30 days and shared by every traveller going there. It finds the places that
have a Wikipedia page somewhere — forts, palaces, temples, national parks, museums, beaches, waterfalls — and how many
language editions have one (`sitelinks`), which is a good stand-in for how well known a place is: Fatehpur Sikri 85,
Keoladeo 56, Deeg Palace 5. Neighbourhood parks are never on Wikidata, so they never come from here.

What it does not have: ratings, opening hours, entry fees. Google fills those in for the places it also finds; a place
only Wikidata knows keeps our estimates, labelled "Estimated", like any other estimate.
"""
from __future__ import annotations

import logging
import math
import re

from app.providers import http
from app.providers import ttl as ttls
from app.providers.cache import cached
from app.providers.types import PlaceData

log = logging.getLogger("tripai.wikidata")

SPARQL = "https://query.wikidata.org/sparql"
# Wikimedia asks for a User-Agent naming the tool and a way to reach its owner; without one it answers 403.
USER_AGENT = "YaitroTripPlanner/1.0 (https://yatro.vercel.app; hello@yaitro.com)"

# Wikidata class -> our place tags (the same tags Google types become, providers/google.py).
TYPES: dict[str, list[str]] = {
    "Q57821": ["fort", "heritage"],            # fortification
    "Q16560": ["palace", "heritage"],          # palace
    "Q842402": ["temple", "spiritual"],        # Hindu temple
    "Q5393308": ["temple", "spiritual"],       # Buddhist temple
    "Q44613": ["spiritual", "heritage"],       # monastery
    "Q16970": ["spiritual", "heritage"],       # church building
    "Q2977": ["spiritual", "heritage"],        # cathedral
    "Q32815": ["spiritual", "heritage"],       # mosque
    "Q4989906": ["heritage", "landmark"],      # monument
    "Q33506": ["museum"],                      # museum
    "Q207694": ["museum", "gallery"],          # art museum
    "Q46169": ["national_park", "nature"],     # national park
    "Q179049": ["national_park", "nature"],    # nature reserve
    "Q40080": ["beach", "sunset", "nature"],   # beach
    "Q34038": ["waterfall", "nature"],         # waterfall
    "Q23397": ["lake", "nature"],              # lake
    "Q8502": ["mountain", "viewpoint"],        # mountain
    "Q39816": ["mountain", "nature"],          # valley
    "Q133056": ["mountain", "viewpoint"],      # mountain pass
    "Q6017969": ["viewpoint"],                 # scenic viewpoint
    "Q35509": ["nature", "adventure"],         # cave
    "Q12518": ["landmark", "observation"],     # tower
    "Q174782": ["landmark"],                   # town square
    "Q1107656": ["garden"],                    # garden
    "Q570116": ["landmark"],                   # tourist attraction
}
_QID = re.compile(r"^Q\d+$")


def _query(lat: float, lng: float, radius_km: int, limit: int) -> str:
    values = " ".join(f"wd:{q}" for q in TYPES)
    return f"""SELECT ?item ?itemLabel ?itemDescription (SAMPLE(?t) AS ?type) (SAMPLE(?loc) AS ?where) (MAX(?links) AS ?n) WHERE {{
  SERVICE wikibase:around {{ ?item wdt:P625 ?loc . bd:serviceParam wikibase:center "Point({lng} {lat})"^^geo:wktLiteral .
                            bd:serviceParam wikibase:radius "{radius_km}" . }}
  ?item wdt:P31/wdt:P279? ?t . VALUES ?t {{ {values} }}
  ?item wikibase:sitelinks ?links .
  FILTER NOT EXISTS {{ ?item wdt:P576 ?gone }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}} GROUP BY ?item ?itemLabel ?itemDescription ORDER BY DESC(?n) LIMIT {limit}"""


def _point(wkt: str) -> tuple[float, float] | None:
    m = re.match(r"Point\(([-\d.]+) ([-\d.]+)\)", wkt or "")
    return (float(m.group(2)), float(m.group(1))) if m else None


def sights(city: PlaceData, radius_km: int = 30, limit: int = 40) -> list[PlaceData]:
    """The best-known sights within `radius_km` of the destination, most famous first. [] on any failure: Wikidata only
    adds to what Google finds, so a slow or unavailable Wikidata never stops a plan."""
    q = _query(round(city.lat, 3), round(city.lng, 3), radius_km, limit)

    def fetch():
        r = http.request("GET", SPARQL, provider="Wikidata", params={"query": q, "format": "json"},
                         headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"})
        return r.json()["results"]["bindings"]

    try:
        rows = cached("wikidata", [q], ttls.get("wikidata"), fetch)
    except Exception as exc:  # noqa: BLE001 - optional source: log and carry on with Google alone
        log.warning("Wikidata sights for %s unavailable: %s", city.name, exc)
        return []
    out = []
    for b in rows:
        name = b.get("itemLabel", {}).get("value", "")
        where = _point(b.get("where", {}).get("value", ""))
        qid = b["item"]["value"].rsplit("/", 1)[-1]
        if not name or _QID.match(name) or not where:
            continue  # no English name (it would read "Q7130214") or no position
        tags = TYPES.get(b.get("type", {}).get("value", "").rsplit("/", 1)[-1], ["landmark"])
        links = int(b.get("n", {}).get("value", 0) or 0)
        out.append(PlaceData("wikidata", f"wd:{qid}", name, "attraction", where[0], where[1], city=city.name,
                             tags=list(tags), types=["wikidata"], description=(b.get("itemDescription") or {}).get("value"),
                             meta={"wiki_links": links, "wikidata": qid}))
    return out


def fame_count(p: PlaceData) -> int | None:
    """A review-count scale for ranking a place Google hasn't rated: a sight with a Wikipedia page in 85 languages is
    as well known as one with about 30,000 reviews, one with a single page as one with about 300."""
    if p.rating_count is not None:
        return p.rating_count
    links = (p.meta or {}).get("wiki_links")
    if not links:
        return None
    return int(10 ** (2.11 + 2.39 * min(1.0, math.log10(links + 1) / 1.95)))  # 1 page ≈ 300, 85 pages ≈ 30,000
