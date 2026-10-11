"""Where the airports are, so a flight is planned from the airport that actually serves a place.

Mussoorie has no airport: the trip is a road ride to Dehradun (Jolly Grant), the flight, and a road ride at the far
end. Without this, a flight was priced from city centre to city centre with a fixed allowance, so the plan never
showed the drive to and from the airport, however long. Positions are good to a few km; `tier` is how well served
the airport is (3 = a major hub with flights to most cities, 2 = a busy regional airport, 1 = a few flights a day).
"""
from __future__ import annotations

from app.utils.geo import haversine_m

# IATA code: (name, lat, lng, tier)
AIRPORTS: dict[str, tuple[str, float, float, int]] = {
    # India: hubs
    "DEL": ("Delhi Indira Gandhi Airport", 28.5562, 77.1000, 3), "BOM": ("Mumbai Airport", 19.0896, 72.8656, 3),
    "BLR": ("Bengaluru Kempegowda Airport", 13.1986, 77.7066, 3), "MAA": ("Chennai Airport", 12.9941, 80.1709, 3),
    "CCU": ("Kolkata Airport", 22.6547, 88.4467, 3), "HYD": ("Hyderabad Airport", 17.2403, 78.4294, 3),
    # India: regional
    "AMD": ("Ahmedabad Airport", 23.0772, 72.6347, 2), "PNQ": ("Pune Airport", 18.5821, 73.9197, 2),
    "GOI": ("Goa Dabolim Airport", 15.3808, 73.8314, 2), "GOX": ("Goa Mopa Airport", 15.7444, 73.8606, 2),
    "COK": ("Kochi Airport", 10.1520, 76.4019, 2), "TRV": ("Thiruvananthapuram Airport", 8.4821, 76.9200, 2),
    "JAI": ("Jaipur Airport", 26.8242, 75.8122, 2), "LKO": ("Lucknow Airport", 26.7606, 80.8893, 2),
    "VNS": ("Varanasi Airport", 25.4524, 82.8593, 2), "PAT": ("Patna Airport", 25.5913, 85.0880, 2),
    "IXB": ("Bagdogra Airport", 26.6812, 88.3286, 2), "GAU": ("Guwahati Airport", 26.1061, 91.5859, 2),
    "BBI": ("Bhubaneswar Airport", 20.2444, 85.8178, 2), "NAG": ("Nagpur Airport", 21.0922, 79.0472, 2),
    "IDR": ("Indore Airport", 22.7218, 75.8011, 2), "IXC": ("Chandigarh Airport", 30.6735, 76.7885, 2),
    "ATQ": ("Amritsar Airport", 31.7096, 74.7973, 2), "SXR": ("Srinagar Airport", 33.9871, 74.7742, 2),
    "CCJ": ("Kozhikode Airport", 11.1368, 75.9553, 2), "CJB": ("Coimbatore Airport", 11.0300, 77.0434, 2),
    "VTZ": ("Visakhapatnam Airport", 17.7212, 83.2245, 2), "IXE": ("Mangaluru Airport", 12.9613, 74.8901, 1),
    "IXR": ("Ranchi Airport", 23.3143, 85.3217, 1), "BHO": ("Bhopal Airport", 23.2875, 77.3374, 1),
    "UDR": ("Udaipur Airport", 24.6177, 73.8961, 1), "JDH": ("Jodhpur Airport", 26.2511, 73.0489, 1),
    "IXL": ("Leh Airport", 34.1359, 77.5465, 1), "IXJ": ("Jammu Airport", 32.6891, 74.8374, 1),
    "DED": ("Dehradun Jolly Grant Airport", 30.1897, 78.1803, 1), "KUU": ("Kullu Bhuntar Airport", 31.8767, 77.1544, 1),
    "SLV": ("Shimla Airport", 31.0818, 77.0680, 1), "DHM": ("Dharamshala Kangra Airport", 32.1651, 76.2634, 1),
    "IXZ": ("Port Blair Airport", 11.6412, 92.7297, 1), "IXM": ("Madurai Airport", 9.8345, 78.0934, 1),
    "TRZ": ("Tiruchirappalli Airport", 10.7654, 78.7097, 1), "VGA": ("Vijayawada Airport", 16.5304, 80.7968, 1),
    "TIR": ("Tirupati Airport", 13.6325, 79.5433, 1), "IXA": ("Agartala Airport", 23.8870, 91.2404, 1),
    "IMF": ("Imphal Airport", 24.7600, 93.8967, 1), "RPR": ("Raipur Airport", 21.1804, 81.7388, 1),
    "STV": ("Surat Airport", 21.1141, 72.7418, 1), "BDQ": ("Vadodara Airport", 22.3362, 73.2263, 1),
    "IXU": ("Aurangabad Airport", 19.8627, 75.3981, 1), "GWL": ("Gwalior Airport", 26.2933, 78.2278, 1), "AGR": ("Agra Airport", 27.1558, 77.9609, 1),
    "GOP": ("Gorakhpur Airport", 26.7397, 83.4497, 1), "IXD": ("Prayagraj Airport", 25.4401, 81.7339, 1),
    "DIB": ("Dibrugarh Airport", 27.4839, 95.0169, 1), "HBX": ("Hubballi Airport", 15.3617, 75.0849, 1),
    "IXG": ("Belagavi Airport", 15.8593, 74.6183, 1), "JLR": ("Jabalpur Airport", 23.1778, 80.0520, 1),
    "PGH": ("Pantnagar Airport", 29.0334, 79.4737, 1), "IXS": ("Silchar Airport", 24.9129, 92.9787, 1),
    # Abroad: the main airports of the most-asked destinations
    "IST": ("Istanbul Airport", 41.2753, 28.7519, 3), "SAW": ("Istanbul Sabiha Gokcen Airport", 40.8986, 29.3092, 2),
    "DXB": ("Dubai Airport", 25.2532, 55.3657, 3), "AUH": ("Abu Dhabi Airport", 24.4330, 54.6511, 2),
    "DOH": ("Doha Hamad Airport", 25.2731, 51.6081, 3), "SIN": ("Singapore Changi Airport", 1.3644, 103.9915, 3),
    "BKK": ("Bangkok Suvarnabhumi Airport", 13.6900, 100.7501, 3), "DMK": ("Bangkok Don Mueang Airport", 13.9126, 100.6067, 2),
    "HKT": ("Phuket Airport", 8.1132, 98.3169, 2), "KUL": ("Kuala Lumpur Airport", 2.7456, 101.7072, 3),
    "DPS": ("Bali Denpasar Airport", -8.7482, 115.1675, 2), "KTM": ("Kathmandu Airport", 27.6966, 85.3591, 2),
    "CMB": ("Colombo Airport", 7.1808, 79.8841, 2), "MLE": ("Male Velana Airport", 4.1918, 73.5291, 2),
    "DAC": ("Dhaka Airport", 23.8433, 90.3978, 2), "PBH": ("Paro Airport", 27.4032, 89.4246, 1),
    "LHR": ("London Heathrow Airport", 51.4700, -0.4543, 3), "LGW": ("London Gatwick Airport", 51.1537, -0.1821, 2),
    "EDI": ("Edinburgh Airport", 55.9508, -3.3615, 2), "CDG": ("Paris Charles de Gaulle Airport", 49.0097, 2.5479, 3),
    "AMS": ("Amsterdam Schiphol Airport", 52.3105, 4.7683, 3), "FRA": ("Frankfurt Airport", 50.0379, 8.5622, 3),
    "MUC": ("Munich Airport", 48.3537, 11.7750, 3), "ZRH": ("Zurich Airport", 47.4582, 8.5555, 3),
    "VIE": ("Vienna Airport", 48.1103, 16.5697, 3), "PRG": ("Prague Airport", 50.1008, 14.2600, 2),
    "FCO": ("Rome Fiumicino Airport", 41.8003, 12.2389, 3), "FLR": ("Florence Airport", 43.8100, 11.2051, 1),
    "MXP": ("Milan Malpensa Airport", 45.6301, 8.7255, 3), "VCE": ("Venice Airport", 45.5053, 12.3519, 2),
    "MAD": ("Madrid Airport", 40.4983, -3.5676, 3), "BCN": ("Barcelona Airport", 41.2974, 2.0833, 3),
    "SVQ": ("Seville Airport", 37.4180, -5.8931, 1), "LIS": ("Lisbon Airport", 38.7756, -9.1354, 2),
    "ATH": ("Athens Airport", 37.9364, 23.9445, 3), "JTR": ("Santorini Airport", 36.3992, 25.4793, 1),
    "BRU": ("Brussels Airport", 50.9010, 4.4856, 2), "KEF": ("Reykjavik Keflavik Airport", 63.9850, -22.6056, 2),
    "RAK": ("Marrakech Airport", 31.6069, -8.0363, 2), "CAI": ("Cairo Airport", 30.1219, 31.4056, 3),
    "CPT": ("Cape Town Airport", -33.9715, 18.6021, 2), "JFK": ("New York JFK Airport", 40.6413, -73.7781, 3),
    "YVR": ("Vancouver Airport", 49.1967, -123.1815, 3), "EZE": ("Buenos Aires Ezeiza Airport", -34.8222, -58.5358, 3),
    "NRT": ("Tokyo Narita Airport", 35.7720, 140.3929, 3), "HND": ("Tokyo Haneda Airport", 35.5494, 139.7798, 3),
    "KIX": ("Osaka Kansai Airport", 34.4347, 135.2440, 3), "ICN": ("Seoul Incheon Airport", 37.4602, 126.4407, 3),
    "SYD": ("Sydney Airport", -33.9399, 151.1753, 3), "ZQN": ("Queenstown Airport", -45.0211, 168.7392, 1),
}

TIER_BONUS_KM = 40  # a busier airport is worth this much more road per tier: far more flights, and cheaper ones
MAX_AIRPORT_KM = 160  # beyond this by air, the "airport for this place" is a day's journey of its own


def airport_label(code: str) -> str:
    return f"{AIRPORTS[code][0]} ({code})"


def nearest_airports(lat: float, lng: float, limit: int = 2,
                     radius_km: float = MAX_AIRPORT_KM) -> list[tuple[str, float, float, float]]:
    """The airports worth flying from for a place, best first: [(code, lat, lng, straight_line_km)]."""
    found = []
    for code, (_, alat, alng, tier) in AIRPORTS.items():
        km = haversine_m(lat, lng, alat, alng) / 1000
        if km <= radius_km:
            found.append((km - TIER_BONUS_KM * tier, code, alat, alng, km))
    found.sort()
    return [(code, alat, alng, round(km, 1)) for _, code, alat, alng, km in found[:limit]]
