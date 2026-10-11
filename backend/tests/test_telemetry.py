"""Phase 0 measurement (ADR 0001): the trace sees every call, pair and stage of a plan, costs nothing when off,
keeps no personal data, and changes no behaviour."""
from datetime import date

import httpx
import pytest

from app.core import telemetry as T
from app.core.config import get_settings
from app.providers import http
from tests.conftest import register, trip_payload
from tests.test_named_trains import DEE_BACK, DEE_TRAINS, JAIPUR, NDLS_BACK, NDLS_TRAINS, NEW_DELHI, provider


@pytest.fixture
def fake_network(monkeypatch):
    """Every HTTP attempt answers 200 without touching the network; the tracing layer above it still runs."""
    seen = []

    def handle(self, request):
        seen.append(request)
        return httpx.Response(200, json={}, request=request)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle)
    return seen


def test_endpoint_templates_keep_no_ids_or_queries():
    assert T.endpoint_template("/v1/places/ChIJN1t_tDeuEmsRUsoyG83frY4") == "/v1/places/{id}"
    assert T.endpoint_template("/trains/between/JP/NDLS") == "/trains/between/{id}/{id}"
    assert T.provider_of("places.googleapis.com") == "google-places"


def test_every_http_attempt_is_recorded_with_its_list_price(fake_network):
    with T.plan_trace("unit") as tr:
        http.request("POST", "https://places.googleapis.com/v1/places:searchText?key=SECRET", provider="Google Places",
                     json={"textQuery": "Jaipur"})
        http.request("POST", "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix", provider="Google Routes",
                     json={"origins": [{}, {}, {}], "destinations": [{}, {}]})
    s = T.RECENT[-1]
    assert s["http_calls"] == 2 and s["http_errors"] == 0
    assert s["endpoints"]["google-places POST /v1/places:searchText"]["usd"] == 0.032
    assert s["endpoints"]["google-routes POST /distanceMatrix/v2:computeRouteMatrix"]["usd"] == pytest.approx(0.03)  # 6 elements
    text = repr(tr.calls) + repr(s)
    assert "SECRET" not in text and "Jaipur" not in text  # no keys, no search text


def test_rail_pairs_are_recorded_with_their_outcome():
    rr = provider({("JP", "NDLS"): NDLS_TRAINS, ("NDLS", "JP"): NDLS_BACK, ("JP", "DEE"): DEE_TRAINS, ("DEE", "JP"): DEE_BACK}, [])
    with T.plan_trace("rail"):
        rr.intercity(JAIPUR, NEW_DELHI, date(2026, 10, 10), date(2026, 10, 10))
    pairs = T.RECENT[-1]["rail_pairs"]
    assert pairs["tried"] >= 2 and pairs["outcomes"].get("trains", 0) >= 2
    assert {"source": "railradar", "pair": "JP-NDLS", "outcome": "trains", "trains": 3, "kind": "direct"} in pairs["detail"]


def test_pool_threads_are_attributed_to_the_plan(fake_network):
    with T.plan_trace("pool"):
        with T.ContextThreadPool(max_workers=3) as pool:
            list(pool.map(lambda _: http.request("GET", "https://api.open-meteo.com/v1/forecast", provider="Open-Meteo"), range(3)))
    assert T.RECENT[-1]["http_calls"] == 3


def test_a_whole_plan_is_measured(client):
    auth = register(client)
    trip = client.post("/api/trips", json=trip_payload(), headers=auth).json()
    last = T.RECENT[-1] if T.RECENT else None
    assert client.post(f"/api/trips/{trip['id']}/plan", headers=auth).status_code == 200
    s = T.RECENT[-1]
    assert s is not last and s["label"].startswith("Gurugram")
    assert s["first_event_ms"] is not None and s["total_ms"] >= s["first_event_ms"]
    assert [st["name"] for st in s["stages"]][:2] == ["stage.understand", "stage.routes"]
    assert s["db"]["queries"] > 0


def test_off_means_off(fake_network):
    st = get_settings()
    st.telemetry_enabled = False
    try:
        before = len(T.RECENT)
        with T.plan_trace("off") as tr:
            http.request("GET", "https://api.open-meteo.com/v1/forecast", provider="Open-Meteo")
        assert tr is None and len(T.RECENT) == before
    finally:
        st.telemetry_enabled = None
