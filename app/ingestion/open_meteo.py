"""Fallback rainfall source, used only when NWS's own observation is unavailable or
ambiguous (see app.ingestion.nws.RainfallUnavailable and its three causes).

Open-Meteo's recent-hour precipitation comes from a weather *model*, not a physical rain
gauge like NWS's KPHL station - real data, but a different kind of evidence. Per CLAUDE.md's
"honest language" rule, app/scoring/pull_reading.py records which source actually supplied
the figure (evidence.rainfall_source) rather than presenting both as equivalent.

No API key is required for non-commercial use (see https://open-meteo.com/en/docs).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from app.ingestion.nws import RainfallUnavailable

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
# Penn's Landing, USGS gauge 01467200 - same reach as docs/landing-page/index.html's map.
GAUGE_LAT = 39.946402
GAUGE_LON = -75.139360
EASTERN = ZoneInfo("America/New_York")


def fetch_antecedent_rainfall(
    client: httpx.Client | None = None, now: datetime | None = None
) -> dict[str, float]:
    """Return {precip_mm, precip_prev_24h_mm, precip_prev_48h_mm} in mm, from Open-Meteo's
    hourly forecast model at the gauge's coordinates. Same shape as app.ingestion.nws's
    function, including the calendar-day convention for precip_prev_48h_mm (added
    2026-09-27, Milestone 1b) - this is the fallback path used whenever NWS's own
    observation is unavailable, so it must supply the same figure the rainfall rule needs,
    honestly, not just the 24h value.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    owns_client = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        response = client.get(
            OPEN_METEO_URL,
            params={
                "latitude": GAUGE_LAT,
                "longitude": GAUGE_LON,
                "hourly": "precipitation",
                "past_hours": 76,
                "timezone": "UTC",
            },
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise RainfallUnavailable(f"Open-Meteo request failed: {exc}") from exc
    finally:
        if owns_client:
            client.close()

    hours = _parse_hourly_precipitation(payload)
    if not hours:
        raise RainfallUnavailable("Open-Meteo returned no usable hourly precipitation data")

    local_midnight = now.astimezone(EASTERN).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc)
    day_1_start, day_1_end = local_midnight - timedelta(hours=24), local_midnight
    day_2_start, day_2_end = local_midnight - timedelta(hours=48), local_midnight - timedelta(hours=24)

    if not any(ts <= day_2_start for ts, _ in hours):
        raise RainfallUnavailable(
            "Open-Meteo's hourly history does not reach back far enough for precip_prev_48h_mm"
        )

    precip_mm = sum(value for ts, value in hours if local_midnight <= ts <= now)
    precip_prev_24h_mm = sum(value for ts, value in hours if now - ts <= timedelta(hours=24))
    precip_prev_48h_mm = sum(
        value for ts, value in hours if day_1_start <= ts < day_1_end or day_2_start <= ts < day_2_end
    )

    return {
        "precip_mm": round(precip_mm, 1),
        "precip_prev_24h_mm": round(precip_prev_24h_mm, 1),
        "precip_prev_48h_mm": round(precip_prev_48h_mm, 1),
    }


def _parse_hourly_precipitation(payload: dict) -> list[tuple[datetime, float]]:
    hourly = payload.get("hourly", {})
    times = hourly.get("time")
    values = hourly.get("precipitation")
    if not times or not values or len(times) != len(values):
        return []
    return [
        (datetime.fromisoformat(t).replace(tzinfo=timezone.utc), v)
        for t, v in zip(times, values)
        if v is not None
    ]
