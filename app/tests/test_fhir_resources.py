"""Tests for app/fhir/resources.py - pure FHIR resource builders, no I/O.

Per docs/superpowers/specs/2026-09-26-fhir-milestone4-design.md's Resource shapes section.
"""

from __future__ import annotations

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
            }
        },
    }


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
    # 1 Location + 5 proxy Observations + 1 risk-tier Observation + 1 Flag = 8.
    assert len(bundle["entry"]) == 8

    resource_types = [entry["resource"]["resourceType"] for entry in bundle["entry"]]
    assert resource_types.count("Location") == 1
    assert resource_types.count("Observation") == 6
    assert resource_types.count("Flag") == 1


def test_risk_observation_derived_from_references_proxy_full_urls():
    flag = resources.build_flag("flag-1", "Unsafe", "active", "2026-06-01T12:00:00+00:00", None)
    bundle = resources.build_bundle(_reading(), flag)

    proxy_entries = [
        e for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Observation" and "method" not in e["resource"]
    ]
    risk_entry = next(
        e for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Observation" and "method" in e["resource"]
    )

    proxy_full_urls = {e["fullUrl"] for e in proxy_entries}
    derived_from_refs = {d["reference"] for d in risk_entry["resource"]["derivedFrom"]}

    assert len(proxy_entries) == 5
    assert derived_from_refs == proxy_full_urls


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
