"""
Waiting List Model Training
=============================
Trains PriorityClassifier and AdverseOutcomePredictor.

Usage::

    python -m app_09_waiting_list.backend.models.train
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score

from shared.ml.training_base import setup_training_env, get_dirs
logger = setup_training_env("waiting_list.train")
DATASET_DIR, MODEL_DIR = get_dirs("waiting_list")

from shared.ml.registry import ModelRegistry
from app_09_waiting_list.backend.models.priority_model import (
    PriorityClassifier, AdverseOutcomePredictor, URGENCY_LABELS,
)

NON_FEATURE_COLS = {
    "hadm_id", "urgency_category", "urgency_encoded", "adverse_outcome",
    "long_los", "readmission_30d", "los_days",
}


def prepare_xy(df, target):
    feature_cols = [c for c in df.columns if c not in NON_FEATURE_COLS]
    X = df[feature_cols].copy()
    obj_cols = X.select_dtypes(include=["object", "category"]).columns.tolist()
    if obj_cols:
        X = X.drop(columns=obj_cols)
    return X, df[target].values


def main():
    logger.info("Starting Waiting List model training ...")
    train = pd.read_parquet(DATASET_DIR / "train.parquet")
    val = pd.read_parquet(DATASET_DIR / "val.parquet")
    test = pd.read_parquet(DATASET_DIR / "test.parquet")
    logger.info("Loaded: train=%d, val=%d, test=%d", len(train), len(val), len(test))

    registry = ModelRegistry(base_path=str(MODEL_DIR))

    # --- 1. Priority Classifier ---
    logger.info("\n--- Training PriorityClassifier ---")
    X_tr, y_tr = prepare_xy(train, "urgency_encoded")
    X_va, y_va = prepare_xy(val, "urgency_encoded")
    X_te, y_te = prepare_xy(test, "urgency_encoded")

    clf = PriorityClassifier(n_estimators=200, max_depth=5)
    clf.fit(X_tr, y_tr, X_va, y_va)

    y_pred = clf.predict(X_te)
    y_prob = clf.predict_proba(X_te)
    acc = accuracy_score(y_te, y_pred)
    f1 = f1_score(y_te, y_pred, average="weighted", zero_division=0)
    try:
        auroc = roc_auc_score(y_te, y_prob, multi_class="ovr", average="weighted")
    except ValueError:
        auroc = None
    pri_metrics = {"accuracy": round(acc, 4), "weighted_f1": round(f1, 4),
                   "auroc": round(auroc, 4) if auroc else None}
    logger.info("  Priority: Acc=%.4f  F1=%.4f  AUROC=%s", acc, f1,
                f"{auroc:.4f}" if auroc else "N/A")
    registry.save_model(clf, "waiting_list_priority",
                        metrics={"test": pri_metrics}, config=clf.get_params())

    # --- 2. Adverse Outcome Predictor ---
    logger.info("\n--- Training AdverseOutcomePredictor ---")
    X_tr_a, y_tr_a = prepare_xy(train, "adverse_outcome")
    X_va_a, y_va_a = prepare_xy(val, "adverse_outcome")
    X_te_a, y_te_a = prepare_xy(test, "adverse_outcome")

    adv = AdverseOutcomePredictor(n_estimators=200, max_depth=5)
    adv.fit(X_tr_a, y_tr_a, X_va_a, y_va_a)

    y_pred_a = adv.predict(X_te_a)
    y_prob_a = adv.predict_proba(X_te_a)
    f1_a = f1_score(y_te_a, y_pred_a, zero_division=0)
    try:
        auroc_a = roc_auc_score(y_te_a, y_prob_a)
    except ValueError:
        auroc_a = None
    adv_metrics = {"f1": round(f1_a, 4), "auroc": round(auroc_a, 4) if auroc_a else None}
    logger.info("  Adverse: F1=%.4f  AUROC=%s", f1_a, f"{auroc_a:.4f}" if auroc_a else "N/A")
    registry.save_model(adv, "waiting_list_adverse",
                        metrics={"test": adv_metrics}, config=adv.get_params())

    print("\n" + "=" * 60)
    print("WAITING LIST TRAINING SUMMARY")
    print("=" * 60)
    print(f"Priority: Acc={pri_metrics['accuracy']}  F1={pri_metrics['weighted_f1']}  AUROC={pri_metrics.get('auroc')}")
    print(f"Adverse:  F1={adv_metrics['f1']}  AUROC={adv_metrics.get('auroc')}")
    print("=" * 60)
    logger.info("Training complete. Models: %s", MODEL_DIR)


if __name__ == "__main__":
    main()
