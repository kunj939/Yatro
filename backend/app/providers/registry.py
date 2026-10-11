"""Builds the provider set from configuration. This is the only place that knows concrete classes."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from app.core.config import get_settings
from app.providers.base import (
    HotelProvider,
    LLMProvider,
    MapsProvider,
    PlacesProvider,
    TransportProvider,
    WeatherProvider,
    WebSearchProvider,
)
from app.providers.demo import (
    DemoHotelProvider,
    DemoMapsProvider,
    DemoPlacesProvider,
    DemoSearchProvider,
    DemoTransportProvider,
    DemoWeatherProvider,
)
from app.providers.llm import OpenAICompatibleProvider, OpenRouterProvider, TemplateLLMProvider

log = logging.getLogger("tripai.providers")


@dataclass
class Providers:
    maps: MapsProvider
    places: PlacesProvider
    hotels: HotelProvider
    transport: TransportProvider
    search: WebSearchProvider | None
    weather: WeatherProvider | None
    llm: LLMProvider

    @property
    def mode(self) -> str:
        live = [self.maps.live, self.places.live]
        return "live" if all(live) else ("demo" if not any(live) else "mixed")

    def status(self) -> dict:
        return {
            "mode": self.mode,
            "maps": self.maps.name, "places": self.places.name, "hotels": self.hotels.name,
            "transport": self.transport.name, "search": self.search.name if self.search else "none",
            "weather": self.weather.name if self.weather else "none",
            "llm": {"provider": self.llm.name, "model": self.llm.model, "live": self.llm.live},
        }


def build_llm() -> LLMProvider:
    s = get_settings()
    choice = s.llm_provider
    if choice == "auto":
        choice = "openrouter" if s.openrouter_api_key else ("openai_compatible" if s.openai_compat_base_url else "template")
    if choice == "openrouter" and s.openrouter_api_key:
        return OpenRouterProvider(s.openrouter_api_key, s.openrouter_model, s.openrouter_base_url, s.app_name,
                                  s.cors_origin_list[0] if s.cors_origin_list else "http://localhost:3000")
    if choice == "openai_compatible" and s.openai_compat_base_url:
        return OpenAICompatibleProvider(s.openai_compat_base_url, s.openai_compat_api_key, s.openai_compat_model)
    if choice != "template":
        log.warning("LLM provider %s requested but not configured; using templates", choice)
    return TemplateLLMProvider()


def build_search() -> WebSearchProvider | None:
    s = get_settings()
    if s.force_demo:
        return DemoSearchProvider()
    choice = s.web_search_provider
    if choice == "none":
        return None
    if not s.web_search_api_key:
        return DemoSearchProvider() if not s.google_enabled else None
    from app.providers.search import BraveSearchProvider, SerperSearchProvider, TavilySearchProvider
    return {"brave": BraveSearchProvider, "serper": SerperSearchProvider, "tavily": TavilySearchProvider,
            "auto": BraveSearchProvider}[choice]()


@lru_cache
def get_providers() -> Providers:
    s = get_settings()
    if s.google_enabled:
        from app.providers.google import (
            GoogleHotelProvider,
            GoogleMapsProvider,
            GooglePlacesProvider,
            GoogleTransportProvider,
        )
        maps, places = GoogleMapsProvider(), GooglePlacesProvider()
        hotels: HotelProvider = GoogleHotelProvider(places) if s.hotel_provider != "estimate" else DemoHotelProvider()
        transport: TransportProvider = GoogleTransportProvider(maps, s.default_timezone)
        from app.providers.rail_chain import build as with_indian_rail_data
        transport = with_indian_rail_data(transport, places)  # RailRadar and/or IRCTC1, whichever is configured
    else:
        if s.demo_mode == "false":
            log.warning("DEMO_MODE=false but GOOGLE_MAPS_SERVER_KEY is missing — falling back to demo data")
        maps, places, hotels, transport = (DemoMapsProvider(), DemoPlacesProvider(), DemoHotelProvider(),
                                           DemoTransportProvider())
    weather: WeatherProvider | None
    if s.force_demo or not s.google_enabled:
        weather = DemoWeatherProvider()
    elif s.weather_provider == "none":
        weather = None
    else:
        from app.providers.weather import OpenMeteoWeatherProvider
        weather = OpenMeteoWeatherProvider()
    llm = TemplateLLMProvider() if s.force_demo and not s.openrouter_api_key else build_llm()
    providers = Providers(maps, places, hotels, transport, build_search(), weather, llm)
    log.info("Providers: %s", providers.status())
    return providers


@lru_cache
def get_demo_providers() -> Providers:
    """Always-offline provider set used by the public sample trip (no API cost, stable output)."""
    return Providers(DemoMapsProvider(), DemoPlacesProvider(), DemoHotelProvider(), DemoTransportProvider(),
                     DemoSearchProvider(), DemoWeatherProvider(), build_llm())