"""Tests for app/ingestion/nws.py's rainfall windowing.

The two things most likely to be wrong here, per BUILD-SPEC.md's own disclosed
limitations: (1) summing every 5-minute observation instead of 3h-spaced marks would
count the same rain many times over, and (2) a `null` precipitationLast3Hours must only
be treated as 0mm when the observation's own weather description confirms no rain.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.ingestion.nws import RainfallUnavailable, fetch_antecedent_rainfall

NOW = datetime(2026, 9, 25, 21, 0, 0, tzinfo=timezone.utc)  # 5pm US/Eastern (EDT, UTC-4)


def _observation(
    timestamp: datetime,
    precip_mm: float | None,
    description: str = "Partly Cloudy",
    present_weather: list | None = None,
) -> dict:
    return {
        "properties": {
            "timestamp": timestamp.isoformat(),
            "textDescription": description,
            "presentWeather": present_weather or [],
            "precipitationLast3Hours": {"unitCode": "wmoUnit:mm", "value": precip_mm},
        }
    }


def _client_for(observations: list[dict]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"features": observations})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_does_not_double_count_overlapping_5_minute_observations():
    """Every observation reports 1.0mm for its OWN rolling 3h window. If the code just
    summed every 5-minute record in range, 24h would give ~288mm (288 records). The
    correct mark-based total is 8mm (8 non-overlapping 3h marks x 1.0mm).
    """
    observations = [
        _observation(NOW - timedelta(minutes=5 * i), precip_mm=1.0)
        for i in range(0, 24 * 12)  # every 5 minutes for 24h - just enough for the 24h window
    ]

    result = fetch_antecedent_rainfall(client=_client_for(observations), now=NOW)

    assert result["precip_prev_24h_mm"] == 8.0
    assert "precip_prev_48h_mm" not in result  # dropped - see app/model/train.py


def test_works_with_only_a_single_nws_requests_worth_of_history():
    """A real NWS request (limit=500, ~5min spacing) covers ~38-40h, not the full 45h
    that a 48h window would need. This is exactly why 48h was dropped from the model:
    simulate that realistic ~38h ceiling and confirm 24h (which only needs data back to
    21h ago) still resolves fine from it.
    """
    observations = [
        _observation(NOW - timedelta(minutes=5 * i), precip_mm=0.5)
        for i in range(0, 38 * 12)  # ~38h of history, nothing further back
    ]

    result = fetch_antecedent_rainfall(client=_client_for(observations), now=NOW)

    assert result["precip_prev_24h_mm"] == 4.0


def test_null_precip_treated_as_zero_when_weather_confirms_no_rain():
    observations = [
        _observation(NOW - timedelta(minutes=5 * i), precip_mm=None, description="Clear")
        for i in range(0, 24 * 12)
    ]

    result = fetch_antecedent_rainfall(client=_client_for(observations), now=NOW)

    assert result["precip_prev_24h_mm"] == 0.0


def test_fails_closed_when_null_precip_cannot_be_confirmed_dry():
    # presentWeather is non-empty (some phenomenon reported) but no precip value given -
    # per the disclosed rule, this must NOT be assumed to be 0.
    observations = [
        _observation(NOW, precip_mm=None, present_weather=["some_phenomenon"]),
    ]

    with pytest.raises(RainfallUnavailable):
        fetch_antecedent_rainfall(client=_client_for(observations), now=NOW)


def test_fails_closed_when_no_observation_near_a_required_mark():
    # Only one observation, nowhere near most of the 24h marks.
    observations = [_observation(NOW, precip_mm=0.0)]

    with pytest.raises(RainfallUnavailable):
        fetch_antecedent_rainfall(client=_client_for(observations), now=NOW)


def test_fails_closed_on_empty_response():
    with pytest.raises(RainfallUnavailable):
        fetch_antecedent_rainfall(client=_client_for([]), now=NOW)


def test_fails_closed_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RainfallUnavailable):
        fetch_antecedent_rainfall(client=client, now=NOW)
