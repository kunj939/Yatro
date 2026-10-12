"""Wikidata adds famous sights for free (cached 30 days) and lets the Google search skip what it already covers."""
from app.providers import google as G
from app.providers import wiki_places as WP
from app.providers import wikidata as WD
from app.providers.types import PlaceData

BHARATPUR = PlaceData("google", "c1", "Bharatpur", "city", 27.2152, 77.4895)


def _row(qid, name, qtype, lat, lng, links, desc=None):
    b = {"item": {"value": f"http://www.wikidata.org/entity/{qid}"}, "itemLabel": {"value": name},
         "type": {"value": f"http://www.wikidata.org/entity/{qtype}"}, "where": {"value": f"Point({lng} {lat})"},
         "n": {"value": str(links)}}
    if desc:
        b["itemDescription"] = {"value": desc}
    return b


ROWS = [_row("Q1", "Fatehpur Sikri", "Q57821", 27.094, 77.668, 85), _row("Q2", "Kevladev National Park", "Q46169", 27.17, 77.52, 56),
        _row("Q3", "Q7130214", "Q16560", 27.2, 77.4, 9), _row("Q4", "Deeg Palace", "Q16560", 27.47, 77.33, 5, "palace in Rajasthan")]


def test_rows_become_tagged_sights_and_unnamed_items_are_dropped(monkeypatch):
    monkeypatch.setattr(WD, "cached", lambda name, key, ttl, fetch: ROWS)
    out = WD.sights(BHARATPUR)
    assert [p.name for p in out] == ["Fatehpur Sikri", "Kevladev National Park", "Deeg Palace"]  # "Q7130214" has no name
    assert out[0].tags == ["fort", "heritage"] and "national_park" in out[1].tags
    assert out[2].meta["wiki_links"] == 5 and out[2].description == "palace in Rajasthan"
    assert out[0].provider == "wikidata" and out[0].provider_place_id == "wd:Q1" and out[0].rating_count is None


def test_a_wikidata_failure_never_stops_a_plan(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("503")
    monkeypatch.setattr(WD, "cached", boom)
    assert WD.sights(BHARATPUR) == []


def test_fame_from_wikipedia_pages_sits_on_the_review_scale():
    p = PlaceData("wikidata", "wd:Q1", "Fatehpur Sikri", "attraction", 0, 0, meta={"wiki_links": 85})
    assert 20_000 <= WD.fame_count(p) <= 40_000  # a must-see, like a sight with tens of thousands of reviews
    assert WD.fame_count(PlaceData("wikidata", "x", "x", "attraction", 0, 0, meta={"wiki_links": 1})) < 600
    assert WD.fame_count(PlaceData("google", "g", "g", "attraction", 0, 0, rating_count=1234)) == 1234


def test_a_place_both_know_appears_once_with_googles_details_and_wikidatas_fame():
    g = PlaceData("google", "gk", "Keoladeo National Park", "attraction", 27.159, 77.523, rating=4.4, rating_count=30000,
                  tags=["landmark"])
    other = PlaceData("google", "go", "Lohagarh Fort", "attraction", 27.218, 77.490, rating=4.2, rating_count=8000, tags=["fort"])
    wiki = [PlaceData("wikidata", "wd:Q2", "Kevladev National Park", "attraction", 27.17, 77.52, tags=["national_park", "nature"],
                      meta={"wiki_links": 56}),
            PlaceData("wikidata", "wd:Q4", "Deeg Palace", "attraction", 27.47, 77.33, tags=["palace", "heritage"], meta={"wiki_links": 5})]
    out = WP.merge([g, other], wiki)
    assert [p.name for p in out] == ["Keoladeo National Park", "Lohagarh Fort", "Deeg Palace"]
    assert out[0].meta["wiki_links"] == 56 and "national_park" in out[0].tags and out[0].rating == 4.4


def test_with_wikidata_in_hand_google_skips_the_searches_it_covers(monkeypatch):
    asked = []
    monkeypatch.setattr(G.GooglePlacesProvider, "_text_search", lambda self, q, center, radius=25000, **k: asked.append(q) or [])
    known = [PlaceData("wikidata", f"wd:Q{i}", f"Sight {i}", "attraction", 27.2, 77.5, tags=["fort"], meta={"wiki_links": 10})
             for i in range(12)]
    G.GooglePlacesProvider().attractions(BHARATPUR, ["fort", "nightlife"], known=known)
    assert asked[0] == "top tourist attractions in Bharatpur"
    assert "forts in Bharatpur" not in asked and "nightlife and bars in Bharatpur" in asked  # Wikidata has forts, not bars
    assert not any("near Bharatpur" in q for q in asked)  # the famous places around it came from Wikidata
    asked.clear()
    G.GooglePlacesProvider().attractions(BHARATPUR, ["fort"])  # without Wikidata: the full search, as before
    assert "forts in Bharatpur" in asked and any("near Bharatpur" in q for q in asked)


def test_plus_plans_get_every_google_search_and_free_plans_the_lean_one(monkeypatch):
    calls = []

    class Base:
        name, live = "google-places", True

        def attractions(self, city, interests, known=None):
            calls.append(known)
            return []

    wiki = [PlaceData("wikidata", "wd:Q1", "Fatehpur Sikri", "attraction", 27.09, 77.66, tags=["fort"], meta={"wiki_links": 85})]
    monkeypatch.setattr(WP.wikidata, "sights", lambda city: wiki)
    places = WP.WikiAwarePlaces(Base())
    assert [p.name for p in places.attractions(BHARATPUR, ["fort"])] == ["Fatehpur Sikri"] and calls[-1] == wiki  # free: lean
    assert [p.name for p in places.attractions(BHARATPUR, ["fort"], thorough=True)] == ["Fatehpur Sikri"]
    assert calls[-1] is None  # Plus: Google searches everything, Wikidata's extras still merged in
