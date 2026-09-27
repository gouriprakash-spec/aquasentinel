# Milestone 5 (Agent-ready layer) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish AquaSentinel's scored reading through three agent-facing surfaces —
`GET /api/status`, a read-only MCP server, `GET /llms.txt`, and JSON-LD on the dashboard — all
built from one shared data path so they can never disagree with each other or the dashboard
banner.

**Architecture:** A new pure-function status-contract builder (`app/status.py`) is the single
source of truth for "what a reading looks like" to any consumer; `/api/status` and the MCP
server's tools both call it. The MCP server (`app/mcp_server.py`, official SDK v1's `FastMCP`) is
mounted into the *same* FastAPI process as the dashboard, as the very last route registered — a
verified requirement, not a style choice (see Global Constraints).

**Tech Stack:** Python 3.11, FastAPI 0.115.0, the official MCP Python SDK (`mcp==1.30.0`,
pinned to v1's `FastMCP` API — 2.x renamed it to `MCPServer`), `starlette==0.38.6` (pinned
explicitly — see Global Constraints for why).

**Spec:** `docs/superpowers/specs/2026-09-27-agent-ready-layer-milestone5-design.md` — read it
alongside this plan; this plan argues from it and does not repeat its rationale.

## Global Constraints

- `starlette==0.38.6` and `mcp==1.30.0` are both already installed and pinned in
  `requirements.txt` (done prior to this plan, 2026-09-27) — do not `pip install mcp` again
  without the `<2` constraint, and do not let anything upgrade `starlette` past `<0.39.0`
  (`fastapi==0.115.0`'s own requirement).
- **`app.mount("/", mcp_server.mcp.streamable_http_app())` must be the LAST statement that
  registers anything on `app/server.py`'s `app` object** — after every `@app.get`/`@app.post`
  and `app.include_router(...)` call. Verified directly (not assumed): a route registered
  *after* a root `Mount` is silently shadowed and 404s, even though the route decorator itself
  raises no error. This is why Task 3 only *creates* `app/mcp_server.py` without mounting it —
  the actual mount happens in Task 6, after every other route this plan adds.
- `mcp.streamable_http_app()` already serves its own route internally at
  `mcp.settings.streamable_http_path` (default `/mcp`) — mount it at `"/"`, never at `"/mcp"`
  (that would double the path to `/mcp/mcp`).
- The MCP server's `transport_security` (DNS-rebinding protection) must list the hosts actually
  used: `testserver` (for `TestClient`-based tests) and `localhost:8000`/`127.0.0.1:8000` (for
  local dev). **This does not yet include a real deployed hostname** — see Review Focus.
- The status contract never includes `estimate_cfu_100ml` (architecture rule: never show a
  bacteria value the system doesn't have) or `location_name` (not stored per-row in `db.py`'s
  schema — omitted, not invented).
- Python: 4-space indentation, snake_case naming, comments explain WHY not WHAT (`CLAUDE.md`
  Coding Style).

## Review Focus

- An unknown `location_id` passed to `get_current_status`, `get_recent_readings`, or
  `GET /api/status`'s `location` query param — must return a clear "unavailable" response, never
  a guess, a crash, or silently falling back to the one known location. Covered in Tasks 2 and 3.
- An empty database (no reading ever pulled) — `/api/status`, every MCP tool, and the JSON-LD
  `dateModified` substitution must all handle this without crashing or fabricating a reading.
  Covered in Tasks 2, 3, and 5.
- The JSON-LD placeholder token silently failing to get replaced (a typo mismatch between the
  token string in `index.html` and the one `server.py` searches for would make `.replace()`
  silently no-op, leaving the literal placeholder text in the served page). Covered in Task 5
  with an explicit assertion that the token is *gone*, not just that a valid-looking date exists.
- Registering a new route on `app/server.py` *after* the MCP mount in the future (a very easy
  mistake to reintroduce, given how silent the failure mode is — no error, just a 404). Task 6
  includes a regression test that every pre-existing route (`/api/pull-reading`, `/api/readings`,
  `/logo.png`) still resolves correctly once the mount is added, precisely because this failure
  mode produces no exception to catch otherwise.
- **The MCP transport-security host allowlist will reject all real traffic once actually
  deployed** — it's currently hardcoded to `testserver`/`localhost`/`127.0.0.1:8000` only. This
  is a known, deliberate gap, not an oversight: the real deployed hostname doesn't exist yet
  (no host has been chosen — see the `deployment_target` project decision), so there is nothing
  real to add to `app/mcp_server.py`'s `allowed_hosts`/`allowed_origins` yet. Not fixed in this
  plan. Once a host is chosen, add its real hostname to both lists in `app/mcp_server.py` and
  add a line to `plan.md`'s Open Questions noting it's done — this plan does not create that
  line itself, since the hostname doesn't exist at plan-writing time.

---

### Task 1: Shared status-contract builder

**Files:**
- Create: `app/status.py`
- Test: `app/tests/test_status.py`

**Interfaces:**
- Consumes: a row dict shaped like one entry from `app.db.get_recent_readings()`'s return value
  (`location`, `reading_time`, `risk_tier`, `confidence`, `water_temp_c`, `sp_conductance_uscm`,
  `dissolved_oxygen_mgl`, `ph`, `turbidity_fnu`, `precip_mm`, `precip_prev_24h_mm`,
  `threshold_cfu_100ml`, `model_version`, `regime`, `retrieved_at`).
- Produces (used by Tasks 2 and 3):
  - `build_status_contract(row: dict) -> dict`

- [ ] **Step 1: Write the failing tests**

Create `app/tests/test_status.py`:

```python
"""Tests for app/status.py - the shared status-contract builder used identically by
GET /api/status and the MCP server's tools, so they can never disagree.
"""

from __future__ import annotations

from app.status import build_status_contract


def _row() -> dict:
    return {
        "id": 1,
        "location": "penns_landing",
        "reading_time": "2026-09-27T12:00:00-04:00",
        "risk_tier": "Safe",
        "confidence": 0.82,
        "water_temp_c": 20.5,
        "sp_conductance_uscm": 270.0,
        "dissolved_oxygen_mgl": 7.0,
        "ph": 7.3,
        "turbidity_fnu": 5.0,
        "precip_mm": 0.0,
        "precip_prev_24h_mm": 2.0,
        "threshold_cfu_100ml": 235,
        "model_version": "rf_B_post2021",
        "regime": "B_post2021",
        "retrieved_at": "2026-09-27T16:00:00+00:00",
    }


def test_build_status_contract_maps_top_level_fields():
    contract = build_status_contract(_row())

    assert contract["location"] == "penns_landing"
    assert contract["time"] == "2026-09-27T12:00:00-04:00"
    assert contract["risk_tier"] == "Safe"
    assert contract["confidence"] == 0.82
    assert contract["source"] == "aquasentinel"
    assert contract["source_url"] == "https://waterservices.usgs.gov/nwis/iv/?sites=01467200"
    assert contract["retrieved_at"] == "2026-09-27T16:00:00+00:00"
    assert contract["threshold_cfu_100ml"] == 235
    assert contract["model_version"] == "rf_B_post2021"
    assert contract["regime"] == "B_post2021"
    assert contract["kind"] == "model_estimate"


def test_build_status_contract_proxies_shape():
    contract = build_status_contract(_row())

    assert contract["proxies"] == {
        "water_temp_c": 20.5,
        "sp_conductance_uscm": 270.0,
        "dissolved_oxygen_mgl": 7.0,
        "ph": 7.3,
        "turbidity_fnu": 5.0,
        "precip_mm": 0.0,
        "precip_prev_24h_mm": 2.0,
    }


def test_build_status_contract_never_includes_estimate_cfu_or_location_name():
    contract = build_status_contract(_row())

    assert "estimate_cfu_100ml" not in contract
    assert "location_name" not in contract
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_status.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.status'`

- [ ] **Step 3: Write the implementation**

Create `app/status.py`:

```python
"""Shared status-contract builder - reshapes a stored reading row into the documented
contract shape, used identically by GET /api/status and the MCP server's tools (Milestone
5), so they can never disagree about what a reading looks like.

Per docs/superpowers/specs/2026-09-27-agent-ready-layer-milestone5-design.md: deliberately
excludes estimate_cfu_100ml (architecture rule: never show a bacteria value the system does
not have) and location_name (not stored per-row in app/db.py's readings table - omitted
rather than invented).
"""

from __future__ import annotations

from app.scoring.pull_reading import SOURCE_NAME, SOURCE_URL


def build_status_contract(row: dict) -> dict:
    return {
        "location": row["location"],
        "time": row["reading_time"],
        "risk_tier": row["risk_tier"],
        "confidence": row["confidence"],
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "retrieved_at": row["retrieved_at"],
        "threshold_cfu_100ml": row["threshold_cfu_100ml"],
        "model_version": row["model_version"],
        "regime": row["regime"],
        "proxies": {
            "water_temp_c": row["water_temp_c"],
            "sp_conductance_uscm": row["sp_conductance_uscm"],
            "dissolved_oxygen_mgl": row["dissolved_oxygen_mgl"],
            "ph": row["ph"],
            "turbidity_fnu": row["turbidity_fnu"],
            "precip_mm": row["precip_mm"],
            "precip_prev_24h_mm": row["precip_prev_24h_mm"],
        },
        "kind": "model_estimate",
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_status.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/status.py app/tests/test_status.py
git commit -m "feat: add shared status-contract builder for Milestone 5"
```

---

### Task 2: `GET /api/status`

**Files:**
- Modify: `app/server.py`
- Test: `app/tests/test_server.py`

**Interfaces:**
- Consumes: `app.status.build_status_contract` (Task 1), `app.db.get_recent_readings`,
  `app.scoring.pull_reading.LOCATION_ID`.
- Produces: nothing new for later tasks - this is a leaf endpoint.

- [ ] **Step 1: Write the failing tests**

Add to `app/tests/test_server.py` (append; `_client` and other fixtures already exist from
earlier milestones):

```python
def test_api_status_returns_latest_reading_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "pull_reading", lambda: _fake_reading("Safe"))
    client = _client(monkeypatch, tmp_path)
    client.post("/api/pull-reading")

    response = client.get("/api/status")

    assert response.status_code == 200
    body = response.json()
    assert body["risk_tier"] == "Safe"
    assert body["location"] == "penns_landing"
    assert "estimate_cfu_100ml" not in body
    assert "location_name" not in body


def test_api_status_returns_unavailable_when_no_readings_exist(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == {"status": "unavailable", "reason": "no readings yet"}


def test_api_status_rejects_unknown_location(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.get("/api/status", params={"location": "somewhere_else"})

    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_server.py -k api_status -v`
Expected: FAIL with 404 on all three (route doesn't exist yet)

- [ ] **Step 3: Write the implementation**

In `app/server.py`, update the import block:

```python
from app import db
from app.alerts.gating import evaluate_reading
from app.fhir import emit as fhir_emit
from app.fhir import routes as fhir_routes
from app.fhir import store as fhir_store
from app.ingestion.nws import RainfallUnavailable
from app.ingestion.usgs import UsgsDataUnavailable
from app.scoring.pull_reading import LOCATION_ID, pull_reading
from app.status import build_status_contract
```

Add the new route, right after `api_readings`:

```python
@app.get("/api/readings")
def api_readings(limit: int = 9) -> list[dict]:
    """Recent stored readings, newest first - lets the dashboard survive a page reload."""
    return db.get_recent_readings(limit=limit)


@app.get("/api/status")
def api_status(location: str = LOCATION_ID) -> dict:
    """Machine-readable status contract for the given location - the same shape MCP's
    get_current_status tool returns, built from the same app.status.build_status_contract()
    function (Milestone 5), so they can never disagree. Reads the most recently *stored*
    reading, not a fresh independent live pull - nothing computes status twice.
    """
    if location != LOCATION_ID:
        raise HTTPException(status_code=404, detail=f"Unknown location: {location}")
    rows = db.get_recent_readings(limit=1)
    if not rows:
        return {"status": "unavailable", "reason": "no readings yet"}
    return build_status_contract(rows[0])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_server.py -v`
Expected: PASS (all tests in the file, including the 3 new ones)

- [ ] **Step 5: Commit**

```bash
git add app/server.py app/tests/test_server.py
git commit -m "feat: add GET /api/status endpoint"
```

---

### Task 3: MCP server (standalone, not yet mounted)

**Files:**
- Create: `app/mcp_server.py`
- Test: `app/tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `app.status.build_status_contract` (Task 1), `app.db.get_recent_readings`,
  `app.scoring.pull_reading.LOCATION_ID`/`LOCATION_NAME`, `app.fhir.resources.LOCATION_LAT`/
  `LOCATION_LON`.
- Produces (used by Task 6):
  - `mcp: mcp.server.fastmcp.FastMCP` (the server instance, for `.streamable_http_app()` and
    `.session_manager.run()`)

**Important:** this task does **not** touch `app/server.py` at all - see Global Constraints for
why the mount is deferred to Task 6.

- [ ] **Step 1: Write the failing tests**

Create `app/tests/test_mcp_server.py`:

```python
"""Tests for app/mcp_server.py - the read-only MCP server's three tools.

Per the mcp Python SDK (v1): @mcp.tool()-decorated functions remain directly callable as
plain async functions (verified directly - the decorator registers the function with the
server as a side effect and returns the original function unchanged), so these tests call
the tools directly rather than going through the full MCP JSON-RPC protocol. The full
protocol-level integration is covered in Task 6, once the server is actually mounted.
"""

from __future__ import annotations

import asyncio

from app import db
from app import mcp_server


def _fake_reading(risk_tier: str = "Safe") -> dict:
    return {
        "location": "penns_landing",
        "location_name": "Penn's Landing, Center City tidal Delaware",
        "time": "2026-09-27T12:00:00-04:00",
        "risk_tier": risk_tier,
        "confidence": 0.82,
        "source": "aquasentinel",
        "source_url": "https://waterservices.usgs.gov/nwis/iv/?sites=01467200",
        "retrieved_at": "2026-09-27T16:00:00+00:00",
        "evidence": {
            "proxies": {
                "water_temp_c": 20.5, "sp_conductance_uscm": 270.0,
                "dissolved_oxygen_mgl": 7.0, "ph": 7.3, "turbidity_fnu": 5.0,
            },
            "proxy_timestamps": {},
            "rainfall_mm": {"precip_mm": 0.0, "precip_prev_24h_mm": 2.0},
        },
        "threshold_cfu_100ml": 235,
        "model_version": "rf_B_post2021",
        "regime": "B_post2021",
        "kind": "model_estimate",
    }


def test_list_monitored_locations_returns_the_one_known_location(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()

    locations = asyncio.run(mcp_server.list_monitored_locations())

    assert locations == [{
        "id": "penns_landing",
        "name": "Penn's Landing, Center City tidal Delaware",
        "latitude": 39.946402,
        "longitude": -75.139360,
        "gauge_id": "01467200",
    }]


def test_get_current_status_matches_a_stored_reading(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    db.insert_reading(_fake_reading("Unsafe"))

    status = asyncio.run(mcp_server.get_current_status("penns_landing"))

    assert status["risk_tier"] == "Unsafe"
    assert status["location"] == "penns_landing"


def test_get_current_status_unknown_location_is_unavailable_not_a_guess(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    db.insert_reading(_fake_reading("Safe"))

    status = asyncio.run(mcp_server.get_current_status("somewhere_else"))

    assert status == {"status": "unavailable", "reason": "Unknown location_id: somewhere_else"}


def test_get_current_status_empty_database_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()

    status = asyncio.run(mcp_server.get_current_status("penns_landing"))

    assert status == {"status": "unavailable", "reason": "no readings yet"}


def test_get_recent_readings_respects_limit_and_orders_newest_first(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    db.insert_reading(_fake_reading("Safe"))
    db.insert_reading(_fake_reading("Unsafe"))

    readings = asyncio.run(mcp_server.get_recent_readings("penns_landing", limit=1))

    assert len(readings) == 1
    assert readings[0]["risk_tier"] == "Unsafe"


def test_get_recent_readings_unknown_location_returns_empty_list(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    db.insert_reading(_fake_reading("Safe"))

    readings = asyncio.run(mcp_server.get_recent_readings("somewhere_else"))

    assert readings == []


def test_no_write_tool_is_registered():
    """Architecture rule: agents read, code decides. A closed allowlist - if this ever
    fails because a new tool was added, that new tool needs its own explicit review for
    whether it's genuinely read-only before this assertion is updated."""
    tools = asyncio.run(mcp_server.mcp.list_tools())
    tool_names = {t.name for t in tools}

    assert tool_names == {"list_monitored_locations", "get_current_status", "get_recent_readings"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_mcp_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.mcp_server'`

- [ ] **Step 3: Write the implementation**

Create `app/mcp_server.py`:

```python
"""Read-only MCP server (Milestone 5) - exposes AquaSentinel's own scored-reading data to
any MCP-compatible AI agent. No write tools: an agent can read status, never change
anything, matching the same "agents read, code decides" principle enforced everywhere else
in this codebase (see CLAUDE.md's Architecture Rules).

Uses mcp==1.30.0's FastMCP/streamable_http_app() (the v1 API - mcp 2.x renamed FastMCP to
MCPServer with a different API). This module only builds the `mcp` server instance and
registers its tools - it is NOT mounted into app/server.py here. See
docs/superpowers/specs/2026-09-27-agent-ready-layer-milestone5-design.md's "MCP integration
specifics" for the mount-path/lifespan/transport-security gotchas that wiring requires, and
this plan's Task 6 for where the actual mount happens (and why it must be last).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app import db
from app.fhir.resources import LOCATION_LAT, LOCATION_LON
from app.scoring.pull_reading import LOCATION_ID, LOCATION_NAME
from app.status import build_status_contract

# Same USGS gauge id already embedded in pull_reading.SOURCE_URL - not a second source of
# truth, just not currently its own named constant there.
GAUGE_ID = "01467200"

mcp = FastMCP(
    "aquasentinel_mcp",
    transport_security=TransportSecuritySettings(
        allowed_hosts=["testserver", "localhost", "localhost:8000", "127.0.0.1:8000"],
        allowed_origins=[
            "http://testserver", "http://localhost:8000", "http://127.0.0.1:8000",
        ],
    ),
)


@mcp.tool()
async def list_monitored_locations() -> list[dict]:
    """List every location AquaSentinel currently monitors.

    Read-only. Returns the one location this build covers today (Penn's Landing) - call
    this before get_current_status or get_recent_readings to find a valid location_id.

    Returns:
        A list of one object per monitored location:
        {"id": str, "name": str, "latitude": float, "longitude": float, "gauge_id": str}
    """
    return [{
        "id": LOCATION_ID,
        "name": LOCATION_NAME,
        "latitude": LOCATION_LAT,
        "longitude": LOCATION_LON,
        "gauge_id": GAUGE_ID,
    }]


@mcp.tool()
async def get_current_status(location_id: str) -> dict:
    """Get the current water-safety status for one monitored location.

    Read-only. Returns AquaSentinel's most recently stored scored reading - an estimate,
    not a measured bacteria value. The same contract shape GET /api/status and the
    dashboard banner use, so they can never disagree.

    Args:
        location_id: A location id from list_monitored_locations (e.g. "penns_landing").

    Returns:
        On success: {"location": str, "time": str, "risk_tier": "Safe"|"Unsafe",
        "confidence": float, "source": str, "source_url": str, "retrieved_at": str,
        "threshold_cfu_100ml": int, "model_version": str, "regime": str,
        "proxies": {...}, "kind": "model_estimate"}
        On an unknown location or no reading yet: {"status": "unavailable", "reason": str}
    """
    if location_id != LOCATION_ID:
        return {"status": "unavailable", "reason": f"Unknown location_id: {location_id}"}
    rows = db.get_recent_readings(limit=1)
    if not rows:
        return {"status": "unavailable", "reason": "no readings yet"}
    return build_status_contract(rows[0])


@mcp.tool()
async def get_recent_readings(location_id: str, limit: int = 9) -> list[dict]:
    """Get recent scored readings for one monitored location, newest first.

    Read-only. Each entry is the same status-contract shape get_current_status returns.

    Args:
        location_id: A location id from list_monitored_locations (e.g. "penns_landing").
        limit: Maximum number of readings to return (default 9).

    Returns:
        A list of status-contract dicts, newest first. Empty if the location is unknown or
        nothing has been stored yet.
    """
    if location_id != LOCATION_ID:
        return []
    rows = db.get_recent_readings(limit=limit)
    return [build_status_contract(row) for row in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_mcp_server.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add app/mcp_server.py app/tests/test_mcp_server.py
git commit -m "feat: add read-only MCP server (not yet mounted)"
```

---

### Task 4: `GET /llms.txt`

**Files:**
- Create: `docs/landing-page/llms.txt`
- Modify: `app/server.py`
- Test: `app/tests/test_server.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new for later tasks.

- [ ] **Step 1: Write the failing test**

Add to `app/tests/test_server.py`:

```python
def test_llms_txt_is_served_with_honesty_language():
    client = TestClient(server.app)

    response = client.get("/llms.txt")

    assert response.status_code == 200
    assert "AquaSentinel" in response.text
    assert "estimate" in response.text
    assert "predict illness" not in response.text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest app/tests/test_server.py -k llms_txt -v`
Expected: FAIL with 404 (route doesn't exist yet)

- [ ] **Step 3: Write the implementation**

Create `docs/landing-page/llms.txt`:

```
# AquaSentinel

> A virtual water-quality sensor estimating E. coli risk (Safe/Unsafe) for the Center City
> tidal Delaware from live USGS gauge and weather data. This is an estimate, not a measured
> bacteria value - language is "estimate" and "flag elevated risk," never "predict illness."

AquaSentinel publishes its live scored reading through three agent-facing surfaces, all
generated from the same scoring output so they cannot disagree with each other or the
human-facing dashboard:

- [MCP server](/mcp) - read-only tools: list_monitored_locations, get_current_status,
  get_recent_readings. No write tools exist - an agent can read status, never change
  anything.
- [Status endpoint](/api/status) - the same data as a plain JSON GET, no MCP client needed.
- [Methodology](TODO_GITHUB_REPO_URL/blob/main/docs/product-brief.md) - the full design:
  data sources, model, alert rules, and honesty guardrails.

Every reading is an estimate from live proxies (turbidity, temperature, conductance,
dissolved oxygen, rainfall), scored against the EPA 235 CFU/100mL single-sample threshold -
never a measured bacteria value.
```

Note the `TODO_GITHUB_REPO_URL` placeholder — the actual public repo URL doesn't exist yet
(not published). Leave it as this exact, clearly-marked placeholder; do not invent a URL.
Update it once the repo is public, before submission.

In `app/server.py`, add the route right after `logo`:

```python
@app.get("/logo.png")
def logo() -> FileResponse:
    return FileResponse(LANDING_PAGE_DIR / "logo.png")


@app.get("/llms.txt")
def llms_txt() -> FileResponse:
    return FileResponse(LANDING_PAGE_DIR / "llms.txt", media_type="text/markdown")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest app/tests/test_server.py -k llms_txt -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add docs/landing-page/llms.txt app/server.py app/tests/test_server.py
git commit -m "feat: add GET /llms.txt"
```

---

### Task 5: JSON-LD on the dashboard

**Files:**
- Modify: `docs/landing-page/index.html`
- Modify: `app/server.py`
- Test: `app/tests/test_server.py`

**Interfaces:**
- Consumes: `app.db.get_recent_readings`.
- Produces: nothing new for later tasks.

- [ ] **Step 1: Write the failing tests**

Add to `app/tests/test_server.py`:

```python
def test_index_page_contains_substituted_json_ld(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "pull_reading", lambda: _fake_reading("Safe"))
    client = _client(monkeypatch, tmp_path)
    client.post("/api/pull-reading")

    response = client.get("/")

    assert response.status_code == 200
    assert '"@type": "Dataset"' in response.text
    assert "__AQUASENTINEL_DATASET_DATE_MODIFIED__" not in response.text
    assert "2026-09-25T22:09:47+00:00" in response.text  # _fake_reading's retrieved_at


def test_index_page_json_ld_falls_back_to_server_time_when_no_readings(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    client = TestClient(server.app)

    response = client.get("/")

    assert response.status_code == 200
    assert "__AQUASENTINEL_DATASET_DATE_MODIFIED__" not in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_server.py -k json_ld -v`
Expected: FAIL (no JSON-LD block exists in the page yet, and `index()` still returns
`FileResponse`, not a substituted page)

- [ ] **Step 3: Write the implementation**

In `docs/landing-page/index.html`, add the JSON-LD block right after the existing HTML
comment block (before the Google Fonts `<link>`):

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Dataset",
  "name": "AquaSentinel Water-Safety Estimates — Center City Tidal Delaware",
  "description": "Live E. coli risk estimates (Safe/Unsafe) for the Center City tidal Delaware, derived from live USGS gauge proxies and near-real-time rainfall. An estimate, not a measured bacteria value.",
  "variableMeasured": ["risk_tier", "confidence", "water_temp_c", "sp_conductance_uscm", "dissolved_oxygen_mgl", "ph", "turbidity_fnu", "precip_mm", "precip_prev_24h_mm"],
  "spatialCoverage": {
    "@type": "Place",
    "name": "Penn's Landing, Center City tidal Delaware",
    "geo": {"@type": "GeoCoordinates", "latitude": 39.946402, "longitude": -75.139360}
  },
  "dateModified": "__AQUASENTINEL_DATASET_DATE_MODIFIED__",
  "isBasedOn": [
    "https://waterservices.usgs.gov/nwis/iv/?sites=01467200",
    "https://api.weather.gov/stations/KPHL/observations"
  ],
  "creator": {"@type": "Organization", "name": "AquaSentinel"}
}
</script>
```

In `app/server.py`, update imports:

```python
from datetime import datetime, timezone
```

(add alongside the existing `from contextlib import asynccontextmanager` / `from pathlib
import Path` import lines)

```python
from fastapi.responses import FileResponse, HTMLResponse
```

(replace the existing `from fastapi.responses import FileResponse` line)

Add the token constant near `LANDING_PAGE_DIR`:

```python
LANDING_PAGE_DIR = Path(__file__).resolve().parents[1] / "docs" / "landing-page"
DATASET_DATE_MODIFIED_TOKEN = "__AQUASENTINEL_DATASET_DATE_MODIFIED__"
```

Replace the `index()` route:

```python
@app.get("/")
def index() -> HTMLResponse:
    html = (LANDING_PAGE_DIR / "index.html").read_text()
    rows = db.get_recent_readings(limit=1)
    date_modified = rows[0]["retrieved_at"] if rows else datetime.now(timezone.utc).isoformat()
    html = html.replace(DATASET_DATE_MODIFIED_TOKEN, date_modified)
    return HTMLResponse(html)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_server.py -v`
Expected: PASS (all tests in the file, including the 2 new ones)

- [ ] **Step 5: Commit**

```bash
git add docs/landing-page/index.html app/server.py app/tests/test_server.py
git commit -m "feat: add JSON-LD dataset metadata to the dashboard"
```

---

### Task 6: Mount the MCP server (must be last)

**Files:**
- Modify: `app/server.py`
- Test: `app/tests/test_server.py`

**Interfaces:**
- Consumes: `app.mcp_server.mcp` (Task 3).
- Produces: nothing new - this is the final wiring task.

- [ ] **Step 1: Write the failing tests**

Add to `app/tests/test_server.py`:

```python
def test_mcp_endpoint_responds_to_initialize_and_tools_list():
    client = TestClient(server.app)

    init_response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1"},
            },
        },
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert init_response.status_code == 200
    session_id = init_response.headers["mcp-session-id"]

    tools_response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers={
            "Accept": "application/json, text/event-stream",
            "mcp-session-id": session_id,
        },
    )
    assert tools_response.status_code == 200
    assert "get_current_status" in tools_response.text


def test_preexisting_routes_still_work_after_the_mcp_mount(monkeypatch, tmp_path):
    """Review Focus: a route registered after app.mount("/", ...) would be silently
    shadowed (404, no exception) - this pins every pre-existing route as a regression
    guard against that exact failure mode being reintroduced later."""
    monkeypatch.setattr(server, "pull_reading", lambda: _fake_reading("Safe"))
    client = _client(monkeypatch, tmp_path)

    assert client.get("/").status_code == 200
    assert client.get("/logo.png").status_code == 200
    assert client.get("/llms.txt").status_code == 200
    assert client.get("/api/status").status_code == 200
    assert client.post("/api/pull-reading").status_code == 200
    assert client.get("/api/readings").status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_server.py -k "mcp_endpoint or preexisting_routes" -v`
Expected: FAIL — `/mcp` doesn't exist yet (404 on `test_mcp_endpoint_...`); the regression
guard test should already pass at this point (nothing's mounted yet to shadow anything) -
that's expected, it becomes meaningful once Step 3 adds the mount.

- [ ] **Step 3: Write the implementation**

In `app/server.py`, add the import:

```python
from app import mcp_server
```

Update the `lifespan` function to enter the MCP session manager's context — this is required
(see Global Constraints and the spec's "MCP integration specifics"): Starlette does not
propagate a mounted sub-app's own lifespan to its parent automatically.

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    fhir_store.init_db()
    async with mcp_server.mcp.session_manager.run():
        yield
```

At the very end of the file, after every other route definition:

```python
# Must be the LAST route registration in this file - a route added after this would be
# silently shadowed (404, no exception raised). See this plan's Global Constraints and
# test_preexisting_routes_still_work_after_the_mcp_mount for why.
app.mount("/", mcp_server.mcp.streamable_http_app())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_server.py -v`
Expected: PASS (every test in the file)

- [ ] **Step 5: Run the full suite**

Run: `./venv/bin/python -m pytest app/tests/ -v`
Expected: PASS (every test in the project)

- [ ] **Step 6: Commit**

```bash
git add app/server.py app/tests/test_server.py
git commit -m "feat: mount the MCP server into app/server.py"
```

---

### Task 7: Consistency test

**Files:**
- Create: `app/tests/test_consistency.py`

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: nothing new - this is the milestone's explicit Definition of Done.

- [ ] **Step 1: Write the failing test**

Create `app/tests/test_consistency.py`:

```python
"""Consistency test - Milestone 5's explicit Definition of Done (plan.md): MCP,
/api/status, and the dashboard's data source (GET /api/readings) must agree on tier and
timestamp for the same reading. One scoring output, read three ways, never computed twice.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app import db, mcp_server, server


def _fake_reading(risk_tier: str = "Unsafe") -> dict:
    return {
        "location": "penns_landing",
        "location_name": "Penn's Landing, Center City tidal Delaware",
        "time": "2026-09-27T12:00:00-04:00",
        "risk_tier": risk_tier,
        "confidence": 0.91,
        "source": "aquasentinel",
        "source_url": "https://waterservices.usgs.gov/nwis/iv/?sites=01467200",
        "retrieved_at": "2026-09-27T16:00:00+00:00",
        "evidence": {
            "proxies": {
                "water_temp_c": 24.0, "sp_conductance_uscm": 300.0,
                "dissolved_oxygen_mgl": 4.5, "ph": 7.0, "turbidity_fnu": 40.0,
            },
            "proxy_timestamps": {},
            "rainfall_mm": {"precip_mm": 5.0, "precip_prev_24h_mm": 30.0},
        },
        "threshold_cfu_100ml": 235,
        "model_version": "rf_B_post2021",
        "regime": "B_post2021",
        "kind": "model_estimate",
    }


def test_mcp_status_and_readings_agree_on_tier_and_timestamp(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(server, "pull_reading", lambda: _fake_reading("Unsafe"))
    db.init_db()

    client = TestClient(server.app)
    pulled = client.post("/api/pull-reading")
    assert pulled.status_code == 200

    # (a) what the dashboard banner renders from
    readings = db.get_recent_readings(limit=1)
    assert readings[0]["risk_tier"] == "Unsafe"
    banner_time = readings[0]["reading_time"]

    # (b) /api/status
    status_response = client.get("/api/status")
    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["risk_tier"] == "Unsafe"
    assert status_body["time"] == banner_time

    # (c) the MCP tool - called directly per Task 3's note on why that's valid
    mcp_result = asyncio.run(mcp_server.get_current_status("penns_landing"))
    assert mcp_result["risk_tier"] == "Unsafe"
    assert mcp_result["time"] == banner_time
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest app/tests/test_consistency.py -v`
Expected: FAIL only if an earlier task is incomplete. If Tasks 1-6 are all done, this may
already PASS on the first run, since it only exercises already-implemented code - in that
case, treat that consciously as confirmation, not a mistake (same situation as Milestone 4's
Task 7).

- [ ] **Step 3: No new implementation code needed**

This task is verification-only. If it fails, the fix belongs in whichever earlier task's
file is actually wrong - check the failure message against Tasks 1-6.

- [ ] **Step 4: Run the full test suite one final time**

Run: `./venv/bin/python -m pytest app/tests/ -v`
Expected: PASS (every test in the project, Milestones 1 through 5)

- [ ] **Step 5: Manually verify against the real dashboard**

Run `./venv/bin/uvicorn app.server:app --reload`, open `http://localhost:8000` in a browser,
confirm no console errors. Then check `http://localhost:8000/api/status`,
`http://localhost:8000/llms.txt`, and view the page source to confirm the JSON-LD block has
a real `dateModified`, not the placeholder token. An MCP client (or `curl` with the
`initialize`/`tools/list` JSON-RPC payloads from Task 6's test) against
`http://localhost:8000/mcp` should list all three tools.

- [ ] **Step 6: Commit**

```bash
git add app/tests/test_consistency.py
git commit -m "test: add end-to-end consistency verification for Milestone 5"
```
