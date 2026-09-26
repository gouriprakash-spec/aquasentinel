"""Tests for app/alerts/gating.py - one test per rule in docs/alert-rules-decisions.md.

Per plan.md's Testing Plan: freshness, change-of-state-only, the 48h all-clear window
(reset by Unsafe, paused-not-reset by stale), the season gate, and the fail-closed path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import db
from app.alerts import gating

LOCATION = "penns_landing"

# Within recreation season (May 1 - Oct 31), so public events are not suppressed by default.
IN_SEASON_START = datetime(2026, 6, 1, tzinfo=timezone.utc)

# Outside recreation season, for the season-gate test.
OUT_OF_SEASON_START = datetime(2026, 12, 15, tzinfo=timezone.utc)


def _reading(risk_tier: str, time: datetime, location: str = LOCATION) -> dict:
    return {"location": location, "time": time.isoformat(), "risk_tier": risk_tier}


def test_stale_reading_is_unavailable_with_no_event(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path=db_path)

    reading_time = IN_SEASON_START
    now = reading_time + timedelta(hours=3)  # older than FRESHNESS_LIMIT_HOURS (2h)

    decision = gating.evaluate_reading(_reading("Unsafe", reading_time), db_path=db_path, now=now)

    assert decision["status"] == "unavailable"
    assert decision["agency_event"] is None
    assert decision["public_event"] is None


def test_first_unsafe_reading_fires_onset_to_both_channels(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path=db_path)

    t0 = IN_SEASON_START
    decision = gating.evaluate_reading(_reading("Unsafe", t0), db_path=db_path, now=t0)

    assert decision["status"] == "Unsafe"
    assert decision["agency_event"] == "unsafe_onset"
    assert decision["public_event"] == "unsafe_onset"


def test_first_safe_reading_fires_no_event(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path=db_path)

    t0 = IN_SEASON_START
    decision = gating.evaluate_reading(_reading("Safe", t0), db_path=db_path, now=t0)

    assert decision["status"] == "Safe"
    assert decision["agency_event"] is None
    assert decision["public_event"] is None


def test_repeated_unsafe_readings_fire_only_one_event(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path=db_path)

    t0 = IN_SEASON_START
    first = gating.evaluate_reading(_reading("Unsafe", t0), db_path=db_path, now=t0)
    second = gating.evaluate_reading(
        _reading("Unsafe", t0 + timedelta(hours=1)), db_path=db_path, now=t0 + timedelta(hours=1)
    )

    assert first["agency_event"] == "unsafe_onset"
    assert second["status"] == "Unsafe"
    assert second["agency_event"] is None
    assert second["public_event"] is None


def test_all_clear_fires_only_after_48_continuous_safe_hours(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path=db_path)

    t0 = IN_SEASON_START
    gating.evaluate_reading(_reading("Unsafe", t0), db_path=db_path, now=t0)

    safe_start = t0 + timedelta(hours=1)
    gating.evaluate_reading(_reading("Safe", safe_start), db_path=db_path, now=safe_start)

    almost = safe_start + timedelta(hours=47, minutes=59)
    too_soon = gating.evaluate_reading(_reading("Safe", almost), db_path=db_path, now=almost)
    assert too_soon["agency_event"] is None
    assert too_soon["status"] == "Safe"  # raw reading is Safe...
    # ...but the alerted state is still Unsafe until the window completes:
    assert db.get_alert_state(LOCATION, db_path=db_path)["current_tier"] == "Unsafe"

    exactly_48h = safe_start + timedelta(hours=48)
    all_clear = gating.evaluate_reading(_reading("Safe", exactly_48h), db_path=db_path, now=exactly_48h)
    assert all_clear["agency_event"] == "all_clear"
    assert all_clear["public_event"] == "all_clear"
    assert db.get_alert_state(LOCATION, db_path=db_path)["current_tier"] == "Safe"


def test_unsafe_reading_resets_the_all_clear_clock_to_zero(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path=db_path)

    t0 = IN_SEASON_START
    gating.evaluate_reading(_reading("Unsafe", t0), db_path=db_path, now=t0)
    gating.evaluate_reading(
        _reading("Safe", t0 + timedelta(hours=10)), db_path=db_path, now=t0 + timedelta(hours=10)
    )

    # An Unsafe reading here wipes the 10h of progress already made.
    reset_at = t0 + timedelta(hours=20)
    gating.evaluate_reading(_reading("Unsafe", reset_at), db_path=db_path, now=reset_at)

    # The clock only actually starts counting from the next Safe reading after the reset.
    safe_start_after_reset = reset_at + timedelta(hours=1)
    gating.evaluate_reading(
        _reading("Safe", safe_start_after_reset), db_path=db_path, now=safe_start_after_reset
    )

    # 47h59m after THAT - would already have fired (58h59m elapsed) had the original 10h of
    # pre-reset progress survived, so this only passes if the reset actually zeroed it out.
    still_waiting = safe_start_after_reset + timedelta(hours=47, minutes=59)
    decision = gating.evaluate_reading(_reading("Safe", still_waiting), db_path=db_path, now=still_waiting)
    assert decision["agency_event"] is None

    exactly_48h_after_reset = safe_start_after_reset + timedelta(hours=48)
    all_clear = gating.evaluate_reading(
        _reading("Safe", exactly_48h_after_reset), db_path=db_path, now=exactly_48h_after_reset
    )
    assert all_clear["agency_event"] == "all_clear"


def test_stale_reading_pauses_the_clock_without_resetting_progress(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path=db_path)

    t0 = IN_SEASON_START
    gating.evaluate_reading(_reading("Unsafe", t0), db_path=db_path, now=t0)

    safe_start = t0 + timedelta(hours=1)
    gating.evaluate_reading(_reading("Safe", safe_start), db_path=db_path, now=safe_start)

    # 27h of Safe progress banked, then the feed goes stale (age > 2h at evaluation time).
    stale_time = safe_start + timedelta(hours=27)
    stale_decision = gating.evaluate_reading(
        _reading("Unsafe", stale_time),  # risk_tier is irrelevant while stale
        db_path=db_path,
        now=stale_time + timedelta(hours=3),
    )
    assert stale_decision["status"] == "unavailable"
    assert stale_decision["agency_event"] is None

    # Fresh again, one hour after the stale reading - if the pause had instead reset
    # progress to zero, 21 more hours here would not be enough to reach 48h.
    resume_time = stale_time + timedelta(hours=1)
    decision = gating.evaluate_reading(_reading("Safe", resume_time), db_path=db_path, now=resume_time)
    assert decision["agency_event"] is None  # only 27h banked + 0h so far

    twenty_one_more_hours = resume_time + timedelta(hours=21)  # 27h banked + 21h = 48h
    all_clear = gating.evaluate_reading(
        _reading("Safe", twenty_one_more_hours), db_path=db_path, now=twenty_one_more_hours
    )
    assert all_clear["agency_event"] == "all_clear"


def test_season_gate_suppresses_public_but_not_agency(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path=db_path)

    t0 = OUT_OF_SEASON_START
    decision = gating.evaluate_reading(_reading("Unsafe", t0), db_path=db_path, now=t0)

    assert decision["agency_event"] == "unsafe_onset"
    assert decision["public_event"] is None


def test_stale_input_never_produces_an_all_clear(tmp_path):
    """Fail-closed guarantee from plan.md's Definition of Done: a stale input never
    produces a message, and never an all-clear, no matter how close the window is."""
    db_path = tmp_path / "test.db"
    db.init_db(db_path=db_path)

    t0 = IN_SEASON_START
    gating.evaluate_reading(_reading("Unsafe", t0), db_path=db_path, now=t0)
    safe_start = t0 + timedelta(hours=1)
    gating.evaluate_reading(_reading("Safe", safe_start), db_path=db_path, now=safe_start)

    # This reading's own timestamp is 48h past the streak start (the window is technically
    # "due"), but it is being evaluated stale (fetched late) - it must not fire anything.
    right_at_the_window = safe_start + timedelta(hours=48)
    decision = gating.evaluate_reading(
        _reading("Safe", right_at_the_window),
        db_path=db_path,
        now=right_at_the_window + timedelta(hours=3),
    )

    assert decision["status"] == "unavailable"
    assert decision["agency_event"] is None
    assert decision["public_event"] is None
