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

    if _missing_hourly_coverage(hours, day_1_start, day_1_end) or _missing_hourly_coverage(
        hours, day_2_start, day_2_end
    ):
        raise RainfallUnavailable(
            "Open-Meteo's hourly series is missing at least one hour within the "
            "precip_prev_48h_mm window - either it doesn't reach back far enough, or a "
            "null value fell inside the window. Either way, a null can't be silently "
            "treated as 0mm (see _parse_hourly_precipitation, which drops null hours "
            "entirely rather than counting them as no rain)."
        )

    precip_mm = sum(value for ts, value in hours if local_midnight <= ts <= now)
    # Bounded on BOTH sides: Open-Meteo returns forecast hours after `now` in the same
    # series (hundreds of them, verified live), and `now - ts` is negative for every one,
    # so a one-sided `<= 24h` check would add forecast rain to an observed-rain figure.
    precip_prev_24h_mm = sum(
        value for ts, value in hours if timedelta(0) <= now - ts <= timedelta(hours=24)
    )
    precip_prev_48h_mm = sum(
        value for ts, value in hours if day_1_start <= ts < day_1_end or day_2_start <= ts < day_2_end
    )

    return {
        "precip_mm": round(precip_mm, 1),
        "precip_prev_24h_mm": round(precip_prev_24h_mm, 1),
        "precip_prev_48h_mm": round(precip_prev_48h_mm, 1),
    }


def _missing_hourly_coverage(
    hours: list[tuple[datetime, float]], start: datetime, end: datetime
) -> bool:
    """True if any hourly mark in [start, end) has no corresponding value in `hours`.

    Catches both a series that doesn't reach back far enough AND a null value that fell
    in the MIDDLE of the window - `_parse_hourly_precipitation` drops null hours entirely,
    so from here a mid-window null looks identical to a gap at the edge, and both must
    fail closed the same way. This is the hourly-granularity analog of NWS's
    `_rain_at_mark`, which requires an observation near every 3h mark it needs; here every
    hour in the window is its own required mark, with no tolerance since Open-Meteo's
    hourly series has no missing-by-design timestamps the way NWS's 5-minute
    observations do.
    """
    present = {ts for ts, _ in hours}
    mark = start
    while mark < end:
        if mark not in present:
            return True
        mark += timedelta(hours=1)
    return False


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
