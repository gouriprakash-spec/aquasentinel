"""SQLite persistence for FHIR Subscriptions and Flags - Milestone 4.

Lives in the same aquasentinel.db as app/db.py's readings/alert_state tables (per
docs/superpowers/specs/2026-09-26-fhir-milestone4-design.md's Storage section), but keeps
its own schema and query functions in this module rather than growing app/db.py, so FHIR
concerns stay in the app/fhir/ package.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from app import config

# Same file as app/db.py's readings table, so it reads the same setting.
DB_PATH = config.resolve_db_path(os.environ.get(config.DB_PATH_ENV_VAR))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fhir_subscriptions (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    criteria TEXT NOT NULL,
    channel_endpoint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fhir_flags (
    id TEXT PRIMARY KEY,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT
);
"""


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path if db_path is not None else DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | None = None) -> None:
    Path(db_path if db_path is not None else DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)


def create_subscription(
    id: str, criteria: str, channel_endpoint: str, now: str, db_path: Path | None = None
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO fhir_subscriptions
               (id, status, criteria, channel_endpoint, created_at, updated_at)
               VALUES (?, 'requested', ?, ?, ?, ?)""",
            (id, criteria, channel_endpoint, now, now),
        )


def update_subscription_status(id: str, status: str, now: str, db_path: Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE fhir_subscriptions SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, id),
        )


def get_subscription_by_criteria_and_endpoint(
    criteria: str, channel_endpoint: str, db_path: Path | None = None
) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM fhir_subscriptions WHERE criteria = ? AND channel_endpoint = ?",
            (criteria, channel_endpoint),
        ).fetchone()
    return dict(row) if row is not None else None


def get_active_subscription(criteria: str, db_path: Path | None = None) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM fhir_subscriptions WHERE criteria = ? AND status = 'active' "
            "ORDER BY updated_at DESC LIMIT 1",
            (criteria,),
        ).fetchone()
    return dict(row) if row is not None else None


def create_flag(
    id: str, location: str, status: str, period_start: str, db_path: Path | None = None
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO fhir_flags (id, location, status, period_start, period_end) "
            "VALUES (?, ?, ?, ?, NULL)",
            (id, location, status, period_start),
        )


def update_flag(id: str, status: str, period_end: str, db_path: Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE fhir_flags SET status = ?, period_end = ? WHERE id = ?",
            (status, period_end, id),
        )


def get_open_flag(location: str, db_path: Path | None = None) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM fhir_flags WHERE location = ? AND status = 'active'", (location,)
        ).fetchone()
    return dict(row) if row is not None else None
