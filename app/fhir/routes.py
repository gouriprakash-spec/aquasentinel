"""FHIR Subscription creation - the R4 rest-hook + handshake pattern.

Per docs/superpowers/specs/2026-09-26-fhir-milestone4-design.md: scoped to our one real
topic (Flag changes for Location/penns-landing) - any other criteria or channel type is
rejected before anything is persisted. The handshake is our own webhook-verification
convention (a plain confirmation POST), not a formally-specified R4 payload - base FHIR R4
does not mandate a handshake structure (see the spec's Honesty notes).

Endpoint allowlist added 2026-09-27 (closes a finding deferred in the Milestone 4 final
review, I3): channel.endpoint must match the one configured RPHSA_BASE_URL. Without this,
any caller that could reach AquaSentinel could register its own endpoint and redirect where
RPHSA's FHIR deliveries go. Deferred at the time because no production deploy existed or was
planned; no longer true once this runs on a real public URL.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.fhir import store

SUPPORTED_CRITERIA = "Flag?subject=Location/penns-landing"
SUPPORTED_CHANNEL_TYPE = "rest-hook"
RPHSA_BASE_URL = os.environ.get("RPHSA_BASE_URL", "http://localhost:8001")

router = APIRouter()


def _operation_outcome(diagnostics: str) -> dict:
    return {
        "resourceType": "OperationOutcome",
        "issue": [{"severity": "error", "code": "invalid", "diagnostics": diagnostics}],
    }


def _attempt_handshake(
    subscription_id: str, channel_endpoint: str, now: str, client: httpx.Client | None = None
) -> str:
    owns_client = client is None
    client = client or httpx.Client(timeout=10.0)
    try:
        response = client.post(
            channel_endpoint,
            json={"aquasentinel_handshake": True, "subscription_id": subscription_id},
        )
        response.raise_for_status()
        status = "active"
    except httpx.HTTPError:
        status = "error"
    finally:
        if owns_client:
            client.close()
    store.update_subscription_status(subscription_id, status, now)
    return status


def handle_create_subscription(body: dict, client: httpx.Client | None = None) -> tuple[dict, int]:
    """The testable business logic behind POST /fhir/Subscription. Returns (content, status_code)."""
    if not isinstance(body, dict):
        return _operation_outcome("Request body must be a JSON object."), 400

    criteria = body.get("criteria")
    # body.get("channel", {}) would NOT fall back to {} when "channel" is present with
    # value None (the default only applies when the key is absent) - so validate the
    # actual value's type instead of trusting .get()'s default.
    channel = body.get("channel")
    channel = channel if isinstance(channel, dict) else {}
    channel_type = channel.get("type")
    channel_endpoint = channel.get("endpoint")

    if criteria != SUPPORTED_CRITERIA:
        return _operation_outcome(
            f"Unsupported criteria. AquaSentinel only supports: {SUPPORTED_CRITERIA}"
        ), 400
    if channel_type != SUPPORTED_CHANNEL_TYPE or not channel_endpoint:
        return _operation_outcome(
            "Unsupported channel. AquaSentinel only supports channel.type="
            f"'{SUPPORTED_CHANNEL_TYPE}' with a channel.endpoint."
        ), 400

    expected_endpoint = f"{RPHSA_BASE_URL}/rphsa/notifications"
    if channel_endpoint != expected_endpoint:
        return _operation_outcome(
            f"Unrecognized channel.endpoint. AquaSentinel only accepts the configured RPHSA "
            f"endpoint: {expected_endpoint}"
        ), 400

    existing = store.get_subscription_by_criteria_and_endpoint(criteria, channel_endpoint)
    subscription_id = existing["id"] if existing else str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    if existing is None:
        store.create_subscription(subscription_id, criteria, channel_endpoint, now)

    status = _attempt_handshake(subscription_id, channel_endpoint, now, client=client)

    return {
        "resourceType": "Subscription",
        "id": subscription_id,
        "status": status,
        "criteria": criteria,
        "channel": {
            "type": channel_type,
            "endpoint": channel_endpoint,
            "payload": "application/fhir+json",
        },
    }, 201


@router.post("/fhir/Subscription")
async def create_subscription(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(
            status_code=400, content=_operation_outcome("Request body is not valid JSON.")
        )
    # Final-review finding: handle_create_subscription makes a blocking httpx call (the
    # handshake, up to a 10s timeout) - running it inline in this async handler would
    # stall AquaSentinel's whole event loop, including the dashboard, for that long.
    # asyncio.to_thread runs it in a worker thread instead; handle_create_subscription
    # itself is unchanged and every existing test still calls it directly, synchronously.
    content, status_code = await asyncio.to_thread(handle_create_subscription, body)
    return JSONResponse(status_code=status_code, content=content)
