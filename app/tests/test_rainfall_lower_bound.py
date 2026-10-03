"""A rainfall lower bound can still decide Unsafe (Gouri, 2026-10-03).

The NWS station leaves holes in its hourly record (29% of the hours in a live 3-day check, and
old holes stay missing). One unresolved hour used to fail the whole NWS reading, so the pull fell
back to Open-Meteo, which measured 0.0 mm over a window where the resolved NWS hours already showed
4.6 mm. Missing hours can only ADD rain, never remove it, so when the hours that DID resolve
already reach the rule's threshold, Unsafe is certain. The stored value is that known total, flagged
as a lower bound ("at least"); it can never produce a Safe, and anything below the threshold still
falls back to Open-Meteo exactly as before.
"""

from __future__ import annotations

import pytest

from app import db, status
from app.fhir import resources
from app.ingestion import open_meteo
from app.ingestion.nws import PartialRainfall, RainfallUnavailable, fetch_antecedent_rainfall
from app.scoring import pull_reading as pr
from app.tests.test_nws_ingestion import (
    DAY_BEFORE_MIDNIGHT,
    NOW,
    TODAY_MIDNIGHT,
    YESTERDAY_MIDNIGHT,
    _client_for,
    _raw_metar,
    _record,
    _report_time,
    _station,
)
from app.tests.test_pull_reading import _fake_proxies, _stub_bundle
from app.tests.test_server import _client, _fake_reading


# --- the NWS reader reports what it could resolve ---

def test_a_gap_in_the_prior_two_days_reports_the_known_rain_and_how_many_hours_are_missing():
    rain = {_report_time(YESTERDAY_MIDNIGHT, 10): 4.6}
    gap = _report_time(DAY_BEFORE_MIDNIGHT, 20)

    with pytest.raises(PartialRainfall) as raised:
        fetch_antecedent_rainfall(client=_client_for(_station(rain, overrides={gap: None})), now=NOW)

    assert raised.value.known_mm == 4.6
    assert raised.value.missing_hours == 1
    assert isinstance(raised.value, RainfallUnavailable)  # callers that only know the old error still work
    assert "No NWS routine report" in str(raised.value)    # and the reason is the first unresolved hour's


def test_every_kind_of_unresolved_hour_is_counted_once():
    absent = _report_time(DAY_BEFORE_MIDNIGHT, 3)
    empty = _report_time(DAY_BEFORE_MIDNIGHT, 4)
    gauge_off = _report_time(YESTERDAY_MIDNIGHT, 5)
    overrides = {
        absent: None,
        empty: _record(empty, raw="", last_hour=None),
        gauge_off: _record(gauge_off, raw=_raw_metar(gauge_off, None, remarks_extra=" PNO"), last_hour=None),
    }

    with pytest.raises(PartialRainfall) as raised:
        fetch_antecedent_rainfall(client=_client_for(_station(overrides=overrides)), now=NOW)

    assert raised.value.missing_hours == 3
    assert raised.value.known_mm == 0.0  # the rest of the window was dry; nothing is invented


def test_a_gap_only_in_todays_window_is_not_a_lower_bound():
    """The rule decides on the two PREVIOUS days. A hole today says nothing about that window, so it
    stays the plain failure it always was (and the pull falls back to Open-Meteo)."""
    gap = _report_time(TODAY_MIDNIGHT, 3)

    with pytest.raises(RainfallUnavailable) as raised:
        fetch_antecedent_rainfall(client=_client_for(_station(overrides={gap: None})), now=NOW)

    assert not isinstance(raised.value, PartialRainfall)


# --- the pull ---

def _open_meteo_says_dry(monkeypatch):
    monkeypatch.setattr(
        open_meteo, "fetch_antecedent_rainfall",
        lambda: {"precip_mm": 0.0, "precip_prev_24h_mm": 0.0, "precip_prev_48h_mm": 0.0},
    )


def _pull_with_nws_gaps(monkeypatch, known_mm: float, missing_hours: int = 18):
    def nws_with_holes():
        raise PartialRainfall("Routine report at 2026-10-01T05:54:00+00:00 has no hourly precip value",
                              known_mm=known_mm, missing_hours=missing_hours)

    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", nws_with_holes)
    monkeypatch.setattr(pr, "_load_model", lambda: _stub_bundle(probability_unsafe=0.1))
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [])
    _open_meteo_says_dry(monkeypatch)
    return pr.pull_reading()


def test_known_rain_above_the_threshold_decides_unsafe_even_though_open_meteo_says_dry(monkeypatch):
    reading = _pull_with_nws_gaps(monkeypatch, known_mm=4.6)

    assert reading["risk_tier"] == "Unsafe"
    assert reading["evidence"]["decision_basis"] == "rainfall_rule"
    assert reading["evidence"]["rainfall_mm"]["precip_prev_48h_mm"] == 4.6   # the lower bound, not Open-Meteo's 0.0
    assert reading["evidence"]["rainfall_missing_hours"] == 18
    assert reading["evidence"]["rainfall_source"] == "nws-partial"


def test_exactly_the_threshold_is_unsafe_like_the_rule_itself(monkeypatch):
    reading = _pull_with_nws_gaps(monkeypatch, known_mm=2.5)

    assert reading["risk_tier"] == "Unsafe"
    assert reading["evidence"]["rainfall_source"] == "nws-partial"


def test_the_models_rain_features_still_come_from_open_meteo_in_this_case(monkeypatch):
    """The design keeps the model path untouched: only the rule's decided value changes."""
    monkeypatch.setattr(
        open_meteo, "fetch_antecedent_rainfall",
        lambda: {"precip_mm": 0.7, "precip_prev_24h_mm": 1.1, "precip_prev_48h_mm": 0.0},
    )
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: (_ for _ in ()).throw(
        PartialRainfall("gap", known_mm=4.6, missing_hours=2)))
    monkeypatch.setattr(pr, "_load_model", lambda: _stub_bundle(probability_unsafe=0.1))
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [])

    reading = pr.pull_reading()

    assert reading["evidence"]["rainfall_mm"]["precip_mm"] == 0.7
    assert reading["evidence"]["rainfall_mm"]["precip_prev_24h_mm"] == 1.1


def test_known_rain_below_the_threshold_can_never_certify_safe_so_it_falls_back_as_before(monkeypatch):
    reading = _pull_with_nws_gaps(monkeypatch, known_mm=2.4)

    assert reading["evidence"]["rainfall_source"] == "open-meteo"
    assert reading["evidence"]["rainfall_missing_hours"] == 0
    assert reading["evidence"]["rainfall_mm"]["precip_prev_48h_mm"] == 0.0   # Open-Meteo's value, not 2.4
    assert reading["risk_tier"] == "Safe"                                    # unchanged behavior


def test_a_complete_nws_reading_is_unchanged(monkeypatch):
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: {"precip_mm": 0.0, "precip_prev_24h_mm": 0.0, "precip_prev_48h_mm": 1.0})
    monkeypatch.setattr(pr, "_load_model", lambda: _stub_bundle(probability_unsafe=0.1))
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [])

    reading = pr.pull_reading()

    assert reading["evidence"]["rainfall_source"] == "nws"
    assert reading["evidence"]["rainfall_missing_hours"] == 0


# --- storage and the contract ---

def _lower_bound_reading() -> dict:
    reading = _fake_reading("Unsafe")
    reading["evidence"]["rainfall_mm"]["precip_prev_48h_mm"] = 4.6
    reading["evidence"]["rainfall_source"] = "nws-partial"
    reading["evidence"]["rainfall_missing_hours"] = 18
    return reading


def test_the_missing_hour_count_is_stored_and_defaults_to_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()

    db.insert_reading(_lower_bound_reading())
    db.insert_reading({**_fake_reading("Safe"), "time": "2026-09-25T18:40:00-04:00"})
    newest, older = db.get_recent_readings(limit=2)

    assert older["rainfall_missing_hours"] == 18
    assert newest["rainfall_missing_hours"] == 0  # a reading that says nothing about gaps has none


def test_a_database_from_before_this_change_is_migrated(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    db.init_db(path)
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE readings DROP COLUMN rainfall_missing_hours")

    db.init_db(path)  # must add the column back instead of failing

    with sqlite3.connect(path) as conn:
        assert "rainfall_missing_hours" in [row[1] for row in conn.execute("PRAGMA table_info(readings)")]


def test_the_status_contract_tells_agents_the_rain_total_is_a_lower_bound(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    db.insert_reading(_lower_bound_reading())

    contract = status.build_status_contract(db.get_recent_readings(limit=1)[0])

    assert contract["proxies"]["precip_prev_48h_mm"] == 4.6
    assert contract["rainfall_missing_hours"] == 18
    assert contract["rainfall_source"] == "nws-partial"


def test_the_readings_api_carries_the_missing_hour_count(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    db.insert_reading(_lower_bound_reading())

    rows = client.get("/api/readings").json()

    assert rows[0]["rainfall_missing_hours"] == 18


# --- FHIR ---

def _rainfall_observation(reading: dict) -> dict:
    return resources.build_rainfall_observation(reading)["resource"]


def test_fhir_reports_the_lower_bound_with_the_standard_greater_or_equal_comparator():
    reading = _lower_bound_reading()

    quantity = _rainfall_observation(reading)["valueQuantity"]
    note = _rainfall_observation(reading)["note"][0]["text"]

    assert quantity == {"value": 4.6, "unit": "mm", "comparator": ">="}
    assert "18" in note and "lower bound" in note.lower()


def test_fhir_leaves_the_comparator_off_when_the_total_is_exact():
    quantity = _rainfall_observation(_fake_reading("Safe"))["valueQuantity"]

    assert "comparator" not in quantity


# --- the page ---

def test_the_page_shows_a_greater_or_equal_sign_when_the_rain_total_is_a_lower_bound(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    page = client.get("/").text

    assert "rainfall_missing_hours" in page          # the page reads the count the server sends
    assert "&ge;" in page.split("function toDisplayRow")[1].split("function loadRecentReadings")[0]
    assert "at least" in page                        # the banner says "at least X mm"
    assert "nws-partial" in page and "some hourly reports missing" in page  # and the source label says why
