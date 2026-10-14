"""A short "about this place" from Wikipedia, for the trip chat (ADR 0017).

The text is the article's own introduction (CC BY-SA), always shown with a link back to the article. A place found
through Wikidata (`meta.wikidata`) is looked up by its exact English article; any other place by a search for its
name and the destination, kept only when the article's title really names that place. Kept 30 days and shared by
every traveller. Nothing here is required: on any failure the chat answers from the plan alone.
"""
from __future__ import annotations

import logging
import re

from app.providers import http
from app.providers import ttl as ttls
from app.providers.cache import cached

log = logging.getLogger("tripai.wikipedia")

API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
USER_AGENT = "YaitroTripPlanner/1.0 (https://yatro.vercel.app; hello@yaitro.com)"  # Wikimedia asks for one
_STOP = {"the", "of", "and", "a", "an", "at", "in", "on", "de", "la", "le", "temple", "fort", "palace", "museum",
         "park", "beach", "lake", "garden", "gardens", "market", "bazaar", "mandir", "masjid", "church", "national"}


# "The Taj Mahal ( TAHJ mə-HAHL; Hindustani: [t̪ɑːd͡ʒ ˈmɛɦ(ɛ)l]; lit. 'Crown of the Palace')": a reader wants none of it
_PRONOUNCE = re.compile(r"\s*\((?=[^()]*(?:\(|[\[ˈəɑɛːɦ]|\blit\.|Hindi:|Hindustani:|Urdu:|pronounced))(?:[^()]|\([^()]*\))*\)")


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}


def names_match(place: str, title: str) -> bool:
    """Is the article `title` about `place`? Its distinctive words must be in the title ("Hawa Mahal" for "Hawa Mahal,
    Jaipur"; not "Jaipur" for "Hawa Mahal")."""
    want = _tokens(place) - _STOP or _tokens(place)
    have = _tokens(title)
    need = len(want) if len(want) <= 2 else -(-len(want) * 3 // 5)  # all of one or two words, else three in five
    return bool(want) and len(want & have) >= need


def _get(url: str, params: dict) -> dict:
    r = http.request("GET", url, provider="Wikipedia", params={**params, "format": "json", "formatversion": 2},
                     headers={"User-Agent": USER_AGENT})
    return r.json()


def _title_for_qid(qid: str) -> str | None:
    data = _get(WIKIDATA_API, {"action": "wbgetentities", "ids": qid, "props": "sitelinks", "sitefilter": "enwiki"})
    return (((data.get("entities") or {}).get(qid) or {}).get("sitelinks") or {}).get("enwiki", {}).get("title")


def _title_by_search(name: str, near: str | None) -> str | None:
    q = f"{name} {near}" if near else name
    hits = (_get(API, {"action": "query", "list": "search", "srsearch": q, "srlimit": 5}).get("query") or {}).get("search") or []
    return next((h["title"] for h in hits if names_match(name, h["title"])), None)


def _intro(title: str) -> dict | None:
    data = _get(API, {"action": "query", "prop": "extracts|info", "exintro": 1, "explaintext": 1, "exsentences": 6,
                      "inprop": "url", "redirects": 1, "titles": title})
    pages = (data.get("query") or {}).get("pages") or []
    page = pages[0] if pages else {}
    text = _PRONOUNCE.sub("", re.sub(r"\s+", " ", page.get("extract") or "")).strip()
    if not text or page.get("missing") or "may refer to" in text[:200]:
        return None  # no article, or a disambiguation page
    return {"title": page.get("title") or title, "extract": text[:1500], "url": page.get("fullurl")}


def about(name: str, near: str | None = None, qid: str | None = None) -> dict | None:
    """{title, extract, url} for the place, or None when there's no article or Wikipedia can't be reached."""
    name = (name or "").strip()
    if len(name) < 3:
        return None

    def fetch():
        title = _title_for_qid(qid) if qid else None
        title = title if title and names_match(name, title) else _title_by_search(name, near)
        if title:
            return _intro(title) or {}
        # "Amer Fort" is the article "Amber Fort": the name itself, through redirects, when the article says where
        page = _intro(name)
        near_word = (near or "").split(",")[0].strip().lower()
        return page if page and near_word and near_word in page["extract"].lower() else {}

    try:
        found = cached("wikipedia", [name.lower(), (near or "").lower(), qid or ""], ttls.get("wikipedia"), fetch)
    except Exception as exc:  # noqa: BLE001 - optional: the chat still answers from the plan
        log.info("Wikipedia about %r unavailable: %s", name, exc)
        return None
    return found or None
