"""
ED Flow Model Training
=======================
Trains DispositionClassifier, EDLOSRegressor, and PETBreachClassifier.

Usage::

    python -m app_14_ed_flow.backend.models.train
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score, f1_score, mean_absolute_error, mean_squared_error,
    r2_score, roc_auc_score,
)

from shared.ml.training_base import setup_training_env, get_dirs
logger = setup_training_env("ed_flow.train")
DATASET_DIR, MODEL_DIR = get_dirs("ed_flow")

from shared.ml.registry import ModelRegistry
from app_14_ed_flow.backend.models.flow_model import (
    DISPOSITION_LABELS, DispositionClassifier, EDLOSRegressor, PETBreachClassifier,
)

NON_FEATURE_COLS = {
    "hadm_id", "ed_los_minutes", "disposition", "disposition_encoded",
    "pet_breach", "lwbs",
}


def load_datasets():
    train = pd.read_parquet(DATASET_DIR / "train.parquet")
    val = pd.read_parquet(DATASET_DIR / "val.parquet")
    test = pd.read_parquet(DATASET_DIR / "test.parquet")
    logger.info("Loaded: train=%d, val=%d, test=%d", len(train), len(val), len(test))
    return train, val, test


def prepare_xy(df, target):
    feature_cols = [c for c in df.columns if c not in NON_FEATURE_COLS]
    X = df[feature_cols].copy()
    obj_cols = X.select_dtypes(include=["object", "category"]).columns.tolist()
    if obj_cols:
        X = X.drop(columns=obj_cols)
    y = df[target].values
    return X, y


def main() -> None:
    logger.info("Starting ED Flow model training ...")
    train_df, val_df, test_df = load_datasets()
    registry = ModelRegistry(base_path=str(MODEL_DIR))

    # ----- 1. Disposition Classifier -----
    logger.info("\n--- Training DispositionClassifier ---")
    X_tr, y_tr = prepare_xy(train_df, "disposition_encoded")
    X_va, y_va = prepare_xy(val_df, "disposition_encoded")
    X_te, y_te = prepare_xy(test_df, "disposition_encoded")

    disp_clf = DispositionClassifier(n_estimators=300, max_depth=6)
    disp_clf.fit(X_tr, y_tr, X_va, y_va)

    y_pred = disp_clf.predict(X_te)
    y_prob = disp_clf.predict_proba(X_te)
    acc = accuracy_score(y_te, y_pred)
    f1 = f1_score(y_te, y_pred, average="weighted", zero_division=0)
    try:
        auroc = roc_auc_score(y_te, y_prob, multi_class="ovr", average="weighted")
    except ValueError:
        auroc = None
    disp_metrics = {"accuracy": round(acc, 4), "weighted_f1": round(f1, 4),
                    "auroc": round(auroc, 4) if auroc else None}
    logger.info("  Disposition: Acc=%.4f  F1=%.4f  AUROC=%s", acc, f1,
                f"{auroc:.4f}" if auroc else "N/A")

    registry.save_model(disp_clf, "ed_flow_disposition",
                        metrics={"test": disp_metrics}, config=disp_clf.get_params())

    # ----- 2. ED LOS Regressor -----
    logger.info("\n--- Training EDLOSRegressor ---")
    X_tr_r, y_tr_r = prepare_xy(train_df, "ed_los_minutes")
    X_va_r, y_va_r = prepare_xy(val_df, "ed_los_minutes")
    X_te_r, y_te_r = prepare_xy(test_df, "ed_los_minutes")

    los_reg = EDLOSRegressor(n_estimators=300, max_depth=6)
    los_reg.fit(X_tr_r, y_tr_r, X_va_r, y_va_r)

    y_pred_r = los_reg.predict(X_te_r)
    mae = mean_absolute_error(y_te_r, y_pred_r)
    rmse = float(np.sqrt(mean_squared_error(y_te_r, y_pred_r)))
    r2 = r2_score(y_te_r, y_pred_r)
    los_metrics = {"mae_minutes": round(mae, 1), "rmse_minutes": round(rmse, 1), "r2": round(r2, 4)}
    logger.info("  LOS: MAE=%.1f min  RMSE=%.1f min  R2=%.4f", mae, rmse, r2)

    registry.save_model(los_reg, "ed_flow_los",
                        metrics={"test": los_metrics}, config=los_reg.get_params())

    # ----- 3. PET Breach Classifier -----
    logger.info("\n--- Training PETBreachClassifier ---")
    X_tr_p, y_tr_p = prepare_xy(train_df, "pet_breach")
    X_va_p, y_va_p = prepare_xy(val_df, "pet_breach")
    X_te_p, y_te_p = prepare_xy(test_df, "pet_breach")

    pet_clf = PETBreachClassifier(n_estimators=300, max_depth=6)
    pet_clf.fit(X_tr_p, y_tr_p, X_va_p, y_va_p)

    y_pred_p = pet_clf.predict(X_te_p)
    y_prob_p = pet_clf.predict_proba(X_te_p)
    f1_p = f1_score(y_te_p, y_pred_p, zero_division=0)
    try:
        auroc_p = roc_auc_score(y_te_p, y_prob_p)
    except ValueError:
        auroc_p = None
    pet_metrics = {"f1": round(f1_p, 4), "auroc": round(auroc_p, 4) if auroc_p else None}
    logger.info("  PET: F1=%.4f  AUROC=%s", f1_p, f"{auroc_p:.4f}" if auroc_p else "N/A")

    registry.save_model(pet_clf, "ed_flow_pet_breach",
                        metrics={"test": pet_metrics}, config=pet_clf.get_params())

    # Summary
    print("\n" + "=" * 60)
    print("ED FLOW TRAINING SUMMARY")
    print("=" * 60)
    print(f"Disposition: Acc={disp_metrics['accuracy']}  F1={disp_metrics['weighted_f1']}  AUROC={disp_metrics.get('auroc')}")
    print(f"ED LOS: MAE={los_metrics['mae_minutes']}min  RMSE={los_metrics['rmse_minutes']}min  R2={los_metrics['r2']}")
    print(f"PET Breach: F1={pet_metrics['f1']}  AUROC={pet_metrics.get('auroc')}")
    print("=" * 60)

    logger.info("Training complete. Models: %s", MODEL_DIR)


if __name__ == "__main__":
    main()
