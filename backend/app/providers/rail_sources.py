"""The rail source chain's last links (ADR 0002): official datasets and AI station guidance.

Chain, stopping at the first that yields trains, each option stamped with the source that answered:

  1. saved_timetable   inside each rail provider, per route (rail_common.RailCache)
  2. live_api          RailRadar, then IRCTC1 (rail_chain.RailChain)
  3. official_dataset  `OfficialTimetable`: no dataset is imported yet, so the default yields nothing
  4. station_guidance  rail_common.estimated_rail_options: stations and distances only, no train facts
  5. ai_guidance       only if 4 yields nothing; behind AI_RAIL_GUIDANCE (default off, per the complexity guard:
                       Phase 0 found no Indian route where 4 came back empty). Every station it names must be in the
                       gazetteer; anything else is dropped, and the text must pass `rail_text_ok`.
"""
from __future__ import annotations

import re
from typing import Protocol

from app.providers.rail_stations import STATIONS
from app.providers.types import PlaceData, TransportData

# Anything that looks like a train fact: never accepted from AI text (I1).
_TRAIN_NUMBER = re.compile(r"(?<!\d)\d{4,6}(?!\d)")
_CLOCK = re.compile(r"\b(?:[01]?\d|2[0-3])[:.][0-5]\d\b|\b\d{1,2}\s?(?:am|pm)\b", re.IGNORECASE)
_MONEY = re.compile(r"(?:₹|rs\.?|inr|\$|€|£)\s?\d|\d\s?(?:rupees|rs)\b", re.IGNORECASE)
_TRAIN_NAME = re.compile(r"\b(?:express|shatabdi|rajdhani|vande bharat|duronto|garib rath|mail|humsafar|tejas|"
                         r"gatimaan|superfast|jan shatabdi|double decker)\b", re.IGNORECASE)


def rail_text_ok(text: str) -> bool:
    """AI output guard: no train number, clock time, money amount or train-type name."""
    return not any(rx.search(text or "") for rx in (_TRAIN_NUMBER, _CLOCK, _MONEY, _TRAIN_NAME))


def validated_stations(suggested: list[str]) -> list[tuple[str, str]]:
    """Keep only station codes the gazetteer knows: [(code, name)]. Unknown or malformed entries are dropped."""
    out: list[tuple[str, str]] = []
    for raw in suggested:
        code = str(raw or "").strip().upper()
        if code in STATIONS and code not in {c for c, _ in out}:
            out.append((code, STATIONS[code][0]))
    return out


class OfficialTimetable(Protocol):
    def intercity(self, origin: PlaceData, dest: PlaceData, depart_day, return_day) -> list[TransportData]: ...


class NoOfficialTimetable:
    """No official dataset has been imported: this link of the chain yields nothing."""
    name = "official-dataset(none)"

    def intercity(self, origin, dest, depart_day, return_day) -> list[TransportData]:
        return []


class AiGuidance(Protocol):
    def stations(self, origin: PlaceData, dest: PlaceData) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]: ...


class NoAiGuidance:
    """AI_RAIL_GUIDANCE is off: this link yields nothing."""

    def stations(self, origin, dest):
        return [], []
