"""Tests for app/ingestion/nws.py's rainfall windowing.

The two things most likely to be wrong here, per BUILD-SPEC.md's own disclosed
limitations: (1) summing every 5-minute observation instead of 3h-spaced marks would
count the same rain many times over, and (2) a `null` precipitationLast3Hours must only
be treated as 0mm when the observation's own weather description confirms no rain.

precip_prev_48h_mm (Milestone 1b, 2026-09-27) needs data further back than a single
`limit=500` request reaches, so fetch_antecedent_rainfall() now makes two requests: the
existing recent one, and a second `start`/`end`-bounded one for the older window. Tests
that need 48h coverage use _full_observations() to synthesize both windows; tests that
only ever cared about the 24h path keep using the older, shorter fixture unchanged.
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


def _full_observations(hours: int = 76, precip_mm: float = 0.5) -> list[dict]:
    """Enough 5-minute observations to cover every 3h mark back to `hours` before NOW -
    enough for precip_mm, precip_prev_24h_mm, AND precip_prev_48h_mm to all resolve."""
    return [
        _observation(NOW - timedelta(minutes=5 * i), precip_mm=precip_mm)
        for i in range(0, hours * 12)
    ]


def _observations_between(
    hours_back_min: int, hours_back_max: int, precip_mm: float
) -> list[dict]:
    """5-minute observations covering ONLY `hours_back_min`..`hours_back_max` hours before
    NOW - simulates a genuinely distinct `older` request whose reach doesn't overlap a
    `recent` fixture that only goes back to `hours_back_min` hours (or less). Used to prove
    a merge actually happened: if `recent` and `older` never share a timestamp and each
    carries its own precip value, a correct total can only come from combining both."""
    return [
        _observation(NOW - timedelta(minutes=m), precip_mm=precip_mm)
        for m in range(hours_back_min * 60, hours_back_max * 60 + 1, 5)
    ]


def _client_for(recent: list[dict], older: list[dict] | None = None) -> httpx.Client:
    """Simulates NWS's two-request shape: a plain `limit=500` request (recent) and a
    `start`/`end`-bounded request (older). Defaults `older` to `recent` so tests that don't
    care about the distinction can pass one list, as before."""
    older = recent if older is None else older

    def handler(request: httpx.Request) -> httpx.Response:
        if "start" in request.url.params:
            return httpx.Response(200, json={"features": older})
        return httpx.Response(200, json={"features": recent})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_does_not_double_count_overlapping_5_minute_observations():
    """Every observation reports 1.0mm for its OWN rolling 3h window. If the code just
    summed every 5-minute record in range, 24h would give ~288mm (288 records). The
    correct mark-based total is 8mm (8 non-overlapping 3h marks x 1.0mm)."""
    observations = _full_observations(hours=76, precip_mm=1.0)

    result = fetch_antecedent_rainfall(client=_client_for(observations), now=NOW)

    assert result["precip_prev_24h_mm"] == 8.0


def test_precip_prev_48h_mm_sums_the_two_prior_calendar_days():
    """Matches training's window (build_dataset.py:load_precip()): the two FULL local
    calendar days before today, not a rolling 48h-from-now window. 8 marks/day x 2 days x
    1.0mm/mark = 16.0mm, regardless of how much (if any) rain fell today."""
    observations = _full_observations(hours=76, precip_mm=1.0)

    result = fetch_antecedent_rainfall(client=_client_for(observations), now=NOW)

    assert result["precip_prev_48h_mm"] == 16.0


def test_precip_prev_48h_mm_only_resolves_by_combining_both_requests():
    """Regression guard for this task's entire stated purpose: if `nws.py` ever silently
    dropped `older_payload` (e.g. someone simplifies
    `_parse_observations(recent_payload) + _parse_observations(older_payload)` back down
    to just `recent_payload`), this test must fail - not any of the tests above it, which
    all pass the SAME 76h fixture as both `recent` and `older`, so `recent` alone already
    covers everything they need regardless of whether `older` was ever consulted.

    Here `recent` (38h, matching NWS's real single-request ceiling) and `older` (a
    disjoint 39h-76h slice, matching the real OLDER_WINDOW bounds) never share a
    timestamp and carry DIFFERENT precip values, so the expected 48h total (yesterday's
    8 marks from `recent` at 1.0mm + the day-before's 8 marks from `older` at 2.0mm) can
    only be correct if both were genuinely combined - dropping `older` would make the
    day-before-yesterday marks unresolvable (a raise, not a silently-passing wrong sum),
    and using only `older` would give the wrong total for yesterday."""
    recent = _full_observations(hours=38, precip_mm=1.0)  # covers yesterday, not further
    older = _observations_between(39, 76, precip_mm=2.0)  # covers only the day before that

    result = fetch_antecedent_rainfall(client=_client_for(recent, older=older), now=NOW)

    assert result["precip_prev_48h_mm"] == 24.0  # 8*1.0 (yesterday) + 8*2.0 (day before)


def test_fails_closed_when_the_older_window_is_incomplete():
    """The `start`/`end` request comes back too short to cover both prior calendar days -
    must fail closed, never a partial 48h figure."""
    recent = _full_observations(hours=40, precip_mm=1.0)
    older = _full_observations(hours=5, precip_mm=1.0)  # nowhere near enough

    with pytest.raises(RainfallUnavailable):
        fetch_antecedent_rainfall(client=_client_for(recent, older=older), now=NOW)


def test_fails_closed_even_when_24h_alone_would_have_resolved():
    """A real NWS request (limit=500, ~5min spacing) covers ~38-40h - plenty for 24h rain
    (which only needs data back to 21h ago) but nowhere near the ~62h back that the
    day-before-yesterday half of precip_prev_48h_mm needs. Before this task, that would
    have been fine: 48h wasn't computed at all, so 24h resolving on its own was enough.
    Now precip_prev_48h_mm is mandatory (Task 5's rainfall rule needs it to decide the
    tier), and the three fields are computed together in one dict - there's no "24h
    succeeds independently of 48h" anymore. So even though the RECENT request alone could
    satisfy precip_mm/precip_prev_24h_mm here, an empty OLDER request must still fail the
    whole fetch, per the fail-closed rule: never a guessed or partial rainfall figure."""
    recent = _full_observations(hours=38, precip_mm=0.5)

    with pytest.raises(RainfallUnavailable):
        fetch_antecedent_rainfall(client=_client_for(recent, older=[]), now=NOW)


def test_null_precip_treated_as_zero_when_weather_confirms_no_rain():
    observations = [
        _observation(NOW - timedelta(minutes=5 * i), precip_mm=None, description="Clear")
        for i in range(0, 76 * 12)
    ]

    result = fetch_antecedent_rainfall(client=_client_for(observations), now=NOW)

    assert result["precip_prev_24h_mm"] == 0.0
    assert result["precip_prev_48h_mm"] == 0.0


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
