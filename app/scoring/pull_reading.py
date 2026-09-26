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
from app.ingestion.nws import RainfallUnavailable
from app.ingestion.nws import fetch_antecedent_rainfall as fetch_nws_rainfall
from app.ingestion.usgs import fetch_usgs_proxies

ARTIFACTS_DIR = Path(__file__).resolve().parents[1] / "model" / "artifacts"
MODEL_PATH = ARTIFACTS_DIR / "rf_B_post2021.joblib"

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
    """Fetch live data, score it, and return the shared signal contract."""
    proxies = fetch_usgs_proxies()
    rainfall, rainfall_source = _fetch_rainfall()

    bundle = _load_model()
    feature_values = _build_feature_vector(proxies, rainfall, bundle["features"])

    model = bundle["model"]
    probability_unsafe = float(model.predict_proba([feature_values])[0][1])
    is_unsafe = probability_unsafe >= 0.5  # same rule used to compute precision/recall in training
    risk_tier = "Unsafe" if is_unsafe else "Safe"
    confidence = probability_unsafe if is_unsafe else (1.0 - probability_unsafe)

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
        },
        "threshold_cfu_100ml": config.UNSAFE_THRESHOLD_CFU_100ML,
        "model_version": "rf_B_post2021",
        "regime": "B_post2021",
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


def _build_feature_vector(proxies: dict, rainfall: dict, features_order: list[str]) -> list[float]:
    # Live scoring gives one instantaneous turbidity reading; the model trained on daily
    # mean/max. Using the live value for both is the best available live approximation -
    # documented in BUILD-SPEC.md as a disclosed limitation.
    turbidity = proxies["turbidity_fnu"].value
    values = {
        "water_temp_c": proxies["water_temp_c"].value,
        "sp_conductance_uscm": proxies["sp_conductance_uscm"].value,
        "dissolved_oxygen_mgl": proxies["dissolved_oxygen_mgl"].value,
        "ph": proxies["ph"].value,
        "precip_mm": rainfall["precip_mm"],
        "precip_prev_24h_mm": rainfall["precip_prev_24h_mm"],
        "turbidity_fnu_mean": turbidity,
        "turbidity_fnu_max": turbidity,
    }
    return [values[name] for name in features_order]
