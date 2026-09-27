"""Tests for app/server.py - the FastAPI wiring around pull_reading() and the DB.

Uses FastAPI's TestClient (no real network, no real HTTP server process) and monkeypatches
pull_reading() so these tests don't depend on live USGS/NWS - that's covered by
test_pull_reading.py and the manual live check already run for milestone 2.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import db, server
from app.fhir import store as fhir_store
from app.ingestion.usgs import UsgsDataUnavailable


def _fake_reading(risk_tier: str = "Safe") -> dict:
    return {
        "location": "penns_landing",
        "location_name": "Penn's Landing, Center City tidal Delaware",
        "time": "2026-09-25T17:40:00-04:00",
        "risk_tier": risk_tier,
        "confidence": 0.974,
        "source": "aquasentinel",
        "source_url": "https://waterservices.usgs.gov/nwis/iv/?sites=01467200",
        "retrieved_at": "2026-09-25T22:09:47+00:00",
        "evidence": {
            "proxies": {
                "water_temp_c": 21.5, "sp_conductance_uscm": 266.0,
                "dissolved_oxygen_mgl": 6.6, "ph": 7.3, "turbidity_fnu": 6.3,
            },
            "proxy_timestamps": {},
            "rainfall_mm": {"precip_mm": 0.0, "precip_prev_24h_mm": 0.0},
        },
        "threshold_cfu_100ml": 235,
        "model_version": "rf_B_post2021",
        "regime": "B_post2021",
        "kind": "model_estimate",
    }


def _client(monkeypatch, tmp_path):
    # TestClient only runs the app's lifespan (which calls db.init_db()) when used as a
    # context manager - call init_db() directly so a plain TestClient(...) still works.
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(fhir_store, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    fhir_store.init_db()
    return TestClient(server.app)


def test_pull_reading_endpoint_scores_persists_and_returns(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "pull_reading", lambda: _fake_reading("Unsafe"))
    client = _client(monkeypatch, tmp_path)

    response = client.post("/api/pull-reading")

    assert response.status_code == 200
    body = response.json()
    assert body["risk_tier"] == "Unsafe"
    assert "estimate_cfu_100ml" not in body

    stored = db.get_recent_readings(db_path=tmp_path / "test.db")
    assert len(stored) == 1
    assert stored[0]["risk_tier"] == "Unsafe"


def test_pull_reading_endpoint_fails_closed_on_usgs_error(monkeypatch, tmp_path):
    def raise_unavailable():
        raise UsgsDataUnavailable("USGS is down")

    monkeypatch.setattr(server, "pull_reading", raise_unavailable)
    client = _client(monkeypatch, tmp_path)

    response = client.post("/api/pull-reading")

    assert response.status_code == 503
    # Nothing should have been persisted from a failed pull.
    assert db.get_recent_readings(db_path=tmp_path / "test.db") == []


def test_readings_endpoint_returns_newest_first(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(fhir_store, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    fhir_store.init_db()
    db.insert_reading(_fake_reading("Safe"))
    db.insert_reading(_fake_reading("Unsafe"))

    client = TestClient(server.app)
    response = client.get("/api/readings")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["risk_tier"] == "Unsafe"
    assert body[1]["risk_tier"] == "Safe"


def test_readings_endpoint_respects_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(fhir_store, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    fhir_store.init_db()
    for _ in range(5):
        db.insert_reading(_fake_reading())

    client = TestClient(server.app)
    response = client.get("/api/readings", params={"limit": 2})

    assert len(response.json()) == 2


def test_index_serves_the_dashboard_html(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    client = TestClient(server.app)
    response = client.get("/")

    assert response.status_code == 200
    assert "AquaSentinel" in response.text


def test_pull_reading_endpoint_succeeds_even_if_fhir_delivery_fails(monkeypatch, tmp_path):
    # A fresh reading time, not the shared fixture's fixed past timestamp - gating's
    # freshness check would otherwise make this reading "unavailable" (agency_event=None),
    # so emit_event would return before ever attempting delivery, proving nothing.
    fresh_reading = _fake_reading("Unsafe")
    fresh_reading["time"] = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(server, "pull_reading", lambda: fresh_reading)
    client = _client(monkeypatch, tmp_path)

    # A real active Subscription pointing at an address that will genuinely fail to
    # resolve - this is a full end-to-end resilience check, not a mocked one.
    fhir_store.create_subscription(
        "sub-1", "Flag?subject=Location/penns-landing", "http://unreachable-host.invalid/notifications",
        "2026-06-01T00:00:00+00:00", db_path=tmp_path / "test.db",
    )
    fhir_store.update_subscription_status(
        "sub-1", "active", "2026-06-01T00:00:00+00:00", db_path=tmp_path / "test.db"
    )

    response = client.post("/api/pull-reading")

    assert response.status_code == 200
    assert response.json()["risk_tier"] == "Unsafe"
    # Final-review finding (Important): confirms emit_event actually reached the delivery
    # attempt (not a no-op from a stale/no-event gating decision) - the Flag was recorded
    # locally even though delivery to the unreachable endpoint failed.
    assert fhir_store.get_open_flag("penns_landing", db_path=tmp_path / "test.db") is not None


def test_api_status_returns_latest_reading_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "pull_reading", lambda: _fake_reading("Safe"))
    client = _client(monkeypatch, tmp_path)
    client.post("/api/pull-reading")

    response = client.get("/api/status")

    assert response.status_code == 200
    body = response.json()
    assert body["risk_tier"] == "Safe"
    assert body["location"] == "penns_landing"
    assert "estimate_cfu_100ml" not in body
    assert "location_name" not in body


def test_api_status_returns_unavailable_when_no_readings_exist(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == {"status": "unavailable", "reason": "no readings yet"}


def test_api_status_rejects_unknown_location(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.get("/api/status", params={"location": "somewhere_else"})

    assert response.status_code == 404


def test_llms_txt_is_served_with_honesty_language():
    client = TestClient(server.app)

    response = client.get("/llms.txt")

    assert response.status_code == 200
    assert "AquaSentinel" in response.text
    assert "estimate" in response.text
    assert "predict illness" not in response.text.lower()


def test_index_page_contains_substituted_json_ld(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "pull_reading", lambda: _fake_reading("Safe"))
    client = _client(monkeypatch, tmp_path)
    client.post("/api/pull-reading")

    response = client.get("/")

    assert response.status_code == 200
    assert '"@type": "Dataset"' in response.text
    assert "__AQUASENTINEL_DATASET_DATE_MODIFIED__" not in response.text
    assert "2026-09-25T22:09:47+00:00" in response.text  # _fake_reading's retrieved_at


def test_index_page_json_ld_falls_back_to_server_time_when_no_readings(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    client = TestClient(server.app)

    response = client.get("/")

    assert response.status_code == 200
    assert "__AQUASENTINEL_DATASET_DATE_MODIFIED__" not in response.text
