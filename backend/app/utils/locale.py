"""Where a trip actually is, worked out offline from coordinates: no API call, no key, no per-request cost
(after the one-time in-memory data load on first use). Gives each trip its own timezone — so "7am" means
7am *there*, not 7am in India — and its own currency, instead of assuming India for every trip.
"""
from __future__ import annotations

import logging
from functools import lru_cache

log = logging.getLogger("tripai.locale")

DEFAULT_TIMEZONE = "Asia/Kolkata"
DEFAULT_CURRENCY = "INR"

# ISO 3166-1 country -> ISO 4217 currency. Countries that share a currency (the euro area, the East
# Caribbean dollar, the CFA franc zones, the US dollar in a few small economies...) all point to that
# currency; this covers ordinary trip planning, not every territory's edge case or currency history.
_EUR = {"AD", "AT", "BE", "CY", "DE", "EE", "ES", "FI", "FR", "GR", "HR", "IE", "IT", "LT", "LU", "LV", "MC",
        "MT", "NL", "PT", "SI", "SK", "SM", "VA", "XK", "ME"}
_XOF = {"BJ", "BF", "CI", "GW", "ML", "NE", "SN", "TG"}  # West African CFA franc
_XAF = {"CM", "CF", "TD", "CG", "GA", "GQ"}  # Central African CFA franc
_XCD = {"AG", "DM", "GD", "KN", "LC", "VC", "AI", "MS"}  # East Caribbean dollar
_AUD_ALSO = {"KI", "NR", "TV"}
_USD_ALSO = {"EC", "SV", "PA", "TL", "ZW", "FM", "MH", "PW", "BQ"}
_NZD_ALSO = {"CK", "NU", "TK"}
_ZAR_ALSO = {"LS", "NA", "SZ"}  # peg to the rand; also accept it directly

COUNTRY_CURRENCY: dict[str, str] = {
    **{c: "EUR" for c in _EUR}, **{c: "XOF" for c in _XOF}, **{c: "XAF" for c in _XAF},
    **{c: "XCD" for c in _XCD}, **{c: "AUD" for c in _AUD_ALSO}, **{c: "USD" for c in _USD_ALSO},
    **{c: "NZD" for c in _NZD_ALSO}, **{c: "ZAR" for c in _ZAR_ALSO},
    "IN": "INR", "GB": "GBP", "US": "USD", "CA": "CAD", "AU": "AUD", "NZ": "NZD", "JP": "JPY", "CN": "CNY",
    "HK": "HKD", "MO": "MOP", "TW": "TWD", "KR": "KRW", "KP": "KPW", "SG": "SGD", "MY": "MYR", "TH": "THB",
    "VN": "VND", "ID": "IDR", "PH": "PHP", "MM": "MMK", "KH": "KHR", "LA": "LAK", "BN": "BND",
    "PK": "PKR", "BD": "BDT", "LK": "LKR", "NP": "NPR", "BT": "BTN", "MV": "MVR", "AF": "AFN",
    "CH": "CHF", "LI": "CHF", "NO": "NOK", "SE": "SEK", "DK": "DKK", "IS": "ISK", "GL": "DKK", "FO": "DKK",
    "PL": "PLN", "CZ": "CZK", "HU": "HUF", "RO": "RON", "BG": "BGN", "RS": "RSD", "BA": "BAM", "MK": "MKD",
    "AL": "ALL", "MD": "MDL", "UA": "UAH", "BY": "BYN", "RU": "RUB", "GE": "GEL", "AM": "AMD", "AZ": "AZN",
    "TR": "TRY", "IL": "ILS", "PS": "ILS", "JO": "JOD", "LB": "LBP", "SY": "SYP", "IQ": "IQD", "IR": "IRR",
    "SA": "SAR", "AE": "AED", "QA": "QAR", "KW": "KWD", "BH": "BHD", "OM": "OMR", "YE": "YER",
    "EG": "EGP", "LY": "LYD", "TN": "TND", "DZ": "DZD", "MA": "MAD", "SD": "SDG", "SS": "SSP",
    "ET": "ETB", "KE": "KES", "TZ": "TZS", "UG": "UGX", "RW": "RWF", "BI": "BIF", "SO": "SOS",
    "NG": "NGN", "GH": "GHS", "SL": "SLL", "LR": "LRD", "GM": "GMD", "GN": "GNF", "CV": "CVE", "MR": "MRU",
    "ZM": "ZMW", "MW": "MWK", "MZ": "MZN", "AO": "AOA", "ZA": "ZAR", "BW": "BWP",
    "MG": "MGA", "MU": "MUR", "SC": "SCR", "KM": "KMF", "DJ": "DJF", "ER": "ERN",
    "MX": "MXN", "GT": "GTQ", "HN": "HNL", "NI": "NIO", "CR": "CRC", "CU": "CUP", "DO": "DOP", "HT": "HTG",
    "JM": "JMD", "TT": "TTD", "BS": "BSD", "BB": "BBD", "BZ": "BZD", "SR": "SRD", "GY": "GYD",
    "CO": "COP", "VE": "VES", "PE": "PEN", "BO": "BOB", "PY": "PYG", "UY": "UYU", "CL": "CLP", "AR": "ARS",
    "BR": "BRL", "FJ": "FJD", "PG": "PGK", "SB": "SBD", "VU": "VUY", "WS": "WST", "TO": "TOP",
    "PF": "XPF", "NC": "XPF", "MN": "MNT", "KZ": "KZT", "KG": "KGS", "TJ": "TJS", "TM": "TMT", "UZ": "UZS",
    "IE": "EUR",
}


@lru_cache
def _tz_finder():
    from timezonefinder import TimezoneFinder
    return TimezoneFinder()


def timezone_for(lat: float, lng: float) -> str:
    try:
        return _tz_finder().timezone_at(lat=lat, lng=lng) or DEFAULT_TIMEZONE
    except Exception as exc:  # noqa: BLE001 - a timezone lookup is never worth failing trip creation over
        log.info("Timezone lookup failed for (%s, %s): %s", lat, lng, exc)
        return DEFAULT_TIMEZONE


@lru_cache(maxsize=8192)
def _country_at(lat: float, lng: float) -> str | None:
    try:
        import reverse_geocoder as rg
        # mode=1: one in-process lookup. The default (mode=2) starts a pool of worker processes for every call —
        # about 50 ms each, and unsafe inside a threaded web server — to answer a single nearest-point query.
        result = rg.search([(lat, lng)], mode=1)
        return result[0].get("cc") if result else None
    except Exception as exc:  # noqa: BLE001
        log.info("Country lookup failed for (%s, %s): %s", lat, lng, exc)
        return None


def country_for(lat: float, lng: float) -> str | None:
    # A place's country never changes: round to ~100 m so every lookup for the same city (places, restaurants,
    # hotels, the trip itself) is answered from memory.
    return _country_at(round(lat, 3), round(lng, 3))


def warm_up() -> None:
    """Load the offline country and timezone data now, so the first trip planned after a restart doesn't wait
    about two seconds for it. Safe to call from a background thread."""
    country_for(28.6, 77.2)
    timezone_for(28.6, 77.2)


def currency_for(lat: float, lng: float) -> str:
    return COUNTRY_CURRENCY.get(country_for(lat, lng) or "", DEFAULT_CURRENCY)


def locale_for(lat: float | None, lng: float | None) -> tuple[str, str]:
    """(timezone, currency) for a destination. The India defaults when coordinates aren't known — the same
    behaviour as before this existed, rather than guessing."""
    if lat is None or lng is None:
        return DEFAULT_TIMEZONE, DEFAULT_CURRENCY
    return timezone_for(lat, lng), currency_for(lat, lng)