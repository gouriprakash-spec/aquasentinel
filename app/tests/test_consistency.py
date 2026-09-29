"""Consistency test - Milestone 5's explicit Definition of Done (plan.md): MCP,
/api/status, and the dashboard's data source (GET /api/readings) must agree on tier and
timestamp for the same reading. One scoring output, read three ways, never computed twice.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import db, mcp_server, server
from app.fhir import store as fhir_store


def _fake_reading(risk_tier: str = "Unsafe") -> dict:
    # "time" is computed relative to now, not a fixed past date - see the identical fix in
    # test_mcp_server.py's _fake_reading() for why a fixed date eventually goes stale.
    return {
        "location": "penns_landing",
        "location_name": "Penn's Landing, Center City tidal Delaware",
        "time": datetime.now(timezone.utc).isoformat(),
        "risk_tier": risk_tier,
        "confidence": 0.91,
        "source": "aquasentinel",
        "source_url": "https://waterservices.usgs.gov/nwis/iv/?sites=01467200",
        "retrieved_at": "2026-09-27T16:00:00+00:00",
        "evidence": {
            "proxies": {
                "water_temp_c": 24.0, "sp_conductance_uscm": 300.0,
                "dissolved_oxygen_mgl": 4.5, "ph": 7.0, "turbidity_fnu": 40.0,
            },
            "proxy_timestamps": {},
            "rainfall_mm": {"precip_mm": 5.0, "precip_prev_24h_mm": 30.0},
        },
        "threshold_cfu_100ml": 235,
        "model_version": "rf_B_post2021",
        "regime": "B_post2021",
        "kind": "model_estimate",
    }


def test_mcp_status_and_readings_agree_on_tier_and_timestamp(monkeypatch, tmp_path):
    # Final-review finding (Important): this test's Unsafe reading makes /api/pull-reading
    # gate an unsafe_onset and write a real FHIR Flag - without also isolating
    # fhir_store's DB_PATH (only db.DB_PATH was patched here), that write landed in the
    # real dev aquasentinel.db instead of this test's tmp_path.
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(fhir_store, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(server, "pull_reading", lambda: _fake_reading("Unsafe"))
    db.init_db()
    fhir_store.init_db()

    client = TestClient(server.app)
    pulled = client.post("/api/pull-reading")
    assert pulled.status_code == 200

    # (a) what the dashboard banner renders from
    readings = db.get_recent_readings(limit=1)
    assert readings[0]["risk_tier"] == "Unsafe"
    banner_time = readings[0]["reading_time"]

    # (b) /api/status
    status_response = client.get("/api/status")
    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["risk_tier"] == "Unsafe"
    assert status_body["time"] == banner_time

    # (c) the MCP tool - called directly per Task 3's note on why that's valid
    mcp_result = asyncio.run(mcp_server.get_current_status("penns_landing"))
    assert mcp_result["risk_tier"] == "Unsafe"
    assert mcp_result["time"] == banner_time
