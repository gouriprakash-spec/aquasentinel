"""Tests for app/ingestion/open_meteo.py - the fallback rainfall source used only when
NWS's own observation is unavailable or ambiguous (see test_pull_reading.py for that
wiring). This module talks to Open-Meteo directly, so it's tested the same way
test_nws_ingestion.py tests NWS: a mocked transport returning a fixed JSON payload.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.ingestion.nws import RainfallUnavailable
from app.ingestion.open_meteo import fetch_antecedent_rainfall

# 5pm US/Eastern (EDT, UTC-4) on 2026-09-25 - local midnight was 17h earlier, at 04:00 UTC.
NOW = datetime(2026, 9, 25, 21, 0, 0, tzinfo=timezone.utc)


def _hourly_payload(hours_back: int, precip_mm: float | None = 1.0) -> dict:
    """One hourly entry per hour from `hours_back` hours ago through `now`, inclusive."""
    times = [(NOW - timedelta(hours=h)).isoformat() for h in range(hours_back, -1, -1)]
    values = [precip_mm] * len(times)
    return {"hourly": {"time": times, "precipitation": values}}


def _client_for(payload: dict) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sums_last_24_hours_for_precip_prev_24h_mm():
    # hours_back=76 (not just 30) so this fixture's reach matches the real request's
    # past_hours=76 (Milestone 1b) - fetch_antecedent_rainfall() now needs history back to
    # ~65h before `now` to validate precip_prev_48h_mm on EVERY call, even one that (like
    # this test) only asserts on the 24h figure.
    payload = _hourly_payload(hours_back=76, precip_mm=1.0)

    result = fetch_antecedent_rainfall(client=_client_for(payload), now=NOW)

    # Entries within the last 24h: -24h..0h inclusive = 25 entries x 1.0mm.
    assert result["precip_prev_24h_mm"] == 25.0


def test_sums_only_since_local_midnight_for_precip_mm():
    payload = _hourly_payload(hours_back=76, precip_mm=1.0)  # see comment above

    result = fetch_antecedent_rainfall(client=_client_for(payload), now=NOW)

    # Local (US/Eastern) midnight was 17h before NOW: entries -17h..0h = 18 entries x 1.0mm.
    assert result["precip_mm"] == 18.0


def test_null_hourly_values_are_excluded_not_summed_as_zero():
    payload = _hourly_payload(hours_back=76, precip_mm=1.0)  # see comment above
    payload["hourly"]["precipitation"][0] = None  # the oldest (-76h) entry is null

    result = fetch_antecedent_rainfall(client=_client_for(payload), now=NOW)

    # Still 25 valid entries within the 24h window (the null one was outside it anyway).
    assert result["precip_prev_24h_mm"] == 25.0


def test_fails_closed_when_all_hourly_values_are_null():
    payload = _hourly_payload(hours_back=24, precip_mm=None)

    with pytest.raises(RainfallUnavailable):
        fetch_antecedent_rainfall(client=_client_for(payload), now=NOW)


def test_fails_closed_on_malformed_response():
    with pytest.raises(RainfallUnavailable):
        fetch_antecedent_rainfall(client=_client_for({"hourly": {}}), now=NOW)


def test_fails_closed_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RainfallUnavailable):
        fetch_antecedent_rainfall(client=client, now=NOW)


def test_precip_prev_48h_mm_sums_the_two_prior_calendar_days():
    """Matches training's window (build_dataset.py:load_precip()) and NWS's own Milestone 1b
    implementation: the two FULL local (US/Eastern) calendar days before today, not a rolling
    48h-from-now window. NOW is 2026-09-25T21:00 UTC (5pm EDT); local midnight is
    2026-09-25T04:00 UTC, so "yesterday" is Sep 24 local and "the day before" is Sep 23 local."""
    times = [(NOW - timedelta(hours=h)).isoformat() for h in range(76, -1, -1)]
    values = [0.0] * len(times)
    yesterday_hour = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    day_before_hour = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    values[times.index(yesterday_hour)] = 1.0
    values[times.index(day_before_hour)] = 1.0
    payload = {"hourly": {"time": times, "precipitation": values}}

    result = fetch_antecedent_rainfall(client=_client_for(payload), now=NOW)

    assert result["precip_prev_48h_mm"] == 2.0


def test_fails_closed_when_hourly_history_does_not_reach_back_far_enough():
    payload = _hourly_payload(hours_back=30, precip_mm=0.0)  # nowhere near the 48h+ needed

    with pytest.raises(RainfallUnavailable):
        fetch_antecedent_rainfall(client=_client_for(payload), now=NOW)
