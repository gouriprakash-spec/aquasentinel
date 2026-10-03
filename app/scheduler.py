"""A minimal in-process scheduler: run a job at every wall-clock boundary (for an hourly
interval, at the top of each hour: 13:00, 14:00, ...). It does NOT run the job at startup.

Why in-process and not a Render cron job: a cron job runs as a separate process with its own
disk, so it could not write to the web service's SQLite file. Running inside the app
shares the same database, and it needs no new dependency.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def seconds_until_next_boundary(now: datetime, interval_seconds: float) -> float:
    """Seconds from `now` until the next wall-clock multiple of `interval_seconds`.

    Aligned to the Unix epoch, so 3600 gives the top of every hour (the hour boundaries are the
    same in UTC and in Philadelphia time). Exactly on a boundary this returns a full interval,
    not zero: the pull for "now" has already happened, so the next one is the following boundary.
    """
    epoch = now.timestamp()
    return (math.floor(epoch / interval_seconds) + 1) * interval_seconds - epoch


async def _sleep_until_next_boundary(
    interval_seconds: float,
    clock: Callable[[], datetime],
    sleep: Callable[[float], Awaitable[None]],
) -> None:
    now = clock()
    target_epoch = now.timestamp() + seconds_until_next_boundary(now, interval_seconds)
    # Loop, not a single sleep: a timer that wakes a hair early would otherwise run the job at
    # 12:59:59 and then again at 13:00:00. Waiting out the remainder keeps it to one run, on the
    # hour.
    while True:
        remaining = target_epoch - clock().timestamp()
        if remaining <= 0:
            return
        await sleep(remaining)


async def run_on_the_hour(
    interval_seconds: float,
    job: Callable[[], None],
    *,
    clock: Callable[[], datetime] = _utc_now,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Run `job` at every wall-clock multiple of `interval_seconds`, until cancelled. (`clock` and
    `sleep` are injectable only so tests need not really wait.)

    Deliberately NOT at startup (Gouri, 2026-10-03): the readings live on a persistent disk, so a
    restart or a deploy keeps the latest stored reading instead of fetching a new one. The
    trade-off: on a brand-new, empty database there is no reading until the first top of the hour.
    The job is blocking (HTTP calls, SQLite), so it runs in a worker thread - inline it would
    stall the whole event loop, including the dashboard and the MCP endpoint. The next boundary is
    worked out after the job finishes, so a slow job never pushes later runs off the hour.
    """
    while True:
        await _sleep_until_next_boundary(interval_seconds, clock, sleep)
        try:
            await asyncio.to_thread(job)
        except Exception:
            # Logged loudly, not swallowed: a crash here that ended the loop would silently
            # stop all future pulls, which is worse than one logged failure. (CancelledError
            # is not an Exception subclass, so cancelling the task still stops it.)
            logger.exception("Scheduled job raised; will retry at the next boundary")
