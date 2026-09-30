"""Tests for app/fhir/resources.py - pure FHIR resource builders, no I/O.

Per docs/superpowers/specs/2026-09-26-fhir-milestone4-design.md's Resource shapes section.
"""

from __future__ import annotations

import pytest

from app.fhir import resources


def _reading(risk_tier: str = "Unsafe") -> dict:
    return {
        "time": "2026-06-01T12:00:00+00:00",
        "risk_tier": risk_tier,
        "evidence": {
            "proxies": {
                "water_temp_c": 21.5,
                "sp_conductance_uscm": 266.0,
                "dissolved_oxygen_mgl": 6.6,
                "ph": 7.3,
                "turbidity_fnu": 6.3,
            },
            "rainfall_mm": {"precip_mm": 0.0, "precip_prev_24h_mm": 1.0, "precip_prev_48h_mm": 4.2},
            "rainfall_source": "nws",
            "rule_threshold_mm": 2.5,
        },
    }


def _risk_entry(bundle: dict) -> dict:
    return next(
        e for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Observation" and "method" in e["resource"]
    )


def _rainfall_entries(bundle: dict) -> list[dict]:
    return [
        e for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Observation"
        and e["resource"]["code"]["text"] == resources.RAINFALL_48H_DISPLAY_NAME
    ]


def test_build_flag_active_uses_unsafe_code():
    flag = resources.build_flag("flag-1", "Unsafe", "active", "2026-06-01T12:00:00+00:00", None)

    assert flag["resourceType"] == "Flag"
    assert flag["id"] == "flag-1"
    assert flag["status"] == "active"
    assert flag["subject"] == {"reference": "Location/penns-landing"}
    assert flag["period"] == {"start": "2026-06-01T12:00:00+00:00"}
    coding = flag["code"]["coding"][0]
    assert coding["system"] == resources.RISK_TIER_SYSTEM
    assert coding["code"] == "unsafe"


def test_build_flag_inactive_sets_period_end():
    flag = resources.build_flag(
        "flag-1", "Safe", "inactive", "2026-06-01T12:00:00+00:00", "2026-06-03T12:00:00+00:00"
    )

    assert flag["status"] == "inactive"
    assert flag["period"]["end"] == "2026-06-03T12:00:00+00:00"
    assert flag["code"]["coding"][0]["code"] == "safe"


def test_build_bundle_contains_one_entry_per_resource():
    flag = resources.build_flag("flag-1", "Unsafe", "active", "2026-06-01T12:00:00+00:00", None)
    bundle = resources.build_bundle(_reading(), flag)

    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "collection"
    # 1 Location + 1 rainfall Observation + 5 proxy Observations + 1 risk-tier Observation
    # + 1 Flag = 9.
    assert len(bundle["entry"]) == 9

    resource_types = [entry["resource"]["resourceType"] for entry in bundle["entry"]]
    assert resource_types.count("Location") == 1
    assert resource_types.count("Observation") == 7
    assert resource_types.count("Flag") == 1


def test_risk_observation_derived_from_references_rainfall_and_proxy_full_urls():
    flag = resources.build_flag("flag-1", "Unsafe", "active", "2026-06-01T12:00:00+00:00", None)
    bundle = resources.build_bundle(_reading(), flag)

    input_entries = [
        e for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Observation" and "method" not in e["resource"]
    ]
    derived_from_refs = [d["reference"] for d in _risk_entry(bundle)["resource"]["derivedFrom"]]

    assert len(input_entries) == 6  # 1 rainfall + 5 proxies
    assert set(derived_from_refs) == {e["fullUrl"] for e in input_entries}
    # The value that decided the tier is listed first.
    assert derived_from_refs[0] == _rainfall_entries(bundle)[0]["fullUrl"]


def test_bundle_carries_the_48h_rainfall_that_decided_the_tier():
    """Milestone 1b final-review finding: the rule decides Safe/Unsafe from 48h rainfall,
    but the Bundle used to carry only proxies (none of which decide the tier) - RPHSA
    could not audit the decision from what it received."""
    flag = resources.build_flag("flag-1", "Unsafe", "active", "2026-06-01T12:00:00+00:00", None)
    bundle = resources.build_bundle(_reading(), flag)

    rainfall = _rainfall_entries(bundle)
    assert len(rainfall) == 1
    resource = rainfall[0]["resource"]
    assert resource["valueQuantity"] == {"value": 4.2, "unit": "mm"}
    assert resource["subject"] == {"reference": "Location/penns-landing"}
    assert resource["effectiveDateTime"] == "2026-06-01T12:00:00+00:00"
    note = resource["note"][0]["text"]
    assert "NWS" in note
    assert ">= 2.5 mm" in note


def test_bundle_cannot_be_built_without_the_deciding_rainfall_value():
    """A reading that can't show what decided its tier must not produce a Bundle that
    silently leaves the deciding value out."""
    reading = _reading()
    del reading["evidence"]["rainfall_mm"]["precip_prev_48h_mm"]
    flag = resources.build_flag("flag-1", "Unsafe", "active", "2026-06-01T12:00:00+00:00", None)

    with pytest.raises(KeyError):
        resources.build_bundle(reading, flag)


def test_risk_observation_value_matches_tier():
    flag = resources.build_flag("flag-1", "Safe", "inactive", "t", "t")
    bundle = resources.build_bundle(_reading("Safe"), flag)

    risk_entry = next(
        e for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Observation" and "method" in e["resource"]
    )
    coding = risk_entry["resource"]["valueCodeableConcept"]["coding"][0]
    assert coding["code"] == "safe"
    assert coding["system"] == resources.RISK_TIER_SYSTEM


def test_risk_observation_method_describes_the_rule_not_random_forest():
    """Milestone 1b (2026-09-27): the rule decides the tier now, not a random forest - a
    person reading this Observation off the wire must not conclude a model is deciding."""
    flag = resources.build_flag("flag-1", "Unsafe", "active", "2026-06-01T12:00:00+00:00", None)
    bundle = resources.build_bundle(_reading(), flag)

    risk_entry = next(
        e for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Observation" and "method" in e["resource"]
    )
    method_text = risk_entry["resource"]["method"]["text"]

    assert "random forest" not in method_text.lower()
    assert "rule" in method_text.lower()
