"""Training script for Sepsis ICU prediction models.

Trains both LightGBM (tabular) and LSTM (sequential) models, evaluates
with clinically-relevant metrics, and saves the best performer.

Usage
-----
    python -m backend.models.train
    python -m backend.models.train --data-dir ../datasets/sepsis_icu --model both
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)

from .sepsis_model import SepsisLGBM, SepsisLSTM

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)

PROJECT_ROOT = Path(__file__).resolve().parents[4]  # D:/project-demo/cancer
DEFAULT_DATA_DIR = PROJECT_ROOT / "datasets" / "sepsis_icu"
DEFAULT_MODEL_DIR = Path(__file__).resolve().parent / "saved"


# ============================================================================
# Data loading
# ============================================================================

def load_split(data_dir: Path, split: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load a single split and return (X_seq, X_flat, y)."""
    f = np.load(data_dir / f"{split}.npz")
    return f["X_seq"], f["X_flat"], f["y"]


# ============================================================================
# Evaluation
# ============================================================================

def sensitivity_at_specificity(
    y_true: np.ndarray, y_prob: np.ndarray, target_spec: float = 0.95,
) -> Tuple[float, float]:
    """Find sensitivity (recall) at a given specificity level.

    Returns (sensitivity, threshold).
    """
    fpr, tpr, thresholds = roc_curve(y_true, y_prob)
    specificity = 1 - fpr
    # Find the operating point closest to target specificity (from above)
    valid = specificity >= target_spec
    if not valid.any():
        return 0.0, 1.0
    idx = np.argmax(tpr[valid])
    actual_indices = np.where(valid)[0]
    best_idx = actual_indices[idx]
    return float(tpr[best_idx]), float(thresholds[best_idx])


def evaluate(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    name: str = "Model",
) -> Dict[str, Any]:
    """Compute comprehensive metrics for binary classification."""
    auroc = roc_auc_score(y_true, y_prob)
    auprc = average_precision_score(y_true, y_prob)

    sens_95, thresh_95 = sensitivity_at_specificity(y_true, y_prob, 0.95)

    # Use AUPRC-optimal threshold for general metrics
    precision_arr, recall_arr, thresholds_pr = precision_recall_curve(y_true, y_prob)
    f1_scores = 2 * (precision_arr * recall_arr) / (precision_arr + recall_arr + 1e-8)
    best_idx = np.argmax(f1_scores)
    optimal_threshold = float(thresholds_pr[best_idx]) if best_idx < len(thresholds_pr) else 0.5

    y_pred = (y_prob >= optimal_threshold).astype(int)
    acc = accuracy_score(y_true, y_pred)
    f1 = f1_score(y_true, y_pred)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred).ravel()

    metrics = {
        "model": name,
        "auroc": round(auroc, 4),
        "auprc": round(auprc, 4),
        "sensitivity_at_95_specificity": round(sens_95, 4),
        "threshold_at_95_specificity": round(thresh_95, 4),
        "optimal_threshold": round(optimal_threshold, 4),
        "accuracy": round(acc, 4),
        "f1": round(f1, 4),
        "sensitivity": round(tp / max(tp + fn, 1), 4),
        "specificity": round(tn / max(tn + fp, 1), 4),
        "ppv": round(tp / max(tp + fp, 1), 4),
        "npv": round(tn / max(tn + fn, 1), 4),
        "tp": int(tp),
        "fp": int(fp),
        "fn": int(fn),
        "tn": int(tn),
        "n_positive": int(y_true.sum()),
        "n_negative": int((y_true == 0).sum()),
        "prevalence": round(float(y_true.mean()), 4),
    }

    logger.info("=" * 60)
    logger.info("  %s Evaluation Results", name)
    logger.info("=" * 60)
    for k, v in metrics.items():
        logger.info("  %-35s %s", k, v)
    logger.info("=" * 60)

    return metrics


# ============================================================================
# Training routines
# ============================================================================

def train_lgbm(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    model_dir: Path,
) -> Dict[str, Any]:
    """Train and evaluate LightGBM model on flat features."""
    logger.info("Training LightGBM model ...")
    model = SepsisLGBM(
        n_estimators=1000,
        learning_rate=0.05,
        max_depth=7,
        num_leaves=63,
    )
    model.fit(X_train, y_train, X_val, y_val)

    # Evaluate on test
    y_prob = model.predict_proba(X_test)
    metrics = evaluate(y_test, y_prob, name="LightGBM")

    # Set threshold to the sensitivity@95%specificity point
    model.threshold = metrics["threshold_at_95_specificity"]

    # Save
    model_path = model_dir / "sepsis_lgbm.pkl"
    model.save(model_path)

    # Feature importance (top 20)
    imp = model.feature_importance()
    top_idx = np.argsort(imp)[::-1][:20]
    logger.info("Top 20 features by importance:")
    for rank, idx in enumerate(top_idx, 1):
        logger.info("  %2d. feature_%03d  importance=%d", rank, idx, imp[idx])

    return metrics


def train_lstm(
    X_seq_train: np.ndarray,
    y_train: np.ndarray,
    X_seq_val: np.ndarray,
    y_val: np.ndarray,
    X_seq_test: np.ndarray,
    y_test: np.ndarray,
    model_dir: Path,
) -> Dict[str, Any]:
    """Train and evaluate LSTM model on sequential features."""
    logger.info("Training LSTM model ...")
    input_dim = X_seq_train.shape[2]
    model = SepsisLSTM(
        input_dim=input_dim,
        hidden_dim=64,
        n_layers=2,
        dropout=0.3,
        lr=1e-3,
        epochs=50,
        batch_size=256,
        patience=8,
    )
    model.fit(X_seq_train, y_train, X_seq_val, y_val)

    # Evaluate on test
    y_prob = model.predict_proba(X_seq_test)
    metrics = evaluate(y_test, y_prob, name="LSTM-Attention")

    # Set threshold
    model.threshold = metrics["threshold_at_95_specificity"]

    # Save
    model_path = model_dir / "sepsis_lstm.pt"
    model.save(model_path)

    return metrics


# ============================================================================
# Main
# ============================================================================

def main(
    data_dir: Path = DEFAULT_DATA_DIR,
    model_dir: Path = DEFAULT_MODEL_DIR,
    model_type: str = "both",
) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Loading datasets from %s ...", data_dir)
    X_seq_train, X_flat_train, y_train = load_split(data_dir, "train")
    X_seq_val, X_flat_val, y_val = load_split(data_dir, "val")
    X_seq_test, X_flat_test, y_test = load_split(data_dir, "test")

    logger.info("Train: %d samples (%.1f%% positive)", len(y_train), 100 * y_train.mean())
    logger.info("Val:   %d samples (%.1f%% positive)", len(y_val), 100 * y_val.mean())
    logger.info("Test:  %d samples (%.1f%% positive)", len(y_test), 100 * y_test.mean())

    all_metrics: Dict[str, Any] = {}

    if model_type in ("lgbm", "both"):
        all_metrics["lgbm"] = train_lgbm(
            X_flat_train, y_train, X_flat_val, y_val, X_flat_test, y_test, model_dir,
        )

    if model_type in ("lstm", "both"):
        all_metrics["lstm"] = train_lstm(
            X_seq_train, y_train, X_seq_val, y_val, X_seq_test, y_test, model_dir,
        )

    # Compare and record best model
    if len(all_metrics) > 1:
        best_name = max(all_metrics, key=lambda k: all_metrics[k]["auroc"])
        logger.info(
            "Best model: %s (AUROC=%.4f, AUPRC=%.4f)",
            best_name,
            all_metrics[best_name]["auroc"],
            all_metrics[best_name]["auprc"],
        )
        all_metrics["best_model"] = best_name

    # Save metrics JSON
    metrics_path = model_dir / "metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(all_metrics, f, indent=2)
    logger.info("Metrics saved to %s", metrics_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train sepsis prediction models")
    parser.add_argument("--data-dir", type=str, default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--model-dir", type=str, default=str(DEFAULT_MODEL_DIR))
    parser.add_argument("--model", choices=["lgbm", "lstm", "both"], default="both")
    args = parser.parse_args()

    main(
        data_dir=Path(args.data_dir),
        model_dir=Path(args.model_dir),
        model_type=args.model,
    )
