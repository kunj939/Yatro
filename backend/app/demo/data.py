"""Demo dataset: Gurugram / Delhi NCR -> Jaipur.

SAMPLE DATA for demo mode and UI development. Timings, fares and hours are illustrative and are
always labelled `demo` in the product. Live mode replaces all of this with provider data.
"""
from __future__ import annotations

CITIES = [
    {"id": "demo:city:gurugram", "name": "Gurugram", "aliases": ["gurgaon", "gurugram", "ggn", "cyber city"],
     "secondary": "Haryana, India", "lat": 28.4595, "lng": 77.0266, "hub": "ggn"},
    {"id": "demo:city:new-delhi", "name": "New Delhi", "aliases": ["delhi", "new delhi", "ndls"],
     "secondary": "Delhi, India", "lat": 28.6139, "lng": 77.2090, "hub": "ndls"},
    {"id": "demo:city:noida", "name": "Noida", "aliases": ["noida", "greater noida"],
     "secondary": "Uttar Pradesh, India", "lat": 28.5355, "lng": 77.3910, "hub": "ndls"},
    {"id": "demo:city:faridabad", "name": "Faridabad", "aliases": ["faridabad"],
     "secondary": "Haryana, India", "lat": 28.4089, "lng": 77.3178, "hub": "ndls"},
    {"id": "demo:city:jaipur", "name": "Jaipur", "aliases": ["jaipur", "pink city"],
     "secondary": "Rajasthan, India", "lat": 26.9124, "lng": 75.7873, "hub": "jp"},
]
DEMO_DESTINATION_ID = "demo:city:jaipur"

HUBS = {
    "ggn": {"train": ("Gurugram Railway Station", 28.4867, 77.0142), "bus": ("IFFCO Chowk bus stop", 28.4722, 77.0725),
            "air": ("Delhi Airport T3", 28.5562, 77.1000), "offset": 0},
    "ndls": {"train": ("New Delhi Railway Station", 28.6427, 77.2197), "bus": ("Bikaner House", 28.6112, 77.2310),
             "air": ("Delhi Airport T3", 28.5562, 77.1000), "offset": 35},
    "jp": {"train": ("Jaipur Junction", 26.9196, 75.7878), "bus": ("Sindhi Camp Bus Stand", 26.9235, 75.8015),
           "air": ("Jaipur International Airport", 26.8242, 75.8122)},
}

# Departure times are from the origin hub; offsets shift them for other NCR hubs.
# Sample timetable for demo mode. These are NOT real services: they carry no names and every option is
# labelled "sample timing" in the app. Real trips use the live timetable from the transport provider.
TRAINS_OUT = [("05:50", 265), ("06:40", 270), ("09:20", 285)]  # (departure, minutes)
TRAINS_BACK = [("16:55", 265), ("18:40", 270), ("20:50", 250)]
TRAIN_FARES = {"2S": 165, "CC": 575}
BUSES_OUT = [("06:00", 310, 690, "ac"), ("05:30", 330, 395, "ordinary")]  # (departure, minutes, fare, type)
BUSES_BACK = [("18:30", 315, 690, "ac"), ("17:30", 330, 395, "ordinary")]
FLIGHTS_OUT = [("07:05", 65, 3400)]  # (departure, minutes, fare)
FLIGHTS_BACK = [("21:20", 65, 3650)]
ROAD_KM = 238
ROAD_MINUTES = 280

H = lambda o, c: [[o, c]]  # noqa: E731
DAILY = lambda spans: {str(d): spans for d in range(7)}  # noqa: E731

ATTRACTIONS = [
    {"id": "hawa-mahal", "name": "Hawa Mahal", "lat": 26.9239, "lng": 75.8267, "tags": ["palace", "landmark", "heritage", "photography"],
     "hours": DAILY(H("09:00", "16:30")), "fee": 50, "minutes": 45, "rating": 4.5, "reviews": 98000,
     "desc": "The five-storey 'Palace of Winds' with 953 latticed windows, facing Siredeori Bazaar.",
     "website": "https://hawamahal.com"},
    {"id": "city-palace", "name": "City Palace", "lat": 26.9258, "lng": 75.8237, "tags": ["palace", "museum", "heritage", "royal"],
     "hours": DAILY([["09:30", "17:00"], ["19:00", "22:00"]]), "fee": 200, "minutes": 90, "rating": 4.4, "reviews": 64000,
     "desc": "Royal residence complex with courtyards, textile and armoury galleries.", "website": "https://www.royaljaipur.in"},
    {"id": "jantar-mantar", "name": "Jantar Mantar", "lat": 26.9248, "lng": 75.8246, "tags": ["landmark", "heritage", "science"],
     "hours": DAILY(H("09:00", "16:30")), "fee": 50, "minutes": 45, "rating": 4.5, "reviews": 41000,
     "desc": "UNESCO-listed 18th-century astronomical instruments, next to City Palace."},
    {"id": "albert-hall", "name": "Albert Hall Museum", "lat": 26.9117, "lng": 75.8195, "tags": ["museum", "heritage", "architecture"],
     "hours": DAILY([["09:00", "17:00"], ["19:00", "22:00"]]), "fee": 40, "minutes": 60, "rating": 4.5, "reviews": 52000,
     "desc": "Indo-Saracenic state museum in Ram Niwas Garden; lit up in the evening.",
     "website": "https://alberthalljaipur.gov.in"},
    {"id": "amber-fort", "name": "Amber Fort", "lat": 26.9855, "lng": 75.8513, "tags": ["fort", "palace", "heritage", "adventure"],
     "hours": DAILY(H("08:00", "17:30")), "fee": 100, "minutes": 120, "rating": 4.6, "reviews": 120000,
     "desc": "Hilltop fort-palace of red sandstone and marble overlooking Maota Lake."},
    {"id": "nahargarh-sunset", "name": "Nahargarh Fort Sunset Point", "lat": 26.9373, "lng": 75.8155,
     "tags": ["fort", "viewpoint", "sunset", "romantic", "heritage"], "hours": DAILY(H("10:00", "20:00")), "fee": 50,
     "minutes": 50, "rating": 4.5, "reviews": 47000, "desc": "Ridge-top fort walls with the widest view over the city at dusk."},
    {"id": "jal-mahal", "name": "Jal Mahal viewpoint", "lat": 26.9535, "lng": 75.8462, "tags": ["lake", "viewpoint", "sunset", "romantic", "photography"],
     "hours": DAILY(H("06:00", "22:00")), "fee": 0, "minutes": 30, "rating": 4.3, "reviews": 38000,
     "desc": "Palace in the middle of Man Sagar Lake, seen from the lakeside promenade."},
    {"id": "birla-mandir", "name": "Birla Mandir", "lat": 26.8921, "lng": 75.8155, "tags": ["temple", "spiritual", "architecture"],
     "hours": DAILY([["06:00", "12:00"], ["15:00", "20:00"]]), "fee": 0, "minutes": 40, "rating": 4.6, "reviews": 36000,
     "desc": "White marble Lakshmi-Narayan temple below Moti Dungri."},
    {"id": "patrika-gate", "name": "Patrika Gate", "lat": 26.8414, "lng": 75.8007, "tags": ["landmark", "photography", "architecture"],
     "hours": DAILY(H("08:00", "20:00")), "fee": 0, "minutes": 30, "rating": 4.5, "reviews": 29000,
     "desc": "Painted ceremonial gateway at Jawahar Circle garden."},
    {"id": "johari-bazaar", "name": "Johari Bazaar", "lat": 26.9215, "lng": 75.8260, "tags": ["market", "shopping", "food", "street_food"],
     "hours": {**DAILY(H("10:30", "21:30")), "6": []}, "fee": 0, "minutes": 60, "rating": 4.3, "reviews": 21000,
     "desc": "Jewellery, textiles and street food along the old city walls (most shops shut Sundays)."},
    {"id": "galta-ji", "name": "Galta Ji Temple", "lat": 26.9180, "lng": 75.8580, "tags": ["temple", "spiritual", "nature", "adventure"],
     "hours": DAILY(H("05:00", "21:00")), "fee": 0, "minutes": 60, "rating": 4.4, "reviews": 19000,
     "desc": "Temple complex with natural water tanks in a gorge east of the old city."},
    {"id": "panna-meena", "name": "Panna Meena ka Kund", "lat": 26.9870, "lng": 75.8560, "tags": ["heritage", "photography", "architecture"],
     "hours": DAILY(H("06:00", "19:00")), "fee": 0, "minutes": 25, "rating": 4.4, "reviews": 15000,
     "desc": "Criss-cross stepwell a short walk from Amber Fort."},
    {"id": "sisodia-garden", "name": "Sisodia Rani Garden", "lat": 26.8906, "lng": 75.8745, "tags": ["garden", "romantic", "relaxed", "nature"],
     "hours": DAILY(H("08:00", "18:00")), "fee": 30, "minutes": 40, "rating": 4.1, "reviews": 9000,
     "desc": "Terraced Mughal-style garden with fountains and murals of Krishna legends."},
]

RESTAURANTS = [
    {"id": "rawat", "name": "Rawat Mishthan Bhandar", "lat": 26.9208, "lng": 75.7990, "veg_only": True, "cost": 150,
     "meals": ["breakfast", "snack"], "hours": DAILY(H("07:00", "22:00")), "rating": 4.3, "reviews": 31000,
     "cuisines": ["Rajasthani snacks", "Sweets"], "tags": ["street_food", "local_favourite"]},
    {"id": "lmb", "name": "Laxmi Mishthan Bhandar (LMB)", "lat": 26.9219, "lng": 75.8266, "veg_only": True, "cost": 450,
     "meals": ["breakfast", "lunch", "snack", "dinner"], "hours": DAILY(H("08:00", "23:00")), "rating": 4.1, "reviews": 26000,
     "cuisines": ["Rajasthani thali", "Sweets"], "tags": ["heritage", "local_favourite"]},
    {"id": "tapri", "name": "Tapri Central", "lat": 26.9063, "lng": 75.8045, "veg_only": True, "cost": 380,
     "meals": ["breakfast", "snack", "lunch"], "hours": DAILY(H("07:30", "23:00")), "rating": 4.4, "reviews": 22000,
     "cuisines": ["Cafe", "Chai"], "tags": ["cafe", "romantic", "relaxed"]},
    {"id": "masala-chowk", "name": "Masala Chowk (food court)", "lat": 26.9127, "lng": 75.8188, "veg_only": False,
     "cost": 200, "meals": ["lunch", "snack", "dinner"], "hours": DAILY(H("11:00", "23:00")), "rating": 4.2,
     "reviews": 18000, "cuisines": ["Street food", "Rajasthani", "Chaat"], "tags": ["street_food", "budget", "local_favourite"]},
    {"id": "natraj", "name": "Natraj Restaurant", "lat": 26.9156, "lng": 75.8067, "veg_only": True, "cost": 500,
     "meals": ["lunch", "dinner"], "hours": DAILY(H("09:00", "23:00")), "rating": 4.2, "reviews": 14000,
     "cuisines": ["North Indian", "Rajasthani"], "tags": ["family"]},
    {"id": "niros", "name": "Niros", "lat": 26.9160, "lng": 75.8105, "veg_only": False, "cost": 900,
     "meals": ["lunch", "dinner"], "hours": DAILY(H("10:00", "23:00")), "rating": 4.2, "reviews": 12000,
     "cuisines": ["North Indian", "Continental"], "tags": ["classic"]},
    {"id": "padao", "name": "Padao Open Bar & Restaurant (Nahargarh)", "lat": 26.9384, "lng": 75.8150, "veg_only": False,
     "cost": 650, "meals": ["snack", "dinner"], "hours": DAILY(H("11:00", "22:00")), "rating": 4.0, "reviews": 9000,
     "cuisines": ["North Indian", "Snacks"], "tags": ["romantic", "viewpoint", "sunset"]},
    {"id": "peacock", "name": "Peacock Rooftop Restaurant", "lat": 26.9150, "lng": 75.7930, "veg_only": False, "cost": 700,
     "meals": ["lunch", "dinner"], "hours": DAILY(H("08:00", "23:00")), "rating": 4.4, "reviews": 11000,
     "cuisines": ["Rajasthani", "Continental"], "tags": ["romantic", "rooftop"]},
    {"id": "suvarna", "name": "Suvarna Mahal, Rambagh Palace", "lat": 26.8979, "lng": 75.8081, "veg_only": False, "cost": 4000,
     "meals": ["lunch", "dinner"], "hours": DAILY([["12:30", "15:00"], ["19:00", "23:00"]]), "rating": 4.6, "reviews": 3000,
     "cuisines": ["Royal Indian"], "tags": ["premium", "romantic", "fine_dining"]},
    {"id": "1135ad", "name": "1135 AD, Amber Fort", "lat": 26.9856, "lng": 75.8510, "veg_only": False, "cost": 2500,
     "meals": ["lunch", "dinner"], "hours": DAILY(H("12:30", "22:30")), "rating": 4.3, "reviews": 5000,
     "cuisines": ["Royal Rajasthani"], "tags": ["premium", "romantic", "heritage"]},
    {"id": "chokhi-dhani", "name": "Chokhi Dhani", "lat": 26.7683, "lng": 75.8315, "veg_only": True, "cost": 1100,
     "meals": ["dinner"], "hours": DAILY(H("17:00", "23:00")), "rating": 4.3, "reviews": 60000,
     "cuisines": ["Rajasthani village thali"], "tags": ["family", "culture", "food"]},
]

HOTELS = [
    {"id": "itc-rajputana", "name": "ITC Rajputana", "lat": 26.9194, "lng": 75.7933, "tier": "premium", "rate": 12500, "stars": 5},
    {"id": "clarks-amer", "name": "Hotel Clarks Amer", "lat": 26.8526, "lng": 75.8052, "tier": "tier1", "rate": 6800, "stars": 4},
    {"id": "pearl-palace", "name": "Hotel Pearl Palace", "lat": 26.9150, "lng": 75.7930, "tier": "tier2", "rate": 2600, "stars": 3},
    {"id": "arya-niwas", "name": "Hotel Arya Niwas", "lat": 26.9195, "lng": 75.8010, "tier": "budget", "rate": 1900, "stars": 2},
    {"id": "oyo-bani-park", "name": "OYO Townhouse Bani Park", "lat": 26.9270, "lng": 75.7960, "tier": "oyo", "rate": 1300, "stars": 2},
    {"id": "zostel", "name": "Zostel Jaipur", "lat": 26.9235, "lng": 75.8205, "tier": "hostel", "rate": 650, "stars": None},
]

# Simulated web verification results (to demonstrate the verification layer offline).
SEARCH_RESULTS = {
    "hawa-mahal": [
        {"title": "Hawa Mahal timings and ticket price", "url": "https://www.example-travel-guide.com/jaipur/hawa-mahal",
         "snippet": "Hawa Mahal is open 9:00 AM to 5:00 PM on all days. Entry fee ₹50 for Indians."},
    ],
    "albert-hall": [
        {"title": "Albert Hall Museum — Visit", "url": "https://alberthalljaipur.gov.in/visit",
         "snippet": "Museum timings: 9:00 AM to 5:00 PM and 7:00 PM to 10:00 PM. Open all days."},
    ],
    "city-palace": [
        {"title": "City Palace Jaipur — Plan your visit", "url": "https://www.royaljaipur.in/city-palace",
         "snippet": "Day visit 9:30 AM to 5:00 PM. Night visit 7:00 PM to 10:00 PM."},
    ],
    "amber-fort": [
        {"title": "Amer Fort visiting hours", "url": "https://www.tripadvisor.in/Attraction_Review-amber-fort",
         "snippet": "Amer Fort opens 8:00 AM to 5:30 PM. Light and sound show in the evening."},
    ],
}

WEATHER = {"summary": "Clear, warm afternoon", "max": 33.0, "min": 22.0, "rain": 5}