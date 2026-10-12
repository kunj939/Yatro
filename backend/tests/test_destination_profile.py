"""Plans should match the place: beaches in Goa, mountains in Manali, landmarks in Paris, and no neighbourhood parks
standing in for sights (a Bharatpur plan had "C Block Park" and "Deendayal Nagar Park")."""
from datetime import datetime
from decimal import Decimal

from app.providers.google import _tags
from app.providers.types import PlaceData
from app.services import destination_profile as DP
from app.services.engine_types import Candidate, PlanInput
from app.services.planning_support import local_park, weak_place
from app.services.ranking import score_attraction


def place(name, types=(), n=5000, rating=4.4):
    p = PlaceData("google", name, name, "attraction", 15.5, 73.8, rating=rating, rating_count=n, types=list(types))
    p.tags = _tags(list(types), name)
    return p


GOA = [place("Baga Beach", ["beach"], 40000), place("Calangute Beach", ["beach"], 60000), place("Palolem Beach", ["beach"], 30000),
       place("Fort Aguada", ["historical_landmark"], 50000), place("Anjuna Flea Market", ["market"], 20000)]
MANALI = [place("Solang Valley", ["tourist_attraction"], 30000), place("Rohtang Pass Snow Point", ["tourist_attraction"], 20000),
          place("Hadimba Devi Temple", ["hindu_temple"], 40000), place("Jogini Falls Trek", ["hiking_area"], 8000)]
PARIS = [place("Louvre Museum", ["museum"], 300000), place("Musée d'Orsay", ["museum", "art_gallery"], 150000),
         place("Eiffel Tower", ["tourist_attraction", "observation_deck"], 400000), place("Notre-Dame", ["church"], 200000)]


def test_the_destination_reads_as_what_it_is():
    assert next(iter(DP.profile(GOA))) == "beach"
    assert next(iter(DP.profile(MANALI))) == "mountain"
    assert next(iter(DP.profile(PARIS))) == "city"
    assert DP.queries(DP.profile(GOA))[0] == "best beaches"
    assert DP.profile([]) == {}


def test_neighbourhood_parks_are_not_sights_but_real_parks_are():
    for name, n in (("C Block Park", 400), ("Deendayal Nagar Park", 1200), ("rajesh pilot park", 300), ("Sector 29 Park", 2500)):
        assert local_park(place(name, ["park"], n)), name
        assert weak_place(place(name, ["park"], n), "Bharatpur"), name
    assert not local_park(place("Lodhi Garden", ["park", "tourist_attraction"], 40000))
    assert not local_park(place("Keoladeo National Park", ["national_park", "tourist_attraction"], 30000))
    assert not local_park(place("Lohagarh Fort", ["historical_landmark"], 20000))


def _inp():
    return PlanInput("Delhi", "Goa", None, None, datetime(2026, 10, 17, 7), datetime(2026, 10, 20, 22), Decimal(40000),
                     2, "couple", ["train"], "both", ["balanced"], "tier2")


def test_in_a_beach_town_a_beach_outranks_an_equally_known_market():
    inp = _inp()
    inp.dest_kinds = DP.profile(GOA)
    beach = Candidate(key="b", place=place("Colva Beach", ["beach"], 20000), kind="attraction")
    market = Candidate(key="m", place=place("Mapusa Market", ["market"], 20000), kind="attraction")
    for c in (beach, market):
        score_attraction(c, inp, Decimal(5000))
    assert beach.score > market.score
    assert "one of the beaches this coast is known for" in beach.reasons


def test_a_neighbourhood_park_ranks_below_a_real_sight():
    inp = _inp()
    park = Candidate(key="p", place=place("A Block Park", ["park"], 900, rating=4.6), kind="attraction")
    fort = Candidate(key="f", place=place("Lohagarh Fort", ["historical_landmark"], 900, rating=4.2), kind="attraction")
    for c in (park, fort):
        score_attraction(c, inp, Decimal(5000))
    assert fort.score > park.score


def test_why_go_explains_a_sight_in_our_words_without_google_figures():
    from app.services.planner import why_go
    inp = _inp()
    inp.dest_kinds = DP.profile(GOA)
    beach = Candidate(key="b", place=place("Colva Beach", ["beach"], 20000), kind="attraction")
    score_attraction(beach, inp, Decimal(5000))
    assert why_go(beach) == "One of the beaches this coast is known for"
    fort = Candidate(key="f", place=place("Amber Fort", ["historical_landmark"], 90000), kind="attraction")
    score_attraction(fort, _inp(), Decimal(5000))
    assert why_go(fort) == "One of the best-known sights here"  # "(90,000 reviews)" is Google content (I6): left out
    assert why_go(Candidate(key="r", place=place("Cafe", [], 100), kind="restaurant")) is None
