# Milestone 1b — Model honesty fix and rule-first pivot (incremental)

Status: **APPROVED 2026-09-27** (Gouri), after an independent fresh re-derivation of every number
below (see Section 2b) confirmed the leak, confirmed the rule's signal on all three datasets, and
surfaced one correction to this draft's own reasoning: turbidity does not hold up as a real
feature anywhere it was tested (Section 2b.3) — the near-shore training set is widened from 14 to
69 rows as a direct result (Step 2, revised). Nothing below has been applied to `app/` yet; this
document is the approved design, the implementation plan is next. When implemented, record the
pivot as decision 6 in `docs/alert-rules-decisions.md` (authority order: decisions log, then
BUILD-SPEC, then brief).

This is an **incremental fix on top of Milestones 1 and 2**, not a rewrite. What was built stays; the
record of what was built, and why it changed, is kept below so the history is readable later.

---

## 1. What was implemented (preserved record, as of 2026-09-25)

- **Milestone 1 (DONE 2026-09-25).** Random forest on the post-2021 (turbidity) regime, 60 rows / 15
  unsafe, labels from DRBC Ben Franklin Bridge (892071) + Navy Yard (892065), proxies from USGS
  01467200, rainfall from NCEI PHL. Reported precision 0.615 / recall 0.533 (5-fold `StratifiedKFold`).
  Pre-2021 model evaluated and dropped (0.346 / 0.30) because the rainfall rule beat it.
  Rules fallback derived: prior-48h rain >= 2.5 mm (`config.RAIN_FALLBACK_THRESHOLD_MM`),
  in-sample precision 0.372 / recall 0.653 on 330 rows.
- **Milestone 2 (DONE 2026-09-25).** Live `pull_reading()`: USGS 01467200 proxies + NWS KPHL rain.
  One NWS request covers only ~38-40 h, so the model was cut to 8 features using `precip_mm` +
  `precip_prev_24h_mm` (48h/72h/7d dropped; ablation showed no change at the time).
  The model decides the tier (`predict_proba >= 0.5`); confidence = the winning class probability.
  The rules fallback exists in `app/model/rules_fallback.py` but is not wired into live scoring.

## 2. What we found (2026-09-27)

Full numbers: `AquaSentinel-dataset/nearshore_experiment/README.md`.

1. **The shipped metrics leak.** Regime B is 30 dates x 2 stations. Both stations get the *same*
   gauge and rain features on the same day, so random row-level folds put identical feature rows in
   train and test. Re-run with folds grouped by date (`StratifiedGroupKFold`, 10 seeds):
   precision 0.19 / recall 0.09 (vs 0.54 / 0.46 ungrouped on the same seeds). The 0.615 / 0.533
   figure should not be presented.
2. **The rainfall rule beats the model under honest validation.** Channel stations, regime B:
   48h rule P 0.55 / R 0.73 / F1 0.63; 24h rule F1 0.44; RF (date-grouped) F1 0.12.
3. **Near-shore labels exist and fit the gauge better.** `drbc_nearshore_ecoli_2019_2025.csv` has
   DRBC near-shore samples at Penn's Landing: Lagoon DRBC-DEL-LL (0.17 km from the gauge, 2019-2022),
   the 2024 DRBC-6107-049..055 cluster, and the 2021 C1-C5 transect. Collapsed to one label per date
   (max): 69 days / 30 unsafe; 14 days / 8 unsafe in the turbidity era.
   Without turbidity, near-shore F1 ~0.6 vs ~0.1 for channel stations.
4. **Pooling helps, a little.** Regime B + near-shore (date-grouped): channel F1 0.12 -> 0.25;
   near-shore P 0.90 / R 0.75 on 14 days (directional only, n too small).
5. **Turbidity does not separate near-shore labels** (gauge mean 6.0 FNU unsafe vs 6.9 safe); it does
   for channel labels (10.0 vs 6.9). The gauge's turbidity reflects the channel, not the shoreline.
6. Raw turbidity IV restored to `AquaSentinel-dataset/raw/usgs_iv_turbidity_raw.csv`; daily
   aggregates reproduce `regime_B_post2021.csv` exactly (60/60 rows).

## 2b. Independent verification and one correction (2026-09-27, same day, fresh code)

Gouri asked for the leak and every headline number to be re-derived independently before
approving - fresh code, not a re-run of `nearshore_experiment/compare_models.py`, so this is a
second, separate check rather than a re-print of numbers already in question.

1. **The leak, confirmed independently.** Regime B honest (date-grouped) RF: precision 0.193 /
   recall 0.073 / F1 0.105 (10-seed average) - matches item 1 above within noise. Regime A has the
   *same* per-date feature duplication (all 125 dates, both stations share identical features) and
   the *same* collapse under honest validation: row-level F1 0.307, date-grouped F1 0.056. The
   pre-2021 model's originally-reported 0.346/0.30 (Section 1) was never date-grouped either and
   should be read with the same caution as the shipped 0.615/0.533.
2. **The rule holds up on all three datasets, not just regime B**, including plain accuracy (which
   is *not* the right metric here - see the caution below): regime A 78.4% accuracy (P 0.29 / R
   0.57), regime B 78.3% (P 0.55 / R 0.73), near-shore 78.6% (P 1.00 / R 0.63). Caution: "always
   guess Safe" scores 88.0% on regime A and 75.0% on regime B - *higher* than the rule on raw
   accuracy alone, purely because Unsafe is a minority class there. Precision/recall are what show
   the rule is doing real work; accuracy alone would make regime A's rule look like it's failing.
3. **Turbidity does not hold up as a feature anywhere it was tested - this corrects item 5 above.**
   Item 5's "10.0 vs 6.9" channel-label gap looked like turbidity mattered for channel stations; a
   significance test says otherwise: Mann-Whitney p=0.885 (channel, regime B) and p=0.181
   (near-shore) - both far from significant, and channel turbidity's ROC AUC is 0.487 (at-chance).
   Removing turbidity from the honest RF changes nothing beyond noise everywhere it was checked:
   regime B F1 0.105 -> 0.099; near-shore-alone (n=14) F1 0.780 -> 0.775; pooled-evaluated-on-near-
   shore F1 0.790 -> 0.800 (very slightly *better* without it). Turbidity stays on the dashboard as
   honest evidence/context - it is dropped only as a model feature.
4. **Consequence of #3: the near-shore training set widens from 14 to 69 rows.** Restricting to the
   turbidity era was only necessary because the shipped feature set included turbidity. Once it's
   dropped, all 69 near-shore label-days (2019-2025, `pennslanding_nearshore_labels.csv`) become
   usable, not just the 14 that happen to fall after 2021-10-28. This also matters for validation
   trustworthiness: the n=14 subset's F1 0.78 turned out to be an artifact of the tiny sample - on
   the full 69 rows, honest (5-fold, 10-seed) RF F1 drops to **0.562** (P 0.623 / R 0.513), while
   the rule on the same 69 rows scores F1 **0.643** (P 0.692 / R 0.600) - the rule now *clearly*
   beats the model, where the 14-row comparison had made them look roughly tied. The n=14 number
   should not be presented as the model's expected performance; 0.562 on n=69 is the honest one.
5. **3-way ensemble across datasets: tested empirically, rejected.** A majority vote across models
   trained separately on regime A, regime B, and near-shore, evaluated on the 14 near-shore
   turbidity-era days: the regime-A model predicted "Safe" for every single day (0 precision, 0
   recall - no transferable signal from a different station, a different era, and a different
   feature set), and folding it into a vote made the ensemble *worse* than near-shore alone (F1
   0.714 vs 0.750). Not pursued further.
6. **CSOcast (Milestone 6) cannot help this milestone.** Its feed is real-time-only (`timeInfo` and
   `archivingInfo` both absent from the ArcGIS service; exactly 164 rows, one per outfall, each
   overwritten in place on every poll - confirmed live, not assumed). There is no historical
   overflow record to join to past labeled dates, so it has no role in training or retraining the
   model; it stays a live-only input to the separate CSO overflow rule.

## 3. The pivot

| | Before (M1/M2 as built) | After (M1b) |
|---|---|---|
| Decides the tier | Random forest, p >= 0.5 | **Rainfall rule** (prior-48h >= 2.5 mm), deterministic |
| Role of the RF | The sensor | **Confidence signal**: agreement with the rule |
| Confidence | Winning-class probability | Low when rule and model disagree |
| Reference label | Channel stations (BFB + Navy Yard) | **Penn's Landing near-shore**, all 69 label-days (2019-2025) |
| Model features | 8, incl. turbidity | **6, turbidity dropped** (Section 2b.3: no significant relationship anywhere it was tested; stays a dashboard evidence value, not a model input) |
| Validation | Row-level stratified k-fold | **Date-grouped** stratified k-fold |
| Sampling trigger (M8) | Unsafe OR low confidence | Same rule, now driven by rule/model disagreement |

This keeps every architecture rule in `CLAUDE.md`: code decides, fail closed, two levels, honest
language. It strengthens "code decides, agents don't": the decider is now a one-line, disclosed rule.

## 4. Incremental changes, in order

Each step is independently shippable. Stop anywhere and the system still works.

### Step 1 — Honest validation (MUST, ~1 h)
- `app/model/train.py`: replace `StratifiedKFold` with `StratifiedGroupKFold(groups=date)`; average
  over several seeds. Keep the old row-level number in `training_report.json` under a key marked
  `superseded_leaky_row_level_cv`, not deleted.
- Evaluate the rules fallback on the same folds, so rule and RF are compared on identical splits.
  Re-derive the rain threshold inside each training fold (not once on the full master), so the
  rule's score is out-of-sample like the RF's.
- Report each dataset separately (regime A, regime B, near-shore) as well as pooled.
- Test: a fold assertion that no date appears in both train and test.
- Done when: `training_report.json` shows date-grouped RF and rule metrics side by side.

### Step 2 — Train the confidence-signal model on near-shore labels (MUST, ~1 h; data prep DONE)

**Revised 2026-09-27 per Section 2b.3/2b.4** - the original version of this step trained on
`regime_B_plus_nearshore.csv` (channel + only the 14 turbidity-era near-shore rows). That file
still exists and is fine to keep for reference, but it is **not** what gets trained on now:

- Turbidity is dropped from the feature set (Section 2b.3 - no significant relationship anywhere
  it was tested; keep showing it on the dashboard as evidence, just not as a model input).
- Channel pooling is dropped, not carried forward - channel labels never produced a working model
  (Section 2b.1), and pooling them into near-shore training wasn't tested against the corrected
  6-feature, 69-row near-shore set. If someone wants to test whether pooling still helps under the
  revised feature set, that's a fine thing to try during implementation, but it is not required:
  the near-shore-alone number (F1 0.562, Section 2b.4) is already the approved baseline.
- Train directly on `nearshore_experiment/pennslanding_nearshore_labels.csv` (69 rows, 30 unsafe,
  2019-2025) with the 6-feature set (water_temp_c, sp_conductance_uscm, dissolved_oxygen_mgl, ph,
  precip_mm, precip_prev_24h_mm). Move its builder into `build_dataset.py` as a proper output
  rather than leaving it a one-off experiment script.
- Live scoring doesn't need a `nearshore` flag feature (that was for the pooled channel+near-shore
  approach, no longer used) - there's only one location, and it always is Penn's Landing near-shore.
- Update `DATA-DICTIONARY.md`: sources (DRBC-DEL-LL, the 2024 DRBC-6107-049..055 cluster, the 2021
  C1-C5 transect), max-per-date collapse, MPN (near-shore) vs CFU (channel) units treated as
  equivalent at 235, the 2024 max-over-7-sites caveat, and a note that turbidity was evaluated and
  dropped as a feature (Section 2b.3) though it remains a displayed evidence value.

### Step 3 — Live 48h rainfall (SHOULD, ~3-4 h, highest value)
- `app/ingestion/nws.py`: fetch two windows (now-48h to now-24h, and last 24h) instead of one
  `limit=500` request, then compute `precip_prev_48h_mm` with the existing 3-hour-mark method.
  Verify the NWS observations endpoint's `start`/`end` parameters before relying on them.
- Fail closed if the 48h window is not fully covered (`RainfallUnavailable`).
- **Window mismatch to resolve (training vs live).** The rule's threshold was derived on
  `precip_prev_48h_mm` from `build_dataset.py:load_precip()`: NCEI daily totals for the two
  *calendar days before* the sample date, sample day excluded. Live, a rolling 48h window ending
  "now" is a different window: it includes rain from earlier today and cuts yesterday and the day
  before at the current clock time. Options: (a) match training by summing the two prior local
  calendar days (midnight to midnight, US/Eastern) from NWS observations; (b) keep rolling 48h and
  disclose the mismatch. Recommend (a): it matches how 2.5 mm was derived. Today's rain can still
  be shown in `evidence`, and it is what the separate `precip_mm` feature already carries.
  Note: option (a) needs data back to ~48-72h before "now" depending on time of day, so the
  two-request fetch must cover up to 72h.
- Minor: the dataset's `rain_prev_48h_flag` uses `> 2.5`; the rule uses `>= 2.5`. Keep `>=` (the
  derived threshold) and note the difference in `DATA-DICTIONARY.md`.
- Tests: fixture covering 48h across two pages; gap -> fail closed.
- If this slips: run the rule on 24h instead. Channel F1 was 0.44 in the original check (Section 1);
  the near-shore comparison needs re-running against the corrected 69-row set (Section 2b.4) during
  implementation - the 0.77 figure quoted in an earlier draft of this step was the n=14 number and
  should not be reused. Whichever window ships, say so on the dashboard's methodology note.

### Step 4 — Rule decides, model informs confidence (MUST, ~2 h)
- `app/scoring/pull_reading.py`: tier = `classify_by_rainfall(precip_prev_48h_mm)` (or 24h per Step 3).
  The near-shore-trained RF from Step 2 (6 features, no turbidity) still runs; its
  `probability_unsafe` goes into `evidence`. Turbidity is still fetched and shown in `evidence`'s
  proxies (it's real, honest context) - it is simply not one of the 6 values passed into the model.
- Confidence: high when rule and RF agree, low when they disagree. Set `LOW_CONFIDENCE_CUTOFF`
  from date-grouped validation (closes the existing `TODO(decide)` in `config.py`).
- Contract unchanged in shape; `evidence` gains `decision_basis: "rainfall_rule"`,
  `rule_threshold_mm`, `model_probability_unsafe`.
- Check `app/fhir/` so the Observation's method / derivedFrom describe the rule, not "random forest".
- Tests: rule/model agree -> high confidence; disagree -> low confidence; missing rain -> unavailable.

### Step 5 — Sampling Coordinator trigger (part of M8, no new build)
- Trigger stays "Unsafe OR low confidence"; low confidence now means rule/model disagreement.
- Target location: Penn's Landing near-shore. Each returned label grows the near-shore set (69
  days as of 2026-09-27, per Section 2b.4).
- Demo claim unchanged: architecture plus one scripted turn, no measured accuracy gain.

### Step 6 — Docs (MUST, ~1 h). Amend, do not rewrite.
- `plan.md` Milestone 1: append "Amended 2026-09-27 (M1b)" with the date-grouped numbers; leave the
  original text in place.
- `docs/alert-rules-decisions.md`: add decision 6 (rule-first; model as confidence).
- `docs/landing-page/BUILD-SPEC.md` line ~84 and `docs/product-brief.md` "Model choice": replace the
  0.615 / 0.533 claim; add a line that finding and fixing the leak is part of the data-quality story.
- `config.py` comments: note the rule threshold (2.5 mm) was derived in-sample.

## 5. Cut line

**Revised 2026-09-27:** Step 2 moves from "Should" to "Must", alongside Steps 1, 4, and 6. Step 4's
confidence signal only means anything if it comes from a model with real, honestly-validated skill
(Section 2b.4) - falling back to the disproven channel-trained model would just reintroduce the
same dishonesty this milestone exists to fix. If time runs out for Step 2 anyway, Step 4 ships with
the rule as the sole decider and confidence left undifferentiated (e.g. fixed high) rather than
faked from a model already shown to be noise - never resurrect the channel model for this.

Must: Steps 1, 2, 4, 6. Should: 3 (24h fallback if it slips - re-derive the fallback's near-shore
number against the 69-row set first, per Step 3's note). Step 5 rides on M8 (Sampling Coordinator,
renumbered from M6 - see plan.md's 2026-09-27 milestone reorder). Build window ends Oct 4 (Oct 3-4
reserved).

## 6. Honesty notes

- Near-shore model training set is 69 rows (2019-2025), not 14 - the 14-row turbidity-era subset
  turned out to give an inflated F1 (0.78) purely from its small size; the honest number on the
  full 69 rows is F1 0.562 (Section 2b.4). Present 0.562, not 0.78.
- The 2.5 mm threshold was chosen on the same data it is evaluated on (one parameter, low overfit
  risk, but still in-sample). Say so.
- The rule rests mostly on regime A: the threshold was derived from the 330-row master (266 regime A
  rows, 64 regime B). The shipped RF used regime B only. Under rule-first, the decider is built
  mainly on pre-2021 data; check it holds on regime B and near-shore separately (it did on
  2026-09-27, re-confirmed on the corrected 69-row near-shore set: channel regime B F1 0.63,
  near-shore F1 0.643, both in-sample for the threshold).
- Turbidity was evaluated as a model feature and dropped (Section 2b.3): no significant relationship
  with the outcome anywhere it was tested (regime B channel, near-shore alone, or pooled). It stays
  a real, honest evidence value shown on the dashboard - just not a model input. Say so if asked why
  a proxy the model ingests isn't one it decides with.
- Near-shore labels are a max across nearby sites per date; conservative, slightly inflates "unsafe".
- Deck language: "a transparent rainfall rule decides; the model flags when it disagrees, and the
  system samples where it is unsure."

## 7. Deferred

- Daily turbidity at 01467200 from 2008-09-10 to 2011-12-12 (older sensor; instrument-change risk).
- Washington Ave. Green (DRBC-DEL-WA) as a second near-shore site.
- Gauge discharge (00060, from 2023-09) as a candidate feature.

## 8. Open questions - resolved 2026-09-27

1. **Approve rule-first (tier decided by the rainfall rule)?** Yes. Reinforced, not just approved:
   the near-shore correction (Section 2b.4) shows the rule *clearly* beating the model on the more
   trustworthy 69-row sample, where the original 14-row comparison had made them look tied.
2. **If Step 3 slips, is a 24h rule acceptable for the demo?** Not fully decided - genuinely
   undecided pending Step 3's real engineering feasibility. Default: attempt the 48h fetch as
   designed first, since that's what the 2.5mm threshold was actually derived against; treat 24h as
   a disclosed fallback only if 48h proves infeasible in the time remaining, and re-derive the near-
   shore 24h number against the corrected 69-row set before using it (Step 3's note).
3. **Keep the RF at all, or show rule-only plus the sampling loop?** Keep it, but only as a
   confidence/agreement signal, retrained on near-shore labels only (69 rows, 6 features, no
   turbidity - Section 2b.3/2b.4), never as the decider. A channel-trained model must not be reused
   for this even as a fallback (Section 5's revised cut line).
