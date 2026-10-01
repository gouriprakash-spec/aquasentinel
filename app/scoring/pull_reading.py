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

from datetime import datetime, timezone
from pathlib import Path

import joblib

from app import config
from app.ingestion import open_meteo
from app.ingestion.csocast import CsoDataUnavailable
from app.ingestion.csocast import fetch_nearby_outfalls as fetch_cso_outfalls
from app.ingestion.nws import RainfallUnavailable
from app.ingestion.nws import fetch_antecedent_rainfall as fetch_nws_rainfall
from app.ingestion.usgs import fetch_usgs_proxies
from app.model.cso_rule import apply_cso_escalation
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
    """
    proxies = fetch_usgs_proxies()
    rainfall, rainfall_source = _fetch_rainfall()

    bundle = _load_model()
    decision = classify_by_rainfall(rainfall["precip_prev_48h_mm"])
    risk_tier = decision["risk_tier"]

    feature_values = _build_feature_vector(proxies, rainfall, bundle["features"])
    model = bundle["model"]
    probability_unsafe = float(model.predict_proba([feature_values])[0][1])
    # Confidence: how much the model agrees with the rule's decision. If the rule says
    # Unsafe, a high probability_unsafe from the model IS agreement; if the rule says Safe,
    # a LOW probability_unsafe is agreement - same shape the old model-decides confidence
    # formula used, just now measuring agreement with the rule instead of the model's own
    # certainty in its own decision.
    confidence = probability_unsafe if risk_tier == "Unsafe" else (1.0 - probability_unsafe)

    nearby_outfalls = _fetch_cso_signal()
    escalation = apply_cso_escalation(risk_tier, confidence, nearby_outfalls)
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

    oldest_proxy_time = min(reading.retrieved_at for reading in proxies.values())

    return {
        "location": LOCATION_ID,
        "location_name": LOCATION_NAME,
        "time": oldest_proxy_time.isoformat(),
        "risk_tier": risk_tier,
        "confidence": round(confidence, 3),
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "evidence": {
            "proxies": {name: reading.value for name, reading in proxies.items()},
            "proxy_timestamps": {
                name: reading.retrieved_at.isoformat() for name, reading in proxies.items()
            },
            "rainfall_mm": rainfall,
            "rainfall_source": rainfall_source,
            "decision_basis": decision_basis,
            "rule_threshold_mm": config.RAIN_FALLBACK_THRESHOLD_MM,
            "model_probability_unsafe": round(probability_unsafe, 3),
            "cso_status": cso_status,
        },
        "threshold_cfu_100ml": config.UNSAFE_THRESHOLD_CFU_100ML,
        "model_version": "rf_nearshore",
        "regime": "nearshore",
        "kind": "model_estimate",
    }


def _fetch_rainfall() -> tuple[dict, str]:
    """NWS (a physical station) is primary. Open-Meteo (a forecast model) is only used
    when NWS's own observation is unavailable or ambiguous - see
    app.ingestion.nws.RainfallUnavailable's three causes. If Open-Meteo also fails, this
    still raises RainfallUnavailable and the caller fails closed, unchanged from before.
    """
    try:
        return fetch_nws_rainfall(), "nws"
    except RainfallUnavailable:
        return open_meteo.fetch_antecedent_rainfall(), "open-meteo"


def _fetch_cso_signal() -> list:
    """CSOcast is an escalation-only add-on, not a required input (spec Scope decision 5): a
    total outage must not fail the reading closed the way a stale USGS gauge does - it just
    means no CSO signal this cycle, identical in effect to every nearby outfall being stale.
    """
    try:
        return fetch_cso_outfalls()
    except CsoDataUnavailable:
        return []


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
