"""Train and validate the E. coli risk model.

Two levels only, against the EPA single-sample threshold (app.config.UNSAFE_THRESHOLD_CFU_100ML).

Ships ONE model, trained on the post-2021 data (turbidity present). Live scoring always runs
"today", and continuous turbidity has streamed from USGS 01467200 since 2021-10-28, so every
live reading going forward has turbidity available - the pre-2021 regime never applies to a
live reading. We also evaluated a pre-2021-only model (no turbidity) and dropped it: at
precision 0.346 / recall 0.30 it did not beat the rainfall-only rules fallback in this same file
(precision 0.372 / recall 0.653) while being an opaque ML model instead of a one-line rule. See
docs/product-brief.md and AquaSentinel-dataset/DATA-DICTIONARY.md for why the dataset itself
still keeps the two regimes separate (the 2021 instrument change is a silent-bias trap if
naively concatenated) - that's a data-honesty concern, not a reason to ship a second, weaker
model.

Headline model: random forest classifier on the binary `unsafe` target.
Baseline: linear regression on log10(ecoli_cfu_100ml), thresholded at log10(235) - the classic
beach-advisory approach (~70% of models in the literature review cited in
docs/product-brief.md use plain regression like this).

Both are evaluated with out-of-fold predictions from stratified k-fold CV: only 15 positive
samples is too few to trust a single train/test split, per the data dictionary's own guidance
("stratified cross-validation").

Run: python -m app.model.train
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression
from sklearn.metrics import precision_score, recall_score, roc_curve
from sklearn.model_selection import StratifiedGroupKFold, StratifiedKFold
from sklearn.pipeline import Pipeline

from app import config

DATASET_DIR = Path(__file__).resolve().parents[2] / "AquaSentinel-dataset" / "data"
ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"

TARGET = "unsafe"
MODEL_NAME = "B_post2021"
MODEL_DATA_FILE = DATASET_DIR / "regime_B_post2021.csv"

FEATURES = [
    "water_temp_c",
    "sp_conductance_uscm",
    "dissolved_oxygen_mgl",
    "ph",
    "precip_mm",
    "precip_prev_24h_mm",
    "turbidity_fnu_mean",
    "turbidity_fnu_max",
]
# precip_prev_48h_mm, precip_prev_72h_mm and precip_prev_7d_mm were DROPPED 2026-09-25,
# not just left unused: live scoring (app/ingestion/nws.py) gets rainfall from NWS station
# observations, and a single request only reliably covers ~38-40h of history (its `limit`
# caps at 500 records x ~5min). 24h rain needs data back to 21h ago - safely inside that
# window. But 48h rain needs data back to 45h ago, which is NOT reliably available from one
# request (this was caught by a test fixture bug that turned out to be a real production
# bug, not a test bug - see app/tests/test_nws_ingestion.py's git history). Feeding a
# permanently-NaN column and letting SimpleImputer fill the training median would make it a
# fixed constant on every live reading forever - mathematically inert, not "included" in
# any real sense, just disguised as such. An ablation confirmed dropping 48h/72h/7d
# together costs nothing: identical precision (0.615) and recall (0.533) with vs. without,
# on identical CV folds. So the shipped model matches exactly what a live reading can supply.

RANDOM_STATE = 42
MAX_SPLITS = 5


def _make_rf_pipeline() -> Pipeline:
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("clf", RandomForestClassifier(
            n_estimators=300, class_weight="balanced", random_state=RANDOM_STATE
        )),
    ])


def _make_lr_pipeline() -> Pipeline:
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("reg", LinearRegression()),
    ])


def _cv_splitter(y: np.ndarray) -> StratifiedKFold:
    n_splits = min(MAX_SPLITS, int(y.sum()), int((1 - y).sum()))
    if n_splits < 2:
        raise ValueError(f"Too few positives/negatives to cross-validate: {y.sum()} positive of {len(y)}")
    return StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=RANDOM_STATE)


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


def derive_rain_fallback_threshold() -> dict:
    """Youden's-J-optimal precip_prev_48h_mm threshold, from the full labeled master
    (including proxy-less rows), for use when no USGS gauge reading is available.

    This is a single scalar rule, not a fitted model, and it is derived (not invented) per
    docs/product-brief.md's insistence on values coming from our own data.
    """
    df = pd.read_csv(DATASET_DIR / "aquasentinel_labels_master.csv")
    df = df.dropna(subset=["precip_prev_48h_mm", TARGET])
    y = df[TARGET].astype(int).to_numpy()
    rain = df["precip_prev_48h_mm"].to_numpy()

    fpr, tpr, thresholds = roc_curve(y, rain)
    best_idx = int(np.argmax(tpr - fpr))
    best_threshold = float(thresholds[best_idx])

    pred = (rain >= best_threshold).astype(int)
    return {
        "threshold_mm": round(best_threshold, 1),
        "n_rows": len(df),
        "n_unsafe": int(y.sum()),
        "precision": round(precision_score(y, pred, zero_division=0), 3),
        "recall": round(recall_score(y, pred, zero_division=0), 3),
    }


def main() -> None:
    report: dict = {}

    print(f"--- Model ({MODEL_NAME}) ---")
    metrics = evaluate_model()
    report["model"] = metrics
    print(json.dumps(metrics, indent=2))

    print("--- Rules fallback (rainfall-only, from full master) ---")
    fallback = derive_rain_fallback_threshold()
    report["rules_fallback"] = fallback
    print(json.dumps(fallback, indent=2))
    print(
        f"\nNOTE: RAIN_FALLBACK_THRESHOLD_MM derived as {fallback['threshold_mm']} mm. "
        "This value still needs to be copied into app/config.py by hand (not written "
        "automatically) so the change is visible in a diff."
    )

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = ARTIFACTS_DIR / "training_report.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"\nFull report written to {report_path}")


if __name__ == "__main__":
    main()
