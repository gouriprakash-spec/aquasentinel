# AquaSentinel landing page — build spec

This folder is a **reference implementation** to hand to a coding agent. `index.html` is
the real, standard-web version of the landing page (plain HTML/CSS/vanilla JS, no build
step). It renders and runs as-is, but two behaviors are **mocked** and marked `TODO(real)`
in the JS. This document says what to build for real.

## Files
- `index.html` — the page. Open it in a browser to see it work.
- `logo.png` — the AquaSentinel logo, referenced by the page.
- `BUILD-SPEC.md` — this file.

## What the page is
A single-reach monitoring page for the Center City tidal Delaware — pull-based, public, the
same for anyone who visits (no alerting, no personalization; see `docs/product-brief.md`'s
"No direct-to-public alerting, by design"). Sections top to bottom: hero (logo + statement +
a real Leaflet map), a "Live readings" toolbar (originally with a **Pull latest reading**
button; removed 2026-10-02 - the server's scheduled job pulls instead, so no visitor can
trigger live requests to the data sources), a latest-status banner, and a readings table.

## Data lineage (get this right — it is the credibility of the whole thing)
- **Proxies come from ONE USGS gauge: Penn's Landing, site `01467200`.** It streams
  water temp, specific conductance, dissolved oxygen, pH (since 2007) and turbidity
  (since 2021-10-28). This is the live-data source.
- **Rainfall for live scoring comes from NWS station observations** (`api.weather.gov`,
  station `KPHL`, Philadelphia Intl Airport) — near-real-time, minutes old. **NCEI daily
  PRCP** (same station, ID `USW00013739`) is the **training-set** source only
  (`AquaSentinel-dataset/build_dataset.py`); checked live Sep 25, 2026, it lags ~3 days
  behind today and cannot supply "yesterday's rain" for a live reading.
- **Ben Franklin Bridge and Navy Yard are DRBC E. coli lab-sampling stations, NOT gauges.**
  They supply the ground-truth labels the model is trained/validated against; they are not
  polled for live readings. Do not label them as data sources on the operator view.

## TODO 1 — real `pullReading()`
Replace the mocked `scoreReading()` in `index.html`.
1. Fetch latest proxies from USGS Water Services (instantaneous values):
   `https://waterservices.usgs.gov/nwis/iv/?format=json&sites=01467200&parameterCd=00010,00095,00300,00400,63680`
   (temp, sp. conductance, DO, pH, turbidity).
   **Checked Sep 23, 2026, re-verified Sep 25, 2026:** each parameter's `values` array holds
   more than one block. Block 0 is **not empty** — it holds a real but years-stale reading
   (e.g. from 2020-12-03, `qualifiers: ["A"]` = "Approved", a discontinued method) with no
   `method` label. The live reading sits in a later block, `qualifiers: ["P"]` (provisional),
   method containing "ISM Test Bed (barge)" — though the exact method-label wording differs
   slightly per parameter, so matching on method text is fragile. The robust rule: for each
   parameter, take the value across ALL its blocks with the newest `dateTime`, not a fixed
   block index and not a method-string match. Readings arrive every 5 minutes.
2. Fetch antecedent rainfall (previous 24h/48h, plus same-day) from NWS station observations:
   `https://api.weather.gov/stations/KPHL/observations?limit=500`
   (requires a `User-Agent` header per NWS API policy; no API key needed).
   **Checked Sep 25, 2026:** NCEI daily-summaries (the training-set source) lags ~3 days and
   cannot serve a live reading — confirmed by fetching it live and finding no data newer than
   3 days old. NWS observations update every ~5 minutes but `limit` caps at 500 records
   (~40 hours of history) per request — not enough for 72h or 7d, which is why **the model no
   longer uses those two windows** (see Model section below). NWS also reports
   `precipitationLast3Hours` as `null` when it isn't currently raining, rather than `0`. Each
   observation's window already covers 3h, so sample observations ~3h apart (not every 5-minute
   record, which would double-count overlapping windows) and treat a `null` value as `0mm`
   **only** when that observation's `textDescription`/`presentWeather` doesn't indicate
   precipitation — a disclosed limitation, not a fully verified zero.
3. Feed those into the trained model (see below) to get an estimated E. coli value +
   a confidence. Compare against the EPA single-sample limit **235 CFU/100mL** ->
   Safe / Unsafe tier. Keep the tier decision deterministic.
4. Append the row to the table and update the banner. Persist rows server-side if you
   want history to survive reloads.

### Model
- Small-N problem: train tree ensembles (random forest / gradient boosting) with an MLR
  baseline and a rainfall/CSO rules fallback. Report classification metrics (precision/
  recall on "unsafe") vs the 235 threshold, not R^2.
- **Two regimes, validated separately:** pre-2021 (no turbidity) and post-2021 (with
  turbidity) — a 2021 instrument change is a silent-bias trap. **Decided 2026-09-25: only the
  post-2021 model ships.** Live scoring always runs on "today," and turbidity has streamed
  continuously since 2021-10-28, so the pre-2021 regime never applies to a live reading; it was
  also outperformed by the rainfall-only rules fallback (precision 0.346/recall 0.30 vs.
  0.372/0.653), so there was no case for shipping a second, weaker model. See `plan.md`
  milestone 1.
- Only rainfall and turbidity carry strong signal in the current data; conductance, DO and
  pH are shown as context. Don't imply they drive the estimate until the model says so.
- **Decided 2026-09-25: the shipped model uses 9 features, not 11.** `precip_prev_72h_mm` and
  `precip_prev_7d_mm` were dropped, not just left unused. Live scoring can't reliably supply
  them (one NWS request only covers ~40h, see TODO 1 step 2 above), and keeping them in the
  model while always feeding it `NaN` at prediction time would let the imputer silently fill
  the same fixed training-median constant on every live reading forever — mathematically
  inert, not really "included," just disguised as such. An ablation on the training data
  confirmed dropping them costs nothing: identical precision (0.615) and recall (0.533) with
  vs. without. Shipped features: `water_temp_c`, `sp_conductance_uscm`,
  `dissolved_oxygen_mgl`, `ph`, `precip_mm`, `precip_prev_24h_mm`, `precip_prev_48h_mm`,
  `turbidity_fnu_mean`, `turbidity_fnu_max`. See `app/model/train.py`.

- **Amended 2026-09-27 (Milestone 1b):** that model's reported 0.615/0.533 did not survive
  honest, date-grouped cross-validation (Navy Yard and Ben Franklin Bridge share identical
  gauge/rain features per date - ordinary folds leaked). Live scoring no longer uses this model
  to decide `risk_tier`: a disclosed rainfall rule (prior-48h rain >= 2.5mm) decides it, and a
  model retrained on Penn's Landing near-shore labels (6 features, turbidity excluded - it
  showed no significant relationship with the outcome anywhere it was tested) only sets
  `confidence`. See `docs/superpowers/specs/2026-09-27-model-honesty-fix-milestone1b-design.md`.

### No public alerting (scope decision, 2026-09-26)
An earlier version of this spec had a "TODO 2 — real `subscribe()`" here: a phone-number
opt-in, a Subscription Agent parsing plain-language alert requests, and a WhatsApp broadcaster
(OpenClaw) sending confirmed subscribers a message on status change. That's cut, deliberately,
not for time — see `docs/product-brief.md`'s "No direct-to-public alerting, by design." FHIR
delivery to RPHSA (below) is the only notification channel that exists, agency-first and
year-round. The dashboard stays exactly as described above: pull-based, public, unpersonalized.

### Alert rules (decided Sep 23, 2026; see `docs/alert-rules-decisions.md`)
- **Two levels only:** Safe / Unsafe at 235 CFU/100 mL. No Caution level.
- **Sewer overflow:** active CSO overflow near the reach (if CSOcast is usable) forces Unsafe.
  The message names the reason; never show a bacteria value we do not have. (Amended 2026-10-03:
  it does NOT alter the confidence, which is always the rule/model agreement.)
- **Freshness:** newest gauge reading older than **2 hours** -> status "unavailable", no message,
  never an all-clear. Rainfall-only fallback may still run.
- **Change of state only:** alert when the level differs from the last alerted state.
- **All-clear:** only after **48 continuous hours** of Safe; any Unsafe reading restarts the clock.
- **Agency delivery:** FHIR Flag changes go to RPHSA **year-round** — the only notification
  channel that exists (no public alerting; see the section above).
- **Still to derive:** low-confidence cutoff (from model validation), forecast rain threshold T
  (from our rainfall data), CSO outfall set near Penn's Landing (research).

## TODO 2 — make the site agent-ready
AquaSentinel should be a site agents can read reliably. Every surface below is generated from
the **same scoring output** as the banner and table; never compute status twice.

1. **Status endpoint** — `GET /api/status` (and `?location=<id>`) returns the contract:
   `{ location, time, risk_tier, confidence, source: "aquasentinel", source_url, retrieved_at,
   estimate_cfu_100ml, threshold_cfu_100ml: 235, model_version, regime, proxies: {...} }`.
   Label it an estimate in the payload (e.g. `"kind": "model_estimate"`).
2. **MCP server (read-only)** — expose these tools over MCP (Streamable HTTP transport):
   - `list_monitored_locations()` -> locations with id, name, coordinates, gauge id.
   - `get_current_status(location_id)` -> the same object as `/api/status`.
   - `get_recent_readings(location_id, limit)` -> recent scored rows.
   **No write tools.** Agents must not be able to trigger alerts or change anything. Tool
   descriptions carry the honesty language ("estimate", "flag elevated risk").
3. **`/llms.txt`** — Markdown per llmstxt.org: H1 "AquaSentinel", a one-paragraph summary,
   then links to the MCP endpoint, `/api/status`, a methodology page (data lineage, EPA
   threshold, two-regime caveat), and the honesty statement.
4. **JSON-LD on the page** — a schema.org `Dataset` for the readings (variableMeasured,
   spatialCoverage as a `Place` for the Center City reach, `dateModified` = last scored
   reading, `isBasedOn` USGS 01467200 and NOAA USW00013739). There is, to our knowledge, no
   schema.org type specific to water-safety advisories, so do not invent one here.
5. **Consistency test** — one test asserts that the MCP tool output, `/api/status`, and the
   rendered banner report the same tier and timestamp for the same reading.
6. **Map outfalls** (added 2026-10-03) — `GET /api/outfalls` serves the snapshot of the sewer outfalls the
   overflow rule considered at the last scheduled pull (Delaware-side, within `CSO_NEARBY_RADIUS_KM`, fresh
   data), with coordinates, CSOcast status, a plain-language `status_text`, and `triggering` for the outfall
   that decided the newest reading. Read-only; saved by the hourly pull (table `cso_outfalls`). The page
   draws it on the map and never calls CSOcast itself. A feed outage keeps the previous snapshot.
7. **Not in scope:** WebMCP (W3C community-group draft, Sep 2026; browser-bound).

How the Advisory Reader Agent consumes sources (for the separate agent build): prefer a
source's MCP server, then a structured feed advertised in `llms.txt`, then the HTML page
(legacy mode, heavier validation).

## Honesty guardrails (keep these in the copy)
- Label predictions as estimates; language is "flag elevated risk," never "predict illness."
- The hero map (updated 2026-09-25) is a real Leaflet.js map with real coordinates for the
  USGS gauge and the two DRBC label stations, pulled live from the USGS site service and the
  EPA Water Quality Portal station service - not estimated. It is no longer schematic, so the
  page no longer labels it "schematic, not to scale"; it credits the tile provider instead
  (Esri World Street Map, per that service's attribution requirement).

## Suggested stack
(Agent-ready layer: the MCP server and `/llms.txt` run on the same thin API server; see TODO 2.)
Static front-end (this file, or port to React) + a thin API server (`/api/status`, MCP) +
a scheduled job that pulls USGS/NOAA, scores, and (on change of state) delivers FHIR to RPHSA.
See `docs/superpowers/specs/2026-09-26-fhir-milestone4-design.md` for the FHIR delivery design.
