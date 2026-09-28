# Milestone 1b — Model Honesty Fix and Rule-First Pivot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the cross-validation leak in Milestone 1's shipped model, and pivot live scoring
from "the trained model decides the tier" to "a disclosed rainfall rule decides the tier, a
model retrained on near-shore labels tells you how much to trust that decision."

**Architecture:** `app/model/train.py` gains honest (date-grouped) validation for the existing
channel model and a new evaluation/training path for a near-shore-only model (6 features, no
turbidity, 69 rows). `app/ingestion/nws.py` and `app/ingestion/open_meteo.py` learn to supply a
real, calendar-day-based `precip_prev_48h_mm` live. `app/scoring/pull_reading.py` is rewired so
`app.model.rules_fallback.classify_by_rainfall()` decides `risk_tier`, and the near-shore model's
`probability_unsafe` only feeds `confidence`. `app/fhir/resources.py`'s Observation text is
corrected to describe the rule, not "random forest." Docs are amended (not rewritten) to carry
the honest numbers forward.

**Tech Stack:** Python 3.11, scikit-learn (`StratifiedGroupKFold`, `RandomForestClassifier`,
`roc_curve`), pandas, httpx. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-27-model-honesty-fix-milestone1b-design.md`

## Global Constraints

- **Rule-first, not model-first.** `app.model.rules_fallback.classify_by_rainfall()` decides
  `risk_tier` in live scoring. The near-shore model's `probability_unsafe` only ever feeds
  `confidence` — it must never gate `risk_tier` (spec Section 3).
- **Near-shore model features are exactly these 6, in this order** (spec Section 3, "Model
  features" row): `water_temp_c`, `sp_conductance_uscm`, `dissolved_oxygen_mgl`, `ph`,
  `precip_mm`, `precip_prev_24h_mm`. Turbidity is excluded — no significant relationship with
  the outcome was found anywhere it was tested (spec Section 2b.3).
- **Never resurrect the channel-trained model (`rf_B_post2021.joblib`) as a confidence
  fallback**, even if the near-shore retrain runs out of time (spec Section 5's revised cut
  line). If the near-shore model can't be built, the rule ships alone with confidence left
  undifferentiated — never a model already shown to carry no real signal.
- **All cross-validation must be date-grouped** — no date's rows may appear in both a fold's
  train and test split (spec Section 2b.1, the leak's root cause).
- **Don't invent numbers.** Any new threshold (the rules-fallback rain threshold, the new
  low-confidence cutoff) is derived from real data via the existing Youden's-J-on-`roc_curve`
  pattern already used by `app/model/train.py:derive_rain_fallback_threshold()`, then copied
  into `app/config.py` by hand — matching how `RAIN_FALLBACK_THRESHOLD_MM` already works.
- **Fail closed.** A `precip_prev_48h_mm` that can't be reliably computed from live data raises
  `RainfallUnavailable` — never a guessed or partial rainfall figure.
- **Turbidity stays a displayed evidence value.** Dropping it as a *model feature* must not
  remove it from `evidence.proxies` in `pull_reading()`'s output — it's honest context, just not
  a model input (spec Section 2b.3, Step 4).
- **The shared signal contract's shape is unchanged:**
  `{ location, time, risk_tier, confidence, source, source_url, retrieved_at, evidence }`.
  `evidence` gains `decision_basis`, `rule_threshold_mm`, `model_probability_unsafe` (spec Step 4).
- **Existing CSVs are never modified or deleted** — `regime_A_pre2021.csv`,
  `regime_B_post2021.csv`, and `regime_B_plus_nearshore.csv` all stay exactly as they are. New
  files are added alongside them (spec Step 2). `regime_B_plus_nearshore.csv` becomes unused by
  live code after this plan but is not deleted — it's a superseded experiment, not garbage.
- **Docs are amended, not rewritten** (spec Step 6) — preserve each file's original text and add
  clearly dated corrections, the same pattern already used throughout this project's docs.

## Review Focus

- **The `>=` boundary on the rainfall rule.** `precip_prev_48h_mm` exactly equal to
  `RAIN_FALLBACK_THRESHOLD_MM` must classify Unsafe, not Safe — `classify_by_rainfall()` already
  uses `>=` (not `>`), but nothing currently pins this at the exact boundary value. Covered in
  Task 5.
- **Open-Meteo's fallback path must supply the same `precip_prev_48h_mm` NWS does, honestly.**
  A reader might assume only the primary (NWS) source was upgraded for the rule to work; if
  Open-Meteo's fallback still only returns 24h, the *rule itself* (not just a proxy) silently
  breaks the moment NWS has any hiccup, which is a live rainfall source used every single reading,
  not an edge case. Covered in Task 4.
- **A live reading whose 48h rainfall can't be computed must fail exactly the way every other
  live-fetch failure already does** — `HTTPException(503)` from `/api/pull-reading`, no fake
  reading shown to the dashboard. This isn't new code (the existing `RainfallUnavailable` →
  `503` handler in `app/server.py` already covers it), but nothing currently proves the *new*
  48h-specific failure path reaches that same handler. Covered in Task 5.
- **`LOW_CONFIDENCE_CUTOFF` moving from `None` to a real number.** Nothing in the current
  codebase consumes it yet (the Sampling Coordinator, Milestone 8, isn't built), but a reader
  might assume setting it silently activates some other gate. Task 3's test confirms only
  `config.LOW_CONFIDENCE_CUTOFF` itself changes — no other module's behavior does.
- **The FHIR Observation delivered to RPHSA must correctly reflect the actual decision basis**
  once it's the rule and not the model — a person reading a Flag off the wire, mid-pivot, could
  otherwise see the old "Estimated (modeled) risk" text and reasonably conclude a random forest
  is still deciding tiers when it isn't. Covered in Task 6.

---

### Task 1: Honest date-grouped validation for the shipped channel model

**Files:**
- Modify: `app/model/train.py`
- Test: `app/tests/test_model.py`

**Interfaces:**
- Consumes: nothing new — works on the existing `regime_B_post2021.csv` and
  `aquasentinel_labels_master.csv`.
- Produces: `evaluate_model()`'s return dict gains new keys (see Step 3) that Task 3 does not
  depend on — this task is self-contained.

- [ ] **Step 1: Write the failing tests**

Add to `app/tests/test_model.py` (the existing imports already cover what's needed):

```python
def test_evaluate_model_reports_date_grouped_metrics_not_just_row_level():
    metrics = train.evaluate_model()

    assert "random_forest" in metrics  # now the HONEST, date-grouped number
    assert "random_forest_superseded_leaky_row_level_cv" in metrics  # old number, kept not deleted
    assert metrics["random_forest"]["precision"] != metrics["random_forest_superseded_leaky_row_level_cv"]["precision"]


def test_evaluate_model_reports_the_rainfall_rule_on_the_same_folds():
    metrics = train.evaluate_model()

    assert "rainfall_rule" in metrics
    assert 0.0 <= metrics["rainfall_rule"]["precision"] <= 1.0
    assert 0.0 <= metrics["rainfall_rule"]["recall"] <= 1.0


def test_date_grouped_cv_never_splits_a_date_across_train_and_test():
    """The leak's root cause (spec Section 2b.1): Navy Yard and Ben Franklin Bridge share a
    date and identical gauge/rain features. A fold assertion, not just a metric check."""
    import pandas as pd
    from sklearn.model_selection import StratifiedGroupKFold

    df = pd.read_csv(train.MODEL_DATA_FILE)
    y = df[train.TARGET].astype(int).to_numpy()
    dates = df["date"].to_numpy()
    n_splits = min(5, y.sum(), (1 - y).sum())
    cv = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=train.RANDOM_STATE)

    for train_idx, test_idx in cv.split(df, y, groups=dates):
        train_dates = set(dates[train_idx])
        test_dates = set(dates[test_idx])
        assert train_dates.isdisjoint(test_dates)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_model.py -k "date_grouped or rainfall_rule_on" -v`
Expected: FAIL — `random_forest_superseded_leaky_row_level_cv` and `rainfall_rule` don't exist
in `evaluate_model()`'s return dict yet; the date-grouping test fails because `evaluate_model()`
doesn't expose a groupable interface yet (this third test only exercises sklearn directly against
the CSV, so it may already pass — if it does, that's fine, it's pinning behavior for Step 3, not
proving a bug).

- [ ] **Step 3: Write the implementation**

Replace `evaluate_model()` in `app/model/train.py`:

```python
from sklearn.metrics import precision_score, recall_score, roc_curve
from sklearn.model_selection import StratifiedGroupKFold, StratifiedKFold


def _group_cv_n_splits(y: np.ndarray, groups: np.ndarray) -> int:
    """How many folds a date-grouped split can safely use. Regime B has 2 rows per date
    (Navy Yard + Ben Franklin Bridge), so counting rows the way the old row-level splitter
    did would overcount - StratifiedGroupKFold needs enough DISTINCT DATES in each class,
    not enough rows."""
    n_pos_groups = len(set(groups[y == 1]))
    n_neg_groups = len(set(groups[y == 0]))
    n_splits = min(MAX_SPLITS, n_pos_groups, n_neg_groups)
    if n_splits < 2:
        raise ValueError(f"Too few positive/negative date-groups to cross-validate: {n_pos_groups} vs {n_neg_groups}")
    return n_splits


def _derive_rule_threshold(df_train: "pd.DataFrame") -> float:
    """Youden's-J threshold on precip_prev_48h_mm, fit on a fold's TRAINING rows only, so
    the rule's fold score is out-of-sample the same way the RF's is (spec Step 1)."""
    sub = df_train.dropna(subset=["precip_prev_48h_mm", TARGET])
    fpr, tpr, thresholds = roc_curve(sub[TARGET].astype(int), sub["precip_prev_48h_mm"])
    return float(thresholds[int(np.argmax(tpr - fpr))])


def evaluate_model(n_seeds: int = 10) -> dict:
    """Out-of-fold predictions for RF, the LR baseline, and the rainfall rule, from
    date-grouped folds so no date's rows split across train and test (spec Section 2b.1).
    Also reports the original row-level (leaky) number, kept for the record, not deleted.
    """
    df = pd.read_csv(MODEL_DATA_FILE)
    X = df[FEATURES].to_numpy()
    y = df[TARGET].astype(int).to_numpy()
    dates = df["date"].to_numpy()
    log_target = np.log10(df["ecoli_cfu_100ml"].clip(lower=1)).to_numpy()
    unsafe_log_threshold = np.log10(config.UNSAFE_THRESHOLD_CFU_100ML)

    # --- Honest, date-grouped CV (averaged over seeds - a single split is noisy at n=60) ---
    n_splits = _group_cv_n_splits(y, dates)
    rf_p, rf_r, lr_p, lr_r, rule_p, rule_r = [], [], [], [], [], []
    for seed in range(n_seeds):
        cv = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=seed)
        rf_oof = np.empty(len(y), dtype=int)
        lr_oof = np.empty(len(y), dtype=int)
        rule_oof = np.empty(len(y), dtype=int)
        for train_idx, test_idx in cv.split(X, y, groups=dates):
            rf = _make_rf_pipeline()
            rf.fit(X[train_idx], y[train_idx])
            rf_oof[test_idx] = rf.predict(X[test_idx])

            lr = _make_lr_pipeline()
            lr.fit(X[train_idx], log_target[train_idx])
            lr_oof[test_idx] = (lr.predict(X[test_idx]) >= unsafe_log_threshold).astype(int)

            fold_threshold = _derive_rule_threshold(df.iloc[train_idx])
            rule_oof[test_idx] = (df.iloc[test_idx]["precip_prev_48h_mm"] >= fold_threshold).astype(int)

        rf_p.append(precision_score(y, rf_oof, zero_division=0)); rf_r.append(recall_score(y, rf_oof, zero_division=0))
        lr_p.append(precision_score(y, lr_oof, zero_division=0)); lr_r.append(recall_score(y, lr_oof, zero_division=0))
        rule_p.append(precision_score(y, rule_oof, zero_division=0)); rule_r.append(recall_score(y, rule_oof, zero_division=0))

    # --- Superseded row-level CV, kept for the record (this is the ORIGINAL implementation) ---
    cv = _cv_splitter(y)
    rf_oof_leaky = np.empty(len(y), dtype=int)
    for train_idx, test_idx in cv.split(X, y):
        rf = _make_rf_pipeline()
        rf.fit(X[train_idx], y[train_idx])
        rf_oof_leaky[test_idx] = rf.predict(X[test_idx])

    metrics = {
        "n_rows": len(df),
        "n_unsafe": int(y.sum()),
        "cv_folds": n_splits,
        "random_forest": {
            "precision": round(float(np.mean(rf_p)), 3),
            "recall": round(float(np.mean(rf_r)), 3),
        },
        "random_forest_superseded_leaky_row_level_cv": {
            "precision": round(precision_score(y, rf_oof_leaky, zero_division=0), 3),
            "recall": round(recall_score(y, rf_oof_leaky, zero_division=0), 3),
        },
        "linear_regression_baseline": {
            "precision": round(float(np.mean(lr_p)), 3),
            "recall": round(float(np.mean(lr_r)), 3),
        },
        "rainfall_rule": {
            "precision": round(float(np.mean(rule_p)), 3),
            "recall": round(float(np.mean(rule_r)), 3),
        },
    }

    final_rf = _make_rf_pipeline()
    final_rf.fit(X, y)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": final_rf, "features": FEATURES}, ARTIFACTS_DIR / f"rf_{MODEL_NAME}.joblib")

    return metrics
```

Note: `_cv_splitter` (row-level `StratifiedKFold`) stays in the file unchanged — it's still used
for the superseded number. `MAX_SPLITS` and `RANDOM_STATE` are unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_model.py -v`
Expected: PASS (all tests in the file, including the 3 new ones and the existing 6)

- [ ] **Step 5: Commit**

```bash
git add app/model/train.py app/tests/test_model.py
git commit -m "fix: replace leaky row-level CV with date-grouped validation (Milestone 1b Step 1)"
```

---

### Task 2: Bring the near-shore raw data into the repo and build the full 69-row dataset

**Files:**
- Create: `AquaSentinel-dataset/raw/drbc_nearshore_ecoli_2019_2025.csv` (copied from outside the repo)
- Modify: `AquaSentinel-dataset/build_dataset.py`
- Modify: `AquaSentinel-dataset/DATA-DICTIONARY.md`
- Test: none (this is a data-pipeline/docs task; Task 3 tests the resulting file's shape)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `AquaSentinel-dataset/data/nearshore_labels.csv` (69 rows, 2019-2025), which Task 3
  reads directly by path.

**Finding surfaced while writing this plan, not in the spec:** the near-shore experiment's raw
source file lives at `/Users/gouri/Desktop/Hackathons/OneAquaHealth/drbc_nearshore_ecoli_2019_2025.csv`
— one directory *above* this git repository, referenced by
`AquaSentinel-dataset/nearshore_experiment/build_nearshore_labels.py`'s `SRC` constant via
`os.path.join(DS, "..", "..", "drbc_nearshore_ecoli_2019_2025.csv")`. It is not tracked by git.
Since this repo will be public and this file is now the foundation of the approved model pivot,
it must move inside the repo, matching where every other raw source already lives
(`raw/usgs_dv_raw.csv`, `raw/wqp_ecoli_raw.csv`, `raw/ncei_precip_raw.csv`,
`raw/usgs_iv_turbidity_raw.csv`).

- [ ] **Step 1: Copy the raw file into the repo**

```bash
cp "/Users/gouri/Desktop/Hackathons/OneAquaHealth/drbc_nearshore_ecoli_2019_2025.csv" \
   AquaSentinel-dataset/raw/drbc_nearshore_ecoli_2019_2025.csv
```

- [ ] **Step 2: Add a `build_nearshore()` function to `build_dataset.py`**

Add near the bottom of `AquaSentinel-dataset/build_dataset.py`, after `build()` and before the
`if __name__ == "__main__":` block:

```python
NEARSHORE_RAW = os.path.join(RAW, "drbc_nearshore_ecoli_2019_2025.csv")
NEARSHORE_SITES = (["DRBC-DEL-LL"] + [f"DRBC-6107-0{n}" for n in range(49, 56)]
                   + [f"DRBC-C{n}" for n in range(1, 6)])


def build_nearshore():
    """Penn's Landing near-shore labels, collapsed to one row per date (MAX across sites -
    conservative, matches load_labels()'s own same-station-same-date rule). Moved here from
    nearshore_experiment/build_nearshore_labels.py 2026-09-27 (Milestone 1b Step 2) - this is
    now a first-class output, not a one-off experiment script. All 69 label-days ship (not
    just the turbidity era): the near-shore model doesn't use turbidity as a feature (see
    docs/superpowers/specs/2026-09-27-model-honesty-fix-milestone1b-design.md Section 2b.3),
    so restricting to 2021-10-28 onward would only throw away real, usable near-shore samples.
    """
    d = pd.read_csv(NEARSHORE_RAW, parse_dates=["date"])
    d = d[d.site_id.isin(NEARSHORE_SITES)]
    lab = (d.groupby("date")
             .agg(ecoli_mpn_100ml=("ecoli_value", "max"),
                  n_samples=("ecoli_value", "size"),
                  n_sites=("site_id", "nunique"),
                  sites=("site_id", lambda s: ";".join(sorted(set(s)))),
                  censored=("detection_condition", lambda s: s.notna().any()))
             .reset_index())
    lab["station_name"] = "Penns Landing near-shore (collapsed)"
    lab["unsafe"] = (lab.ecoli_mpn_100ml >= EPA_SINGLE_SAMPLE).astype(int)
    df = lab.merge(load_proxies(), on="date", how="left").merge(load_precip(), on="date", how="left")
    df.to_csv(os.path.join(OUT, "nearshore_labels.csv"), index=False)
    print(f"\nNear-shore: {len(df)} label-days, {int(df.unsafe.sum())} unsafe; "
          f"from {d.shape[0]} raw samples")
    return df
```

Then call it from `main()`'s existing flow — add this line right after `build()` is called in
the `if __name__ == "__main__":` block:

```python
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="re-pull raw sources")
    a = ap.parse_args()
    if a.fetch:
        fetch()
    build()
    build_nearshore()
```

- [ ] **Step 3: Run the builder and verify the output**

Run (from the repo root, using the project's venv interpreter, not system Python):
```bash
cd AquaSentinel-dataset && ../venv/bin/python3 build_dataset.py
```
Expected: prints `Near-shore: 69 label-days, 30 unsafe; from 133 raw samples` (matching the
existing `nearshore_experiment/README.md`'s already-verified numbers), and creates
`AquaSentinel-dataset/data/nearshore_labels.csv`.

Verify the row count directly (from the repo root):
```bash
./venv/bin/python3 -c "import pandas as pd; df = pd.read_csv('AquaSentinel-dataset/data/nearshore_labels.csv'); print(len(df), int(df.unsafe.sum()))"
```
Expected output: `69 30`

- [ ] **Step 4: Replace the DATA-DICTIONARY.md addendum**

The existing addendum (added earlier the same day, before this plan was approved) describes the
superseded 74-row pooled approach. Replace it — find this block in
`AquaSentinel-dataset/DATA-DICTIONARY.md`:

```markdown
## Addendum 2026-09-27 — `data/regime_B_plus_nearshore.csv` (proposed Milestone 1b)

Regime B (Ben Franklin Bridge + Navy Yard, 60 rows) plus 14 Penn's Landing near-shore label-days
from the turbidity era. 74 rows / 41 distinct dates / 23 unsafe. Existing CSVs are unchanged.
Built by `nearshore_experiment/build_nearshore_labels.py` then `build_combined_regime_B.py`,
from `../../drbc_nearshore_ecoli_2019_2025.csv` (DRBC near-shore program).

- Same columns as regime B, plus `nearshore` (1 = Penn's Landing near-shore, 0 = channel station)
  and `source_sites` (DRBC site IDs behind each row).
- Near-shore rows collapse DRBC-DEL-LL, DRBC-6107-049..055 and DRBC-C1..C5 to one row per date
  (MAX value). The 2024 rows take the max over up to 7 sites, which inflates "unsafe".
- Near-shore values are MPN/100mL (`unit` says so) but stored in `ecoli_cfu_100ml`; treated as
  equivalent at the 235 threshold.
- **Split any cross-validation by `date`.** BFB and Navy Yard share all 30 dates and identical gauge
  features, so row-level folds leak (see `nearshore_experiment/README.md`).
- 6 regime B rows have a blank `unit` (inherited from the source).
```

Replace it with:

```markdown
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
```

- [ ] **Step 5: Commit**

```bash
git add AquaSentinel-dataset/raw/drbc_nearshore_ecoli_2019_2025.csv \
        AquaSentinel-dataset/build_dataset.py AquaSentinel-dataset/DATA-DICTIONARY.md \
        AquaSentinel-dataset/data/nearshore_labels.csv
git commit -m "feat: bring near-shore raw data into the repo, build the full 69-row dataset (Milestone 1b Step 2, data prep)"
```

---

### Task 3: Retrain the confidence-signal model on near-shore labels, derive the low-confidence cutoff

**Files:**
- Modify: `app/model/train.py`
- Test: `app/tests/test_model.py`

**Interfaces:**
- Consumes: `AquaSentinel-dataset/data/nearshore_labels.csv` (Task 2); `_group_cv_n_splits(y, groups) -> int` (Task 1, same file — computes fold count from distinct date-groups, not raw rows).
- Produces (used by Task 5): `app/model/artifacts/rf_nearshore.joblib` (a `{"model": ..., "features": NEARSHORE_FEATURES}` bundle, same shape as the existing `rf_B_post2021.joblib`); `config.LOW_CONFIDENCE_CUTOFF` gets a real derived value (currently `None`).

- [ ] **Step 1: Write the failing tests**

Add to `app/tests/test_model.py`:

```python
def test_nearshore_model_trains_on_all_69_rows_without_turbidity():
    metrics = train.evaluate_nearshore_model()

    assert metrics["n_rows"] == 69
    assert metrics["n_unsafe"] == 30
    assert "turbidity_fnu_mean" not in train.NEARSHORE_FEATURES
    assert "turbidity_fnu_max" not in train.NEARSHORE_FEATURES
    assert len(train.NEARSHORE_FEATURES) == 6


def test_nearshore_model_honest_metrics_are_reported():
    metrics = train.evaluate_nearshore_model()

    assert "precision" in metrics["random_forest"]
    assert "recall" in metrics["random_forest"]
    assert 0.0 <= metrics["random_forest"]["precision"] <= 1.0
    assert 0.0 <= metrics["random_forest"]["recall"] <= 1.0


def test_nearshore_model_artifact_is_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(train, "ARTIFACTS_DIR", tmp_path)
    train.evaluate_nearshore_model()

    bundle = joblib.load(tmp_path / "rf_nearshore.joblib")
    assert bundle["features"] == train.NEARSHORE_FEATURES
    assert hasattr(bundle["model"], "predict_proba")


def test_low_confidence_cutoff_is_derived_from_data_not_invented():
    cutoff = train.derive_low_confidence_cutoff()

    # Confidence can range over the full [0,1]: unlike the old model-decides formula (where
    # the same probability picked both the tier and the confidence, guaranteeing >= 0.5),
    # the rule and the model are now decoupled - a model that strongly disagrees with the
    # rule's call must be able to report LOW confidence, below 0.5 (Task 5).
    assert 0.0 <= cutoff <= 1.0


def test_low_confidence_cutoff_is_not_yet_consumed_anywhere_else():
    """Review Focus: config.LOW_CONFIDENCE_CUTOFF moves from None to a real number in this
    task, but nothing should start reading it - that happens when the Sampling Coordinator
    (Milestone 8) is actually built. This is an intentional, current-state pin: when
    Milestone 8 wires it in, this test is the first thing to update, not a trap to work
    around."""
    import pathlib

    app_dir = pathlib.Path(train.__file__).resolve().parents[1]
    hits = []
    for py_file in app_dir.rglob("*.py"):
        if py_file.name in ("config.py",) or "tests" in py_file.parts:
            continue
        if "LOW_CONFIDENCE_CUTOFF" in py_file.read_text():
            hits.append(str(py_file))

    assert hits == []
```

Note: `joblib` is already imported at the top of `test_model.py`'s module under test
(`app/model/train.py` imports it) — add `import joblib` to `app/tests/test_model.py`'s own
imports if it isn't already there.

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_model.py -k "nearshore or low_confidence" -v`
Expected: FAIL with `AttributeError: module 'app.model.train' has no attribute 'evaluate_nearshore_model'` (and similarly for `NEARSHORE_FEATURES`, `derive_low_confidence_cutoff`)

- [ ] **Step 3: Write the implementation**

Add to `app/model/train.py` (after the existing `evaluate_model()` function):

```python
NEARSHORE_DATA_FILE = DATASET_DIR / "nearshore_labels.csv"
NEARSHORE_MODEL_NAME = "nearshore"
NEARSHORE_TARGET = "unsafe"
NEARSHORE_FEATURES = [
    "water_temp_c",
    "sp_conductance_uscm",
    "dissolved_oxygen_mgl",
    "ph",
    "precip_mm",
    "precip_prev_24h_mm",
]
# Turbidity is deliberately excluded - see docs/superpowers/specs/
# 2026-09-27-model-honesty-fix-milestone1b-design.md Section 2b.3: no significant relationship
# with the outcome anywhere it was tested (Mann-Whitney p=0.885 channel, p=0.181 near-shore;
# ROC AUC 0.487 and 0.271 respectively - both at or below chance). Removing it from the honest
# RF changed nothing beyond noise in every dataset it was checked against. It is still fetched
# and shown as dashboard evidence (app/scoring/pull_reading.py) - just not a model input.


def evaluate_nearshore_model(n_seeds: int = 10) -> dict:
    """Honest (date-grouped) out-of-fold metrics for the near-shore-trained confidence-signal
    model, then a final fit on all 69 rows, saved as the live model artifact.

    Already one row per date (spec Section 2b's near-shore labels are collapsed to a single
    row per date before this function ever sees them), so StratifiedKFold and
    StratifiedGroupKFold produce identical splits here - StratifiedGroupKFold is used anyway,
    for the same defense-in-depth reason DATA-DICTIONARY.md's addendum gives.
    """
    df = pd.read_csv(NEARSHORE_DATA_FILE)
    X = df[NEARSHORE_FEATURES].to_numpy()
    y = df[NEARSHORE_TARGET].astype(int).to_numpy()
    dates = df["date"].to_numpy()

    n_splits = _group_cv_n_splits(y, dates)
    precisions, recalls = [], []
    for seed in range(n_seeds):
        cv = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=seed)
        oof = np.empty(len(y), dtype=int)
        for train_idx, test_idx in cv.split(X, y, groups=dates):
            rf = _make_rf_pipeline()
            rf.fit(X[train_idx], y[train_idx])
            oof[test_idx] = rf.predict(X[test_idx])
        precisions.append(precision_score(y, oof, zero_division=0))
        recalls.append(recall_score(y, oof, zero_division=0))

    metrics = {
        "n_rows": len(df),
        "n_unsafe": int(y.sum()),
        "cv_folds": n_splits,
        "random_forest": {
            "precision": round(float(np.mean(precisions)), 3),
            "recall": round(float(np.mean(recalls)), 3),
        },
    }

    final_rf = _make_rf_pipeline()
    final_rf.fit(X, y)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {"model": final_rf, "features": NEARSHORE_FEATURES},
        ARTIFACTS_DIR / f"rf_{NEARSHORE_MODEL_NAME}.joblib",
    )

    return metrics


def derive_low_confidence_cutoff(n_seeds: int = 10) -> float:
    """Youden's-J threshold on "confidence" (spec Step 4's formula: probability_unsafe if the
    rule says Unsafe, else 1 - probability_unsafe) that best separates near-shore days where
    the rainfall rule's decision matched the true label from days where it didn't - same
    ROC/Youden's-J pattern as derive_rain_fallback_threshold(), applied to a different signal.

    The rule itself needs no cross-validation (it's a fixed threshold, not fit per fold); the
    model's probability must come from out-of-fold predictions so it isn't cheating by having
    seen that row during training.
    """
    df = pd.read_csv(NEARSHORE_DATA_FILE)
    X = df[NEARSHORE_FEATURES].to_numpy()
    y = df[NEARSHORE_TARGET].astype(int).to_numpy()
    dates = df["date"].to_numpy()
    rain = df["precip_prev_48h_mm"].to_numpy()

    n_splits = _group_cv_n_splits(y, dates)
    confidences_by_seed = []
    for seed in range(n_seeds):
        cv = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=seed)
        oof_proba = np.empty(len(y), dtype=float)
        for train_idx, test_idx in cv.split(X, y, groups=dates):
            rf = _make_rf_pipeline()
            rf.fit(X[train_idx], y[train_idx])
            oof_proba[test_idx] = rf.predict_proba(X[test_idx])[:, 1]

        rule_threshold = config.RAIN_FALLBACK_THRESHOLD_MM
        rule_unsafe = rain >= rule_threshold
        confidence = np.where(rule_unsafe, oof_proba, 1.0 - oof_proba)
        rule_correct = rule_unsafe.astype(int) == y
        confidences_by_seed.append((confidence, rule_correct))

    all_confidence = np.concatenate([c for c, _ in confidences_by_seed])
    all_correct = np.concatenate([c for _, c in confidences_by_seed])

    fpr, tpr, thresholds = roc_curve(all_correct, all_confidence)
    best_idx = int(np.argmax(tpr - fpr))
    return float(thresholds[best_idx])
```

Also add `import joblib` to the top of `app/tests/test_model.py` if not already present.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_model.py -v`
Expected: PASS (all tests, including the 5 new ones)

- [ ] **Step 5: Run the training script and copy the derived cutoff into config.py**

Run: `./venv/bin/python -m app.model.train`

This still only runs the existing `main()` (channel model + rules fallback) — it does not yet
call `evaluate_nearshore_model()` or `derive_low_confidence_cutoff()`. Run them directly instead:

```bash
./venv/bin/python -c "
from app.model import train
print('nearshore:', train.evaluate_nearshore_model())
print('low_confidence_cutoff:', train.derive_low_confidence_cutoff())
"
```

Copy the printed `low_confidence_cutoff` value into `app/config.py`, replacing:

```python
LOW_CONFIDENCE_CUTOFF = None  # TODO(decide): derive from the trained model's validation results.
```

with (using the actual printed value in place of `<value>`):

```python
# Derived 2026-09-27 by app/model/train.py:derive_low_confidence_cutoff() - the confidence
# value (see app/scoring/pull_reading.py's formula) below which the rainfall rule's decision
# and the near-shore model most often disagreed, out-of-fold, on the 69-row near-shore set.
LOW_CONFIDENCE_CUTOFF = <value>
```

- [ ] **Step 6: Commit**

```bash
git add app/model/train.py app/tests/test_model.py app/config.py app/model/artifacts/rf_nearshore.joblib
git commit -m "feat: retrain confidence-signal model on near-shore labels, derive low-confidence cutoff (Milestone 1b Step 2)"
```

---

### Task 4: Live 48-hour rainfall from both NWS and Open-Meteo

**Files:**
- Modify: `app/ingestion/nws.py`
- Modify: `app/ingestion/open_meteo.py`
- Test: `app/tests/test_nws_ingestion.py`
- Test: `app/tests/test_open_meteo_ingestion.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces (used by Task 5): `fetch_antecedent_rainfall()` in both modules now returns
  `{"precip_mm": float, "precip_prev_24h_mm": float, "precip_prev_48h_mm": float}` (a new key,
  `precip_prev_48h_mm`, added to both). Raises `RainfallUnavailable` (already the shared
  exception type both modules use) if the 48h window can't be fully computed.

**Verified live during plan-writing (not assumed):** the NWS observations endpoint accepts
`start`/`end` query parameters and returns real historical data outside the `limit=500` request's
~38-40h reach — confirmed with a direct request for the 76h-to-36h-ago window, which returned 416
real observations spanning that exact range.

- [ ] **Step 1: Write the failing tests**

Replace the top of `app/tests/test_nws_ingestion.py` (add a shared fixture helper and update
`_client_for` to serve two different responses depending on the request):

```python
"""Tests for app/ingestion/nws.py's rainfall windowing.

The two things most likely to be wrong here, per BUILD-SPEC.md's own disclosed
limitations: (1) summing every 5-minute observation instead of 3h-spaced marks would
count the same rain many times over, and (2) a `null` precipitationLast3Hours must only
be treated as 0mm when the observation's own weather description confirms no rain.

precip_prev_48h_mm (Milestone 1b, 2026-09-27) needs data further back than a single
`limit=500` request reaches, so fetch_antecedent_rainfall() now makes two requests: the
existing recent one, and a second `start`/`end`-bounded one for the older window. Tests
that need 48h coverage use _full_observations() to synthesize both windows; tests that
only ever cared about the 24h path keep using the older, shorter fixture unchanged.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.ingestion.nws import RainfallUnavailable, fetch_antecedent_rainfall

NOW = datetime(2026, 9, 25, 21, 0, 0, tzinfo=timezone.utc)  # 5pm US/Eastern (EDT, UTC-4)


def _observation(
    timestamp: datetime,
    precip_mm: float | None,
    description: str = "Partly Cloudy",
    present_weather: list | None = None,
) -> dict:
    return {
        "properties": {
            "timestamp": timestamp.isoformat(),
            "textDescription": description,
            "presentWeather": present_weather or [],
            "precipitationLast3Hours": {"unitCode": "wmoUnit:mm", "value": precip_mm},
        }
    }


def _full_observations(hours: int = 76, precip_mm: float = 0.5) -> list[dict]:
    """Enough 5-minute observations to cover every 3h mark back to `hours` before NOW -
    enough for precip_mm, precip_prev_24h_mm, AND precip_prev_48h_mm to all resolve."""
    return [
        _observation(NOW - timedelta(minutes=5 * i), precip_mm=precip_mm)
        for i in range(0, hours * 12)
    ]


def _client_for(recent: list[dict], older: list[dict] | None = None) -> httpx.Client:
    """Simulates NWS's two-request shape: a plain `limit=500` request (recent) and a
    `start`/`end`-bounded request (older). Defaults `older` to `recent` so tests that don't
    care about the distinction can pass one list, as before."""
    older = recent if older is None else older

    def handler(request: httpx.Request) -> httpx.Response:
        if "start" in request.url.params:
            return httpx.Response(200, json={"features": older})
        return httpx.Response(200, json={"features": recent})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_does_not_double_count_overlapping_5_minute_observations():
    """Every observation reports 1.0mm for its OWN rolling 3h window. If the code just
    summed every 5-minute record in range, 24h would give ~288mm (288 records). The
    correct mark-based total is 8mm (8 non-overlapping 3h marks x 1.0mm)."""
    observations = _full_observations(hours=76, precip_mm=1.0)

    result = fetch_antecedent_rainfall(client=_client_for(observations), now=NOW)

    assert result["precip_prev_24h_mm"] == 8.0


def test_precip_prev_48h_mm_sums_the_two_prior_calendar_days():
    """Matches training's window (build_dataset.py:load_precip()): the two FULL local
    calendar days before today, not a rolling 48h-from-now window. 8 marks/day x 2 days x
    1.0mm/mark = 16.0mm, regardless of how much (if any) rain fell today."""
    observations = _full_observations(hours=76, precip_mm=1.0)

    result = fetch_antecedent_rainfall(client=_client_for(observations), now=NOW)

    assert result["precip_prev_48h_mm"] == 16.0


def test_fails_closed_when_the_older_window_is_incomplete():
    """The `start`/`end` request comes back too short to cover both prior calendar days -
    must fail closed, never a partial 48h figure."""
    recent = _full_observations(hours=40, precip_mm=1.0)
    older = _full_observations(hours=5, precip_mm=1.0)  # nowhere near enough

    with pytest.raises(RainfallUnavailable):
        fetch_antecedent_rainfall(client=_client_for(recent, older=older), now=NOW)


def test_works_with_only_a_single_nws_requests_worth_of_history_for_24h():
    """A real NWS request (limit=500, ~5min spacing) covers ~38-40h, not the full 45h
    that a 48h window would need. Confirms 24h (which only needs data back to 21h ago)
    resolves fine even when the OLDER request comes back empty - the two are independent."""
    recent = _full_observations(hours=38, precip_mm=0.5)

    result = fetch_antecedent_rainfall(client=_client_for(recent, older=[]), now=NOW)

    assert result["precip_prev_24h_mm"] == 4.0


def test_null_precip_treated_as_zero_when_weather_confirms_no_rain():
    observations = [
        _observation(NOW - timedelta(minutes=5 * i), precip_mm=None, description="Clear")
        for i in range(0, 76 * 12)
    ]

    result = fetch_antecedent_rainfall(client=_client_for(observations), now=NOW)

    assert result["precip_prev_24h_mm"] == 0.0
    assert result["precip_prev_48h_mm"] == 0.0


def test_fails_closed_when_null_precip_cannot_be_confirmed_dry():
    # presentWeather is non-empty (some phenomenon reported) but no precip value given -
    # per the disclosed rule, this must NOT be assumed to be 0.
    observations = [
        _observation(NOW, precip_mm=None, present_weather=["some_phenomenon"]),
    ]

    with pytest.raises(RainfallUnavailable):
        fetch_antecedent_rainfall(client=_client_for(observations), now=NOW)


def test_fails_closed_when_no_observation_near_a_required_mark():
    # Only one observation, nowhere near most of the 24h marks.
    observations = [_observation(NOW, precip_mm=0.0)]

    with pytest.raises(RainfallUnavailable):
        fetch_antecedent_rainfall(client=_client_for(observations), now=NOW)


def test_fails_closed_on_empty_response():
    with pytest.raises(RainfallUnavailable):
        fetch_antecedent_rainfall(client=_client_for([]), now=NOW)


def test_fails_closed_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RainfallUnavailable):
        fetch_antecedent_rainfall(client=client, now=NOW)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_nws_ingestion.py -v`
Expected: FAIL — `precip_prev_48h_mm` doesn't exist in the returned dict yet; the two-request
distinction (`older=`) isn't implemented yet so `test_fails_closed_when_the_older_window_is_incomplete`
fails because there's only ever one request made.

- [ ] **Step 3: Write the implementation**

Replace `fetch_antecedent_rainfall()` in `app/ingestion/nws.py` and add one new constant:

```python
OLDER_WINDOW_START = timedelta(hours=76)  # covers the worst case: local midnight up to 24h
OLDER_WINDOW_END = timedelta(hours=36)    # before "now", plus 45h of marks back from there,
                                           # with a margin overlapping the recent request's
                                           # ~38-40h reach so no mark falls in a gap.


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
```

`_parse_observations`, `_local_midnight_utc`, `_sum_over_marks`, and `_rain_at_mark` are unchanged
— this reuses them exactly as they already work; `_sum_over_marks` already raises
`RainfallUnavailable` per-mark, so both `test_fails_closed_when_the_older_window_is_incomplete`
and the mark-tolerance behavior fall out of the existing helper for free.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_nws_ingestion.py -v`
Expected: PASS (all 9 tests)

- [ ] **Step 5: Do the same for Open-Meteo**

Open-Meteo is a weather *model*, not a physical gauge with a request-size limit, so it needs no
two-request split — just a longer `past_hours` window and the same calendar-day summation logic,
adapted to its hourly (not 3h-marked) granularity.

Add to `app/tests/test_open_meteo_ingestion.py`, using its existing `NOW`, `_hourly_payload()`,
and `_client_for()` helpers:

```python
def test_precip_prev_48h_mm_sums_the_two_prior_calendar_days():
    """Matches training's window (build_dataset.py:load_precip()) and NWS's own Milestone 1b
    implementation: the two FULL local (US/Eastern) calendar days before today, not a rolling
    48h-from-now window. NOW is 2026-09-25T21:00 UTC (5pm EDT); local midnight is
    2026-09-25T04:00 UTC, so "yesterday" is Sep 24 local and "the day before" is Sep 23 local."""
    times = [(NOW - timedelta(hours=h)).isoformat() for h in range(76, -1, -1)]
    values = [0.0] * len(times)
    yesterday_hour = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    day_before_hour = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    values[times.index(yesterday_hour)] = 1.0
    values[times.index(day_before_hour)] = 1.0
    payload = {"hourly": {"time": times, "precipitation": values}}

    result = fetch_antecedent_rainfall(client=_client_for(payload), now=NOW)

    assert result["precip_prev_48h_mm"] == 2.0


def test_fails_closed_when_hourly_history_does_not_reach_back_far_enough():
    payload = _hourly_payload(hours_back=30, precip_mm=0.0)  # nowhere near the 48h+ needed

    with pytest.raises(RainfallUnavailable):
        fetch_antecedent_rainfall(client=_client_for(payload), now=NOW)
```

- [ ] **Step 6: Run tests to verify they fail, then implement**

Run: `./venv/bin/python -m pytest app/tests/test_open_meteo_ingestion.py -v`
Expected: FAIL — `precip_prev_48h_mm` doesn't exist yet.

Replace `fetch_antecedent_rainfall()` in `app/ingestion/open_meteo.py`:

```python
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

    if not any(ts <= day_2_start for ts, _ in hours):
        raise RainfallUnavailable(
            "Open-Meteo's hourly history does not reach back far enough for precip_prev_48h_mm"
        )

    precip_mm = sum(value for ts, value in hours if local_midnight <= ts <= now)
    precip_prev_24h_mm = sum(value for ts, value in hours if now - ts <= timedelta(hours=24))
    precip_prev_48h_mm = sum(
        value for ts, value in hours if day_1_start <= ts < day_1_end or day_2_start <= ts < day_2_end
    )

    return {
        "precip_mm": round(precip_mm, 1),
        "precip_prev_24h_mm": round(precip_prev_24h_mm, 1),
        "precip_prev_48h_mm": round(precip_prev_48h_mm, 1),
    }
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_open_meteo_ingestion.py app/tests/test_nws_ingestion.py -v`
Expected: PASS (every test in both files)

- [ ] **Step 8: Commit**

```bash
git add app/ingestion/nws.py app/ingestion/open_meteo.py \
        app/tests/test_nws_ingestion.py app/tests/test_open_meteo_ingestion.py
git commit -m "feat: live 48h rainfall from NWS (two-request) and Open-Meteo (Milestone 1b Step 3)"
```

---

### Task 5: Wire the rule as decider, the near-shore model as confidence, into live scoring

**Files:**
- Modify: `app/scoring/pull_reading.py`
- Test: `app/tests/test_pull_reading.py`
- Test: `app/tests/test_server.py`

**Interfaces:**
- Consumes: `app.model.rules_fallback.classify_by_rainfall` (existing), `app.config.RAIN_FALLBACK_THRESHOLD_MM` / `app.config.LOW_CONFIDENCE_CUTOFF` (Task 3), `app/model/artifacts/rf_nearshore.joblib` and `train.NEARSHORE_FEATURES` (Task 3), `precip_prev_48h_mm` from both rainfall sources (Task 4).
- Produces: nothing new for later tasks — this is the pivot's central wiring point.

- [ ] **Step 1: Write the failing tests**

Replace `_fake_rainfall()` in `app/tests/test_pull_reading.py` (it needs `precip_prev_48h_mm`
now) and add new tests:

```python
def _fake_rainfall(precip_prev_48h_mm: float = 0.0) -> dict:
    return {"precip_mm": 0.0, "precip_prev_24h_mm": 2.0, "precip_prev_48h_mm": precip_prev_48h_mm}


def test_the_rainfall_rule_decides_the_tier_not_the_model(monkeypatch):
    """Core of the pivot: an obviously-Unsafe rainfall total must produce Unsafe regardless
    of what the model's own probability happens to be."""
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall(precip_prev_48h_mm=50.0))

    reading = pr.pull_reading()

    assert reading["risk_tier"] == "Unsafe"
    assert reading["evidence"]["decision_basis"] == "rainfall_rule"


def test_rainfall_at_exactly_the_threshold_is_unsafe():
    """classify_by_rainfall uses >=, not > - pin the exact boundary (Review Focus)."""
    from app import config
    from app.model.rules_fallback import classify_by_rainfall

    result = classify_by_rainfall(config.RAIN_FALLBACK_THRESHOLD_MM)

    assert result["risk_tier"] == "Unsafe"


class _StubModel:
    """A predict_proba stub with a known, fixed output - the real trained model's exact
    behavior on arbitrary fake proxy values isn't something to assert on without actually
    verifying it, so these tests control probability_unsafe directly instead."""

    def __init__(self, probability_unsafe: float):
        self._probability_unsafe = probability_unsafe

    def predict_proba(self, X):
        return [[1.0 - self._probability_unsafe, self._probability_unsafe]]


def _stub_bundle(probability_unsafe: float) -> dict:
    return {
        "model": _StubModel(probability_unsafe),
        "features": [
            "water_temp_c", "sp_conductance_uscm", "dissolved_oxygen_mgl",
            "ph", "precip_mm", "precip_prev_24h_mm",
        ],
    }


def test_confidence_is_high_when_rule_and_model_agree(monkeypatch):
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall(precip_prev_48h_mm=0.0))  # rule: Safe
    monkeypatch.setattr(pr, "_load_model", lambda: _stub_bundle(probability_unsafe=0.1))  # model agrees

    reading = pr.pull_reading()

    assert reading["risk_tier"] == "Safe"
    assert reading["confidence"] == 0.9  # 1 - 0.1: high agreement


def test_confidence_is_low_when_rule_and_model_disagree(monkeypatch):
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall(precip_prev_48h_mm=0.0))  # rule: Safe
    monkeypatch.setattr(pr, "_load_model", lambda: _stub_bundle(probability_unsafe=0.9))  # model disagrees

    reading = pr.pull_reading()

    assert reading["risk_tier"] == "Safe"  # the rule still decides the tier
    assert reading["confidence"] == 0.1  # 1 - 0.9: low - the model thought this was likely Unsafe
    assert reading["evidence"]["model_probability_unsafe"] == 0.9


def test_evidence_still_carries_turbidity_even_though_the_model_no_longer_uses_it(monkeypatch):
    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall())

    reading = pr.pull_reading()

    assert reading["evidence"]["proxies"]["turbidity_fnu"] == 5.5


def test_evidence_reports_the_rule_threshold_used(monkeypatch):
    from app import config

    monkeypatch.setattr(pr, "fetch_usgs_proxies", lambda: _fake_proxies())
    monkeypatch.setattr(pr, "fetch_nws_rainfall", lambda: _fake_rainfall())

    reading = pr.pull_reading()

    assert reading["evidence"]["rule_threshold_mm"] == config.RAIN_FALLBACK_THRESHOLD_MM
```

Also update the existing `test_uses_the_oldest_proxy_reading_as_the_reading_time` and
`test_falls_back_to_open_meteo_when_nws_rainfall_is_unavailable` tests' calls to `_fake_rainfall()`
— they call it with no arguments, which still works since `precip_prev_48h_mm` now has a default
of `0.0`, so **no change needed there**.

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest app/tests/test_pull_reading.py -v`
Expected: FAIL — `risk_tier` still comes from the old channel model's `predict_proba`, not the
rule; `evidence["decision_basis"]`, `evidence["model_probability_unsafe"]`, and
`evidence["rule_threshold_mm"]` don't exist yet.

- [ ] **Step 3: Write the implementation**

Replace `pull_reading()` and `_build_feature_vector()` in `app/scoring/pull_reading.py`, and
update the imports and `MODEL_PATH`:

```python
from app.model.rules_fallback import classify_by_rainfall

ARTIFACTS_DIR = Path(__file__).resolve().parents[1] / "model" / "artifacts"
MODEL_PATH = ARTIFACTS_DIR / "rf_nearshore.joblib"  # changed from rf_B_post2021.joblib - the
# channel-trained model is retired from live scoring entirely (spec Section 5): it showed no
# real out-of-sample skill (Milestone 1b), so it must not be resurrected even as a fallback.
```

```python
def pull_reading() -> dict:
    """Fetch live data, score it, and return the shared signal contract.

    Milestone 1b (2026-09-27): the rainfall rule decides risk_tier; the near-shore-trained
    model only informs confidence (how much it agrees with the rule), never the tier itself.
    See docs/superpowers/specs/2026-09-27-model-honesty-fix-milestone1b-design.md Section 3.
    """
    proxies = fetch_usgs_proxies()
    rainfall, rainfall_source = _fetch_rainfall()

    bundle = _load_model()
    decision = classify_by_rainfall(rainfall["precip_prev_48h_mm"])
    risk_tier = decision["risk_tier"]

    feature_values = _build_feature_vector(proxies, rainfall, bundle["features"])
    model = bundle["model"]
    probability_unsafe = float(model.predict_proba([feature_values])[0][1])
    # Confidence: how much the model agrees with the rule's decision. If the rule says
    # Unsafe, a high probability_unsafe from the model IS agreement; if the rule says Safe,
    # a LOW probability_unsafe is agreement - same shape the old model-decides confidence
    # formula used, just now measuring agreement with the rule instead of the model's own
    # certainty in its own decision.
    confidence = probability_unsafe if risk_tier == "Unsafe" else (1.0 - probability_unsafe)

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
            "decision_basis": "rainfall_rule",
            "rule_threshold_mm": config.RAIN_FALLBACK_THRESHOLD_MM,
            "model_probability_unsafe": round(probability_unsafe, 3),
        },
        "threshold_cfu_100ml": config.UNSAFE_THRESHOLD_CFU_100ML,
        "model_version": "rf_nearshore",
        "regime": "nearshore",
        "kind": "model_estimate",
    }


def _build_feature_vector(proxies: dict, rainfall: dict, features_order: list[str]) -> list[float]:
    # Turbidity is still fetched (proxies["turbidity_fnu"]) and shown in evidence.proxies -
    # it just isn't one of the near-shore model's 6 features (see app/model/train.py's
    # NEARSHORE_FEATURES comment for why it was dropped as a model input).
    values = {
        "water_temp_c": proxies["water_temp_c"].value,
        "sp_conductance_uscm": proxies["sp_conductance_uscm"].value,
        "dissolved_oxygen_mgl": proxies["dissolved_oxygen_mgl"].value,
        "ph": proxies["ph"].value,
        "precip_mm": rainfall["precip_mm"],
        "precip_prev_24h_mm": rainfall["precip_prev_24h_mm"],
    }
    return [values[name] for name in features_order]
```

Note: `_fetch_rainfall()` itself is unchanged — it already returns whatever dict either source
gives it, and both sources now include `precip_prev_48h_mm` (Task 4).

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest app/tests/test_pull_reading.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Add the missing rainfall fail-closed test at the API layer**

`app/server.py` already has `except (UsgsDataUnavailable, RainfallUnavailable): raise
HTTPException(503)`, but `app/tests/test_server.py` only ever tests the `UsgsDataUnavailable`
half of that tuple (`test_pull_reading_endpoint_fails_closed_on_usgs_error`) — nothing currently
proves a `RainfallUnavailable` (including the new 48h-specific one from Task 4) reaches the same
503 response. Add to `app/tests/test_server.py`, mirroring the existing USGS test exactly:

```python
from app.ingestion.nws import RainfallUnavailable


def test_pull_reading_endpoint_fails_closed_on_rainfall_error(monkeypatch, tmp_path):
    def raise_unavailable():
        raise RainfallUnavailable("48h rainfall window incomplete")

    monkeypatch.setattr(server, "pull_reading", raise_unavailable)
    client = _client(monkeypatch, tmp_path)

    response = client.post("/api/pull-reading")

    assert response.status_code == 503
    # Nothing should have been persisted from a failed pull.
    assert db.get_recent_readings(db_path=tmp_path / "test.db") == []
```

Run: `./venv/bin/python -m pytest app/tests/test_server.py -k fails_closed_on -v`
Expected: PASS (both the existing USGS test and the new rainfall test)

- [ ] **Step 6: Run the full test suite**

Run: `./venv/bin/python -m pytest app/tests/ -v`
Expected: PASS (every test in the project)

- [ ] **Step 7: Commit**

```bash
git add app/scoring/pull_reading.py app/tests/test_pull_reading.py app/tests/test_server.py
git commit -m "feat: rule decides risk_tier, near-shore model informs confidence (Milestone 1b Step 4)"
```

---

### Task 6: Correct the FHIR Observation's method text

**Files:**
- Modify: `app/fhir/resources.py`
- Test: `app/tests/test_fhir_resources.py`

**Interfaces:**
- Consumes: nothing new — `build_risk_observation(reading, proxy_entries)`'s existing signature and callers are unchanged; only its output text changes.
- Produces: nothing new for later tasks.

- [ ] **Step 1: Write the failing test**

Add to `app/tests/test_fhir_resources.py`, using its existing `_reading()` fixture helper and the
same `build_bundle` + find-the-Observation-with-`method` pattern its other tests already use:

```python
def test_risk_observation_method_describes_the_rule_not_random_forest():
    """Milestone 1b (2026-09-27): the rule decides the tier now, not a random forest - a
    person reading this Observation off the wire must not conclude a model is deciding."""
    flag = resources.build_flag("flag-1", "Unsafe", "active", "2026-06-01T12:00:00+00:00", None)
    bundle = resources.build_bundle(_reading(), flag)

    risk_entry = next(
        e for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Observation" and "method" in e["resource"]
    )
    method_text = risk_entry["resource"]["method"]["text"]

    assert "random forest" not in method_text.lower()
    assert "rule" in method_text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest app/tests/test_fhir_resources.py -k method_describes_the_rule -v`
Expected: FAIL — current text is `"Estimated (modeled) risk"`, which contains neither "rule" nor
excludes "random forest" by name (it doesn't currently say "random forest" either, so only the
`"rule" in method_text.lower()` assertion fails).

- [ ] **Step 3: Write the implementation**

In `app/fhir/resources.py`, change `build_risk_observation`'s `method` field:

```python
"method": {"text": "Estimated (rainfall-rule-based) risk, model-informed confidence"},
```

(replacing the existing `"method": {"text": "Estimated (modeled) risk"}`)

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest app/tests/test_fhir_resources.py -v`
Expected: PASS (every test in the file)

- [ ] **Step 5: Run the FHIR end-to-end test**

Run: `./venv/bin/python -m pytest app/tests/test_fhir_end_to_end.py app/tests/test_fhir_emit.py -v`
Expected: PASS — confirms the wording change doesn't break the delivered Bundle's shape.

- [ ] **Step 6: Commit**

```bash
git add app/fhir/resources.py app/tests/test_fhir_resources.py
git commit -m "fix: FHIR Observation method text describes the rule, not random forest (Milestone 1b Step 4)"
```

---

### Task 7: Docs — amend, don't rewrite

**Files:**
- Modify: `plan.md`
- Modify: `docs/alert-rules-decisions.md`
- Modify: `docs/landing-page/BUILD-SPEC.md`
- Modify: `docs/product-brief.md`
- Modify: `app/config.py`
- Test: none (documentation-only task)

**Interfaces:**
- Consumes: the actual derived numbers from Task 1 (`training_report.json`'s honest metrics) and Task 3 (`LOW_CONFIDENCE_CUTOFF`'s derived value, already copied into `config.py` in Task 3 Step 5).
- Produces: nothing — this is the plan's final task.

- [ ] **Step 1: Amend `plan.md`'s Milestone 1**

Find Milestone 1 in `plan.md` (starts `1. **Model trained and honestly validated. DONE
2026-09-25.**`) and append this paragraph immediately after its existing text (do not remove or
edit the original sentences):

```markdown
   **Amended 2026-09-27 (Milestone 1b).** The 0.615/0.533 figure above does not survive
   date-grouped cross-validation - Navy Yard and Ben Franklin Bridge share identical gauge/rain
   features on every date, so ordinary row-level folds leaked. Honest, date-grouped precision/
   recall for that same channel model: see `app/model/artifacts/training_report.json`'s
   `random_forest` key (the row-level number is kept, not deleted, under
   `random_forest_superseded_leaky_row_level_cv`). Live scoring no longer uses this channel
   model to decide anything - see Milestone 6 (renumbered from a since-superseded position;
   see `plan.md`'s 2026-09-27 milestone reorder history in git log) for the rule-first pivot:
   a disclosed rainfall rule now decides Safe/Unsafe, and a model retrained on Penn's Landing
   near-shore labels (69 rows, 6 features, no turbidity) only informs confidence. Full design:
   `docs/superpowers/specs/2026-09-27-model-honesty-fix-milestone1b-design.md`.
```

- [ ] **Step 2: Add decision 6 to `docs/alert-rules-decisions.md`**

Append after the existing "5. Recreation season" section and before "## Still open":

```markdown
## 6. Model decides confidence, a rainfall rule decides the tier
- Decision: `app.model.rules_fallback.classify_by_rainfall()` (prior-48h rain >=
  `RAIN_FALLBACK_THRESHOLD_MM`) decides `risk_tier` in live scoring. A random forest retrained
  on Penn's Landing near-shore labels only sets `confidence` (high when it agrees with the
  rule, low when it doesn't) - it never decides the tier itself.
- Reason: date-grouped (honest) cross-validation showed the originally-shipped channel-station
  model carried no real out-of-sample skill (F1 dropped from 0.49 to 0.11 once dates weren't
  split across train and test) - the rainfall rule consistently outperformed it. A model
  retrained specifically on near-shore labels does show real skill, but on the full 69-row
  near-shore history (not a smaller turbidity-restricted subset) the rule still edges it out.
  Full analysis: `docs/superpowers/specs/2026-09-27-model-honesty-fix-milestone1b-design.md`.
- Basis: independently re-derived twice (once during the original leak review, once fresh
  during spec approval) with matching results both times.
```

Also remove the now-resolved item from "## Still open" — replace:

```markdown
- Low-confidence cutoff: set from the trained model's validation results.
```

with:

```markdown
- ~~Low-confidence cutoff: set from the trained model's validation results.~~ Resolved
  2026-09-27 - see decision 6 and `app/config.py:LOW_CONFIDENCE_CUTOFF`.
```

- [ ] **Step 3: Update `docs/landing-page/BUILD-SPEC.md`**

Find the paragraph starting `**Decided 2026-09-25: the shipped model uses 9 features, not
11.**` (around line 84) and append immediately after it:

```markdown
- **Amended 2026-09-27 (Milestone 1b):** that model's reported 0.615/0.533 did not survive
  honest, date-grouped cross-validation (Navy Yard and Ben Franklin Bridge share identical
  gauge/rain features per date - ordinary folds leaked). Live scoring no longer uses this model
  to decide `risk_tier`: a disclosed rainfall rule (prior-48h rain >= 2.5mm) decides it, and a
  model retrained on Penn's Landing near-shore labels (6 features, turbidity excluded - it
  showed no significant relationship with the outcome anywhere it was tested) only sets
  `confidence`. See `docs/superpowers/specs/2026-09-27-model-honesty-fix-milestone1b-design.md`.
```

- [ ] **Step 4: Update `docs/product-brief.md`'s "Model choice" paragraph**

Find the paragraph starting `**Model choice, honestly scoped.**` and append immediately after
it (as a new paragraph, not editing the original sentence):

```markdown
**Amended 2026-09-27.** A post-hoc review found that the shipped random-forest's reported
precision/recall did not survive honest, date-grouped cross-validation - two label stations
sharing one upstream gauge meant ordinary folds leaked identical feature rows across train and
test. Finding and fixing that leak is part of this project's data-quality story, not a footnote:
live scoring now uses a disclosed, one-line rainfall rule to decide Safe/Unsafe, and a model
retrained on Penn's Landing near-shore labels only informs how much to trust that decision.
```

- [ ] **Step 5: Update `app/config.py`'s comment on the rain threshold**

Find the comment block above `RAIN_FALLBACK_THRESHOLD_MM` and append one line noting it's
in-sample:

```python
# Derived 2026-09-25 from aquasentinel_labels_master.csv (330 rows, 49 unsafe): precision 0.372,
# recall 0.653 in-sample. Re-derive if the dataset is rebuilt with `build_dataset.py --fetch`.
# Note (2026-09-27, Milestone 1b): this threshold is chosen on the same data it is evaluated
# against - one parameter, low overfit risk, but still in-sample. Disclosed, not hidden.
RAIN_FALLBACK_THRESHOLD_MM = 2.5
```

- [ ] **Step 6: Commit**

```bash
git add plan.md docs/alert-rules-decisions.md docs/landing-page/BUILD-SPEC.md \
        docs/product-brief.md app/config.py
git commit -m "docs: amend Milestone 1's numbers and record the rule-first decision (Milestone 1b Step 6)"
```

---

## Final verification

After all 7 tasks: run `./venv/bin/python -m pytest app/tests/ -v` and confirm every test in the
project passes, then manually pull one live reading (`./venv/bin/uvicorn app.server:app --reload`,
`POST /api/pull-reading`) and confirm `evidence.decision_basis == "rainfall_rule"` and
`evidence.model_probability_unsafe` is present.
