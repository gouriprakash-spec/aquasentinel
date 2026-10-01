"""Tests for app/model/cso_rule.py - pure escalation logic, no I/O.

Per docs/superpowers/specs/2026-10-01-cso-overflow-rule-milestone6-design.md's Scope
decision 4: one-directional only. This can force Safe -> Unsafe, never the reverse, and when
nothing qualifies it must leave risk_tier/confidence completely untouched.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app import config
from app.ingestion.csocast import OutfallReading
from app.model.cso_rule import apply_cso_escalation

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _outfall(status: int, name: str = "D_test", distance_km: float = 1.0) -> OutfallReading:
    return OutfallReading(name=name, status=status, distance_km=distance_km, last_poll=NOW)


def test_escalates_safe_to_unsafe_when_an_outfall_is_overflowing():
    result = apply_cso_escalation("Safe", 0.9, [_outfall(status=4)])

    assert result["risk_tier"] == "Unsafe"
    assert result["confidence"] == config.CSO_OVERRIDE_CONFIDENCE
    assert result["decision_basis"] == "cso_overflow_rule"
    assert result["triggered_outfall"].name == "D_test"


def test_status_3_recent_overflow_triggers_the_same_as_status_4_active():
    result = apply_cso_escalation("Safe", 0.9, [_outfall(status=3)])

    assert result["risk_tier"] == "Unsafe"
    assert result["decision_basis"] == "cso_overflow_rule"


def test_any_one_qualifying_outfall_is_enough_not_a_majority():
    outfalls = [
        _outfall(status=1, name="D_clean_1"),
        _outfall(status=1, name="D_clean_2"),
        _outfall(status=3, name="D_trigger"),
    ]
    result = apply_cso_escalation("Safe", 0.9, outfalls)

    assert result["risk_tier"] == "Unsafe"
    assert result["triggered_outfall"].name == "D_trigger"


def test_does_not_escalate_when_no_outfall_qualifies():
    outfalls = [_outfall(status=1, name="D_1"), _outfall(status=0, name="D_2")]
    result = apply_cso_escalation("Safe", 0.9, outfalls)

    assert result["risk_tier"] == "Safe"
    assert result["confidence"] == 0.9
    assert result["decision_basis"] is None
    assert result["triggered_outfall"] is None


def test_empty_outfall_list_leaves_everything_unchanged():
    result = apply_cso_escalation("Unsafe", 0.65, [])

    assert result["risk_tier"] == "Unsafe"
    assert result["confidence"] == 0.65
    assert result["decision_basis"] is None
    assert result["triggered_outfall"] is None


def test_triggering_on_an_already_unsafe_reading_still_overwrites_confidence_and_basis():
    """One-directional escalation: CSO can't change an already-Unsafe tier, but Milestone 8's
    sampling trigger reads confidence, not tier - confidence/decision_basis must still reflect
    that CSO is what's actually current, even though the tier value itself doesn't change."""
    result = apply_cso_escalation("Unsafe", 0.91, [_outfall(status=4)])

    assert result["risk_tier"] == "Unsafe"  # unchanged value...
    assert result["confidence"] == config.CSO_OVERRIDE_CONFIDENCE  # ...but overwritten anyway
    assert result["decision_basis"] == "cso_overflow_rule"
