# Advisory Reader Agent (native mode) — Design

## Context and motivation

`docs/product-brief.md` names three co-equal contributions: the agent-ready publishing
layer (Milestone 5, done), the Advisory Reader Agent's extensibility, and the Sampling
Coordinator loop (Milestone 6, not yet built). The Reader Agent never got its own numbered
milestone in `plan.md` — this spec fills that gap. It is scoped as **Milestone 5.5**, sitting
between the agent-ready layer and the Sampling Coordinator in `plan.md`'s build order.

The Reader Agent is the other side of Milestone 5's idea: Milestone 5 made AquaSentinel
*readable* by agents; the Reader Agent is a component that *reads* an agent-ready source,
with AquaSentinel's own MCP server as "the reference native source" (`docs/product-brief.md:105`).
Per `docs/landing-page/BUILD-SPEC.md:135`, it prefers a source's MCP server, then a
structured feed advertised in `llms.txt`, then (legacy mode) the HTML page itself.

**This build covers native mode only.** Legacy mode (scraping a source that publishes
nothing for agents) is explicitly deferred to Future Directions in `docs/product-brief.md`
and is not part of this spec.

## Scope

**In scope:**
- A small, hardcoded sources registry (one entry: AquaSentinel itself), proving the
  registry pattern is real without building a second source.
- A real MCP client (not an in-process shortcut) that opens a genuine MCP session against a
  registered source's `mcp_url`, calls `list_monitored_locations` and `get_current_status`,
  and normalizes the result into a scale-agnostic envelope.
- One read-only API endpoint that triggers a read and returns the normalized envelope.
- Fail-closed error handling consistent with the rest of the codebase.

**Out of scope (deliberately, not oversights):**
- Legacy mode (HTML scraping, heavier validation, LLM-assisted extraction).
- Persisting reads anywhere. Each call is a live, ephemeral read-through; nothing is stored.
- Feeding a Reader Agent read into AquaSentinel's own alert/gating pipeline. Per `plan.md`'s
  data flow diagram, only AquaSentinel's own model output drives alerts in this build;
  a third-party source's rating informing alerts is a Future Direction.
- A second, genuinely external source. The registry pattern is proven; adding a real second
  source is future work once one is chosen.
- Any LLM call. Native-mode reading is a deterministic MCP client call — matching
  `docs/landing-page/BUILD-SPEC.md`'s own note that "the read itself is a deterministic MCP
  client call."

## Architecture

New subpackage `app/reading/`, matching the existing per-concern layout (`app/ingestion/`,
`app/fhir/`, `app/alerts/`, `app/scoring/`):

- **`app/reading/sources.py`** — the sources registry:
  ```python
  SOURCES = {
      "aquasentinel": {
          "name": "AquaSentinel",
          "mcp_url": f"{AQUASENTINEL_BASE_URL}/mcp",
      },
  }
  ```
  `AQUASENTINEL_BASE_URL` is imported from the same place `app/rphsa_stub.py` already reads
  it (`os.environ.get("AQUASENTINEL_BASE_URL", "http://localhost:8000")`) — one source of
  truth for "where AquaSentinel itself lives," not a second config knob.

- **`app/reading/agent.py`** — the reader itself:
  ```python
  async def read_source(source_id: str, location_id: str | None = None,
                         http_client: httpx.AsyncClient | None = None) -> dict:
      ...
  ```
  Raises a dedicated exception (`UnknownSource`) for a registry miss, and a dedicated
  exception (`SourceUnreachable`) for any connection failure, protocol error, timeout, or
  unparseable response — `app/server.py`'s route maps these to `404` / `503` respectively
  (see Error handling below). `http_client` is `None` in production (the SDK builds a real
  network client against `mcp_url`); tests inject one wired to
  `httpx.ASGITransport(app=server.app)`, verified live end-to-end in a real Python session
  during this design's brainstorming — a genuine MCP handshake, `list_tools`, and
  `call_tool`, entirely in-process, no live server needed (see Testing).

- **`app/server.py`** — one new route:
  ```python
  @app.get("/api/reader-agent/read")
  async def api_reader_agent_read(source: str = "aquasentinel",
                                   location_id: str | None = None) -> dict:
      ...
  ```
  `GET`, not `POST`: this has no side effects (no persistence, no write) — unlike
  `/api/pull-reading`, a real external pull that gets stored.

## Data flow — the normalized envelope

1. Look up `source_id` in the registry. Unknown key → `UnknownSource`.
2. Open **one** real MCP session against the registry entry's `mcp_url` (initialize,
   negotiate) — both of the following calls happen within that same session, not two
   separate connections.
3. If `location_id` is not given, call `list_monitored_locations()` and use the first
   result's `"id"`.
4. Call `get_current_status(location_id)`.
5. Parse the tool result. **Amended 2026-10-01**, re-verified live against both tools this
   build actually calls (the original text below covered only the dict-returning case):
   - `get_current_status(location_id)` returns a single `dict` → FastMCP gives no
     `structuredContent` for a bare `dict` return type; the payload is
     `json.loads(result.content[0].text)`.
   - `list_monitored_locations()` returns a `list[dict]` → FastMCP infers a schema for a
     list-typed return and populates `result.structuredContent = {"result": [...]}` with the
     real list; `content[0].text` is **not** the whole list (confirmed live: with one
     location registered, `content[0].text` is that single location's JSON object with no
     enclosing array — naively doing `json.loads(content[0].text)[0]` would silently work
     today only by accident, and would break or misbehave the moment a second location
     exists). Use `structuredContent["result"]` for this tool instead.
   - The parsing helper must handle both shapes: prefer `structuredContent` when present
     (unwrapping a `{"result": ...}` envelope), fall back to `json.loads(content[0].text)`
     otherwise.
6. Wrap the parsed payload, unchanged, into:
   ```python
   {
       "source": "aquasentinel",              # registry key
       "source_name": "AquaSentinel",         # registry's display name
       "source_url": "http://localhost:8000/mcp",   # the mcp_url actually called
       "retrieved_at": "<this agent's own retrieval timestamp, ISO8601 UTC>",
       "location_id": "penns_landing",
       "native_rating": "Safe",               # whatever field the source used - unchanged;
                                               # null if the source itself reported "unavailable"
       "native_status": { ... },              # the full parsed tool response, verbatim
   }
   ```

Two decisions worth restating from brainstorming:
- **`retrieved_at` is the *reader's* clock**, distinct from whatever timestamp the source's
  own payload carries (that one stays nested inside `native_status`, untouched). One is
  "when the source scored its reading," the other is "when this agent read it."
- **`native_status` is the whole raw parsed response**, not a trimmed field. A native
  source's answer is already structured JSON, not prose — "verbatim evidence" here means
  "the untouched response," not an extracted quote. Extraction/summarization is a
  legacy-mode concern, out of scope.
- **`native_rating` is scale-agnostic on purpose.** A genuine third-party source (e.g.
  RiverCast) would not use AquaSentinel's `Safe`/`Unsafe` vocabulary. Reusing the
  `risk_tier` field name would imply reinterpreting a third-party rating onto AquaSentinel's
  scale, which `docs/product-brief.md` explicitly forbids ("Any third-party rating would
  keep its own scale and thresholds, never remapped onto AquaSentinel's 235 threshold").
  `native_rating` holds whatever the source published, unchanged — for AquaSentinel-as-source
  that happens to be `"Safe"` or `"Unsafe"`, but the field name itself stays generic so the
  same code path is honest for a future, differently-scaled source.

## Error handling (fail closed, consistent with the rest of the codebase)

| Situation | Response |
|---|---|
| Unknown `source` key (not in the registry) | `404` — matches `/api/status`'s unknown-location handling |
| Source reachable, but its own answer is `{"status": "unavailable", ...}` (e.g. AquaSentinel's own stale-reading fail-closed from Milestone 5) | `200`, passed through faithfully as `native_status`, `native_rating: null` — an honest, valid answer, not an error |
| Source unreachable, MCP handshake fails, times out, or returns a shape that can't be parsed | `503` with a clear reason — matches how `/api/pull-reading` reports a live USGS/NWS failure |

The rule carried over from everywhere else in this codebase: never synthesize a rating. A
connection failure or malformed response is a `503`, never a fabricated `native_rating`.

## Testing plan

Following this codebase's existing preference for real integration tests over mocks (see
`test_fhir_end_to_end.py`, Milestone 5's `test_mcp_server.py`/`test_consistency.py`):
`read_source()` takes an optional injected `httpx.AsyncClient`. Tests pass one built on
`httpx.ASGITransport(app=server.app)`, with the app's `lifespan` driven manually via
`async with server.app.router.lifespan_context(server.app):` (the same lifespan-must-run
gotcha already documented from Milestone 5's `TestClient` tests, applied to an async client
instead). This exercises the *real* MCP protocol — real session negotiation, real tool
calls — with no live server process and nothing about the MCP layer mocked.

Tests to write:
- Reading a fresh AquaSentinel reading end-to-end → correct `native_rating`, `native_status`,
  `source_url`, `retrieved_at`.
- Reading when AquaSentinel's own status is `"unavailable"` (stale or no data) → passed
  through, `native_rating: null`, still `200`.
- Unknown `source` query value → `404`.
- Unreachable/broken source (a registry entry pointed at a closed port, or a client that
  errors) → `503`, no fabricated rating.
- No `location_id` given → falls back to `list_monitored_locations()`'s first result.

## Config additions

No new environment variable. `AQUASENTINEL_BASE_URL` (already in `.env.example` since
Milestone 4) is reused as the registry's only source URL for this build.

## Honesty guardrails carried over from the rest of the project

- Never reinterpret or reword a source's rating — `native_rating` is copied verbatim.
- Never show a bacteria value the system does not have — not applicable here since this
  component never computes a value, only reads one.
- The one-sentence pitch's claim ("a reader agent designed to extend that same pattern to
  other advisory sites") is made true by the registry + real-MCP-client design, not just by
  prose: adding a second real source is a config entry, not a new code path.
