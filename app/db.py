"""SQLite persistence for scored readings and alert-gating state.

Per CLAUDE.md: SQLite holds readings, subscriptions, and alert state. This module handles
readings (milestone 2) and alert_state (milestone 3) - subscriptions come later.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "aquasentinel.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location TEXT NOT NULL,
    reading_time TEXT NOT NULL,
    risk_tier TEXT NOT NULL,
    confidence REAL NOT NULL,
    water_temp_c REAL,
    sp_conductance_uscm REAL,
    dissolved_oxygen_mgl REAL,
    ph REAL,
    turbidity_fnu REAL,
    precip_mm REAL,
    precip_prev_24h_mm REAL,
    threshold_cfu_100ml INTEGER NOT NULL,
    model_version TEXT NOT NULL,
    regime TEXT NOT NULL,
    retrieved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_state (
    location TEXT PRIMARY KEY,
    current_tier TEXT NOT NULL,
    safe_streak_started_at TEXT,
    safe_accumulated_seconds REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    # db_path defaults to None (not DB_PATH) so a test's monkeypatch.setattr(db, "DB_PATH",
    # ...) is honored - a default arg bound to DB_PATH would freeze the ORIGINAL value at
    # import time, before any test gets a chance to patch it.
    conn = sqlite3.connect(db_path if db_path is not None else DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)


def insert_reading(reading: dict, db_path: Path | None = None) -> int:
    """Store a reading dict shaped like app.scoring.pull_reading.pull_reading()'s output."""
    proxies = reading["evidence"]["proxies"]
    rainfall = reading["evidence"]["rainfall_mm"]
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO readings (
                location, reading_time, risk_tier, confidence,
                water_temp_c, sp_conductance_uscm, dissolved_oxygen_mgl, ph, turbidity_fnu,
                precip_mm, precip_prev_24h_mm,
                threshold_cfu_100ml, model_version, regime, retrieved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reading["location"],
                reading["time"],
                reading["risk_tier"],
                reading["confidence"],
                proxies.get("water_temp_c"),
                proxies.get("sp_conductance_uscm"),
                proxies.get("dissolved_oxygen_mgl"),
                proxies.get("ph"),
                proxies.get("turbidity_fnu"),
                rainfall.get("precip_mm"),
                rainfall.get("precip_prev_24h_mm"),
                reading["threshold_cfu_100ml"],
                reading["model_version"],
                reading["regime"],
                reading["retrieved_at"],
            ),
        )
        return cursor.lastrowid


def get_recent_readings(limit: int = 9, db_path: Path | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM readings ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def get_alert_state(location: str, db_path: Path | None = None) -> dict | None:
    """The gating module's memory for one location, or None if it's never been seen before."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM alert_state WHERE location = ?", (location,)
        ).fetchone()
    return dict(row) if row is not None else None


def upsert_alert_state(
    location: str,
    current_tier: str,
    safe_streak_started_at: str | None,
    safe_accumulated_seconds: float,
    updated_at: str,
    db_path: Path | None = None,
) -> None:
    """Replace the stored gating state for one location with the gating module's new decision."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO alert_state (
                location, current_tier, safe_streak_started_at, safe_accumulated_seconds, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(location) DO UPDATE SET
                current_tier = excluded.current_tier,
                safe_streak_started_at = excluded.safe_streak_started_at,
                safe_accumulated_seconds = excluded.safe_accumulated_seconds,
                updated_at = excluded.updated_at
            """,
            (location, current_tier, safe_streak_started_at, safe_accumulated_seconds, updated_at),
        )
