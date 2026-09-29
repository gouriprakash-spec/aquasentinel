"""Fetch near-real-time antecedent rainfall from NWS station observations (KPHL).

See docs/landing-page/BUILD-SPEC.md's Data lineage section for why this replaces NCEI
for live scoring: NCEI daily-summaries (the training-set source) lags ~3 days, confirmed
live Sep 25, 2026. NWS observations update every ~5 minutes, but two things make this
less simple than "sum the values":

1. NWS caps each request at 500 records (`limit`'s hard max) - about 38-40 hours of
   5-minute observations in practice. 24h rain needs data back to 21h ago, safely inside
   that window. 48h needs data back to 45h ago, which is NOT reliably available from one
   request - that's why the model only uses precip_mm/24h (see app/model/train.py), not
   48h/72h/7d.
2. Each observation's `precipitationLast3Hours` already covers a ROLLING 3-hour window.
   Summing every 5-minute record over a lookback period would count the same rain dozens
   of times over. Instead, this module samples observations ~3 hours apart ("marks") so
   the windows don't overlap.
3. NWS reports `precipitationLast3Hours` as `null` when it isn't currently raining, not
   `0`. A null is only treated as 0mm when that observation's own weather description
   also shows no precipitation - otherwise we fail closed rather than guess.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

NWS_STATION = "KPHL"
NWS_OBSERVATIONS_URL = f"https://api.weather.gov/stations/{NWS_STATION}/observations"
USER_AGENT = "AquaSentinel (hackathon prototype; contact: project owner)"
MAX_LIMIT = 500  # NWS API's hard cap on the `limit` query param.

EASTERN = ZoneInfo("America/New_York")
MARK_SPACING = timedelta(hours=3)  # matches precipitationLast3Hours' own window width
OBSERVATION_TOLERANCE = timedelta(minutes=90)  # how close an obs must be to a mark

OLDER_WINDOW_START = timedelta(hours=76)  # covers the worst case: local midnight up to 24h
OLDER_WINDOW_END = timedelta(hours=36)    # before "now", plus 45h of marks back from there,
                                           # with a margin overlapping the recent request's
                                           # ~38-40h reach so no mark falls in a gap.

_NO_PRECIP_KEYWORDS = (
    "rain", "shower", "drizzle", "thunderstorm", "snow", "sleet", "precipitation", "hail",
)


class RainfallUnavailable(RuntimeError):
    """Raised when NWS observations can't supply a reliable antecedent-rain figure -
    fail closed, don't guess.
    """


def fetch_antecedent_rainfall(
    client: httpx.Client | None = None, now: datetime | None = None
) -> dict[str, float]:
    """Return {precip_mm, precip_prev_24h_mm, precip_prev_48h_mm} in mm.

    precip_mm is rain since local (US/Eastern) midnight, rounded down to the nearest 3h
    mark. precip_prev_24h_mm is a rolling 24h window ending at `now`. precip_prev_48h_mm
    (added 2026-09-27, Milestone 1b) is the sum of the two full prior LOCAL CALENDAR DAYS
    (midnight to midnight, US/Eastern) - matching how training derived the rule's threshold
    (build_dataset.py:load_precip()), not a rolling 48h-from-now window, which would be a
    different quantity (see the spec's "Window mismatch" note). This needs data back to
    ~69h before "now" in the worst case, further than a single limit=500 request reliably
    reaches (~38-40h) - so this makes a second, start/end-bounded request for the older
    window, verified live to work (see this task's plan notes).
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    owns_client = client is None
    client = client or httpx.Client(timeout=30.0, headers={"User-Agent": USER_AGENT})
    try:
        recent_payload = _get_observations(client, {"limit": MAX_LIMIT})
        older_payload = _get_observations(client, {
            "start": (now - OLDER_WINDOW_START).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": (now - OLDER_WINDOW_END).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": MAX_LIMIT,
        })
    finally:
        if owns_client:
            client.close()

    observations = _parse_observations(recent_payload) + _parse_observations(older_payload)
    if not observations:
        raise RainfallUnavailable("NWS returned no usable observations")

    midnight = _local_midnight_utc(now)
    hours_since_midnight = max(0, int((now - midnight).total_seconds() // 3600))
    hours_today = (hours_since_midnight // 3) * 3

    day_1_end = midnight
    day_2_end = midnight - timedelta(hours=24)

    return {
        "precip_mm": _sum_over_marks(observations, now, hours_back=hours_today),
        "precip_prev_24h_mm": _sum_over_marks(observations, now, hours_back=24),
        "precip_prev_48h_mm": round(
            _sum_over_marks(observations, day_1_end, hours_back=24)
            + _sum_over_marks(observations, day_2_end, hours_back=24),
            1,
        ),
    }


def _get_observations(client: httpx.Client, params: dict) -> dict:
    try:
        response = client.get(NWS_OBSERVATIONS_URL, params=params)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise RainfallUnavailable(f"NWS observations request failed: {exc}") from exc


def _local_midnight_utc(now_utc: datetime) -> datetime:
    local_now = now_utc.astimezone(EASTERN)
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc)


def _parse_observations(payload: dict) -> list[tuple[datetime, float | None, bool]]:
    """Return [(timestamp_utc, precip_mm_or_None, no_precip_confirmed), ...]."""
    parsed = []
    for feature in payload.get("features", []):
        props = feature.get("properties", {})
        timestamp = props.get("timestamp")
        if not timestamp:
            continue
        ts = datetime.fromisoformat(timestamp).astimezone(timezone.utc)
        precip = props.get("precipitationLast3Hours") or {}
        raw_value = precip.get("value")
        parsed.append((ts, raw_value, _describes_no_precipitation(props)))
    return parsed


def _describes_no_precipitation(props: dict) -> bool:
    if props.get("presentWeather"):
        return False  # NWS explicitly listed weather phenomena - don't assume none.
    description = (props.get("textDescription") or "").lower()
    return not any(word in description for word in _NO_PRECIP_KEYWORDS)


def _sum_over_marks(
    observations: list[tuple[datetime, float | None, bool]], now: datetime, hours_back: int
) -> float:
    """Sum non-overlapping 3h-spaced marks back from `now`, covering `hours_back` hours.

    Each mark takes the single closest observation's own 3h precip total - NOT a sum of
    every observation in the window, which would count the same rain many times over.
    """
    if hours_back <= 0:
        return 0.0
    n_marks = hours_back // 3
    marks = [now - MARK_SPACING * i for i in range(n_marks)]
    return round(sum(_rain_at_mark(observations, mark) for mark in marks), 1)


def _rain_at_mark(
    observations: list[tuple[datetime, float | None, bool]], mark: datetime
) -> float:
    nearby = [obs for obs in observations if abs(obs[0] - mark) <= OBSERVATION_TOLERANCE]
    if not nearby:
        raise RainfallUnavailable(
            f"No NWS observation within {OBSERVATION_TOLERANCE} of {mark.isoformat()}"
        )
    _, raw_value, no_precip_confirmed = min(nearby, key=lambda obs: abs(obs[0] - mark))
    if raw_value is not None:
        return float(raw_value)
    if no_precip_confirmed:
        return 0.0
    raise RainfallUnavailable(
        f"Observation near {mark.isoformat()} has no precip value and its weather "
        "description doesn't confirm no precipitation."
    )
