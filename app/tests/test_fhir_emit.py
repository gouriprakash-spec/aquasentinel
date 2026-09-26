"""Tests for app/fhir/emit.py - delivering FHIR Bundles on a gating agency event.

Per the spec's Review Focus: emit_event must never raise (a malformed reading or an
all_clear-with-no-open-Flag are both internal problems that must be logged, not crash
/api/pull-reading), and a no-op (no active Subscription) must not attempt any network call.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.fhir import emit, store


def _reading(risk_tier: str = "Unsafe", location: str = "penns_landing") -> dict:
    return {
        "location": location,
        "time": "2026-06-01T12:00:00+00:00",
        "risk_tier": risk_tier,
        "evidence": {
            "proxies": {
                "water_temp_c": 21.5, "sp_conductance_uscm": 266.0,
                "dissolved_oxygen_mgl": 6.6, "ph": 7.3, "turbidity_fnu": 6.3,
            }
        },
    }


def _client_recording_posts(sink: list) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        sink.append(json.loads(request.content))
        return httpx.Response(200, json={"received": True})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_no_op_when_agency_event_is_none(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db(db_path=tmp_path / "test.db")

    calls = []
    emit.emit_event(_reading(), {"location": "penns_landing", "agency_event": None},
                     client=_client_recording_posts(calls))

    assert calls == []


def test_no_op_when_no_active_subscription_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db(db_path=tmp_path / "test.db")

    calls = []
    emit.emit_event(_reading(), {"location": "penns_landing", "agency_event": "unsafe_onset"},
                     client=_client_recording_posts(calls))

    assert calls == []  # no Subscription registered yet - real Subscription semantics


def test_unsafe_onset_creates_flag_and_delivers_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db(db_path=tmp_path / "test.db")
    store.create_subscription("sub-1", "Flag?subject=Location/penns-landing",
                               "http://rphsa/notifications", "2026-06-01T00:00:00+00:00")
    store.update_subscription_status("sub-1", "active", "2026-06-01T00:00:00+00:00")

    calls = []
    emit.emit_event(_reading("Unsafe"), {"location": "penns_landing", "agency_event": "unsafe_onset"},
                     client=_client_recording_posts(calls))

    assert len(calls) == 1
    flag_entries = [e for e in calls[0]["entry"] if e["resource"]["resourceType"] == "Flag"]
    assert flag_entries[0]["resource"]["status"] == "active"

    open_flag = store.get_open_flag("penns_landing")
    assert open_flag is not None


def test_all_clear_updates_the_same_flag_not_a_new_one(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db(db_path=tmp_path / "test.db")
    store.create_subscription("sub-1", "Flag?subject=Location/penns-landing",
                               "http://rphsa/notifications", "2026-06-01T00:00:00+00:00")
    store.update_subscription_status("sub-1", "active", "2026-06-01T00:00:00+00:00")

    calls = []
    emit.emit_event(_reading("Unsafe"), {"location": "penns_landing", "agency_event": "unsafe_onset"},
                     client=_client_recording_posts(calls))
    onset_flag_id = store.get_open_flag("penns_landing")["id"]

    emit.emit_event(_reading("Safe"), {"location": "penns_landing", "agency_event": "all_clear"},
                     client=_client_recording_posts(calls))

    assert store.get_open_flag("penns_landing") is None  # closed, not left open
    flag_entries = [e for e in calls[1]["entry"] if e["resource"]["resourceType"] == "Flag"]
    assert flag_entries[0]["resource"]["id"] == onset_flag_id  # same id, not a new Flag
    assert flag_entries[0]["resource"]["status"] == "inactive"


def test_all_clear_with_no_open_flag_is_logged_not_raised(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db(db_path=tmp_path / "test.db")

    # No unsafe_onset ever happened, so there is no open Flag - this is an internal
    # inconsistency (gating and FHIR state disagree), not a normal path.
    emit.emit_event(_reading("Safe"), {"location": "penns_landing", "agency_event": "all_clear"})

    assert "no open Flag exists" in caplog.text


def test_malformed_reading_does_not_raise(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db(db_path=tmp_path / "test.db")
    store.create_subscription("sub-1", "Flag?subject=Location/penns-landing",
                               "http://rphsa/notifications", "2026-06-01T00:00:00+00:00")
    store.update_subscription_status("sub-1", "active", "2026-06-01T00:00:00+00:00")

    broken_reading = {"location": "penns_landing", "time": "2026-06-01T12:00:00+00:00",
                       "risk_tier": "Unsafe"}  # missing "evidence" entirely

    emit.emit_event(broken_reading, {"location": "penns_landing", "agency_event": "unsafe_onset"})

    assert "FHIR delivery failed" in caplog.text
