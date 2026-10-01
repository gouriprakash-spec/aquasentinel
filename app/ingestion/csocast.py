"""Fetch live CSO (combined sewer overflow) outfall status from PWD's CSOcast feed.

Verified live 2026-10-01: the real ArcGIS FeatureServer org id is POWz8dBwmjnei8fu, found by
loading water.phila.gov/maps/csocast/ and inspecting its own network traffic. (An earlier org
id recorded in plan.md notes from 2026-09-27 no longer resolves - re-discovered fresh, not
assumed still correct.) Public, unauthenticated GeoJSON.

Applies both filters from docs/superpowers/specs/2026-10-01-cso-overflow-rule-milestone6-
design.md per-outfall, not to the feed as a whole: radius (config.CSO_NEARBY_RADIUS_KM) and
freshness (config.CSO_OUTFALL_FRESHNESS_HOURS). The freshness filter matters because the real
closest Delaware outfall to Penn's Landing, D_54 (0.11km), has had a stale LastPoll since
2024-01-26 - if freshness were checked feed-wide via the nearest outfall, this rule would
never fire.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import NamedTuple

import httpx

from app import config

CSOCAST_URL = (
    "https://services2.arcgis.com/POWz8dBwmjnei8fu/arcgis/rest/services/"
    "CSOCast_Layerboard/FeatureServer/0/query"
)
DELAWARE_WATERBODY_CODE = "D"


class OutfallReading(NamedTuple):
    name: str
    status: int  # CSOcast's own code: 0 no data, 1 no overflow (72h), 3 overflow (72h), 4 active
    distance_km: float
    last_poll: datetime  # tz-aware UTC


class CsoDataUnavailable(RuntimeError):
    """Raised when the CSOcast feed can't be fetched or parsed - fail closed on a genuine
    failure. NOT raised when zero outfalls survive the radius/freshness filters - an empty
    result is normal and valid (app.model.cso_rule treats it as "no escalation", not an error).
    """


def fetch_nearby_outfalls(
    client: httpx.Client | None = None, now: datetime | None = None
) -> list[OutfallReading]:
    """Return the Delaware-tagged outfalls within config.CSO_NEARBY_RADIUS_KM of Penn's
    Landing whose LastPoll is within config.CSO_OUTFALL_FRESHNESS_HOURS of `now`.
    """
    now = now or datetime.now(timezone.utc)
    owns_client = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        return _fetch_and_filter(client, now)
    except CsoDataUnavailable:
        raise
    except Exception as exc:
        # Any other failure mode of this one feed (malformed JSON, an HTML error page
        # served with a 200, a garbage LastPoll value, ...) must never propagate past
        # this function - spec Scope decision 5: a CSOcast glitch means "no CSO signal
        # this cycle," never a failed reading.
        raise CsoDataUnavailable(f"Unexpected CSOcast failure: {exc}") from exc
    finally:
        if owns_client:
            client.close()


def _fetch_and_filter(client: httpx.Client, now: datetime) -> list[OutfallReading]:
    try:
        response = client.get(
            CSOCAST_URL,
            params={
                "where": f"Waterbody='{DELAWARE_WATERBODY_CODE}'",
                "outFields": "*",
                "f": "geojson",
            },
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise CsoDataUnavailable(f"CSOcast request failed: {exc}") from exc

    features = payload["features"]
    if not isinstance(features, list):
        raise CsoDataUnavailable(
            f"Unexpected CSOcast response shape: features is {type(features).__name__}"
        )

    nearby: list[OutfallReading] = []
    for feature in features:
        props = feature["properties"]
        name = props["Name"]
        status = int(props["Status"])
        lat = float(props["Latitude"])
        lon = float(props["Longitude"])
        last_poll_ms = props["LastPoll"]

        distance_km = _haversine_km(config.LOCATION_LAT, config.LOCATION_LON, lat, lon)
        # not isfinite() (not just the simpler `>`) because NaN compares False to
        # everything, including `>` - a NaN distance would otherwise silently PASS this
        # filter instead of being excluded.
        if not math.isfinite(distance_km) or distance_km > config.CSO_NEARBY_RADIUS_KM:
            continue

        last_poll = datetime.fromtimestamp(last_poll_ms / 1000, tz=timezone.utc)
        age_hours = (now - last_poll).total_seconds() / 3600
        # age_hours < 0 excludes a future/clock-skewed LastPoll, which would otherwise
        # silently pass the `> freshness` check below as if it were fresh.
        if age_hours < 0 or age_hours > config.CSO_OUTFALL_FRESHNESS_HOURS:
            continue

        nearby.append(
            OutfallReading(name=name, status=status, distance_km=distance_km, last_poll=last_poll)
        )

    return nearby


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r_km = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    delta_p = math.radians(lat2 - lat1)
    delta_l = math.radians(lon2 - lon1)
    a = math.sin(delta_p / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(delta_l / 2) ** 2
    return 2 * r_km * math.asin(math.sqrt(a))
