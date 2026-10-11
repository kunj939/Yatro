"""Provider interfaces. The planning engine depends only on these abstractions.

Swapping OpenRouter for a self-hosted vLLM/Qwen server, or Google for another maps vendor,
means adding one class that implements the interface — the engine does not change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime

from app.providers.types import (
    LLMMessage,
    LLMResult,
    MatrixResult,
    PlaceData,
    RouteResult,
    SearchResult,
    Suggestion,
    TransportData,
    WeatherData,
)


class Provider(ABC):
    name: str = "provider"
    live: bool = False  # True when backed by a real external API


class MapsProvider(Provider):
    @abstractmethod
    def compute_route(self, origin: tuple[float, float], dest: tuple[float, float], mode: str = "drive",
                      depart_at: datetime | None = None) -> RouteResult: ...

    @abstractmethod
    def compute_matrix(self, points: list[tuple[float, float]], mode: str = "drive") -> MatrixResult: ...


class PlacesProvider(Provider):
    @abstractmethod
    def autocomplete(self, query: str, limit: int = 6) -> list[Suggestion]: ...

    @abstractmethod
    def resolve(self, text: str, place_id: str | None = None) -> PlaceData | None:
        """Turn user text (or a place id) into a located city/place."""

    @abstractmethod
    def reverse(self, lat: float, lng: float) -> PlaceData | None: ...

    @abstractmethod
    def attractions(self, city: PlaceData, interests: list[str]) -> list[PlaceData]: ...

    @abstractmethod
    def restaurants(self, city: PlaceData, food: str, premium: bool) -> list[PlaceData]: ...

    def attraction(self, place_id: str, city: PlaceData) -> PlaceData | None:
        """One specific place, by id, as an attraction — for a place the traveller picked or named that the area
        search didn't happen to return. None when this provider can't look places up individually."""
        return None

    def refresh_place(self, place_id: str) -> PlaceData | None:
        """Today's name, address, location, rating and hours for a place already in a saved trip, by its id, with the
        cheapest field mask that covers them (ADR 0005). None when this provider can't (demo data has nothing to refresh)."""
        return None

    def find_attraction(self, name: str, city: PlaceData, max_km: float = 60) -> PlaceData | None:
        """The place a traveller means by `name` near `city` ("Fatehpur Sikri" while planning Agra), or None."""
        return None


class HotelProvider(Provider):
    @abstractmethod
    def hotels(self, city: PlaceData, tier: str) -> list[PlaceData]: ...


class TransportProvider(Provider):
    @abstractmethod
    def intercity_options(self, origin: PlaceData, dest: PlaceData, depart_day: date, return_day: date,
                          window_start: datetime, window_end: datetime, modes: list[str]) -> list[TransportData]: ...


class WebSearchProvider(Provider):
    @abstractmethod
    def search(self, query: str, num: int = 5) -> list[SearchResult]: ...


class WeatherProvider(Provider):
    @abstractmethod
    def forecast(self, lat: float, lng: float, day: date) -> WeatherData | None: ...


class LLMProvider(Provider):
    model: str = ""

    @abstractmethod
    def complete(self, messages: list[LLMMessage], *, json_schema: dict | None = None, temperature: float = 0.3,
                 max_tokens: int | None = None) -> LLMResult: ...
