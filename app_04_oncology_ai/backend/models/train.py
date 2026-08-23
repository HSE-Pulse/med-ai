"""Training script for oncology risk prediction models."""
import sys
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, average_precision_score, f1_score, classification_report

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT))

DATASET_DIR = ROOT / "datasets" / "oncology"
MODEL_DIR = ROOT / "models" / "oncology"

FEATURE_COLS = [
    "age", "gender_encoded", "stage_proxy", "drg_mortality",
    "num_procedures", "has_surgery", "has_chemotherapy", "has_radiation",
    "chemo_drug_count", "num_prior_admissions", "days_since_last_admission",
    "total_los_days", "num_comorbidities", "charlson_score",
    "insurance_encoded", "time_to_first_procedure_days",
]


def load_data():
    """Load train/val/test splits."""
    train = pd.read_parquet(DATASET_DIR / "train.parquet")
    val = pd.read_parquet(DATASET_DIR / "val.parquet")
    test = pd.read_parquet(DATASET_DIR / "test.parquet")
    return train, val, test


def prepare_features(df: pd.DataFrame, features: list[str], target: str):
    """Extract feature matrix and label vector."""
    available = [f for f in features if f in df.columns]
    X = df[available].fillna(0).values.astype(np.float32)
    y = df[target].values.astype(np.float32) if target in df.columns else np.zeros(len(df))
    return X, y, available


def evaluate(y_true, y_pred, y_prob, label=""):
    """Print evaluation metrics."""
    auroc = roc_auc_score(y_true, y_prob) if len(np.unique(y_true)) > 1 else 0.0
    auprc = average_precision_score(y_true, y_prob) if len(np.unique(y_true)) > 1 else 0.0
    f1 = f1_score(y_true, y_pred, zero_division=0)
    print(f"\n  {label} Metrics:")
    print(f"    AUROC:  {auroc:.4f}")
    print(f"    AUPRC:  {auprc:.4f}")
    print(f"    F1:     {f1:.4f}")
    print(f"    Pos rate (true): {y_true.mean():.3f}")
    print(f"    Pos rate (pred): {y_pred.mean():.3f}")
    return {"auroc": auroc, "auprc": auprc, "f1": f1}


def main():
    print("=" * 60)
    print("Training Oncology Risk Models")
    print("=" * 60)

    if not (DATASET_DIR / "train.parquet").exists():
        print("ERROR: Dataset not found. Run build_dataset.py first.")
        return

    train, val, test = load_data()
    print(f"Train: {len(train):,} | Val: {len(val):,} | Test: {len(test):,}")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    targets = ["readmission_30d", "hospital_mortality"]
    all_metrics = {}

    for target in targets:
        print(f"\n{'='*60}")
        print(f"  Target: {target}")
        print(f"{'='*60}")

        X_train, y_train, used_features = prepare_features(train, FEATURE_COLS, target)
        X_val, y_val, _ = prepare_features(val, FEATURE_COLS, target)
        X_test, y_test, _ = prepare_features(test, FEATURE_COLS, target)

        print(f"  Features: {len(used_features)}")
        print(f"  Train positive rate: {y_train.mean():.3f}")

        # ------ Baseline: XGBoost ------
        print("\n  Training XGBoost (baseline)...")
        from app_04_oncology_ai.backend.models.risk_model import OncologyRiskXGB

        xgb_model = OncologyRiskXGB(target=target, n_estimators=200)
        xgb_model.fit(X_train, y_train)

        y_prob_val = xgb_model.predict_proba(X_val)
        y_pred_val = (y_prob_val >= 0.5).astype(int)
        xgb_val_metrics = evaluate(y_val, y_pred_val, y_prob_val, f"XGBoost {target} (val)")

        y_prob_test = xgb_model.predict_proba(X_test)
        y_pred_test = (y_prob_test >= 0.5).astype(int)
        xgb_test_metrics = evaluate(y_test, y_pred_test, y_prob_test, f"XGBoost {target} (test)")

        # Feature importance
        importance = xgb_model.feature_importance()
        top_features = sorted(importance.items(), key=lambda x: x[1], reverse=True)[:10]
        print(f"\n  Top features: {[f'{k}={v:.3f}' for k, v in top_features]}")

        # Save XGBoost model
        xgb_path = MODEL_DIR / f"xgb_{target}"
        xgb_model.save(xgb_path)

        # ------ Advanced: Transformer ------
        print(f"\n  Training Transformer (advanced)...")
        try:
            from app_04_oncology_ai.backend.models.risk_model import OncologyTransformer

            tf_model = OncologyTransformer(
                n_features=X_train.shape[1], d_model=64, n_heads=4,
                n_layers=2, dropout=0.2, epochs=30, batch_size=256,
            )
            tf_model.fit(X_train, y_train)

            y_prob_val_tf = tf_model.predict_proba(X_val)
            y_pred_val_tf = (y_prob_val_tf >= 0.5).astype(int)
            tf_val_metrics = evaluate(y_val, y_pred_val_tf, y_prob_val_tf, f"Transformer {target} (val)")

            y_prob_test_tf = tf_model.predict_proba(X_test)
            y_pred_test_tf = (y_prob_test_tf >= 0.5).astype(int)
            tf_test_metrics = evaluate(y_test, y_pred_test_tf, y_prob_test_tf, f"Transformer {target} (test)")

            tf_path = MODEL_DIR / f"transformer_{target}"
            tf_model.save(tf_path)

            all_metrics[f"transformer_{target}"] = {
                "val": tf_val_metrics, "test": tf_test_metrics
            }
        except Exception as e:
            print(f"  Transformer training failed: {e}")

        all_metrics[f"xgb_{target}"] = {
            "val": xgb_val_metrics, "test": xgb_test_metrics,
            "feature_importance": dict(top_features),
        }

    # Save all metrics
    with open(MODEL_DIR / "metrics.json", "w") as f:
        json.dump(all_metrics, f, indent=2)

    print(f"\n{'='*60}")
    print("Training complete. Models saved to:", MODEL_DIR)
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
