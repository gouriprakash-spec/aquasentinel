"""Pure CSO-overflow escalation rule - no I/O. Given the already radius/freshness-filtered
outfalls from app.ingestion.csocast, decides whether any of them forces Safe -> Unsafe.

One-directional only (spec's Scope decision 4): this can escalate Safe to Unsafe, never the
reverse, and when nothing qualifies it has no opinion at all - the caller's existing tier and
confidence (from the rainfall rule / near-shore model, Milestone 1b) pass through untouched.
"""

from __future__ import annotations

from app import config
from app.ingestion.csocast import OutfallReading


def apply_cso_escalation(
    risk_tier: str, confidence: float, nearby_outfalls: list[OutfallReading]
) -> dict:
    """Return {risk_tier, confidence, decision_basis, triggered_outfall}.

    decision_basis is "cso_overflow_rule" when an outfall triggered, else None - the caller
    keeps whatever decision_basis it already had (e.g. "rainfall_rule") in that case.
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
        "confidence": config.CSO_OVERRIDE_CONFIDENCE,
        "decision_basis": "cso_overflow_rule",
        "triggered_outfall": triggered,
    }
