"""SQLite persistence for scored readings and alert-gating state.

This module handles readings (milestone 2) and alert_state (milestone 3). FHIR Subscriptions
and Flags (milestone 4, agency-side) live in their own module, app/fhir/store.py. There is no
public-subscriber store - no direct-to-public alerting exists, per plan.md's 2026-09-26 scope
decision.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from app import config

# Still a module-level name (tests patch it); its value comes from AQUASENTINEL_DB_PATH when set.
DB_PATH = config.resolve_db_path(os.environ.get(config.DB_PATH_ENV_VAR))

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
    -- Milestone 1b: what actually decided risk_tier. precip_prev_48h_mm is the value the
    -- rainfall rule compared against rule_threshold_mm; model_probability_unsafe only fed
    -- confidence. Stored so the tier can be audited later, not just displayed.
    precip_prev_48h_mm REAL,
    rainfall_source TEXT,
    decision_basis TEXT,
    rule_threshold_mm REAL,
    model_probability_unsafe REAL,
    -- Milestone 6: which outfall (if any) escalated this reading to Unsafe.
    cso_outfall_name TEXT,
    cso_outfall_status INTEGER,
    cso_distance_km REAL,
    cso_last_poll TEXT,
    -- The dashboard's CSO field: "Overflow" / "No overflow" / "Reading unavailable".
    -- NULL on rows stored before this existed - read back as "Reading unavailable".
    cso_state TEXT,
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


_CSO_COLUMNS = [
    ("cso_outfall_name", "TEXT"),
    ("cso_outfall_status", "INTEGER"),
    ("cso_distance_km", "REAL"),
    ("cso_last_poll", "TEXT"),
    ("cso_state", "TEXT"),
]


def init_db(db_path: Path | None = None) -> None:
    # A freshly attached disk's mount folder exists, but a sub-folder under it may not - create
    # it rather than crash on first start.
    Path(db_path if db_path is not None else DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        _migrate_cso_columns(conn)


def _migrate_cso_columns(conn: sqlite3.Connection) -> None:
    """CREATE TABLE IF NOT EXISTS is a no-op on an existing table - a readings table
    created before Milestone 6 is missing the 4 cso_* columns. Add whichever are missing
    so an existing database keeps working after this upgrade, instead of every
    insert_reading() call raising OperationalError.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(readings)")}
    for column_name, column_type in _CSO_COLUMNS:
        if column_name not in existing:
            conn.execute(f"ALTER TABLE readings ADD COLUMN {column_name} {column_type}")


def _reading_columns(reading: dict) -> dict:
    """Column name -> value for one reading, shared by the INSERT and the UPDATE below so the
    two can never list different columns."""
    evidence = reading["evidence"]
    proxies = evidence["proxies"]
    rainfall = evidence["rainfall_mm"]
    cso_status = evidence.get("cso_status") or {}
    return {
        "location": reading["location"],
        "reading_time": reading["time"],
        "risk_tier": reading["risk_tier"],
        "confidence": reading["confidence"],
        "water_temp_c": proxies.get("water_temp_c"),
        "sp_conductance_uscm": proxies.get("sp_conductance_uscm"),
        "dissolved_oxygen_mgl": proxies.get("dissolved_oxygen_mgl"),
        "ph": proxies.get("ph"),
        "turbidity_fnu": proxies.get("turbidity_fnu"),
        "precip_mm": rainfall.get("precip_mm"),
        "precip_prev_24h_mm": rainfall.get("precip_prev_24h_mm"),
        "precip_prev_48h_mm": rainfall.get("precip_prev_48h_mm"),
        "rainfall_source": evidence.get("rainfall_source"),
        "decision_basis": evidence.get("decision_basis"),
        "rule_threshold_mm": evidence.get("rule_threshold_mm"),
        "model_probability_unsafe": evidence.get("model_probability_unsafe"),
        "cso_outfall_name": cso_status.get("outfall_name"),
        "cso_outfall_status": cso_status.get("status"),
        "cso_distance_km": cso_status.get("distance_km"),
        "cso_last_poll": cso_status.get("last_poll"),
        "cso_state": evidence.get("cso"),
        "threshold_cfu_100ml": reading["threshold_cfu_100ml"],
        "model_version": reading["model_version"],
        "regime": reading["regime"],
        "retrieved_at": reading["retrieved_at"],
    }


def insert_reading(reading: dict, db_path: Path | None = None) -> int:
    """Store a reading dict shaped like app.scoring.pull_reading.pull_reading()'s output."""
    columns = _reading_columns(reading)
    # The column names come from the literal dict above, never from user input, so building
    # the SQL text from them is safe; the values still go through ? placeholders.
    names = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    with _connect(db_path) as conn:
        cursor = conn.execute(
            f"INSERT INTO readings ({names}) VALUES ({placeholders})",
            tuple(columns.values()),
        )
        return cursor.lastrowid


def save_reading(reading: dict, db_path: Path | None = None) -> int:
    """Store a reading, keeping ONE row per gauge reading (location + reading_time).

    reading_time is when the USGS gauge measured, and the gauge only updates about every 15
    minutes - so pulling again inside that window must not add an identical-looking row. A
    repeat pull UPDATES the stored row instead (rainfall and CSO can change faster than the
    gauge, so the newer values win). Returns the row's id.
    """
    columns = _reading_columns(reading)
    with _connect(db_path) as conn:
        existing = conn.execute(
            "SELECT MAX(id) FROM readings WHERE location = ? AND reading_time = ?",
            (columns["location"], columns["reading_time"]),
        ).fetchone()[0]
        if existing is None:
            names = ", ".join(columns)
            placeholders = ", ".join("?" for _ in columns)
            cursor = conn.execute(
                f"INSERT INTO readings ({names}) VALUES ({placeholders})",
                tuple(columns.values()),
            )
            return cursor.lastrowid
        assignments = ", ".join(f"{name} = ?" for name in columns)
        conn.execute(
            f"UPDATE readings SET {assignments} WHERE id = ?",
            (*columns.values(), existing),
        )
        return existing


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
