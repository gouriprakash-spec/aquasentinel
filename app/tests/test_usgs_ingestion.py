"""Tests for app/ingestion/usgs.py's multi-block parsing.

The fixture below mirrors a REAL response fetched live from USGS on 2026-09-25: block 0
is not empty, it holds a real but years-stale "Approved" reading with no method label,
and the current "ISM Test Bed (barge)" reading sits in a later, provisional block. The
parser must pick the freshest value by timestamp, not by block position or method text.
"""

from __future__ import annotations

import httpx
import pytest

from app.ingestion.usgs import UsgsDataUnavailable, fetch_usgs_proxies


def _timeseries_entry(code: str, name: str, value_blocks: list[dict]) -> dict:
    return {
        "variable": {"variableCode": [{"value": code}], "variableName": name},
        "values": value_blocks,
    }


def _stale_block(value: str) -> dict:
    return {
        "method": [{"methodDescription": ""}],
        "value": [{"value": value, "qualifiers": ["A"], "dateTime": "2020-12-03T09:30:00.000-05:00"}],
    }


def _live_block(value: str, method: str = "ISM Test Bed, [ISM Test Bed (barge)]") -> dict:
    return {
        "method": [{"methodDescription": method}],
        "value": [{"value": value, "qualifiers": ["P"], "dateTime": "2026-09-25T17:25:00.000-04:00"}],
    }


REALISTIC_PAYLOAD = {
    "value": {
        "timeSeries": [
            _timeseries_entry("00010", "Temperature, water", [_stale_block("8.8"), _live_block("21.5")]),
            _timeseries_entry("00095", "Specific conductance", [_stale_block("171"), _live_block("269")]),
            _timeseries_entry("00300", "Dissolved oxygen", [_stale_block("10.3"), _live_block("6.6")]),
            _timeseries_entry("00400", "pH", [_stale_block("7.2"), _live_block("7.3")]),
            # Turbidity only ever has one block, per the real Sep 23/25 checks.
            _timeseries_entry(
                "63680", "Turbidity", [_live_block("5.5", method="[ISM Test Bed (barge)]")]
            ),
        ]
    }
}


def _client_for(payload: dict) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_picks_the_freshest_block_not_block_zero():
    readings = fetch_usgs_proxies(client=_client_for(REALISTIC_PAYLOAD))

    assert readings["water_temp_c"].value == 21.5
    assert readings["water_temp_c"].qualifier == "P"
    assert readings["water_temp_c"].retrieved_at.year == 2026


def test_all_five_proxies_present():
    readings = fetch_usgs_proxies(client=_client_for(REALISTIC_PAYLOAD))

    assert set(readings) == {
        "water_temp_c", "sp_conductance_uscm", "dissolved_oxygen_mgl", "ph", "turbidity_fnu",
    }


def test_single_block_parameter_still_works():
    readings = fetch_usgs_proxies(client=_client_for(REALISTIC_PAYLOAD))

    assert readings["turbidity_fnu"].value == 5.5


def test_fails_closed_when_a_parameter_is_missing_entirely():
    incomplete_payload = {
        "value": {"timeSeries": REALISTIC_PAYLOAD["value"]["timeSeries"][:4]}  # drop turbidity
    }

    with pytest.raises(UsgsDataUnavailable, match="turbidity_fnu"):
        fetch_usgs_proxies(client=_client_for(incomplete_payload))


def test_fails_closed_on_unexpected_response_shape():
    with pytest.raises(UsgsDataUnavailable):
        fetch_usgs_proxies(client=_client_for({"unexpected": "shape"}))


def test_fails_closed_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(UsgsDataUnavailable):
        fetch_usgs_proxies(client=client)
