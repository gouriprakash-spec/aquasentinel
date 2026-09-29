"""Tests for the E. coli risk model and the rainfall rules fallback.

Definition of Done (CLAUDE.md) requires the alert rules and a cross-surface consistency
test; this file covers the model-training half: real metrics reported (not R-squared),
plus the rules fallback's fail-closed behavior.

Only the post-2021 (turbidity) model ships - see app/model/train.py's module docstring for
why the pre-2021 model was evaluated and dropped (30% recall, beaten by the rules fallback).
"""

from __future__ import annotations

import joblib
import pytest

from app import config
from app.model import train
from app.model.rules_fallback import RainFallbackNotConfigured, classify_by_rainfall


def test_model_trains_on_the_post_2021_turbidity_data():
    metrics = train.evaluate_model()

    assert metrics["n_rows"] == 60
    assert metrics["n_unsafe"] == 15


def test_features_exclude_rainfall_windows_live_scoring_cannot_supply():
    """precip_prev_48h_mm, precip_prev_72h_mm and precip_prev_7d_mm were dropped on
    2026-09-25: a single NWS request only reliably covers ~38-40h of history, which
    isn't enough to reach the 45h-back mark a 48h window needs (24h, needing only 21h
    back, is fine). An ablation showed dropping all three costs nothing. If someone adds
    any of them back, this should fail and point them at that decision.
    """
    assert "precip_prev_48h_mm" not in train.FEATURES
    assert "precip_prev_72h_mm" not in train.FEATURES
    assert "precip_prev_7d_mm" not in train.FEATURES
    assert len(train.FEATURES) == 8


def test_metrics_report_precision_and_recall_not_r_squared():
    metrics = train.evaluate_model()

    for model_key in ("random_forest", "linear_regression_baseline"):
        assert "precision" in metrics[model_key]
        assert "recall" in metrics[model_key]
        assert 0.0 <= metrics[model_key]["precision"] <= 1.0
        assert 0.0 <= metrics[model_key]["recall"] <= 1.0


def test_rain_fallback_threshold_is_derived_from_data_not_invented():
    fallback = train.derive_rain_fallback_threshold()

    assert fallback["threshold_mm"] > 0
    assert fallback["n_rows"] > 0
    assert 0.0 <= fallback["precision"] <= 1.0
    assert 0.0 <= fallback["recall"] <= 1.0


def test_rules_fallback_fails_closed_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "RAIN_FALLBACK_THRESHOLD_MM", None)

    with pytest.raises(RainFallbackNotConfigured):
        classify_by_rainfall(precip_prev_48h_mm=20.0)


def test_rules_fallback_classifies_once_configured(monkeypatch):
    monkeypatch.setattr(config, "RAIN_FALLBACK_THRESHOLD_MM", 10.0)

    unsafe = classify_by_rainfall(precip_prev_48h_mm=15.0)
    safe = classify_by_rainfall(precip_prev_48h_mm=2.0)

    assert unsafe["risk_tier"] == "Unsafe"
    assert safe["risk_tier"] == "Safe"


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
