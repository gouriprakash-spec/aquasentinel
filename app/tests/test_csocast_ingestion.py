"""Tests for app/ingestion/csocast.py - radius and per-outfall freshness filtering.

The freshness filter is per-outfall, not whole-feed, because the real closest Delaware
outfall to Penn's Landing (D_54, 0.11km) has had a stale LastPoll since 2024-01-26 - see
docs/superpowers/specs/2026-10-01-cso-overflow-rule-milestone6-design.md's Scope decision 2.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import config
from app.ingestion.csocast import CsoDataUnavailable, fetch_nearby_outfalls

KM_PER_DEGREE_LAT = 111.32  # good enough at the 1km/8km/25h test offsets used below
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _offset_lat(km_north: float) -> float:
    return config.LOCATION_LAT + km_north / KM_PER_DEGREE_LAT


def _feature(name: str, status: int, km_from_penns_landing: float, age_hours: float) -> dict:
    last_poll = NOW - timedelta(hours=age_hours)
    return {
        "properties": {
            "Name": name,
            "Status": status,
            "Status_Message": "test fixture",
            "LastPoll": int(last_poll.timestamp() * 1000),
            "Latitude": _offset_lat(km_from_penns_landing),
            "Longitude": config.LOCATION_LON,
            "Waterbody": "D",
        }
    }


def _client_for(features: list[dict]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"type": "FeatureCollection", "features": features})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_excludes_outfall_beyond_the_radius():
    features = [
        _feature("D_near", status=1, km_from_penns_landing=1.0, age_hours=0.5),
        _feature("D_far", status=1, km_from_penns_landing=8.0, age_hours=0.5),
    ]
    result = fetch_nearby_outfalls(client=_client_for(features), now=NOW)

    assert {r.name for r in result} == {"D_near"}


def test_excludes_a_closer_but_stale_outfall_in_favor_of_a_farther_fresh_one():
    """The D_54 case: the real closest Delaware outfall to Penn's Landing has been stale
    since 2024. Per-outfall freshness must exclude it without blocking a farther, fresh,
    actually-overflowing outfall from still triggering the rule."""
    features = [
        _feature("D_54_like_stale", status=4, km_from_penns_landing=0.1, age_hours=48),
        _feature("D_52_like_fresh", status=4, km_from_penns_landing=1.0, age_hours=0.3),
    ]
    result = fetch_nearby_outfalls(client=_client_for(features), now=NOW)

    assert {r.name for r in result} == {"D_52_like_fresh"}


def test_excludes_a_stale_outfall_even_when_it_is_the_only_one_in_radius():
    features = [_feature("D_stale_only", status=4, km_from_penns_landing=1.0, age_hours=25)]
    result = fetch_nearby_outfalls(client=_client_for(features), now=NOW)

    assert result == []


def test_empty_result_is_not_an_error():
    result = fetch_nearby_outfalls(client=_client_for([]), now=NOW)

    assert result == []


def test_fails_closed_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(CsoDataUnavailable):
        fetch_nearby_outfalls(client=client, now=NOW)


def test_fails_closed_on_unexpected_response_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(CsoDataUnavailable):
        fetch_nearby_outfalls(client=client, now=NOW)


def test_returns_the_outfalls_status_for_the_rule_to_check():
    features = [_feature("D_overflow", status=4, km_from_penns_landing=1.0, age_hours=0.3)]
    result = fetch_nearby_outfalls(client=_client_for(features), now=NOW)

    assert result[0].status == 4
    assert result[0].name == "D_overflow"
    assert result[0].distance_km < 1.1


# --- Final-review finding (Important #2): malformed CSOcast responses must never 500 the
# whole reading - a glitch here must always degrade to CsoDataUnavailable (caught by
# app.scoring.pull_reading._fetch_cso_signal() as "no CSO signal"), never propagate as some
# other exception type. ---


def test_fails_closed_on_an_html_200_body():
    """response.json() raises a JSONDecodeError (a ValueError) when the server serves an
    HTML error page with a 200 status - this must still degrade to CsoDataUnavailable."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>Service Temporarily Unavailable</body></html>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(CsoDataUnavailable):
        fetch_nearby_outfalls(client=client, now=NOW)


def test_fails_closed_on_null_features():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"type": "FeatureCollection", "features": None})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(CsoDataUnavailable):
        fetch_nearby_outfalls(client=client, now=NOW)


def test_fails_closed_on_null_last_poll():
    feature = _feature("D_bad", status=1, km_from_penns_landing=1.0, age_hours=0.5)
    feature["properties"]["LastPoll"] = None

    client = _client_for([feature])
    with pytest.raises(CsoDataUnavailable):
        fetch_nearby_outfalls(client=client, now=NOW)


def test_fails_closed_on_an_absurdly_large_last_poll():
    """datetime.fromtimestamp raises OSError/OverflowError on a value way out of range -
    must still degrade to CsoDataUnavailable, not propagate as that exception type."""
    feature = _feature("D_bad", status=1, km_from_penns_landing=1.0, age_hours=0.5)
    feature["properties"]["LastPoll"] = 99999999999999999999

    client = _client_for([feature])
    with pytest.raises(CsoDataUnavailable):
        fetch_nearby_outfalls(client=client, now=NOW)


def test_excludes_an_outfall_with_nan_coordinates_instead_of_passing_the_radius_filter():
    """nan > CSO_NEARBY_RADIUS_KM is False in Python, so a naive `distance_km > radius`
    check would silently let a NaN-coordinate outfall pass the filter - the opposite of
    what's intended. This must be excluded, not treated as an error."""
    feature = _feature("D_nan", status=4, km_from_penns_landing=1.0, age_hours=0.3)
    feature["properties"]["Latitude"] = float("nan")

    result = fetch_nearby_outfalls(client=_client_for([feature]), now=NOW)

    assert result == []


def test_excludes_an_outfall_with_a_future_last_poll_instead_of_treating_it_as_fresh():
    """A negative age (clock-skewed/future LastPoll) must not silently pass the
    `age_hours > freshness` check as if it were fresh."""
    feature = _feature("D_future", status=4, km_from_penns_landing=1.0, age_hours=-1)

    result = fetch_nearby_outfalls(client=_client_for([feature]), now=NOW)

    assert result == []
