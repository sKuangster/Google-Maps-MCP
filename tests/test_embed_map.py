from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import ValidationError

import client
from client import EmbeddedMapRequest, ItineraryStop, TravelMode, build_itinerary_map

EMBED_BASE = "https://www.google.com/maps/embed/v1/directions"


@pytest.fixture(autouse=True)
def fake_embed_key(monkeypatch):
    monkeypatch.setattr(client, "EMBED_API_KEY", "test-key")


def _params(url: str) -> dict:
    assert url.startswith(EMBED_BASE)
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def three_stops() -> list[ItineraryStop]:
    return [
        ItineraryStop(name="Fort Greene Park", address="Fort Greene Park, Brooklyn, NY"),
        ItineraryStop(name="Roman's", address="243 DeKalb Ave, Brooklyn, NY", notes="dinner"),
        ItineraryStop(name="BAM", lat=40.6863, lng=-73.9771),
    ]


def test_walking_route_builds_single_embed_with_waypoints():
    result = build_itinerary_map(three_stops(), TravelMode.WALKING)

    params = _params(result["embed_url"])
    assert params["key"] == "test-key"
    assert params["mode"] == "walking"
    assert params["origin"] == "Fort Greene Park, Brooklyn, NY"
    assert params["waypoints"] == "243 DeKalb Ave, Brooklyn, NY"
    assert params["destination"] == "40.6863,-73.9771"
    assert "leg_embed_urls" not in result


def test_latlng_preferred_over_address():
    stops = [
        ItineraryStop(name="A", address="somewhere", lat=1.5, lng=-2.5),
        ItineraryStop(name="B", address="elsewhere"),
    ]
    result = build_itinerary_map(stops, TravelMode.DRIVING)
    assert _params(result["embed_url"])["origin"] == "1.5,-2.5"


def test_two_stops_have_no_waypoints_param():
    result = build_itinerary_map(three_stops()[:2], TravelMode.DRIVING)
    assert "waypoints" not in _params(result["embed_url"])


def test_transit_multi_stop_builds_per_leg_embeds():
    result = build_itinerary_map(three_stops(), TravelMode.TRANSIT)

    assert "embed_url" not in result
    legs = result["leg_embed_urls"]
    assert len(legs) == 2
    first, second = _params(legs[0]), _params(legs[1])
    assert first["mode"] == second["mode"] == "transit"
    assert first["origin"] == "Fort Greene Park, Brooklyn, NY"
    assert first["destination"] == "243 DeKalb Ave, Brooklyn, NY"
    assert second["origin"] == "243 DeKalb Ave, Brooklyn, NY"
    assert second["destination"] == "40.6863,-73.9771"
    assert "waypoints" not in first and "waypoints" not in second


def test_transit_two_stops_uses_single_embed():
    result = build_itinerary_map(three_stops()[:2], TravelMode.TRANSIT)
    assert "leg_embed_urls" not in result
    assert _params(result["embed_url"])["mode"] == "transit"


def test_maps_link_fallback():
    result = build_itinerary_map(three_stops(), TravelMode.WALKING)

    parsed = urlparse(result["maps_link"])
    assert parsed.netloc == "www.google.com" and parsed.path == "/maps/dir/"
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert params["api"] == "1"
    assert params["origin"] == "Fort Greene Park, Brooklyn, NY"
    assert params["destination"] == "40.6863,-73.9771"
    assert params["waypoints"] == "243 DeKalb Ave, Brooklyn, NY"
    assert params["travelmode"] == "walking"
    assert "key" not in params


def test_result_echoes_stops_and_mode():
    result = build_itinerary_map(three_stops(), TravelMode.WALKING)
    assert result["mode"] == "walking"
    assert [s["name"] for s in result["stops"]] == ["Fort Greene Park", "Roman's", "BAM"]
    assert result["stops"][1]["notes"] == "dinner"


def test_stop_requires_address_or_full_coords():
    with pytest.raises(ValidationError):
        ItineraryStop(name="nowhere")
    with pytest.raises(ValidationError):
        ItineraryStop(name="half", lat=40.0)
    ItineraryStop(name="ok-addr", address="x")
    ItineraryStop(name="ok-coords", lat=40.0, lng=-73.0)


def test_request_enforces_stop_count():
    one = [ItineraryStop(name="A", address="a")]
    with pytest.raises(ValidationError):
        EmbeddedMapRequest(stops=one)
    with pytest.raises(ValidationError):
        EmbeddedMapRequest(stops=[ItineraryStop(name=f"S{i}", address=f"a{i}") for i in range(23)])
    EmbeddedMapRequest(stops=one * 2)  # minimum of 2 is valid
