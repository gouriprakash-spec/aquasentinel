# Advisory Reader Agent (Milestone 7) Implementation Plan

Status: not built (cut 2026-10-02; see `docs/product-brief.md`'s Future directions). Kept for later.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a component that *reads* an agent-ready source via a real MCP client session
(not an in-process shortcut) and normalizes the result into a scale-agnostic envelope —
AquaSentinel's own MCP server, registered as the one source, proving the pattern is real.

**Architecture:** A new `app/reading/` subpackage (matching the existing per-concern layout):
a hardcoded one-entry source registry, a reader that opens a genuine MCP `streamable_http`
session against a registry entry's `mcp_url` and calls two of its tools, and one new read-only
`GET` route in `app/server.py` that triggers a read and returns the envelope. Nothing is
persisted; every read is live and ephemeral; nothing here feeds AquaSentinel's own alert
pipeline.

**Tech Stack:** Python 3.11, the already-installed `mcp==1.30.0` SDK's client-side API
(`mcp.client.streamable_http.streamable_http_client`, `mcp.ClientSession`), httpx, FastAPI.

**Spec:** `docs/superpowers/specs/2026-09-27-advisory-reader-agent-design.md` (amended
2026-10-01 — see its Step 5 correction: the spec's own parsing description was verified
live during this plan's authoring and found incomplete for the list-returning tool; the
amendment is carried forward into Task 2 below).

## Global Constraints

- **Native mode only.** No HTML-scraping/legacy mode, no LLM call anywhere in this path — a
  deterministic MCP client call, full stop.
- **Nothing is persisted.** Every call to the new endpoint is a live, ephemeral read-through;
  no database write, no caching.
- **Never feeds AquaSentinel's own alert/gating pipeline.** This component only reads; it has
  no path into `app/alerts/gating.py` or anything that decides a tier.
- **`native_rating` is scale-agnostic and copied verbatim**, never remapped onto
  AquaSentinel's own Safe/Unsafe vocabulary as if it were a universal scale — even though
  today's one source happens to use that exact vocabulary.
- **`GET`, not `POST`**, for the new route — this has no side effects, unlike
  `POST /api/pull-reading`.
- **The new route must be registered in `app/server.py` BEFORE the existing
  `app.mount("/", mcp_server.mcp.streamable_http_app())` line**, which must stay the last
  line in the file. A route added after that mount is silently shadowed (404, no exception) —
  this is already documented at that exact line in the file from Milestone 5.
- **Error handling, fail closed, no fabricated rating:**
  | Situation | Response |
  |---|---|
  | Unknown `source` key | `404` |
  | Source reachable, its own answer is `{"status": "unavailable", ...}` | `200`, passed through faithfully as `native_status`, `native_rating: null` — an honest answer, not an error |
  | Unreachable, handshake fails, times out, or unparseable | `503` |
- **No new environment variable.** `AQUASENTINEL_BASE_URL` (already in `.env.example` since
  Milestone 4, read the same way `app/rphsa_stub.py:28` already reads it —
  `os.environ.get("AQUASENTINEL_BASE_URL", "http://localhost:8000")`) is the registry's only
  source URL.
- Indentation: 4 spaces (Python). Naming: snake_case. Comments explain the WHY, not the WHAT.
- This codebase prefers real integration tests over mocks (see `test_fhir_end_to_end.py`,
  Milestone 5's `test_mcp_server.py`) — follow that here: real MCP protocol exercised via
  `httpx.ASGITransport(app=server.app)`, with the app's `lifespan` driven manually (see Task 2).
- **No `pytest-asyncio`/`anyio` plugin is installed in this project.** Every existing async
  test in this codebase (`test_mcp_server.py`, `test_consistency.py`) is a plain `def test_...`
  that calls `asyncio.run(...)` internally — follow that exact convention, never `async def
  test_...`.

## Review Focus

1. **A list-returning MCP tool's `content[0].text` is NOT the whole list.** Verified live
   during this plan's authoring (see the spec's 2026-10-01 amendment): FastMCP gives
   `list_monitored_locations()` a `structuredContent = {"result": [...]}` with the real list,
   while `content[0].text` is only the first location's JSON object with no enclosing array. A
   naive `json.loads(content[0].text)` would silently "work" today (one location registered)
   and break the moment a second exists. Pinned in Task 2.
2. **An MCP tool call that itself reports `isError: True`** (a protocol-level tool failure,
   distinct from a connection failure) must be treated as `SourceUnreachable`, not parsed as if
   its content were a valid payload. Pinned in Task 2.
3. **The new route must come before the MCP mount, or it's silently shadowed** — a real,
   already-documented gotcha in this exact file, easy to get backwards by habit (most new
   routes in this file were added in the natural reading order, near the top). Pinned in Task 3.
4. **A caller-supplied `httpx.AsyncClient` must not be closed by `read_source()` itself** —
   `streamable_http_client` only manages the lifecycle of a client it created internally
   (`http_client=None`); closing or leaking a caller-provided one would be a real, easy-to-miss
   resource-management bug, and this is the first place in the codebase doing this kind of
   nested-async-context-manager session work. Pinned in Task 2 by reusing the same client
   across two consecutive calls — a premature close would fail the second one.
5. **"Source's own answer is unavailable" must produce `200`, not `503`.** A syntactically
   valid read of a data-sparse-but-honest response is not a failure of the read itself — easy
   to instinctively (and wrongly) turn into an error path. Pinned in Task 2.

---

### Task 1: `app/reading/sources.py` — the sources registry

**Files:**
- Create: `app/reading/__init__.py` (empty, just makes it a package)
- Create: `app/reading/sources.py`
- Test: `app/tests/test_reading_sources.py`

**Interfaces:**
- Produces: `SOURCES: dict[str, dict]` with one key `"aquasentinel"`, each value shaped
  `{"name": str, "mcp_url": str}` — Task 2 imports this.

- [ ] **Step 1: Write the failing test**

Create `app/tests/test_reading_sources.py`:

```python
"""Tests for app/reading/sources.py - the one-entry source registry.

Milestone 7: proves the registry pattern is real without building a second source. See
docs/superpowers/specs/2026-09-27-advisory-reader-agent-design.md's Scope section.
"""

from __future__ import annotations

from app.reading.sources import SOURCES


def test_aquasentinel_is_registered():
    assert "aquasentinel" in SOURCES
    entry = SOURCES["aquasentinel"]
    assert entry["name"] == "AquaSentinel"
    assert entry["mcp_url"].endswith("/mcp")


def test_mcp_url_defaults_to_localhost_8000(monkeypatch):
    """No AQUASENTINEL_BASE_URL set -> the same localhost:8000 default app/rphsa_stub.py
    already uses - reused, not reinvented."""
    import importlib

    monkeypatch.delenv("AQUASENTINEL_BASE_URL", raising=False)
    import app.reading.sources as sources_module

    importlib.reload(sources_module)

    assert sources_module.SOURCES["aquasentinel"]["mcp_url"] == "http://localhost:8000/mcp"


def test_mcp_url_honors_aquasentinel_base_url_env_var(monkeypatch):
    import importlib

    monkeypatch.setenv("AQUASENTINEL_BASE_URL", "http://example.test:9000")
    import app.reading.sources as sources_module

    importlib.reload(sources_module)

    assert sources_module.SOURCES["aquasentinel"]["mcp_url"] == "http://example.test:9000/mcp"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest app/tests/test_reading_sources.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.reading'`.

- [ ] **Step 3: Write the implementation**

Create `app/reading/__init__.py` (empty file).

Create `app/reading/sources.py`:

```python
"""Registry of agent-ready sources the Reader Agent can read from.

One entry today - AquaSentinel itself, proving the registry pattern is real without
building a second source (see docs/superpowers/specs/2026-09-27-advisory-reader-agent-
design.md's Scope section: a genuinely external second source is future work).
"""

from __future__ import annotations

import os

# Same default and same env var app/rphsa_stub.py already reads ("where AquaSentinel itself
# lives") - one source of truth, not a second config knob.
AQUASENTINEL_BASE_URL = os.environ.get("AQUASENTINEL_BASE_URL", "http://localhost:8000")

SOURCES: dict[str, dict] = {
    "aquasentinel": {
        "name": "AquaSentinel",
        "mcp_url": f"{AQUASENTINEL_BASE_URL}/mcp",
    },
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest app/tests/test_reading_sources.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/reading/__init__.py app/reading/sources.py app/tests/test_reading_sources.py
git commit -m "feat: add the one-entry agent-ready sources registry"
```

---

### Task 2: `app/reading/agent.py` — the real MCP client reader

**Files:**
- Create: `app/reading/agent.py`
- Test: `app/tests/test_reading_agent.py`

**Interfaces:**
- Consumes: `app.reading.sources.SOURCES` (Task 1).
- Produces: `UnknownSource` (exception class), `SourceUnreachable` (exception class),
  `async def read_source(source_id: str, location_id: str | None = None, http_client:
  httpx.AsyncClient | None = None) -> dict` returning the normalized envelope shape (`source`,
  `source_name`, `source_url`, `retrieved_at`, `location_id`, `native_rating`,
  `native_status`) — Task 3 imports all three.

This is the one task in this plan that isn't pure transcription: the MCP client-session
mechanics and the `structuredContent`-vs-`content` parsing split were independently verified
live against this exact codebase during this plan's authoring (not from SDK docs alone — see
the spec's 2026-10-01 amendment). The code below is that verified result; read it carefully
rather than re-deriving it from scratch, but do confirm it still behaves as described when you
run it, since "verified once, by someone else, earlier" is not the same as "verified by you,
now."

- [ ] **Step 1: Write the failing tests**

Create `app/tests/test_reading_agent.py`:

```python
"""Tests for app/reading/agent.py - a real MCP client session against a registered source.

Follows this codebase's existing preference for real integration tests over mocks: every
test here exercises the actual MCP protocol (real session negotiation, real tool calls) via
httpx.ASGITransport against the real app, with no live server process and nothing about the
MCP layer mocked. See docs/superpowers/specs/2026-09-27-advisory-reader-agent-design.md's
Testing plan.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app import db, server
from app.fhir import store as fhir_store
from app.reading.agent import SourceUnreachable, UnknownSource, read_source
from app.reading.sources import SOURCES


def _run_read_source(**kwargs):
    """Drive the app's real lifespan (starts the MCP session manager - see
    app/server.py:36-40) and a real in-process httpx.AsyncClient for one read_source() call.
    DB isolation is the CALLER's job (monkeypatch both db.DB_PATH and fhir_store.DB_PATH
    before calling this - same two-patch requirement test_consistency.py already documents),
    since server.py's lifespan calls both modules' init_db() and this helper has no fixture
    access of its own to do it safely with auto-teardown.
    """
    async def _inner():
        async with server.app.router.lifespan_context(server.app):
            transport = httpx.ASGITransport(app=server.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
                return await read_source(http_client=http_client, **kwargs)

    return asyncio.run(_inner())


def _isolate_db(monkeypatch, db_path):
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(fhir_store, "DB_PATH", db_path)
    db.init_db(db_path=db_path)
    fhir_store.init_db(db_path=db_path)


def test_reads_a_fresh_aquasentinel_reading_end_to_end(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    _isolate_db(monkeypatch, db_path)
    db.insert_reading({
        "location": "penns_landing",
        "time": "2026-10-01T12:00:00+00:00",
        "risk_tier": "Safe",
        "confidence": 0.9,
        "retrieved_at": "2026-10-01T12:00:05+00:00",
        "threshold_cfu_100ml": 235,
        "model_version": "rf_nearshore",
        "regime": "nearshore",
        "evidence": {
            "proxies": {
                "water_temp_c": 21.0, "sp_conductance_uscm": 260.0,
                "dissolved_oxygen_mgl": 7.0, "ph": 7.2, "turbidity_fnu": 5.0,
            },
            "rainfall_mm": {"precip_mm": 0.0, "precip_prev_24h_mm": 0.0, "precip_prev_48h_mm": 1.0},
            "rainfall_source": "nws",
            "decision_basis": "rainfall_rule",
            "rule_threshold_mm": 2.5,
            "model_probability_unsafe": 0.1,
        },
    }, db_path=db_path)

    envelope = _run_read_source(source_id="aquasentinel", location_id="penns_landing")

    assert envelope["source"] == "aquasentinel"
    assert envelope["source_name"] == "AquaSentinel"
    assert envelope["source_url"] == SOURCES["aquasentinel"]["mcp_url"]
    assert envelope["location_id"] == "penns_landing"
    assert envelope["native_rating"] == "Safe"
    assert envelope["native_status"]["risk_tier"] == "Safe"
    assert envelope["native_status"]["decision_basis"] == "rainfall_rule"
    assert "retrieved_at" in envelope


def test_passes_through_an_unavailable_native_status_honestly(tmp_path, monkeypatch):
    """Review Focus #5: no stored reading at all -> AquaSentinel's own tool returns
    {"status": "unavailable", ...} - this must come back as a normal envelope with
    native_rating: null, NOT raise SourceUnreachable."""
    db_path = tmp_path / "test.db"
    _isolate_db(monkeypatch, db_path)

    envelope = _run_read_source(source_id="aquasentinel", location_id="penns_landing")

    assert envelope["native_rating"] is None
    assert envelope["native_status"]["status"] == "unavailable"


def test_falls_back_to_the_first_listed_location_when_none_given(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    _isolate_db(monkeypatch, db_path)

    envelope = _run_read_source(source_id="aquasentinel")

    assert envelope["location_id"] == "penns_landing"


def test_a_caller_supplied_http_client_survives_the_call_and_is_reusable(tmp_path, monkeypatch):
    """Review Focus #4: streamable_http_client only closes a client it created itself
    (http_client=None); a caller-supplied one must be left open and usable afterward. Proven
    by reusing the SAME client across two consecutive read_source() calls in one `async with`
    block - if read_source() had closed it, the second call would fail with httpx's "client
    has been closed" RuntimeError."""
    db_path = tmp_path / "test.db"
    _isolate_db(monkeypatch, db_path)

    async def _inner():
        async with server.app.router.lifespan_context(server.app):
            transport = httpx.ASGITransport(app=server.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
                first = await read_source(
                    "aquasentinel", location_id="penns_landing", http_client=http_client
                )
                second = await read_source(
                    "aquasentinel", location_id="penns_landing", http_client=http_client
                )
                return first, second

    first, second = asyncio.run(_inner())
    assert first["location_id"] == second["location_id"] == "penns_landing"


def test_unknown_source_raises_unknown_source(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    _isolate_db(monkeypatch, db_path)

    with pytest.raises(UnknownSource):
        _run_read_source(source_id="not_a_real_source")


def test_a_tool_level_error_raises_source_unreachable(tmp_path, monkeypatch):
    """Review Focus #2, verified live during this plan's authoring: calling an unknown tool
    name (or a real tool with a missing required argument) does NOT raise an exception from
    session.call_tool() - it returns a normal CallToolResult with isError=True and an error
    message in content[0].text. _call_tool_parsed must treat that as a failure, not parse
    content[0].text as if it were a valid JSON payload."""
    from app.reading import agent as agent_module

    db_path = tmp_path / "test.db"
    _isolate_db(monkeypatch, db_path)

    async def _inner():
        async with server.app.router.lifespan_context(server.app):
            transport = httpx.ASGITransport(app=server.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
                from mcp import ClientSession
                from mcp.client.streamable_http import streamable_http_client

                async with streamable_http_client(
                    SOURCES["aquasentinel"]["mcp_url"], http_client=http_client
                ) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        await agent_module._call_tool_parsed(session, "not_a_real_tool", {})

    with pytest.raises(SourceUnreachable):
        asyncio.run(_inner())


def test_unreachable_source_raises_source_unreachable(monkeypatch):
    """A real, genuinely unreachable network address (port 1 is reserved and will refuse
    the connection) - not mocked, matching this codebase's real-over-mocked test preference.
    No DB isolation needed: the call fails before anything reads the registered reading.
    """
    monkeypatch.setitem(SOURCES, "broken", {"name": "Broken", "mcp_url": "http://127.0.0.1:1/mcp"})

    async def _inner():
        return await read_source("broken")

    with pytest.raises(SourceUnreachable):
        asyncio.run(_inner())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_reading_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.reading.agent'`.

- [ ] **Step 3: Write the implementation**

Create `app/reading/agent.py`:

```python
"""Read an agent-ready source via a real MCP client session - native mode only (Milestone 7).

Opens a genuine mcp.ClientSession against a registered source's mcp_url (not an in-process
shortcut), calls list_monitored_locations and get_current_status, and normalizes the result
into a scale-agnostic envelope. See docs/superpowers/specs/2026-09-27-advisory-reader-agent-
design.md for the full design and its 2026-10-01 amendment on result parsing.

No persistence: every call is a live, ephemeral read-through. Never feeds AquaSentinel's own
alert/gating pipeline - this component only reads.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.reading.sources import SOURCES


class UnknownSource(RuntimeError):
    """Raised when source_id isn't in the registry - fail closed, don't guess."""


class SourceUnreachable(RuntimeError):
    """Raised on any MCP connection failure, protocol error, timeout, tool-level error, or
    unparseable response - fail closed, never a fabricated rating."""


async def read_source(
    source_id: str,
    location_id: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict:
    """Return the normalized envelope for one live read of a registered source.

    http_client is None in production (streamable_http_client builds and owns a real network
    client against mcp_url, closing it itself). Tests inject one wired to
    httpx.ASGITransport(app=server.app) - streamable_http_client never closes a
    caller-provided client, so ownership stays with the caller in that case (Review Focus #4).
    """
    if source_id not in SOURCES:
        raise UnknownSource(f"Unknown source: {source_id}")
    entry = SOURCES[source_id]
    mcp_url = entry["mcp_url"]

    try:
        async with streamable_http_client(mcp_url, http_client=http_client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                if location_id is None:
                    locations = await _call_tool_parsed(session, "list_monitored_locations", {})
                    location_id = locations[0]["id"]

                status = await _call_tool_parsed(
                    session, "get_current_status", {"location_id": location_id}
                )
    except UnknownSource:
        raise
    except Exception as exc:
        raise SourceUnreachable(f"Could not read source {source_id!r}: {exc}") from exc

    native_rating = None if status.get("status") == "unavailable" else status.get("risk_tier")

    return {
        "source": source_id,
        "source_name": entry["name"],
        "source_url": mcp_url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "location_id": location_id,
        "native_rating": native_rating,
        "native_status": status,
    }


async def _call_tool_parsed(session: ClientSession, name: str, arguments: dict) -> Any:
    """Call one MCP tool and return its parsed payload.

    Review Focus #1: a list-returning tool (list_monitored_locations) gets a
    structuredContent = {"result": [...]} with the real list; content[0].text for that same
    tool is only the FIRST list item's JSON, not the whole list - verified live 2026-10-01,
    see the spec's amendment. A dict-returning tool (get_current_status) gets no
    structuredContent at all; its payload is content[0].text. Handle both: prefer
    structuredContent when present (unwrapping the {"result": ...} envelope a list return
    produces), fall back to content[0].text otherwise.

    Review Focus #2: isError is a protocol-level tool failure (distinct from a connection
    failure) - must raise SourceUnreachable, not be parsed as if content were a valid payload.
    """
    result = await session.call_tool(name, arguments)
    if result.isError:
        detail = result.content[0].text if result.content else "no error detail"
        raise SourceUnreachable(f"{name} returned an error: {detail}")

    if result.structuredContent is not None:
        if set(result.structuredContent.keys()) == {"result"}:
            return result.structuredContent["result"]
        return result.structuredContent

    return json.loads(result.content[0].text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_reading_agent.py -v`
Expected: PASS (7 passed). This exercises a real MCP handshake end to end — if it hangs
instead of completing within a few seconds, something in the lifespan/session-manager wiring
is wrong; don't let it hang, interrupt and re-check the `async with server.app.router.
lifespan_context(server.app):` nesting against Task 2's `_run_read_source` helper above.

- [ ] **Step 5: Commit**

```bash
git add app/reading/agent.py app/tests/test_reading_agent.py
git commit -m "feat: add the real-MCP-client reader agent"
```

---

### Task 3: `app/server.py` — the `GET /api/reader-agent/read` route

**Files:**
- Modify: `app/server.py`
- Test: `app/tests/test_server.py`

**Interfaces:**
- Consumes: `app.reading.agent.read_source`, `UnknownSource`, `SourceUnreachable` (Task 2).

- [ ] **Step 1: Write the failing tests**

In `app/tests/test_server.py`, add these tests at the end of the file (check the file's
existing imports first — it already imports `from fastapi.testclient import TestClient`,
`from app import db, server`, and `from app.fhir import store as fhir_store`; reuse those,
don't re-import):

```python
def _isolate_db(monkeypatch, db_path):
    """Matches this file's own existing convention (see e.g. its api_status fixture setup
    around line 52): TestClient(server.app) runs the real lifespan, which initializes BOTH
    db and fhir_store - patch both or a test silently touches the real project database."""
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(fhir_store, "DB_PATH", db_path)
    db.init_db(db_path=db_path)
    fhir_store.init_db(db_path=db_path)


def test_reader_agent_read_returns_the_normalized_envelope(monkeypatch, tmp_path):
    _isolate_db(monkeypatch, tmp_path / "test.db")

    client = TestClient(server.app)
    response = client.get("/api/reader-agent/read?source=aquasentinel&location_id=penns_landing")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "aquasentinel"
    assert body["location_id"] == "penns_landing"
    assert "native_status" in body


def test_reader_agent_read_404s_on_an_unknown_source(monkeypatch, tmp_path):
    _isolate_db(monkeypatch, tmp_path / "test.db")

    client = TestClient(server.app)
    response = client.get("/api/reader-agent/read?source=not_a_real_source")

    assert response.status_code == 404


def test_reader_agent_read_503s_on_an_unreachable_source(monkeypatch, tmp_path):
    from app.reading.sources import SOURCES

    _isolate_db(monkeypatch, tmp_path / "test.db")
    monkeypatch.setitem(SOURCES, "broken", {"name": "Broken", "mcp_url": "http://127.0.0.1:1/mcp"})

    client = TestClient(server.app)
    response = client.get("/api/reader-agent/read?source=broken")

    assert response.status_code == 503


def test_reader_agent_route_is_not_shadowed_by_the_mcp_mount(monkeypatch, tmp_path):
    """Review Focus #3: a route registered after app.mount('/', ...) is silently 404'd, no
    exception. This test exists specifically to catch that class of mistake for THIS route,
    the same way Milestone 5's test_preexisting_routes_still_work_after_the_mcp_mount already
    does for the routes that existed before it."""
    _isolate_db(monkeypatch, tmp_path / "test.db")

    client = TestClient(server.app)
    response = client.get("/api/reader-agent/read?source=unknown_but_route_must_still_match")

    assert response.status_code == 404  # reached the handler (UnknownSource), not a raw
    # routing 404 from an unmatched path - if the route were shadowed, this would also be
    # 404, so the real proof is that test_reader_agent_read_returns_the_normalized_envelope
    # above gets a 200, not a 404, for a VALID source+location.
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_server.py -v -k reader_agent`
Expected: FAIL — `404 Not Found` on all of them (no such route exists yet), or a collection
error if `app.reading` imports aren't yet wired into `server.py`.

- [ ] **Step 3: Write the implementation**

In `app/server.py`, add this import alongside the existing ones (near the top, in the
`from app....` block):

```python
from app.reading.agent import SourceUnreachable, UnknownSource, read_source
```

Add this new route **immediately after** the existing `api_status` function (which ends right
before the `# Must be the LAST route registration...` comment) and **before** that comment
and the final `app.mount(...)` line:

```python
@app.get("/api/reader-agent/read")
async def api_reader_agent_read(source: str = "aquasentinel", location_id: str | None = None) -> dict:
    """Read one agent-ready source via a real MCP client session and return the normalized,
    scale-agnostic envelope (Milestone 7). No persistence - a live, ephemeral read-through.
    """
    try:
        return await read_source(source, location_id)
    except UnknownSource as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SourceUnreachable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
```

The file's existing final two lines (the shadowing-warning comment and the `app.mount(...)`
call) stay exactly where they are, after this new route.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_server.py -v`
Expected: PASS — every test in the file, including the pre-existing ones (this confirms the
new route and import didn't disturb anything already there).

Run the full suite: `./venv/bin/python -m pytest app/tests/ -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/server.py app/tests/test_server.py
git commit -m "feat: add GET /api/reader-agent/read"
```
