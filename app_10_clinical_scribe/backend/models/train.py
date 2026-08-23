"""
Clinical Scribe Model Training
================================
Trains ICDCoder, KeywordNEREngine, and SectionClassifier.

Usage::

    python -m app_10_clinical_scribe.backend.models.train
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score

from shared.ml.training_base import setup_training_env, get_dirs
logger = setup_training_env("clinical_scribe.train")
DATASET_DIR, MODEL_DIR = get_dirs("clinical_scribe")

from shared.ml.registry import ModelRegistry
from app_10_clinical_scribe.backend.models.scribe_model import (
    ICDCoder, KeywordNEREngine, SectionClassifier,
)


def main():
    logger.info("Starting Clinical Scribe model training ...")
    registry = ModelRegistry(base_path=str(MODEL_DIR))

    # ----- 1. ICD Coder -----
    logger.info("\n--- Training ICDCoder ---")
    icd_train_path = DATASET_DIR / "icd_coding_train.parquet"
    icd_test_path = DATASET_DIR / "icd_coding_test.parquet"

    if icd_train_path.exists():
        train_df = pd.read_parquet(icd_train_path)
        test_df = pd.read_parquet(icd_test_path)
        logger.info("  ICD data: train=%d, test=%d", len(train_df), len(test_df))

        texts_train = train_df["text"].tolist()
        label_cols = [c for c in train_df.columns if c not in ("hadm_id", "subject_id", "text")]

        coder = ICDCoder(max_features=10000, ngram_range=(1, 2), C=1.0)
        coder.fit(texts_train, train_df[label_cols])

        # Evaluate on test
        texts_test = test_df["text"].tolist()
        predictions = coder.predict_batch(texts_test)

        # Per-code AUROC
        code_aurocs = {}
        for code in predictions:
            if code in test_df.columns:
                y_true = test_df[code].values
                y_prob = predictions[code]
                if y_true.sum() > 0 and y_true.sum() < len(y_true):
                    try:
                        code_aurocs[code] = round(float(roc_auc_score(y_true, y_prob)), 4)
                    except ValueError:
                        pass

        mean_auroc = round(np.mean(list(code_aurocs.values())), 4) if code_aurocs else 0
        logger.info("  ICD Coder: %d codes, mean AUROC=%.4f", len(code_aurocs), mean_auroc)

        registry.save_model(
            coder, "scribe_icd_coder",
            metrics={"mean_auroc": mean_auroc, "n_codes_evaluated": len(code_aurocs),
                     "top_code_aurocs": dict(sorted(code_aurocs.items(), key=lambda x: -x[1])[:10])},
            config=coder.get_params(),
        )
    else:
        logger.warning("  ICD coding data not found. Skipping.")

    # ----- 2. NER Engine -----
    logger.info("\n--- Training KeywordNEREngine ---")
    ner_path = DATASET_DIR / "ner_ground_truth.parquet"
    if ner_path.exists():
        ner_df = pd.read_parquet(ner_path)
        # Collect all unique medications
        all_meds = set()
        for meds_json in ner_df["medications"]:
            try:
                meds = json.loads(meds_json)
                all_meds.update(meds)
            except (json.JSONDecodeError, TypeError):
                pass

        ner_engine = KeywordNEREngine()
        ner_engine.fit(list(all_meds))
        logger.info("  NER Engine: %d medication terms", len(all_meds))

        registry.save_model(
            ner_engine, "scribe_ner_engine",
            metrics={"n_medications": len(all_meds)},
            config=ner_engine.get_params(),
        )
    else:
        logger.warning("  NER ground truth not found. Skipping.")

    # ----- 3. Section Classifier -----
    logger.info("\n--- Training SectionClassifier ---")
    sec_train_path = DATASET_DIR / "note_sections_train.parquet"
    sec_test_path = DATASET_DIR / "note_sections_test.parquet"

    if sec_train_path.exists():
        sec_train = pd.read_parquet(sec_train_path)
        sec_test = pd.read_parquet(sec_test_path)
        logger.info("  Section data: train=%d, test=%d", len(sec_train), len(sec_test))

        sec_clf = SectionClassifier(max_features=5000)
        sec_clf.fit(sec_train["text_chunk"].tolist(), sec_train["section_label"].tolist())

        # Evaluate
        y_pred = [sec_clf.predict(t) for t in sec_test["text_chunk"].tolist()]
        y_true = sec_test["section_label"].tolist()
        acc = accuracy_score(y_true, y_pred)
        f1 = f1_score(y_true, y_pred, average="weighted", zero_division=0)
        logger.info("  Section Classifier: Acc=%.4f  F1=%.4f", acc, f1)

        registry.save_model(
            sec_clf, "scribe_section_clf",
            metrics={"accuracy": round(acc, 4), "weighted_f1": round(f1, 4)},
            config=sec_clf.get_params(),
        )
    else:
        logger.warning("  Section data not found. Skipping.")

    # Summary
    print("\n" + "=" * 60)
    print("CLINICAL SCRIBE TRAINING SUMMARY")
    print("=" * 60)
    if icd_train_path.exists():
        print(f"ICD Coder: {len(code_aurocs)} codes, mean AUROC={mean_auroc}")
    if sec_train_path.exists():
        print(f"Section Classifier: Acc={acc:.4f}  F1={f1:.4f}")
    print("=" * 60)
    logger.info("Training complete. Models: %s", MODEL_DIR)


if __name__ == "__main__":
    main()
