"""Deliver FHIR Observation + Flag resources to RPHSA's Subscription when a gating
decision reports an agency event. Per the spec's Endpoints section (step 5): this never
raises - a failed delivery, an unreachable Subscription, or even a malformed reading is
logged, not surfaced, so it can never break /api/pull-reading's response to the dashboard.
Logging loudly (not silently swallowing) is how this stays honest about failures while
still protecting the caller - see the plan's Review Focus for why both properties matter
at once.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import httpx

from app.fhir import resources, store

logger = logging.getLogger(__name__)

SUPPORTED_CRITERIA = "Flag?subject=Location/penns-landing"


def emit_event(reading: dict, decision: dict, client: httpx.Client | None = None) -> None:
    if decision.get("agency_event") is None:
        return
    try:
        _emit_event(reading, decision, client=client)
    except Exception:
        logger.exception("FHIR delivery failed for %s event", decision.get("agency_event"))


def _emit_event(reading: dict, decision: dict, client: httpx.Client | None = None) -> None:
    location = decision["location"]
    now = datetime.now(timezone.utc).isoformat()

    if decision["agency_event"] == "unsafe_onset":
        # Reuse an already-open Flag if one exists rather than creating a second one -
        # closes a race where two near-simultaneous pulls (FastAPI's threadpool, no
        # transaction around gating's read-then-write) could both compute unsafe_onset
        # for the same location.
        existing_flag = store.get_open_flag(location)
        if existing_flag is not None:
            flag_id = existing_flag["id"]
            period_start = existing_flag["period_start"]
        else:
            flag_id = str(uuid.uuid4())
            period_start = reading["time"]
            store.create_flag(flag_id, location, "active", period_start)
        flag = resources.build_flag(flag_id, reading["risk_tier"], "active", period_start, None)
    else:  # "all_clear"
        open_flag = store.get_open_flag(location)
        if open_flag is None:
            raise RuntimeError(
                f"all_clear fired for {location} but no open Flag exists - gating and "
                "FHIR state have gone out of sync."
            )
        store.update_flag(open_flag["id"], "inactive", reading["time"])
        flag = resources.build_flag(
            open_flag["id"], reading["risk_tier"], "inactive", open_flag["period_start"], reading["time"]
        )

    bundle = resources.build_bundle(reading, flag)

    subscription = store.get_active_subscription(SUPPORTED_CRITERIA)
    if subscription is None:
        return  # nobody has subscribed yet - real Subscription semantics, not an error

    owns_client = client is None
    client = client or httpx.Client(timeout=10.0)
    try:
        response = client.post(subscription["channel_endpoint"], json=bundle)
        response.raise_for_status()
    finally:
        if owns_client:
            client.close()
