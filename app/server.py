"""FastAPI app: serves the dashboard and the read-only status API.

A scheduled job (app/scheduler.py, started in this module's lifespan) runs
app/scoring/pull_reading.py (real USGS + NWS + model) at startup and on the hour and stores
the result; the dashboard that docs/landing-page/index.html renders, /api/status and the MCP
server only read what is stored. Per CLAUDE.md, the front end stays framework-free - this
only serves the existing page and answers its fetches; the page itself is not rewritten as
React or anything else.

Run: ./venv/bin/uvicorn app.server:app --reload
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from app import config
from app import db
from app import mcp_server
from app import scheduler
from app.alerts.gating import evaluate_reading
from app.fhir import emit as fhir_emit
from app.fhir import routes as fhir_routes
from app.fhir import store as fhir_store
from app.ingestion.nws import RainfallUnavailable
from app.ingestion.usgs import UsgsDataUnavailable
from app.scoring.pull_reading import LOCATION_ID, pull_reading
from app.status import CSO_STATUS_TEXT, annotate_freshness, current_status

LANDING_PAGE_DIR = Path(__file__).resolve().parents[1] / "docs" / "landing-page"
DATASET_DATE_MODIFIED_TOKEN = "__AQUASENTINEL_DATASET_DATE_MODIFIED__"

logger = logging.getLogger(__name__)

# A module flag (not just config) so a test that runs the real lifespan can switch the timer
# off and avoid a live USGS call at startup. Production leaves it True.
SCHEDULER_ENABLED = True


def _pull_and_publish() -> dict:
    """Fetch a real reading, store it, gate it, and send any FHIR event.

    Called only by the scheduler. Raises UsgsDataUnavailable/RainfallUnavailable when a source
    can't be read - the caller decides how to surface that.
    """
    reading = pull_reading()
    # save_reading keeps one row per gauge reading; gating and FHIR below still run on EVERY
    # pull, so a tier change inside one gauge window is never swallowed by the update.
    db.save_reading(reading)
    outfalls = reading["evidence"].get("cso_outfalls")
    if outfalls is not None:  # None = the feed could not be read: keep the previous snapshot
        db.save_cso_outfalls(outfalls, snapshot_at=reading["retrieved_at"])
    decision = evaluate_reading(reading)
    fhir_emit.emit_event(reading, decision)
    return reading


def _scheduled_pull() -> None:
    try:
        _pull_and_publish()
    except (UsgsDataUnavailable, RainfallUnavailable) as exc:
        # Fail closed: nothing is stored, so /api/status keeps reporting "unavailable" once the
        # last reading goes stale. Logged, not hidden - the next interval simply tries again.
        logger.warning("Scheduled pull skipped, source unavailable: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    fhir_store.init_db()
    pull_task = None
    if SCHEDULER_ENABLED:
        pull_task = asyncio.create_task(
            scheduler.run_on_the_hour(config.SCHEDULED_PULL_INTERVAL_MINUTES * 60, _scheduled_pull)
        )
    try:
        async with mcp_server.mcp.session_manager.run():
            yield
    finally:
        if pull_task is not None:
            pull_task.cancel()


app = FastAPI(title="AquaSentinel", lifespan=lifespan)
app.include_router(fhir_routes.router)


@app.get("/")
def index() -> HTMLResponse:
    html = (LANDING_PAGE_DIR / "index.html").read_text()
    rows = db.get_recent_readings(limit=1)
    date_modified = rows[0]["retrieved_at"] if rows else datetime.now(timezone.utc).isoformat()
    html = html.replace(DATASET_DATE_MODIFIED_TOKEN, date_modified)
    return HTMLResponse(html)


@app.get("/logo.png")
def logo() -> FileResponse:
    return FileResponse(LANDING_PAGE_DIR / "logo.png")


@app.get("/llms.txt")
def llms_txt() -> FileResponse:
    return FileResponse(LANDING_PAGE_DIR / "llms.txt", media_type="text/markdown")


# There is deliberately NO public route that triggers a live pull (POST /api/pull-reading was
# removed 2026-10-02). Each call made about six live requests to USGS, NWS, Open-Meteo and
# CSOcast and needed no login, so anyone could risk getting this server rate-limited by the
# data sources. The scheduler (app/scheduler.py) is the only thing that fetches; every public
# route below reads what is already stored.


@app.get("/api/readings")
def api_readings(limit: int = 9) -> list[dict]:
    """Recent stored readings, newest first - what the dashboard reads on load. Each row also
    carries `gauge_age_hours` and `stale` (see app.status.annotate_freshness), so the page can
    flag a stale reading without knowing the freshness limit itself."""
    return annotate_freshness(db.get_recent_readings(limit=limit))


@app.get("/api/outfalls")
def api_outfalls() -> dict:
    """The sewer outfalls the overflow rule considered at the last scheduled pull, for the
    dashboard map. Read-only, served from the saved snapshot: a visitor never triggers an
    outside call. `triggering` marks the outfall that decided the newest reading, if any."""
    snapshot = db.get_cso_outfalls()
    newest = db.get_recent_readings(limit=1)
    triggering_name = newest[0]["cso_outfall_name"] if newest else None
    for outfall in snapshot["outfalls"]:
        outfall["status_text"] = CSO_STATUS_TEXT.get(outfall["status"], f"Status {outfall['status']}")
        outfall["triggering"] = outfall["name"] == triggering_name
    return snapshot


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
    return current_status(rows[0] if rows else None)


# Must be the LAST route registration in this file - a route added after this would be
# silently shadowed (404, no exception raised). See this plan's Global Constraints and
# test_preexisting_routes_still_work_after_the_mcp_mount for why.
app.mount("/", mcp_server.mcp.streamable_http_app())
