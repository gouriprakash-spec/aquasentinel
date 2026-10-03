"""When the USGS gauge has no current reading, the system still produces a reading (Gouri,
2026-10-03): the tier comes from the rainfall rule and the overflow rule - neither uses the gauge,
which only fed the model's confidence - and the water-quality values and the rule/model agreement
are "n/a". The reading's time is the pull time, so it counts as a normal reading for status and for
the agency alert logic (including the 48-hour all-clear).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import config, db, mcp_server, server, status
from app.alerts.gating import evaluate_reading
from app.fhir import resources
from app.fhir import store as fhir_store
from app.ingestion.csocast import OutfallReading
from app.ingestion.nws import RainfallUnavailable
from app.ingestion.usgs import UsgsDataUnavailable
from app.scoring import pull_reading as pr
from app.tests.test_pull_reading import _fake_proxies, _fake_rainfall, _stub_bundle
from app.tests.test_server import _fake_reading


def _usgs_down():
    raise UsgsDataUnavailable("USGS request failed: Server error '503'")


def _overflowing() -> list:
    return [OutfallReading(name="D_23", status=3, distance_km=4.6,
                           last_poll=datetime.now(timezone.utc), latitude=39.97, longitude=-75.1)]


def _pull(monkeypatch, *, usgs, rain: float = 0.0, cso=lambda: []):
    monkeypatch.setattr(pr, "fetch_usgs_proxies", usgs)
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall(precip_prev_48h_mm=rain))
    monkeypatch.setattr(pr, "_load_model", lambda: _stub_bundle(probability_unsafe=0.1))
    monkeypatch.setattr(pr, "fetch_cso_outfalls", cso)
    return pr.pull_reading()


# --- the pull ---

def test_a_usgs_error_still_yields_a_reading_without_gauge_data(monkeypatch):
    reading = _pull(monkeypatch, usgs=_usgs_down)

    assert reading["evidence"]["gauge_available"] is False
    assert reading["confidence"] is None                       # the model cannot run without the gauge
    assert reading["evidence"]["model_probability_unsafe"] is None
    assert reading["evidence"]["proxies"] == {}                # nothing invented
    assert reading["time"] == reading["retrieved_at"]          # the pull time
    assert reading["risk_tier"] == "Safe"                      # from the rainfall rule (0 mm)
    assert reading["evidence"]["rainfall_mm"]["precip_prev_48h_mm"] == 0.0


def test_gauge_data_older_than_the_limit_is_treated_as_no_gauge_data(monkeypatch):
    old = _fake_proxies(age_hours=config.FRESHNESS_LIMIT_HOURS + 1)

    reading = _pull(monkeypatch, usgs=lambda: old)

    assert reading["evidence"]["gauge_available"] is False
    assert reading["evidence"]["proxies"] == {}
    assert reading["time"] == reading["retrieved_at"]  # NOT the gauge's old measurement time


def test_fresh_gauge_data_still_gives_the_full_reading(monkeypatch):
    reading = _pull(monkeypatch, usgs=lambda: _fake_proxies())

    assert reading["evidence"]["gauge_available"] is True
    assert len(reading["evidence"]["proxies"]) == 5
    assert reading["confidence"] == pytest.approx(0.9)


def test_the_rainfall_rule_still_decides_without_the_gauge(monkeypatch):
    reading = _pull(monkeypatch, usgs=_usgs_down, rain=5.0)

    assert reading["risk_tier"] == "Unsafe"
    assert reading["evidence"]["decision_basis"] == "rainfall_rule"


def test_an_overflow_still_forces_unsafe_without_the_gauge(monkeypatch):
    reading = _pull(monkeypatch, usgs=_usgs_down, cso=_overflowing)

    assert reading["risk_tier"] == "Unsafe"
    assert reading["evidence"]["decision_basis"] == "cso_overflow_rule"
    assert reading["confidence"] is None


def test_missing_rainfall_still_fails_closed(monkeypatch):
    monkeypatch.setattr(pr, "fetch_usgs_proxies", _usgs_down)

    def rainfall_down():
        raise RainfallUnavailable("both rainfall sources failed")

    monkeypatch.setattr(pr, "_fetch_rainfall", rainfall_down)
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [])

    with pytest.raises(RainfallUnavailable):
        pr.pull_reading()  # no rainfall = no tier = no reading


# --- storage ---

def _gauge_less(tier="Safe") -> dict:
    reading = _fake_reading(tier)
    reading["time"] = datetime.now(timezone.utc).isoformat()
    reading["retrieved_at"] = reading["time"]
    reading["confidence"] = None
    reading["evidence"]["gauge_available"] = False
    reading["evidence"]["proxies"] = {}
    reading["evidence"]["model_probability_unsafe"] = None
    reading["model_version"] = "rainfall_rule_only"
    reading["regime"] = "no_gauge"
    return reading


def test_a_gauge_less_reading_is_stored_with_empty_water_quality_columns(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()

    db.insert_reading(_gauge_less())
    row = db.get_recent_readings(limit=1)[0]

    assert row["gauge_available"] == 0
    assert all(row[c] is None for c in ("water_temp_c", "sp_conductance_uscm", "dissolved_oxygen_mgl", "ph", "turbidity_fnu"))
    assert row["model_probability_unsafe"] is None


def test_a_database_from_before_this_change_is_migrated_with_gauge_available_set_to_1(tmp_path):
    path = tmp_path / "old.db"
    db.init_db(path)
    db.insert_reading(_fake_reading("Safe"), db_path=path)
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE readings DROP COLUMN gauge_available")

    db.init_db(path)

    assert db.get_recent_readings(db_path=path)[0]["gauge_available"] == 1  # old rows all had gauge data


# --- the contract ---

def test_the_status_contract_says_na_not_zero_for_a_gauge_less_reading(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    db.insert_reading(_gauge_less("Unsafe"))

    contract = status.build_status_contract(db.get_recent_readings(limit=1)[0])

    assert contract["gauge_available"] is False
    assert contract["confidence"] is None
    assert contract["model_probability_unsafe"] is None
    assert all(value is None for key, value in contract["proxies"].items() if key in
               ("water_temp_c", "sp_conductance_uscm", "dissolved_oxygen_mgl", "ph", "turbidity_fnu"))
    assert contract["risk_tier"] == "Unsafe"


def test_a_fresh_gauge_less_reading_is_a_real_current_status_not_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    db.insert_reading(_gauge_less("Safe"))

    current = status.current_status(db.get_recent_readings(limit=1)[0])

    assert current["risk_tier"] == "Safe"
    assert current["gauge_available"] is False


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(fhir_store, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    fhir_store.init_db()
    return TestClient(server.app)


def test_readings_api_flags_the_gauge_and_names_when_it_last_reported(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    last_good = _fake_reading("Safe")
    last_good["time"] = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    db.insert_reading(last_good)
    db.insert_reading(_gauge_less("Safe"))

    rows = client.get("/api/readings").json()

    assert rows[0]["gauge_available"] is False and rows[0]["confidence"] is None
    assert rows[1]["gauge_available"] is True
    assert rows[0]["last_gauge_reading_time"] == last_good["time"]


def test_status_api_and_mcp_agree_on_a_gauge_less_reading(monkeypatch, tmp_path):
    import asyncio

    client = _client(monkeypatch, tmp_path)
    db.insert_reading(_gauge_less("Unsafe"))

    api = client.get("/api/status").json()
    mcp = asyncio.run(mcp_server.get_current_status("penns_landing"))

    for surface in (api, mcp):
        assert surface["risk_tier"] == "Unsafe"
        assert surface["confidence"] is None
        assert surface["gauge_available"] is False


# --- the scheduled pull ---

def test_the_scheduled_pull_stores_a_gauge_less_row_when_usgs_is_down(monkeypatch, tmp_path):
    _client(monkeypatch, tmp_path)
    monkeypatch.setattr(pr, "fetch_usgs_proxies", _usgs_down)
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall())
    monkeypatch.setattr(pr, "_load_model", lambda: _stub_bundle(0.1))
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [])
    monkeypatch.setattr(server, "pull_reading", pr.pull_reading)

    server._scheduled_pull()

    rows = db.get_recent_readings(limit=5)
    assert len(rows) == 1 and rows[0]["gauge_available"] == 0


# --- the agency alert logic ---

def test_gauge_less_readings_count_toward_the_48_hour_all_clear(tmp_path):
    """Gouri, 2026-10-03: the gauge never decided the tier, so a gauge-less Safe reading counts like
    any other - 48 continuous hours of Safe end an alert whether or not the probe was reporting."""
    path = tmp_path / "gating.db"
    db.init_db(path)
    now = datetime.now(timezone.utc)
    db.upsert_alert_state(
        location="penns_landing", current_tier="Unsafe",
        safe_streak_started_at=(now - timedelta(hours=49)).isoformat(),
        safe_accumulated_seconds=0.0, updated_at=now.isoformat(), db_path=path,
    )

    decision = evaluate_reading(_gauge_less("Safe"), db_path=path, now=now)

    assert decision["agency_event"] == "all_clear"


# --- FHIR ---

def test_the_fhir_bundle_for_a_gauge_less_reading_invents_no_water_quality_observations():
    reading = _gauge_less("Unsafe")
    reading["evidence"]["decision_basis"] = "rainfall_rule"
    flag = resources.build_flag("f1", "Unsafe", "active", reading["time"], None)

    bundle = resources.build_bundle(reading, flag)

    names = [e["resource"].get("code", {}).get("text", "") for e in bundle["entry"] if e["resource"]["resourceType"] == "Observation"]
    assert not any(n in names for n in resources.PROXY_DISPLAY_NAMES.values())
    risk = next(e["resource"] for e in bundle["entry"] if "method" in e["resource"])
    assert "confidence" not in risk["method"]["text"].lower() or "no" in risk["method"]["text"].lower()
    assert "unavailable" in risk["method"]["text"].lower()


# --- the page ---

def test_the_headers_say_pull_and_the_page_knows_how_to_show_na(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    page = client.get("/").text

    assert "<th>PULL DATE</th>" in page and "<th>PULL TIME</th>" in page
    assert "<th>POLL" not in page
    assert "gauge_available" in page          # the page reads the flag the server sends
    assert "'n/a'" in page                    # and prints n/a rather than 0 or a blank


def test_the_note_under_the_table_says_the_sensors_are_not_reporting_and_when_they_last_did(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    page = client.get("/").text

    assert "are not reporting" in page
    assert "last reported on" in page
    assert "last pulled" in page
