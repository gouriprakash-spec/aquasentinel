"""Pure CSO-overflow escalation rule - no I/O. Given the already radius/freshness-filtered
outfalls from app.ingestion.csocast, decides whether any of them forces Safe -> Unsafe.

One-directional only (spec's Scope decision 4): this can escalate Safe to Unsafe, never the
reverse, and when nothing qualifies it has no opinion at all - the caller's existing tier and
confidence (from the rainfall rule / near-shore model, Milestone 1b) pass through untouched.
"""

from __future__ import annotations

from app import config
from app.ingestion.csocast import OutfallReading

# The three values of the dashboard/API "CSO" field. Exact wording decided with Gouri.
CSO_OVERFLOW = "Overflow"
CSO_NO_OVERFLOW = "No overflow"
CSO_UNAVAILABLE = "Reading unavailable"


def classify_cso_state(nearby_outfalls: list[OutfallReading] | None) -> str:
    """The value of the CSO field, decided in code (architecture rule: code decides).

    `nearby_outfalls` is the radius/freshness-filtered list, or None when the CSOcast feed
    could not be fetched at all. "No overflow" is claimed only when a fresh nearby outfall
    positively reports it - an unreachable feed, an empty list (no fresh nearby outfall), or
    only "no data" outfalls all say "Reading unavailable", never a made-up all-clear.
    """
    if nearby_outfalls is None:
        return CSO_UNAVAILABLE
    if any(outfall.status in config.CSO_TRIGGER_STATUSES for outfall in nearby_outfalls):
        return CSO_OVERFLOW
    if any(outfall.status == config.CSO_NO_OVERFLOW_STATUS for outfall in nearby_outfalls):
        return CSO_NO_OVERFLOW
    return CSO_UNAVAILABLE


def apply_cso_escalation(
    risk_tier: str, confidence: float, nearby_outfalls: list[OutfallReading]
) -> dict:
    """Return {risk_tier, confidence, decision_basis, triggered_outfall}.

    decision_basis is "cso_overflow_rule" when an outfall triggered, else None - the caller
    keeps whatever decision_basis it already had (e.g. "rainfall_rule") in that case.

    `confidence` is passed through UNCHANGED. It is the rule/model agreement, and it must show
    exactly that: an overflow changes the tier and the stated reason, never the agreement figure
    (Gouri, 2026-10-03). It used to be overwritten with a fixed placeholder (0.3) so a
    Milestone 8 sampling request would trigger; that feature was cut, and the placeholder only
    ever misled readers into thinking it measured something.
    """
    triggered = next(
        (outfall for outfall in nearby_outfalls if outfall.status in config.CSO_TRIGGER_STATUSES),
        None,
    )

    if triggered is None:
        return {
            "risk_tier": risk_tier,
            "confidence": confidence,
            "decision_basis": None,
            "triggered_outfall": None,
        }

    return {
        "risk_tier": "Unsafe",
        "confidence": confidence,
        "decision_basis": "cso_overflow_rule",
        "triggered_outfall": triggered,
    }
