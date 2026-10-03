"""Score a live reading: fetch USGS proxies + NWS rainfall (falling back to Open-Meteo if
NWS's own observation is unavailable or ambiguous - see _fetch_rainfall), run the trained
model, and decide the Safe/Unsafe tier in deterministic code - never the model itself.

Scope note (milestone 2, plan.md): this assembles and scores ONE live reading and returns
the shared signal contract from docs/product-brief.md:
    { location, time, risk_tier, confidence, source, source_url, retrieved_at, evidence }
It does NOT yet apply the alert-rules gating (freshness -> "unavailable", change-of-state,
48h all-clear, season gate) - that's milestone 3, layered on top of this function's output
using the per-proxy timestamps it returns in `evidence`.

`estimate_cfu_100ml` is deliberately NOT included in the output. The shipped model is a
classifier (Safe/Unsafe + a confidence), not a CFU regressor, and the architecture rules
say never to show a bacteria value the system does not have.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import joblib

from app import config
from app.ingestion import open_meteo
from app.ingestion.csocast import CsoDataUnavailable
from app.ingestion.csocast import fetch_nearby_outfalls as fetch_cso_outfalls
from app.ingestion.nws import PartialRainfall, RainfallUnavailable
from app.ingestion.nws import fetch_antecedent_rainfall as fetch_nws_rainfall
from app.ingestion.usgs import UsgsDataUnavailable, fetch_usgs_proxies
from app.model.cso_rule import apply_cso_escalation, classify_cso_state
from app.model.rules_fallback import classify_by_rainfall

ARTIFACTS_DIR = Path(__file__).resolve().parents[1] / "model" / "artifacts"
MODEL_PATH = ARTIFACTS_DIR / "rf_nearshore.joblib"  # changed from rf_B_post2021.joblib - the
# channel-trained model is retired from live scoring entirely (spec Section 5): it showed no
# real out-of-sample skill (Milestone 1b), so it must not be resurrected even as a fallback.

LOCATION_ID = "penns_landing"
LOCATION_NAME = "Penn's Landing, Center City tidal Delaware"
SOURCE_NAME = "aquasentinel"
SOURCE_URL = "https://waterservices.usgs.gov/nwis/iv/?sites=01467200"

_model_bundle: dict | None = None  # lazy-loaded, cached at module level

logger = logging.getLogger(__name__)


def _load_model() -> dict:
    global _model_bundle
    if _model_bundle is None:
        _model_bundle = joblib.load(MODEL_PATH)
    return _model_bundle


def pull_reading() -> dict:
    """Fetch live data, score it, and return the shared signal contract.

    Milestone 1b (2026-09-27): the rainfall rule decides risk_tier; the near-shore-trained
    model only informs confidence (how much it agrees with the rule), never the tier itself.
    See docs/superpowers/specs/2026-09-27-model-honesty-fix-milestone1b-design.md Section 3.

    Gauge unavailable (Gouri, 2026-10-03): the gauge only ever fed the model, so when USGS has no
    CURRENT reading (an error, or data older than config.FRESHNESS_LIMIT_HOURS) the pull still
    returns a reading. The tier comes from the rainfall rule and the overflow rule exactly as
    before; the water-quality values and the confidence (rule/model agreement) are None, because
    the model cannot run without them; and the reading's time is the pull time. Missing RAINFALL
    still raises (no rainfall = no tier = no reading).
    """
    pulled_at = datetime.now(timezone.utc)
    proxies = _fetch_current_gauge_proxies(pulled_at)  # None = no current gauge reading
    rainfall, rainfall_source, rainfall_missing_hours = _fetch_rainfall()

    decision = classify_by_rainfall(rainfall["precip_prev_48h_mm"])
    risk_tier = decision["risk_tier"]

    if proxies is not None:
        bundle = _load_model()
        feature_values = _build_feature_vector(proxies, rainfall, bundle["features"])
        model = bundle["model"]
        probability_unsafe = float(model.predict_proba([feature_values])[0][1])
        # Confidence: how much the model agrees with the rule's decision. If the rule says
        # Unsafe, a high probability_unsafe from the model IS agreement; if the rule says Safe,
        # a LOW probability_unsafe is agreement - same shape the old model-decides confidence
        # formula used, just now measuring agreement with the rule instead of the model's own
        # certainty in its own decision.
        confidence = probability_unsafe if risk_tier == "Unsafe" else (1.0 - probability_unsafe)
    else:
        probability_unsafe = None
        confidence = None

    nearby_outfalls = _fetch_cso_signal()
    escalation = apply_cso_escalation(risk_tier, confidence, nearby_outfalls or [])
    cso_state = classify_cso_state(nearby_outfalls)
    risk_tier = escalation["risk_tier"]
    confidence = escalation["confidence"]
    decision_basis = escalation["decision_basis"] or "rainfall_rule"
    cso_status = None
    if escalation["triggered_outfall"] is not None:
        outfall = escalation["triggered_outfall"]
        cso_status = {
            "outfall_name": outfall.name,
            "status": outfall.status,
            "distance_km": round(outfall.distance_km, 2),
            "last_poll": outfall.last_poll.isoformat(),
        }

    gauge_available = proxies is not None
    # The reading's time is when the DATA is from: the gauge's oldest measurement when we have
    # one, otherwise the pull itself (rainfall and CSOcast are real-time as of this pull).
    reading_time = (
        min(reading.retrieved_at for reading in proxies.values()) if gauge_available else pulled_at
    )

    return {
        "location": LOCATION_ID,
        "location_name": LOCATION_NAME,
        "time": reading_time.isoformat(),
        "risk_tier": risk_tier,
        "confidence": None if confidence is None else round(confidence, 3),
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "retrieved_at": pulled_at.isoformat(),
        "evidence": {
            "gauge_available": gauge_available,
            "proxies": (
                {name: reading.value for name, reading in proxies.items()} if gauge_available else {}
            ),
            "proxy_timestamps": (
                {name: reading.retrieved_at.isoformat() for name, reading in proxies.items()}
                if gauge_available else {}
            ),
            "rainfall_mm": rainfall,
            "rainfall_source": rainfall_source,
            # 0 = the rain totals are exact. Above 0 = that many hourly NWS reports in the rule's
            # two-previous-days window could not be resolved, so precip_prev_48h_mm is "at least".
            "rainfall_missing_hours": rainfall_missing_hours,
            "decision_basis": decision_basis,
            "rule_threshold_mm": config.RAIN_FALLBACK_THRESHOLD_MM,
            "model_probability_unsafe": (
                None if probability_unsafe is None else round(probability_unsafe, 3)
            ),
            "cso_status": cso_status,
            "cso": cso_state,
            # Every outfall the rule considered, with coordinates, for the dashboard map (saved by
            # the scheduled pull). None means the feed could not be read - NOT "nothing nearby" -
            # so the previous snapshot is kept instead of being wiped.
            "cso_outfalls": None if nearby_outfalls is None else [
                {
                    "name": outfall.name,
                    "status": outfall.status,
                    "distance_km": round(outfall.distance_km, 2),
                    "last_poll": outfall.last_poll.isoformat(),
                    "latitude": outfall.latitude,
                    "longitude": outfall.longitude,
                }
                for outfall in nearby_outfalls
            ],
        },
        "threshold_cfu_100ml": config.UNSAFE_THRESHOLD_CFU_100ML,
        "model_version": "rf_nearshore" if gauge_available else "rainfall_rule_only",
        "regime": "nearshore" if gauge_available else "no_gauge",
        "kind": "model_estimate",
    }


def _fetch_current_gauge_proxies(now: datetime) -> dict | None:
    """The USGS gauge proxies, or None when the gauge has no CURRENT reading: the request failed,
    or its newest data is older than config.FRESHNESS_LIMIT_HOURS. Never silent: the reason is
    logged. (Returning None, not raising, is the point - see pull_reading's docstring.)"""
    try:
        proxies = fetch_usgs_proxies()
    except UsgsDataUnavailable as exc:
        logger.warning("USGS gauge unavailable (%s); continuing without gauge data", exc)
        return None
    oldest = min(reading.retrieved_at for reading in proxies.values())
    if now - oldest > timedelta(hours=config.FRESHNESS_LIMIT_HOURS):
        logger.warning(
            "USGS gauge data is older than the freshness limit (its newest values are from %s); "
            "continuing without gauge data", oldest.isoformat(),
        )
        return None
    return proxies


def _fetch_rainfall() -> tuple[dict, str, int]:
    """(rainfall, source, missing_hours). NWS (a physical station) is primary. Open-Meteo (a
    forecast model) is only used when NWS's own observation is unavailable or ambiguous - see
    app.ingestion.nws.RainfallUnavailable's three causes. If Open-Meteo also fails, this
    still raises RainfallUnavailable and the caller fails closed, unchanged from before.

    Lower bound (Gouri, 2026-10-03): when NWS could resolve only SOME of the hours in the rule's
    two-previous-days window, the hours it did resolve are a floor (missing hours can only add
    rain). If that floor already makes the rule say Unsafe, the NWS floor decides - Open-Meteo
    measured 0.0 mm over a window where NWS had already shown 4.6 mm. If the floor is below the
    threshold the missing hours could still push it over, so nothing can be certified from it
    and it falls back to Open-Meteo exactly as before. This path can only produce Unsafe, never
    a Safe. The model's other rain features (today, last 24h) still come from Open-Meteo.
    """
    try:
        return fetch_nws_rainfall(), "nws", 0
    except PartialRainfall as partial:
        fallback = open_meteo.fetch_antecedent_rainfall()
        # Same rule function that decides the tier, so the threshold is never copied here.
        if classify_by_rainfall(partial.known_mm)["risk_tier"] == "Unsafe":
            logger.warning(
                "NWS rain is partial (%d hourly reports unresolved); the %.1f mm that did resolve "
                "already reaches the rule's threshold, so it decides Unsafe", partial.missing_hours, partial.known_mm,
            )
            return {**fallback, "precip_prev_48h_mm": partial.known_mm}, "nws-partial", partial.missing_hours
        return fallback, "open-meteo", 0
    except RainfallUnavailable:
        return open_meteo.fetch_antecedent_rainfall(), "open-meteo", 0


def _fetch_cso_signal() -> list | None:
    """CSOcast is an escalation-only add-on, not a required input (spec Scope decision 5): a
    total outage must not fail the reading closed the way a stale USGS gauge does - it just
    means no CSO signal this cycle, with no effect on the tier.

    Returns None (not []) on an outage, so the CSO field can say "Reading unavailable" for a
    feed that failed instead of treating it like "feed answered, nothing nearby".
    """
    try:
        return fetch_cso_outfalls()
    except CsoDataUnavailable:
        return None


def _build_feature_vector(proxies: dict, rainfall: dict, features_order: list[str]) -> list[float]:
    # Turbidity is still fetched (proxies["turbidity_fnu"]) and shown in evidence.proxies -
    # it just isn't one of the near-shore model's 6 features (see app/model/train.py's
    # NEARSHORE_FEATURES comment for why it was dropped as a model input).
    values = {
        "water_temp_c": proxies["water_temp_c"].value,
        "sp_conductance_uscm": proxies["sp_conductance_uscm"].value,
        "dissolved_oxygen_mgl": proxies["dissolved_oxygen_mgl"].value,
        "ph": proxies["ph"].value,
        "precip_mm": rainfall["precip_mm"],
        "precip_prev_24h_mm": rainfall["precip_prev_24h_mm"],
    }
    return [values[name] for name in features_order]
