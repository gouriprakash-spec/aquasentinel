"""Tests for the three-valued CSO field: "Overflow", "No overflow", "Reading unavailable".

The honesty rule (decided with Gouri, 2026-10-02): "No overflow" is only ever claimed when at
least one fresh, nearby outfall actually reports "no overflow in the past 72h" (CSOcast
Status 1). A feed that failed, no fresh nearby outfall, or only "no data" (Status 0)
outfalls all say "Reading unavailable" - never a made-up all-clear.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app import db, status
from app.ingestion.csocast import CsoDataUnavailable, OutfallReading
from app.model.cso_rule import (
    CSO_NO_OVERFLOW,
    CSO_OVERFLOW,
    CSO_UNAVAILABLE,
    classify_cso_state,
)
from app.scoring import pull_reading as pr
from app.tests.test_pull_reading import _fake_proxies, _fake_rainfall, _stub_bundle
from app.tests.test_server import _fake_reading

NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)


def _outfall(status_code: int) -> OutfallReading:
    return OutfallReading(name="D_test", status=status_code, distance_km=1.0, last_poll=NOW)


# --- classify_cso_state: the pure decision ---

def test_the_three_labels_are_exactly_what_was_agreed():
    assert (CSO_OVERFLOW, CSO_NO_OVERFLOW, CSO_UNAVAILABLE) == (
        "Overflow", "No overflow", "Reading unavailable",
    )


@pytest.mark.parametrize("status_code", [3, 4])
def test_an_overflowing_outfall_is_overflow(status_code):
    assert classify_cso_state([_outfall(status_code)]) == "Overflow"


def test_a_fresh_outfall_reporting_no_overflow_is_no_overflow():
    assert classify_cso_state([_outfall(1)]) == "No overflow"


def test_overflow_wins_over_no_overflow_when_outfalls_disagree():
    assert classify_cso_state([_outfall(1), _outfall(4)]) == "Overflow"


def test_no_overflow_when_one_reports_clear_and_another_has_no_data():
    assert classify_cso_state([_outfall(0), _outfall(1)]) == "No overflow"


def test_feed_unreachable_is_reading_unavailable():
    assert classify_cso_state(None) == "Reading unavailable"


def test_no_fresh_nearby_outfall_is_reading_unavailable_not_no_overflow():
    assert classify_cso_state([]) == "Reading unavailable"


def test_only_no_data_outfalls_is_reading_unavailable():
    assert classify_cso_state([_outfall(0), _outfall(0)]) == "Reading unavailable"


# --- pull_reading: feed failure must be distinguishable from "nothing nearby" ---

def _pull_with_cso(monkeypatch, cso_fetch):
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall(precip_prev_48h_mm=0.0))
    monkeypatch.setattr(pr, "_load_model", lambda: _stub_bundle(probability_unsafe=0.1))
    monkeypatch.setattr(pr, "fetch_cso_outfalls", cso_fetch)
    return pr.pull_reading()


def test_pull_reading_reports_overflow_and_still_escalates(monkeypatch):
    reading = _pull_with_cso(monkeypatch, lambda: [_outfall(4)])

    assert reading["evidence"]["cso"] == "Overflow"
    assert reading["risk_tier"] == "Unsafe"


def test_pull_reading_reports_no_overflow(monkeypatch):
    reading = _pull_with_cso(monkeypatch, lambda: [_outfall(1)])

    assert reading["evidence"]["cso"] == "No overflow"


def test_pull_reading_reports_unavailable_when_the_feed_is_down(monkeypatch):
    def feed_down():
        raise CsoDataUnavailable("CSOcast request failed")

    reading = _pull_with_cso(monkeypatch, feed_down)

    assert reading["evidence"]["cso"] == "Reading unavailable"
    # A CSOcast outage still must not fail the whole reading (milestone 6's decision).
    assert reading["risk_tier"] == "Safe"


def test_pull_reading_reports_unavailable_when_no_nearby_outfall_is_fresh(monkeypatch):
    reading = _pull_with_cso(monkeypatch, lambda: [])

    assert reading["evidence"]["cso"] == "Reading unavailable"


# --- storage and the shared status contract ---

def test_cso_state_round_trips_through_the_database(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    reading = _fake_reading("Safe")
    reading["evidence"]["cso"] = "No overflow"

    db.insert_reading(reading)

    assert db.get_recent_readings(limit=1)[0]["cso_state"] == "No overflow"


def test_old_database_without_the_column_is_migrated(monkeypatch, tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE readings DROP COLUMN cso_state")

    db.init_db()  # must add it back, like the Milestone 6 cso_* columns

    reading = _fake_reading("Safe")
    reading["evidence"]["cso"] = "Overflow"
    db.insert_reading(reading)
    assert db.get_recent_readings(limit=1)[0]["cso_state"] == "Overflow"


def test_status_contract_exposes_the_cso_field(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    reading = _fake_reading("Safe")
    reading["evidence"]["cso"] = "No overflow"
    db.insert_reading(reading)

    contract = status.build_status_contract(db.get_recent_readings(limit=1)[0])

    assert contract["cso"] == "No overflow"


def test_a_stored_row_with_no_cso_value_reads_as_unavailable_never_no_overflow(
    monkeypatch, tmp_path
):
    """Rows stored before this field existed (or by a caller that did not set it) have
    NULL. The honest reading of "we never recorded it" is "Reading unavailable"."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    db.insert_reading(_fake_reading("Safe"))  # no evidence["cso"] set

    contract = status.build_status_contract(db.get_recent_readings(limit=1)[0])

    assert contract["cso"] == "Reading unavailable"
