"""Tests for app/fhir/store.py - SQLite persistence for FHIR Subscriptions and Flags."""

from __future__ import annotations

from app.fhir import store


def test_create_and_fetch_subscription_by_criteria_and_endpoint(tmp_path):
    db_path = tmp_path / "test.db"
    store.init_db(db_path=db_path)

    store.create_subscription("sub-1", "Flag?subject=Location/penns-landing",
                               "http://rphsa/notifications", "2026-06-01T00:00:00+00:00",
                               db_path=db_path)

    found = store.get_subscription_by_criteria_and_endpoint(
        "Flag?subject=Location/penns-landing", "http://rphsa/notifications", db_path=db_path
    )
    assert found["id"] == "sub-1"
    assert found["status"] == "requested"


def test_get_active_subscription_ignores_non_active_ones(tmp_path):
    db_path = tmp_path / "test.db"
    store.init_db(db_path=db_path)
    store.create_subscription("sub-1", "Flag?subject=Location/penns-landing",
                               "http://rphsa/notifications", "2026-06-01T00:00:00+00:00",
                               db_path=db_path)

    assert store.get_active_subscription("Flag?subject=Location/penns-landing", db_path=db_path) is None

    store.update_subscription_status("sub-1", "active", "2026-06-01T00:01:00+00:00", db_path=db_path)

    active = store.get_active_subscription("Flag?subject=Location/penns-landing", db_path=db_path)
    assert active["id"] == "sub-1"
    assert active["channel_endpoint"] == "http://rphsa/notifications"


def test_flag_lifecycle_create_then_update_same_id(tmp_path):
    db_path = tmp_path / "test.db"
    store.init_db(db_path=db_path)

    store.create_flag("flag-1", "penns_landing", "active", "2026-06-01T00:00:00+00:00", db_path=db_path)

    open_flag = store.get_open_flag("penns_landing", db_path=db_path)
    assert open_flag["id"] == "flag-1"
    assert open_flag["status"] == "active"
    assert open_flag["period_end"] is None

    store.update_flag("flag-1", "inactive", "2026-06-03T00:00:00+00:00", db_path=db_path)

    assert store.get_open_flag("penns_landing", db_path=db_path) is None  # no longer open


def test_get_open_flag_returns_none_when_nothing_is_open(tmp_path):
    db_path = tmp_path / "test.db"
    store.init_db(db_path=db_path)

    assert store.get_open_flag("penns_landing", db_path=db_path) is None
