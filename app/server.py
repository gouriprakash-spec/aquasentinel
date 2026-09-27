"""FastAPI app: serves the dashboard and the live-reading API.

Wires app/scoring/pull_reading.py (real USGS + NWS + model) into the dashboard that
docs/landing-page/index.html renders. Per CLAUDE.md, the front end stays framework-free -
this only serves the existing page and answers its fetches; the page itself is not
rewritten as React or anything else.

Run: ./venv/bin/uvicorn app.server:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from app import db
from app.alerts.gating import evaluate_reading
from app.fhir import emit as fhir_emit
from app.fhir import routes as fhir_routes
from app.fhir import store as fhir_store
from app.ingestion.nws import RainfallUnavailable
from app.ingestion.usgs import UsgsDataUnavailable
from app.scoring.pull_reading import LOCATION_ID, pull_reading
from app.status import build_status_contract

LANDING_PAGE_DIR = Path(__file__).resolve().parents[1] / "docs" / "landing-page"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    fhir_store.init_db()
    yield


app = FastAPI(title="AquaSentinel", lifespan=lifespan)
app.include_router(fhir_routes.router)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(LANDING_PAGE_DIR / "index.html")


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
        reading = pull_reading()
    except (UsgsDataUnavailable, RainfallUnavailable) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    db.insert_reading(reading)
    decision = evaluate_reading(reading)
    fhir_emit.emit_event(reading, decision)
    return reading


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
