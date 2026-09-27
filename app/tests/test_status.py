"""Tests for app/status.py - the shared status-contract builder used identically by
GET /api/status and the MCP server's tools, so they can never disagree.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.status import build_status_contract, current_status


def _row() -> dict:
    return {
        "id": 1,
        "location": "penns_landing",
        "reading_time": "2026-09-27T12:00:00-04:00",
        "risk_tier": "Safe",
        "confidence": 0.82,
        "water_temp_c": 20.5,
        "sp_conductance_uscm": 270.0,
        "dissolved_oxygen_mgl": 7.0,
        "ph": 7.3,
        "turbidity_fnu": 5.0,
        "precip_mm": 0.0,
        "precip_prev_24h_mm": 2.0,
        "threshold_cfu_100ml": 235,
        "model_version": "rf_B_post2021",
        "regime": "B_post2021",
        "retrieved_at": "2026-09-27T16:00:00+00:00",
    }


def test_build_status_contract_maps_top_level_fields():
    contract = build_status_contract(_row())

    assert contract["location"] == "penns_landing"
    assert contract["time"] == "2026-09-27T12:00:00-04:00"
    assert contract["risk_tier"] == "Safe"
    assert contract["confidence"] == 0.82
    assert contract["source"] == "aquasentinel"
    assert contract["source_url"] == "https://waterservices.usgs.gov/nwis/iv/?sites=01467200"
    assert contract["retrieved_at"] == "2026-09-27T16:00:00+00:00"
    assert contract["threshold_cfu_100ml"] == 235
    assert contract["model_version"] == "rf_B_post2021"
    assert contract["regime"] == "B_post2021"
    assert contract["kind"] == "model_estimate"


def test_build_status_contract_proxies_shape():
    contract = build_status_contract(_row())

    assert contract["proxies"] == {
        "water_temp_c": 20.5,
        "sp_conductance_uscm": 270.0,
        "dissolved_oxygen_mgl": 7.0,
        "ph": 7.3,
        "turbidity_fnu": 5.0,
        "precip_mm": 0.0,
        "precip_prev_24h_mm": 2.0,
    }


def test_build_status_contract_never_includes_estimate_cfu_or_location_name():
    contract = build_status_contract(_row())

    assert "estimate_cfu_100ml" not in contract
    assert "location_name" not in contract


def test_current_status_returns_the_contract_for_a_fresh_reading():
    row = _row()
    row["reading_time"] = datetime.now(timezone.utc).isoformat()

    result = current_status(row)

    assert result["risk_tier"] == "Safe"
    assert result["kind"] == "model_estimate"


def test_current_status_fails_closed_on_a_stale_reading():
    """CLAUDE.md's non-negotiable rule: a gauge reading older than the freshness limit is
    "status unavailable", never an all-clear - this must hold for every surface that
    publishes "current status", not just the alert-gating decision for the same row."""
    row = _row()
    row["reading_time"] = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()

    result = current_status(row)

    assert result == {
        "status": "unavailable",
        "reason": "latest reading is stale (older than the freshness limit)",
    }


def test_current_status_returns_unavailable_when_no_reading_exists():
    result = current_status(None)

    assert result == {"status": "unavailable", "reason": "no readings yet"}
