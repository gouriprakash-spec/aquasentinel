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
from sklearn.model_selection import StratifiedKFold
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


def evaluate_model() -> dict:
    """Out-of-fold predictions for RF and the LR baseline, from the SAME folds, so the
    two models are compared on identical splits.
    """
    df = pd.read_csv(MODEL_DATA_FILE)
    X = df[FEATURES].to_numpy()
    y = df[TARGET].astype(int).to_numpy()
    log_target = np.log10(df["ecoli_cfu_100ml"].clip(lower=1)).to_numpy()
    unsafe_log_threshold = np.log10(config.UNSAFE_THRESHOLD_CFU_100ML)

    cv = _cv_splitter(y)
    rf_oof_pred = np.empty(len(y), dtype=int)
    lr_oof_pred = np.empty(len(y), dtype=int)

    for train_idx, test_idx in cv.split(X, y):
        rf = _make_rf_pipeline()
        rf.fit(X[train_idx], y[train_idx])
        rf_oof_pred[test_idx] = rf.predict(X[test_idx])

        lr = _make_lr_pipeline()
        lr.fit(X[train_idx], log_target[train_idx])
        log_pred = lr.predict(X[test_idx])
        lr_oof_pred[test_idx] = (log_pred >= unsafe_log_threshold).astype(int)

    metrics = {
        "n_rows": len(df),
        "n_unsafe": int(y.sum()),
        "cv_folds": cv.n_splits,
        "random_forest": {
            "precision": round(precision_score(y, rf_oof_pred, zero_division=0), 3),
            "recall": round(recall_score(y, rf_oof_pred, zero_division=0), 3),
        },
        "linear_regression_baseline": {
            "precision": round(precision_score(y, lr_oof_pred, zero_division=0), 3),
            "recall": round(recall_score(y, lr_oof_pred, zero_division=0), 3),
        },
    }

    # Fit the final RF on every row and save it for the scoring pipeline.
    final_rf = _make_rf_pipeline()
    final_rf.fit(X, y)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": final_rf, "features": FEATURES}, ARTIFACTS_DIR / f"rf_{MODEL_NAME}.joblib")

    return metrics


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
