"""A minimal in-process scheduler: run one job now, then again every N seconds.

Why in-process and not a Render cron job: a cron job runs as a separate process with its
own disk, so it could not write to the web service's SQLite file. Running inside the app
shares the same database, and it needs no new dependency.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


async def run_every(interval_seconds: float, job: Callable[[], None]) -> None:
    """Run `job` immediately, then every `interval_seconds`, until cancelled.

    Runs immediately so a fresh deploy has a reading without waiting a full interval. The job
    is blocking (HTTP calls, SQLite), so it runs in a worker thread - inline it would stall the
    whole event loop, including the dashboard and the MCP endpoint.
    """
    while True:
        try:
            await asyncio.to_thread(job)
        except Exception:
            # Logged loudly, not swallowed: a crash here that ended the loop would silently
            # stop all future pulls, which is worse than one logged failure. (CancelledError
            # is not an Exception subclass, so cancelling the task still stops it.)
            logger.exception("Scheduled job raised; will retry at the next interval")
        await asyncio.sleep(interval_seconds)
