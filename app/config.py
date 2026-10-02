"""Central config for every rule threshold used in scoring and alerting.

Per CLAUDE.md: "Keep all rule thresholds in one config module, never inline."
Values marked TODO(decide) are placeholders the docs explicitly say not to invent —
see docs/alert-rules-decisions.md, "Still open (to be derived, not decided)".
"""

# --- Safety classification (docs/product-brief.md, docs/alert-rules-decisions.md) ---
UNSAFE_THRESHOLD_CFU_100ML = 235  # EPA single-sample max. The only tier boundary. No "Caution".
GEOMEAN_THRESHOLD_CFU_100ML = 126  # EPA 30-day geometric-mean anchor. Context only, not an alert level.

# --- Location (docs/product-brief.md) ---
# Penn's Landing, USGS gauge 01467200's own site coordinate. Single shared source - until
# Milestone 6, this existed as three separate Python copies (app/fhir/resources.py,
# app/ingestion/open_meteo.py); both now import it from here instead.
LOCATION_LAT = 39.946402
LOCATION_LON = -75.139360

# --- Alert gating (docs/alert-rules-decisions.md, decisions 2-5) ---
FRESHNESS_LIMIT_HOURS = 2  # Gauge reading older than this -> "status unavailable".
ALL_CLEAR_WINDOW_HOURS = 48  # Continuous hours of Safe required before an all-clear fires.
RECREATION_SEASON_START = (5, 1)  # (month, day) - season boundary for the public_event field
RECREATION_SEASON_END = (10, 31)  # app/alerts/gating.py still computes (currently unused - no
# direct-to-public alerting exists, see plan.md's scope decision 2026-09-26 and Open Questions).
# Agency (RPHSA) FHIR Flag delivery is year-round - no season gate for it.

# --- Scheduled pull (app/scheduler.py) ---
# Why 60: well inside FRESHNESS_LIMIT_HOURS (2h), so one failed pull still leaves the previous
# reading fresh until the next attempt - a single USGS hiccup does not flip the status to
# "unavailable". Without a scheduled pull, /api/status and MCP only ever see a reading when
# someone happens to open the dashboard.
SCHEDULED_PULL_INTERVAL_MINUTES = 60

# --- Rules fallback (derived from AquaSentinel-dataset, not invented) ---
# Derived by app/model/train.py from AquaSentinel-dataset/data/aquasentinel_labels_master.csv
# (all rows with a non-null precip_prev_48h_mm and a label, including proxy-less rows) via the
# rainfall threshold that maximizes Youden's J (TPR - FPR) on the "unsafe" target.
# Derived 2026-09-25 from aquasentinel_labels_master.csv (330 rows, 49 unsafe): precision 0.372,
# recall 0.653 in-sample. Re-derive if the dataset is rebuilt with `build_dataset.py --fetch`.
# Note (2026-09-27, Milestone 1b): this threshold is chosen on the same data it is evaluated
# against - one parameter, low overfit risk, but still in-sample. Disclosed, not hidden.
RAIN_FALLBACK_THRESHOLD_MM = 2.5

# --- Values not yet decided (do not invent numbers here) ---
# Derived 2026-09-27 by app/model/train.py:derive_low_confidence_cutoff() - the confidence
# value (see app/scoring/pull_reading.py's formula) below which the rainfall rule's decision
# and the near-shore model most often disagreed, out-of-fold, on the 69-row near-shore set.
LOW_CONFIDENCE_CUTOFF = 0.7466666666666666
FORECAST_RAIN_THRESHOLD_MM_T = None  # TODO(decide): derive from our rainfall data (NWS heads-up).

# --- CSO overflow rule (Milestone 6) ---
# Verified live 2026-10-01 against the real CSOcast feed - see
# docs/superpowers/specs/2026-10-01-cso-overflow-rule-milestone6-design.md's Scope decisions
# for why these values, not a published tidal-excursion figure (none was found).
CSO_NEARBY_RADIUS_KM = 5.0  # of 53 Delaware-tagged outfalls, 35 fall within this radius
CSO_OUTFALL_FRESHNESS_HOURS = 24  # per-outfall, not whole-feed - see the D_54 case in the spec
CSO_TRIGGER_STATUSES = (3, 4)  # 3 = overflow in past 72h, 4 = currently overflowing
# The one CSOcast code that positively says "no overflow in the past 72h". The dashboard's CSO
# field only says "No overflow" when at least one fresh nearby outfall reports this - code 0
# ("no data") is NOT evidence of no overflow.
CSO_NO_OVERFLOW_STATUS = 1
# Deliberately a plain literal, not computed from LOW_CONFIDENCE_CUTOFF - just needs to stay
# below it so a CSO-forced Unsafe always queues a Milestone 8 confirmatory sample.
CSO_OVERRIDE_CONFIDENCE = 0.3
