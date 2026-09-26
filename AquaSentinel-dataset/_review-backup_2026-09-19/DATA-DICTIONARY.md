# AquaSentinel — Training Dataset (Data Dictionary)

*Tidal-Delaware proof-of-concept. Built 2026-09-05. Every value is real, pulled
from the public record by `build_dataset.py` — nothing is synthesized.*

## What this is

A supervised training set that pairs real-time water-quality **proxies** with
lab-confirmed **E. coli labels** so a model can estimate present-day recreational
safety before an 18–24h lab culture would return. It is deliberately built on the
**tidal Delaware** — the reach Philadelphia's RiverCast does not cover.

- **Label source:** DRBC E. coli grab samples (EPA Water Quality Portal) at
  Ben Franklin Bridge (`31DELRBC_WQX-892071`) and Navy Yard (`31DELRBC_WQX-892065`).
- **Proxy source:** USGS gauge `01467200` (Delaware R at Penn's Landing) — daily
  water temperature, specific conductance, dissolved oxygen, pH; and turbidity
  (continuous from 2021-10-28).
- **Rainfall source:** NOAA NCEI daily precipitation, Philadelphia Intl Airport
  (`USW00013739`).

## Files

| File | Rows | What it is |
|------|------|-----------|
| `aquasentinel_labels_master.csv` | 330 | Every joined sample, both regimes, all columns. The source of truth. |
| `regime_A_pre2021.csv` | 250 | Pre-2021 samples. Features exclude turbidity (it did not exist yet). |
| `regime_B_post2021.csv` | 64 | Post-2021 samples. Includes turbidity (60/64 rows). |
| `build_dataset.py` | — | Reproducible builder. `python build_dataset.py --fetch` re-pulls everything. |
| `raw/` | — | Unmodified API responses, for audit. |

## The two regimes — validate them SEPARATELY

Continuous turbidity at Penn's Landing begins **2021-10-28**. Turbidity is one of
the strongest bacteria predictors, so a model trained across the boundary would
silently learn "turbidity present = recent = different" rather than a real signal.
Train, tune, and report metrics on Regime A and Regime B **independently**. Do not
concatenate them.

## Columns

**Identifiers**

- `station_id`, `station_name` — DRBC monitoring location.
- `date` — sample date (`ActivityStartDate`).
- `year`, `month`, `recreation_season` — `recreation_season` = 1 for May–Sep.
- `regime` — `A_pre2021` or `B_post2021`.

**Proxy features (USGS 01467200, daily)**

- `water_temp_c` — daily mean water temperature (°C).
- `sp_conductance_uscm` — daily mean specific conductance (µS/cm).
- `dissolved_oxygen_mgl` — daily mean dissolved oxygen (mg/L).
- `ph` — daily median pH.
- `turbidity_fnu_mean`, `turbidity_fnu_max` — daily mean/max turbidity (FNU).
  *Regime B only; blank before 2021-10-28.*

**Rainfall features (NCEI PHL airport, daily, mm)**

- `precip_mm` — precipitation on the sample day.
- `precip_prev_24h_mm`, `precip_prev_48h_mm`, `precip_prev_72h_mm`,
  `precip_prev_7d_mm` — antecedent rainfall over prior windows (exclude sample day).
- `rain_prev_48h_flag` — 1 if prior-48h rain > 2.5 mm (~0.1 in).

**Labels**

- `ecoli_cfu_100ml` — measured E. coli (CFU/100 mL).
- `value_censored` — 1 if the result was a non-detect or a `<`/`>` censored value.
- `unit` — always `cfu/100mL`.
- `unsafe` — **primary target.** 1 if `ecoli_cfu_100ml` ≥ 235 (EPA single-sample max).
- `exceeds_geomean_126` — 1 if ≥ 126 (EPA 30-day geometric-mean anchor). Secondary.

## Class balance (the real constraint)

| Regime | Rows | Unsafe (≥235) | Unsafe % | Complete-feature rows |
|--------|------|---------------|----------|-----------------------|
| A pre-2021 | 250 | 30 | 12% | 234 |
| B post-2021 | 64 | 16 | 25% | 60 |

The binding constraint is the **positive count** (30 and 16), not the row count.
This is why the plan is tree ensembles + a regression baseline reporting
**precision/recall on the unsafe class**, not R², with stratified cross-validation
— and why the agency-sampling loop (which manufactures new positive labels where
the model is unsure) matters.

## Honest caveats (read before modeling)

1. **Cross-agency, cross-location join.** Proxies (USGS, mid-channel at Penn's
   Landing) and labels (DRBC, at the two stations) are matched by **date**, not by
   co-located instrument. The two stations bracket the gauge; treat the proxy row
   as a shared daily condition, not a point measurement at each station.
2. **Daily granularity on a tidal river.** Proxies are daily aggregates; the
   Delaware is tidal, so within-day variation is real and is averaged out here.
   Acceptable for a POC; a production version would align on sample time and tide.
3. **Rainfall is a metro proxy.** PHL airport is ~7 miles from the stations and
   from the upstream CSO outfalls that actually drive contamination. It captures
   storm timing well, basin-specific intensity less well.
4. **Non-detects set to 1 CFU.** Non-detect labels (no numeric value) are encoded
   as 1 CFU/100 mL — clearly safe. `value_censored` flags every such row (15 total)
   so you can exclude or re-handle them.
5. **Same-day duplicates → max.** Where a station had multiple results on one date,
   the maximum is kept (conservative for a safety label).
6. **2020 gap.** No samples in 2020 (likely COVID-era sampling pause).

## Provenance

Built by `build_dataset.py` from public APIs on 2026-09-05:
USGS NWIS (`waterservices.usgs.gov`), EPA Water Quality Portal
(`waterqualitydata.us`), NOAA NCEI (`ncei.noaa.gov`). Re-run with `--fetch` to
refresh. Raw responses are preserved in `raw/` for audit.
