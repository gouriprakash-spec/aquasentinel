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
from app.ingestion.nws import RainfallUnavailable
from app.ingestion.usgs import UsgsDataUnavailable
from app.scoring.pull_reading import pull_reading

LANDING_PAGE_DIR = Path(__file__).resolve().parents[1] / "docs" / "landing-page"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="AquaSentinel", lifespan=lifespan)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(LANDING_PAGE_DIR / "index.html")


@app.get("/logo.png")
def logo() -> FileResponse:
    return FileResponse(LANDING_PAGE_DIR / "logo.png")


@app.post("/api/pull-reading")
def api_pull_reading() -> dict:
    """Fetch a real live reading, score it, persist it, gate it, and return the reading.

    Fails closed: if USGS or NWS can't be reached or parsed, this returns 503 rather
    than a fabricated reading. The alert-rules gating (milestone 3) runs after every real
    pull - triggered here by the dashboard (on open, on click, or hourly while open), not
    by a background scheduler (see plan.md's Open Questions for that known gap). Its
    decision isn't sent anywhere yet (no FHIR/WhatsApp until milestones 4-5) or returned
    to the caller - it only updates the stored alert_state so change-of-state and the
    48h all-clear window track correctly once something does act on them.
    """
    try:
        reading = pull_reading()
    except (UsgsDataUnavailable, RainfallUnavailable) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    db.insert_reading(reading)
    evaluate_reading(reading)
    return reading


@app.get("/api/readings")
def api_readings(limit: int = 9) -> list[dict]:
    """Recent stored readings, newest first - lets the dashboard survive a page reload."""
    return db.get_recent_readings(limit=limit)
