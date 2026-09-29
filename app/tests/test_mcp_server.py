"""Tests for app/mcp_server.py - the read-only MCP server's three tools.

Per the mcp Python SDK (v1): @mcp.tool()-decorated functions remain directly callable as
plain async functions (verified directly - the decorator registers the function with the
server as a side effect and returns the original function unchanged), so these tests call
the tools directly rather than going through the full MCP JSON-RPC protocol. The full
protocol-level integration is covered in Task 6, once the server is actually mounted.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app import db
from app import mcp_server


def _fake_reading(risk_tier: str = "Safe") -> dict:
    # "time" is computed relative to now, not a fixed past date - a fixed date would
    # eventually trip the freshness check (config.FRESHNESS_LIMIT_HOURS) once enough real
    # time passes, exactly the stale-fixture bug already fixed once in test_server.py.
    return {
        "location": "penns_landing",
        "location_name": "Penn's Landing, Center City tidal Delaware",
        "time": datetime.now(timezone.utc).isoformat(),
        "risk_tier": risk_tier,
        "confidence": 0.82,
        "source": "aquasentinel",
        "source_url": "https://waterservices.usgs.gov/nwis/iv/?sites=01467200",
        "retrieved_at": "2026-09-27T16:00:00+00:00",
        "evidence": {
            "proxies": {
                "water_temp_c": 20.5, "sp_conductance_uscm": 270.0,
                "dissolved_oxygen_mgl": 7.0, "ph": 7.3, "turbidity_fnu": 5.0,
            },
            "proxy_timestamps": {},
            "rainfall_mm": {"precip_mm": 0.0, "precip_prev_24h_mm": 2.0},
        },
        "threshold_cfu_100ml": 235,
        "model_version": "rf_B_post2021",
        "regime": "B_post2021",
        "kind": "model_estimate",
    }


def test_list_monitored_locations_returns_the_one_known_location(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()

    locations = asyncio.run(mcp_server.list_monitored_locations())

    assert locations == [{
        "id": "penns_landing",
        "name": "Penn's Landing, Center City tidal Delaware",
        "latitude": 39.946402,
        "longitude": -75.139360,
        "gauge_id": "01467200",
    }]


def test_get_current_status_matches_a_stored_reading(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    db.insert_reading(_fake_reading("Unsafe"))

    status = asyncio.run(mcp_server.get_current_status("penns_landing"))

    assert status["risk_tier"] == "Unsafe"
    assert status["location"] == "penns_landing"


def test_get_current_status_unknown_location_is_unavailable_not_a_guess(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    db.insert_reading(_fake_reading("Safe"))

    status = asyncio.run(mcp_server.get_current_status("somewhere_else"))

    assert status == {"status": "unavailable", "reason": "Unknown location_id: somewhere_else"}


def test_get_current_status_empty_database_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()

    status = asyncio.run(mcp_server.get_current_status("penns_landing"))

    assert status == {"status": "unavailable", "reason": "no readings yet"}


def test_get_current_status_fails_closed_on_a_stale_reading(monkeypatch, tmp_path):
    """Final-review finding (Critical): get_current_status previously published the newest
    stored row as-is, even when it was too old to trust - contradicting the gating decision
    for that same row (CLAUDE.md's "Fail closed" rule)."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    stale_reading = _fake_reading("Safe")
    stale_reading["time"] = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    db.insert_reading(stale_reading)

    status = asyncio.run(mcp_server.get_current_status("penns_landing"))

    assert status == {
        "status": "unavailable",
        "reason": "latest reading is stale (older than the freshness limit)",
    }


def test_get_recent_readings_respects_limit_and_orders_newest_first(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    db.insert_reading(_fake_reading("Safe"))
    db.insert_reading(_fake_reading("Unsafe"))

    readings = asyncio.run(mcp_server.get_recent_readings("penns_landing", limit=1))

    assert len(readings) == 1
    assert readings[0]["risk_tier"] == "Unsafe"


def test_get_recent_readings_unknown_location_returns_empty_list(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    db.insert_reading(_fake_reading("Safe"))

    readings = asyncio.run(mcp_server.get_recent_readings("somewhere_else"))

    assert readings == []


def test_no_write_tool_is_registered():
    """Architecture rule: agents read, code decides. A closed allowlist - if this ever
    fails because a new tool was added, that new tool needs its own explicit review for
    whether it's genuinely read-only before this assertion is updated."""
    tools = asyncio.run(mcp_server.mcp.list_tools())
    tool_names = {t.name for t in tools}

    assert tool_names == {"list_monitored_locations", "get_current_status", "get_recent_readings"}
