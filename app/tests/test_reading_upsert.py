"""One row per gauge reading, not one row per pull.

The TIME column is when the USGS gauge measured (it only updates every ~15 minutes), not when
we asked. Pulling again inside that window used to add an identical-looking row each time -
every dashboard visit would too. Decision (Gouri, 2026-10-02): a repeat pull for the same
gauge time UPDATES the stored row (CSO and rainfall can change faster than the gauge), and
the alert gating + FHIR delivery still run on every pull, so a tier flip inside one gauge
window (e.g. a CSO overflow appearing) is never missed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import db, server
from app.fhir import emit as fhir_emit
from app.fhir import store as fhir_store
from app.tests.test_server import _fake_reading


def _fresh_reading(risk_tier: str = "Safe", gauge_time: datetime | None = None) -> dict:
    reading = _fake_reading(risk_tier)
    gauge_time = gauge_time or datetime.now(timezone.utc) - timedelta(minutes=10)
    reading["time"] = gauge_time.isoformat()
    return reading


def _use_temp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(fhir_store, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    fhir_store.init_db()


# --- db.save_reading ---

def test_saving_the_same_gauge_time_twice_keeps_one_row(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)
    reading = _fresh_reading()

    first_id = db.save_reading(reading)
    second_id = db.save_reading(reading)

    assert first_id == second_id
    assert len(db.get_recent_readings(limit=10)) == 1


def test_a_repeat_pull_updates_the_row_with_the_newer_values(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)
    gauge_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    older = _fresh_reading("Safe", gauge_time)
    older["evidence"]["cso"] = "No overflow"
    newer = _fresh_reading("Unsafe", gauge_time)
    newer["evidence"]["cso"] = "Overflow"
    newer["retrieved_at"] = "2026-10-02T18:00:00+00:00"

    db.save_reading(older)
    db.save_reading(newer)

    rows = db.get_recent_readings(limit=10)
    assert len(rows) == 1
    assert rows[0]["risk_tier"] == "Unsafe"
    assert rows[0]["cso_state"] == "Overflow"
    assert rows[0]["retrieved_at"] == "2026-10-02T18:00:00+00:00"


def test_a_new_gauge_time_adds_a_new_row(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)

    db.save_reading(_fresh_reading(gauge_time=now - timedelta(minutes=30)))
    db.save_reading(_fresh_reading(gauge_time=now - timedelta(minutes=15)))

    assert len(db.get_recent_readings(limit=10)) == 2


def test_the_same_gauge_time_at_another_location_is_a_separate_row(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)
    gauge_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    here = _fresh_reading(gauge_time=gauge_time)
    elsewhere = _fresh_reading(gauge_time=gauge_time)
    elsewhere["location"] = "somewhere_else"

    db.save_reading(here)
    db.save_reading(elsewhere)

    assert len(db.get_recent_readings(limit=10)) == 2


# --- the server's shared pull path ---

def test_two_pulls_in_one_gauge_window_leave_one_row(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)
    reading = _fresh_reading()
    monkeypatch.setattr(server, "pull_reading", lambda: reading)
    monkeypatch.setattr(fhir_emit, "emit_event", lambda reading, decision: None)

    server._pull_and_publish()
    server._pull_and_publish()

    assert len(db.get_recent_readings(limit=10)) == 1


def test_a_tier_flip_inside_one_gauge_window_still_fires_the_agency_event(
    monkeypatch, tmp_path
):
    """Safe, then a CSO overflow appears before the gauge updates: the stored row becomes
    Unsafe AND the agency is told - updating the row must never swallow the event."""
    _use_temp_db(monkeypatch, tmp_path)
    gauge_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    pulls = iter([_fresh_reading("Safe", gauge_time), _fresh_reading("Unsafe", gauge_time)])
    monkeypatch.setattr(server, "pull_reading", lambda: next(pulls))
    events = []
    monkeypatch.setattr(
        fhir_emit, "emit_event", lambda reading, decision: events.append(decision["agency_event"])
    )

    server._pull_and_publish()
    server._pull_and_publish()

    assert events == [None, "unsafe_onset"]
    rows = db.get_recent_readings(limit=10)
    assert len(rows) == 1 and rows[0]["risk_tier"] == "Unsafe"
