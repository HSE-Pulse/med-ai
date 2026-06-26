"""
ED Flow Optimizer Dataset Builder
==================================
Extends the ED Triage cohort (App 01) with flow-specific features:
arrival timing, ED census, lab ordering flags, vitals trends, and
flow outcome labels (disposition, ED LOS, PET breach, LWBS).

Uses the same MIMIC-IV ED cohort (admissions where edregtime != null)
but engineers additional features for patient flow prediction.

Usage::

    python -m app_14_ed_flow.backend.data.build_dataset
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("ed_flow.build_dataset")

# ---------------------------------------------------------------------------
# Constants (same as App 01 + extensions)
# ---------------------------------------------------------------------------
VITAL_ITEMIDS = {
    "heart_rate": 220045, "respiratory_rate": 220210, "spo2": 220277,
    "sbp": 220179, "dbp": 220180, "temperature": 223761,
}
LAB_ITEMIDS = {
    "wbc": 51301, "hemoglobin": 51222, "platelets": 51265,
    "lactate": 50813, "glucose": 50931, "creatinine": 50912,
    "bun": 51006, "troponin": 51003, "sodium": 50983, "potassium": 50971,
}
VITAL_RANGES = {
    "heart_rate": (20, 250), "respiratory_rate": (4, 60), "spo2": (50, 100),
    "sbp": (40, 300), "dbp": (20, 200), "temperature": (32, 42),
}
LAB_RANGES = {
    "wbc": (0.1, 100), "hemoglobin": (2, 22), "platelets": (5, 1500),
    "lactate": (0.1, 30), "glucose": (20, 1000), "creatinine": (0.1, 30),
    "bun": (1, 200), "troponin": (0, 100), "sodium": (100, 180), "potassium": (1.5, 10),
}

DISPOSITION_LABELS = ["admit_to_inpatient", "discharge_home", "transfer", "expired", "lwbs"]

DATASET_OUT_DIR = Path(
    os.getenv("DATASET_OUT_DIR", "./datasets/ed_flow")
)
BATCH = 5000


# ===================================================================
# 1.  EXTRACT ED COHORT
# ===================================================================
def extract_ed_cohort(mongo: MongoManager) -> pd.DataFrame:
    logger.info("Fetching ED admissions ...")
    docs = mongo.fetch_admissions(
        filters={"edregtime": {"$ne": None}},
        fields={
            "_id": 0, "subject_id": 1, "hadm_id": 1, "admittime": 1,
            "dischtime": 1, "edregtime": 1, "edouttime": 1,
            "admission_type": 1, "admission_location": 1,
            "discharge_location": 1, "hospital_expire_flag": 1,
        },
    )
    df = pd.DataFrame(docs)
    for col in ("admittime", "dischtime", "edregtime", "edouttime"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    df = df.dropna(subset=["edregtime", "hadm_id"]).copy()
    logger.info("  ED admissions: %d", len(df))
    return df


# ===================================================================
# 2.  DEMOGRAPHICS (same pattern as App 01)
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
    if "anchor_age" in df.columns and "anchor_year" in df.columns:
        df["age"] = df["anchor_age"] + (df["admittime"].dt.year - df["anchor_year"])
        df["age"] = df["age"].clip(lower=18, upper=100)
        df.drop(columns=["anchor_age", "anchor_year"], inplace=True, errors="ignore")
    else:
        df["age"] = np.nan
    df["gender_encoded"] = (
        df.get("gender", pd.Series("U", index=df.index)).str.upper().str.strip() == "M"
    ).astype(int)
    return df


# ===================================================================
# 3.  VITALS WITHIN 2h OF EDREGTIME (same pattern as App 01)
# ===================================================================
def attach_vitals(df: pd.DataFrame, mongo: MongoManager) -> pd.DataFrame:
    logger.info("Fetching vitals within 2h of edregtime ...")
    hadm_ids = df["hadm_id"].dropna().astype(int).unique().tolist()
    icu_docs = list(mongo.mimic_icu["icustays"].find(
        {"hadm_id": {"$in": hadm_ids}},
        {"_id": 0, "hadm_id": 1, "stay_id": 1, "intime": 1},
    ))
    if not icu_docs:
        for vname in VITAL_ITEMIDS:
            df[vname] = np.nan
            df[f"{vname}_missing"] = 1
        return df

    icu_df = pd.DataFrame(icu_docs)
    icu_df["intime"] = pd.to_datetime(icu_df["intime"], errors="coerce")
    icu_df = icu_df.sort_values("intime").drop_duplicates(subset=["hadm_id"], keep="first")
    stay_ids = icu_df["stay_id"].dropna().astype(int).unique().tolist()

    vital_records: List[Dict] = []
    for i in range(0, len(stay_ids), BATCH):
        vital_records.extend(mongo.fetch_vitals(stay_ids[i:i+BATCH], list(VITAL_ITEMIDS.values())))
    logger.info("  Fetched %d vital records", len(vital_records))

    if not vital_records:
        for vname in VITAL_ITEMIDS:
            df[vname] = np.nan
            df[f"{vname}_missing"] = 1
        return df

    vit_df = pd.DataFrame(vital_records)
    vit_df["charttime"] = pd.to_datetime(vit_df["charttime"], errors="coerce")
    vit_df = vit_df.merge(icu_df[["hadm_id", "stay_id"]], on="stay_id", how="left")
    vit_df = vit_df.merge(df[["hadm_id", "edregtime"]], on="hadm_id", how="left")
    vit_df["hours_since_ed"] = (vit_df["charttime"] - vit_df["edregtime"]).dt.total_seconds() / 3600
    vit_df = vit_df[(vit_df["hours_since_ed"] >= 0) & (vit_df["hours_since_ed"] <= 2)]

    itemid_to_name = {v: k for k, v in VITAL_ITEMIDS.items()}
    vit_df["vital_name"] = vit_df["itemid"].map(itemid_to_name)
    vit_df = vit_df.dropna(subset=["vital_name"])
    vit_df = vit_df.sort_values("charttime")

    # First vitals (for base features)
    first_vitals = vit_df.groupby(["hadm_id", "vital_name"])["valuenum"].first().unstack("vital_name")
    # Last vitals (for trend)
    last_vitals = vit_df.groupby(["hadm_id", "vital_name"])["valuenum"].last().unstack("vital_name")

    for vname in VITAL_ITEMIDS:
        if vname not in first_vitals.columns:
            first_vitals[vname] = np.nan
        if vname not in last_vitals.columns:
            last_vitals[vname] = np.nan
    first_vitals = first_vitals.reset_index()
    last_vitals = last_vitals.reset_index()

    # Missingness flags
    for vname in VITAL_ITEMIDS:
        first_vitals[f"{vname}_missing"] = first_vitals[vname].isna().astype(int)

    df = df.merge(first_vitals, on="hadm_id", how="left")
    for vname in VITAL_ITEMIDS:
        if f"{vname}_missing" not in df.columns:
            df[f"{vname}_missing"] = 1
        df[f"{vname}_missing"] = df[f"{vname}_missing"].fillna(1).astype(int)

    # Compute vitals trends (last - first)
    for vname in ["heart_rate", "spo2", "sbp"]:
        trend_col = f"{vname}_trend"
        first_vals = first_vitals.set_index("hadm_id").get(vname)
        last_vals = last_vitals.set_index("hadm_id").get(vname)
        if first_vals is not None and last_vals is not None:
            trend = (last_vals - first_vals).rename(trend_col).reset_index()
            df = df.merge(trend, on="hadm_id", how="left")
            df[trend_col] = df[trend_col].fillna(0)
        else:
            df[trend_col] = 0

    return df


# ===================================================================
# 4.  LABS WITHIN 2h (same pattern as App 01)
# ===================================================================
def attach_labs(df: pd.DataFrame, mongo: MongoManager) -> pd.DataFrame:
    logger.info("Fetching labs within 2h of edregtime ...")
    hadm_ids = df["hadm_id"].dropna().astype(int).unique().tolist()
    lab_records: List[Dict] = []
    for i in range(0, len(hadm_ids), BATCH):
        lab_records.extend(mongo.fetch_labs(hadm_ids[i:i+BATCH], list(LAB_ITEMIDS.values())))
    logger.info("  Fetched %d lab records", len(lab_records))

    if not lab_records:
        for lname in LAB_ITEMIDS:
            df[lname] = np.nan
            df[f"{lname}_missing"] = 1
        df["has_labs_ordered"] = 0
        df["num_labs_ordered"] = 0
        return df

    lab_df = pd.DataFrame(lab_records)
    lab_df["charttime"] = pd.to_datetime(lab_df["charttime"], errors="coerce")
    lab_df = lab_df.merge(df[["hadm_id", "edregtime"]], on="hadm_id", how="left")
    lab_df["hours_since_ed"] = (lab_df["charttime"] - lab_df["edregtime"]).dt.total_seconds() / 3600
    lab_df_window = lab_df[(lab_df["hours_since_ed"] >= 0) & (lab_df["hours_since_ed"] <= 2)]

    # Lab ordering features (any labs within 2h)
    lab_counts = lab_df_window.groupby("hadm_id")["itemid"].agg(["count", "nunique"]).reset_index()
    lab_counts.columns = ["hadm_id", "num_lab_results", "num_labs_ordered"]
    lab_counts["has_labs_ordered"] = 1
    df = df.merge(lab_counts[["hadm_id", "has_labs_ordered", "num_labs_ordered"]], on="hadm_id", how="left")
    df["has_labs_ordered"] = df["has_labs_ordered"].fillna(0).astype(int)
    df["num_labs_ordered"] = df["num_labs_ordered"].fillna(0).astype(int)

    # First lab values
    itemid_to_name = {v: k for k, v in LAB_ITEMIDS.items()}
    lab_df_window["lab_name"] = lab_df_window["itemid"].map(itemid_to_name)
    lab_df_window = lab_df_window.dropna(subset=["lab_name"])
    lab_df_window = lab_df_window.sort_values("charttime")
    first_labs = lab_df_window.groupby(["hadm_id", "lab_name"])["valuenum"].first().unstack("lab_name")
    for lname in LAB_ITEMIDS:
        if lname not in first_labs.columns:
            first_labs[lname] = np.nan
    first_labs = first_labs.reset_index()
    for lname in LAB_ITEMIDS:
        first_labs[f"{lname}_missing"] = first_labs[lname].isna().astype(int)

    df = df.merge(first_labs, on="hadm_id", how="left")
    for lname in LAB_ITEMIDS:
        if f"{lname}_missing" not in df.columns:
            df[f"{lname}_missing"] = 1
        df[f"{lname}_missing"] = df[f"{lname}_missing"].fillna(1).astype(int)
    return df


# ===================================================================
# 5.  ED CENSUS AT ARRIVAL
# ===================================================================
def attach_ed_census(df: pd.DataFrame, mongo: MongoManager) -> pd.DataFrame:
    """Estimate ED census at arrival using hourly aggregation (fast)."""
    logger.info("Computing ED census at arrival (hourly approximation) ...")
    # Fetch ED transfers
    ed_transfers = list(mongo.mimic["transfers"].find(
        {"careunit": {"$regex": "Emergency", "$options": "i"}},
        {"_id": 0, "intime": 1, "outtime": 1},
    ))
    if not ed_transfers:
        df["ed_census_at_arrival"] = 0
        return df

    et_df = pd.DataFrame(ed_transfers)
    et_df["intime"] = pd.to_datetime(et_df["intime"], errors="coerce")
    et_df["outtime"] = pd.to_datetime(et_df["outtime"], errors="coerce")
    et_df = et_df.dropna(subset=["intime"]).copy()
    logger.info("  ED transfer records: %d", len(et_df))

    # Approximate: compute hourly ED census, then map each arrival to its hour
    # This is O(n_hours) instead of O(n_patients * n_transfers)
    min_time = et_df["intime"].min().floor("h")
    max_time = et_df["outtime"].dropna().max().ceil("h")
    if (max_time - min_time).days > 365 * 3:
        min_time = max_time - pd.Timedelta(days=365 * 3)

    # Sample hours (every hour in the range)
    hours = pd.date_range(min_time, max_time, freq="h")
    et_in = et_df["intime"].values
    et_out = et_df["outtime"].fillna(pd.Timestamp.max).values

    # Compute census for sampled hours (vectorized per hour)
    hourly_census = {}
    for ts in hours:
        ts_np = np.datetime64(ts)
        count = int(((et_in <= ts_np) & (et_out > ts_np)).sum())
        hourly_census[ts] = count

    # Map each patient's arrival to nearest hour census
    df["arrival_hour"] = df["edregtime"].dt.floor("h")
    census_series = df["arrival_hour"].map(hourly_census)
    df["ed_census_at_arrival"] = census_series.fillna(0).astype(int)
    df.drop(columns=["arrival_hour"], inplace=True, errors="ignore")
    logger.info("  ED census: mean=%.1f, max=%d",
                df["ed_census_at_arrival"].mean(), df["ed_census_at_arrival"].max())
    return df


# ===================================================================
# 6.  ICD CATEGORY (reuse App 01 pattern)
# ===================================================================
def attach_icd_category(df: pd.DataFrame, mongo: MongoManager) -> pd.DataFrame:
    logger.info("Fetching ICD categories ...")
    hadm_ids = df["hadm_id"].dropna().astype(int).unique().tolist()
    diag_records: List[Dict] = []
    for i in range(0, len(hadm_ids), BATCH):
        diag_records.extend(list(mongo.mimic["diagnoses_icd"].find(
            {"hadm_id": {"$in": hadm_ids[i:i+BATCH]}, "seq_num": 1},
            {"_id": 0, "hadm_id": 1, "icd_code": 1},
        )))
    if not diag_records:
        df["icd_category"] = "Unknown"
        return df
    diag_df = pd.DataFrame(diag_records)
    diag_df["icd_category"] = diag_df["icd_code"].apply(_icd_to_category)
    df = df.merge(diag_df[["hadm_id", "icd_category"]], on="hadm_id", how="left")
    df["icd_category"] = df["icd_category"].fillna("Unknown")
    return df


def _icd_to_category(code) -> str:
    if pd.isna(code) or not isinstance(code, str) or len(code) == 0:
        return "Unknown"
    c = code[0].upper()
    mapping = {
        "A": "Infectious", "B": "Infectious", "C": "Neoplasm",
        "D": "Blood", "E": "Endocrine", "I": "Circulatory",
        "J": "Respiratory", "K": "Digestive", "M": "Musculoskeletal",
        "N": "Genitourinary", "R": "Symptoms", "S": "Injury", "T": "Injury",
    }
    if c.isdigit():
        num = int(code[:3]) if len(code) >= 3 and code[:3].isdigit() else int(c)
        if num < 140: return "Infectious"
        elif num < 240: return "Neoplasm"
        elif num < 390: return "Nervous"
        elif num < 460: return "Circulatory"
        elif num < 520: return "Respiratory"
        elif num < 580: return "Digestive"
        elif num < 800: return "Symptoms"
        else: return "Injury"
    return mapping.get(c, "Other")


# ===================================================================
# 7.  DERIVE TARGETS
# ===================================================================
def derive_targets(df: pd.DataFrame) -> pd.DataFrame:
    logger.info("Deriving flow targets ...")

    # ED LOS in minutes
    if "edouttime" in df.columns:
        df["ed_los_minutes"] = (
            (df["edouttime"] - df["edregtime"]).dt.total_seconds() / 60
        ).clip(lower=0, upper=1440)
    else:
        df["ed_los_minutes"] = np.nan

    # PET breach (6 hours = 360 minutes)
    df["pet_breach"] = (df["ed_los_minutes"] > 360).astype(int)

    # Disposition
    def _map_disposition(row):
        loc = str(row.get("discharge_location", "")).upper()
        if "LEFT" in loc or "AGAINST" in loc:
            return "lwbs"
        if row.get("hospital_expire_flag") == 1:
            return "expired"
        if any(kw in loc for kw in ("HOME", "SELF", "ASSISTED")):
            return "discharge_home"
        if any(kw in loc for kw in ("TRANSFER", "REHAB", "SNF", "PSYCH", "HOSPICE")):
            return "transfer"
        return "admit_to_inpatient"

    df["disposition"] = df.apply(_map_disposition, axis=1)

    # LWBS flag
    df["lwbs"] = (df["disposition"] == "lwbs").astype(int)

    # Disposition encoded (for stratification)
    disp_map = {d: i for i, d in enumerate(DISPOSITION_LABELS)}
    df["disposition_encoded"] = df["disposition"].map(disp_map).fillna(0).astype(int)

    logger.info("  PET breach rate: %.1f%%", df["pet_breach"].mean() * 100)
    logger.info("  LWBS rate: %.1f%%", df["lwbs"].mean() * 100)
    logger.info("  Disposition dist: %s", df["disposition"].value_counts().to_dict())
    return df


# ===================================================================
# 8.  FEATURE ENGINEERING
# ===================================================================
def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    logger.info("Engineering features ...")

    # Clip
    for col, (lo, hi) in VITAL_RANGES.items():
        if col in df.columns:
            df[col] = df[col].clip(lower=lo, upper=hi)
    for col, (lo, hi) in LAB_RANGES.items():
        if col in df.columns:
            df[col] = df[col].clip(lower=lo, upper=hi)

    # Median impute
    for col in list(VITAL_ITEMIDS.keys()) + list(LAB_ITEMIDS.keys()):
        if col in df.columns:
            df[col] = df[col].fillna(df[col].median())
    if "age" in df.columns:
        df["age"] = df["age"].fillna(df["age"].median())

    # Arrival timing features
    df["hour_of_arrival"] = df["edregtime"].dt.hour
    df["day_of_week"] = df["edregtime"].dt.dayofweek
    df["month"] = df["edregtime"].dt.month
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
    df["is_night"] = ((df["hour_of_arrival"] < 8) | (df["hour_of_arrival"] >= 20)).astype(int)

    # Arrival mode dummies
    arrival_cats = ["EMERGENCY ROOM", "PHYSICIAN REFERRAL", "TRANSFER FROM HOSPITAL", "AMBULANCE"]
    if "admission_location" in df.columns:
        df["arrival_mode"] = df["admission_location"].fillna("UNKNOWN").str.upper()
        for cat in arrival_cats:
            safe = cat.replace(" ", "_").lower()
            df[f"arrival_{safe}"] = df["arrival_mode"].str.contains(
                cat.split("/")[0], case=False, na=False
            ).astype(int)

    # ICD dummies
    if "icd_category" in df.columns:
        icd_dummies = pd.get_dummies(df["icd_category"], prefix="icd").astype(int)
        df = pd.concat([df, icd_dummies], axis=1)

    return df


def select_features(df: pd.DataFrame) -> List[str]:
    base = [
        "age", "gender_encoded",
        "heart_rate", "respiratory_rate", "spo2", "sbp", "dbp", "temperature",
        "wbc", "hemoglobin", "platelets", "lactate", "glucose", "creatinine",
        "bun", "troponin", "sodium", "potassium",
        "hour_of_arrival", "day_of_week", "month", "is_weekend", "is_night",
        "ed_census_at_arrival", "has_labs_ordered", "num_labs_ordered",
        "heart_rate_trend", "spo2_trend", "sbp_trend",
    ]
    missing_flags = [c for c in df.columns if c.endswith("_missing")]
    arrival_flags = [c for c in df.columns if c.startswith("arrival_")]
    icd_flags = [c for c in df.columns if c.startswith("icd_")]
    all_features = base + missing_flags + arrival_flags + icd_flags
    return [f for f in all_features if f in df.columns]


# ===================================================================
# 9.  SPLIT & SAVE
# ===================================================================
def split_and_save(df: pd.DataFrame, feature_cols: List[str], out_dir: Path):
    from sklearn.model_selection import train_test_split
    out_dir.mkdir(parents=True, exist_ok=True)

    target_cols = ["ed_los_minutes", "disposition", "disposition_encoded", "pet_breach", "lwbs"]
    keep_cols = ["hadm_id"] + feature_cols + [c for c in target_cols if c in df.columns]
    data = df[keep_cols].copy()

    # Drop rows without ED LOS
    data = data.dropna(subset=["ed_los_minutes"]).copy()

    train_df, temp_df = train_test_split(
        data, train_size=0.7, stratify=data["disposition_encoded"], random_state=42,
    )
    relative_val = 0.15 / 0.3
    val_df, test_df = train_test_split(
        temp_df, train_size=relative_val, stratify=temp_df["disposition_encoded"], random_state=42,
    )

    train_df.to_parquet(out_dir / "train.parquet", index=False)
    val_df.to_parquet(out_dir / "val.parquet", index=False)
    test_df.to_parquet(out_dir / "test.parquet", index=False)
    logger.info("  Saved: train=%d, val=%d, test=%d", len(train_df), len(val_df), len(test_df))

    metadata = {
        "total_samples": len(data),
        "feature_columns": feature_cols,
        "target_columns": target_cols,
        "disposition_labels": DISPOSITION_LABELS,
        "pet_breach_rate": float(data["pet_breach"].mean()),
        "lwbs_rate": float(data["lwbs"].mean()),
        "disposition_distribution": data["disposition"].value_counts().to_dict(),
        "median_ed_los_minutes": float(data["ed_los_minutes"].median()),
    }
    with open(out_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2, default=str)

    return train_df, val_df, test_df


# ===================================================================
# MAIN
# ===================================================================
def main() -> None:
    logger.info("Starting ED Flow dataset build ...")

    with MongoManager() as mongo:
        df = extract_ed_cohort(mongo)
        df = attach_demographics(df, mongo)
        df = attach_vitals(df, mongo)
        df = attach_labs(df, mongo)
        df = attach_ed_census(df, mongo)
        df = attach_icd_category(df, mongo)

    df = derive_targets(df)
    df = engineer_features(df)
    feature_cols = select_features(df)
    logger.info("Selected %d features", len(feature_cols))

    train_df, val_df, test_df = split_and_save(df, feature_cols, DATASET_OUT_DIR)

    total = len(train_df) + len(val_df) + len(test_df)
    print("\n" + "=" * 60)
    print("ED FLOW OPTIMIZER DATASET STATISTICS")
    print("=" * 60)
    print(f"Total samples:  {total:,}")
    print(f"  Train: {len(train_df):,}  Val: {len(val_df):,}  Test: {len(test_df):,}")
    print(f"PET breach rate: {train_df['pet_breach'].mean()*100:.1f}%")
    print(f"LWBS rate: {train_df['lwbs'].mean()*100:.1f}%")
    print(f"Median ED LOS: {train_df['ed_los_minutes'].median():.0f} min")
    print(f"Disposition: {train_df['disposition'].value_counts().to_dict()}")
    print(f"Features: {len(feature_cols)}")
    print("=" * 60)

    logger.info("Dataset build complete. Output: %s", DATASET_OUT_DIR)


if __name__ == "__main__":
    main()
