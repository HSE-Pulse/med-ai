"""
ED Triage Model Training Script
================================
Loads Parquet datasets, trains both XGBoost (baseline) and NN (advanced)
models for ESI-equivalent acuity classification, evaluates on the
validation set, and persists the best model via the shared ModelRegistry.

Usage::

    python -m app_01_ed_triage.backend.models.train          # from cancer/
    python train.py                                           # from models/

Environment variables:
    DATASET_DIR   Path to Parquet splits (default: D:/project-demo/cancer/datasets/ed_triage)
    MODEL_DIR     Path for persisted models (default: D:/project-demo/cancer/models/ed_triage)
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    f1_score,
    roc_auc_score,
)

# ---------------------------------------------------------------------------
# Ensure imports resolve
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_PROJECT_ROOT))

from shared.ml.registry import ModelRegistry
from app_01_ed_triage.backend.models.triage_model import (
    NUM_CLASSES,
    ACUITY_LABELS,
    TriageNN,
    TriageXGBoost,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("ed_triage.train")

DATASET_DIR = Path(os.getenv("DATASET_DIR", "D:/project-demo/cancer/datasets/ed_triage"))
MODEL_DIR = Path(os.getenv("MODEL_DIR", "D:/project-demo/cancer/models/ed_triage"))

TARGET_COL = "acuity_level"


# ===================================================================
# Helpers
# ===================================================================
def load_datasets() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load train / val / test Parquet files."""
    train = pd.read_parquet(DATASET_DIR / "train.parquet")
    val = pd.read_parquet(DATASET_DIR / "val.parquet")
    test = pd.read_parquet(DATASET_DIR / "test.parquet")
    logger.info("Loaded datasets: train=%d, val=%d, test=%d", len(train), len(val), len(test))
    return train, val, test


def prepare_xy(
    df: pd.DataFrame,
) -> Tuple[pd.DataFrame, np.ndarray]:
    """Split a dataframe into feature matrix X and 0-indexed label array y.

    The target column (``acuity_level``) uses values 1--5; we shift to 0--4
    for model training and shift back during inference.
    """
    non_feature_cols = {"hadm_id", "acuity_level", "acuity_label", "disposition", "ed_los_hours",
                        "subject_id", "admittime", "dischtime", "edregtime", "edouttime"}
    feature_cols = [c for c in df.columns if c not in non_feature_cols]
    X = df[feature_cols].copy()
    # Drop any remaining object/string columns (raw categoricals before encoding)
    obj_cols = X.select_dtypes(include=["object", "category"]).columns.tolist()
    if obj_cols:
        X = X.drop(columns=obj_cols)
    y = (df[TARGET_COL].values - 1).astype(int)  # 0-indexed
    return X, y


def evaluate_model(
    model: Any,
    X: pd.DataFrame,
    y: np.ndarray,
    label: str,
) -> Dict[str, Any]:
    """Compute and print classification metrics.

    Returns a dict with accuracy, weighted-F1, per-class F1, and AUROC.
    """
    y_pred = model.predict(X)
    y_proba = model.predict_proba(X)

    acc = accuracy_score(y, y_pred)
    f1_w = f1_score(y, y_pred, average="weighted", zero_division=0)

    # Per-class F1
    report = classification_report(
        y, y_pred,
        target_names=[f"ESI-{i+1}" for i in range(NUM_CLASSES)],
        output_dict=True,
        zero_division=0,
    )

    # AUROC (one-vs-rest, macro)
    try:
        # OVR AUROC requires at least 2 classes present
        auroc = roc_auc_score(y, y_proba, multi_class="ovr", average="weighted")
    except ValueError:
        auroc = float("nan")

    print(f"\n{'=' * 50}")
    print(f"  {label} Evaluation")
    print(f"{'=' * 50}")
    print(f"  Accuracy:     {acc:.4f}")
    print(f"  Weighted F1:  {f1_w:.4f}")
    print(f"  AUROC (OVR):  {auroc:.4f}")
    print()
    print(
        classification_report(
            y, y_pred,
            target_names=[f"ESI-{i+1}" for i in range(NUM_CLASSES)],
            zero_division=0,
        )
    )

    metrics: Dict[str, Any] = {
        "accuracy": round(acc, 4),
        "weighted_f1": round(f1_w, 4),
        "auroc": round(auroc, 4) if not np.isnan(auroc) else None,
        "per_class_f1": {
            f"ESI-{i+1}": round(report.get(f"ESI-{i+1}", {}).get("f1-score", 0), 4)
            for i in range(NUM_CLASSES)
        },
    }
    return metrics


# ===================================================================
# Training
# ===================================================================
def train_xgboost(
    X_train: pd.DataFrame,
    y_train: np.ndarray,
    X_val: pd.DataFrame,
    y_val: np.ndarray,
) -> Tuple[TriageXGBoost, Dict[str, Any]]:
    """Train and evaluate the XGBoost baseline."""
    logger.info("Training XGBoost baseline ...")
    model = TriageXGBoost(
        n_estimators=300,
        max_depth=6,
        learning_rate=0.1,
        min_child_weight=5,
        subsample=0.8,
        colsample_bytree=0.8,
    )
    model.fit(X_train, y_train, X_val=X_val, y_val=y_val)
    metrics = evaluate_model(model, X_val, y_val, "XGBoost (Validation)")
    return model, metrics


def train_nn(
    X_train: pd.DataFrame,
    y_train: np.ndarray,
    X_val: pd.DataFrame,
    y_val: np.ndarray,
) -> Tuple[TriageNN, Dict[str, Any]]:
    """Train and evaluate the NN model."""
    logger.info("Training Neural Network ...")
    model = TriageNN(
        numeric_dim=X_train.shape[1],
        hidden_dims=[256, 128, 64],
        dropout=0.3,
        lr=1e-3,
        epochs=50,
        batch_size=256,
        patience=7,
    )
    model.fit(X_train, y_train, X_val=X_val, y_val=y_val)
    metrics = evaluate_model(model, X_val, y_val, "Neural Network (Validation)")
    return model, metrics


# ===================================================================
# Main
# ===================================================================
def main() -> None:
    """Run the full training pipeline."""
    train_df, val_df, test_df = load_datasets()
    X_train, y_train = prepare_xy(train_df)
    X_val, y_val = prepare_xy(val_df)
    X_test, y_test = prepare_xy(test_df)

    logger.info("Feature count: %d", X_train.shape[1])
    logger.info("Training samples: %d", len(X_train))

    # Train both models
    xgb_model, xgb_metrics = train_xgboost(X_train, y_train, X_val, y_val)
    nn_model, nn_metrics = train_nn(X_train, y_train, X_val, y_val)

    # Pick best by weighted F1
    registry = ModelRegistry(base_path=str(MODEL_DIR))

    xgb_f1 = xgb_metrics["weighted_f1"]
    nn_f1 = nn_metrics["weighted_f1"]

    if xgb_f1 >= nn_f1:
        best_name, best_model, best_metrics = "xgboost", xgb_model, xgb_metrics
        best_config = xgb_model.get_params()
    else:
        best_name, best_model, best_metrics = "neural_net", nn_model, nn_metrics
        best_config = nn_model.get_params()

    logger.info(
        "Best model: %s (weighted F1 = %.4f)", best_name, best_metrics["weighted_f1"]
    )

    # Save both models
    registry.save_model(xgb_model, "ed_triage_xgb", metrics=xgb_metrics, config=xgb_model.get_params())
    registry.save_model(nn_model, "ed_triage_nn", metrics=nn_metrics, config=nn_model.get_params())

    # Save best as the default serving model
    registry.save_model(best_model, "ed_triage_best", metrics=best_metrics, config=best_config)

    # Feature importance (XGBoost only)
    fi = xgb_model.feature_importances
    if fi:
        print("\nTop 15 Feature Importances (XGBoost):")
        sorted_fi = sorted(fi.items(), key=lambda x: x[1], reverse=True)[:15]
        for name, imp in sorted_fi:
            print(f"  {name:30s}  {imp:.4f}")

    # Final test evaluation
    print("\n" + "#" * 50)
    print("  FINAL TEST SET EVALUATION")
    print("#" * 50)
    test_metrics = evaluate_model(best_model, X_test, y_test, f"Best ({best_name}) - Test")

    # Update metadata with test metrics
    registry.save_model(
        best_model,
        "ed_triage_best",
        metrics={**best_metrics, "test": test_metrics},
        config=best_config,
    )

    logger.info("Training complete. Models saved to: %s", MODEL_DIR)


if __name__ == "__main__":
    main()
