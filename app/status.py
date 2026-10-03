"""Shared status-contract builder - reshapes a stored reading row into the documented
contract shape, used identically by GET /api/status and the MCP server's tools (Milestone
5), so they can never disagree about what a reading looks like.

Per docs/superpowers/specs/2026-09-27-agent-ready-layer-milestone5-design.md: deliberately
excludes estimate_cfu_100ml (architecture rule: never show a bacteria value the system does
not have) and location_name (not stored per-row in app/db.py's readings table - omitted
rather than invented).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import config
from app.scoring.pull_reading import SOURCE_NAME, SOURCE_URL


def build_status_contract(row: dict) -> dict:
    # No current USGS reading for this pull (2026-10-03): the stored confidence is only a NOT NULL
    # placeholder, so it is reported as None (n/a), never as a number. The water-quality values in
    # `proxies` are already None for such a row. Rows from before the column existed had gauge data.
    gauge_available = bool(row.get("gauge_available", 1))
    cso_status = None
    if row["cso_outfall_name"] is not None:
        cso_status = {
            "outfall_name": row["cso_outfall_name"],
            "status": row["cso_outfall_status"],
            "distance_km": row["cso_distance_km"],
            "last_poll": row["cso_last_poll"],
        }
    return {
        "location": row["location"],
        "time": row["reading_time"],
        "risk_tier": row["risk_tier"],
        "confidence": row["confidence"] if gauge_available else None,
        "gauge_available": gauge_available,
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "retrieved_at": row["retrieved_at"],
        "threshold_cfu_100ml": row["threshold_cfu_100ml"],
        "model_version": row["model_version"],
        "regime": row["regime"],
        # Milestone 1b: what decided risk_tier, so an agent or person reading status can
        # audit the decision, not just see its outcome. The rainfall rule decides
        # (precip_prev_48h_mm >= rule_threshold_mm -> Unsafe); model_probability_unsafe
        # only informs confidence, which measures rule/model agreement.
        "decision_basis": row["decision_basis"],
        "rule_threshold_mm": row["rule_threshold_mm"],
        "rainfall_source": row["rainfall_source"],
        # Above 0: that many hourly NWS reports in the rule's two-previous-days window were
        # unavailable, so proxies.precip_prev_48h_mm is a lower bound ("at least"), not an exact total.
        "rainfall_missing_hours": row.get("rainfall_missing_hours", 0),
        "model_probability_unsafe": row["model_probability_unsafe"],
        "cso_status": cso_status,
        # "Overflow" / "No overflow" / "Reading unavailable". A row with no stored value
        # (stored before this field existed) reads as unavailable - never as "No overflow".
        "cso": row.get("cso_state") or "Reading unavailable",
        "proxies": {
            "water_temp_c": row["water_temp_c"],
            "sp_conductance_uscm": row["sp_conductance_uscm"],
            "dissolved_oxygen_mgl": row["dissolved_oxygen_mgl"],
            "ph": row["ph"],
            "turbidity_fnu": row["turbidity_fnu"],
            "precip_mm": row["precip_mm"],
            "precip_prev_24h_mm": row["precip_prev_24h_mm"],
            "precip_prev_48h_mm": row["precip_prev_48h_mm"],
        },
        "kind": "model_estimate",
    }


def current_status(row: dict | None) -> dict:
    """The single decision point for "what is the current status" - used identically by
    GET /api/status and the MCP server's get_current_status tool, so publishing can never
    disagree with app.alerts.gating.evaluate_reading's decision for the same row.

    Per CLAUDE.md's non-negotiable "Fail closed" rule: a gauge reading older than
    config.FRESHNESS_LIMIT_HOURS is "status unavailable", never an all-clear - the same
    freshness check gating applies, applied here too so a stale reading is never published
    as current just because no fresher one has arrived yet (there is no background scheduler
    to guarantee that; see plan.md's Open Questions).
    """
    if row is None:
        return {"status": "unavailable", "reason": "no readings yet"}

    reading_time = datetime.fromisoformat(row["reading_time"])
    if reading_time.tzinfo is None:
        reading_time = reading_time.replace(tzinfo=timezone.utc)

    if datetime.now(timezone.utc) - reading_time > timedelta(hours=config.FRESHNESS_LIMIT_HOURS):
        return {
            "status": "unavailable",
            "reason": "latest reading is stale (older than the freshness limit)",
        }

    return build_status_contract(row)


# CSOcast's own status codes in plain language, for the dashboard map. Display only - the
# overflow rule itself works from the codes (config.CSO_TRIGGER_STATUSES). An unknown code is
# shown as such, never mapped to something reassuring.
CSO_STATUS_TEXT = {
    0: "No data",
    1: "No overflow in the past 72 hours",
    3: "Overflow in the past 72 hours",
    4: "Overflowing now",
}


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def annotate_freshness(rows: list[dict], now: datetime | None = None) -> list[dict]:
    """Copies of newest-first stored rows, each tagged with `gauge_age_hours` and `stale`.

    Lets the dashboard flag a stale reading without hard-coding the limit in JavaScript (the
    server decides, from config.FRESHNESS_LIMIT_HOURS). Two deliberate rules:
    - The NEWEST row is judged against now - exactly what current_status() does, so a "stale"
      note on the banner and an "unavailable" status can never disagree.
    - Older rows are judged against when they were CHECKED (retrieved_at). Judged against now,
      every historical row would read stale just because time has passed.
    A timestamp that cannot be read fails closed: stale, with no age.
    """
    now = now or datetime.now(timezone.utc)
    limit = timedelta(hours=config.FRESHNESS_LIMIT_HOURS)
    annotated = []
    for index, row in enumerate(rows):
        copy = dict(row)
        # No gauge data: the stored confidence is a placeholder, so say n/a instead of a number.
        copy["gauge_available"] = bool(row.get("gauge_available", 1))
        if not copy["gauge_available"]:
            copy["confidence"] = None
        try:
            reading_time = _parse_utc(row["reading_time"])
            judged_at = now if index == 0 else _parse_utc(row["retrieved_at"])
            age = max(judged_at - reading_time, timedelta(0))  # clock skew never gives a negative age
            copy["gauge_age_hours"] = round(age.total_seconds() / 3600, 1)
            copy["stale"] = age > limit
        except (KeyError, TypeError, ValueError):
            copy["gauge_age_hours"] = None
            copy["stale"] = True
        annotated.append(copy)
    return annotated
