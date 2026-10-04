# Milestone 6 — CSO overflow rule

Status: implemented (see `plan.md`); kept as the design record. Source of truth for what "build Milestone 6"
means, per the conversation on 2026-10-01. Supersedes nothing — `plan.md`'s milestone list and
`docs/alert-rules-decisions.md` still govern where this fits and what triggers it.

## Why this exists

`plan.md`'s Milestone 6 says: when an outfall near the Center City reach shows active or recent
overflow, a rule forces the status to Unsafe and lowers confidence. CSOcast access was verified
live on 2026-09-27 (a public, unauthenticated ArcGIS FeatureServer) but two real design questions
were left open: the exact nearby-outfall set (the reach is tidal, so "near" isn't just
upstream), and how to fail closed per-outfall when sensor coverage is uneven. Both are resolved
below, grounded in a fresh live read of the feed on 2026-10-01, not guessed.

## Scope decisions already made (do not re-litigate without going back to the user)

1. **Radius: 5km from Penn's Landing**, measured from the same coordinate the project already
   uses everywhere (`LOCATION_LAT`/`LOCATION_LON` = 39.946402 / -75.139360 — the USGS 01467200
   gauge's own site coordinate, currently defined in `app/fhir/resources.py`). Verified live
   2026-10-01: of 53 `Waterbody='D'` (Delaware) outfalls in the feed, 35 fall within 5km. This
   radius was chosen specifically because it's the smallest of the candidate radii (2/3/5km) that
   still covers a real, live overflow cluster seen that day (D_25, D_22, D_20 at 4.33-5.47km,
   Status 3) — a tighter radius would have missed real, current overflow data during the exact
   investigation that grounded this design.
2. **Per-outfall freshness: 24 hours**, independently per outfall — not a single whole-feed
   check. Verified live: the single *closest* Delaware outfall to Penn's Landing, D_54 (0.11km),
   has had a stale `LastPoll` since 2024-01-26 (978+ days) at design time. If freshness were
   checked as "is the nearest outfall fresh," the rule would never fire. Working sensors report
   same-day (observed ages 0.2-0.5 days); broken ones are stuck for weeks to years, not slightly
   lagging — so 24 hours cleanly separates "alive" from "dead" without needing USGS-gauge-grade
   (2-hour) tightness, which doesn't fit this feed's actual update cadence.
3. **Trigger: `Status` in `(3, 4)`** on at least one fresh, in-radius outfall. Per CSOcast's own
   enum (0 = data not available, 1 = no overflow in past 72h, 3 = overflow in past 72h, 4 =
   currently overflowing) — this is a direct reading of plan.md's own "active or recent overflow"
   phrase (active = 4, recent = 3), not a new distinction invented here. It only takes **one**
   qualifying outfall to trigger — not a majority, not an average.
4. **One-directional escalation only.** CSO can force Safe → Unsafe. It can never force
   Unsafe → Safe (no power to downgrade or issue an all-clear), and when it has nothing to say
   (no triggering outfall in range, or the whole feed is unreachable) it does not act at all —
   the rainfall rule's tier and the near-shore model's confidence (Milestone 1b) stand exactly as
   they already do today, untouched.
5. **Total CSOcast outage does not fail the reading closed.** This is a deliberate, narrower
   reading of CLAUDE.md's "fail closed" rule than the USGS gauge gets: CSO is an escalation-only
   add-on, not a required input to the base tier decision the way the gauge and rainfall are. If
   the feed is totally unreachable, the reading still succeeds on the rainfall rule + model alone,
   identical in effect to every nearby outfall being stale. Decided explicitly with Gouri
   2026-10-01, not assumed.

## Honesty notes (things invented or adapted, flagged rather than silently assumed)

- **The 5km radius is a chosen engineering default, not a derived or published tidal-excursion
  figure.** Two live web searches (2026-10-01) for an authoritative Delaware-at-Philadelphia
  tidal excursion distance came back empty — NOAA's Delaware Bay/River tide publications discuss
  tidal range (~6-8 ft), not horizontal excursion distance, and no PWD/DRBC document surfaced a
  specific mileage. In the absence of that figure, the radius was chosen from real outfall
  density and a real observed overflow event (see decision 1) rather than invented as a round
  number with no grounding. Same disclosure spirit as `RAIN_FALLBACK_THRESHOLD_MM`'s in-sample
  note in `app/config.py`.
- **`CSO_OVERRIDE_CONFIDENCE = 0.3` is a chosen constant, not derived from data.** There is no
  CSO-specific labeled dataset to fit a confidence value to. It's set below
  `LOW_CONFIDENCE_CUTOFF` (0.7467) specifically so a CSO-forced Unsafe reading always queues a
  Milestone 8 confirmatory sample, per `docs/alert-rules-decisions.md` decision 1 ("forces Unsafe
  and lowers confidence, which also triggers a sampling request"). Deliberately a plain literal
  (not computed as an offset from `LOW_CONFIDENCE_CUTOFF`) so it reads clearly in config, tests,
  and FHIR evidence without needing the reader to chase a second constant.
- **`CSO_OUTFALL_IDS` (existing placeholder in `app/config.py`) becomes dead and is removed.** It
  pre-dated this design, from before the geographic-radius approach was chosen over a hand-picked
  outfall ID list. Removing a placeholder that nothing uses is not the same as inventing a value
  for one that's still needed.

## Architecture

Two new pieces, following the existing split between ingestion (I/O, fail-closed on real
failures) and rule logic (pure, no I/O) already used for rainfall:

1. **`app/ingestion/csocast.py`** — fetches `Waterbody='D'` outfalls from the live FeatureServer
   (`https://services2.arcgis.com/POWz8dBwmjnei8fu/arcgis/rest/services/CSOCast_Layerboard/
   FeatureServer/0/query`, verified live 2026-10-01 — note this is a different ArcGIS org id than
   what was recorded in earlier plan.md notes from 2026-09-27; that one no longer resolves and
   this one was re-discovered via the live map's own network traffic, not guessed). Computes
   distance from `config.LOCATION_LAT`/`LOCATION_LON` using the haversine formula, filters to
   `config.CSO_NEARBY_RADIUS_KM`, filters again to `LastPoll` within `config.
   CSO_OUTFALL_FRESHNESS_HOURS`, and returns the surviving list as typed readings (name, status,
   distance_km, last_poll). Raises `CsoDataUnavailable` only on a genuine fetch/parse failure
   (network error, unexpected response shape) — never on "zero outfalls survived the filters,"
   which is a normal, valid empty result, not a failure.
2. **`app/model/cso_rule.py`** — pure function, no I/O: `apply_cso_escalation(risk_tier,
   confidence, nearby_outfalls) -> dict`. Checks the already-filtered list for any `Status` in
   `config.CSO_TRIGGER_STATUSES`; if found, returns an escalated `risk_tier="Unsafe"`,
   `confidence=config.CSO_OVERRIDE_CONFIDENCE`, `decision_basis="cso_overflow_rule"`, and which
   outfall triggered it. If not found (including an empty list, or `csocast.py` having raised
   `CsoDataUnavailable` upstream), returns the input `risk_tier`/`confidence` unchanged and
   `decision_basis=None` (caller keeps whatever basis it already had).

`app/model/rules_fallback.py` is not touched — its scope stays "no USGS gauge reading available
for the day," a different trigger than an always-checked escalation layer.

## Data flow

`pull_reading()` (`app/scoring/pull_reading.py`), after today's existing flow (fetch proxies →
fetch rainfall → `classify_by_rainfall()` decides base tier → near-shore model computes
confidence from agreement with that tier — all unchanged):

1. Try `csocast.fetch_nearby_outfalls()`. On `CsoDataUnavailable`, catch it and treat as `[]`
   (no nearby outfalls this cycle) — same shape as `_fetch_rainfall()`'s existing NWS → Open-Meteo
   fallback pattern, except here the fallback is simply "no CSO signal," not a second source.
2. Call `cso_rule.apply_cso_escalation(risk_tier, confidence, nearby_outfalls)`.
3. If it escalated: overwrite `risk_tier`, `confidence`, and set `evidence["decision_basis"] =
   "cso_overflow_rule"`; add `evidence["cso_status"]` with the triggering outfall's name, status,
   distance, and `last_poll`.
4. If it didn't: `risk_tier`/`confidence`/`decision_basis` stay exactly what the rainfall rule and
   model already produced. `evidence["cso_status"]` is still set, but to `None` — not omitted —
   so a reading can be told apart from "we never checked" vs. "we checked, nothing triggered."

## FHIR surfacing

Mirrors the rainfall Observation added in Milestone 1b (`app/fhir/resources.py`):

- New `build_cso_observation(reading)`, included in the Bundle **only when
  `evidence["decision_basis"] == "cso_overflow_rule"`** — unlike the rainfall Observation (always
  present, since the rainfall rule always runs), a CSO Observation only exists when CSO is
  actually what decided the tier, keeping the common-case Bundle unchanged from today.
- `build_risk_observation()`'s `method` text becomes conditional: the existing "Estimated
  (rainfall-rule-based) risk, model-informed confidence" stays for `decision_basis ==
  "rainfall_rule"`; a new "Estimated risk: active/recent combined-sewer overflow near the reach
  overrides the rainfall rule" for `decision_basis == "cso_overflow_rule"`.
- When CSO triggered, `derivedFrom` on the risk Observation includes the new CSO Observation's
  `fullUrl` alongside the rainfall Observation's — both evidence sources traveled, since the
  rainfall rule's own verdict is still real context even when CSO is what actually decided it.

## Config additions and one relocation

New, in `app/config.py` next to `RAIN_FALLBACK_THRESHOLD_MM`:
```python
CSO_NEARBY_RADIUS_KM = 5.0
CSO_OUTFALL_FRESHNESS_HOURS = 24
CSO_TRIGGER_STATUSES = (3, 4)
CSO_OVERRIDE_CONFIDENCE = 0.3
```
Removed: `CSO_OUTFALL_IDS` (dead placeholder, see Honesty notes).

Relocated: the coordinate for Penn's Landing currently exists as **three** separate Python
copies, not the one this design first assumed — `app/fhir/resources.py`'s `LOCATION_LAT`/
`LOCATION_LON`, and `app/ingestion/open_meteo.py`'s `GAUGE_LAT`/`GAUGE_LON` (same exact values,
same real point, its own comment already says "Penn's Landing, USGS gauge 01467200"). Both move
into `app/config.py` as `LOCATION_LAT`/`LOCATION_LON`, the single shared source the new
`csocast.py` also needs (an ingestion module importing from the FHIR layer would be a backwards
dependency). `app/fhir/resources.py` and `app/ingestion/open_meteo.py` both import them from
`config` instead of each defining their own copy. (The frontend's own two copies — the JSON-LD
block and the `gauge` object in `docs/landing-page/index.html` — are out of scope for this
consolidation: the dashboard stays framework-free per CLAUDE.md, with no shared-constant import
path into static JS, so those stay as-is.)

## Testing plan / Review Focus

- **Radius filter correctness**: an outfall just inside 5km counts, just outside doesn't — pinned
  with real or synthetic coordinates, not just "some outfalls included."
- **The D_54 case, directly**: a fresh in-radius outfall at Status 1 plus a *closer* but stale
  (>24h) outfall at Status 4 — the stale one must be excluded, the result must come from the
  fresh one only. This is the single most important regression test: it's the real, live failure
  mode this design exists to handle, not a hypothetical.
- **Trigger is any-one, not majority**: one qualifying outfall among several non-qualifying ones
  still escalates.
- **One-directional escalation**: CSO triggering when the rainfall rule already said Unsafe
  changes nothing about the existing tier (still Unsafe) but does still overwrite `confidence`
  and `decision_basis` — the escalation path is about which signal gets credit, not just the
  tier value, since Milestone 8's sampling trigger reads confidence, not tier.
- **No trigger leaves the rainfall-rule result completely untouched**: same `risk_tier`,
  `confidence`, and `decision_basis` as before this feature existed — a regression pin against
  the Milestone 1b behavior this builds on top of. `evidence["cso_status"]` is still present on
  the reading (set to `None`), not omitted — a separate assertion, since "we checked and nothing
  triggered" must be distinguishable from a reading built before this feature existed at all.
- **Total CSOcast outage still produces a valid reading**: `csocast.fetch_nearby_outfalls()`
  raising `CsoDataUnavailable` must not propagate out of `pull_reading()` — the reading succeeds
  on the rainfall rule alone, per the decision in this spec (explicitly opposite of the gauge's
  fail-closed behavior, so a test must pin this is intentional, not an oversight).
- **FHIR**: Bundle omits the CSO Observation when CSO didn't trigger; includes it, with the right
  `method` text change on the risk Observation, when it did.
- **Consistency test** (CLAUDE.md's Definition of Done): MCP output, `/api/status`, and the
  dashboard banner must agree on tier, confidence, and decision basis for the same CSO-escalated
  reading, not just for a rainfall-rule reading as today's existing consistency test covers.

## Open items for the implementation plan to resolve

- Exact haversine implementation location (a small shared geo helper vs. inlined in
  `csocast.py` — likely the latter, since nothing else in the codebase needs distance math yet).
- Whether `csocast.py`'s HTTP client follows the same `httpx.Client(timeout=30.0)` convention as
  `usgs.py`/`nws.py` (expected: yes, no reason to diverge).
- Dashboard display: whether/how `docs/landing-page/index.html` surfaces a CSO-triggered reading
  differently from a rainfall-rule one (e.g., showing which outfall and its distance) — not
  specified here, left for the implementation plan given BUILD-SPEC.md governs dashboard changes.
