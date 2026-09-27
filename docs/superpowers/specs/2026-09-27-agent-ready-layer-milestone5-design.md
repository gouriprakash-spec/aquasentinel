# Milestone 5 — Agent-ready layer (MCP, `/api/status`, `/llms.txt`, JSON-LD)

Status: approved design, not yet implemented. Source of truth for this milestone, per the
conversation on 2026-09-27. `plan.md`'s milestone list still governs where this fits.

## Why this exists

`plan.md`'s Milestone 5: "one of three co-equal contributions... `/api/status`, the read-only MCP
server, `/llms.txt`, JSON-LD. Done when the consistency test passes — MCP, `/api/status`, and the
banner agree on tier and timestamp for one reading." `docs/landing-page/BUILD-SPEC.md`'s "TODO 2 —
make the site agent-ready" gives the concrete shape (tool names, contract fields, JSON-LD
structure), but two of its details are stale against decisions made after it was written (see
Honesty notes), and the actual wiring (how the MCP server is hosted, what `/api/status` reads
from, how a static page gets one dynamic field) wasn't specified. This document is that design.

## Scope decisions already made (do not re-litigate without going back to the user)

1. **New dependency approved 2026-09-27**: the official MCP Python SDK (`mcp` package), added to
   `requirements.txt`.
2. **MCP hosting**: mounted into the *same* FastAPI process as `app/server.py`, not a separate
   process. Unlike the RPHSA stub (a genuinely separate external system), the MCP server is just
   another read-only interface onto AquaSentinel's own data.
3. **`/api/status` reads the most recently *stored* reading**, not a fresh independent live pull.
   Matches `plan.md`'s "nothing computes status twice" — one scoring output, read three ways
   (banner, `/api/status`, MCP), never three separate computations that could disagree.
4. **`/llms.txt`'s methodology link points at `docs/product-brief.md` on the public GitHub repo**,
   not a new in-app page. No new route to build; the repo becomes the public methodology
   reference at submission regardless.

## Honesty notes (things corrected or deliberately decided, flagged rather than silently assumed)

- **`BUILD-SPEC.md`'s `/api/status` contract lists `estimate_cfu_100ml`.** That directly
  contradicts the non-negotiable architecture rule ("never show a bacteria value the system does
  not have"), already correctly enforced in `pull_reading()` and tested
  (`test_pull_reading.py::test_pull_reading_returns_the_shared_signal_contract` asserts
  `"estimate_cfu_100ml" not in reading`). `/api/status` omits it too — this is a correction to a
  stale spec, not a new design choice.
- **`BUILD-SPEC.md`'s JSON-LD spec cites `isBasedOn` USGS 01467200 and NOAA `USW00013739`.**
  `USW00013739` was the original NCEI source, replaced in Milestone 2 by NWS `KPHL`, with the
  Open-Meteo fallback added later (`docs/superpowers/specs/2026-09-26-fhir-milestone4-design.md`
  is unrelated to this but `app/ingestion/open_meteo.py` is the actual fallback in use). JSON-LD's
  `isBasedOn` cites the two sources actually in use today: USGS 01467200 and NWS KPHL.
- **JSON-LD's `dateModified` is the one genuinely dynamic field on an otherwise-static page.**
  Rather than adding a templating engine (a new dependency, and a step toward the "build step"
  `CLAUDE.md` says to avoid), `app/server.py`'s `index()` route reads `index.html`'s raw bytes and
  does one plain string substitution (a placeholder token replaced with the latest reading's
  timestamp) before returning `HTMLResponse` instead of `FileResponse`. It is still the same
  static file, byte-for-byte, except for that one token. If no reading has ever been stored, the
  placeholder is replaced with the current server time at request time (a fallback, not
  fabricated reading data — `dateModified` describes when the *page* was last meaningfully
  current, and an empty dataset is itself accurate information).

## Architecture

Four pieces, all reading from the same underlying data, none of them computing status themselves:

1. **A shared status-contract builder** (new `app/status.py`) — a pure function
   `build_status_contract(row: dict) -> dict` that reshapes one stored-reading row (the flat
   columns from `db.get_recent_readings()`) into the documented contract shape. `source`,
   `source_url`, and `kind` aren't stored per-row in `db.py`'s schema (they're always the same
   constant), so this function fills them in from `pull_reading.py`'s own constants
   (`SOURCE_NAME`, `SOURCE_URL`) rather than duplicating them a second time.
2. **`GET /api/status`** (`app/server.py`) — calls `db.get_recent_readings(limit=1)`, passes the
   one row through `build_status_contract`, returns it. No stored readings yet → a clear
   "no readings yet" response (see Endpoints), never a fabricated one.
3. **The MCP server** (new `app/mcp_server.py`, official SDK's `FastMCP`, Streamable HTTP
   transport) — three read-only tools, all built on the same `app/status.py` builder and
   `db.get_recent_readings()`. No write tools, matching the same "agents read, code decides"
   principle already enforced everywhere else in this codebase.
4. **`GET /llms.txt`** (`app/server.py`) — a new static file, `docs/landing-page/llms.txt`, served
   the same way `/logo.png` already is.

## Resource shapes / contract

**The status contract** (used identically by `/api/status` and `get_current_status`):
```
{
  "location": "penns_landing",
  "time": "<oldest proxy timestamp from the reading>",
  "risk_tier": "Safe" | "Unsafe",
  "confidence": 0.0-1.0,
  "source": "aquasentinel",
  "source_url": "https://waterservices.usgs.gov/nwis/iv/?sites=01467200",
  "retrieved_at": "<when the reading was pulled>",
  "threshold_cfu_100ml": 235,
  "model_version": "rf_B_post2021",
  "regime": "B_post2021",
  "proxies": {
    "water_temp_c": ..., "sp_conductance_uscm": ..., "dissolved_oxygen_mgl": ...,
    "ph": ..., "turbidity_fnu": ..., "precip_mm": ..., "precip_prev_24h_mm": ...
  },
  "kind": "model_estimate"
}
```
Deliberately excludes `estimate_cfu_100ml` (see Honesty notes) and `location_name` (present in
`pull_reading()`'s live output but not stored in `db.py`'s `readings` table — omitted here rather
than invented, since `db.get_recent_readings()` can't supply it).

**MCP tools:**
- `list_monitored_locations()` → `[{"id": "penns_landing", "name": "Penn's Landing, Center City
  tidal Delaware", "latitude": 39.946402, "longitude": -75.139360, "gauge_id": "01467200"}]` —
  reusing `pull_reading.LOCATION_ID`/`LOCATION_NAME` (the underscore internal id, not the
  hyphenated FHIR-specific one from `app/fhir/resources.py`) and the same lat/lon values already
  defined there, not a third copy of the same numbers.
- `get_current_status(location_id: str)` → the status contract above. An unrecognized
  `location_id` (anything other than `"penns_landing"`) returns a clear error, not a guess or an
  empty/default response.
- `get_recent_readings(location_id: str, limit: int = 9)` → a list of the *same status contract
  shape* as `get_current_status` (one entry per stored row, each passed through
  `build_status_contract`), newest first, after validating `location_id`. Consistent shape
  everywhere beats a lighter but different one — an agent reading history gets the exact same
  fields it already knows from `get_current_status`, not a second shape to learn.

**`/llms.txt`** (static file, `docs/landing-page/llms.txt`): Markdown per llmstxt.org — H1
`AquaSentinel`, a one-paragraph summary, then links to the MCP endpoint, `/api/status`, the
GitHub-hosted `docs/product-brief.md` (methodology), and the honesty statement ("estimate", "flag
elevated risk", never "predict illness").

**JSON-LD** (embedded in `docs/landing-page/index.html`, one static `<script
type="application/ld+json">` block with a single substituted token): a schema.org `Dataset` with
`variableMeasured` (the proxy list), `spatialCoverage` as a `Place` for Penn's Landing (reusing the
same coordinates), `dateModified` (the substituted token — see Honesty notes), and `isBasedOn`
citing USGS 01467200 and NWS KPHL.

## Endpoints

- `GET /api/status` — 200 with the status contract if a reading exists; if `db.get_recent_readings`
  returns nothing, 200 with `{"status": "unavailable", "reason": "no readings yet"}` (fail-closed
  in spirit — no fabricated tier, matching the rest of this codebase's conventions — chosen over a
  404, since "no data yet" is itself valid, expected information for a fresh deployment, not an
  error condition).
- `GET /llms.txt` — `FileResponse(docs/landing-page/llms.txt)`, mirroring the existing `/logo.png`
  route exactly.
- MCP endpoint — mounted via the SDK's Streamable HTTP ASGI app, at a path resolved during
  planning (the SDK has a default; no reason to override it unless it conflicts with an existing
  route).

## Testing plan

- Pure-function tests for `build_status_contract`: correct reshaping, correct constant fill-in,
  `estimate_cfu_100ml` and `location_name` genuinely absent.
- `/api/status` tests: returns the contract for the latest stored reading; returns the
  "unavailable" shape on an empty database.
- MCP tool tests: `list_monitored_locations` returns the one known location with correct
  coordinates; `get_current_status` matches `/api/status`'s output for the same reading;
  `get_current_status` with an unknown location_id errors clearly; `get_recent_readings` respects
  `limit` and orders newest-first; **no write tool is registered on the server** (an explicit
  assertion, not just "we didn't write one").
- `/llms.txt` test: the route serves the file, content-type is text, the honesty phrases are
  present.
- JSON-LD test: the served HTML contains a valid JSON-LD script block, `dateModified` is not the
  literal placeholder token (i.e., substitution actually happened), `isBasedOn` cites the two
  correct current sources.
- **Consistency test** (the milestone's explicit Definition of Done): pull one reading, then
  assert the MCP tool output, `/api/status`, and `GET /api/readings` (what the dashboard renders
  from) all report the same `risk_tier` and the same reading timestamp.

## MCP integration specifics (resolved 2026-09-27, verified against the actually-installed SDK)

Three real gotchas surfaced only by installing and smoke-testing the SDK directly, not from
documentation alone — each verified with a working request/response, not just "should work":

1. **Dependency conflict.** `mcp` pulls in `sse-starlette`, which has no upper bound on its own
   `starlette` requirement — pip installs a `starlette` far newer than `fastapi==0.115.0` tolerates
   (`starlette<0.39.0`), breaking the *entire existing app* (`Router.__init__()` signature
   changed). Fix: pin `starlette==0.38.6` explicitly in `requirements.txt`, verified compatible
   with both `fastapi==0.115.0` (full existing suite, 83/83) and `mcp==1.30.0`'s `FastMCP` /
   `streamable_http_app()` (direct smoke test).
2. **SDK major-version split.** The default `pip install mcp` resolves to 2.x, where `FastMCP` was
   renamed to `MCPServer` with a different API. This project uses `mcp<2` (resolved to `1.30.0`)
   deliberately, for the well-documented `FastMCP`/`@mcp.tool()` API this design is written against.
3. **Mounting into an existing FastAPI app** — two things are easy to get wrong:
   - `mcp.streamable_http_app()` already serves its own route at `mcp.settings.streamable_http_path`
     (default `/mcp`) *inside* the Starlette app it returns. Mount it at the parent's root
     (`app.mount("/", mcp.streamable_http_app())`), not at `/mcp` — mounting at `/mcp` doubles the
     path to `/mcp/mcp`.
   - Starlette does **not** propagate a mounted sub-app's own `lifespan` to the parent
     automatically. `streamable_http_app()`'s lifespan starts the MCP session manager's task group
     (`self.session_manager.run()`) — without it, every request fails with `RuntimeError: Task
     group is not initialized`. Fix: the parent's own lifespan must explicitly enter it:
     ```python
     @asynccontextmanager
     async def lifespan(app: FastAPI):
         db.init_db()
         fhir_store.init_db()
         async with mcp_server.mcp.session_manager.run():
             yield
     ```
   - The SDK's DNS-rebinding protection (`TransportSecuritySettings`, on by default) rejects
     requests whose `Host`/`Origin` headers aren't explicitly allowlisted — including `testserver`
     (`fastapi.testclient.TestClient`'s default `Host` header) and `localhost:8000`/
     `127.0.0.1:8000` for real local use. Must be configured at `FastMCP(...)` construction time,
     not left as the empty default (which allows nothing).
