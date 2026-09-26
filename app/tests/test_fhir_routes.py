"""Tests for app/fhir/routes.py - Subscription creation, validated and handshaken.

Per the spec's Review Focus: a rejected Subscription must leave no row behind, a failed
handshake must still create the resource (status: error, not a 500), and re-registering
with the same criteria+endpoint must not create a duplicate.
"""

from __future__ import annotations

import httpx

from app.fhir import routes, store


def _client_that_succeeds() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"received": True})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _client_that_fails() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unreachable")

    return httpx.Client(transport=httpx.MockTransport(handler))


def _valid_body(endpoint: str = "http://rphsa/notifications") -> dict:
    return {
        "resourceType": "Subscription",
        "criteria": "Flag?subject=Location/penns-landing",
        "channel": {"type": "rest-hook", "endpoint": endpoint, "payload": "application/fhir+json"},
    }


def test_valid_subscription_with_successful_handshake_becomes_active(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db(db_path=tmp_path / "test.db")

    content, status_code = routes.handle_create_subscription(
        _valid_body(), client=_client_that_succeeds()
    )

    assert status_code == 201
    assert content["resourceType"] == "Subscription"
    assert content["status"] == "active"

    stored = store.get_active_subscription("Flag?subject=Location/penns-landing", db_path=tmp_path / "test.db")
    assert stored is not None
    assert stored["channel_endpoint"] == "http://rphsa/notifications"


def test_failed_handshake_still_creates_resource_with_error_status(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db(db_path=tmp_path / "test.db")

    content, status_code = routes.handle_create_subscription(
        _valid_body(), client=_client_that_fails()
    )

    assert status_code == 201  # the resource IS created - it just didn't verify
    assert content["status"] == "error"
    assert store.get_active_subscription("Flag?subject=Location/penns-landing", db_path=tmp_path / "test.db") is None


def test_wrong_criteria_is_rejected_and_nothing_is_persisted(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db(db_path=tmp_path / "test.db")

    body = _valid_body()
    body["criteria"] = "Observation?subject=Location/penns-landing"

    content, status_code = routes.handle_create_subscription(body, client=_client_that_succeeds())

    assert status_code == 400
    assert content["resourceType"] == "OperationOutcome"
    with store._connect(tmp_path / "test.db") as conn:
        count = conn.execute("SELECT COUNT(*) FROM fhir_subscriptions").fetchone()[0]
    assert count == 0


def test_wrong_channel_type_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db(db_path=tmp_path / "test.db")

    body = _valid_body()
    body["channel"]["type"] = "websocket"

    content, status_code = routes.handle_create_subscription(body, client=_client_that_succeeds())

    assert status_code == 400
    assert content["resourceType"] == "OperationOutcome"


def test_reregistering_same_criteria_and_endpoint_does_not_duplicate(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db(db_path=tmp_path / "test.db")

    first, _ = routes.handle_create_subscription(_valid_body(), client=_client_that_succeeds())
    second, _ = routes.handle_create_subscription(_valid_body(), client=_client_that_succeeds())

    assert first["id"] == second["id"]

    with store._connect(tmp_path / "test.db") as conn:
        count = conn.execute("SELECT COUNT(*) FROM fhir_subscriptions").fetchone()[0]
    assert count == 1
