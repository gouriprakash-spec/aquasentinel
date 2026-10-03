"""Stored overflow readings from before 2026-10-03 carry the old fixed placeholder confidence
(0.3) instead of the real rule/model agreement. Left alone, the dashboard would keep presenting
that 0.3 under RULE/MODEL AGREEMENT. The real figure is exactly recomputable from stored fields
(the model's probability of Unsafe, and whether the rainfall rule said Unsafe), so init_db()
corrects only those legacy rows - once, idempotently.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app import db
from app.tests.test_server import _fake_reading


def _insert(path: Path, *, basis: str, confidence: float, p_unsafe, rain48: float, time: str) -> int:
    reading = _fake_reading("Unsafe")
    reading["time"] = time
    reading["confidence"] = confidence
    reading["evidence"]["decision_basis"] = basis
    reading["evidence"]["model_probability_unsafe"] = p_unsafe
    reading["evidence"]["rule_threshold_mm"] = 2.5
    reading["evidence"]["rainfall_mm"] = {
        "precip_mm": 0.0, "precip_prev_24h_mm": 0.0, "precip_prev_48h_mm": rain48,
    }
    return db.insert_reading(reading, db_path=path)


def _confidence(path: Path, row_id: int) -> float:
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT confidence FROM readings WHERE id = ?", (row_id,)).fetchone()[0]


def _fresh_db(tmp_path) -> Path:
    path = tmp_path / "legacy.db"
    db.init_db(path)
    return path


def test_a_legacy_overflow_row_where_the_rain_rule_said_safe_gets_its_real_agreement(tmp_path):
    path = _fresh_db(tmp_path)
    row = _insert(path, basis="cso_overflow_rule", confidence=0.3, p_unsafe=0.14, rain48=0.0,
                  time="2026-10-02T23:40:00-04:00")

    db.init_db(path)

    assert _confidence(path, row) == 0.86  # rule Safe: agreement = 1 - P(unsafe)


def test_a_legacy_overflow_row_where_the_rain_rule_said_unsafe_gets_its_real_agreement(tmp_path):
    path = _fresh_db(tmp_path)
    row = _insert(path, basis="cso_overflow_rule", confidence=0.3, p_unsafe=0.9, rain48=5.0,
                  time="2026-10-02T22:40:00-04:00")

    db.init_db(path)

    assert _confidence(path, row) == 0.9  # rule Unsafe: agreement = P(unsafe)


def test_an_overflow_row_that_already_has_a_real_confidence_is_left_alone(tmp_path):
    path = _fresh_db(tmp_path)
    row = _insert(path, basis="cso_overflow_rule", confidence=0.77, p_unsafe=0.14, rain48=0.0,
                  time="2026-10-02T21:40:00-04:00")

    db.init_db(path)

    assert _confidence(path, row) == 0.77


def test_a_rainfall_rule_row_is_never_touched_even_if_its_confidence_is_exactly_0_3(tmp_path):
    path = _fresh_db(tmp_path)
    row = _insert(path, basis="rainfall_rule", confidence=0.3, p_unsafe=0.14, rain48=0.0,
                  time="2026-10-02T20:40:00-04:00")

    db.init_db(path)

    assert _confidence(path, row) == 0.3


def test_a_row_without_a_model_probability_is_left_alone_rather_than_guessed(tmp_path):
    path = _fresh_db(tmp_path)
    row = _insert(path, basis="cso_overflow_rule", confidence=0.3, p_unsafe=None, rain48=0.0,
                  time="2026-10-02T19:40:00-04:00")

    db.init_db(path)

    assert _confidence(path, row) == 0.3


def test_the_correction_is_idempotent(tmp_path):
    path = _fresh_db(tmp_path)
    row = _insert(path, basis="cso_overflow_rule", confidence=0.3, p_unsafe=0.14, rain48=0.0,
                  time="2026-10-02T18:40:00-04:00")

    db.init_db(path)
    first = _confidence(path, row)
    db.init_db(path)

    assert first == _confidence(path, row) == 0.86
