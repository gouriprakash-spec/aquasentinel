"""Tests for app/db.py's SQLite persistence."""

from __future__ import annotations

import sqlite3

from app import db


def _fake_reading(risk_tier: str = "Safe") -> dict:
    return {
        "location": "penns_landing",
        "time": "2026-09-25T17:40:00-04:00",
        "risk_tier": risk_tier,
        "confidence": 0.974,
        "retrieved_at": "2026-09-25T22:09:47+00:00",
        "threshold_cfu_100ml": 235,
        "model_version": "rf_B_post2021",
        "regime": "B_post2021",
        "evidence": {
            "proxies": {
                "water_temp_c": 21.5,
                "sp_conductance_uscm": 266.0,
                "dissolved_oxygen_mgl": 6.6,
                "ph": 7.3,
                "turbidity_fnu": 6.3,
            },
            "rainfall_mm": {
                "precip_mm": 0.0, "precip_prev_24h_mm": 0.0, "precip_prev_48h_mm": 3.1,
            },
            "rainfall_source": "open-meteo",
            "decision_basis": "rainfall_rule",
            "rule_threshold_mm": 2.5,
            "model_probability_unsafe": 0.42,
        },
    }


def test_persists_what_decided_the_tier(tmp_path):
    """Milestone 1b final-review finding: pull_reading()'s evidence carries the 48h rain
    value the rule decided on, the threshold, the rain source, and the model's
    confidence-only probability - all five used to be silently dropped on insert."""
    db_path = tmp_path / "test.db"
    db.init_db(db_path=db_path)

    db.insert_reading(_fake_reading(), db_path=db_path)

    row = db.get_recent_readings(db_path=db_path)[0]
    assert row["precip_prev_48h_mm"] == 3.1
    assert row["rainfall_source"] == "open-meteo"
    assert row["decision_basis"] == "rainfall_rule"
    assert row["rule_threshold_mm"] == 2.5
    assert row["model_probability_unsafe"] == 0.42


def test_insert_and_read_back(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path=db_path)

    row_id = db.insert_reading(_fake_reading(), db_path=db_path)
    assert row_id == 1

    rows = db.get_recent_readings(db_path=db_path)
    assert len(rows) == 1
    assert rows[0]["risk_tier"] == "Safe"
    assert rows[0]["turbidity_fnu"] == 6.3
    assert rows[0]["precip_prev_24h_mm"] == 0.0


def test_get_recent_readings_orders_newest_first(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path=db_path)

    db.insert_reading(_fake_reading("Safe"), db_path=db_path)
    db.insert_reading(_fake_reading("Unsafe"), db_path=db_path)

    rows = db.get_recent_readings(db_path=db_path)
    assert rows[0]["risk_tier"] == "Unsafe"
    assert rows[1]["risk_tier"] == "Safe"


def test_get_recent_readings_respects_limit(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path=db_path)

    for _ in range(5):
        db.insert_reading(_fake_reading(), db_path=db_path)

    rows = db.get_recent_readings(limit=2, db_path=db_path)
    assert len(rows) == 2


def test_monkeypatched_db_path_is_honored(monkeypatch, tmp_path):
    """Guards against the default-argument footgun: db_path must be read fresh at call
    time (module global), not frozen into a default parameter at import time.
    """
    db_path = tmp_path / "patched.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)

    db.init_db()
    db.insert_reading(_fake_reading())

    assert db_path.exists()
    assert len(db.get_recent_readings()) == 1


def test_persists_cso_trigger_info_when_present(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path=db_path)
    reading = _fake_reading()
    reading["evidence"]["cso_status"] = {
        "outfall_name": "D_25", "status": 3, "distance_km": 4.33,
        "last_poll": "2026-10-01T10:00:00+00:00",
    }

    db.insert_reading(reading, db_path=db_path)

    row = db.get_recent_readings(db_path=db_path)[0]
    assert row["cso_outfall_name"] == "D_25"
    assert row["cso_outfall_status"] == 3
    assert row["cso_distance_km"] == 4.33
    assert row["cso_last_poll"] == "2026-10-01T10:00:00+00:00"


def test_init_db_migrates_an_existing_pre_milestone_6_database(tmp_path):
    """Final-review finding (Important #3): CREATE TABLE IF NOT EXISTS is a no-op against a
    table that already exists with the OLD schema (no cso_* columns) - reproduced directly
    against a copy of this worktree's own aquasentinel.db, insert_reading() raised
    sqlite3.OperationalError: table readings has no column named cso_outfall_name.
    init_db() must migrate an existing old-schema file in place, not just create-if-missing.
    """
    db_path = tmp_path / "old_schema.db"
    # Build the pre-Milestone-6 schema by hand - the full column list above (db.py's own
    # _SCHEMA constant) minus the 4 cso_* columns added in Milestone 6.
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location TEXT NOT NULL,
            reading_time TEXT NOT NULL,
            risk_tier TEXT NOT NULL,
            confidence REAL NOT NULL,
            water_temp_c REAL,
            sp_conductance_uscm REAL,
            dissolved_oxygen_mgl REAL,
            ph REAL,
            turbidity_fnu REAL,
            precip_mm REAL,
            precip_prev_24h_mm REAL,
            precip_prev_48h_mm REAL,
            rainfall_source TEXT,
            decision_basis TEXT,
            rule_threshold_mm REAL,
            model_probability_unsafe REAL,
            threshold_cfu_100ml INTEGER NOT NULL,
            model_version TEXT NOT NULL,
            regime TEXT NOT NULL,
            retrieved_at TEXT NOT NULL
        );
        """
    )
    conn.close()

    db.init_db(db_path=db_path)

    reading = _fake_reading()
    reading["evidence"]["cso_status"] = {
        "outfall_name": "D_25", "status": 3, "distance_km": 4.33,
        "last_poll": "2026-10-01T10:00:00+00:00",
    }
    db.insert_reading(reading, db_path=db_path)  # must not raise OperationalError

    row = db.get_recent_readings(db_path=db_path)[0]
    assert row["cso_outfall_name"] == "D_25"
    assert row["cso_outfall_status"] == 3
    assert row["cso_distance_km"] == 4.33
    assert row["cso_last_poll"] == "2026-10-01T10:00:00+00:00"


def test_persists_null_cso_fields_when_not_triggered(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path=db_path)
    reading = _fake_reading()
    reading["evidence"]["cso_status"] = None

    db.insert_reading(reading, db_path=db_path)

    row = db.get_recent_readings(db_path=db_path)[0]
    assert row["cso_outfall_name"] is None
    assert row["cso_outfall_status"] is None
    assert row["cso_distance_km"] is None
    assert row["cso_last_poll"] is None
