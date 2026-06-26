"""
Bed Management Dataset Builder
===============================
Extracts discharge-prediction and capacity-forecasting datasets from
MIMIC-IV MongoDB.  For each admission a *snapshot* is created at a
random point during the stay (25-75 % of actual LOS) to simulate a
mid-stay prediction task.

Features at the snapshot include: demographics, current department,
transfer count, latest vitals / labs, diagnosis counts, and temporal
context.  Labels are binary discharge-within-24 h / 48 h and the
remaining LOS in hours.

A separate capacity dataset records hourly census per department.

Usage::

    python -m app_08_bed_management.backend.data.build_dataset

Environment variables:
    MONGO_URI          MongoDB connection string
    DATASET_OUT_DIR    Output directory (default: datasets/bed_management)
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
logger = logging.getLogger("bed_management.build_dataset")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VITAL_ITEMIDS = {
    "heart_rate": 220045,
    "respiratory_rate": 220210,
    "spo2": 220277,
    "sbp": 220179,
    "dbp": 220180,
    "temperature": 223761,
    "mbp": 220181,
}

LAB_ITEMIDS = {
    "wbc": 51301,
    "hemoglobin": 51222,
    "platelets": 51265,
    "creatinine": 50912,
    "bun": 51006,
    "sodium": 50983,
    "potassium": 50971,
    "glucose": 50931,
}

VITAL_RANGES = {
    "heart_rate": (20, 250),
    "respiratory_rate": (4, 60),
    "spo2": (50, 100),
    "sbp": (40, 300),
    "dbp": (20, 200),
    "temperature": (32, 42),
    "mbp": (30, 250),
}

LAB_RANGES = {
    "wbc": (0.1, 100),
    "hemoglobin": (2, 22),
    "platelets": (5, 1500),
    "creatinine": (0.1, 30),
    "bun": (1, 200),
    "sodium": (100, 180),
    "potassium": (1.5, 10),
    "glucose": (20, 1000),
}

DATASET_OUT_DIR = Path(
    os.getenv("DATASET_OUT_DIR", "./datasets/bed_management")
)

BATCH = 5000
MAX_ADMISSIONS = 50000  # Sample to keep memory manageable


# ===================================================================
# 1.  EXTRACT ADMISSIONS COHORT
# ===================================================================
def extract_admissions(mongo: MongoManager) -> pd.DataFrame:
    """All admissions with valid admittime AND dischtime (sampled)."""
    logger.info("Fetching admissions with valid admit/disch times ...")
    docs = mongo.fetch_admissions(
        filters={"admittime": {"$ne": None}, "dischtime": {"$ne": None}},
        fields={
            "_id": 0,
            "subject_id": 1,
            "hadm_id": 1,
            "admittime": 1,
            "dischtime": 1,
            "admission_type": 1,
            "discharge_location": 1,
            "hospital_expire_flag": 1,
        },
    )
    df = pd.DataFrame(docs)
    for col in ("admittime", "dischtime"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    df = df.dropna(subset=["admittime", "dischtime", "hadm_id"]).copy()
    df = df[df["dischtime"] > df["admittime"]].copy()
    logger.info("  Valid admissions: %d", len(df))

    # Sample to keep memory manageable
    if len(df) > MAX_ADMISSIONS:
        df = df.sample(MAX_ADMISSIONS, random_state=42).reset_index(drop=True)
        logger.info("  Sampled to %d admissions", len(df))
    return df


# ===================================================================
# 2.  CREATE SNAPSHOT TIMES
# ===================================================================
def create_snapshots(df: pd.DataFrame) -> pd.DataFrame:
    """For each admission create a snapshot at a random point (25-75 % LOS)."""
    logger.info("Creating mid-stay snapshots ...")
    rng = np.random.RandomState(42)
    los_seconds = (df["dischtime"] - df["admittime"]).dt.total_seconds().values
    fractions = rng.uniform(0.25, 0.75, size=len(df))
    offsets = pd.to_timedelta(los_seconds * fractions, unit="s")
    df["snapshot_time"] = df["admittime"] + offsets
    df["days_since_admission"] = (
        (df["snapshot_time"] - df["admittime"]).dt.total_seconds() / 86400
    )
    df["remaining_los_hours"] = (
        (df["dischtime"] - df["snapshot_time"]).dt.total_seconds() / 3600
    ).clip(lower=0, upper=720)
    df["discharge_within_24h"] = (df["remaining_los_hours"] <= 24).astype(int)
    df["discharge_within_48h"] = (df["remaining_los_hours"] <= 48).astype(int)
    logger.info("  discharge_within_24h rate: %.1f%%",
                df["discharge_within_24h"].mean() * 100)
    return df


# ===================================================================
# 3.  DEMOGRAPHICS
# ===================================================================
def attach_demographics(df: pd.DataFrame, mongo: MongoManager) -> pd.DataFrame:
    """Attach age and gender from patients collection."""
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
        admit_year = df["admittime"].dt.year
        df["age"] = df["anchor_age"] + (admit_year - df["anchor_year"])
        df["age"] = df["age"].clip(lower=18, upper=100)
        df.drop(columns=["anchor_age", "anchor_year"], inplace=True, errors="ignore")
    else:
        df["age"] = np.nan
    df["gender_encoded"] = (
        df.get("gender", pd.Series("U", index=df.index))
        .str.upper().str.strip() == "M"
    ).astype(int)
    return df


# ===================================================================
# 4.  TRANSFERS — current department + transfer count at snapshot
# ===================================================================
def attach_transfers(df: pd.DataFrame, mongo: MongoManager) -> pd.DataFrame:
    """For each admission, find the department at snapshot_time and count
    transfers that occurred before the snapshot."""
    logger.info("Fetching transfers ...")
    hadm_ids = df["hadm_id"].dropna().astype(int).unique().tolist()
    all_transfers: List[Dict] = []
    for i in range(0, len(hadm_ids), BATCH):
        batch = hadm_ids[i : i + BATCH]
        all_transfers.extend(list(mongo.mimic["transfers"].find(
            {"hadm_id": {"$in": batch}},
            {"_id": 0, "hadm_id": 1, "careunit": 1, "intime": 1, "outtime": 1},
        )))
        if (i // BATCH) % 5 == 0:
            logger.info("  Transfer fetch: %d/%d hadm_ids", min(i+BATCH, len(hadm_ids)), len(hadm_ids))
    logger.info("  Fetched %d transfer records", len(all_transfers))

    if not all_transfers:
        df["current_careunit"] = "Unknown"
        df["num_transfers"] = 0
        return df

    tr_df = pd.DataFrame(all_transfers)
    tr_df["intime"] = pd.to_datetime(tr_df["intime"], errors="coerce")
    tr_df["outtime"] = pd.to_datetime(tr_df["outtime"], errors="coerce")

    # Vectorized: merge snapshot_time, then filter
    tr_df = tr_df.merge(df[["hadm_id", "snapshot_time"]], on="hadm_id", how="inner")
    # Keep transfers before snapshot
    tr_df = tr_df[tr_df["intime"] <= tr_df["snapshot_time"]].copy()

    # Count transfers per hadm_id
    transfer_counts = tr_df.groupby("hadm_id").size().reset_index(name="num_transfers")

    # Latest careunit per hadm_id (the one with max intime before snapshot)
    tr_df = tr_df.sort_values("intime")
    latest_unit = tr_df.groupby("hadm_id").last().reset_index()[["hadm_id", "careunit"]]
    latest_unit = latest_unit.rename(columns={"careunit": "current_careunit"})

    df = df.merge(transfer_counts, on="hadm_id", how="left")
    df = df.merge(latest_unit, on="hadm_id", how="left")
    df["current_careunit"] = df["current_careunit"].fillna("Unknown")
    df["num_transfers"] = df["num_transfers"].fillna(0).astype(int)
    return df


# ===================================================================
# 5.  LATEST VITALS BEFORE SNAPSHOT
# ===================================================================
def attach_vitals(df: pd.DataFrame, mongo: MongoManager) -> pd.DataFrame:
    """Fetch the latest vital reading before each admission's snapshot."""
    logger.info("Fetching vitals (latest before snapshot) ...")
    hadm_ids = df["hadm_id"].dropna().astype(int).unique().tolist()

    # hadm_id -> stay_id mapping
    icu_docs = list(mongo.mimic_icu["icustays"].find(
        {"hadm_id": {"$in": hadm_ids}},
        {"_id": 0, "hadm_id": 1, "stay_id": 1, "intime": 1},
    ))
    if not icu_docs:
        logger.warning("  No ICU stays found; vital columns will be NaN.")
        for vname in VITAL_ITEMIDS:
            df[vname] = np.nan
            df[f"{vname}_missing"] = 1
        return df

    icu_df = pd.DataFrame(icu_docs)
    icu_df["intime"] = pd.to_datetime(icu_df["intime"], errors="coerce")
    icu_df = icu_df.sort_values("intime").drop_duplicates(subset=["hadm_id"], keep="first")
    stay_ids = icu_df["stay_id"].dropna().astype(int).unique().tolist()

    all_vital_itemids = list(VITAL_ITEMIDS.values())
    vital_records: List[Dict] = []
    for i in range(0, len(stay_ids), BATCH):
        batch = stay_ids[i : i + BATCH]
        vital_records.extend(mongo.fetch_vitals(batch, all_vital_itemids))
    logger.info("  Fetched %d vital records", len(vital_records))

    if not vital_records:
        for vname in VITAL_ITEMIDS:
            df[vname] = np.nan
            df[f"{vname}_missing"] = 1
        return df

    vit_df = pd.DataFrame(vital_records)
    vit_df["charttime"] = pd.to_datetime(vit_df["charttime"], errors="coerce")
    vit_df = vit_df.merge(icu_df[["hadm_id", "stay_id"]], on="stay_id", how="left")
    # Join snapshot time
    vit_df = vit_df.merge(df[["hadm_id", "snapshot_time"]], on="hadm_id", how="left")
    # Keep only readings before snapshot
    vit_df = vit_df[vit_df["charttime"] <= vit_df["snapshot_time"]]

    itemid_to_name = {v: k for k, v in VITAL_ITEMIDS.items()}
    vit_df["vital_name"] = vit_df["itemid"].map(itemid_to_name)
    vit_df = vit_df.dropna(subset=["vital_name"])
    # Take LATEST value before snapshot
    vit_df = vit_df.sort_values("charttime")
    latest_vitals = (
        vit_df.groupby(["hadm_id", "vital_name"])["valuenum"]
        .last()
        .unstack("vital_name")
    )
    for vname in VITAL_ITEMIDS:
        if vname not in latest_vitals.columns:
            latest_vitals[vname] = np.nan
    latest_vitals = latest_vitals.reset_index()

    for vname in VITAL_ITEMIDS:
        latest_vitals[f"{vname}_missing"] = latest_vitals[vname].isna().astype(int)

    df = df.merge(latest_vitals, on="hadm_id", how="left")
    for vname in VITAL_ITEMIDS:
        if f"{vname}_missing" not in df.columns:
            df[f"{vname}_missing"] = 1
        df[f"{vname}_missing"] = df[f"{vname}_missing"].fillna(1).astype(int)
    return df


# ===================================================================
# 6.  LATEST LABS BEFORE SNAPSHOT
# ===================================================================
def attach_labs(df: pd.DataFrame, mongo: MongoManager) -> pd.DataFrame:
    """Fetch the latest lab reading before each admission's snapshot."""
    logger.info("Fetching labs (latest before snapshot) ...")
    hadm_ids = df["hadm_id"].dropna().astype(int).unique().tolist()
    all_lab_itemids = list(LAB_ITEMIDS.values())

    lab_records: List[Dict] = []
    for i in range(0, len(hadm_ids), BATCH):
        batch = hadm_ids[i : i + BATCH]
        lab_records.extend(mongo.fetch_labs(batch, all_lab_itemids))
    logger.info("  Fetched %d lab records", len(lab_records))

    if not lab_records:
        for lname in LAB_ITEMIDS:
            df[lname] = np.nan
            df[f"{lname}_missing"] = 1
        return df

    lab_df = pd.DataFrame(lab_records)
    lab_df["charttime"] = pd.to_datetime(lab_df["charttime"], errors="coerce")
    lab_df = lab_df.merge(df[["hadm_id", "snapshot_time"]], on="hadm_id", how="left")
    lab_df = lab_df[lab_df["charttime"] <= lab_df["snapshot_time"]]

    itemid_to_name = {v: k for k, v in LAB_ITEMIDS.items()}
    lab_df["lab_name"] = lab_df["itemid"].map(itemid_to_name)
    lab_df = lab_df.dropna(subset=["lab_name"])
    lab_df = lab_df.sort_values("charttime")
    latest_labs = (
        lab_df.groupby(["hadm_id", "lab_name"])["valuenum"]
        .last()
        .unstack("lab_name")
    )
    for lname in LAB_ITEMIDS:
        if lname not in latest_labs.columns:
            latest_labs[lname] = np.nan
    latest_labs = latest_labs.reset_index()

    for lname in LAB_ITEMIDS:
        latest_labs[f"{lname}_missing"] = latest_labs[lname].isna().astype(int)

    df = df.merge(latest_labs, on="hadm_id", how="left")
    for lname in LAB_ITEMIDS:
        if f"{lname}_missing" not in df.columns:
            df[f"{lname}_missing"] = 1
        df[f"{lname}_missing"] = df[f"{lname}_missing"].fillna(1).astype(int)
    return df


# ===================================================================
# 7.  DIAGNOSIS & PROCEDURE COUNTS
# ===================================================================
def attach_diagnoses(df: pd.DataFrame, mongo: MongoManager) -> pd.DataFrame:
    """Attach diagnosis count and primary ICD category."""
    logger.info("Fetching diagnoses ...")
    hadm_ids = df["hadm_id"].dropna().astype(int).unique().tolist()
    diag_records: List[Dict] = []
    for i in range(0, len(hadm_ids), BATCH):
        batch = hadm_ids[i : i + BATCH]
        diag_records.extend(list(mongo.mimic["diagnoses_icd"].find(
            {"hadm_id": {"$in": batch}},
            {"_id": 0, "hadm_id": 1, "icd_code": 1, "seq_num": 1},
        )))
    logger.info("  Fetched %d diagnosis records", len(diag_records))

    if not diag_records:
        df["num_diagnoses"] = 0
        df["icd_category"] = "Unknown"
        return df

    diag_df = pd.DataFrame(diag_records)
    # Count per admission
    counts = diag_df.groupby("hadm_id").size().reset_index(name="num_diagnoses")
    # Primary diagnosis (seq_num == 1)
    primary = diag_df[diag_df["seq_num"] == 1].drop_duplicates(subset=["hadm_id"], keep="first")
    primary["icd_category"] = primary["icd_code"].apply(_icd_to_category)

    df = df.merge(counts, on="hadm_id", how="left")
    df = df.merge(primary[["hadm_id", "icd_category"]], on="hadm_id", how="left")
    df["num_diagnoses"] = df["num_diagnoses"].fillna(0).astype(int)
    df["icd_category"] = df["icd_category"].fillna("Unknown")
    return df


def attach_procedures(df: pd.DataFrame, mongo: MongoManager) -> pd.DataFrame:
    """Count procedures per admission."""
    logger.info("Fetching procedures ...")
    hadm_ids = df["hadm_id"].dropna().astype(int).unique().tolist()
    proc_records: List[Dict] = []
    for i in range(0, len(hadm_ids), BATCH):
        batch = hadm_ids[i : i + BATCH]
        proc_records.extend(list(mongo.mimic["procedures_icd"].find(
            {"hadm_id": {"$in": batch}},
            {"_id": 0, "hadm_id": 1},
        )))
    counts = pd.DataFrame(proc_records).groupby("hadm_id").size().reset_index(
        name="num_procedures") if proc_records else pd.DataFrame(columns=["hadm_id", "num_procedures"])
    df = df.merge(counts, on="hadm_id", how="left")
    df["num_procedures"] = df["num_procedures"].fillna(0).astype(int)
    return df


def _icd_to_category(code: Any) -> str:
    """Map ICD-9/10 code to high-level clinical category."""
    if pd.isna(code) or not isinstance(code, str) or len(code) == 0:
        return "Unknown"
    c = code[0].upper()
    mapping = {
        "A": "Infectious", "B": "Infectious", "C": "Neoplasm",
        "D": "Blood", "E": "Endocrine", "F": "Mental",
        "G": "Nervous", "H": "Eye_Ear", "I": "Circulatory",
        "J": "Respiratory", "K": "Digestive", "L": "Skin",
        "M": "Musculoskeletal", "N": "Genitourinary", "O": "Pregnancy",
        "R": "Symptoms", "S": "Injury", "T": "Injury",
        "Z": "Health_Services",
    }
    if c.isdigit():
        num = int(code[:3]) if len(code) >= 3 and code[:3].isdigit() else int(c)
        if num < 140: return "Infectious"
        elif num < 240: return "Neoplasm"
        elif num < 280: return "Endocrine"
        elif num < 290: return "Blood"
        elif num < 320: return "Mental"
        elif num < 390: return "Nervous"
        elif num < 460: return "Circulatory"
        elif num < 520: return "Respiratory"
        elif num < 580: return "Digestive"
        elif num < 630: return "Genitourinary"
        elif num < 680: return "Pregnancy"
        elif num < 740: return "Musculoskeletal"
        elif num < 800: return "Symptoms"
        else: return "Injury"
    return mapping.get(c, "Other")


# ===================================================================
# 8.  FEATURE ENGINEERING
# ===================================================================
def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Clip, impute, encode, derive temporal context features."""
    logger.info("Engineering features ...")

    # Clip vitals
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

    # Temporal context
    df["day_of_week"] = df["snapshot_time"].dt.dayofweek
    df["hour_of_day"] = df["snapshot_time"].dt.hour

    # Encode admission_type
    atype_map = {
        "EW EMER.": 0, "URGENT": 1, "DIRECT EMER.": 0, "AMBULANCE": 0,
        "ELECTIVE": 3, "SURGICAL SAME DAY ADMIT": 2,
        "EU OBSERVATION": 2, "OBSERVATION ADMIT": 2, "DIRECT OBSERVATION": 2,
    }
    df["admission_type_encoded"] = df["admission_type"].map(atype_map).fillna(1).astype(int)

    # Encode careunit (label encode top units)
    top_units = df["current_careunit"].value_counts().head(15).index.tolist()
    unit_map = {u: i + 1 for i, u in enumerate(top_units)}
    df["careunit_encoded"] = df["current_careunit"].map(unit_map).fillna(0).astype(int)

    # ICD dummies
    if "icd_category" in df.columns:
        icd_dummies = pd.get_dummies(df["icd_category"], prefix="icd").astype(int)
        df = pd.concat([df, icd_dummies], axis=1)

    return df


def select_features(df: pd.DataFrame) -> List[str]:
    """Return feature column names for modelling."""
    base = [
        "age", "gender_encoded", "admission_type_encoded",
        "days_since_admission", "careunit_encoded", "num_transfers",
        "heart_rate", "respiratory_rate", "spo2", "sbp", "dbp", "temperature", "mbp",
        "wbc", "hemoglobin", "platelets", "creatinine", "bun", "sodium", "potassium", "glucose",
        "num_diagnoses", "num_procedures",
        "day_of_week", "hour_of_day",
    ]
    missing_flags = [c for c in df.columns if c.endswith("_missing")]
    icd_flags = [c for c in df.columns if c.startswith("icd_") and c != "icd_category"]
    all_features = base + missing_flags + icd_flags
    return [f for f in all_features if f in df.columns]


# ===================================================================
# 9.  CAPACITY DATASET
# ===================================================================
def build_capacity_dataset(mongo: MongoManager, out_dir: Path) -> None:
    """Build hourly census per department from transfers (sampled)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Building capacity (hourly census) dataset ...")
    # Fetch limited transfers for capacity profiling
    docs = list(mongo.mimic["transfers"].find(
        {"careunit": {"$ne": None}},
        {"_id": 0, "hadm_id": 1, "careunit": 1, "intime": 1, "outtime": 1},
    ).limit(200000))
    if not docs:
        logger.warning("  No transfers found.")
        return
    tr = pd.DataFrame(docs)
    tr["intime"] = pd.to_datetime(tr["intime"], errors="coerce")
    tr["outtime"] = pd.to_datetime(tr["outtime"], errors="coerce")
    tr = tr.dropna(subset=["intime", "careunit"]).copy()

    # Sample to reduce memory: take 200K transfers
    if len(tr) > 200_000:
        tr = tr.sample(200_000, random_state=42)

    # Create hourly time range
    min_time = tr["intime"].min().floor("h")
    max_time = tr["outtime"].dropna().max().ceil("h")
    # Limit to 1 year to keep size manageable
    if (max_time - min_time).days > 365:
        min_time = max_time - pd.Timedelta(days=365)
        tr = tr[tr["intime"] >= min_time].copy()

    hours = pd.date_range(min_time, max_time, freq="h")

    # Count concurrent patients per department per hour (sampled)
    # For efficiency, sample every 6th hour
    sample_hours = hours[::6]
    records = []
    for ts in sample_hours:
        mask = (tr["intime"] <= ts)
        mask &= (tr["outtime"].isna() | (tr["outtime"] > ts))
        active = tr[mask]
        for dept, cnt in active["careunit"].value_counts().items():
            records.append({
                "careunit": dept,
                "timestamp": ts,
                "census": cnt,
                "hour_of_day": ts.hour,
                "day_of_week": ts.dayofweek,
            })

    if records:
        cap_df = pd.DataFrame(records)
        cap_df.to_parquet(out_dir / "capacity_hourly.parquet", index=False)
        logger.info("  Capacity dataset: %d rows, %d departments",
                     len(cap_df), cap_df["careunit"].nunique())
    else:
        logger.warning("  No capacity data generated.")


# ===================================================================
# 10.  SPLIT & SAVE
# ===================================================================
def split_and_save(df: pd.DataFrame, feature_cols: List[str], out_dir: Path):
    """Stratified split and save to parquet."""
    from sklearn.model_selection import train_test_split

    out_dir.mkdir(parents=True, exist_ok=True)

    target_cols = [
        "discharge_within_24h", "discharge_within_48h", "remaining_los_hours",
    ]
    keep_cols = ["hadm_id"] + feature_cols + [c for c in target_cols if c in df.columns]
    data = df[keep_cols].copy()

    train_df, temp_df = train_test_split(
        data, train_size=0.7,
        stratify=data["discharge_within_24h"],
        random_state=42,
    )
    relative_val = 0.15 / 0.3
    val_df, test_df = train_test_split(
        temp_df, train_size=relative_val,
        stratify=temp_df["discharge_within_24h"],
        random_state=42,
    )

    train_df.to_parquet(out_dir / "train.parquet", index=False)
    val_df.to_parquet(out_dir / "val.parquet", index=False)
    test_df.to_parquet(out_dir / "test.parquet", index=False)
    logger.info("  Saved: train=%d, val=%d, test=%d",
                len(train_df), len(val_df), len(test_df))

    # Metadata
    metadata = {
        "total_samples": len(data),
        "train_samples": len(train_df),
        "val_samples": len(val_df),
        "test_samples": len(test_df),
        "feature_columns": feature_cols,
        "target_columns": target_cols,
        "discharge_24h_rate": float(data["discharge_within_24h"].mean()),
        "discharge_48h_rate": float(data["discharge_within_48h"].mean()),
        "median_remaining_los_hours": float(data["remaining_los_hours"].median()),
        "mean_remaining_los_hours": float(data["remaining_los_hours"].mean()),
    }
    with open(out_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2, default=str)

    return train_df, val_df, test_df


# ===================================================================
# MAIN
# ===================================================================
def main() -> None:
    logger.info("Starting Bed Management dataset build ...")

    with MongoManager() as mongo:
        df = extract_admissions(mongo)
        df = create_snapshots(df)
        df = attach_demographics(df, mongo)
        df = attach_transfers(df, mongo)
        df = attach_vitals(df, mongo)
        df = attach_labs(df, mongo)
        df = attach_diagnoses(df, mongo)
        df = attach_procedures(df, mongo)

        # Capacity dataset (separate)
        build_capacity_dataset(mongo, DATASET_OUT_DIR)

    df = engineer_features(df)
    feature_cols = select_features(df)
    logger.info("Selected %d features: %s", len(feature_cols), feature_cols)

    train_df, val_df, test_df = split_and_save(df, feature_cols, DATASET_OUT_DIR)

    # Print statistics
    total = len(train_df) + len(val_df) + len(test_df)
    print("\n" + "=" * 60)
    print("BED MANAGEMENT DATASET STATISTICS")
    print("=" * 60)
    print(f"Total samples:  {total:,}")
    print(f"  Train:        {len(train_df):,}")
    print(f"  Validation:   {len(val_df):,}")
    print(f"  Test:         {len(test_df):,}")
    print(f"\nDischarge within 24h rate: {train_df['discharge_within_24h'].mean()*100:.1f}%")
    print(f"Discharge within 48h rate: {train_df['discharge_within_48h'].mean()*100:.1f}%")
    los = train_df["remaining_los_hours"]
    print(f"Remaining LOS (hours): median={los.median():.1f}, mean={los.mean():.1f}")
    print(f"Features: {len(feature_cols)}")
    print("=" * 60)

    logger.info("Dataset build complete.  Output: %s", DATASET_OUT_DIR)


if __name__ == "__main__":
    main()
