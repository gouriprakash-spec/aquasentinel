"""Deterministic alert-rules gating (milestone 3, plan.md).

Per CLAUDE.md's non-negotiable architecture rule - "Code decides, agents don't" - this module
contains no model calls and no LLM calls. It takes one scored reading (the same dict shape
app.scoring.pull_reading.pull_reading() returns) and decides whether that reading changes the
agency's status. It does not send anything; milestone 4 (FHIR, app/fhir/emit.py) is the only
thing that acts on the `agency_event` this produces. It also still computes a `public_event`
field (season-gated separately) with no consumer - no direct-to-public alerting exists, per the
2026-09-26 scope decision in plan.md's Overview; see plan.md's Open Questions for why that field
wasn't deleted outright.

Rules (docs/alert-rules-decisions.md), all fail-closed:
1. Freshness (config.FRESHNESS_LIMIT_HOURS) - a stale reading produces "unavailable", never an
   event, and pauses (does not reset) any in-progress all-clear countdown.
2. Change of state only - an event only fires when the tier actually changes, compared against
   the last tier that was itself alerted (alert_state.current_tier), not the previous raw reading.
3. 48-hour all-clear window (config.ALL_CLEAR_WINDOW_HOURS) - after an Unsafe alert, an all-clear
   only fires after 48 continuous hours of Safe readings. Any Unsafe reading during the wait
   resets the accumulated time to zero; a stale reading pauses it without resetting it.
4. Recreation season (config.RECREATION_SEASON_START/END) - the agency event fires year-round;
   the public event is suppressed outside the season.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import config, db


def evaluate_reading(reading: dict, db_path: Path | None = None, now: datetime | None = None) -> dict:
    """Decide what, if anything, changed for this location's alert state.

    Returns:
        {
            "location": str,
            "status": "Safe" | "Unsafe" | "unavailable",
            "agency_event": None | "unsafe_onset" | "all_clear",
            "public_event": None | "unsafe_onset" | "all_clear",
            "reason": str,
        }
    """
    now = now or datetime.now(timezone.utc)
    location = reading["location"]
    reading_time = _parse_iso(reading["time"])

    state = db.get_alert_state(location, db_path) or {
        "current_tier": "unknown",
        "safe_streak_started_at": None,
        "safe_accumulated_seconds": 0.0,
    }

    if now - reading_time > timedelta(hours=config.FRESHNESS_LIMIT_HOURS):
        decision = _handle_stale(location, state, reading_time)
    else:
        decision = _handle_fresh(location, reading["risk_tier"], state, reading_time)

    db.upsert_alert_state(
        location=location,
        current_tier=decision["_next_tier"],
        safe_streak_started_at=decision["_next_streak_started_at"],
        safe_accumulated_seconds=decision["_next_accumulated_seconds"],
        updated_at=now.isoformat(),
        db_path=db_path,
    )
    decision.pop("_next_tier")
    decision.pop("_next_streak_started_at")
    decision.pop("_next_accumulated_seconds")
    return decision


def _handle_stale(location: str, state: dict, reading_time: datetime) -> dict:
    accumulated = state["safe_accumulated_seconds"]
    streak_started_at = state["safe_streak_started_at"]

    if streak_started_at is not None:
        accumulated += (reading_time - _parse_iso(streak_started_at)).total_seconds()
        streak_started_at = None  # paused, progress kept

    return {
        "location": location,
        "status": "unavailable",
        "agency_event": None,
        "public_event": None,
        "reason": "reading is stale (older than the freshness limit) - no event, clock paused",
        "_next_tier": state["current_tier"],
        "_next_streak_started_at": streak_started_at,
        "_next_accumulated_seconds": accumulated,
    }


def _handle_fresh(location: str, raw_tier: str, state: dict, reading_time: datetime) -> dict:
    current_tier = state["current_tier"]
    in_season = _in_recreation_season(reading_time)

    if raw_tier == "Unsafe":
        if current_tier == "Unsafe":
            # Already alerted; any Unsafe reading during the all-clear wait resets the clock.
            return _decision(location, "Unsafe", None, None, "still Unsafe, no change of state",
                              "Unsafe", None, 0.0)
        return _decision(
            location, "Unsafe", "unsafe_onset", "unsafe_onset" if in_season else None,
            "tier changed to Unsafe", "Unsafe", None, 0.0,
        )

    # raw_tier == "Safe"
    if current_tier != "Unsafe":
        # Steady Safe (or first-ever reading): nothing pending, nothing to announce.
        return _decision(location, "Safe", None, None, "Safe, no all-clear pending",
                          "Safe", None, 0.0)

    # Waiting for an all-clear: resume the clock (or keep it running) and check the window.
    streak_started_at = state["safe_streak_started_at"] or reading_time.isoformat()
    accumulated = state["safe_accumulated_seconds"]
    elapsed_total = accumulated + (reading_time - _parse_iso(streak_started_at)).total_seconds()

    if elapsed_total >= config.ALL_CLEAR_WINDOW_HOURS * 3600:
        return _decision(
            location, "Safe", "all_clear", "all_clear" if in_season else None,
            f"{config.ALL_CLEAR_WINDOW_HOURS}h continuous Safe reached - all-clear",
            "Safe", None, 0.0,
        )

    return _decision(location, "Safe", None, None, "Safe, all-clear window still running",
                      "Unsafe", streak_started_at, accumulated)


def _decision(location, status, agency_event, public_event, reason,
              next_tier, next_streak_started_at, next_accumulated_seconds) -> dict:
    return {
        "location": location,
        "status": status,
        "agency_event": agency_event,
        "public_event": public_event,
        "reason": reason,
        "_next_tier": next_tier,
        "_next_streak_started_at": next_streak_started_at,
        "_next_accumulated_seconds": next_accumulated_seconds,
    }


def _in_recreation_season(when: datetime) -> bool:
    return config.RECREATION_SEASON_START <= (when.month, when.day) <= config.RECREATION_SEASON_END


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
