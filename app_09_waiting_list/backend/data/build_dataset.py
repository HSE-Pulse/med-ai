"""
Waiting List Intelligence Dataset Builder
==========================================
Extracts elective admissions from MIMIC-IV to build a priority scoring
and adverse outcome prediction dataset.  Features include demographics,
diagnosis complexity, prior history, procedures, medications, and DRG
severity.

Usage::

    python -m app_09_waiting_list.backend.data.build_dataset
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_PROJECT_ROOT))

from shared.db.mongo import MongoManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("waiting_list.build_dataset")

DATASET_OUT_DIR = Path(
    os.getenv("DATASET_OUT_DIR", "./datasets/waiting_list")
)
BATCH = 5000

ELECTIVE_TYPES = ["ELECTIVE", "SURGICAL SAME DAY ADMIT"]


# ===================================================================
# 1.  EXTRACT ELECTIVE COHORT
# ===================================================================
def extract_elective_cohort(mongo: MongoManager) -> pd.DataFrame:
    logger.info("Fetching elective admissions ...")
    docs = mongo.fetch_admissions(
        filters={"admission_type": {"$in": ELECTIVE_TYPES}},
        fields={
            "_id": 0, "subject_id": 1, "hadm_id": 1, "admittime": 1,
            "dischtime": 1, "admission_type": 1, "discharge_location": 1,
            "hospital_expire_flag": 1, "insurance": 1,
        },
    )
    df = pd.DataFrame(docs)
    for col in ("admittime", "dischtime"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    df = df.dropna(subset=["admittime", "hadm_id"]).copy()
    logger.info("  Elective admissions: %d", len(df))
    return df


# ===================================================================
# 2.  DEMOGRAPHICS
# ===================================================================
def attach_demographics(df: pd.DataFrame, mongo: MongoManager) -> pd.DataFrame:
    logger.info("Fetching demographics ...")
    subject_ids = df["subject_id"].unique().tolist()
    pat_docs = list(mongo.mimic["patients"].find(
        {"subject_id": {"$in": subject_ids}},
        {"_id": 0, "subject_id": 1, "gender": 1, "anchor_age": 1, "anchor_year": 1},
    ))
    if not pat_docs:
        df["age"] = np.nan
        df["gender_encoded"] = 0
        return df
    pat_df = pd.DataFrame(pat_docs)
    df = df.merge(pat_df, on="subject_id", how="left")
    if "anchor_age" in df.columns:
        df["age"] = df["anchor_age"] + (df["admittime"].dt.year - df["anchor_year"])
        df["age"] = df["age"].clip(lower=18, upper=100)
        df.drop(columns=["anchor_age", "anchor_year"], inplace=True, errors="ignore")
    df["gender_encoded"] = (
        df.get("gender", pd.Series("U", index=df.index)).str.upper().str.strip() == "M"
    ).astype(int)
    return df


# ===================================================================
# 3.  DIAGNOSES + CHARLSON SCORE
# ===================================================================
def attach_diagnoses(df: pd.DataFrame, mongo: MongoManager) -> pd.DataFrame:
    logger.info("Fetching diagnoses & computing Charlson score ...")
    hadm_ids = df["hadm_id"].dropna().astype(int).unique().tolist()
    diag_records: List[Dict] = []
    for i in range(0, len(hadm_ids), BATCH):
        diag_records.extend(list(mongo.mimic["diagnoses_icd"].find(
            {"hadm_id": {"$in": hadm_ids[i:i+BATCH]}},
            {"_id": 0, "hadm_id": 1, "icd_code": 1, "seq_num": 1},
        )))
    logger.info("  Fetched %d diagnosis records", len(diag_records))

    if not diag_records:
        df["num_diagnoses"] = 0
        df["charlson_score"] = 0
        df["primary_icd_category"] = "Unknown"
        return df

    diag_df = pd.DataFrame(diag_records)
    # Count per admission
    counts = diag_df.groupby("hadm_id").size().reset_index(name="num_diagnoses")
    # Primary ICD
    primary = diag_df[diag_df["seq_num"] == 1].drop_duplicates(subset=["hadm_id"], keep="first")
    primary["primary_icd_category"] = primary["icd_code"].apply(_icd_to_category)
    # Charlson per admission
    charlson = diag_df.groupby("hadm_id")["icd_code"].apply(
        lambda codes: _compute_charlson(codes.tolist())
    ).reset_index(name="charlson_score")

    df = df.merge(counts, on="hadm_id", how="left")
    df = df.merge(primary[["hadm_id", "primary_icd_category"]], on="hadm_id", how="left")
    df = df.merge(charlson, on="hadm_id", how="left")
    df["num_diagnoses"] = df["num_diagnoses"].fillna(0).astype(int)
    df["charlson_score"] = df["charlson_score"].fillna(0).astype(int)
    df["primary_icd_category"] = df["primary_icd_category"].fillna("Unknown")
    return df


def _compute_charlson(codes: List[str]) -> int:
    """Simplified Charlson score from ICD codes."""
    score = 0
    codes_str = [str(c).upper() for c in codes if pd.notna(c)]
    if any(c.startswith(("I21", "I22", "410")) for c in codes_str): score += 1
    if any(c.startswith(("I50", "428")) for c in codes_str): score += 1
    if any(c.startswith(("I6", "43")) for c in codes_str): score += 1
    if any(c.startswith(("E10", "E11", "E12", "E13", "E14", "250")) for c in codes_str): score += 1
    if any(c.startswith(("J4", "49")) for c in codes_str): score += 1
    if any(c.startswith(("N18", "N19", "585", "586")) for c in codes_str): score += 2
    if any(c.startswith(("K7", "571")) for c in codes_str): score += 1
    if any(c.startswith(("C77", "C78", "C79", "196", "197", "198", "199")) for c in codes_str): score += 6
    return score


def _icd_to_category(code) -> str:
    if pd.isna(code) or not isinstance(code, str) or len(code) == 0:
        return "Unknown"
    c = code[0].upper()
    mapping = {"A": "Infectious", "B": "Infectious", "C": "Neoplasm",
               "I": "Circulatory", "J": "Respiratory", "K": "Digestive",
               "M": "Musculoskeletal", "N": "Genitourinary", "S": "Injury", "T": "Injury"}
    return mapping.get(c, "Other")


# ===================================================================
# 4.  PRIOR ADMISSION HISTORY
# ===================================================================
def attach_prior_history(df: pd.DataFrame) -> pd.DataFrame:
    logger.info("Computing prior admission history ...")
    df = df.sort_values(["subject_id", "admittime"]).copy()
    df["num_prior_admissions"] = df.groupby("subject_id").cumcount()
    df["prev_dischtime"] = df.groupby("subject_id")["dischtime"].shift(1)
    df["days_since_last_admission"] = (
        (df["admittime"] - df["prev_dischtime"]).dt.total_seconds() / 86400
    ).fillna(-1)
    df.drop(columns=["prev_dischtime"], inplace=True, errors="ignore")
    return df


# ===================================================================
# 5.  PROCEDURES
# ===================================================================
def attach_procedures(df: pd.DataFrame, mongo: MongoManager) -> pd.DataFrame:
    logger.info("Fetching procedures ...")
    hadm_ids = df["hadm_id"].dropna().astype(int).unique().tolist()
    proc_records: List[Dict] = []
    for i in range(0, len(hadm_ids), BATCH):
        proc_records.extend(list(mongo.mimic["procedures_icd"].find(
            {"hadm_id": {"$in": hadm_ids[i:i+BATCH]}}, {"_id": 0, "hadm_id": 1},
        )))
    counts = pd.DataFrame(proc_records).groupby("hadm_id").size().reset_index(
        name="num_procedures") if proc_records else pd.DataFrame(columns=["hadm_id", "num_procedures"])
    df = df.merge(counts, on="hadm_id", how="left")
    df["num_procedures"] = df["num_procedures"].fillna(0).astype(int)
    df["has_surgery"] = (df["num_procedures"] > 0).astype(int)
    return df


# ===================================================================
# 6.  MEDICATIONS
# ===================================================================
def attach_medications(df: pd.DataFrame, mongo: MongoManager) -> pd.DataFrame:
    logger.info("Fetching medications ...")
    hadm_ids = df["hadm_id"].dropna().astype(int).unique().tolist()
    med_counts = {}
    for i in range(0, len(hadm_ids), BATCH):
        batch = hadm_ids[i:i+BATCH]
        cursor = mongo.mimic["prescriptions"].find(
            {"hadm_id": {"$in": batch}}, {"_id": 0, "hadm_id": 1, "drug": 1},
        )
        for doc in cursor:
            hid = doc.get("hadm_id")
            if hid:
                med_counts[hid] = med_counts.get(hid, set())
                drug = doc.get("drug")
                if drug:
                    med_counts[hid].add(drug)
        if (i // BATCH) % 5 == 0:
            logger.info("  Processed %d/%d hadm_ids for medications", min(i+BATCH, len(hadm_ids)), len(hadm_ids))

    med_df = pd.DataFrame([
        {"hadm_id": hid, "num_medications": len(drugs)}
        for hid, drugs in med_counts.items()
    ])
    if not med_df.empty:
        df = df.merge(med_df, on="hadm_id", how="left")
    df["num_medications"] = df.get("num_medications", pd.Series(0, index=df.index)).fillna(0).astype(int)
    return df


# ===================================================================
# 7.  DRG SEVERITY
# ===================================================================
def attach_drg(df: pd.DataFrame, mongo: MongoManager) -> pd.DataFrame:
    logger.info("Fetching DRG codes ...")
    hadm_ids = df["hadm_id"].dropna().astype(int).unique().tolist()
    drg_records: List[Dict] = []
    for i in range(0, len(hadm_ids), BATCH):
        drg_records.extend(list(mongo.mimic["drgcodes"].find(
            {"hadm_id": {"$in": hadm_ids[i:i+BATCH]}},
            {"_id": 0, "hadm_id": 1, "drg_severity": 1, "drg_mortality": 1},
        )))
    if drg_records:
        drg_df = pd.DataFrame(drg_records)
        drg_agg = drg_df.groupby("hadm_id").agg(
            drg_severity=("drg_severity", "max"),
            drg_mortality=("drg_mortality", "max"),
        ).reset_index()
        df = df.merge(drg_agg, on="hadm_id", how="left")
    df["drg_severity"] = df.get("drg_severity", pd.Series(2, index=df.index)).fillna(2).clip(1, 4)
    df["drg_mortality"] = df.get("drg_mortality", pd.Series(1, index=df.index)).fillna(1).clip(1, 4)
    return df


# ===================================================================
# 8.  ICU TRANSFER FLAG
# ===================================================================
def attach_icu_flag(df: pd.DataFrame, mongo: MongoManager) -> pd.DataFrame:
    logger.info("Checking ICU transfers ...")
    hadm_ids = df["hadm_id"].dropna().astype(int).unique().tolist()
    icu_hadms = set()
    for i in range(0, len(hadm_ids), BATCH):
        batch = hadm_ids[i:i+BATCH]
        cursor = mongo.mimic["transfers"].find(
            {"hadm_id": {"$in": batch}, "careunit": {"$regex": "ICU|CCU|MICU|SICU", "$options": "i"}},
            {"_id": 0, "hadm_id": 1},
        )
        for doc in cursor:
            icu_hadms.add(doc["hadm_id"])
    df["had_icu_transfer"] = df["hadm_id"].isin(icu_hadms).astype(int)
    logger.info("  ICU transfer rate: %.1f%%", df["had_icu_transfer"].mean() * 100)
    return df


# ===================================================================
# 9.  DERIVE TARGETS
# ===================================================================
def derive_targets(df: pd.DataFrame) -> pd.DataFrame:
    logger.info("Deriving targets ...")
    # LOS
    if "dischtime" in df.columns:
        df["los_days"] = (df["dischtime"] - df["admittime"]).dt.total_seconds() / 86400
        df["los_days"] = df["los_days"].clip(lower=0, upper=365)
    else:
        df["los_days"] = np.nan

    # Long LOS
    median_los = df["los_days"].median()
    df["long_los"] = (df["los_days"] > median_los).astype(int)
    logger.info("  Median LOS: %.1f days, long_los rate: %.1f%%",
                median_los, df["long_los"].mean() * 100)

    # Readmission within 30 days
    df = df.sort_values(["subject_id", "admittime"])
    df["next_admittime"] = df.groupby("subject_id")["admittime"].shift(-1)
    df["days_to_readmission"] = (
        (df["next_admittime"] - df["dischtime"]).dt.total_seconds() / 86400
    )
    df["readmission_30d"] = ((df["days_to_readmission"] >= 0) & (df["days_to_readmission"] <= 30)).astype(int).fillna(0)
    df.drop(columns=["next_admittime", "days_to_readmission"], inplace=True, errors="ignore")

    # Adverse outcome
    df["adverse_outcome"] = (
        (df["hospital_expire_flag"] == 1) | (df["readmission_30d"] == 1)
    ).astype(int)

    # Urgency category
    def _assign_urgency(row):
        if row.get("hospital_expire_flag") == 1 or row.get("had_icu_transfer") == 1:
            return "urgent"
        if row.get("readmission_30d") == 1 or row.get("charlson_score", 0) > 5:
            return "soon"
        return "routine"

    df["urgency_category"] = df.apply(_assign_urgency, axis=1)
    urgency_map = {"urgent": 0, "soon": 1, "routine": 2}
    df["urgency_encoded"] = df["urgency_category"].map(urgency_map)

    logger.info("  Urgency dist: %s", df["urgency_category"].value_counts().to_dict())
    logger.info("  Adverse outcome rate: %.1f%%", df["adverse_outcome"].mean() * 100)
    return df


# ===================================================================
# 10.  FEATURE ENGINEERING
# ===================================================================
def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    if "age" in df.columns:
        df["age"] = df["age"].fillna(df["age"].median())
    # Insurance encoding
    ins_map = {"Medicare": 0, "Medicaid": 1, "Other": 2}
    df["insurance_encoded"] = df.get("insurance", pd.Series("Other", index=df.index)).map(ins_map).fillna(2).astype(int)
    # Admission type encoding
    atype_map = {"ELECTIVE": 0, "SURGICAL SAME DAY ADMIT": 1}
    df["admission_type_encoded"] = df["admission_type"].map(atype_map).fillna(0).astype(int)
    # ICD dummies
    if "primary_icd_category" in df.columns:
        icd_dummies = pd.get_dummies(df["primary_icd_category"], prefix="icd").astype(int)
        df = pd.concat([df, icd_dummies], axis=1)
    return df


def select_features(df: pd.DataFrame) -> List[str]:
    base = [
        "age", "gender_encoded", "num_diagnoses", "charlson_score",
        "num_prior_admissions", "days_since_last_admission",
        "num_procedures", "has_surgery", "num_medications",
        "drg_severity", "drg_mortality", "insurance_encoded",
        "admission_type_encoded", "had_icu_transfer",
    ]
    icd_flags = [c for c in df.columns if c.startswith("icd_")]
    return [f for f in base + icd_flags if f in df.columns]


# ===================================================================
# 11.  SPLIT & SAVE
# ===================================================================
def split_and_save(df, feature_cols, out_dir):
    from sklearn.model_selection import train_test_split
    out_dir.mkdir(parents=True, exist_ok=True)

    target_cols = ["urgency_category", "urgency_encoded", "adverse_outcome", "long_los",
                   "readmission_30d", "los_days"]
    keep = ["hadm_id"] + feature_cols + [c for c in target_cols if c in df.columns]
    data = df[keep].copy()

    train_df, temp = train_test_split(data, train_size=0.7, stratify=data["urgency_encoded"], random_state=42)
    val_df, test_df = train_test_split(temp, train_size=0.5, stratify=temp["urgency_encoded"], random_state=42)

    train_df.to_parquet(out_dir / "train.parquet", index=False)
    val_df.to_parquet(out_dir / "val.parquet", index=False)
    test_df.to_parquet(out_dir / "test.parquet", index=False)
    logger.info("  Saved: train=%d, val=%d, test=%d", len(train_df), len(val_df), len(test_df))

    metadata = {
        "total_samples": len(data), "feature_columns": feature_cols,
        "urgency_distribution": data["urgency_category"].value_counts().to_dict(),
        "adverse_outcome_rate": float(data["adverse_outcome"].mean()),
    }
    with open(out_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2, default=str)
    return train_df, val_df, test_df


def main() -> None:
    logger.info("Starting Waiting List dataset build ...")
    with MongoManager() as mongo:
        df = extract_elective_cohort(mongo)
        df = attach_demographics(df, mongo)
        df = attach_diagnoses(df, mongo)
        df = attach_prior_history(df)
        df = attach_procedures(df, mongo)
        df = attach_medications(df, mongo)
        df = attach_drg(df, mongo)
        df = attach_icu_flag(df, mongo)

    df = derive_targets(df)
    df = engineer_features(df)
    feature_cols = select_features(df)
    logger.info("Selected %d features", len(feature_cols))

    train_df, val_df, test_df = split_and_save(df, feature_cols, DATASET_OUT_DIR)

    total = len(train_df) + len(val_df) + len(test_df)
    print("\n" + "=" * 60)
    print("WAITING LIST DATASET STATISTICS")
    print("=" * 60)
    print(f"Total: {total:,}  Train: {len(train_df):,}  Val: {len(val_df):,}  Test: {len(test_df):,}")
    print(f"Urgency: {train_df['urgency_category'].value_counts().to_dict()}")
    print(f"Adverse outcome rate: {train_df['adverse_outcome'].mean()*100:.1f}%")
    print(f"Features: {len(feature_cols)}")
    print("=" * 60)

    logger.info("Dataset build complete. Output: %s", DATASET_OUT_DIR)


if __name__ == "__main__":
    main()
