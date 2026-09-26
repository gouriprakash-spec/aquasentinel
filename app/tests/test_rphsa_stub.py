"""Tests for app/rphsa_stub.py - the fictional agency's receiving system.

Genuinely separate from AquaSentinel: its own SQLite file, its own FastAPI app. These
tests exercise the storage/classification logic directly, without needing AquaSentinel
running (that combination is covered by app/tests/test_fhir_end_to_end.py in Task 7).
"""

from __future__ import annotations

import httpx

from app import rphsa_stub


def test_store_and_list_notifications_newest_first(tmp_path):
    db_path = tmp_path / "rphsa.db"
    rphsa_stub.init_db(db_path=db_path)

    rphsa_stub.store_notification("handshake", {"aquasentinel_handshake": True}, db_path=db_path)
    rphsa_stub.store_notification("event", {"resourceType": "Bundle", "entry": []}, db_path=db_path)

    notifications = rphsa_stub.get_notifications(db_path=db_path)
    assert len(notifications) == 2
    assert notifications[0]["kind"] == "event"  # newest first
    assert notifications[1]["kind"] == "handshake"


def test_register_with_aquasentinel_posts_expected_subscription_shape(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"resourceType": "Subscription", "id": "sub-1", "status": "active"})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = rphsa_stub.register_with_aquasentinel(client=client)

    assert result["status"] == "active"
    assert captured["body"]["criteria"] == "Flag?subject=Location/penns-landing"
    assert captured["body"]["channel"]["type"] == "rest-hook"
    assert captured["body"]["channel"]["endpoint"].endswith("/rphsa/notifications")


def test_receive_notification_endpoint_classifies_bundle_as_event(tmp_path, monkeypatch):
    monkeypatch.setattr(rphsa_stub, "DB_PATH", tmp_path / "rphsa.db")
    monkeypatch.setattr(rphsa_stub, "register_with_aquasentinel", lambda client=None: {"status": "skipped"})
    rphsa_stub.init_db()

    from fastapi.testclient import TestClient
    with TestClient(rphsa_stub.app) as client:
        response = client.post("/rphsa/notifications", json={"resourceType": "Bundle", "entry": []})
        assert response.status_code == 200

        listed = client.get("/rphsa/notifications")
        assert listed.json()[0]["kind"] == "event"


def test_receive_notification_endpoint_classifies_ping_as_handshake(tmp_path, monkeypatch):
    monkeypatch.setattr(rphsa_stub, "DB_PATH", tmp_path / "rphsa.db")
    monkeypatch.setattr(rphsa_stub, "register_with_aquasentinel", lambda client=None: {"status": "skipped"})
    rphsa_stub.init_db()

    from fastapi.testclient import TestClient
    with TestClient(rphsa_stub.app) as client:
        response = client.post("/rphsa/notifications", json={"aquasentinel_handshake": True})
        assert response.status_code == 200

        listed = client.get("/rphsa/notifications")
        assert listed.json()[0]["kind"] == "handshake"
