"""Pins that Penn's Landing's coordinate has exactly one definition (app.config), not the
three separate copies that existed before Milestone 6 - see
docs/superpowers/specs/2026-10-01-cso-overflow-rule-milestone6-design.md's Config section.
"""

from __future__ import annotations

from app import config
from app.fhir import resources
from app.ingestion import open_meteo


def test_location_coordinate_is_defined_in_config():
    assert config.LOCATION_LAT == 39.946402
    assert config.LOCATION_LON == -75.139360


def test_fhir_resources_uses_the_shared_config_coordinate():
    assert resources.LOCATION_LAT == config.LOCATION_LAT
    assert resources.LOCATION_LON == config.LOCATION_LON


def test_open_meteo_uses_the_shared_config_coordinate():
    assert open_meteo.GAUGE_LAT == config.LOCATION_LAT
    assert open_meteo.GAUGE_LON == config.LOCATION_LON


def test_cso_config_constants_exist_with_sane_values():
    assert config.CSO_NEARBY_RADIUS_KM == 5.0
    assert config.CSO_OUTFALL_FRESHNESS_HOURS == 24
    assert config.CSO_TRIGGER_STATUSES == (3, 4)
    assert 0.0 <= config.CSO_OVERRIDE_CONFIDENCE < config.LOW_CONFIDENCE_CUTOFF
