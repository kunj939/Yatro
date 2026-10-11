"""Where the major Indian railway stations are, so that ANY place can be matched to the stations that serve it.

A traveller books a train to wherever trains actually go and finishes the journey by road: to Dehradun for
Mussoorie, to New Delhi for a Gurugram start. Which stations those are can't be looked up from a city name (the
timetable APIs only know station codes), so this holds the ~100 stations that carry the most traffic, with
positions good to a few km, which is all that choosing between them needs.

Positions are approximate on purpose; the plan's own road legs are costed from real routes where available.
`tier` is how many trains a station gets: 3 = a major hub, 2 = a regional junction, 1 = a smaller station. A
higher tier is worth a longer ride (see nearest_stations()).
"""
from __future__ import annotations

from app.utils.geo import haversine_m

# code: (name, lat, lng, tier)
STATIONS: dict[str, tuple[str, float, float, int]] = {
    # Delhi NCR
    "NDLS": ("New Delhi", 28.6428, 77.2197, 3), "DLI": ("Old Delhi", 28.6614, 77.2273, 3),
    "NZM": ("Hazrat Nizamuddin", 28.5880, 77.2544, 3), "ANVT": ("Anand Vihar Terminal", 28.6508, 77.3155, 3),
    "DEE": ("Delhi Sarai Rohilla", 28.6631, 77.1849, 2), "GGN": ("Gurgaon", 28.4643, 77.0289, 1),
    "FDB": ("Faridabad", 28.4110, 77.3100, 1), "GZB": ("Ghaziabad", 28.6660, 77.4285, 2),
    "MTC": ("Meerut City", 28.9990, 77.7100, 2),
    # Rajasthan
    "JP": ("Jaipur Jn", 26.9190, 75.7870, 3), "AII": ("Ajmer Jn", 26.4530, 74.6400, 2),
    "JU": ("Jodhpur Jn", 26.2830, 73.0230, 3), "UDZ": ("Udaipur City", 24.5757, 73.6990, 2),
    "BKN": ("Bikaner Jn", 28.0181, 73.3141, 2), "KOTA": ("Kota Jn", 25.1840, 75.8500, 3),
    "JSM": ("Jaisalmer", 26.9130, 70.9130, 1), "SWM": ("Sawai Madhopur", 26.0140, 76.3570, 2),
    "ABR": ("Abu Road", 24.4830, 72.7800, 1), "AWR": ("Alwar", 27.5580, 76.6110, 1),
    "BTE": ("Bharatpur", 27.2170, 77.4800, 1), "COR": ("Chittaurgarh", 24.8830, 74.6330, 1),
    # Uttar Pradesh / Uttarakhand
    "AGC": ("Agra Cantt", 27.1583, 77.9905, 3), "MTJ": ("Mathura Jn", 27.4930, 77.6740, 3),
    "GWL": ("Gwalior", 26.2183, 78.1820, 3), "JHS": ("Jhansi Jn", 25.4420, 78.5700, 3),
    "CNB": ("Kanpur Central", 26.4540, 80.3510, 3), "LKO": ("Lucknow NR", 26.8320, 80.9230, 3),
    "BSB": ("Varanasi Jn", 25.3320, 82.9870, 3), "PRYJ": ("Prayagraj Jn", 25.4420, 81.8290, 3),
    "GKP": ("Gorakhpur Jn", 26.7580, 83.3730, 3), "BE": ("Bareilly", 28.3600, 79.4160, 2),
    "MB": ("Moradabad", 28.8460, 78.7730, 2), "SRE": ("Saharanpur", 29.9640, 77.5330, 2),
    "DDN": ("Dehradun", 30.3165, 78.0322, 3), "HW": ("Haridwar Jn", 29.9450, 78.1690, 3),
    "RKSH": ("Rishikesh", 30.0980, 78.2960, 1), "RMR": ("Ramnagar", 29.3960, 79.1270, 1),
    "KGM": ("Kathgodam", 29.2650, 79.5420, 2),
    # Punjab / Haryana / Himachal / J&K
    "UMB": ("Ambala Cantt", 30.3450, 76.8300, 3), "CDG": ("Chandigarh", 30.7080, 76.8210, 2),
    "KLK": ("Kalka", 30.8380, 76.9410, 2), "LDH": ("Ludhiana Jn", 30.9070, 75.8460, 3),
    "ASR": ("Amritsar Jn", 31.6340, 74.8700, 3), "JUC": ("Jalandhar City", 31.3290, 75.5780, 2),
    "PTK": ("Pathankot Jn", 32.2760, 75.6400, 2), "JAT": ("Jammu Tawi", 32.7150, 74.8720, 3),
    "SVDK": ("Shri Mata Vaishno Devi Katra", 32.9850, 74.9450, 2), "SML": ("Shimla", 31.1050, 77.1670, 1),
    # Madhya Pradesh / Chhattisgarh / Gujarat / Maharashtra
    "BPL": ("Bhopal Jn", 23.2670, 77.4130, 3), "HBJ": ("Rani Kamlapati", 23.2280, 77.4360, 2),
    "INDB": ("Indore Jn", 22.7200, 75.8670, 3), "UJN": ("Ujjain Jn", 23.1860, 75.7770, 2),
    "JBP": ("Jabalpur", 23.1640, 79.9340, 3), "NGP": ("Nagpur", 21.1520, 79.0880, 3),
    "R": ("Raipur Jn", 21.2540, 81.6290, 3), "ADI": ("Ahmedabad Jn", 23.0250, 72.6010, 3),
    "ST": ("Surat", 21.2060, 72.8400, 3), "BRC": ("Vadodara Jn", 22.3100, 73.1810, 3),
    "RJT": ("Rajkot Jn", 22.3000, 70.7900, 2), "CSMT": ("Mumbai CSMT", 18.9400, 72.8350, 3),
    "MMCT": ("Mumbai Central", 18.9690, 72.8190, 3), "LTT": ("Lokmanya Tilak Terminus", 19.0700, 72.8880, 3),
    "BDTS": ("Bandra Terminus", 19.0620, 72.8400, 3), "DR": ("Dadar", 19.0190, 72.8420, 2),
    "PUNE": ("Pune Jn", 18.5290, 73.8740, 3), "NK": ("Nashik Road", 19.9500, 73.8330, 2),
    # South
    "MAO": ("Madgaon Jn", 15.2760, 73.9580, 3), "SBC": ("KSR Bengaluru", 12.9780, 77.5680, 3),
    "YPR": ("Yesvantpur Jn", 13.0230, 77.5510, 2), "MYS": ("Mysuru Jn", 12.3120, 76.6500, 2),
    "MAS": ("Chennai Central", 13.0820, 80.2750, 3), "MS": ("Chennai Egmore", 13.0790, 80.2610, 2),
    "CBE": ("Coimbatore Jn", 10.9970, 76.9670, 3), "MTP": ("Mettupalayam", 11.2950, 76.9450, 1),
    "MDU": ("Madurai Jn", 9.9190, 78.1130, 2), "TPJ": ("Tiruchirappalli Jn", 10.8050, 78.6890, 2),
    "ERS": ("Ernakulam Jn", 9.9690, 76.2910, 3), "TVC": ("Thiruvananthapuram Central", 8.4880, 76.9520, 3),
    "MAQ": ("Mangaluru Central", 12.8730, 74.8420, 2), "SC": ("Secunderabad Jn", 17.4340, 78.5010, 3),
    "HYB": ("Hyderabad Deccan", 17.3910, 78.4680, 2), "BZA": ("Vijayawada Jn", 16.5170, 80.6190, 3),
    "VSKP": ("Visakhapatnam", 17.7220, 83.2890, 3), "TPTY": ("Tirupati", 13.6320, 79.4190, 2),
    # East / North-east
    "HWH": ("Howrah Jn", 22.5850, 88.3430, 3), "SDAH": ("Sealdah", 22.5670, 88.3700, 3),
    "KOAA": ("Kolkata", 22.5690, 88.3760, 2), "NJP": ("New Jalpaiguri Jn", 26.6800, 88.4370, 3),
    "GHY": ("Guwahati", 26.1810, 91.7500, 3), "PNBE": ("Patna Jn", 25.6020, 85.1380, 3),
    "DHN": ("Dhanbad Jn", 23.7960, 86.4270, 2), "RNC": ("Ranchi", 23.3410, 85.3260, 2),
    "BBS": ("Bhubaneswar", 20.2500, 85.8440, 3), "PURI": ("Puri", 19.8000, 85.8260, 2),
}

# Cities served by several major terminals, each with trains the others don't have: Jaipur's Double Decker, the
# Garib Rath and a dozen more end at Delhi Sarai Rohilla, not New Delhi; Mumbai's trains split between CSMT, LTT,
# Mumbai Central and Bandra. A search that only asks about the city's main station misses them. Listed in the order
# worth trying: the main station first.
STATION_GROUPS: dict[str, list[str]] = {
    "Delhi": ["NDLS", "DEE", "NZM", "DLI", "ANVT"],
    "Mumbai": ["CSMT", "LTT", "MMCT", "BDTS", "DR"],
    "Kolkata": ["HWH", "SDAH", "KOAA"],
    "Chennai": ["MAS", "MS"],
    "Hyderabad": ["SC", "HYB"],
    "Bengaluru": ["SBC", "YPR"],
}
GROUP_OF: dict[str, str] = {code: city for city, codes in STATION_GROUPS.items() for code in codes}


def group_stations(code: str) -> list[tuple[str, str]]:
    """The other major terminals of the city this station belongs to ([] for a station that is on its own)."""
    group = STATION_GROUPS.get(GROUP_OF.get(code, ""), [])
    return [(c, STATIONS[c][0]) for c in group if c != code and c in STATIONS]


# Cities where a metro or suburban rail network links the suburbs to the main stations, so the ride to the
# station can be by metro instead of a cab: (name, centre lat, centre lng, radius km, first train, last train).
# Both ends of the transfer must fall in the same area (Gurugram -> New Delhi by the Yellow Line; not Gurugram ->
# Meerut). Service hours are the usual first and last departures from the terminal stations, to the nearest
# quarter hour: a metro ride is only planned inside them, otherwise the cab is used.
METRO_AREAS: list[tuple[str, float, float, float, tuple[int, int], tuple[int, int]]] = [
    ("Delhi NCR", 28.61, 77.21, 45, (5, 15), (23, 0)), ("Mumbai", 19.07, 72.88, 50, (5, 30), (23, 30)),
    ("Kolkata", 22.57, 88.36, 30, (6, 30), (22, 15)), ("Chennai", 13.06, 80.25, 30, (5, 15), (22, 30)),
    ("Bengaluru", 12.97, 77.59, 25, (5, 15), (22, 45)), ("Hyderabad", 17.40, 78.48, 25, (6, 0), (22, 30)),
    ("Pune", 18.52, 73.86, 15, (6, 0), (22, 0)), ("Ahmedabad", 23.03, 72.58, 15, (6, 30), (21, 0)),
    ("Lucknow", 26.85, 80.95, 15, (6, 0), (22, 0)), ("Jaipur", 26.91, 75.79, 10, (6, 0), (21, 30)),
    ("Kochi", 9.98, 76.29, 15, (6, 0), (22, 0)),
]


def metro_area(lat: float, lng: float) -> str | None:
    for name, clat, clng, radius, _, _ in METRO_AREAS:
        if haversine_m(lat, lng, clat, clng) <= radius * 1000:
            return name
    return None


def metro_runs_at(area: str, at) -> bool:
    """Is a metro in `area` running at this moment (a datetime)? Between the last train and the first, no."""
    for name, *_, first, last in METRO_AREAS:
        if name == area:
            return first <= (at.hour, at.minute) <= last
    return False


TIER_BONUS_KM = 20  # each tier is worth about this much extra riding when picking a station
DEFAULT_RADIUS_KM = 100  # a railhead further than this by air is a day of travelling, not a transfer


def station_position(code: str) -> tuple[float, float] | None:
    s = STATIONS.get(code)
    return (s[1], s[2]) if s else None


def nearest_stations(lat: float, lng: float, exclude: set[str] | frozenset[str] = frozenset(), limit: int = 3,
                     radius_km: float = DEFAULT_RADIUS_KM) -> list[tuple[str, str, float]]:
    """The stations worth using for a place, best first: [(code, name, straight_line_km)].

    A big hub is worth a longer ride than a small station, since it has far more trains: New Delhi beats
    Faridabad for a Gurugram start though it is a little further. Distance still decides between stations of
    the same size.
    """
    found = []
    for code, (name, slat, slng, tier) in STATIONS.items():
        if code in exclude:
            continue
        km = haversine_m(lat, lng, slat, slng) / 1000
        if km <= radius_km:
            found.append((km - TIER_BONUS_KM * tier, code, name, km))
    found.sort()
    return [(code, name, round(km, 1)) for _, code, name, km in found[:limit]]