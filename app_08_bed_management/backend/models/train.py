"""
Bed Management Model Training
===============================
Loads parquet datasets, trains DischargeClassifier, LOSRegressor, and
CapacityForecaster, evaluates on validation/test sets, and saves via
ModelRegistry.

Usage::

    python -m app_08_bed_management.backend.models.train
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
    roc_auc_score,
)

from shared.ml.training_base import setup_training_env, get_dirs
logger = setup_training_env("bed_management.train")
DATASET_DIR, MODEL_DIR = get_dirs("bed_management")

from shared.ml.registry import ModelRegistry
from app_08_bed_management.backend.models.discharge_model import (
    CapacityForecaster,
    DischargeClassifier,
    LOSRegressor,
)

TARGET_CLASSIFICATION = "discharge_within_24h"
TARGET_REGRESSION = "remaining_los_hours"
NON_FEATURE_COLS = {
    "hadm_id", "discharge_within_24h", "discharge_within_48h", "remaining_los_hours",
}


# ===================================================================
# Helpers
# ===================================================================
def load_datasets() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train = pd.read_parquet(DATASET_DIR / "train.parquet")
    val = pd.read_parquet(DATASET_DIR / "val.parquet")
    test = pd.read_parquet(DATASET_DIR / "test.parquet")
    logger.info("Loaded: train=%d, val=%d, test=%d", len(train), len(val), len(test))
    return train, val, test


def prepare_xy(df: pd.DataFrame, target: str):
    """Separate features and target."""
    feature_cols = [c for c in df.columns if c not in NON_FEATURE_COLS]
    # Drop any object columns
    X = df[feature_cols].copy()
    obj_cols = X.select_dtypes(include=["object", "category"]).columns.tolist()
    if obj_cols:
        X = X.drop(columns=obj_cols)
    y = df[target].values
    return X, y


def evaluate_classifier(y_true, y_pred, y_prob, label="") -> Dict[str, Any]:
    acc = accuracy_score(y_true, y_pred)
    f1 = f1_score(y_true, y_pred, zero_division=0)
    try:
        auroc = roc_auc_score(y_true, y_prob)
    except ValueError:
        auroc = None
    metrics = {
        "accuracy": round(acc, 4),
        "f1": round(f1, 4),
        "auroc": round(auroc, 4) if auroc is not None else None,
        "positive_rate": round(float(y_true.mean()), 4),
    }
    logger.info("  [%s] Accuracy=%.4f  F1=%.4f  AUROC=%s",
                label, acc, f1, f"{auroc:.4f}" if auroc else "N/A")
    return metrics


def evaluate_regressor(y_true, y_pred, label="") -> Dict[str, Any]:
    mae = mean_absolute_error(y_true, y_pred)
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    r2 = r2_score(y_true, y_pred)
    metrics = {
        "mae_hours": round(mae, 2),
        "rmse_hours": round(rmse, 2),
        "r2": round(r2, 4),
    }
    logger.info("  [%s] MAE=%.2fh  RMSE=%.2fh  R2=%.4f", label, mae, rmse, r2)
    return metrics


# ===================================================================
# Main
# ===================================================================
def main() -> None:
    logger.info("Starting Bed Management model training ...")
    train_df, val_df, test_df = load_datasets()

    registry = ModelRegistry(base_path=str(MODEL_DIR))

    # ----- 1. Discharge Classifier (binary: discharge within 24h) -----
    logger.info("\n--- Training DischargeClassifier ---")
    X_train, y_train = prepare_xy(train_df, TARGET_CLASSIFICATION)
    X_val, y_val = prepare_xy(val_df, TARGET_CLASSIFICATION)
    X_test, y_test = prepare_xy(test_df, TARGET_CLASSIFICATION)

    clf = DischargeClassifier(n_estimators=300, max_depth=6, learning_rate=0.1)
    clf.fit(X_train, y_train, X_val, y_val)

    # Evaluate
    y_pred_val = clf.predict(X_val)
    y_prob_val = clf.predict_proba(X_val)
    val_metrics = evaluate_classifier(y_val, y_pred_val, y_prob_val, "val")

    y_pred_test = clf.predict(X_test)
    y_prob_test = clf.predict_proba(X_test)
    test_metrics = evaluate_classifier(y_test, y_pred_test, y_prob_test, "test")

    # Feature importance
    fi = clf.feature_importances
    top_features = dict(sorted(fi.items(), key=lambda x: -x[1])[:15])
    logger.info("  Top features: %s", list(top_features.keys()))

    registry.save_model(
        clf, "bed_discharge_24h",
        metrics={"val": val_metrics, "test": test_metrics, "top_features": top_features},
        config=clf.get_params(),
    )

    # ----- 2. LOS Regressor (regression: remaining hours) -----
    logger.info("\n--- Training LOSRegressor ---")
    X_train_r, y_train_r = prepare_xy(train_df, TARGET_REGRESSION)
    X_val_r, y_val_r = prepare_xy(val_df, TARGET_REGRESSION)
    X_test_r, y_test_r = prepare_xy(test_df, TARGET_REGRESSION)

    reg = LOSRegressor(n_estimators=300, max_depth=6, learning_rate=0.1)
    reg.fit(X_train_r, y_train_r, X_val_r, y_val_r)

    y_pred_val_r = reg.predict(X_val_r)
    val_reg_metrics = evaluate_regressor(y_val_r, y_pred_val_r, "val")

    y_pred_test_r = reg.predict(X_test_r)
    test_reg_metrics = evaluate_regressor(y_test_r, y_pred_test_r, "test")

    fi_reg = reg.feature_importances
    top_features_reg = dict(sorted(fi_reg.items(), key=lambda x: -x[1])[:15])

    registry.save_model(
        reg, "bed_los_regressor",
        metrics={"val": val_reg_metrics, "test": test_reg_metrics,
                 "top_features": top_features_reg},
        config=reg.get_params(),
    )

    # ----- 3. Capacity Forecaster -----
    logger.info("\n--- Training CapacityForecaster ---")
    cap_path = DATASET_DIR / "capacity_hourly.parquet"
    if cap_path.exists():
        cap_df = pd.read_parquet(cap_path)
        forecaster = CapacityForecaster()
        forecaster.fit(cap_df)

        registry.save_model(
            forecaster, "bed_capacity_forecast",
            metrics={"n_departments": cap_df["careunit"].nunique() if not cap_df.empty else 0},
            config=forecaster.get_params(),
        )
        logger.info("  CapacityForecaster saved.")
    else:
        logger.warning("  capacity_hourly.parquet not found; skipping CapacityForecaster.")

    # ----- Summary -----
    print("\n" + "=" * 60)
    print("BED MANAGEMENT TRAINING SUMMARY")
    print("=" * 60)
    print(f"DischargeClassifier (24h):")
    print(f"  Val:  AUROC={val_metrics.get('auroc')}  F1={val_metrics['f1']}")
    print(f"  Test: AUROC={test_metrics.get('auroc')}  F1={test_metrics['f1']}")
    print(f"LOSRegressor:")
    print(f"  Val:  MAE={val_reg_metrics['mae_hours']}h  RMSE={val_reg_metrics['rmse_hours']}h  R2={val_reg_metrics['r2']}")
    print(f"  Test: MAE={test_reg_metrics['mae_hours']}h  RMSE={test_reg_metrics['rmse_hours']}h  R2={test_reg_metrics['r2']}")
    print("=" * 60)

    logger.info("Training complete. Models saved to %s", MODEL_DIR)


if __name__ == "__main__":
    main()
