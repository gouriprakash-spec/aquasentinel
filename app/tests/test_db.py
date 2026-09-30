"""Tests for app/db.py's SQLite persistence."""

from __future__ import annotations

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
