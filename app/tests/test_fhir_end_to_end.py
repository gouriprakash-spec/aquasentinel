"""End-to-end test: a real gating decision results in a Bundle actually landing in the
RPHSA stub's stored notifications - the two apps talking over HTTP via in-process ASGI
transports (no real sockets/ports needed), exactly as they would for real.

The Subscription-creation HTTP path itself (criteria/channel validation, the handshake)
is already covered by app/tests/test_fhir_routes.py - this test sets up an already-active
Subscription directly and focuses on what the spec calls "end-to-end wiring": a gating
decision -> a Bundle -> delivered -> received.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app import rphsa_stub
from app.fhir import emit as fhir_emit
from app.fhir import store as fhir_store


def _reading(risk_tier: str, time: str = "2026-06-01T12:00:00+00:00") -> dict:
    return {
        "location": "penns_landing",
        "time": time,
        "risk_tier": risk_tier,
        "evidence": {
            "proxies": {
                "water_temp_c": 21.5, "sp_conductance_uscm": 266.0,
                "dissolved_oxygen_mgl": 6.6, "ph": 7.3, "turbidity_fnu": 6.3,
            },
            "rainfall_mm": {"precip_mm": 0.0, "precip_prev_24h_mm": 1.0, "precip_prev_48h_mm": 4.2},
            "rainfall_source": "nws",
            "rule_threshold_mm": 2.5,
        },
    }


def test_unsafe_onset_bundle_is_received_and_stored_by_rphsa_stub(monkeypatch, tmp_path):
    monkeypatch.setattr(fhir_store, "DB_PATH", tmp_path / "aquasentinel.db")
    monkeypatch.setattr(rphsa_stub, "DB_PATH", tmp_path / "rphsa.db")
    fhir_store.init_db()
    rphsa_stub.init_db()

    fhir_store.create_subscription(
        "sub-1", "Flag?subject=Location/penns-landing", "http://rphsa/rphsa/notifications",
        "2026-06-01T00:00:00+00:00",
    )
    fhir_store.update_subscription_status("sub-1", "active", "2026-06-01T00:00:00+00:00")

    rphsa_client = TestClient(rphsa_stub.app, base_url="http://rphsa")

    decision = {"location": "penns_landing", "agency_event": "unsafe_onset"}
    fhir_emit.emit_event(_reading("Unsafe"), decision, client=rphsa_client)

    notifications = rphsa_stub.get_notifications(db_path=tmp_path / "rphsa.db")
    events = [n for n in notifications if n["kind"] == "event"]
    assert len(events) == 1

    bundle = json.loads(events[0]["payload"])
    assert bundle["resourceType"] == "Bundle"
    flag_entries = [e for e in bundle["entry"] if e["resource"]["resourceType"] == "Flag"]
    assert flag_entries[0]["resource"]["status"] == "active"

    # Milestone 1b final-review finding: what RPHSA receives must let it audit the tier -
    # the 48h rain value the rule decided on arrives in the Bundle and is referenced by the
    # risk Observation's derivedFrom.
    by_url = {e["fullUrl"]: e["resource"] for e in bundle["entry"]}
    risk = next(r for r in by_url.values() if r["resourceType"] == "Observation" and "method" in r)
    derived = [by_url[d["reference"]] for d in risk["derivedFrom"]]
    rainfall = [r for r in derived if r["code"]["text"].startswith("Precipitation")]
    assert len(rainfall) == 1
    assert rainfall[0]["valueQuantity"] == {"value": 4.2, "unit": "mm"}


def test_all_clear_after_onset_is_received_as_inactive_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(fhir_store, "DB_PATH", tmp_path / "aquasentinel.db")
    monkeypatch.setattr(rphsa_stub, "DB_PATH", tmp_path / "rphsa.db")
    fhir_store.init_db()
    rphsa_stub.init_db()

    fhir_store.create_subscription(
        "sub-1", "Flag?subject=Location/penns-landing", "http://rphsa/rphsa/notifications",
        "2026-06-01T00:00:00+00:00",
    )
    fhir_store.update_subscription_status("sub-1", "active", "2026-06-01T00:00:00+00:00")

    rphsa_client = TestClient(rphsa_stub.app, base_url="http://rphsa")

    fhir_emit.emit_event(
        _reading("Unsafe", "2026-06-01T12:00:00+00:00"),
        {"location": "penns_landing", "agency_event": "unsafe_onset"},
        client=rphsa_client,
    )
    fhir_emit.emit_event(
        _reading("Safe", "2026-06-03T12:00:00+00:00"),
        {"location": "penns_landing", "agency_event": "all_clear"},
        client=rphsa_client,
    )

    notifications = rphsa_stub.get_notifications(db_path=tmp_path / "rphsa.db")
    events = [json.loads(n["payload"]) for n in notifications if n["kind"] == "event"]
    assert len(events) == 2

    onset_flag = next(e for e in events[1]["entry"] if e["resource"]["resourceType"] == "Flag")
    all_clear_flag = next(e for e in events[0]["entry"] if e["resource"]["resourceType"] == "Flag")
    assert onset_flag["resource"]["id"] == all_clear_flag["resource"]["id"]  # same Flag resource
    assert all_clear_flag["resource"]["status"] == "inactive"


def test_rphsa_unreachable_does_not_raise_and_stores_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(fhir_store, "DB_PATH", tmp_path / "aquasentinel.db")
    fhir_store.init_db()

    fhir_store.create_subscription(
        "sub-1", "Flag?subject=Location/penns-landing", "http://unreachable-host.invalid/notifications",
        "2026-06-01T00:00:00+00:00",
    )
    fhir_store.update_subscription_status("sub-1", "active", "2026-06-01T00:00:00+00:00")

    # No client override - a real httpx.Client will genuinely fail to resolve this host.
    # This must not raise.
    fhir_emit.emit_event(
        _reading("Unsafe"), {"location": "penns_landing", "agency_event": "unsafe_onset"}
    )

    # The Flag was still recorded locally even though delivery failed - only the network
    # call failed, not the local bookkeeping that happens before it.
    assert fhir_store.get_open_flag("penns_landing") is not None
