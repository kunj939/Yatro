"""Destination photos from Wikimedia Commons (providers/photos.py): the right place, a real photo, a short credit."""
from __future__ import annotations

import httpx

from app.core.errors import ProviderUnavailable
from app.providers import photos


def fake(monkeypatch, entities: dict, artist="Jane Doe", license_="CC BY-SA 4.0", fail=False):
    calls = []

    def request(method, url, *, provider, params=None, headers=None, **kw):
        calls.append(params.get("action"))
        if fail:
            raise ProviderUnavailable("Wikidata unavailable (timeout)")
        if params["action"] == "wbsearchentities":
            body = {"search": [{"id": i} for i in entities]}
        elif params["action"] == "wbgetentities":
            body = {"entities": entities}
        else:
            body = {"query": {"pages": {"1": {"imageinfo": [{"thumburl": f"https://upload.wikimedia.org/{params['titles']}",
                                                            "descriptionurl": "https://commons.wikimedia.org/wiki/x",
                                                            "extmetadata": {"Artist": {"value": f"<a href='u'>{artist}</a>"},
                                                                            "LicenseShortName": {"value": license_}}}]}}}}
        return httpx.Response(200, json=body)

    monkeypatch.setattr(photos.http, "request", request)
    monkeypatch.setattr(photos, "cached", lambda provider, key, ttl, fetch: fetch())
    return calls


def item(lat, lng, *files, banner=None):
    claims = {"P625": [{"mainsnak": {"datavalue": {"value": {"latitude": lat, "longitude": lng}}}}],
              "P18": [{"mainsnak": {"datavalue": {"value": f}}} for f in files]}
    if banner:
        claims["P948"] = [{"mainsnak": {"datavalue": {"value": banner}}}]
    return {"claims": claims}


def test_the_photo_of_the_right_place_with_its_credit(monkeypatch):
    fake(monkeypatch, {"Q1": item(51.0, 0.0, "Other_Rishikesh.jpg"), "Q2": item(30.09, 78.27, "Lakshman_Jhula.jpg")})
    out = photos.destination_photo("Rishikesh, Uttarakhand", 30.08, 78.27)
    assert out["url"].endswith("File:Lakshman_Jhula.jpg") and out["credit"] == "Jane Doe" and out["license"] == "CC BY-SA 4.0"


def test_maps_flags_and_collages_are_skipped_for_the_banner(monkeypatch):
    fake(monkeypatch, {"Q2": item(26.9, 75.8, "Jaipur_collage.jpg", "Jaipur_locator_map.svg", banner="Jaipur_banner.jpg")})
    assert photos.destination_photo("Jaipur", 26.9, 75.8)["url"].endswith("File:Jaipur_banner.jpg")


def test_a_long_artist_field_becomes_a_short_credit(monkeypatch):
    fake(monkeypatch, {"Q2": item(26.9, 75.8, "Amer.jpg")}, artist="Amer_Fort.jpg: This image was created by User:X. If you are")
    assert photos.destination_photo("Jaipur", 26.9, 75.8)["credit"] == "Wikimedia Commons"


def test_no_photo_and_failures_are_just_none(monkeypatch):
    fake(monkeypatch, {"Q2": item(26.9, 75.8)})
    assert photos.destination_photo("Nowhere", 26.9, 75.8) is None
    fake(monkeypatch, {}, fail=True)
    assert photos.destination_photo("Jaipur", 26.9, 75.8) is None


def test_the_endpoint(client, monkeypatch):
    fake(monkeypatch, {"Q2": item(15.3, 74.1, "Goa_beach.jpg")})
    r = client.get("/api/places/photo", params={"name": "Goa", "lat": 15.3, "lng": 74.1})
    assert r.status_code == 200 and r.json()["photo"]["url"].endswith("File:Goa_beach.jpg")
