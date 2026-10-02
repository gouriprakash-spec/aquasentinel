"""Tests for app/scheduler.py and the scheduled pull wired into app/server.py.

The scheduler tests use asyncio.run() with a tiny interval and a fake job, so no real
60-minute wait and no pytest-asyncio dependency. The server tests monkeypatch
pull_reading() for the same reason test_server.py does: no live USGS/NWS in unit tests.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app import db, scheduler, server
from app.fhir import store as fhir_store
from app.ingestion.usgs import UsgsDataUnavailable
from app.tests.test_server import _fake_reading


def _run_for(seconds: float, coroutine) -> None:
    """Run the never-ending scheduler loop for a short time, then stop it."""
    async def runner():
        task = asyncio.create_task(coroutine)
        await asyncio.sleep(seconds)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(runner())


def test_run_every_runs_the_job_immediately_then_repeats():
    calls = []

    _run_for(0.2, scheduler.run_every(0.02, lambda: calls.append(1)))

    # Immediately (so a fresh deploy has a reading without waiting an hour) and repeatedly.
    assert len(calls) >= 3


def test_run_every_survives_a_job_that_raises():
    calls = []

    def flaky_job():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("simulated crash in the first run")

    _run_for(0.2, scheduler.run_every(0.02, flaky_job))

    # One bad run must not end the schedule - otherwise readings would silently stop
    # arriving and the status would quietly go "unavailable" forever.
    assert len(calls) >= 2


def test_run_every_stops_cleanly_when_cancelled():
    calls = []

    _run_for(0.1, scheduler.run_every(0.02, lambda: calls.append(1)))
    count_at_stop = len(calls)
    time.sleep(0.1)

    assert len(calls) == count_at_stop


def _use_temp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(fhir_store, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    fhir_store.init_db()


def test_scheduled_pull_stores_a_reading(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "pull_reading", lambda: _fake_reading("Safe"))

    server._scheduled_pull()

    assert len(db.get_recent_readings(limit=5)) == 1


def test_scheduled_pull_fails_closed_without_raising_when_data_is_unavailable(
    monkeypatch, tmp_path
):
    _use_temp_db(monkeypatch, tmp_path)

    def raise_unavailable():
        raise UsgsDataUnavailable("USGS returned 503")

    monkeypatch.setattr(server, "pull_reading", raise_unavailable)

    server._scheduled_pull()  # must not raise

    # Fail closed: nothing stored, so /api/status keeps saying "unavailable" rather than
    # showing an old reading as if it were new.
    assert db.get_recent_readings(limit=5) == []


@asynccontextmanager
async def _no_op_mcp_session():
    yield


def test_app_startup_runs_the_scheduled_pull(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "pull_reading", lambda: _fake_reading("Safe"))
    monkeypatch.setattr(server, "SCHEDULER_ENABLED", True)
    # The MCP library allows its session manager to start only once per process, and
    # test_server.py's MCP test already uses that one start. This test is about the
    # scheduler, not MCP, so stand in a no-op for it.
    monkeypatch.setattr(server.mcp_server.mcp.session_manager, "run", _no_op_mcp_session)

    with TestClient(server.app):
        deadline = time.time() + 5
        while time.time() < deadline and not db.get_recent_readings(limit=1):
            time.sleep(0.05)

        assert len(db.get_recent_readings(limit=5)) == 1
