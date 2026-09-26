"""Tests for app/scoring/pull_reading.py - the wiring between ingestion and the model.

Model accuracy itself is tested in test_model.py; this file checks that live proxies and
rainfall get assembled into the right shape and that the output matches the shared signal
contract from docs/product-brief.md:
    { location, time, risk_tier, confidence, source, source_url, retrieved_at, evidence }
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.ingestion import open_meteo
from app.ingestion.nws import RainfallUnavailable
from app.ingestion.usgs import ProxyReading
from app.scoring import pull_reading as pr


def _fake_proxies() -> dict:
    now = datetime(2026, 9, 25, 21, 25, tzinfo=timezone.utc)
    return {
        "water_temp_c": ProxyReading(21.5, now, "P"),
        "sp_conductance_uscm": ProxyReading(269.0, now, "P"),
        "dissolved_oxygen_mgl": ProxyReading(6.6, now, "P"),
        "ph": ProxyReading(7.3, now, "P"),
        "turbidity_fnu": ProxyReading(5.5, now, "P"),
    }


def _fake_rainfall() -> dict:
    return {"precip_mm": 0.0, "precip_prev_24h_mm": 2.0}


def test_pull_reading_returns_the_shared_signal_contract(monkeypatch):
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall())

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

    reading = pr.pull_reading()

    assert reading["evidence"]["rainfall_source"] == "open-meteo"
    assert reading["evidence"]["rainfall_mm"]["precip_prev_24h_mm"] == 2.0


def test_uses_the_oldest_proxy_reading_as_the_reading_time(monkeypatch):
    stale = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    proxies = _fake_proxies()
    proxies["ph"] = ProxyReading(7.3, stale, "P")  # one straggler, older than the rest

    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: proxies)
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall())

    reading = pr.pull_reading()

    assert reading["time"] == stale.isoformat()
