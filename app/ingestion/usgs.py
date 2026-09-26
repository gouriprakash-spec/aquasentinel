"""Fetch live water-quality proxies from the USGS Penn's Landing gauge (01467200).

Handles the multi-block gotcha (checked Sep 23, re-verified live Sep 25, 2026): each
parameter's `values` array can hold more than one block. Block 0 is NOT reliably empty -
it can hold a real but years-stale reading (e.g. from 2020-12-03, `qualifiers: ["A"]` =
"Approved", from a discontinued method with no `method` label), while the current live
reading sits in a later block (`qualifiers: ["P"]` = provisional, method mentioning
"ISM Test Bed (barge)" - though the exact wording differs slightly per parameter, so
matching on method text is fragile). The robust rule used here: for each parameter, take
the value - across ALL of its blocks, not a fixed index - with the newest `dateTime`.

See docs/landing-page/BUILD-SPEC.md TODO 1 step 1 for the full writeup.
"""

from __future__ import annotations

from datetime import datetime
from typing import NamedTuple

import httpx

USGS_SITE = "01467200"
USGS_IV_URL = "https://waterservices.usgs.gov/nwis/iv/"

# USGS parameter code -> our feature name.
PARAMETER_CODES = {
    "00010": "water_temp_c",
    "00095": "sp_conductance_uscm",
    "00300": "dissolved_oxygen_mgl",
    "00400": "ph",
    "63680": "turbidity_fnu",
}


class ProxyReading(NamedTuple):
    value: float
    retrieved_at: datetime  # the reading's own dateTime, from USGS, tz-aware
    qualifier: str  # "P" (provisional) or "A" (approved), as USGS reports it


class UsgsDataUnavailable(RuntimeError):
    """Raised when USGS can't be fetched or parsed - fail closed, don't guess."""


def fetch_usgs_proxies(client: httpx.Client | None = None) -> dict[str, ProxyReading]:
    """Return {feature_name: ProxyReading} for the freshest block of each parameter.

    Raises UsgsDataUnavailable if the request fails, the response shape is unexpected,
    or any expected parameter is missing from the response entirely.
    """
    owns_client = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        response = client.get(
            USGS_IV_URL,
            params={
                "format": "json",
                "sites": USGS_SITE,
                "parameterCd": ",".join(PARAMETER_CODES),
            },
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise UsgsDataUnavailable(f"USGS request failed: {exc}") from exc
    finally:
        if owns_client:
            client.close()

    try:
        time_series = payload["value"]["timeSeries"]
    except (KeyError, TypeError) as exc:
        raise UsgsDataUnavailable(f"Unexpected USGS response shape: {exc}") from exc

    readings: dict[str, ProxyReading] = {}
    for series in time_series:
        code = series["variable"]["variableCode"][0]["value"]
        feature_name = PARAMETER_CODES.get(code)
        if feature_name is None:
            continue

        freshest = _freshest_value(series.get("values", []))
        if freshest is None:
            continue

        readings[feature_name] = ProxyReading(
            value=float(freshest["value"]),
            retrieved_at=datetime.fromisoformat(freshest["dateTime"]),
            qualifier=(freshest.get("qualifiers") or [""])[0],
        )

    missing = set(PARAMETER_CODES.values()) - set(readings)
    if missing:
        raise UsgsDataUnavailable(f"USGS response missing parameters: {sorted(missing)}")

    return readings


def _freshest_value(value_blocks: list[dict]) -> dict | None:
    """Across ALL blocks' values, return the single value dict with the newest dateTime."""
    candidates = [v for block in value_blocks for v in block.get("value", [])]
    if not candidates:
        return None
    return max(candidates, key=lambda v: datetime.fromisoformat(v["dateTime"]))
