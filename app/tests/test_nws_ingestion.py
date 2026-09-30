"""Tests for app/ingestion/nws.py's rainfall windowing.

The fixtures here are shaped like REAL KPHL data (verified against a live week,
2026-09-23..30), not like the idealized feed the previous tests assumed. The old fixtures put
a `precipitationLast3Hours` value on every 5-minute record; in reality that field was filled
in on 8 of 2241 observations, which is how the old design could silently return 0.0mm for a
week with ~53mm of rain while every test still passed. Realistic shape:

- A record every 5 minutes with an empty `rawMessage` and every precip field null.
- A routine METAR at :54 each hour. `precipitationLastHour` has a value only when it rained
  that hour; otherwise it's null and the raw METAR simply has no `P` group in its remarks.
- During rain, mid-hour SPECI reports whose `precipitationLastHour` is a running subtotal of
  the same hour (must NOT be added on top of the :54 report).
- `precipitationLast3Hours` / `precipitationLast6Hours` null throughout.

The mock server honors `start`/`end`/`limit` (newest first, capped at 500) the way the real
endpoint does, so request-window bugs show up as missing hours, not silently.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.ingestion.nws import RainfallUnavailable, fetch_antecedent_rainfall

# 5pm US/Eastern (EDT, UTC-4). Local midnight today = 2026-09-25T04:00Z.
NOW = datetime(2026, 9, 25, 21, 0, 0, tzinfo=timezone.utc)
TODAY_MIDNIGHT = datetime(2026, 9, 25, 4, 0, tzinfo=timezone.utc)
YESTERDAY_MIDNIGHT = TODAY_MIDNIGHT - timedelta(days=1)  # Sep 24 local
DAY_BEFORE_MIDNIGHT = TODAY_MIDNIGHT - timedelta(days=2)  # Sep 23 local
FIXTURE_START = NOW - timedelta(hours=80)

_NULL_PRECIP = {"unitCode": "wmoUnit:mm", "value": None, "qualityControl": "Z"}


def _report_time(midnight: datetime, local_hour: int) -> datetime:
    """The :54 routine report closing the given local hour of the day that starts at
    `midnight` (local_hour=0 -> the 00:54 report)."""
    return midnight + timedelta(hours=local_hour, minutes=54)


def _raw_metar(ts: datetime, rain_mm: float | None, remarks_extra: str = "") -> str:
    """A realistic KPHL METAR. The P group (hundredths of an inch) appears only when it
    rained; other remark groups that start with P or contain digits are included on
    purpose so a sloppy parser would trip over them."""
    stamp = ts.strftime("%d%H%MZ")
    p_group = f" P{round(rain_mm / 0.254):04d}" if rain_mm else ""
    return (
        f"KPHL {stamp} 01016KT 10SM OVC030 17/13 A2982 RMK AO2 PK WND 01043/0631 "
        f"SLP102{p_group}{remarks_extra} T01830072 $"
    )


def _record(ts: datetime, raw: str = "", last_hour: float | None = None) -> dict:
    return {
        "properties": {
            "timestamp": ts.isoformat(),
            "rawMessage": raw,
            "textDescription": "Cloudy",
            "presentWeather": [],
            "precipitationLastHour": {"unitCode": "wmoUnit:mm", "value": last_hour},
            "precipitationLast3Hours": dict(_NULL_PRECIP),
            "precipitationLast6Hours": dict(_NULL_PRECIP),
        }
    }


def _station(
    rain: dict[datetime, float] | None = None,
    overrides: dict[datetime, dict | None] | None = None,
    start: datetime = FIXTURE_START,
    end: datetime = NOW,
) -> list[dict]:
    """Synthesize every record KPHL would publish in [start, end].

    rain: {routine_report_time: mm in the hour ending then}; all other hours are dry.
    overrides: {routine_report_time: replacement record, or None to drop that report}.
    """
    rain = rain or {}
    overrides = overrides or {}
    records = []
    ts = start.replace(minute=0, second=0, microsecond=0)
    while ts <= end:
        if start <= ts:
            if ts.minute == 54:
                if ts in overrides:
                    if overrides[ts] is not None:
                        records.append(overrides[ts])
                else:
                    mm = rain.get(ts)
                    records.append(_record(ts, raw=_raw_metar(ts, mm), last_hour=mm))
                    if mm:
                        # A SPECI 24 minutes earlier carrying a subtotal of the same hour.
                        speci = ts - timedelta(minutes=24)
                        records.append(_record(speci, raw=_raw_metar(speci, mm / 2), last_hour=mm / 2))
            elif ts.minute % 5 == 0:
                records.append(_record(ts))  # routine 5-minute record: no precip at all
        ts += timedelta(minutes=1)
    return records


def _client_for(records: list[dict], requests: list[httpx.Request] | None = None) -> httpx.Client:
    """Mock api.weather.gov: filter by start/end, newest first, capped at `limit`."""

    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        params = request.url.params
        start = datetime.fromisoformat(params["start"].replace("Z", "+00:00")) if "start" in params else None
        end = datetime.fromisoformat(params["end"].replace("Z", "+00:00")) if "end" in params else None
        limit = int(params.get("limit", 500))
        selected = []
        for record in records:
            ts = datetime.fromisoformat(record["properties"]["timestamp"])
            if (start is None or ts >= start) and (end is None or ts <= end):
                selected.append(record)
        selected.sort(key=lambda r: r["properties"]["timestamp"], reverse=True)
        return httpx.Response(200, json={"features": selected[:limit]})

    return httpx.Client(transport=httpx.MockTransport(handler))


# --- The Critical finding's regression -------------------------------------------------


def test_counts_rain_reported_only_in_precipitation_last_hour():
    """The exact shape that broke the old design: real rain is in `precipitationLastHour`
    on the :54 reports, and `precipitationLast3Hours` is null everywhere. The old
    3h-mark method read those nulls as 0mm and returned 0.0 here (a false Safe); the
    correct 48h total is 3.0mm, which crosses the 2.5mm rule."""
    rain = {
        _report_time(YESTERDAY_MIDNIGHT, 10): 1.5,
        _report_time(DAY_BEFORE_MIDNIGHT, 15): 1.5,
    }

    result = fetch_antecedent_rainfall(client=_client_for(_station(rain)), now=NOW)

    assert result["precip_prev_48h_mm"] == 3.0


def test_does_not_add_speci_subtotals_or_5_minute_records():
    """Each rainy hour also has a SPECI carrying half the hour's rain as a subtotal. Only
    the :54 routine report may count, or rain gets counted 1.5x."""
    rain = {_report_time(YESTERDAY_MIDNIGHT, h): 1.0 for h in range(0, 24)}

    result = fetch_antecedent_rainfall(client=_client_for(_station(rain)), now=NOW)

    assert result["precip_prev_48h_mm"] == 24.0


# --- Windows ---------------------------------------------------------------------------


def test_precip_prev_48h_mm_is_the_two_prior_calendar_days_only():
    """Matches training's window (build_dataset.py:load_precip()): the two FULL local
    calendar days before today. Rain today and three days ago must be excluded, and the
    first/last hour of each day must be included."""
    rain = {
        _report_time(DAY_BEFORE_MIDNIGHT, 0): 1.0,   # first hour of the window
        _report_time(YESTERDAY_MIDNIGHT, 23): 2.0,   # last hour of the window
        _report_time(TODAY_MIDNIGHT, 0): 4.0,        # today - excluded
        DAY_BEFORE_MIDNIGHT - timedelta(minutes=6): 8.0,  # 23:54 three days ago - excluded
    }

    result = fetch_antecedent_rainfall(client=_client_for(_station(rain)), now=NOW)

    assert result["precip_prev_48h_mm"] == 3.0


def test_precip_mm_and_24h_windows():
    """NOW is 21:00Z; with the 45-minute publishing allowance, the latest report counted is
    19:54Z (15:54 local). precip_mm = today's reports up to then; precip_prev_24h_mm = the
    24 reports ending then (20:54Z yesterday .. 19:54Z today)."""
    latest = datetime(2026, 9, 25, 19, 54, tzinfo=timezone.utc)
    rain = {
        latest: 1.0,                                  # today and within 24h
        _report_time(TODAY_MIDNIGHT, 0): 2.0,         # today and within 24h
        latest - timedelta(hours=23): 4.0,            # yesterday, oldest hour in 24h
        latest - timedelta(hours=24): 8.0,            # just outside 24h
        latest + timedelta(hours=1): 16.0,            # 20:54Z: not yet expected, ignored
    }

    result = fetch_antecedent_rainfall(client=_client_for(_station(rain)), now=NOW)

    assert result["precip_mm"] == 3.0
    assert result["precip_prev_24h_mm"] == 7.0


def test_dry_hours_resolve_to_zero_from_the_raw_metar():
    """A null `precipitationLastHour` with a raw METAR that has no P group is a real 0mm
    (METAR omits the group when it didn't rain). Other remark tokens (SLP..., PK WND) must
    not be mistaken for a precip group."""
    result = fetch_antecedent_rainfall(client=_client_for(_station()), now=NOW)

    assert result == {"precip_mm": 0.0, "precip_prev_24h_mm": 0.0, "precip_prev_48h_mm": 0.0}


def test_null_value_with_p_group_in_raw_metar_is_read_from_the_raw_text():
    """Seen live twice in one week: NWS's parsed `precipitationLastHour` was null while the
    raw METAR said P0002 (0.02in). Treating that null as 0 would undercount."""
    ts = _report_time(YESTERDAY_MIDNIGHT, 12)
    report = _record(ts, raw=_raw_metar(ts, 3 * 0.254), last_hour=None)  # P0003 = 0.762mm

    result = fetch_antecedent_rainfall(
        client=_client_for(_station(overrides={ts: report})), now=NOW
    )

    assert result["precip_prev_48h_mm"] == 0.8


# --- Fail closed -----------------------------------------------------------------------


def test_fails_closed_when_null_value_and_raw_metar_is_empty():
    """23 of 168 live routine reports looked like this. Nothing confirms the hour was dry,
    so it must raise - never count it as 0mm."""
    ts = _report_time(YESTERDAY_MIDNIGHT, 7)
    records = _station(overrides={ts: _record(ts, raw="", last_hour=None)})

    with pytest.raises(RainfallUnavailable, match="no raw METAR"):
        fetch_antecedent_rainfall(client=_client_for(records), now=NOW)


def test_fails_closed_when_rain_gauge_not_operating():
    ts = _report_time(DAY_BEFORE_MIDNIGHT, 3)
    report = _record(ts, raw=_raw_metar(ts, None, remarks_extra=" PNO"), last_hour=None)

    with pytest.raises(RainfallUnavailable, match="PNO"):
        fetch_antecedent_rainfall(client=_client_for(_station(overrides={ts: report})), now=NOW)


def test_fails_closed_when_rain_amount_indeterminable():
    ts = _report_time(DAY_BEFORE_MIDNIGHT, 3)
    report = _record(ts, raw=_raw_metar(ts, None, remarks_extra=" P////"), last_hour=None)

    with pytest.raises(RainfallUnavailable, match="indeterminable"):
        fetch_antecedent_rainfall(client=_client_for(_station(overrides={ts: report})), now=NOW)


def test_fails_closed_when_raw_metar_has_no_remarks():
    ts = _report_time(YESTERDAY_MIDNIGHT, 3)
    report = _record(ts, raw="KPHL 240754Z 01016KT 10SM OVC030 17/13 A2982", last_hour=None)

    with pytest.raises(RainfallUnavailable, match="remarks"):
        fetch_antecedent_rainfall(client=_client_for(_station(overrides={ts: report})), now=NOW)


def test_fails_closed_when_a_routine_report_is_missing():
    """The 5-minute records around the hour are all present, but the :54 report itself is
    not - its rain can't be accounted for from anything else."""
    ts = _report_time(DAY_BEFORE_MIDNIGHT, 20)

    with pytest.raises(RainfallUnavailable, match="No NWS routine report"):
        fetch_antecedent_rainfall(client=_client_for(_station(overrides={ts: None})), now=NOW)


def test_fails_closed_when_history_does_not_reach_back_far_enough():
    """Only ~38h of records exist (what one plain limit=500 request used to return): 24h
    would resolve, but the day-before-yesterday half of 48h can't, so the whole call must
    raise - there is no partial result."""
    records = _station(start=NOW - timedelta(hours=38))

    with pytest.raises(RainfallUnavailable):
        fetch_antecedent_rainfall(client=_client_for(records), now=NOW)


def test_requests_are_bounded_chunks_that_reach_the_start_of_the_48h_window():
    """Each request is start/end-bounded and at most 24h wide (so it stays well under the
    500-record cap and can't be truncated at its far end), and together they reach back
    before the first 48h report."""
    requests: list[httpx.Request] = []

    fetch_antecedent_rainfall(client=_client_for(_station(), requests=requests), now=NOW)

    spans = []
    for request in requests:
        params = request.url.params
        start = datetime.fromisoformat(params["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(params["end"].replace("Z", "+00:00"))
        assert end - start <= timedelta(hours=24)
        spans.append((start, end))
    assert min(start for start, _ in spans) <= DAY_BEFORE_MIDNIGHT
    assert max(end for _, end in spans) >= NOW - timedelta(minutes=1)


def test_fails_closed_on_empty_response():
    with pytest.raises(RainfallUnavailable):
        fetch_antecedent_rainfall(client=_client_for([]), now=NOW)


def test_fails_closed_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RainfallUnavailable):
        fetch_antecedent_rainfall(client=client, now=NOW)
