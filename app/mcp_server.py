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

import logging
import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app import db
from app.config import LOCATION_LAT, LOCATION_LON
from app.scoring.pull_reading import LOCATION_ID, LOCATION_NAME
from app.status import build_status_contract, current_status

logger = logging.getLogger(__name__)

# Same USGS gauge id already embedded in pull_reading.SOURCE_URL - not a second source of
# truth, just not currently its own named constant there.
GAUGE_ID = "01467200"

ALLOWED_HOSTS_ENV_VAR = "AQUASENTINEL_ALLOWED_HOSTS"
_LOCAL_HOSTS = ["testserver", "localhost", "localhost:8000", "127.0.0.1:8000"]
_LOCAL_ORIGINS = ["http://testserver", "http://localhost:8000", "http://127.0.0.1:8000"]


def build_transport_security(extra_hosts: str | None) -> TransportSecuritySettings:
    """The MCP SDK rejects any request whose Host header is not allowlisted (DNS-rebinding
    protection), answering "Invalid Host header". The local defaults cover dev and tests; a
    real deploy adds its public hostname through AQUASENTINEL_ALLOWED_HOSTS (comma-separated,
    bare hostnames such as "aquasentinel.onrender.com", no scheme or path). The protection is
    extended, never turned off.

    A malformed entry (for example a pasted full URL) is skipped with a logged warning rather
    than accepted as a strange host string or allowed to crash the server at import time.
    """
    hosts = list(_LOCAL_HOSTS)
    origins = list(_LOCAL_ORIGINS)
    for raw_entry in (extra_hosts or "").split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        if "/" in entry or any(character.isspace() for character in entry):
            logger.warning(
                "Ignoring %s entry %r: expected a bare hostname like "
                "'aquasentinel.onrender.com', no scheme or path.",
                ALLOWED_HOSTS_ENV_VAR, entry,
            )
            continue
        hosts.append(entry)
        # A deployed host is served over https, so the matching Origin is the https form.
        origins.append(f"https://{entry}")
    return TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins)


mcp = FastMCP(
    "aquasentinel_mcp",
    transport_security=build_transport_security(os.environ.get(ALLOWED_HOSTS_ENV_VAR)),
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
        "confidence": float|null, "gauge_available": bool, "source": str, "source_url": str, "retrieved_at": str,
        "threshold_cfu_100ml": int, "model_version": str, "regime": str,
        "decision_basis": "rainfall_rule"|"cso_overflow_rule", "rule_threshold_mm": float,
        "rainfall_source": "nws"|"open-meteo", "model_probability_unsafe": float,
        "cso_status": null | {"outfall_name": str, "status": int, "distance_km": float,
        "last_poll": str}, "proxies": {..., "precip_prev_48h_mm": float},
        "kind": "model_estimate"}
        Ordinarily risk_tier is decided by the rainfall rule: Unsafe when
        proxies.precip_prev_48h_mm (rain over the two prior local calendar days) >=
        rule_threshold_mm, and a model's model_probability_unsafe only informs confidence
        (how strongly the model agrees with the rule). But a nearby, fresh, actively or
        recently overflowing combined-sewer outfall (Milestone 6) OVERRIDES that: it forces
        risk_tier to "Unsafe" regardless of the rainfall rule's own verdict, sets
        decision_basis to "cso_overflow_rule", and populates cso_status with the
        triggering outfall. The overflow changes only the tier and its stated reason:
        confidence is ALWAYS the rule/model agreement (how strongly the model agrees with the
        rainfall rule's verdict) and is never altered by an overflow. So an overflow-decided
        "Unsafe" can legitimately show a high confidence (e.g. 0.86) - the rainfall rule and
        the model agreed that rain alone was not a risk; the overflow is what flipped the tier.
        When the USGS gauge had no current reading for the last pull (gauge_available is false),
        the tier is still decided (by the rainfall rule and the overflow rule - the gauge never
        decides it), but confidence is null and the water-quality values in proxies are null:
        report them as "not available", never as zero. precip_* values are real-time rainfall.
        If rainfall_missing_hours is above 0, that many hourly rain reports for the previous two
        days were unavailable: proxies.precip_prev_48h_mm is then "at least" that many mm (a lower
        bound), not an exact total; the tier is still decided correctly (Unsafe) from it.
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
