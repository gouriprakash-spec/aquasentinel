"""Fetch near-real-time antecedent rainfall from NWS station observations (KPHL).

See docs/landing-page/BUILD-SPEC.md's Data lineage section for why this replaces NCEI
for live scoring: NCEI daily-summaries (the training-set source) lags ~3 days, confirmed
live Sep 25, 2026.

How rain is read (redesigned 2026-09-30 after a final-review finding, verified against a
week of live KPHL data, 2241 observations, 2026-09-23..30):

1. Only the HOURLY ROUTINE report counts. KPHL files a routine METAR at :54 past every hour
   (168 of 168 hours present that week, no gaps). Its `precipitationLastHour` is the rain
   since the previous routine report, so 24 consecutive :54 reports tile a day with no
   overlap and no double counting. Everything else is ignored: the ~5-minute records
   (never carry precip) and mid-hour SPECI reports (their `precipitationLastHour` is a
   running subtotal of the same hour the next :54 report already includes).
2. `precipitationLast3Hours` / `precipitationLast6Hours` are NOT used. They were populated
   in only 8 of 2241 observations (synoptic times only). The previous design sampled
   `precipitationLast3Hours` every 3h and treated its constant nulls as 0mm on dry-looking
   descriptions - which silently returned 0.0 across a week that had ~53mm of real rain.
3. A null `precipitationLastHour` does NOT mean 0mm on its own: 2 of 111 null :54 values
   that week had rain in the raw METAR (e.g. `P0002`). Each hour is resolved in this order:
     a. `precipitationLastHour` has a value -> use it.
     b. Otherwise read the raw METAR (`rawMessage`) remarks:
        - No `RMK` section at all -> unknown (every live raw METAR had one).
        - `PNO` (rain gauge not operating) or `P////` (amount indeterminable) -> unknown.
        - `Prrrr` present -> rrrr hundredths of an inch, converted to mm.
        - No `P` group at all -> 0mm. METAR convention omits the group when no rain fell
          since the last routine report; checked live: 0 of 86 such hours had any rain in a
          SPECI within the same hour.
     c. Raw METAR empty too (23 of 168 hours that week) -> unknown.
   Any unknown hour inside a required window raises RainfallUnavailable - fail closed,
   never a lower number. Honest consequence: on current live data most calls fail over to
   Open-Meteo (see app/scoring/pull_reading.py:_fetch_rainfall), because api.weather.gov
   drops the raw METAR for roughly 1 in 7 routine reports. That is the correct trade: a
   missing hour could have been the rain that crosses the 2.5mm rule. (Gouri, 2026-10-03: a
   gap in the rule's two-previous-days window raises PartialRainfall, which carries the rain
   that DID resolve. Missing hours can only add rain, so if that known total already reaches the
   threshold the caller can still call Unsafe - see app/scoring/pull_reading.py:_fetch_rainfall.)

Windows. Each :54 report covers the hour ending at :54, so a local calendar day is the 24
reports from 00:54 through 23:54 local (a 6-minute offset from true midnight - rain in the
last 6 minutes of a day is counted in the next day). Reports are expected up to
REPORT_LATENCY before "now" (api.weather.gov publishes them with a delay), so the most
recent hour may not be included yet.

Requests. The NWS API caps `limit` at 500 records (~38-40h of 5-minute observations), and
precip_prev_48h_mm needs reports back to ~73h before "now" in the worst case. Instead of one
recent request plus one wide older request (the old 76h-to-36h window could exceed 500
records and be silently truncated at its far end), the needed span is fetched in
`start`/`end`-bounded chunks of at most REQUEST_CHUNK (24h, ~300 records). Any chunk that
comes back short still can't cause an undercount: every required hour is checked
individually.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

NWS_STATION = "KPHL"
NWS_OBSERVATIONS_URL = f"https://api.weather.gov/stations/{NWS_STATION}/observations"
USER_AGENT = "AquaSentinel (hackathon prototype; contact: project owner)"
MAX_LIMIT = 500  # NWS API's hard cap on the `limit` query param.

EASTERN = ZoneInfo("America/New_York")
ROUTINE_REPORT_MINUTE = 54  # KPHL's hourly routine METAR time, verified live 2026-09-30.
REPORT_LATENCY = timedelta(minutes=45)  # how long after :54 before the report is expected
REQUEST_CHUNK = timedelta(hours=24)  # keeps each request well under the 500-record cap
REQUEST_MARGIN = timedelta(hours=1)  # fetch a little before the first required report

MM_PER_HUNDREDTH_INCH = 0.254

# Remarks groups. They must stand alone (whitespace-delimited): "P0002" is the hourly
# precip group, but "SLP102" or "PK WND" must never match.
_HOURLY_PRECIP_GROUP = re.compile(r"(?:^|\s)P(\d{4}|////)(?=\s|$)")
_GAUGE_NOT_OPERATING = re.compile(r"(?:^|\s)PNO(?=\s|$)")


class RainfallUnavailable(RuntimeError):
    """Raised when NWS observations can't supply a reliable antecedent-rain figure -
    fail closed, don't guess.
    """


class PartialRainfall(RainfallUnavailable):
    """Some hourly reports in the two-previous-days window could not be resolved (Gouri,
    2026-10-03), but the ones that could are known.

    Still a failure - the exact total is unknown, so no Safe can be certified from it - but it
    carries what WAS resolved: `known_mm` (the sum of the resolved hours) and `missing_hours`
    (how many could not be). A missing hour can only add rain, never remove it, so `known_mm` is
    a true lower bound: if it already reaches the rule's threshold, Unsafe is certain. The caller
    (app/scoring/pull_reading.py) decides what to do with that. It is a RainfallUnavailable, so
    anything that only knows the old error keeps working; its message is the first unresolved
    hour's reason.
    """

    def __init__(self, message: str, known_mm: float, missing_hours: int):
        super().__init__(message)
        self.known_mm = known_mm
        self.missing_hours = missing_hours


def fetch_antecedent_rainfall(
    client: httpx.Client | None = None, now: datetime | None = None
) -> dict[str, float]:
    """Return {precip_mm, precip_prev_24h_mm, precip_prev_48h_mm} in mm.

    - precip_mm: rain since local (US/Eastern) midnight, from today's routine reports.
    - precip_prev_24h_mm: the 24 most recent routine reports (a rolling 24h).
    - precip_prev_48h_mm: the two full prior LOCAL CALENDAR DAYS (midnight to midnight,
      US/Eastern), matching how training derived the rule's 2.5mm threshold
      (build_dataset.py:load_precip()), not a rolling 48h-from-now window.

    All three are computed together; if ANY required hour can't be resolved from real
    data, the whole call raises RainfallUnavailable.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    latest_report = _latest_expected_report(now)
    today_midnight = _local_midnight_utc(now, days_before=0)
    two_days_ago_midnight = _local_midnight_utc(now, days_before=2)
    earliest_needed = min(two_days_ago_midnight, latest_report - timedelta(hours=24))

    owns_client = client is None
    client = client or httpx.Client(timeout=30.0, headers={"User-Agent": USER_AGENT})
    try:
        observations = _fetch_observations(client, earliest_needed - REQUEST_MARGIN, now)
    finally:
        if owns_client:
            client.close()

    if not observations:
        raise RainfallUnavailable("NWS returned no usable observations")

    # The rule's window first, tolerating gaps, so a gap there is reported as a lower bound
    # (PartialRainfall) instead of an all-or-nothing failure. A gap only in today's or the last
    # 24h's window is not about the rule's window, so those stay the plain failure below.
    known_mm, missing_hours, first_reason = _sum_routine_reports_allowing_gaps(
        observations, two_days_ago_midnight, today_midnight
    )
    if missing_hours:
        raise PartialRainfall(first_reason, known_mm=known_mm, missing_hours=missing_hours)

    return {
        "precip_mm": _sum_routine_reports(observations, today_midnight, latest_report),
        "precip_prev_24h_mm": _sum_routine_reports(
            observations, latest_report - timedelta(hours=24), latest_report
        ),
        "precip_prev_48h_mm": known_mm,
    }


def _fetch_observations(
    client: httpx.Client, start: datetime, end: datetime
) -> dict[datetime, dict]:
    """Fetch [start, end] in bounded chunks and return {timestamp_utc: properties}.

    Chunks share only their boundary instant; keying by timestamp collapses any record
    returned twice.
    """
    observations: dict[datetime, dict] = {}
    chunk_end = end
    while chunk_end > start:
        chunk_start = max(start, chunk_end - REQUEST_CHUNK)
        payload = _get_observations(client, {
            "start": chunk_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": chunk_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": MAX_LIMIT,
        })
        for props in _parse_observations(payload):
            observations[props["_timestamp_utc"]] = props
        chunk_end = chunk_start
    return observations


def _get_observations(client: httpx.Client, params: dict) -> dict:
    try:
        response = client.get(NWS_OBSERVATIONS_URL, params=params)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise RainfallUnavailable(f"NWS observations request failed: {exc}") from exc


def _parse_observations(payload: dict) -> list[dict]:
    parsed = []
    for feature in payload.get("features", []):
        props = feature.get("properties", {})
        timestamp = props.get("timestamp")
        if not timestamp:
            continue
        props = dict(props)
        props["_timestamp_utc"] = datetime.fromisoformat(timestamp).astimezone(timezone.utc)
        parsed.append(props)
    return parsed


def _local_midnight_utc(now_utc: datetime, days_before: int) -> datetime:
    """Local (US/Eastern) midnight `days_before` calendar days before today, in UTC.

    Built from the calendar date rather than by subtracting 24h steps, so a day that
    contains a DST change still starts at its own real local midnight.
    """
    local_date: date = now_utc.astimezone(EASTERN).date() - timedelta(days=days_before)
    return datetime.combine(local_date, time(0), tzinfo=EASTERN).astimezone(timezone.utc)


def _latest_expected_report(now: datetime) -> datetime:
    """The most recent routine-report time that should already be published."""
    cutoff = now - REPORT_LATENCY
    candidate = cutoff.replace(minute=ROUTINE_REPORT_MINUTE, second=0, microsecond=0)
    if candidate > cutoff:
        candidate -= timedelta(hours=1)
    return candidate


def _routine_report_times(start: datetime, end: datetime) -> list[datetime]:
    """Every :54 routine-report time in (start, end]."""
    first = start.replace(minute=ROUTINE_REPORT_MINUTE, second=0, microsecond=0)
    if first <= start:
        first += timedelta(hours=1)
    times = []
    current = first
    while current <= end:
        times.append(current)
        current += timedelta(hours=1)
    return times


def _sum_routine_reports(
    observations: dict[datetime, dict], start: datetime, end: datetime
) -> float:
    """Sum the hourly rain from every routine report in (start, end]. Raises
    RainfallUnavailable if any one of them is missing or can't be resolved."""
    total = 0.0
    for report_time in _routine_report_times(start, end):
        total += _report_rain_mm(observations, report_time)
    return round(total, 1)


def _report_rain_mm(observations: dict[datetime, dict], report_time: datetime) -> float:
    """Rain in the hour ending at one routine report, or raise RainfallUnavailable if that
    report is absent or can't be resolved."""
    report = observations.get(report_time)
    if report is None:
        raise RainfallUnavailable(
            f"No NWS routine report at {report_time.isoformat()} - can't account for "
            "that hour's rain."
        )
    return _hourly_rain_mm(report, report_time)


def _sum_routine_reports_allowing_gaps(
    observations: dict[datetime, dict], start: datetime, end: datetime
) -> tuple[float, int, str | None]:
    """Like _sum_routine_reports, but never raises for an unresolved hour: returns
    (sum of the hours that resolved, how many did not, the first unresolved hour's reason).
    The sum is a lower bound whenever the count is above zero."""
    known = 0.0
    missing_hours = 0
    first_reason = None
    for report_time in _routine_report_times(start, end):
        try:
            known += _report_rain_mm(observations, report_time)
        except RainfallUnavailable as exc:
            missing_hours += 1
            first_reason = first_reason or str(exc)
    return round(known, 1), missing_hours, first_reason


def _hourly_rain_mm(report: dict, report_time: datetime) -> float:
    """Rain in the hour ending at this routine report, in mm - or raise if unknown.

    See the module docstring (point 3) for why each branch exists and how it was verified.
    """
    last_hour = report.get("precipitationLastHour") or {}
    value = last_hour.get("value")
    if value is not None:
        unit = last_hour.get("unitCode")
        if unit != "wmoUnit:mm":
            raise RainfallUnavailable(
                f"Unexpected precipitation unit {unit!r} at {report_time.isoformat()}"
            )
        return float(value)

    raw = report.get("rawMessage") or ""
    if not raw.strip():
        raise RainfallUnavailable(
            f"Routine report at {report_time.isoformat()} has no hourly precip value and "
            "no raw METAR to confirm it was dry."
        )
    if " RMK " not in raw:
        # The P group lives in remarks; a report with no remarks section at all (every
        # live KPHL raw METAR had one) can't confirm "no P group" means no rain.
        raise RainfallUnavailable(
            f"Raw METAR at {report_time.isoformat()} has no remarks section to read rain from."
        )
    remarks = raw.split(" RMK ", 1)[1]
    if _GAUGE_NOT_OPERATING.search(remarks):
        raise RainfallUnavailable(
            f"Rain gauge reported not operating (PNO) at {report_time.isoformat()}"
        )
    group = _HOURLY_PRECIP_GROUP.search(remarks)
    if group is None:
        return 0.0  # METAR omits the P group when no rain fell since the last report.
    if group.group(1) == "////":
        raise RainfallUnavailable(
            f"Hourly rain amount indeterminable (P////) at {report_time.isoformat()}"
        )
    return int(group.group(1)) * MM_PER_HUNDREDTH_INCH
