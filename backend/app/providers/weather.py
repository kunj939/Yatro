"""Open-Meteo forecast (free, no API key). Only covers the next ~16 days."""
from __future__ import annotations

from datetime import date, timedelta

from app.core.errors import ProviderUnavailable
from app.providers import http
from app.providers import ttl as ttls
from app.providers.base import WeatherProvider
from app.providers.cache import cached
from app.providers.types import WeatherData

CODES = {0: "Clear sky", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Fog",
         51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
         71: "Snow", 80: "Rain showers", 81: "Rain showers", 82: "Violent showers", 95: "Thunderstorm",
         96: "Thunderstorm with hail", 99: "Thunderstorm with hail"}


class OpenMeteoWeatherProvider(WeatherProvider):
    name = "open-meteo"
    live = True

    def forecast(self, lat: float, lng: float, day: date) -> WeatherData | None:
        if not (date.today() <= day <= date.today() + timedelta(days=15)):
            return None

        def fetch():
            r = http.request("GET", "https://api.open-meteo.com/v1/forecast", provider="Open-Meteo",
                             params={"latitude": lat, "longitude": lng, "timezone": "auto",
                                     "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                                     "start_date": day.isoformat(), "end_date": day.isoformat()})
            return r.json().get("daily", {})
        try:
            d = cached("open-meteo", [round(lat, 2), round(lng, 2), day.isoformat()], ttls.get("open-meteo"), fetch)
        except ProviderUnavailable:
            return None
        if not d.get("time"):
            return None
        return WeatherData(CODES.get(d["weather_code"][0], "Mixed conditions"), d["temperature_2m_max"][0],
                           d["temperature_2m_min"][0], d.get("precipitation_probability_max", [None])[0],
                           "verified_provider", "Open-Meteo forecast")
