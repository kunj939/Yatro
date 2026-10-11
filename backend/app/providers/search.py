"""Web search providers used by the verification layer (Brave, Serper, Tavily)."""
from __future__ import annotations

from datetime import timedelta

from app.core.config import get_settings
from app.providers import http
from app.providers import ttl as ttls
from app.providers.base import WebSearchProvider
from app.providers.cache import cached
from app.providers.types import SearchResult


class _Base(WebSearchProvider):
    live = True
    ttl = timedelta(hours=12)

    def __init__(self) -> None:
        self.key = get_settings().web_search_api_key or ""

    def search(self, query: str, num: int = 5) -> list[SearchResult]:
        rows = cached(self.name, [query, num], ttls.get("search"), lambda: self._fetch(query, num))
        return [SearchResult(**r) for r in rows]

    def _fetch(self, query: str, num: int) -> list[dict]:  # pragma: no cover - network
        raise NotImplementedError


class BraveSearchProvider(_Base):
    name = "brave"

    def _fetch(self, query, num):
        r = http.request("GET", "https://api.search.brave.com/res/v1/web/search", provider="Brave Search",
                         headers={"X-Subscription-Token": self.key, "Accept": "application/json"},
                         params={"q": query, "count": num})
        return [{"title": x.get("title", ""), "url": x["url"], "snippet": x.get("description", "")}
                for x in r.json().get("web", {}).get("results", [])[:num]]


class SerperSearchProvider(_Base):
    name = "serper"

    def _fetch(self, query, num):
        r = http.request("POST", "https://google.serper.dev/search", provider="Serper",
                         headers={"X-API-KEY": self.key, "Content-Type": "application/json"},
                         json={"q": query, "num": num, "gl": "in"})
        return [{"title": x.get("title", ""), "url": x["link"], "snippet": x.get("snippet", "")}
                for x in r.json().get("organic", [])[:num]]


class TavilySearchProvider(_Base):
    name = "tavily"

    def _fetch(self, query, num):
        r = http.request("POST", "https://api.tavily.com/search", provider="Tavily",
                         json={"api_key": self.key, "query": query, "max_results": num, "search_depth": "basic"})
        return [{"title": x.get("title", ""), "url": x["url"], "snippet": x.get("content", "")[:500]}
                for x in r.json().get("results", [])[:num]]
