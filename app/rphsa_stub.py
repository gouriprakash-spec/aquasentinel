"""RPHSA stub - a standalone FastAPI app simulating the (fictional) Regional Public
Health Surveillance Agency's receiving system. Genuinely separate from AquaSentinel: its
own process, its own port, its own SQLite file - connected only over HTTP, the same way
a real agency integration would work.

Per docs/superpowers/specs/2026-09-26-fhir-milestone4-design.md.

Run: ./venv/bin/uvicorn app.rphsa_stub:app --port 8001 --reload
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Request

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "rphsa_stub.db"
AQUASENTINEL_BASE_URL = os.environ.get("AQUASENTINEL_BASE_URL", "http://localhost:8000")
RPHSA_BASE_URL = os.environ.get("RPHSA_BASE_URL", "http://localhost:8001")
SUPPORTED_CRITERIA = "Flag?subject=Location/penns-landing"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS received_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path if db_path is not None else DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)


def store_notification(kind: str, payload: dict, db_path: Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO received_notifications (received_at, kind, payload) VALUES (?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), kind, json.dumps(payload)),
        )


def get_notifications(db_path: Path | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM received_notifications ORDER BY id DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def register_with_aquasentinel(client: httpx.Client | None = None) -> dict:
    """POST our Subscription to AquaSentinel - the RPHSA side of the handshake pattern
    implemented in app/fhir/routes.py.
    """
    owns_client = client is None
    client = client or httpx.Client(timeout=10.0)
    try:
        response = client.post(
            f"{AQUASENTINEL_BASE_URL}/fhir/Subscription",
            json={
                "resourceType": "Subscription",
                "status": "requested",
                "reason": "RPHSA water-safety monitoring for Penn's Landing",
                "criteria": SUPPORTED_CRITERIA,
                "channel": {
                    "type": "rest-hook",
                    "endpoint": f"{RPHSA_BASE_URL}/rphsa/notifications",
                    "payload": "application/fhir+json",
                },
            },
        )
        return response.json()
    finally:
        if owns_client:
            client.close()


async def _register_with_retry(max_attempts: int = 5, delay_seconds: float = 1.0) -> None:
    """Retries registration with backoff rather than a single attempt.

    Final-review finding (Critical): uvicorn runs the ASGI lifespan's startup before it
    begins accepting connections, so a single registration attempt made from inside
    lifespan can race RPHSA's own readiness to receive the handshake callback - this was
    observed to fail in practice, not just in theory, in every startup order tried
    manually. A bounded retry with backoff is standard practice for this kind of
    startup-ordering problem and doesn't depend on guessing the exact timing.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            result = await asyncio.to_thread(register_with_aquasentinel)
            if result.get("status") == "active":
                logger.info("Registered with AquaSentinel on attempt %d: status=active", attempt)
                return
            logger.warning(
                "AquaSentinel Subscription registration returned status=%s (attempt %d/%d)",
                result.get("status"), attempt, max_attempts,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "Could not reach AquaSentinel to register (attempt %d/%d): %s",
                attempt, max_attempts, exc,
            )
        if attempt < max_attempts:
            await asyncio.sleep(delay_seconds)
    logger.error("Gave up registering with AquaSentinel after %d attempts", max_attempts)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Scheduled, not awaited: RPHSA's own startup must not block on reaching
    # AquaSentinel, and by the time this task actually runs, RPHSA is far more likely to
    # already be accepting connections for the handshake AquaSentinel calls back with.
    asyncio.create_task(_register_with_retry())
    yield


app = FastAPI(title="RPHSA stub (fictional agency receiving system)", lifespan=lifespan)


@app.post("/rphsa/notifications")
async def receive_notification(request: Request) -> dict:
    payload = await request.json()
    kind = "event" if payload.get("resourceType") == "Bundle" else "handshake"
    store_notification(kind, payload)
    return {"received": True}


@app.get("/rphsa/notifications")
def list_notifications() -> list[dict]:
    return get_notifications()
