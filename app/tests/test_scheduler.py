"""Tests for app/scheduler.py and the scheduled pull wired into app/server.py.

The scheduler tests drive the loop with a fake clock and a fake sleep (the fake sleep just
moves the fake clock forward), so nothing really waits and there is no pytest-asyncio
dependency. The server tests monkeypatch pull_reading() for the same reason test_server.py
does: no live USGS/NWS in unit tests.

Pulls run on the wall clock - at the top of every hour (13:00, 14:00, ...) - and NOT at startup
(Gouri, 2026-10-03): the readings live on a persistent disk, so a restart or a deploy keeps the
latest stored reading instead of fetching a new one.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import db, scheduler, server
from app.fhir import store as fhir_store
from app.ingestion.usgs import UsgsDataUnavailable
from app.tests.test_server import _fake_reading


HOUR = 3600


def _at(hour: int, minute: int = 0, second: float = 0.0) -> datetime:
    whole_seconds = int(second)
    microseconds = round((second - whole_seconds) * 1_000_000)
    return datetime(2026, 10, 2, hour, minute, whole_seconds, microseconds, tzinfo=timezone.utc)


class _FakeClock:
    """A clock and a sleep that share one timeline: sleeping just moves the clock forward."""

    def __init__(self, start: datetime, stop_after_sleeps: int, wake_early_first_sleep: float = 0.0):
        self.now = start
        self.sleeps = []
        self._stop_after_sleeps = stop_after_sleeps
        self._wake_early_first_sleep = wake_early_first_sleep

    def clock(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if len(self.sleeps) > self._stop_after_sleeps:
            raise asyncio.CancelledError  # ends the never-ending loop, like a shutdown would
        early = self._wake_early_first_sleep if len(self.sleeps) == 1 else 0.0
        self.now += timedelta(seconds=seconds - early)


def _drive(fake: _FakeClock, job) -> None:
    try:
        asyncio.run(scheduler.run_on_the_hour(HOUR, job, clock=fake.clock, sleep=fake.sleep))
    except asyncio.CancelledError:
        pass


# --- seconds_until_next_boundary: the pure timing rule ---

def test_ten_past_the_hour_waits_until_the_next_top_of_the_hour():
    assert scheduler.seconds_until_next_boundary(_at(13, 10), HOUR) == 50 * 60


def test_exactly_on_the_hour_waits_a_full_hour_not_zero():
    # The pull for "now" has already happened; the next one is the following hour.
    assert scheduler.seconds_until_next_boundary(_at(13, 0), HOUR) == HOUR


def test_one_second_before_the_hour_waits_one_second():
    assert scheduler.seconds_until_next_boundary(_at(12, 59, 59), HOUR) == 1


def test_four_forty_four_waits_until_five_oclock():
    assert scheduler.seconds_until_next_boundary(_at(4, 44), HOUR) == 16 * 60


# --- the loop ---

def test_does_not_run_at_startup_only_exactly_on_each_top_of_the_hour():
    fake = _FakeClock(_at(13, 10), stop_after_sleeps=3)
    run_times = []

    _drive(fake, lambda: run_times.append(fake.now))

    # Nothing at 13:10 (startup): the latest stored reading is kept. First run is 14:00 sharp.
    assert run_times == [_at(14), _at(15), _at(16)]


def test_a_slow_job_does_not_push_later_runs_off_the_hour():
    fake = _FakeClock(_at(13, 10), stop_after_sleeps=2)
    run_times = []

    def slow_job():
        run_times.append(fake.now)
        fake.now += timedelta(minutes=7)  # the job itself takes 7 minutes

    _drive(fake, slow_job)

    assert run_times == [_at(14), _at(15)]


def test_waking_slightly_early_never_runs_the_job_before_the_hour():
    # The first sleep returns 0.5s early (13:59:59.5). The job must still wait for 14:00:00,
    # and must run once there - not at 13:59:59.5 and again at 14:00:00.
    fake = _FakeClock(_at(13, 10), stop_after_sleeps=2, wake_early_first_sleep=0.5)
    run_times = []

    _drive(fake, lambda: run_times.append(fake.now))

    assert run_times == [_at(14)]


def test_a_job_that_raises_does_not_end_the_schedule():
    fake = _FakeClock(_at(13, 10), stop_after_sleeps=3)
    run_times = []

    def flaky_job():
        run_times.append(fake.now)
        if len(run_times) == 1:
            raise RuntimeError("simulated crash in the first run")

    _drive(fake, flaky_job)

    # One bad run must not end the schedule - otherwise readings would silently stop
    # arriving and the status would quietly go "unavailable" forever.
    assert run_times == [_at(14), _at(15), _at(16)]


def test_the_loop_stops_cleanly_when_cancelled():
    calls = []

    async def runner():
        task = asyncio.create_task(scheduler.run_on_the_hour(HOUR, lambda: calls.append(1)))
        await asyncio.sleep(0.1)  # long enough that a startup run, if there were one, would have happened
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(runner())
    count_at_stop = len(calls)
    time.sleep(0.1)

    assert count_at_stop == 0  # no startup run: it was asleep until the next top of the hour
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


def test_app_startup_does_not_pull_a_reading(monkeypatch, tmp_path):
    """The latest stored reading is kept across a restart or deploy (it lives on the persistent
    disk); the next pull is the next top of the hour, never at startup."""
    _use_temp_db(monkeypatch, tmp_path)
    calls = []

    def pull_that_must_not_run():
        calls.append(1)
        return _fake_reading("Safe")

    monkeypatch.setattr(server, "pull_reading", pull_that_must_not_run)
    monkeypatch.setattr(server, "SCHEDULER_ENABLED", True)
    # The MCP library allows its session manager to start only once per process, and
    # test_server.py's MCP test already uses that one start. This test is about the
    # scheduler, not MCP, so stand in a no-op for it.
    monkeypatch.setattr(server.mcp_server.mcp.session_manager, "run", _no_op_mcp_session)

    with TestClient(server.app):
        time.sleep(0.5)  # ample time for a startup pull to have happened if there were one

    assert calls == []
    assert db.get_recent_readings(limit=5) == []
