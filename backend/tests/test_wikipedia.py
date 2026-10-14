""""About this place" from Wikipedia (ADR 0017): the right article or none, and never an error."""
from app.providers import wikipedia as W


def test_an_article_counts_only_when_its_title_names_the_place():
    assert W.names_match("Hawa Mahal", "Hawa Mahal")
    assert W.names_match("Nahargarh Fort", "Nahargarh Fort")
    assert W.names_match("Baga Beach", "Baga, Goa")
    assert not W.names_match("Hawa Mahal", "Jaipur")
    assert not W.names_match("Pinch of Spice", "Spice trade")


def test_pronunciation_is_taken_out_of_the_introduction():
    text = ("The Taj Mahal ( TAHJ mə-HAHL; Hindustani: [t̪ɑːd͡ʒ ˈmɛɦ(ɛ)l]; lit. 'Crown of the Palace') is a mausoleum "
            "in Agra. It was commissioned in 1631 by Shah Jahan (r. 1628–1658).")
    assert W._PRONOUNCE.sub("", text) == ("The Taj Mahal is a mausoleum in Agra. It was commissioned in 1631 by Shah "
                                          "Jahan (r. 1628–1658).")


def test_a_search_hit_for_the_city_is_not_taken_for_the_place(monkeypatch):
    monkeypatch.setattr(W, "cached", lambda name, key, ttl, fetch: fetch())
    monkeypatch.setattr(W, "_title_by_search", lambda name, near: None)
    # the name itself is an article about somewhere else: New York's Central Park is not Jaipur's
    monkeypatch.setattr(W, "_intro", lambda title: {"title": "Central Park", "extract": "Central Park is an urban park in "
                                                    "New York City.", "url": "https://en.wikipedia.org/wiki/Central_Park"})
    assert W.about("Central Park", "Jaipur") is None
    assert W.about("Central Park", "New York")["title"] == "Central Park"


def test_wikipedia_down_means_no_article_not_an_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("503")
    monkeypatch.setattr(W, "cached", boom)
    assert W.about("Hawa Mahal", "Jaipur") is None
