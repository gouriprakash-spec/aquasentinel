"""Rainfall-only rules fallback.

Used when no USGS gauge reading is available for a day but rainfall is (per
DATA-DICTIONARY.md, this covers the 20 proxy-less rows held out of both regimes).
Deliberately a single threshold rule, not a fitted model - it's meant to be transparent
and disclosed, in keeping with docs/product-brief.md's "no invented numbers" rule.

The threshold itself is derived from data by app/model/train.py and must be copied into
app/config.py by hand before this module can classify anything (see config.py's TODO).
"""

from __future__ import annotations

from app import config


class RainFallbackNotConfigured(RuntimeError):
    """Raised when RAIN_FALLBACK_THRESHOLD_MM hasn't been derived and set yet."""


def classify_by_rainfall(precip_prev_48h_mm: float) -> dict:
    """Classify Safe/Unsafe from prior-48h rainfall alone.

    Fails closed: raises rather than guessing a threshold if config isn't set yet.
    """
    if config.RAIN_FALLBACK_THRESHOLD_MM is None:
        raise RainFallbackNotConfigured(
            "RAIN_FALLBACK_THRESHOLD_MM is not set in app/config.py. Run "
            "`python -m app.model.train` to derive it, then copy the value in."
        )

    is_unsafe = precip_prev_48h_mm >= config.RAIN_FALLBACK_THRESHOLD_MM
    return {
        "risk_tier": "Unsafe" if is_unsafe else "Safe",
        "source": "rules_fallback",
        "basis": f"precip_prev_48h_mm={precip_prev_48h_mm} vs threshold={config.RAIN_FALLBACK_THRESHOLD_MM}",
    }
