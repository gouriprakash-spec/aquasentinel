# AquaSentinel — Training Dataset (Data Dictionary)

*Tidal-Delaware proof-of-concept. Built 2026-09-05; splits and documentation
corrected 2026-09-19. Every value is real, pulled from the public record by
`build_dataset.py` — nothing is synthesized.*

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
| `regime_A_pre2021.csv` | 250 | Pre-2021 **modeled** samples. Features exclude turbidity (it did not exist yet). |
| `regime_B_post2021.csv` | 60 | Post-2021 **modeled** samples. All carry core proxies + turbidity. |
| `build_dataset.py` | — | Reproducible builder. `python build_dataset.py --fetch` re-pulls everything. |
| `raw/` | — | Unmodified API responses, for audit. |

## Row reconciliation — why 330 ≠ 250 + 60

The master holds **every** joined E. coli sample (330). A row only enters a regime
training file if it carries **at least one USGS proxy reading** — that is the
`proxy_available` flag. Rows where every proxy is null (a lab label + rainfall but
no gauge reading that day) carry no signal for a proxy model, so they are held out
of the regime files but retained in the master.

```
master ................ 330
  modeled (proxy_available = 1) ... 310  = regime A (250) + regime B (60)
  proxy-less, held out (=0) ....... 20   (16 pre-2021 + 4 post-2021; 4 are unsafe)
```

The 20 held-out rows are real, public-record labels (4 of them unsafe). They are
kept in the master because positives are scarce and because they can feed the
rainfall-only **rules fallback** and provenance — but they must not train the
tree-ensemble / turbidity models, whose features they lack. Filter on
`proxy_available == 1` to get the modeled set.

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

**Split control (master only)**

- `proxy_available` — 1 if the row carries ≥1 USGS proxy reading (enters a regime
  file); 0 if every proxy is null (held out of regime training, kept in master).

## Class balance (the real constraint)

| Set | Rows | Unsafe (≥235) | Unsafe % | Complete-feature rows |
|-----|------|---------------|----------|-----------------------|
| A pre-2021 (modeled) | 250 | 30 | 12% | 234 |
| B post-2021 (modeled) | 60 | 15 | 25% | 60 |
| Held out (proxy-less) | 20 | 4 | 20% | 0 |
| **Master total** | **330** | **49** | **15%** | — |

The binding constraint is the **positive count** (30 and 15 in the two modeled
regimes), not the row count. This is why the plan is tree ensembles + a regression
baseline reporting **precision/recall on the unsafe class**, not R², with
stratified cross-validation — and why the agency-sampling loop (which manufactures
new positive labels where the model is unsure) matters.

*As built (2026-10-04): the live model is the near-shore one (69 days, 30 unsafe; see the
addendum below), a rainfall rule decides the tier, and the agency-sampling loop was cut
2026-10-02 (a Future direction).*

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
7. **Proxy-less rows held out (330 vs 310).** 20 sample rows (16 pre-2021,
   4 post-2021; 4 unsafe) have a lab label + rainfall but no USGS proxy reading for
   that date. They are kept in the master (flagged `proxy_available = 0`) and
   excluded from both regime training files by the same drop-all-proxy-null rule.
   Earlier builds applied that rule to regime A only, leaving 4 feature-less rows in
   regime B — corrected 2026-09-19 (regime B: 64 → 60).
8. **Turbidity raw is large.** `usgs_iv_turbidity_raw.csv` (about 20 MB) is kept in `raw/`
   (restored 2026-09-27; its daily aggregates match regime B exactly). If it is ever missing,
   an offline rebuild reproduces every column except turbidity (the builder degrades
   gracefully and warns); run `build_dataset.py --fetch` to restore it and rebuild end-to-end.

## Addendum 2026-09-27 — `data/nearshore_labels.csv` (Milestone 1b, approved and shipped)

**Supersedes the pooled `regime_B_plus_nearshore.csv` approach originally proposed the same day**
(see `docs/superpowers/specs/2026-09-27-model-honesty-fix-milestone1b-design.md` Section 2b.4).
`regime_B_plus_nearshore.csv` still exists, unmodified, but is no longer used by any live code.

All 69 Penn's Landing near-shore label-days (2019-2025), 30 unsafe. Built by
`build_dataset.py:build_nearshore()` from `raw/drbc_nearshore_ecoli_2019_2025.csv` (DRBC
near-shore program via WQP, MPN/100mL) — moved into this repo 2026-09-27; it previously lived
outside the repo entirely and was not reproducible from a clean clone.

- Sources collapsed to one row per date (MAX value, conservative): `DRBC-DEL-LL` (Penn's Landing
  Lagoon, 2019-2022), `DRBC-6107-049..055` (2024 cluster within ~1km of the lagoon), `DRBC-C1..C5`
  (2021 cross-section transect). The 2024 rows take the max over up to 7 sites, which inflates
  "unsafe".
- Near-shore values are MPN/100mL (`unit` says so) but stored as `ecoli_mpn_100ml`; treated as
  equivalent to CFU/100mL at the 235 threshold.
- Joined to USGS 01467200 daily proxies and NCEI PHL precipitation via `build_dataset.py`'s
  existing loaders. Turbidity is joined when present but **not used as a model feature** — it
  showed no significant relationship with the outcome here or anywhere else it was tested
  (Mann-Whitney p=0.181; ROC AUC 0.271; dropping it from the honest model changes nothing beyond
  noise). It is still a genuine measurement, just not one the model uses.
- **Split any cross-validation by `date`** even though this file already has at most one row per
  date (defense in depth, matching the same convention as every other labeled file here).

## Provenance

Built by `build_dataset.py` from public APIs on 2026-09-05 (splits/docs corrected
2026-09-19): USGS NWIS (`waterservices.usgs.gov`), EPA Water Quality Portal
(`waterqualitydata.us`), NOAA NCEI (`ncei.noaa.gov`). Re-run with `--fetch` to
refresh. Raw responses are preserved in `raw/` for audit.
