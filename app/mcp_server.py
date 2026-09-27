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
from app.status import build_status_contract, current_status

# Same USGS gauge id already embedded in pull_reading.SOURCE_URL - not a second source of
# truth, just not currently its own named constant there.
GAUGE_ID = "01467200"

# KNOWN GAP (deliberate, tracked in this plan's Review Focus, not an oversight): this
# allowlist only covers the test client and local dev. No real deploy host is chosen yet
# (see plan.md's Open Questions / the deployment_target decision) - once one is, its real
# hostname must be added to both lists below, and a line added to plan.md's Open Questions
# noting it's done. Until then, every real MCP request against a deployed host will be
# rejected with "Invalid Host header", which will look like a bug rather than this gap.
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
    return current_status(rows[0] if rows else None)


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
