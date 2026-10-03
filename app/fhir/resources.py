"""Pure FHIR R4 resource builders - no I/O, no persistence, no network calls.

Per docs/superpowers/specs/2026-09-26-fhir-milestone4-design.md's Resource shapes and
Honesty notes sections:
- RISK_TIER_SYSTEM is an invented placeholder, not a real published OAH IG canonical URL
  (none exists anywhere in this repo) - clearly namespaced under .example, RFC 2606's
  reserved placeholder domain, so nobody mistakes it for a real registered system.
- LOCATION_ID uses a hyphen ("penns-landing") because FHIR resource ids forbid underscores
  ([A-Za-z0-9\\-\\.]{1,64}) - the internal Python constant elsewhere in this codebase
  (app.scoring.pull_reading.LOCATION_ID) uses an underscore ("penns_landing"). Deliberate,
  not a bug.
- Units on proxy Observations are plain display strings only (no UCUM system/code) - we
  have not independently verified UCUM codes for FNU/pH/uS-cm against the official UCUM
  table, and would rather omit a coded unit than assert one we haven't checked.
"""

from __future__ import annotations

import uuid

from app.config import LOCATION_LAT, LOCATION_LON

RISK_TIER_SYSTEM = "https://aquasentinel.example/fhir/CodeSystem/risk-tier"
LOCATION_ID = "penns-landing"
LOCATION_NAME = "Penn's Landing, Center City tidal Delaware"

PROXY_UNITS = {
    "water_temp_c": "°C",
    "sp_conductance_uscm": "µS/cm",
    "dissolved_oxygen_mgl": "mg/L",
    "ph": "pH",
    "turbidity_fnu": "FNU",
}
PROXY_DISPLAY_NAMES = {
    "water_temp_c": "Water temperature",
    "sp_conductance_uscm": "Specific conductance",
    "dissolved_oxygen_mgl": "Dissolved oxygen",
    "ph": "pH",
    "turbidity_fnu": "Turbidity",
}


def _tier_code(tier: str) -> str:
    return "unsafe" if tier == "Unsafe" else "safe"


def build_location() -> dict:
    return {
        "resourceType": "Location",
        "id": LOCATION_ID,
        "name": LOCATION_NAME,
        "position": {"latitude": LOCATION_LAT, "longitude": LOCATION_LON},
    }


def build_proxy_observations(reading: dict) -> list[dict]:
    """One Bundle-entry per live proxy, each with its own fullUrl so the risk-tier
    Observation's derivedFrom can reference them within the same Bundle.
    """
    proxies = reading["evidence"]["proxies"]
    effective_time = reading["time"]
    entries = []
    for name, value in proxies.items():
        entries.append({
            "fullUrl": f"urn:uuid:{uuid.uuid4()}",
            "resource": {
                "resourceType": "Observation",
                "id": str(uuid.uuid4()),
                "status": "preliminary",
                "code": {"text": PROXY_DISPLAY_NAMES.get(name, name)},
                "subject": {"reference": f"Location/{LOCATION_ID}"},
                "effectiveDateTime": effective_time,
                "valueQuantity": {"value": value, "unit": PROXY_UNITS.get(name, "")},
            },
        })
    return entries


RAINFALL_48H_DISPLAY_NAME = (
    "Precipitation, two prior local calendar days (48h, midnight to midnight US/Eastern)"
)
RAINFALL_SOURCE_DISPLAY = {
    "nws": "NWS station KPHL (hourly routine METAR reports)",
    "open-meteo": "Open-Meteo weather model (fallback when the NWS record is incomplete)",
}


def build_rainfall_observation(reading: dict) -> dict:
    """The 48h rainfall value the rainfall rule actually decided risk_tier on (Milestone
    1b). Without it, RPHSA would receive a tier plus proxy values that don't decide the
    tier, and could not audit the decision from the data it was sent.

    Required, not optional: a KeyError here means the reading can't show what decided its
    tier, and app/fhir/emit.py logs that failure rather than sending an unauditable Bundle.
    """
    evidence = reading["evidence"]
    value = evidence["rainfall_mm"]["precip_prev_48h_mm"]
    resource = {
        "resourceType": "Observation",
        "id": str(uuid.uuid4()),
        "status": "preliminary",
        "code": {"text": RAINFALL_48H_DISPLAY_NAME},
        "subject": {"reference": f"Location/{LOCATION_ID}"},
        "effectiveDateTime": reading["time"],
        "valueQuantity": {"value": value, "unit": "mm"},
    }
    notes = []
    source = evidence.get("rainfall_source")
    if source:
        notes.append(f"Source: {RAINFALL_SOURCE_DISPLAY.get(source, source)}.")
    threshold = evidence.get("rule_threshold_mm")
    if threshold is not None:
        notes.append(f"Risk tier rule: Unsafe when this value is >= {threshold} mm.")
    if notes:
        resource["note"] = [{"text": " ".join(notes)}]
    return {"fullUrl": f"urn:uuid:{uuid.uuid4()}", "resource": resource}


CSO_STATUS_DISPLAY = {
    3: "Overflow occurred in the past 72 hours",
    4: "Currently overflowing",
}


def build_cso_observation(reading: dict) -> dict:
    """The CSO field's value ("Overflow" / "No overflow" / "Reading unavailable") - present in
    every Bundle, whatever decided the tier, so RPHSA always sees what the CSO check found.

    A reading with no stored value is sent as "Reading unavailable", never "No overflow" (the
    same honest default as app.status). When a CSO overflow decided the tier, the triggering
    outfall's details go in a note - required there, not optional: a KeyError means the
    reading claims CSO decided the tier but can't show which outfall, and the caller must not
    silently drop that from the Bundle.
    """
    evidence = reading["evidence"]
    resource = {
        "resourceType": "Observation",
        "id": str(uuid.uuid4()),
        "status": "preliminary",
        "code": {"text": "Combined sewer outfall overflow status"},
        "subject": {"reference": f"Location/{LOCATION_ID}"},
        "effectiveDateTime": reading["time"],
        "valueCodeableConcept": {"text": evidence.get("cso") or "Reading unavailable"},
    }
    if evidence.get("decision_basis") == "cso_overflow_rule":
        cso = evidence["cso_status"]
        status_text = CSO_STATUS_DISPLAY.get(cso["status"], f"Status {cso['status']}")
        resource["note"] = [{
            "text": f"{status_text}. Outfall {cso['outfall_name']}, {cso['distance_km']} km "
                    f"from Penn's Landing. Last reported {cso['last_poll']}."
        }]
    return {"fullUrl": f"urn:uuid:{uuid.uuid4()}", "resource": resource}


RISK_METHOD_TEXT = {
    "rainfall_rule": "Estimated (rainfall-rule-based) risk, model-informed confidence",
    "cso_overflow_rule": (
        "Estimated risk: active/recent combined-sewer overflow near the reach overrides "
        "the rainfall rule"
    ),
}


def build_risk_observation(
    reading: dict,
    proxy_entries: list[dict],
    rainfall_entry: dict,
    cso_entry: dict | None = None,
) -> dict:
    tier_code = _tier_code(reading["risk_tier"])
    decision_basis = reading["evidence"].get("decision_basis", "rainfall_rule")
    method_text = RISK_METHOD_TEXT.get(decision_basis, RISK_METHOD_TEXT["rainfall_rule"])
    if not reading["evidence"].get("gauge_available", True):
        # No gauge data this pull, so the model could not run: say so instead of claiming a
        # model-informed confidence that does not exist.
        method_text = method_text.replace(", model-informed confidence", "")
        method_text += "; no model confidence (gauge data unavailable)"

    derived_from = [{"reference": rainfall_entry["fullUrl"]}]
    if cso_entry is not None:
        derived_from.append({"reference": cso_entry["fullUrl"]})
    derived_from += [{"reference": entry["fullUrl"]} for entry in proxy_entries]

    return {
        "fullUrl": f"urn:uuid:{uuid.uuid4()}",
        "resource": {
            "resourceType": "Observation",
            "id": str(uuid.uuid4()),
            "status": "preliminary",
            "method": {"text": method_text},
            "code": {"text": "E. coli risk tier estimate"},
            "subject": {"reference": f"Location/{LOCATION_ID}"},
            "effectiveDateTime": reading["time"],
            "valueCodeableConcept": {
                "coding": [
                    {"system": RISK_TIER_SYSTEM, "code": tier_code, "display": reading["risk_tier"]}
                ],
                "text": reading["risk_tier"],
            },
            # The rainfall value first (always present: it's what decided the tier, or what
            # the CSO rule overrode), then CSO if it's what actually decided it, then proxies.
            "derivedFrom": derived_from,
        },
    }


def build_flag(flag_id: str, tier: str, status: str, period_start: str, period_end: str | None) -> dict:
    period = {"start": period_start}
    if period_end:
        period["end"] = period_end
    return {
        "resourceType": "Flag",
        "id": flag_id,
        "status": status,
        "code": {
            "coding": [{"system": RISK_TIER_SYSTEM, "code": _tier_code(tier), "display": tier}],
            "text": tier,
        },
        "subject": {"reference": f"Location/{LOCATION_ID}"},
        "period": period,
    }


def build_bundle(reading: dict, flag: dict) -> dict:
    location_entry = {"fullUrl": f"urn:uuid:{uuid.uuid4()}", "resource": build_location()}
    proxy_entries = build_proxy_observations(reading)
    rainfall_entry = build_rainfall_observation(reading)
    decision_basis = reading["evidence"].get("decision_basis", "rainfall_rule")
    cso_entry = build_cso_observation(reading)
    # The tier is only "derived from" the CSO observation when CSO actually decided it; in
    # every other case the CSO value is reported alongside, but is not what the tier came from.
    cso_decided_entry = cso_entry if decision_basis == "cso_overflow_rule" else None
    risk_entry = build_risk_observation(reading, proxy_entries, rainfall_entry, cso_decided_entry)
    flag_entry = {"fullUrl": f"urn:uuid:{uuid.uuid4()}", "resource": flag}

    entries = [location_entry, rainfall_entry, cso_entry]
    entries += proxy_entries + [risk_entry, flag_entry]

    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": entries,
    }
