"""A photo of any destination, from Wikimedia Commons (free to use with credit), for trip cards and the trip header.

Wikidata knows each city's main image (P18) and, often, the Wikivoyage banner (P948). We take the first that isn't a
map, flag, seal or collage, then ask Commons for a 1600 px copy and who made it, so the credit can be shown. One
lookup per destination, kept 30 days and shared by everyone going there; a miss is kept too, so it isn't retried.
The eight cities with our own photos (frontend lib/photos.ts) never get here.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from html import unescape

from app.core.config import get_settings
from app.core.errors import ProviderUnavailable
from app.providers import http
from app.providers.cache import cached
from app.providers.wikidata import USER_AGENT
from app.utils.geo import haversine_m

log = logging.getLogger("tripai.photos")

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
NOT_A_PHOTO = re.compile(r"(map|flag|seal|emblem|logo|coat[_ ]of[_ ]arms|locator|location|collage|montage|\.svg$)", re.I)
TTL = timedelta(days=30)
HEADERS = {"User-Agent": USER_AGENT}


def _claims(entity: dict, prop: str) -> list:
    return [c.get("mainsnak", {}).get("datavalue", {}).get("value") for c in entity.get("claims", {}).get(prop, [])]


def _pick(entity: dict) -> str | None:
    for f in [*_claims(entity, "P18"), *_claims(entity, "P948")]:
        if isinstance(f, str) and not NOT_A_PHOTO.search(f):
            return f
    return None


def _plain(html: str | None) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", html or ""))).strip()


def _credit(artist: str) -> str:
    """A name fit for a one-line credit; Commons' artist field is sometimes a whole paragraph."""
    if not artist or len(artist) > 40 or ":" in artist or "http" in artist.lower():
        return "Wikimedia Commons"
    return artist


def _lookup(name: str, lat: float | None, lng: float | None) -> dict:
    found = http.request("GET", WIKIDATA_API, provider="Wikidata", headers=HEADERS, params={
        "action": "wbsearchentities", "search": name, "language": "en", "type": "item", "limit": 7, "format": "json"}).json()
    ids = [r["id"] for r in found.get("search", [])][:7]
    if not ids:
        return {}
    ents = http.request("GET", WIKIDATA_API, provider="Wikidata", headers=HEADERS, params={
        "action": "wbgetentities", "ids": "|".join(ids), "props": "claims", "format": "json"}).json().get("entities", {})
    file = None
    for i in ids:  # search order: the best-known match first
        e = ents.get(i) or {}
        coords = next((c for c in _claims(e, "P625") if isinstance(c, dict)), None)
        if lat is not None and lng is not None:
            if not coords or haversine_m(lat, lng, coords["latitude"], coords["longitude"]) > 60_000:
                continue  # another place with the same name
        file = _pick(e)
        if file:
            break
    if not file:
        return {}
    info = http.request("GET", COMMONS_API, provider="Wikipedia", headers=HEADERS, params={
        "action": "query", "titles": f"File:{file}", "prop": "imageinfo", "iiprop": "url|extmetadata",
        "iiurlwidth": 1600, "format": "json"}).json()
    page = next(iter(info.get("query", {}).get("pages", {}).values()), {})
    ii = (page.get("imageinfo") or [{}])[0]
    meta = ii.get("extmetadata") or {}
    url = ii.get("thumburl") or ii.get("url")
    if not url:
        return {}
    return {"url": url, "source_url": ii.get("descriptionurl"),
            "credit": _credit(_plain((meta.get("Artist") or {}).get("value"))),
            "license": _plain((meta.get("LicenseShortName") or {}).get("value"))[:40] or None}


def destination_photo(name: str, lat: float | None = None, lng: float | None = None) -> dict | None:
    """{url, credit, license, source_url} or None. Never raises: a missing photo just shows the card's own colours."""
    if not get_settings().wikidata_enabled:
        return None
    city = name.split(",")[0].strip()
    if len(city) < 2:
        return None
    key = [city.lower(), round(lat, 1) if lat is not None else None, round(lng, 1) if lng is not None else None]
    try:
        out = cached("wikimedia-photo", key, TTL, lambda: _lookup(city, lat, lng))
    except (ProviderUnavailable, ValueError, KeyError) as exc:
        log.info("no photo for %s: %s", city, exc)
        return None
    return out or None
