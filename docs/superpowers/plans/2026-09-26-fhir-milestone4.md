# Milestone 4 (FHIR + Subscription mechanics) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver OAH IG-shaped FHIR Observation + Flag resources to a stubbed RPHSA agency
system, using real FHIR R4 Subscription mechanics (criteria, channel, handshake, notify-on-change)
rather than a bare POST-on-change.

**Architecture:** Three new pieces — a pure FHIR resource-builder module, a persistence module for
Subscriptions/Flags, and a delivery/orchestration module — all inside a new `app/fhir/` package,
wired into the existing Milestone 3 gating flow in `app/server.py`. RPHSA is a genuinely separate
FastAPI process (`app/rphsa_stub.py`) with its own database, connected to AquaSentinel only over
HTTP, mirroring how a real agency integration would work.

**Tech Stack:** Python 3.11, FastAPI, httpx (already a dependency — no new installs), SQLite
(`sqlite3` stdlib). No new packages required for this plan.

**Spec:** `docs/superpowers/specs/2026-09-26-fhir-milestone4-design.md` — read it alongside this
plan; this plan argues from it and does not repeat its rationale.

## Global Constraints

- Subscription `criteria` must match exactly `Flag?subject=Location/penns-landing` — anything
  else is rejected, not silently ignored (spec: "Scope decisions", #1).
- Subscription `channel.type` must be exactly `rest-hook` — anything else is rejected.
- RPHSA's database is a genuinely separate SQLite file from `aquasentinel.db` — never a second
  connection into AquaSentinel's own file (spec: "Scope decisions", #2).
- The FHIR code system for risk tier is the placeholder
  `https://aquasentinel.example/fhir/CodeSystem/risk-tier`, codes `safe`/`unsafe` — used
  consistently for both Observation values and Flag codes (spec: "Honesty notes").
- The FHIR Location resource id is `penns-landing` (hyphen — FHIR ids forbid underscores),
  distinct from the internal Python constant `LOCATION_ID = "penns_landing"` in
  `app/scoring/pull_reading.py` (underscore). This is a deliberate mapping, not a bug to fix.
- The handshake is our own webhook-verification convention, not a formally-specified R4 payload
  — code comments must say so, not present it as official FHIR content (spec: "Honesty notes").
- A failed or impossible FHIR delivery must never raise out of `/api/pull-reading` — it is caught,
  logged, and the dashboard's response is unaffected (spec: "Endpoints", step 5).
- Exactly one Flag resource exists per unsafe episode: `unsafe_onset` creates it, the following
  `all_clear` updates that *same* resource rather than creating a new one (spec: "Resource
  shapes").
- Python: 4-space indentation, snake_case naming, comments explain WHY not WHAT (`CLAUDE.md`
  Coding Style).
- No new packages: this plan only uses `httpx` and `fastapi`, both already in
  `requirements.txt`. Do not reach for a FHIR resource library or Pydantic models not already
  used elsewhere in this codebase — the existing style is plain dicts (see `app/server.py`,
  `app/scoring/pull_reading.py`).

## Review Focus

- RPHSA restarts and re-POSTs its Subscription with the same criteria+endpoint — must not create
  a second active Subscription (which would make "the one active Subscription" lookup ambiguous
  and could double-deliver). Covered in Task 3.
- A malformed/incomplete reading dict reaching `emit_event` — must not crash `/api/pull-reading`;
  the resilience wrapper must catch resource-building errors, not only network errors. Covered in
  Task 4.
- An `all_clear` event fires but no Flag is currently open in the database — must raise a clear,
  logged internal error rather than silently fabricating a Flag with a missing start date. Covered
  in Task 4.
- A Subscription's handshake fails (unreachable/invalid `channel.endpoint`) at creation time —
  the Subscription resource must still be created and returned with `status: error` in a
  well-formed `201`, never a `500`. Covered in Task 3.
- A Subscription is rejected for wrong `criteria` or `channel.type` — must leave no row behind in
  `fhir_subscriptions` (reject before persisting, not store-then-invalidate). Covered in Task 3.

---

### Task 1: FHIR resource builders

**Files:**
- Create: `app/fhir/__init__.py`
- Create: `app/fhir/resources.py`
- Test: `app/tests/test_fhir_resources.py`

**Interfaces:**
- Consumes: a reading dict shaped like `app.scoring.pull_reading.pull_reading()`'s output
  (`reading["time"]`, `reading["risk_tier"]`, `reading["evidence"]["proxies"]`).
- Produces (used by Task 4):
  - `RISK_TIER_SYSTEM: str` (the placeholder code system URL)
  - `LOCATION_ID: str` (`"penns-landing"`)
  - `build_flag(flag_id: str, tier: str, status: str, period_start: str, period_end: str | None) -> dict`
  - `build_bundle(reading: dict, flag: dict) -> dict`

- [ ] **Step 1: Write the failing tests**

Create `app/tests/test_fhir_resources.py`:

```python
"""Tests for app/fhir/resources.py - pure FHIR resource builders, no I/O.

Per docs/superpowers/specs/2026-09-26-fhir-milestone4-design.md's Resource shapes section.
"""

from __future__ import annotations

from app.fhir import resources


def _reading(risk_tier: str = "Unsafe") -> dict:
    return {
        "time": "2026-06-01T12:00:00+00:00",
        "risk_tier": risk_tier,
        "evidence": {
            "proxies": {
                "water_temp_c": 21.5,
                "sp_conductance_uscm": 266.0,
                "dissolved_oxygen_mgl": 6.6,
                "ph": 7.3,
                "turbidity_fnu": 6.3,
            }
        },
    }


def test_build_flag_active_uses_unsafe_code():
    flag = resources.build_flag("flag-1", "Unsafe", "active", "2026-06-01T12:00:00+00:00", None)

    assert flag["resourceType"] == "Flag"
    assert flag["id"] == "flag-1"
    assert flag["status"] == "active"
    assert flag["subject"] == {"reference": "Location/penns-landing"}
    assert flag["period"] == {"start": "2026-06-01T12:00:00+00:00"}
    coding = flag["code"]["coding"][0]
    assert coding["system"] == resources.RISK_TIER_SYSTEM
    assert coding["code"] == "unsafe"


def test_build_flag_inactive_sets_period_end():
    flag = resources.build_flag(
        "flag-1", "Safe", "inactive", "2026-06-01T12:00:00+00:00", "2026-06-03T12:00:00+00:00"
    )

    assert flag["status"] == "inactive"
    assert flag["period"]["end"] == "2026-06-03T12:00:00+00:00"
    assert flag["code"]["coding"][0]["code"] == "safe"


def test_build_bundle_contains_one_entry_per_resource():
    flag = resources.build_flag("flag-1", "Unsafe", "active", "2026-06-01T12:00:00+00:00", None)
    bundle = resources.build_bundle(_reading(), flag)

    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "collection"
    # 1 Location + 5 proxy Observations + 1 risk-tier Observation + 1 Flag = 8.
    assert len(bundle["entry"]) == 8

    resource_types = [entry["resource"]["resourceType"] for entry in bundle["entry"]]
    assert resource_types.count("Location") == 1
    assert resource_types.count("Observation") == 6
    assert resource_types.count("Flag") == 1


def test_risk_observation_derived_from_references_proxy_full_urls():
    flag = resources.build_flag("flag-1", "Unsafe", "active", "2026-06-01T12:00:00+00:00", None)
    bundle = resources.build_bundle(_reading(), flag)

    proxy_entries = [
        e for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Observation" and "method" not in e["resource"]
    ]
    risk_entry = next(
        e for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Observation" and "method" in e["resource"]
    )

    proxy_full_urls = {e["fullUrl"] for e in proxy_entries}
    derived_from_refs = {d["reference"] for d in risk_entry["resource"]["derivedFrom"]}

    assert len(proxy_entries) == 5
    assert derived_from_refs == proxy_full_urls


def test_risk_observation_value_matches_tier():
    flag = resources.build_flag("flag-1", "Safe", "inactive", "t", "t")
    bundle = resources.build_bundle(_reading("Safe"), flag)

    risk_entry = next(
        e for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Observation" and "method" in e["resource"]
    )
    coding = risk_entry["resource"]["valueCodeableConcept"]["coding"][0]
    assert coding["code"] == "safe"
    assert coding["system"] == resources.RISK_TIER_SYSTEM
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_fhir_resources.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.fhir'`

- [ ] **Step 3: Write the implementation**

Create `app/fhir/__init__.py` (empty file).

Create `app/fhir/resources.py`:

```python
"""Pure FHIR R4 resource builders - no I/O, no persistence, no network calls.

Per docs/superpowers/specs/2026-09-26-fhir-milestone4-design.md's Resource shapes and
Honesty notes sections:
- RISK_TIER_SYSTEM is an invented placeholder, not a real published OAH IG canonical URL
  (none exists anywhere in this repo) - clearly namespaced under .example, RFC 2606's
  reserved placeholder domain, so nobody mistakes it for a real registered system.
- LOCATION_ID uses a hyphen ("penns-landing") because FHIR resource ids forbid underscores
  ([A-Za-z0-9\\-\\.]{1,64}) - the internal Python constant elsewhere in this codebase
  (app.scoring.pull_reading.LOCATION_ID) uses an underscore ("penns_landing"). Deliberate,
  not a bug.
- Units on proxy Observations are plain display strings only (no UCUM system/code) - we
  have not independently verified UCUM codes for FNU/pH/uS-cm against the official UCUM
  table, and would rather omit a coded unit than assert one we haven't checked.
"""

from __future__ import annotations

import uuid

RISK_TIER_SYSTEM = "https://aquasentinel.example/fhir/CodeSystem/risk-tier"
LOCATION_ID = "penns-landing"
LOCATION_NAME = "Penn's Landing, Center City tidal Delaware"
LOCATION_LAT = 39.946402
LOCATION_LON = -75.139360

PROXY_UNITS = {
    "water_temp_c": "°C",
    "sp_conductance_uscm": "µS/cm",
    "dissolved_oxygen_mgl": "mg/L",
    "ph": "pH",
    "turbidity_fnu": "FNU",
}
PROXY_DISPLAY_NAMES = {
    "water_temp_c": "Water temperature",
    "sp_conductance_uscm": "Specific conductance",
    "dissolved_oxygen_mgl": "Dissolved oxygen",
    "ph": "pH",
    "turbidity_fnu": "Turbidity",
}


def _tier_code(tier: str) -> str:
    return "unsafe" if tier == "Unsafe" else "safe"


def build_location() -> dict:
    return {
        "resourceType": "Location",
        "id": LOCATION_ID,
        "name": LOCATION_NAME,
        "position": {"latitude": LOCATION_LAT, "longitude": LOCATION_LON},
    }


def build_proxy_observations(reading: dict) -> list[dict]:
    """One Bundle-entry per live proxy, each with its own fullUrl so the risk-tier
    Observation's derivedFrom can reference them within the same Bundle.
    """
    proxies = reading["evidence"]["proxies"]
    effective_time = reading["time"]
    entries = []
    for name, value in proxies.items():
        entries.append({
            "fullUrl": f"urn:uuid:{uuid.uuid4()}",
            "resource": {
                "resourceType": "Observation",
                "id": str(uuid.uuid4()),
                "status": "preliminary",
                "code": {"text": PROXY_DISPLAY_NAMES.get(name, name)},
                "subject": {"reference": f"Location/{LOCATION_ID}"},
                "effectiveDateTime": effective_time,
                "valueQuantity": {"value": value, "unit": PROXY_UNITS.get(name, "")},
            },
        })
    return entries


def build_risk_observation(reading: dict, proxy_entries: list[dict]) -> dict:
    tier_code = _tier_code(reading["risk_tier"])
    return {
        "fullUrl": f"urn:uuid:{uuid.uuid4()}",
        "resource": {
            "resourceType": "Observation",
            "id": str(uuid.uuid4()),
            "status": "preliminary",
            "method": {"text": "Estimated (modeled) risk"},
            "code": {"text": "E. coli risk tier estimate"},
            "subject": {"reference": f"Location/{LOCATION_ID}"},
            "effectiveDateTime": reading["time"],
            "valueCodeableConcept": {
                "coding": [
                    {"system": RISK_TIER_SYSTEM, "code": tier_code, "display": reading["risk_tier"]}
                ],
                "text": reading["risk_tier"],
            },
            "derivedFrom": [{"reference": entry["fullUrl"]} for entry in proxy_entries],
        },
    }


def build_flag(flag_id: str, tier: str, status: str, period_start: str, period_end: str | None) -> dict:
    period = {"start": period_start}
    if period_end:
        period["end"] = period_end
    return {
        "resourceType": "Flag",
        "id": flag_id,
        "status": status,
        "code": {
            "coding": [{"system": RISK_TIER_SYSTEM, "code": _tier_code(tier), "display": tier}],
            "text": tier,
        },
        "subject": {"reference": f"Location/{LOCATION_ID}"},
        "period": period,
    }


def build_bundle(reading: dict, flag: dict) -> dict:
    location_entry = {"fullUrl": f"urn:uuid:{uuid.uuid4()}", "resource": build_location()}
    proxy_entries = build_proxy_observations(reading)
    risk_entry = build_risk_observation(reading, proxy_entries)
    flag_entry = {"fullUrl": f"urn:uuid:{uuid.uuid4()}", "resource": flag}
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [location_entry] + proxy_entries + [risk_entry, flag_entry],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_fhir_resources.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/fhir/__init__.py app/fhir/resources.py app/tests/test_fhir_resources.py
git commit -m "feat: add pure FHIR resource builders for Milestone 4"
```

---

### Task 2: FHIR storage layer (Subscriptions and Flags)

**Files:**
- Create: `app/fhir/store.py`
- Test: `app/tests/test_fhir_store.py`

**Interfaces:**
- Consumes: nothing new (uses `sqlite3` directly, same pattern as `app/db.py`).
- Produces (used by Tasks 3, 4, 6, 7):
  - `DB_PATH: Path` (module-level, monkeypatchable like `app.db.DB_PATH`)
  - `init_db(db_path: Path | None = None) -> None`
  - `create_subscription(id: str, criteria: str, channel_endpoint: str, now: str, db_path: Path | None = None) -> None`
  - `update_subscription_status(id: str, status: str, now: str, db_path: Path | None = None) -> None`
  - `get_subscription_by_criteria_and_endpoint(criteria: str, channel_endpoint: str, db_path: Path | None = None) -> dict | None`
  - `get_active_subscription(criteria: str, db_path: Path | None = None) -> dict | None`
  - `create_flag(id: str, location: str, status: str, period_start: str, db_path: Path | None = None) -> None`
  - `update_flag(id: str, status: str, period_end: str, db_path: Path | None = None) -> None`
  - `get_open_flag(location: str, db_path: Path | None = None) -> dict | None`

- [ ] **Step 1: Write the failing tests**

Create `app/tests/test_fhir_store.py`:

```python
"""Tests for app/fhir/store.py - SQLite persistence for FHIR Subscriptions and Flags."""

from __future__ import annotations

from app.fhir import store


def test_create_and_fetch_subscription_by_criteria_and_endpoint(tmp_path):
    db_path = tmp_path / "test.db"
    store.init_db(db_path=db_path)

    store.create_subscription("sub-1", "Flag?subject=Location/penns-landing",
                               "http://rphsa/notifications", "2026-06-01T00:00:00+00:00",
                               db_path=db_path)

    found = store.get_subscription_by_criteria_and_endpoint(
        "Flag?subject=Location/penns-landing", "http://rphsa/notifications", db_path=db_path
    )
    assert found["id"] == "sub-1"
    assert found["status"] == "requested"


def test_get_active_subscription_ignores_non_active_ones(tmp_path):
    db_path = tmp_path / "test.db"
    store.init_db(db_path=db_path)
    store.create_subscription("sub-1", "Flag?subject=Location/penns-landing",
                               "http://rphsa/notifications", "2026-06-01T00:00:00+00:00",
                               db_path=db_path)

    assert store.get_active_subscription("Flag?subject=Location/penns-landing", db_path=db_path) is None

    store.update_subscription_status("sub-1", "active", "2026-06-01T00:01:00+00:00", db_path=db_path)

    active = store.get_active_subscription("Flag?subject=Location/penns-landing", db_path=db_path)
    assert active["id"] == "sub-1"
    assert active["channel_endpoint"] == "http://rphsa/notifications"


def test_flag_lifecycle_create_then_update_same_id(tmp_path):
    db_path = tmp_path / "test.db"
    store.init_db(db_path=db_path)

    store.create_flag("flag-1", "penns_landing", "active", "2026-06-01T00:00:00+00:00", db_path=db_path)

    open_flag = store.get_open_flag("penns_landing", db_path=db_path)
    assert open_flag["id"] == "flag-1"
    assert open_flag["status"] == "active"
    assert open_flag["period_end"] is None

    store.update_flag("flag-1", "inactive", "2026-06-03T00:00:00+00:00", db_path=db_path)

    assert store.get_open_flag("penns_landing", db_path=db_path) is None  # no longer open


def test_get_open_flag_returns_none_when_nothing_is_open(tmp_path):
    db_path = tmp_path / "test.db"
    store.init_db(db_path=db_path)

    assert store.get_open_flag("penns_landing", db_path=db_path) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_fhir_store.py -v`
Expected: FAIL with `AttributeError: module 'app.fhir.store' has no attribute ...` (or import error)

- [ ] **Step 3: Write the implementation**

Create `app/fhir/store.py`:

```python
"""SQLite persistence for FHIR Subscriptions and Flags - Milestone 4.

Lives in the same aquasentinel.db as app/db.py's readings/alert_state tables (per
docs/superpowers/specs/2026-09-26-fhir-milestone4-design.md's Storage section), but keeps
its own schema and query functions in this module rather than growing app/db.py, so FHIR
concerns stay in the app/fhir/ package.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "aquasentinel.db"

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_fhir_store.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/fhir/store.py app/tests/test_fhir_store.py
git commit -m "feat: add SQLite persistence for FHIR Subscriptions and Flags"
```

---

### Task 3: Subscription creation endpoint with handshake

**Files:**
- Create: `app/fhir/routes.py`
- Test: `app/tests/test_fhir_routes.py`

**Interfaces:**
- Consumes: `app.fhir.store` (Task 2) - all functions listed there.
- Produces (used by Task 6):
  - `router: fastapi.APIRouter` (exposes `POST /fhir/Subscription`)
  - `SUPPORTED_CRITERIA: str`
  - `handle_create_subscription(body: dict, client: httpx.Client | None = None) -> tuple[dict, int]`
    (the testable business logic behind the route)

- [ ] **Step 1: Write the failing tests**

Create `app/tests/test_fhir_routes.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_fhir_routes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.fhir.routes'`

- [ ] **Step 3: Write the implementation**

Create `app/fhir/routes.py`:

```python
"""FHIR Subscription creation - the R4 rest-hook + handshake pattern.

Per docs/superpowers/specs/2026-09-26-fhir-milestone4-design.md: scoped to our one real
topic (Flag changes for Location/penns-landing) - any other criteria or channel type is
rejected before anything is persisted. The handshake is our own webhook-verification
convention (a plain confirmation POST), not a formally-specified R4 payload - base FHIR R4
does not mandate a handshake structure (see the spec's Honesty notes).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.fhir import store

SUPPORTED_CRITERIA = "Flag?subject=Location/penns-landing"
SUPPORTED_CHANNEL_TYPE = "rest-hook"

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
    criteria = body.get("criteria")
    channel = body.get("channel", {})
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
    body = await request.json()
    content, status_code = handle_create_subscription(body)
    return JSONResponse(status_code=status_code, content=content)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_fhir_routes.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/fhir/routes.py app/tests/test_fhir_routes.py
git commit -m "feat: add FHIR Subscription creation endpoint with handshake"
```

---

### Task 4: Event emission and delivery

**Files:**
- Create: `app/fhir/emit.py`
- Test: `app/tests/test_fhir_emit.py`

**Interfaces:**
- Consumes: `app.fhir.resources` (Task 1), `app.fhir.store` (Task 2). A `decision` dict shaped
  like `app.alerts.gating.evaluate_reading()`'s output (`decision["location"]`,
  `decision["agency_event"]`).
- Produces (used by Task 6):
  - `emit_event(reading: dict, decision: dict, client: httpx.Client | None = None) -> None`
    (never raises)

- [ ] **Step 1: Write the failing tests**

Create `app/tests/test_fhir_emit.py`:

```python
"""Tests for app/fhir/emit.py - delivering FHIR Bundles on a gating agency event.

Per the spec's Review Focus: emit_event must never raise (a malformed reading or an
all_clear-with-no-open-Flag are both internal problems that must be logged, not crash
/api/pull-reading), and a no-op (no active Subscription) must not attempt any network call.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.fhir import emit, store


def _reading(risk_tier: str = "Unsafe", location: str = "penns_landing") -> dict:
    return {
        "location": location,
        "time": "2026-06-01T12:00:00+00:00",
        "risk_tier": risk_tier,
        "evidence": {
            "proxies": {
                "water_temp_c": 21.5, "sp_conductance_uscm": 266.0,
                "dissolved_oxygen_mgl": 6.6, "ph": 7.3, "turbidity_fnu": 6.3,
            }
        },
    }


def _client_recording_posts(sink: list) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        sink.append(json.loads(request.content))
        return httpx.Response(200, json={"received": True})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_no_op_when_agency_event_is_none(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db(db_path=tmp_path / "test.db")

    calls = []
    emit.emit_event(_reading(), {"location": "penns_landing", "agency_event": None},
                     client=_client_recording_posts(calls))

    assert calls == []


def test_no_op_when_no_active_subscription_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db(db_path=tmp_path / "test.db")

    calls = []
    emit.emit_event(_reading(), {"location": "penns_landing", "agency_event": "unsafe_onset"},
                     client=_client_recording_posts(calls))

    assert calls == []  # no Subscription registered yet - real Subscription semantics


def test_unsafe_onset_creates_flag_and_delivers_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db(db_path=tmp_path / "test.db")
    store.create_subscription("sub-1", "Flag?subject=Location/penns-landing",
                               "http://rphsa/notifications", "2026-06-01T00:00:00+00:00")
    store.update_subscription_status("sub-1", "active", "2026-06-01T00:00:00+00:00")

    calls = []
    emit.emit_event(_reading("Unsafe"), {"location": "penns_landing", "agency_event": "unsafe_onset"},
                     client=_client_recording_posts(calls))

    assert len(calls) == 1
    flag_entries = [e for e in calls[0]["entry"] if e["resource"]["resourceType"] == "Flag"]
    assert flag_entries[0]["resource"]["status"] == "active"

    open_flag = store.get_open_flag("penns_landing")
    assert open_flag is not None


def test_all_clear_updates_the_same_flag_not_a_new_one(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db(db_path=tmp_path / "test.db")
    store.create_subscription("sub-1", "Flag?subject=Location/penns-landing",
                               "http://rphsa/notifications", "2026-06-01T00:00:00+00:00")
    store.update_subscription_status("sub-1", "active", "2026-06-01T00:00:00+00:00")

    calls = []
    emit.emit_event(_reading("Unsafe"), {"location": "penns_landing", "agency_event": "unsafe_onset"},
                     client=_client_recording_posts(calls))
    onset_flag_id = store.get_open_flag("penns_landing")["id"]

    emit.emit_event(_reading("Safe"), {"location": "penns_landing", "agency_event": "all_clear"},
                     client=_client_recording_posts(calls))

    assert store.get_open_flag("penns_landing") is None  # closed, not left open
    flag_entries = [e for e in calls[1]["entry"] if e["resource"]["resourceType"] == "Flag"]
    assert flag_entries[0]["resource"]["id"] == onset_flag_id  # same id, not a new Flag
    assert flag_entries[0]["resource"]["status"] == "inactive"


def test_all_clear_with_no_open_flag_is_logged_not_raised(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db(db_path=tmp_path / "test.db")

    # No unsafe_onset ever happened, so there is no open Flag - this is an internal
    # inconsistency (gating and FHIR state disagree), not a normal path.
    emit.emit_event(_reading("Safe"), {"location": "penns_landing", "agency_event": "all_clear"})

    assert "no open Flag exists" in caplog.text


def test_malformed_reading_does_not_raise(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.init_db(db_path=tmp_path / "test.db")
    store.create_subscription("sub-1", "Flag?subject=Location/penns-landing",
                               "http://rphsa/notifications", "2026-06-01T00:00:00+00:00")
    store.update_subscription_status("sub-1", "active", "2026-06-01T00:00:00+00:00")

    broken_reading = {"location": "penns_landing", "time": "2026-06-01T12:00:00+00:00",
                       "risk_tier": "Unsafe"}  # missing "evidence" entirely

    emit.emit_event(broken_reading, {"location": "penns_landing", "agency_event": "unsafe_onset"})

    assert "FHIR delivery failed" in caplog.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_fhir_emit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.fhir.emit'`

- [ ] **Step 3: Write the implementation**

Create `app/fhir/emit.py`:

```python
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
        flag_id = str(uuid.uuid4())
        store.create_flag(flag_id, location, "active", reading["time"])
        flag = resources.build_flag(flag_id, reading["risk_tier"], "active", reading["time"], None)
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
        client.post(subscription["channel_endpoint"], json=bundle)
    finally:
        if owns_client:
            client.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_fhir_emit.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add app/fhir/emit.py app/tests/test_fhir_emit.py
git commit -m "feat: add FHIR event emission and delivery to RPHSA's Subscription"
```

---

### Task 5: RPHSA stub app

**Files:**
- Create: `app/rphsa_stub.py`
- Test: `app/tests/test_rphsa_stub.py`

**Interfaces:**
- Consumes: nothing from earlier tasks at import time (only calls AquaSentinel's
  `/fhir/Subscription` over HTTP at runtime, not via a Python import).
- Produces (used by Task 7):
  - `app: fastapi.FastAPI`
  - `DB_PATH: Path`
  - `init_db(db_path: Path | None = None) -> None`
  - `store_notification(kind: str, payload: dict, db_path: Path | None = None) -> None`
  - `get_notifications(db_path: Path | None = None) -> list[dict]`
  - `register_with_aquasentinel(client: httpx.Client | None = None) -> dict`

- [ ] **Step 1: Write the failing tests**

Create `app/tests/test_rphsa_stub.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_rphsa_stub.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.rphsa_stub'`

- [ ] **Step 3: Write the implementation**

Create `app/rphsa_stub.py`:

```python
"""RPHSA stub - a standalone FastAPI app simulating the (fictional) Regional Public
Health Surveillance Agency's receiving system. Genuinely separate from AquaSentinel: its
own process, its own port, its own SQLite file - connected only over HTTP, the same way
a real agency integration would work.

Per docs/superpowers/specs/2026-09-26-fhir-milestone4-design.md.

Run: ./venv/bin/uvicorn app.rphsa_stub:app --port 8001 --reload
"""

from __future__ import annotations

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        register_with_aquasentinel()
    except httpx.HTTPError:
        logger.warning("Could not register with AquaSentinel at startup (is it running?)")
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_rphsa_stub.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/rphsa_stub.py app/tests/test_rphsa_stub.py
git commit -m "feat: add RPHSA stub as a standalone FastAPI process"
```

---

### Task 6: Wire into app/server.py, plus env and docs

**Files:**
- Modify: `app/server.py`
- Modify: `app/tests/test_server.py`
- Modify: `.env.example`
- Modify: `CLAUDE.md`
- Modify: `plan.md`

**Interfaces:**
- Consumes: `app.fhir.routes.router` (Task 3), `app.fhir.store.init_db` (Task 2),
  `app.fhir.emit.emit_event` (Task 4).
- Produces: nothing new - this task only wires existing pieces together.

- [ ] **Step 1: Update `app/tests/test_server.py`'s fixture to init the FHIR schema too**

The `_client()` helper currently only initializes `app.db`'s schema. Since `/api/pull-reading`
will now also call `fhir.emit.emit_event()` (which reads/writes `fhir_flags`/`fhir_subscriptions`
tables), the test fixture needs those tables to exist too, in the same tmp database file:

```python
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
```

Also add `monkeypatch.setattr(fhir_store, "DB_PATH", tmp_path / "test.db")` and
`fhir_store.init_db()` to `test_readings_endpoint_returns_newest_first` and
`test_readings_endpoint_respects_limit`, which build their own `TestClient` directly instead
of using `_client()` — for consistency, even though those two tests don't call
`/api/pull-reading` and so don't strictly need it, matching the pattern keeps all four DB-using
tests in this file symmetric and avoids a subtle gap if their scope grows later.

Then add one new test to this file — the spec's resilience requirement ("a failed FHIR delivery
must never surface as an error on `/api/pull-reading`") is only proven at the `emit_event()` unit
level by Task 4's tests; nothing yet proves it at the actual HTTP-endpoint level, with a real
active Subscription pointing at an address that will genuinely fail to connect:

```python
# fhir_store is already imported at the top of this file from Step 1's fixture change.

def test_pull_reading_endpoint_succeeds_even_if_fhir_delivery_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "pull_reading", lambda: _fake_reading("Unsafe"))
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
```

- [ ] **Step 2: Run the server tests to confirm they still pass before changing `app/server.py`**

Run: `./venv/bin/python -m pytest app/tests/test_server.py -v`
Expected: PASS (6 tests - the 5 original ones plus the new resilience test just added; the new
one passes trivially at this point since `app/server.py` doesn't call `emit_event()` yet, so
the unreachable Subscription can't affect anything yet. It becomes a meaningful check only
after Step 3's wiring - re-run it then too, in Step 4.)

- [ ] **Step 3: Wire `app/server.py`**

```python
from app import db
from app.alerts.gating import evaluate_reading
from app.fhir import emit as fhir_emit
from app.fhir import routes as fhir_routes
from app.fhir import store as fhir_store
from app.ingestion.nws import RainfallUnavailable
from app.ingestion.usgs import UsgsDataUnavailable
from app.scoring.pull_reading import pull_reading
```

Update the `lifespan` function:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    fhir_store.init_db()
    yield
```

Mount the router right after `app = FastAPI(...)`:

```python
app = FastAPI(title="AquaSentinel", lifespan=lifespan)
app.include_router(fhir_routes.router)
```

Update the pull-reading endpoint to capture the gating decision and pass it to `emit_event`:

```python
@app.post("/api/pull-reading")
def api_pull_reading() -> dict:
    """Fetch a real live reading, score it, persist it, gate it, and return the reading.

    Fails closed: if USGS or NWS can't be reached or parsed, this returns 503 rather
    than a fabricated reading. The alert-rules gating (milestone 3) runs after every real
    pull - triggered here by the dashboard (on open, on click, or hourly while open), not
    by a background scheduler (see plan.md's Open Questions for that known gap). Its
    decision now also drives FHIR delivery to RPHSA's Subscription (milestone 4) when it
    represents a real agency event - but that delivery is best-effort: a failure there
    (see app/fhir/emit.py) never surfaces here or to the dashboard.
    """
    try:
        reading = pull_reading()
    except (UsgsDataUnavailable, RainfallUnavailable) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    db.insert_reading(reading)
    decision = evaluate_reading(reading)
    fhir_emit.emit_event(reading, decision)
    return reading
```

- [ ] **Step 4: Run the full test suite to confirm nothing broke**

Run: `./venv/bin/python -m pytest app/tests/ -v`
Expected: PASS (all tests, including the new FHIR ones from Tasks 1-5 and the existing
Milestone 1-3 tests)

- [ ] **Step 5: Add the non-secret configurable URLs to `.env.example`**

Append to `.env.example`:

```
# AquaSentinel's own base URL - used by the RPHSA stub (app/rphsa_stub.py) to know where
# to register its FHIR Subscription at startup. Not a secret; safe to commit as a default.
AQUASENTINEL_BASE_URL=http://localhost:8000

# RPHSA stub's own base URL - tells AquaSentinel where to deliver FHIR notifications.
# Not a secret; safe to commit as a default. No production deploy exists for this
# hackathon prototype (see plan.md's Rollout section), so localhost defaults are fine.
RPHSA_BASE_URL=http://localhost:8001
```

- [ ] **Step 6: Add the RPHSA stub's run command to `CLAUDE.md`**

In `CLAUDE.md`'s "## Run Commands" section, add a line after the existing "Dev server" line:

```
- RPHSA stub (Milestone 4 demo, fictional agency receiver): `./venv/bin/uvicorn
  app.rphsa_stub:app --port 8001 --reload` (run alongside the main dev server so it can
  register its Subscription and receive FHIR notifications)
```

- [ ] **Step 7: Point `plan.md`'s Milestone 4 entry at the spec**

In `plan.md`'s Milestones section, change the Milestone 4 line from:

```
4. **FHIR out.** OAH IG Observation + `Flag` delivered over a FHIR `Subscription` to the RPHSA
   stub. Done when a tier change produces a valid Flag at the stub endpoint, year-round.
```

to:

```
4. **FHIR out.** OAH IG Observation + `Flag` delivered over a FHIR `Subscription` to the RPHSA
   stub, with real Subscription mechanics (criteria, channel, handshake) - see
   `docs/superpowers/specs/2026-09-26-fhir-milestone4-design.md` for the full design. Done when
   a tier change produces a valid Flag at the stub endpoint, year-round.
```

- [ ] **Step 8: Commit**

```bash
git add app/server.py app/tests/test_server.py .env.example CLAUDE.md plan.md
git commit -m "feat: wire FHIR delivery into the pull-reading endpoint"
```

---

### Task 7: End-to-end integration test

**Files:**
- Create: `app/tests/test_fhir_end_to_end.py`

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: nothing new - this is a verification-only task.

- [ ] **Step 1: Write the failing test**

Create `app/tests/test_fhir_end_to_end.py`:

```python
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

import httpx

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
            }
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

    rphsa_client = httpx.Client(
        transport=httpx.ASGITransport(app=rphsa_stub.app), base_url="http://rphsa"
    )

    decision = {"location": "penns_landing", "agency_event": "unsafe_onset"}
    fhir_emit.emit_event(_reading("Unsafe"), decision, client=rphsa_client)

    notifications = rphsa_stub.get_notifications(db_path=tmp_path / "rphsa.db")
    events = [n for n in notifications if n["kind"] == "event"]
    assert len(events) == 1

    bundle = json.loads(events[0]["payload"])
    assert bundle["resourceType"] == "Bundle"
    flag_entries = [e for e in bundle["entry"] if e["resource"]["resourceType"] == "Flag"]
    assert flag_entries[0]["resource"]["status"] == "active"


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

    rphsa_client = httpx.Client(
        transport=httpx.ASGITransport(app=rphsa_stub.app), base_url="http://rphsa"
    )

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest app/tests/test_fhir_end_to_end.py -v`
Expected: FAIL only if Tasks 1-6 aren't complete yet. If all prior tasks are done, this may
already PASS on the first run since it only exercises already-implemented code - in that case
skip to Step 4 and confirm this consciously rather than treating a pass as a mistake.

- [ ] **Step 3: No new implementation code needed**

This task is verification-only - Tasks 1 through 6 already implement everything this test
exercises. If it fails, the fix belongs in whichever earlier task's file is actually wrong
(check the failure message against Tasks 1-6, don't add new code in this task's file).

- [ ] **Step 4: Run the full test suite one final time**

Run: `./venv/bin/python -m pytest app/tests/ -v`
Expected: PASS (every test in the project, Milestones 1 through 4)

- [ ] **Step 5: Manually verify against the real dashboard**

Run: `./venv/bin/uvicorn app.server:app --reload` in one terminal and
`./venv/bin/uvicorn app.rphsa_stub:app --port 8001 --reload` in another. Open
`http://localhost:8000` in a browser, confirm no console errors (per this project's Definition
of Done), then check `http://localhost:8001/rphsa/notifications` in a second tab - it should
show at least one `handshake` notification (from RPHSA's own startup registration) once both
servers have started, in either order.

- [ ] **Step 6: Commit**

```bash
git add app/tests/test_fhir_end_to_end.py
git commit -m "test: add end-to-end FHIR delivery verification for Milestone 4"
```
