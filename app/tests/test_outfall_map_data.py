"""The map shows the sewer outfalls the overflow rule actually used (Gouri, 2026-10-03):
the Delaware-side outfalls within the rule's radius whose CSOcast data is fresh.

Visitors never trigger outside calls, and the map must show what the status was decided from, so
the hourly pull SAVES a snapshot of those outfalls (with coordinates) and a read-only endpoint
serves it - the page never calls CSOcast itself.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import db, server
from app.fhir import store as fhir_store
from app.ingestion.csocast import CsoDataUnavailable, OutfallReading
from app.scoring import pull_reading as pr
from app.tests.test_pull_reading import _fake_proxies, _fake_rainfall, _stub_bundle
from app.tests.test_server import _fake_reading

SNAPSHOT_AT = "2026-10-03T14:00:00+00:00"


def _outfall(name="D_23", status=3, km=4.64, lat=39.9, lon=-75.1) -> dict:
    return {
        "name": name, "status": status, "distance_km": km,
        "last_poll": "2026-10-03T12:00:00+00:00", "latitude": lat, "longitude": lon,
    }


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(fhir_store, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    fhir_store.init_db()
    return TestClient(server.app)


# --- storage ---

def test_a_fresh_database_has_no_snapshot(monkeypatch, tmp_path):
    _client(monkeypatch, tmp_path)

    assert db.get_cso_outfalls() == {"snapshot_at": None, "outfalls": []}


def test_a_saved_snapshot_is_returned_with_its_time(monkeypatch, tmp_path):
    _client(monkeypatch, tmp_path)

    db.save_cso_outfalls([_outfall("D_23"), _outfall("D_41", status=1)], SNAPSHOT_AT)
    result = db.get_cso_outfalls()

    assert result["snapshot_at"] == SNAPSHOT_AT
    assert sorted(o["name"] for o in result["outfalls"]) == ["D_23", "D_41"]


def test_saving_a_new_snapshot_replaces_the_old_one(monkeypatch, tmp_path):
    _client(monkeypatch, tmp_path)
    db.save_cso_outfalls([_outfall("D_23")], SNAPSHOT_AT)

    db.save_cso_outfalls([_outfall("D_99", status=4)], "2026-10-03T15:00:00+00:00")

    assert [o["name"] for o in db.get_cso_outfalls()["outfalls"]] == ["D_99"]


def test_an_outfall_without_coordinates_is_skipped_not_guessed(monkeypatch, tmp_path):
    _client(monkeypatch, tmp_path)

    db.save_cso_outfalls([_outfall("D_ok"), _outfall("D_nowhere", lat=None, lon=None)], SNAPSHOT_AT)

    assert [o["name"] for o in db.get_cso_outfalls()["outfalls"]] == ["D_ok"]


# --- the pull keeps what the map needs ---

def _pull(monkeypatch, cso_fetch):
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall(precip_prev_48h_mm=0.0))
    monkeypatch.setattr(pr, "_load_model", lambda: _stub_bundle(probability_unsafe=0.1))
    monkeypatch.setattr(pr, "fetch_cso_outfalls", cso_fetch)
    return pr.pull_reading()


def test_the_pull_carries_the_outfalls_with_coordinates_in_its_evidence(monkeypatch):
    outfall = OutfallReading(
        name="D_23", status=3, distance_km=4.64,
        last_poll=datetime(2026, 10, 3, 12, tzinfo=timezone.utc), latitude=39.95, longitude=-75.13,
    )

    reading = _pull(monkeypatch, lambda: [outfall])

    saved = reading["evidence"]["cso_outfalls"]
    assert saved == [{
        "name": "D_23", "status": 3, "distance_km": 4.64,
        "last_poll": "2026-10-03T12:00:00+00:00", "latitude": 39.95, "longitude": -75.13,
    }]


def test_when_the_feed_is_down_the_evidence_says_none_not_an_empty_list(monkeypatch):
    def feed_down():
        raise CsoDataUnavailable("CSOcast request failed")

    reading = _pull(monkeypatch, feed_down)

    # None = "we do not know" (keep the last snapshot); [] would wrongly mean "nothing nearby".
    assert reading["evidence"]["cso_outfalls"] is None


def test_an_answering_feed_with_nothing_nearby_gives_an_empty_list(monkeypatch):
    reading = _pull(monkeypatch, lambda: [])

    assert reading["evidence"]["cso_outfalls"] == []


# --- the scheduled pull saves the snapshot ---

def test_the_pull_and_publish_path_saves_the_snapshot(monkeypatch, tmp_path):
    _client(monkeypatch, tmp_path)
    reading = _fake_reading("Safe")
    reading["evidence"]["cso_outfalls"] = [_outfall("D_23")]
    monkeypatch.setattr(server, "pull_reading", lambda: reading)

    server._pull_and_publish()

    assert [o["name"] for o in db.get_cso_outfalls()["outfalls"]] == ["D_23"]


def test_a_feed_outage_keeps_the_previous_snapshot(monkeypatch, tmp_path):
    _client(monkeypatch, tmp_path)
    db.save_cso_outfalls([_outfall("D_23")], SNAPSHOT_AT)
    reading = _fake_reading("Safe")
    reading["evidence"]["cso_outfalls"] = None
    monkeypatch.setattr(server, "pull_reading", lambda: reading)

    server._pull_and_publish()

    assert [o["name"] for o in db.get_cso_outfalls()["outfalls"]] == ["D_23"]


# --- the endpoint ---

def test_the_endpoint_serves_the_snapshot_with_plain_language_status(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    db.save_cso_outfalls(
        [_outfall("D_23", status=3), _outfall("D_41", status=1), _outfall("D_43", status=0), _outfall("D_9", status=4)],
        SNAPSHOT_AT,
    )

    body = client.get("/api/outfalls").json()

    by_name = {o["name"]: o for o in body["outfalls"]}
    assert body["snapshot_at"] == SNAPSHOT_AT
    assert by_name["D_23"]["status_text"] == "Overflow in the past 72 hours"
    assert by_name["D_41"]["status_text"] == "No overflow in the past 72 hours"
    assert by_name["D_43"]["status_text"] == "No data"
    assert by_name["D_9"]["status_text"] == "Overflowing now"


def test_the_endpoint_marks_the_outfall_that_decided_the_newest_reading(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    reading = _fake_reading("Unsafe")
    reading["evidence"]["decision_basis"] = "cso_overflow_rule"
    reading["evidence"]["cso_status"] = {
        "outfall_name": "D_23", "status": 3, "distance_km": 4.64, "last_poll": "2026-10-03T12:00:00+00:00",
    }
    db.save_reading(reading)
    db.save_cso_outfalls([_outfall("D_23"), _outfall("D_41", status=1)], SNAPSHOT_AT)

    body = client.get("/api/outfalls").json()

    flags = {o["name"]: o["triggering"] for o in body["outfalls"]}
    assert flags == {"D_23": True, "D_41": False}


def test_the_endpoint_is_empty_not_an_error_when_there_is_no_snapshot(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.get("/api/outfalls")

    assert response.status_code == 200
    assert response.json() == {"snapshot_at": None, "outfalls": []}


# --- the page ---

def test_the_map_asks_our_endpoint_for_outfalls_and_has_a_legend_for_them(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    page = client.get("/").text

    assert "/api/outfalls" in page          # our own endpoint ...
    assert "services2.arcgis.com" not in page  # ... never CSOcast directly from the visitor's browser
    assert "Overflowing now" in page and "No overflow" in page and "No data" in page  # legend entries
