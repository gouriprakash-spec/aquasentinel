# Milestone 6 — CSO Overflow Rule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a fresh, nearby (within 5km) CSOcast outfall shows an active or recent sewage
overflow, force the live reading to Unsafe and drop confidence low enough to queue a Milestone 8
confirmatory sample — a one-directional escalation layered on top of the existing rainfall-rule
decision, never replacing it.

**Architecture:** Two new files follow the existing ingestion/rule split (`app/ingestion/nws.py`
+ `app/model/rules_fallback.py` is the template): `app/ingestion/csocast.py` fetches the live
feed and applies the radius + per-outfall-freshness filters; `app/model/cso_rule.py` is a pure
function deciding escalation from the already-filtered list. `app/scoring/pull_reading.py` wires
them in after its existing rainfall-rule + near-shore-model flow, which is otherwise untouched.
`app/db.py`, `app/status.py`, and `app/fhir/resources.py` each get the same kind of extension
Milestone 1b already made for the rainfall rule's evidence, applied to CSO's evidence instead.

**Tech Stack:** Python 3.11, httpx (already a dependency), pytest, `httpx.MockTransport` for
ingestion tests (matches `app/ingestion/usgs.py`'s test pattern).

**Spec:** `docs/superpowers/specs/2026-10-01-cso-overflow-rule-milestone6-design.md`

## Global Constraints

- **Radius: 5km** from `config.LOCATION_LAT`/`LOCATION_LON` (39.946402 / -75.139360).
- **Per-outfall freshness: 24 hours.** Checked per outfall, never as a whole-feed check.
- **Trigger: `Status` in `(3, 4)`** on at least one fresh, in-radius outfall — any single one
  is sufficient, never a majority or average.
- **One-directional only:** CSO can force Safe → Unsafe. It never forces Unsafe → Safe, and
  when it has nothing to say (no trigger, or the feed is totally unreachable) it does not touch
  `risk_tier`, `confidence`, or `decision_basis` at all.
- **Total CSOcast outage does not fail the reading closed.** `CsoDataUnavailable` from
  `csocast.fetch_nearby_outfalls()` must be caught in `pull_reading()` and treated as "no nearby
  outfalls this cycle" — the reading still succeeds on the rainfall rule alone. This is a
  deliberate, narrower reading of CLAUDE.md's fail-closed rule than the USGS gauge gets (spec
  Scope decision 5) — do not "fix" this into failing closed.
  `CsoDataUnavailable` is still raised on a genuine fetch/parse failure; it is never raised just
  because zero outfalls survive the radius/freshness filters (that is a normal, valid empty
  result).
- **`CSO_OVERRIDE_CONFIDENCE = 0.3`** is a plain literal in `config.py`, not computed from
  `LOW_CONFIDENCE_CUTOFF` — keep it that way; it only needs to stay below 0.7467.
- Indentation: 4 spaces (Python). Naming: snake_case. Comments explain the WHY, not the WHAT
  (CLAUDE.md). All thresholds live in `app/config.py`, never inline.
- Every ingestion module's fail-closed exception (`CsoDataUnavailable`) follows the exact
  existing pattern in `app/ingestion/usgs.py`/`nws.py`: a `RuntimeError` subclass, raised with a
  message that says what failed.

## Review Focus

1. **The D_54 case** — a closer-but-stale (>24h) outfall must never block a farther-but-fresh
   outfall from still triggering the rule. This is the single real failure mode this feature
   exists to handle (the real closest Delaware outfall has been stale since 2024-01-26), not a
   hypothetical — pinned in Task 2.
2. **Total CSOcast outage must not fail the whole reading closed** — the opposite of how the
   USGS gauge behaves, so a reasonable person skimming CLAUDE.md's "fail closed" rule could
   easily "fix" this into breaking the reading. Pinned in Task 4.
3. **CSO triggering on an already-Unsafe reading must still overwrite confidence and
   decision_basis**, even though the tier value itself doesn't change — Milestone 8's sampling
   trigger reads confidence, not tier, so silently leaving the rainfall rule's confidence in
   place would hide that CSO is what's actually current. Pinned in Task 3 and Task 4.
4. **The common case (no CSO trigger) must produce byte-for-byte the same FHIR Bundle shape**
   as before this feature existed — no CSO Observation, same `derivedFrom` order, same entry
   count. Pinned in Task 7 as an explicit regression test, not just an implicit pass of old tests.
5. **Cross-surface consistency for a CSO-escalated reading** — MCP output, `/api/status`, and
   the stored reading row must agree on tier, confidence, decision_basis, and which outfall
   triggered it, matching CLAUDE.md's Definition of Done for the rainfall-rule case. Pinned in
   Task 8.

---

### Task 1: Config additions and coordinate consolidation

**Files:**
- Modify: `app/config.py`
- Modify: `app/fhir/resources.py:19-25` (the `LOCATION_LAT`/`LOCATION_LON` definitions)
- Modify: `app/ingestion/open_meteo.py:21-24` (the `GAUGE_LAT`/`GAUGE_LON` definitions)
- Test: `app/tests/test_config_location.py` (new)

**Interfaces:**
- Produces: `config.LOCATION_LAT: float`, `config.LOCATION_LON: float`,
  `config.CSO_NEARBY_RADIUS_KM: float`, `config.CSO_OUTFALL_FRESHNESS_HOURS: int`,
  `config.CSO_TRIGGER_STATUSES: tuple[int, ...]`, `config.CSO_OVERRIDE_CONFIDENCE: float` — all
  later tasks import these from `app.config`.

- [ ] **Step 1: Write the failing test**

Create `app/tests/test_config_location.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest app/tests/test_config_location.py -v`
Expected: FAIL — `AttributeError: module 'app.config' has no attribute 'LOCATION_LAT'` (and
similar for the other new names).

- [ ] **Step 3: Add the new constants to `app/config.py`**

In `app/config.py`, add this block right after the `UNSAFE_THRESHOLD_CFU_100ML`/
`GEOMEAN_THRESHOLD_CFU_100ML` block (after the `# --- Safety classification ---` section):

```python
# --- Location (docs/product-brief.md) ---
# Penn's Landing, USGS gauge 01467200's own site coordinate. Single shared source - until
# Milestone 6, this existed as three separate Python copies (app/fhir/resources.py,
# app/ingestion/open_meteo.py); both now import it from here instead.
LOCATION_LAT = 39.946402
LOCATION_LON = -75.139360
```

Then, in the `# --- Rules fallback ---` section, after the existing `RAIN_FALLBACK_THRESHOLD_MM
= 2.5` line, add:

```python

# --- CSO overflow rule (Milestone 6) ---
# Verified live 2026-10-01 against the real CSOcast feed - see
# docs/superpowers/specs/2026-10-01-cso-overflow-rule-milestone6-design.md's Scope decisions
# for why these values, not a published tidal-excursion figure (none was found).
CSO_NEARBY_RADIUS_KM = 5.0  # of 53 Delaware-tagged outfalls, 35 fall within this radius
CSO_OUTFALL_FRESHNESS_HOURS = 24  # per-outfall, not whole-feed - see the D_54 case in the spec
CSO_TRIGGER_STATUSES = (3, 4)  # 3 = overflow in past 72h, 4 = currently overflowing
# Deliberately a plain literal, not computed from LOW_CONFIDENCE_CUTOFF - just needs to stay
# below it so a CSO-forced Unsafe always queues a Milestone 8 confirmatory sample.
CSO_OVERRIDE_CONFIDENCE = 0.3
```

Finally, delete the now-dead placeholder line:
```python
CSO_OUTFALL_IDS: list[str] = []  # TODO(decide): research PWD outfall locations + tidal excursion.
```
(This line pre-dates the geographic-radius design above and nothing will consume it.)

- [ ] **Step 4: Update `app/fhir/resources.py` to import the coordinate instead of defining it**

In `app/fhir/resources.py`, find:
```python
RISK_TIER_SYSTEM = "https://aquasentinel.example/fhir/CodeSystem/risk-tier"
LOCATION_ID = "penns-landing"
LOCATION_NAME = "Penn's Landing, Center City tidal Delaware"
LOCATION_LAT = 39.946402
LOCATION_LON = -75.139360
```
Replace with:
```python
from app.config import LOCATION_LAT, LOCATION_LON

RISK_TIER_SYSTEM = "https://aquasentinel.example/fhir/CodeSystem/risk-tier"
LOCATION_ID = "penns-landing"
LOCATION_NAME = "Penn's Landing, Center City tidal Delaware"
```
(Add the `from app.config import ...` line to the existing `import uuid` import block at the
top of the file, not inline where `RISK_TIER_SYSTEM` is — i.e. the file's imports become:
```python
from __future__ import annotations

import uuid

from app.config import LOCATION_LAT, LOCATION_LON
```
and then the `RISK_TIER_SYSTEM = ...` / `LOCATION_ID = ...` / `LOCATION_NAME = ...` block
follows, with the two `LOCATION_LAT`/`LOCATION_LON` lines removed from it.)

- [ ] **Step 5: Update `app/ingestion/open_meteo.py` to import the coordinate instead of defining it**

Find:
```python
from app.ingestion.nws import RainfallUnavailable

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
# Penn's Landing, USGS gauge 01467200 - same reach as docs/landing-page/index.html's map.
GAUGE_LAT = 39.946402
GAUGE_LON = -75.139360
EASTERN = ZoneInfo("America/New_York")
```
Replace with:
```python
from app.config import LOCATION_LAT as GAUGE_LAT
from app.config import LOCATION_LON as GAUGE_LON
from app.ingestion.nws import RainfallUnavailable

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
EASTERN = ZoneInfo("America/New_York")
```
(The `as GAUGE_LAT`/`as GAUGE_LON` aliasing keeps every other line in this file — which
reference `GAUGE_LAT`/`GAUGE_LON` lower down — unchanged.)

- [ ] **Step 6: Run the new test, and the full existing suite, to verify nothing broke**

Run: `./venv/bin/python -m pytest app/tests/test_config_location.py -v`
Expected: PASS (4 passed).

Run: `./venv/bin/python -m pytest app/tests/ -v`
Expected: all existing tests still PASS — this step is a pure refactor (moving where two
constants are defined), so no other test's behavior should change. If `test_fhir_resources.py`
or `test_open_meteo_ingestion.py` fail, the import changes in Steps 4-5 broke something; fix
before continuing.

- [ ] **Step 7: Commit**

```bash
git add app/config.py app/fhir/resources.py app/ingestion/open_meteo.py app/tests/test_config_location.py
git commit -m "feat: add CSO rule config constants, consolidate location coordinate"
```

---

### Task 2: `app/ingestion/csocast.py` — fetch and filter the live feed

**Files:**
- Create: `app/ingestion/csocast.py`
- Test: `app/tests/test_csocast_ingestion.py`

**Interfaces:**
- Consumes: `config.LOCATION_LAT`, `config.LOCATION_LON`, `config.CSO_NEARBY_RADIUS_KM`,
  `config.CSO_OUTFALL_FRESHNESS_HOURS` (Task 1).
- Produces: `OutfallReading` (NamedTuple: `name: str`, `status: int`, `distance_km: float`,
  `last_poll: datetime`), `CsoDataUnavailable` (exception class),
  `fetch_nearby_outfalls(client: httpx.Client | None = None, now: datetime | None = None) ->
  list[OutfallReading]` — Task 3 and Task 4 both import these.

- [ ] **Step 1: Write the failing tests**

Create `app/tests/test_csocast_ingestion.py`:

```python
"""Tests for app/ingestion/csocast.py - radius and per-outfall freshness filtering.

The freshness filter is per-outfall, not whole-feed, because the real closest Delaware
outfall to Penn's Landing (D_54, 0.11km) has had a stale LastPoll since 2024-01-26 - see
docs/superpowers/specs/2026-10-01-cso-overflow-rule-milestone6-design.md's Scope decision 2.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import config
from app.ingestion.csocast import CsoDataUnavailable, fetch_nearby_outfalls

KM_PER_DEGREE_LAT = 111.32  # good enough at the 1km/8km/25h test offsets used below
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _offset_lat(km_north: float) -> float:
    return config.LOCATION_LAT + km_north / KM_PER_DEGREE_LAT


def _feature(name: str, status: int, km_from_penns_landing: float, age_hours: float) -> dict:
    last_poll = NOW - timedelta(hours=age_hours)
    return {
        "properties": {
            "Name": name,
            "Status": status,
            "Status_Message": "test fixture",
            "LastPoll": int(last_poll.timestamp() * 1000),
            "Latitude": _offset_lat(km_from_penns_landing),
            "Longitude": config.LOCATION_LON,
            "Waterbody": "D",
        }
    }


def _client_for(features: list[dict]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"type": "FeatureCollection", "features": features})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_excludes_outfall_beyond_the_radius():
    features = [
        _feature("D_near", status=1, km_from_penns_landing=1.0, age_hours=0.5),
        _feature("D_far", status=1, km_from_penns_landing=8.0, age_hours=0.5),
    ]
    result = fetch_nearby_outfalls(client=_client_for(features), now=NOW)

    assert {r.name for r in result} == {"D_near"}


def test_excludes_a_closer_but_stale_outfall_in_favor_of_a_farther_fresh_one():
    """The D_54 case: the real closest Delaware outfall to Penn's Landing has been stale
    since 2024. Per-outfall freshness must exclude it without blocking a farther, fresh,
    actually-overflowing outfall from still triggering the rule."""
    features = [
        _feature("D_54_like_stale", status=4, km_from_penns_landing=0.1, age_hours=48),
        _feature("D_52_like_fresh", status=4, km_from_penns_landing=1.0, age_hours=0.3),
    ]
    result = fetch_nearby_outfalls(client=_client_for(features), now=NOW)

    assert {r.name for r in result} == {"D_52_like_fresh"}


def test_excludes_a_stale_outfall_even_when_it_is_the_only_one_in_radius():
    features = [_feature("D_stale_only", status=4, km_from_penns_landing=1.0, age_hours=25)]
    result = fetch_nearby_outfalls(client=_client_for(features), now=NOW)

    assert result == []


def test_empty_result_is_not_an_error():
    result = fetch_nearby_outfalls(client=_client_for([]), now=NOW)

    assert result == []


def test_fails_closed_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(CsoDataUnavailable):
        fetch_nearby_outfalls(client=client, now=NOW)


def test_fails_closed_on_unexpected_response_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(CsoDataUnavailable):
        fetch_nearby_outfalls(client=client, now=NOW)


def test_returns_the_outfalls_status_for_the_rule_to_check():
    features = [_feature("D_overflow", status=4, km_from_penns_landing=1.0, age_hours=0.3)]
    result = fetch_nearby_outfalls(client=_client_for(features), now=NOW)

    assert result[0].status == 4
    assert result[0].name == "D_overflow"
    assert result[0].distance_km < 1.1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_csocast_ingestion.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ingestion.csocast'`.

- [ ] **Step 3: Write the implementation**

Create `app/ingestion/csocast.py`:

```python
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
    finally:
        if owns_client:
            client.close()

    try:
        features = payload["features"]
    except (KeyError, TypeError) as exc:
        raise CsoDataUnavailable(f"Unexpected CSOcast response shape: {exc}") from exc

    nearby: list[OutfallReading] = []
    for feature in features:
        try:
            props = feature["properties"]
            name = props["Name"]
            status = int(props["Status"])
            lat = float(props["Latitude"])
            lon = float(props["Longitude"])
            last_poll_ms = props["LastPoll"]
        except (KeyError, TypeError, ValueError) as exc:
            raise CsoDataUnavailable(f"Unexpected CSOcast feature shape: {exc}") from exc

        distance_km = _haversine_km(config.LOCATION_LAT, config.LOCATION_LON, lat, lon)
        if distance_km > config.CSO_NEARBY_RADIUS_KM:
            continue

        last_poll = datetime.fromtimestamp(last_poll_ms / 1000, tz=timezone.utc)
        age_hours = (now - last_poll).total_seconds() / 3600
        if age_hours > config.CSO_OUTFALL_FRESHNESS_HOURS:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_csocast_ingestion.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/csocast.py app/tests/test_csocast_ingestion.py
git commit -m "feat: fetch and filter nearby CSOcast outfalls"
```

---

### Task 3: `app/model/cso_rule.py` — pure escalation logic

**Files:**
- Create: `app/model/cso_rule.py`
- Test: `app/tests/test_cso_rule.py`

**Interfaces:**
- Consumes: `OutfallReading` (Task 2), `config.CSO_TRIGGER_STATUSES`,
  `config.CSO_OVERRIDE_CONFIDENCE` (Task 1).
- Produces: `apply_cso_escalation(risk_tier: str, confidence: float, nearby_outfalls:
  list[OutfallReading]) -> dict` with keys `risk_tier: str`, `confidence: float`,
  `decision_basis: str | None`, `triggered_outfall: OutfallReading | None` — Task 4 imports this.

- [ ] **Step 1: Write the failing tests**

Create `app/tests/test_cso_rule.py`:

```python
"""Tests for app/model/cso_rule.py - pure escalation logic, no I/O.

Per docs/superpowers/specs/2026-10-01-cso-overflow-rule-milestone6-design.md's Scope
decision 4: one-directional only. This can force Safe -> Unsafe, never the reverse, and when
nothing qualifies it must leave risk_tier/confidence completely untouched.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app import config
from app.ingestion.csocast import OutfallReading
from app.model.cso_rule import apply_cso_escalation

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _outfall(status: int, name: str = "D_test", distance_km: float = 1.0) -> OutfallReading:
    return OutfallReading(name=name, status=status, distance_km=distance_km, last_poll=NOW)


def test_escalates_safe_to_unsafe_when_an_outfall_is_overflowing():
    result = apply_cso_escalation("Safe", 0.9, [_outfall(status=4)])

    assert result["risk_tier"] == "Unsafe"
    assert result["confidence"] == config.CSO_OVERRIDE_CONFIDENCE
    assert result["decision_basis"] == "cso_overflow_rule"
    assert result["triggered_outfall"].name == "D_test"


def test_status_3_recent_overflow_triggers_the_same_as_status_4_active():
    result = apply_cso_escalation("Safe", 0.9, [_outfall(status=3)])

    assert result["risk_tier"] == "Unsafe"
    assert result["decision_basis"] == "cso_overflow_rule"


def test_any_one_qualifying_outfall_is_enough_not_a_majority():
    outfalls = [
        _outfall(status=1, name="D_clean_1"),
        _outfall(status=1, name="D_clean_2"),
        _outfall(status=3, name="D_trigger"),
    ]
    result = apply_cso_escalation("Safe", 0.9, outfalls)

    assert result["risk_tier"] == "Unsafe"
    assert result["triggered_outfall"].name == "D_trigger"


def test_does_not_escalate_when_no_outfall_qualifies():
    outfalls = [_outfall(status=1, name="D_1"), _outfall(status=0, name="D_2")]
    result = apply_cso_escalation("Safe", 0.9, outfalls)

    assert result["risk_tier"] == "Safe"
    assert result["confidence"] == 0.9
    assert result["decision_basis"] is None
    assert result["triggered_outfall"] is None


def test_empty_outfall_list_leaves_everything_unchanged():
    result = apply_cso_escalation("Unsafe", 0.65, [])

    assert result["risk_tier"] == "Unsafe"
    assert result["confidence"] == 0.65
    assert result["decision_basis"] is None
    assert result["triggered_outfall"] is None


def test_triggering_on_an_already_unsafe_reading_still_overwrites_confidence_and_basis():
    """One-directional escalation: CSO can't change an already-Unsafe tier, but Milestone 8's
    sampling trigger reads confidence, not tier - confidence/decision_basis must still reflect
    that CSO is what's actually current, even though the tier value itself doesn't change."""
    result = apply_cso_escalation("Unsafe", 0.91, [_outfall(status=4)])

    assert result["risk_tier"] == "Unsafe"  # unchanged value...
    assert result["confidence"] == config.CSO_OVERRIDE_CONFIDENCE  # ...but overwritten anyway
    assert result["decision_basis"] == "cso_overflow_rule"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_cso_rule.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.model.cso_rule'`.

- [ ] **Step 3: Write the implementation**

Create `app/model/cso_rule.py`:

```python
"""Pure CSO-overflow escalation rule - no I/O. Given the already radius/freshness-filtered
outfalls from app.ingestion.csocast, decides whether any of them forces Safe -> Unsafe.

One-directional only (spec's Scope decision 4): this can escalate Safe to Unsafe, never the
reverse, and when nothing qualifies it has no opinion at all - the caller's existing tier and
confidence (from the rainfall rule / near-shore model, Milestone 1b) pass through untouched.
"""

from __future__ import annotations

from app import config
from app.ingestion.csocast import OutfallReading


def apply_cso_escalation(
    risk_tier: str, confidence: float, nearby_outfalls: list[OutfallReading]
) -> dict:
    """Return {risk_tier, confidence, decision_basis, triggered_outfall}.

    decision_basis is "cso_overflow_rule" when an outfall triggered, else None - the caller
    keeps whatever decision_basis it already had (e.g. "rainfall_rule") in that case.
    """
    triggered = next(
        (outfall for outfall in nearby_outfalls if outfall.status in config.CSO_TRIGGER_STATUSES),
        None,
    )

    if triggered is None:
        return {
            "risk_tier": risk_tier,
            "confidence": confidence,
            "decision_basis": None,
            "triggered_outfall": None,
        }

    return {
        "risk_tier": "Unsafe",
        "confidence": config.CSO_OVERRIDE_CONFIDENCE,
        "decision_basis": "cso_overflow_rule",
        "triggered_outfall": triggered,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_cso_rule.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add app/model/cso_rule.py app/tests/test_cso_rule.py
git commit -m "feat: add pure CSO overflow escalation rule"
```

---

### Task 4: Wire CSO escalation into `pull_reading()`

**Files:**
- Modify: `app/scoring/pull_reading.py`
- Test: `app/tests/test_pull_reading.py`

**Interfaces:**
- Consumes: `csocast.fetch_nearby_outfalls`, `csocast.CsoDataUnavailable` (Task 2),
  `cso_rule.apply_cso_escalation` (Task 3).
- Produces: `pull_reading()`'s return dict gains `evidence["cso_status"]` (`dict | None`, keys
  `outfall_name: str`, `status: int`, `distance_km: float`, `last_poll: str` when not `None`);
  `evidence["decision_basis"]` can now be `"cso_overflow_rule"` as well as `"rainfall_rule"` —
  Task 5, 6, 7, 8 all consume `evidence["cso_status"]` and the new `decision_basis` value.

- [ ] **Step 1: Write the failing tests**

In `app/tests/test_pull_reading.py`, add `from app import config` and
`from app.ingestion.csocast import CsoDataUnavailable, OutfallReading` to the existing import
block at the top of the file (alongside the existing `from app.ingestion import open_meteo` /
`from app.ingestion.nws import RainfallUnavailable` / `from app.ingestion.usgs import
ProxyReading` lines), then append these tests at the end of the file:

```python
def _outfall(status: int, name: str = "D_test") -> OutfallReading:
    return OutfallReading(
        name=name, status=status, distance_km=1.0,
        last_poll=datetime(2026, 9, 25, tzinfo=timezone.utc),
    )


def test_cso_overflow_escalates_a_safe_rainfall_reading_to_unsafe(monkeypatch):
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall(precip_prev_48h_mm=0.0))
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [_outfall(status=4)])

    reading = pr.pull_reading()

    assert reading["risk_tier"] == "Unsafe"
    assert reading["confidence"] == config.CSO_OVERRIDE_CONFIDENCE
    assert reading["evidence"]["decision_basis"] == "cso_overflow_rule"
    assert reading["evidence"]["cso_status"]["outfall_name"] == "D_test"
    assert reading["evidence"]["cso_status"]["status"] == 4


def test_cso_rule_does_not_override_when_no_outfall_triggers(monkeypatch):
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall(precip_prev_48h_mm=0.0))
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [_outfall(status=1)])

    reading = pr.pull_reading()

    assert reading["risk_tier"] == "Safe"
    assert reading["evidence"]["decision_basis"] == "rainfall_rule"
    assert reading["evidence"]["cso_status"] is None


def test_total_cso_outage_still_produces_a_valid_reading(monkeypatch):
    """Review Focus #2: a total CSOcast outage must NOT fail the reading closed, unlike the
    USGS gauge - see the Global Constraints section of this plan."""
    def _raise():
        raise CsoDataUnavailable("feed down")

    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall(precip_prev_48h_mm=0.0))
    monkeypatch.setattr(pr, "fetch_cso_outfalls", _raise)

    reading = pr.pull_reading()  # must not raise

    assert reading["risk_tier"] == "Safe"
    assert reading["evidence"]["decision_basis"] == "rainfall_rule"
    assert reading["evidence"]["cso_status"] is None


def test_cso_trigger_on_an_already_unsafe_reading_still_overwrites_confidence_and_basis(monkeypatch):
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall(precip_prev_48h_mm=50.0))
    monkeypatch.setattr(pr, "fetch_cso_outfalls", lambda: [_outfall(status=3)])

    reading = pr.pull_reading()

    assert reading["risk_tier"] == "Unsafe"
    assert reading["confidence"] == config.CSO_OVERRIDE_CONFIDENCE
    assert reading["evidence"]["decision_basis"] == "cso_overflow_rule"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_pull_reading.py -v`
Expected: FAIL — `AttributeError: <module 'app.scoring.pull_reading'> does not have the
attribute 'fetch_cso_outfalls'` (monkeypatch fails because `pull_reading.py` doesn't import it
yet).

- [ ] **Step 3: Write the implementation**

In `app/scoring/pull_reading.py`, add these imports alongside the existing ones:
```python
from app.ingestion.csocast import CsoDataUnavailable
from app.ingestion.csocast import fetch_nearby_outfalls as fetch_cso_outfalls
from app.model.cso_rule import apply_cso_escalation
```

Replace the body of `pull_reading()` from the `confidence = ...` line through the `return {...}`
statement with:

```python
    confidence = probability_unsafe if risk_tier == "Unsafe" else (1.0 - probability_unsafe)

    nearby_outfalls = _fetch_cso_signal()
    escalation = apply_cso_escalation(risk_tier, confidence, nearby_outfalls)
    risk_tier = escalation["risk_tier"]
    confidence = escalation["confidence"]
    decision_basis = escalation["decision_basis"] or "rainfall_rule"
    cso_status = None
    if escalation["triggered_outfall"] is not None:
        outfall = escalation["triggered_outfall"]
        cso_status = {
            "outfall_name": outfall.name,
            "status": outfall.status,
            "distance_km": round(outfall.distance_km, 2),
            "last_poll": outfall.last_poll.isoformat(),
        }

    oldest_proxy_time = min(reading.retrieved_at for reading in proxies.values())

    return {
        "location": LOCATION_ID,
        "location_name": LOCATION_NAME,
        "time": oldest_proxy_time.isoformat(),
        "risk_tier": risk_tier,
        "confidence": round(confidence, 3),
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "evidence": {
            "proxies": {name: reading.value for name, reading in proxies.items()},
            "proxy_timestamps": {
                name: reading.retrieved_at.isoformat() for name, reading in proxies.items()
            },
            "rainfall_mm": rainfall,
            "rainfall_source": rainfall_source,
            "decision_basis": decision_basis,
            "rule_threshold_mm": config.RAIN_FALLBACK_THRESHOLD_MM,
            "model_probability_unsafe": round(probability_unsafe, 3),
            "cso_status": cso_status,
        },
        "threshold_cfu_100ml": config.UNSAFE_THRESHOLD_CFU_100ML,
        "model_version": "rf_nearshore",
        "regime": "nearshore",
        "kind": "model_estimate",
    }
```

Then add this new helper function after `_fetch_rainfall()`:

```python
def _fetch_cso_signal() -> list:
    """CSOcast is an escalation-only add-on, not a required input (spec Scope decision 5): a
    total outage must not fail the reading closed the way a stale USGS gauge does - it just
    means no CSO signal this cycle, identical in effect to every nearby outfall being stale.
    """
    try:
        return fetch_cso_outfalls()
    except CsoDataUnavailable:
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_pull_reading.py -v`
Expected: PASS (all tests, including the pre-existing ones from Milestone 1b).

- [ ] **Step 5: Commit**

```bash
git add app/scoring/pull_reading.py app/tests/test_pull_reading.py
git commit -m "feat: wire CSO overflow escalation into pull_reading()"
```

---

### Task 5: Persist CSO evidence in `app/db.py`

**Files:**
- Modify: `app/db.py`
- Test: `app/tests/test_db.py`

**Interfaces:**
- Consumes: `reading["evidence"]["cso_status"]` (Task 4's shape).
- Produces: four new columns on the `readings` table: `cso_outfall_name TEXT`,
  `cso_outfall_status INTEGER`, `cso_distance_km REAL`, `cso_last_poll TEXT` — Task 6 reads
  these via `db.get_recent_readings()`.

- [ ] **Step 1: Write the failing tests**

In `app/tests/test_db.py`, append:

```python
def test_persists_cso_trigger_info_when_present(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path=db_path)
    reading = _fake_reading()
    reading["evidence"]["cso_status"] = {
        "outfall_name": "D_25", "status": 3, "distance_km": 4.33,
        "last_poll": "2026-10-01T10:00:00+00:00",
    }

    db.insert_reading(reading, db_path=db_path)

    row = db.get_recent_readings(db_path=db_path)[0]
    assert row["cso_outfall_name"] == "D_25"
    assert row["cso_outfall_status"] == 3
    assert row["cso_distance_km"] == 4.33
    assert row["cso_last_poll"] == "2026-10-01T10:00:00+00:00"


def test_persists_null_cso_fields_when_not_triggered(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path=db_path)
    reading = _fake_reading()
    reading["evidence"]["cso_status"] = None

    db.insert_reading(reading, db_path=db_path)

    row = db.get_recent_readings(db_path=db_path)[0]
    assert row["cso_outfall_name"] is None
    assert row["cso_outfall_status"] is None
    assert row["cso_distance_km"] is None
    assert row["cso_last_poll"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_db.py -v`
Expected: FAIL — `KeyError: 'cso_outfall_name'`. (`get_recent_readings()` converts each
`sqlite3.Row` to a plain `dict` via `SELECT *`; since the column doesn't exist yet, it's simply
absent from that dict, and `row["cso_outfall_name"]` raises `KeyError` rather than a SQL error.)

- [ ] **Step 3: Write the implementation**

In `app/db.py`'s `_SCHEMA`, inside the `readings` table definition, find:
```python
    model_probability_unsafe REAL,
    threshold_cfu_100ml INTEGER NOT NULL,
```
Replace with:
```python
    model_probability_unsafe REAL,
    -- Milestone 6: which outfall (if any) escalated this reading to Unsafe.
    cso_outfall_name TEXT,
    cso_outfall_status INTEGER,
    cso_distance_km REAL,
    cso_last_poll TEXT,
    threshold_cfu_100ml INTEGER NOT NULL,
```

In `insert_reading()`, find:
```python
def insert_reading(reading: dict, db_path: Path | None = None) -> int:
    """Store a reading dict shaped like app.scoring.pull_reading.pull_reading()'s output."""
    evidence = reading["evidence"]
    proxies = evidence["proxies"]
    rainfall = evidence["rainfall_mm"]
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO readings (
                location, reading_time, risk_tier, confidence,
                water_temp_c, sp_conductance_uscm, dissolved_oxygen_mgl, ph, turbidity_fnu,
                precip_mm, precip_prev_24h_mm,
                precip_prev_48h_mm, rainfall_source, decision_basis, rule_threshold_mm,
                model_probability_unsafe,
                threshold_cfu_100ml, model_version, regime, retrieved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reading["location"],
                reading["time"],
                reading["risk_tier"],
                reading["confidence"],
                proxies.get("water_temp_c"),
                proxies.get("sp_conductance_uscm"),
                proxies.get("dissolved_oxygen_mgl"),
                proxies.get("ph"),
                proxies.get("turbidity_fnu"),
                rainfall.get("precip_mm"),
                rainfall.get("precip_prev_24h_mm"),
                rainfall.get("precip_prev_48h_mm"),
                evidence.get("rainfall_source"),
                evidence.get("decision_basis"),
                evidence.get("rule_threshold_mm"),
                evidence.get("model_probability_unsafe"),
                reading["threshold_cfu_100ml"],
                reading["model_version"],
                reading["regime"],
                reading["retrieved_at"],
            ),
        )
        return cursor.lastrowid
```
Replace with:
```python
def insert_reading(reading: dict, db_path: Path | None = None) -> int:
    """Store a reading dict shaped like app.scoring.pull_reading.pull_reading()'s output."""
    evidence = reading["evidence"]
    proxies = evidence["proxies"]
    rainfall = evidence["rainfall_mm"]
    cso_status = evidence.get("cso_status") or {}
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO readings (
                location, reading_time, risk_tier, confidence,
                water_temp_c, sp_conductance_uscm, dissolved_oxygen_mgl, ph, turbidity_fnu,
                precip_mm, precip_prev_24h_mm,
                precip_prev_48h_mm, rainfall_source, decision_basis, rule_threshold_mm,
                model_probability_unsafe,
                cso_outfall_name, cso_outfall_status, cso_distance_km, cso_last_poll,
                threshold_cfu_100ml, model_version, regime, retrieved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reading["location"],
                reading["time"],
                reading["risk_tier"],
                reading["confidence"],
                proxies.get("water_temp_c"),
                proxies.get("sp_conductance_uscm"),
                proxies.get("dissolved_oxygen_mgl"),
                proxies.get("ph"),
                proxies.get("turbidity_fnu"),
                rainfall.get("precip_mm"),
                rainfall.get("precip_prev_24h_mm"),
                rainfall.get("precip_prev_48h_mm"),
                evidence.get("rainfall_source"),
                evidence.get("decision_basis"),
                evidence.get("rule_threshold_mm"),
                evidence.get("model_probability_unsafe"),
                cso_status.get("outfall_name"),
                cso_status.get("status"),
                cso_status.get("distance_km"),
                cso_status.get("last_poll"),
                reading["threshold_cfu_100ml"],
                reading["model_version"],
                reading["regime"],
                reading["retrieved_at"],
            ),
        )
        return cursor.lastrowid
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_db.py -v`
Expected: PASS (all tests, including the pre-existing ones — `_fake_reading()` doesn't set
`evidence["cso_status"]` at all for the older tests, and `evidence.get("cso_status") or {}`
handles that missing-key case the same as an explicit `None`).

- [ ] **Step 5: Commit**

```bash
git add app/db.py app/tests/test_db.py
git commit -m "feat: persist which outfall (if any) triggered a CSO-escalated reading"
```

---

### Task 6: Surface CSO status in `app/status.py`

**Files:**
- Modify: `app/status.py`
- Test: `app/tests/test_status.py`

**Interfaces:**
- Consumes: the four `cso_*` row columns (Task 5).
- Produces: `build_status_contract()`'s return dict gains `"cso_status": dict | None` (same
  shape as `pull_reading()`'s `evidence["cso_status"]`) — consumed identically by `/api/status`
  and the MCP server (both already call `build_status_contract`/`current_status`, so no change
  is needed in `app/mcp_server.py` itself). Task 8's consistency test checks this field.

- [ ] **Step 1: Write the failing tests**

In `app/tests/test_status.py`, update the `_row()` fixture to include the four new columns
(add them after the existing `"model_probability_unsafe": 0.18,` line):
```python
        "model_probability_unsafe": 0.18,
        "cso_outfall_name": None,
        "cso_outfall_status": None,
        "cso_distance_km": None,
        "cso_last_poll": None,
```
Then append these new tests at the end of the file:

```python
def test_build_status_contract_cso_status_is_none_when_not_triggered():
    contract = build_status_contract(_row())

    assert contract["cso_status"] is None


def test_build_status_contract_includes_cso_status_when_triggered():
    row = _row()
    row["cso_outfall_name"] = "D_25"
    row["cso_outfall_status"] = 3
    row["cso_distance_km"] = 4.33
    row["cso_last_poll"] = "2026-10-01T10:00:00+00:00"

    contract = build_status_contract(row)

    assert contract["cso_status"] == {
        "outfall_name": "D_25",
        "status": 3,
        "distance_km": 4.33,
        "last_poll": "2026-10-01T10:00:00+00:00",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_status.py -v`
Expected: FAIL — `KeyError: 'cso_status'` on the new tests (the key doesn't exist in
`build_status_contract()`'s output yet).

- [ ] **Step 3: Write the implementation**

In `app/status.py`, find:
```python
def build_status_contract(row: dict) -> dict:
    return {
        "location": row["location"],
```
Replace the function with:
```python
def build_status_contract(row: dict) -> dict:
    cso_status = None
    if row["cso_outfall_name"] is not None:
        cso_status = {
            "outfall_name": row["cso_outfall_name"],
            "status": row["cso_outfall_status"],
            "distance_km": row["cso_distance_km"],
            "last_poll": row["cso_last_poll"],
        }
    return {
        "location": row["location"],
```
(i.e. insert the `cso_status` computation before the `return {`, keeping every existing line
of the returned dict as-is) and then, inside the returned dict, find:
```python
        "model_probability_unsafe": row["model_probability_unsafe"],
        "proxies": {
```
Replace with:
```python
        "model_probability_unsafe": row["model_probability_unsafe"],
        "cso_status": cso_status,
        "proxies": {
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_status.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add app/status.py app/tests/test_status.py
git commit -m "feat: surface CSO trigger status in the shared status contract"
```

---

### Task 7: FHIR surfacing — `build_cso_observation` and conditional method text

**Files:**
- Modify: `app/fhir/resources.py`
- Test: `app/tests/test_fhir_resources.py`

**Interfaces:**
- Consumes: `reading["evidence"]["cso_status"]`, `reading["evidence"]["decision_basis"]`
  (Task 4's shape).
- Produces: `build_cso_observation(reading: dict) -> dict` (same `{fullUrl, resource}` entry
  shape as `build_rainfall_observation`); `build_risk_observation()` gains an optional 4th
  parameter `cso_entry: dict | None = None`; `build_bundle()` includes a CSO Observation entry
  only when `reading["evidence"].get("decision_basis") == "cso_overflow_rule"`.

- [ ] **Step 1: Write the failing tests**

In `app/tests/test_fhir_resources.py`, add this helper after the existing `_reading()` function:

```python
def _cso_reading(risk_tier: str = "Unsafe") -> dict:
    reading = _reading(risk_tier)
    reading["evidence"]["decision_basis"] = "cso_overflow_rule"
    reading["evidence"]["cso_status"] = {
        "outfall_name": "D_25", "status": 3, "distance_km": 4.33,
        "last_poll": "2026-10-01T10:00:00+00:00",
    }
    return reading
```

Then append these tests at the end of the file:

```python
def test_build_cso_observation_shape():
    entry = resources.build_cso_observation(_cso_reading())

    resource = entry["resource"]
    assert resource["resourceType"] == "Observation"
    assert resource["subject"] == {"reference": "Location/penns-landing"}
    assert resource["effectiveDateTime"] == "2026-06-01T12:00:00+00:00"
    assert "72 hours" in resource["valueCodeableConcept"]["text"]
    assert "D_25" in resource["note"][0]["text"]


def test_bundle_includes_cso_observation_when_it_decided_the_tier():
    flag = resources.build_flag("flag-1", "Unsafe", "active", "2026-06-01T12:00:00+00:00", None)
    bundle = resources.build_bundle(_cso_reading(), flag)

    # 1 Location + 1 rainfall + 1 CSO + 5 proxies + 1 risk + 1 Flag = 10
    assert len(bundle["entry"]) == 10
    resource_types = [entry["resource"]["resourceType"] for entry in bundle["entry"]]
    assert resource_types.count("Observation") == 8


def test_bundle_omits_cso_observation_when_rainfall_rule_decided():
    """Review Focus #4: the common case (no CSO trigger) must produce byte-for-byte the same
    Bundle shape as before this feature existed."""
    flag = resources.build_flag("flag-1", "Unsafe", "active", "2026-06-01T12:00:00+00:00", None)
    bundle = resources.build_bundle(_reading(), flag)

    assert len(bundle["entry"]) == 9
    resource_types = [entry["resource"]["resourceType"] for entry in bundle["entry"]]
    assert resource_types.count("Observation") == 7


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_fhir_resources.py -v`
Expected: FAIL on three tests with `AttributeError: module 'app.fhir.resources' has no
attribute 'build_cso_observation'` (`test_build_cso_observation_shape`,
`test_risk_observation_method_describes_cso_override_when_it_decided_the_tier`,
`test_risk_observation_derived_from_includes_the_cso_entry_when_present` — all call it either
directly or via `build_bundle()`, which doesn't call it yet so the error actually surfaces one
level up in those two as `KeyError`/wrong-count before you even get to Step 3... in practice,
run the file and read what each failure actually says rather than predicting it exactly).
`test_bundle_includes_cso_observation_when_it_decided_the_tier` FAILs on `assert
len(bundle["entry"]) == 10` (still 9, since `build_bundle` doesn't branch on `decision_basis`
yet). `test_bundle_omits_cso_observation_when_rainfall_rule_decided` may already PASS — it pins
*existing* behavior (the old `_reading()` fixture already produces 9 entries), which is fine;
it exists to keep that regression visible once Step 3 changes `build_bundle`, not to prove
anything is currently broken.

- [ ] **Step 3: Write the implementation**

In `app/fhir/resources.py`, add this constant after the existing `RAINFALL_SOURCE_DISPLAY`
dict:

```python
CSO_STATUS_DISPLAY = {
    3: "Overflow occurred in the past 72 hours",
    4: "Currently overflowing",
}
```

Add this new function after `build_rainfall_observation()` and before `build_risk_observation()`:

```python
def build_cso_observation(reading: dict) -> dict:
    """The outfall that triggered a CSO escalation. Only built when decision_basis is
    "cso_overflow_rule" (see build_bundle) - required there, not optional: a KeyError means
    the reading claims CSO decided the tier but can't show which outfall, and the caller must
    not silently drop that from the Bundle.
    """
    cso = reading["evidence"]["cso_status"]
    status_text = CSO_STATUS_DISPLAY.get(cso["status"], f"Status {cso['status']}")
    resource = {
        "resourceType": "Observation",
        "id": str(uuid.uuid4()),
        "status": "preliminary",
        "code": {"text": "Combined sewer outfall overflow status"},
        "subject": {"reference": f"Location/{LOCATION_ID}"},
        "effectiveDateTime": reading["time"],
        "valueCodeableConcept": {"text": status_text},
        "note": [{
            "text": f"Outfall {cso['outfall_name']}, {cso['distance_km']} km from Penn's "
                    f"Landing. Last reported {cso['last_poll']}."
        }],
    }
    return {"fullUrl": f"urn:uuid:{uuid.uuid4()}", "resource": resource}
```

Add this constant right before `build_risk_observation()`'s definition:
```python
RISK_METHOD_TEXT = {
    "rainfall_rule": "Estimated (rainfall-rule-based) risk, model-informed confidence",
    "cso_overflow_rule": (
        "Estimated risk: active/recent combined-sewer overflow near the reach overrides "
        "the rainfall rule"
    ),
}
```

Replace `build_risk_observation()` entirely with:
```python
def build_risk_observation(
    reading: dict,
    proxy_entries: list[dict],
    rainfall_entry: dict,
    cso_entry: dict | None = None,
) -> dict:
    tier_code = _tier_code(reading["risk_tier"])
    decision_basis = reading["evidence"].get("decision_basis", "rainfall_rule")
    method_text = RISK_METHOD_TEXT.get(decision_basis, RISK_METHOD_TEXT["rainfall_rule"])

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
```

Replace `build_bundle()` entirely with:
```python
def build_bundle(reading: dict, flag: dict) -> dict:
    location_entry = {"fullUrl": f"urn:uuid:{uuid.uuid4()}", "resource": build_location()}
    proxy_entries = build_proxy_observations(reading)
    rainfall_entry = build_rainfall_observation(reading)
    decision_basis = reading["evidence"].get("decision_basis", "rainfall_rule")
    cso_entry = build_cso_observation(reading) if decision_basis == "cso_overflow_rule" else None
    risk_entry = build_risk_observation(reading, proxy_entries, rainfall_entry, cso_entry)
    flag_entry = {"fullUrl": f"urn:uuid:{uuid.uuid4()}", "resource": flag}

    entries = [location_entry, rainfall_entry]
    if cso_entry is not None:
        entries.append(cso_entry)
    entries += proxy_entries + [risk_entry, flag_entry]

    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": entries,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_fhir_resources.py -v`
Expected: PASS (all tests, including the pre-existing ones — `_reading()`'s fixture has no
`decision_basis` key, which `.get("decision_basis", "rainfall_rule")` defaults correctly, so
`test_build_bundle_contains_one_entry_per_resource` and
`test_risk_observation_derived_from_references_rainfall_and_proxy_full_urls` still produce
exactly their original 9-entry, no-CSO-entry shape).

- [ ] **Step 5: Commit**

```bash
git add app/fhir/resources.py app/tests/test_fhir_resources.py
git commit -m "feat: surface the triggering CSO outfall in the FHIR Bundle"
```

---

### Task 8: End-to-end cross-surface consistency for a CSO-escalated reading

**Files:**
- Modify: `app/tests/test_consistency.py`

**Interfaces:**
- Consumes: everything from Tasks 1-7 (this task adds no new production code, only a test that
  proves the whole chain agrees).

- [ ] **Step 1: Write the failing test**

In `app/tests/test_consistency.py`, add this helper after the existing `_fake_reading()`:

```python
def _fake_cso_reading() -> dict:
    reading = _fake_reading("Unsafe")
    reading["confidence"] = 0.3
    reading["evidence"]["decision_basis"] = "cso_overflow_rule"
    reading["evidence"]["cso_status"] = {
        "outfall_name": "D_25", "status": 3, "distance_km": 4.33,
        "last_poll": "2026-10-01T10:00:00+00:00",
    }
    return reading
```

Then append this test at the end of the file:

```python
def test_mcp_status_and_readings_agree_on_a_cso_escalated_reading(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(fhir_store, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(server, "pull_reading", lambda: _fake_cso_reading())
    db.init_db()
    fhir_store.init_db()

    client = TestClient(server.app)
    pulled = client.post("/api/pull-reading")
    assert pulled.status_code == 200

    readings = db.get_recent_readings(limit=1)
    status_response = client.get("/api/status")
    status_body = status_response.json()
    mcp_result = asyncio.run(mcp_server.get_current_status("penns_landing"))

    for surface in (status_body, mcp_result):
        assert surface["risk_tier"] == "Unsafe"
        assert surface["confidence"] == readings[0]["confidence"] == 0.3
        assert surface["decision_basis"] == readings[0]["decision_basis"] == "cso_overflow_rule"
        assert surface["cso_status"]["outfall_name"] == readings[0]["cso_outfall_name"] == "D_25"
        assert surface["cso_status"]["status"] == readings[0]["cso_outfall_status"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest app/tests/test_consistency.py -v`
Expected: FAIL — `KeyError: 'cso_status'` if any earlier task's wiring is incomplete; if Tasks
1-7 are all correctly done, this should actually already PASS on first run, since it exercises
only already-built code paths. If it fails, that means an earlier task's implementation has a
gap — diagnose against that task's own code before changing this test.

- [ ] **Step 3: Confirm or fix**

If the test fails, re-check Tasks 4-7's implementation against this test's expectations (most
likely culprit: `app/server.py`'s `/api/pull-reading` handler not passing the full reading
through to both `db.insert_reading()` and whatever builds the FHIR Bundle — but per Task 4-7,
no changes to `app/server.py` itself were specified, since `evidence["decision_basis"]` and
`evidence["cso_status"]` already flow generically through the existing `db.insert_reading()` →
`build_status_contract()` chain with no server.py changes needed). Do not add any new
production code in this task beyond what Tasks 1-7 already specified — this task's job is to
prove the chain, not extend it.

- [ ] **Step 4: Run the full test suite**

Run: `./venv/bin/python -m pytest app/tests/ -v`
Expected: all tests PASS (the full suite, not just this file — this is the final task, so this
is the whole feature's regression check).

- [ ] **Step 5: Commit**

```bash
git add app/tests/test_consistency.py
git commit -m "test: pin cross-surface consistency for a CSO-escalated reading"
```
