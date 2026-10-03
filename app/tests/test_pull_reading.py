"""Tests for app/scoring/pull_reading.py - the wiring between ingestion and the model.

Model accuracy itself is tested in test_model.py; this file checks that live proxies and
rainfall get assembled into the right shape and that the output matches the shared signal
contract from docs/product-brief.md:
    { location, time, risk_tier, confidence, source, source_url, retrieved_at, evidence }
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import config
from app.ingestion import open_meteo
from app.ingestion.csocast import CsoDataUnavailable, OutfallReading
from app.ingestion.nws import RainfallUnavailable
from app.ingestion.usgs import ProxyReading
from app.scoring import pull_reading as pr


def _fake_proxies(age_hours: float = 0.25) -> dict:
    # Relative to now: a gauge reading older than config.FRESHNESS_LIMIT_HOURS is treated as "no
    # gauge data" (2026-10-03), so a fixed past timestamp would silently turn every test into one.
    now = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    return {
        "water_temp_c": ProxyReading(21.5, now, "P"),
        "sp_conductance_uscm": ProxyReading(269.0, now, "P"),
        "dissolved_oxygen_mgl": ProxyReading(6.6, now, "P"),
        "ph": ProxyReading(7.3, now, "P"),
        "turbidity_fnu": ProxyReading(5.5, now, "P"),
    }


def _fake_rainfall(precip_prev_48h_mm: float = 0.0) -> dict:
    return {"precip_mm": 0.0, "precip_prev_24h_mm": 2.0, "precip_prev_48h_mm": precip_prev_48h_mm}


def test_pull_reading_returns_the_shared_signal_contract(monkeypatch):
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall())
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [])

    reading = pr.pull_reading()

    for key in (
        "location", "time", "risk_tier", "confidence",
        "source", "source_url", "retrieved_at", "evidence",
    ):
        assert key in reading

    assert reading["risk_tier"] in ("Safe", "Unsafe")
    assert 0.5 <= reading["confidence"] <= 1.0
    assert reading["source"] == "aquasentinel"
    assert reading["threshold_cfu_100ml"] == 235
    # Architecture rule: never show a bacteria value the system does not have.
    assert "estimate_cfu_100ml" not in reading


def test_evidence_carries_the_raw_proxies_and_rainfall(monkeypatch):
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall())
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [])

    reading = pr.pull_reading()

    assert reading["evidence"]["proxies"]["turbidity_fnu"] == 5.5
    assert reading["evidence"]["rainfall_mm"]["precip_prev_24h_mm"] == 2.0
    assert reading["evidence"]["rainfall_source"] == "nws"


def test_falls_back_to_open_meteo_when_nws_rainfall_is_unavailable(monkeypatch):
    def _raise():
        raise RainfallUnavailable("no usable NWS observation")

    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", _raise)
    monkeypatch.setattr(open_meteo, "fetch_antecedent_rainfall", lambda: _fake_rainfall())
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [])

    reading = pr.pull_reading()

    assert reading["evidence"]["rainfall_source"] == "open-meteo"
    assert reading["evidence"]["rainfall_mm"]["precip_prev_24h_mm"] == 2.0


def test_uses_the_oldest_proxy_reading_as_the_reading_time(monkeypatch):
    # Older than the rest but still inside the 2-hour freshness limit (an older straggler would
    # make the whole gauge count as unavailable - see test_gauge_unavailable.py).
    stale = datetime.now(timezone.utc) - timedelta(hours=1)
    proxies = _fake_proxies()
    proxies["ph"] = ProxyReading(7.3, stale, "P")  # one straggler, older than the rest

    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: proxies)
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall())
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [])

    reading = pr.pull_reading()

    assert reading["time"] == stale.isoformat()


def test_the_rainfall_rule_decides_the_tier_not_the_model(monkeypatch):
    """Core of the pivot: an obviously-Unsafe rainfall total must produce Unsafe regardless
    of what the model's own probability happens to be."""
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall(precip_prev_48h_mm=50.0))
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [])

    reading = pr.pull_reading()

    assert reading["risk_tier"] == "Unsafe"
    assert reading["evidence"]["decision_basis"] == "rainfall_rule"


def test_rainfall_at_exactly_the_threshold_is_unsafe():
    """classify_by_rainfall uses >=, not > - pin the exact boundary (Review Focus)."""
    from app import config
    from app.model.rules_fallback import classify_by_rainfall

    result = classify_by_rainfall(config.RAIN_FALLBACK_THRESHOLD_MM)

    assert result["risk_tier"] == "Unsafe"


class _StubModel:
    """A predict_proba stub with a known, fixed output - the real trained model's exact
    behavior on arbitrary fake proxy values isn't something to assert on without actually
    verifying it, so these tests control probability_unsafe directly instead."""

    def __init__(self, probability_unsafe: float):
        self._probability_unsafe = probability_unsafe

    def predict_proba(self, X):
        return [[1.0 - self._probability_unsafe, self._probability_unsafe]]


def _stub_bundle(probability_unsafe: float) -> dict:
    return {
        "model": _StubModel(probability_unsafe),
        "features": [
            "water_temp_c", "sp_conductance_uscm", "dissolved_oxygen_mgl",
            "ph", "precip_mm", "precip_prev_24h_mm",
        ],
    }


def test_confidence_is_high_when_rule_and_model_agree(monkeypatch):
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall(precip_prev_48h_mm=0.0))  # rule: Safe
    monkeypatch.setattr(pr, "_load_model", lambda: _stub_bundle(probability_unsafe=0.1))  # model agrees
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [])

    reading = pr.pull_reading()

    assert reading["risk_tier"] == "Safe"
    assert reading["confidence"] == 0.9  # 1 - 0.1: high agreement


def test_confidence_is_low_when_rule_and_model_disagree(monkeypatch):
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall(precip_prev_48h_mm=0.0))  # rule: Safe
    monkeypatch.setattr(pr, "_load_model", lambda: _stub_bundle(probability_unsafe=0.9))  # model disagrees
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [])

    reading = pr.pull_reading()

    assert reading["risk_tier"] == "Safe"  # the rule still decides the tier
    assert reading["confidence"] == 0.1  # 1 - 0.9: low - the model thought this was likely Unsafe
    assert reading["evidence"]["model_probability_unsafe"] == 0.9


def test_evidence_still_carries_turbidity_even_though_the_model_no_longer_uses_it(monkeypatch):
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall())
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [])

    reading = pr.pull_reading()

    assert reading["evidence"]["proxies"]["turbidity_fnu"] == 5.5


def test_evidence_reports_the_rule_threshold_used(monkeypatch):
    from app import config

    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall())
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [])

    reading = pr.pull_reading()

    assert reading["evidence"]["rule_threshold_mm"] == config.RAIN_FALLBACK_THRESHOLD_MM


def _outfall(status: int, name: str = "D_test") -> OutfallReading:
    return OutfallReading(
        name=name, status=status, distance_km=1.0,
        last_poll=datetime(2026, 9, 25, tzinfo=timezone.utc),
    )


def test_cso_overflow_escalates_a_safe_rainfall_reading_to_unsafe(monkeypatch):
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall(precip_prev_48h_mm=0.0))
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [_outfall(status=4)])

    reading = pr.pull_reading()
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [])
    same_pull_without_overflow = pr.pull_reading()

    assert reading["risk_tier"] == "Unsafe"
    # The overflow changes the tier, never the rule/model agreement.
    assert reading["confidence"] == same_pull_without_overflow["confidence"]
    assert reading["evidence"]["decision_basis"] == "cso_overflow_rule"
    assert reading["evidence"]["cso_status"]["outfall_name"] == "D_test"
    assert reading["evidence"]["cso_status"]["status"] == 4


def test_cso_rule_does_not_override_when_no_outfall_triggers(monkeypatch):
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall(precip_prev_48h_mm=0.0))
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [_outfall(status=1)])

    reading = pr.pull_reading()

    assert reading["risk_tier"] == "Safe"
    assert reading["evidence"]["decision_basis"] == "rainfall_rule"
    assert reading["evidence"]["cso_status"] is None


def test_total_cso_outage_still_produces_a_valid_reading(monkeypatch):
    """Review Focus #2: a total CSOcast outage must NOT fail the reading closed, unlike the
    USGS gauge - see the Global Constraints section of this plan."""
    def _raise():
        raise CsoDataUnavailable("feed down")

    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall(precip_prev_48h_mm=0.0))
    monkeypatch.setattr(pr, "fetch_cso_outfalls", _raise)

    reading = pr.pull_reading()  # must not raise

    assert reading["risk_tier"] == "Safe"
    assert reading["evidence"]["decision_basis"] == "rainfall_rule"
    assert reading["evidence"]["cso_status"] is None


def test_cso_trigger_on_an_already_unsafe_reading_keeps_the_real_confidence_and_records_the_basis(monkeypatch):
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall(precip_prev_48h_mm=50.0))
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [_outfall(status=3)])

    reading = pr.pull_reading()
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [])
    same_pull_without_overflow = pr.pull_reading()

    assert reading["risk_tier"] == "Unsafe"
    assert reading["confidence"] == same_pull_without_overflow["confidence"]
    assert reading["evidence"]["decision_basis"] == "cso_overflow_rule"


def test_overflow_does_not_change_the_rule_model_agreement_exactly(monkeypatch):
    """The user-visible symptom this guards against: a Safe-by-rain reading forced Unsafe by an
    overflow showed 30% 'agreement'. With a model that gives a 10% chance of Unsafe, the rule
    (Safe, 0 mm) and the model agree at exactly 90%, overflow or not."""
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall(precip_prev_48h_mm=0.0))
    monkeypatch.setattr(pr, "_load_model", lambda: _stub_bundle(probability_unsafe=0.1))
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [_outfall(status=3)])

    reading = pr.pull_reading()

    assert reading["risk_tier"] == "Unsafe"
    assert reading["evidence"]["decision_basis"] == "cso_overflow_rule"
    assert reading["confidence"] == 0.9
