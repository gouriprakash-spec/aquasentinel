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


def _cso_reading(risk_tier: str = "Unsafe") -> dict:
    reading = _reading(risk_tier)
    reading["evidence"]["decision_basis"] = "cso_overflow_rule"
    reading["evidence"]["cso"] = "Overflow"  # what pull_reading() sets whenever CSO decided
    reading["evidence"]["cso_status"] = {
        "outfall_name": "D_25", "status": 3, "distance_km": 4.33,
        "last_poll": "2026-10-01T10:00:00+00:00",
    }
    return reading


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
    # 1 Location + 1 rainfall Observation + 1 CSO Observation (always present, see
    # test_bundle_always_carries_the_cso_value) + 5 proxy Observations + 1 risk-tier
    # Observation + 1 Flag = 10.
    assert len(bundle["entry"]) == 10

    resource_types = [entry["resource"]["resourceType"] for entry in bundle["entry"]]
    assert resource_types.count("Location") == 1
    assert resource_types.count("Observation") == 8
    assert resource_types.count("Flag") == 1


def test_risk_observation_derived_from_references_rainfall_and_proxy_full_urls():
    flag = resources.build_flag("flag-1", "Unsafe", "active", "2026-06-01T12:00:00+00:00", None)
    bundle = resources.build_bundle(_reading(), flag)

    # The CSO observation is reported in every Bundle but is not an input the tier was derived
    # from when rainfall decided it - so it is excluded here (see
    # test_risk_observation_is_not_derived_from_cso_when_rainfall_decided_the_tier).
    cso_urls = {e["fullUrl"] for e in _cso_entries(bundle)}
    input_entries = [
        e for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Observation" and "method" not in e["resource"]
        and e["fullUrl"] not in cso_urls
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
    # The new CSO method text ("...overrides the rainfall rule") also contains "rule," so
    # the substring check above no longer discriminates the non-CSO case on its own -
    # pin the exact rainfall-rule text too.
    assert method_text == resources.RISK_METHOD_TEXT["rainfall_rule"]


def test_build_cso_observation_shape():
    entry = resources.build_cso_observation(_cso_reading())

    resource = entry["resource"]
    assert resource["resourceType"] == "Observation"
    assert resource["subject"] == {"reference": "Location/penns-landing"}
    assert resource["effectiveDateTime"] == "2026-06-01T12:00:00+00:00"
    # The value is the CSO field's own three-state value; the specific outfall status
    # ("overflow in the past 72 hours") moves to the note next to the outfall details.
    assert resource["valueCodeableConcept"]["text"] == "Overflow"
    assert "D_25" in resource["note"][0]["text"]
    assert "72 hours" in resource["note"][0]["text"]


def _cso_entries(bundle: dict) -> list[dict]:
    return [
        e for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Observation"
        and e["resource"]["code"]["text"] == "Combined sewer outfall overflow status"
    ]


@pytest.mark.parametrize("state", ["Overflow", "No overflow", "Reading unavailable"])
def test_bundle_always_carries_the_cso_value(state):
    """Whatever decided the tier, RPHSA receives the CSO field's value (Gouri, 2026-10-02)."""
    reading = _reading()
    reading["evidence"]["cso"] = state
    flag = resources.build_flag("flag-1", "Unsafe", "active", "2026-06-01T12:00:00+00:00", None)

    bundle = resources.build_bundle(reading, flag)

    entries = _cso_entries(bundle)
    assert len(entries) == 1  # exactly one - never two conflicting CSO observations
    assert entries[0]["resource"]["valueCodeableConcept"]["text"] == state
    assert entries[0]["resource"]["effectiveDateTime"] == "2026-06-01T12:00:00+00:00"


def test_a_reading_with_no_cso_value_is_sent_as_unavailable_never_no_overflow():
    flag = resources.build_flag("flag-1", "Unsafe", "active", "2026-06-01T12:00:00+00:00", None)

    bundle = resources.build_bundle(_reading(), flag)  # evidence has no "cso" key

    assert _cso_entries(bundle)[0]["resource"]["valueCodeableConcept"]["text"] == (
        "Reading unavailable"
    )


def test_cso_observation_has_no_outfall_note_when_no_outfall_triggered():
    reading = _reading()
    reading["evidence"]["cso"] = "No overflow"
    flag = resources.build_flag("flag-1", "Unsafe", "active", "2026-06-01T12:00:00+00:00", None)

    resource = _cso_entries(resources.build_bundle(reading, flag))[0]["resource"]

    assert "note" not in resource


def test_risk_observation_is_not_derived_from_cso_when_rainfall_decided_the_tier():
    """The CSO value is reported, but the tier was not derived from it - derivedFrom keeps
    its meaning (what actually decided the tier)."""
    reading = _reading()
    reading["evidence"]["cso"] = "No overflow"
    flag = resources.build_flag("flag-1", "Unsafe", "active", "2026-06-01T12:00:00+00:00", None)
    bundle = resources.build_bundle(reading, flag)

    derived_from_refs = [d["reference"] for d in _risk_entry(bundle)["resource"]["derivedFrom"]]
    assert _cso_entries(bundle)[0]["fullUrl"] not in derived_from_refs


def test_bundle_includes_cso_observation_when_it_decided_the_tier():
    flag = resources.build_flag("flag-1", "Unsafe", "active", "2026-06-01T12:00:00+00:00", None)
    bundle = resources.build_bundle(_cso_reading(), flag)

    # 1 Location + 1 rainfall + 1 CSO + 5 proxies + 1 risk + 1 Flag = 10
    assert len(bundle["entry"]) == 10
    resource_types = [entry["resource"]["resourceType"] for entry in bundle["entry"]]
    assert resource_types.count("Observation") == 8


def test_risk_observation_method_describes_cso_override_when_it_decided_the_tier():
    flag = resources.build_flag("flag-1", "Unsafe", "active", "2026-06-01T12:00:00+00:00", None)
    bundle = resources.build_bundle(_cso_reading(), flag)

    method_text = _risk_entry(bundle)["resource"]["method"]["text"].lower()
    assert "overflow" in method_text or "sewer" in method_text


def test_risk_observation_derived_from_includes_the_cso_entry_when_present():
    flag = resources.build_flag("flag-1", "Unsafe", "active", "2026-06-01T12:00:00+00:00", None)
    bundle = resources.build_bundle(_cso_reading(), flag)

    cso_entries = [
        e for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Observation"
        and e["resource"]["code"]["text"] == "Combined sewer outfall overflow status"
    ]
    assert len(cso_entries) == 1
    derived_from_refs = [d["reference"] for d in _risk_entry(bundle)["resource"]["derivedFrom"]]
    assert cso_entries[0]["fullUrl"] in derived_from_refs
