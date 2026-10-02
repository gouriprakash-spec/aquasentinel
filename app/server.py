"""FastAPI app: serves the dashboard and the live-reading API.

Wires app/scoring/pull_reading.py (real USGS + NWS + model) into the dashboard that
docs/landing-page/index.html renders. Per CLAUDE.md, the front end stays framework-free -
this only serves the existing page and answers its fetches; the page itself is not
rewritten as React or anything else.

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
from app.status import current_status

LANDING_PAGE_DIR = Path(__file__).resolve().parents[1] / "docs" / "landing-page"
DATASET_DATE_MODIFIED_TOKEN = "__AQUASENTINEL_DATASET_DATE_MODIFIED__"

logger = logging.getLogger(__name__)

# A module flag (not just config) so a test that runs the real lifespan can switch the timer
# off and avoid a live USGS call at startup. Production leaves it True.
SCHEDULER_ENABLED = True


def _pull_and_publish() -> dict:
    """Fetch a real reading, store it, gate it, and send any FHIR event.

    The one shared path for both the dashboard's POST /api/pull-reading and the scheduled
    pull, so the two can never drift apart. Raises UsgsDataUnavailable/RainfallUnavailable
    when a source can't be read - callers decide how to surface that.
    """
    reading = pull_reading()
    # save_reading keeps one row per gauge reading; gating and FHIR below still run on EVERY
    # pull, so a tier change inside one gauge window is never swallowed by the update.
    db.save_reading(reading)
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
        return _pull_and_publish()
    except (UsgsDataUnavailable, RainfallUnavailable) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
    return current_status(rows[0] if rows else None)


# Must be the LAST route registration in this file - a route added after this would be
# silently shadowed (404, no exception raised). See this plan's Global Constraints and
# test_preexisting_routes_still_work_after_the_mcp_mount for why.
app.mount("/", mcp_server.mcp.streamable_http_app())
