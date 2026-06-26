"""
ED Triage Dataset Builder
=========================
Extracts an emergency-department cohort from MIMIC-IV MongoDB collections,
engineers features (demographics, vitals, labs, arrival context), derives
target labels (ESI-equivalent acuity, disposition, ED LOS), and writes
train / val / test Parquet splits.

Usage::

    python -m app_01_ed_triage.backend.data.build_dataset          # from cancer/
    python build_dataset.py                                         # from data/

Environment variables:
    MONGO_URI          MongoDB connection string (default: mongodb://localhost:27017/)
    DATASET_OUT_DIR    Output directory (default: ./datasets/ed_triage)
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Ensure the shared package is importable regardless of working directory
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[4]  # cancer/
sys.path.insert(0, str(_PROJECT_ROOT))

from shared.db.mongo import MongoManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("ed_triage.build_dataset")

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
}

LAB_ITEMIDS = {
    "wbc": 51301,
    "hemoglobin": 51222,
    "platelets": 51265,
    "lactate": 50813,
    "glucose": 50931,
    "creatinine": 50912,
    "bun": 51006,
    "troponin": 51003,
    "sodium": 50983,
    "potassium": 50971,
}

ACUITY_LABELS = {
    1: "Resuscitation",
    2: "Emergent",
    3: "Urgent",
    4: "Less Urgent",
    5: "Non-urgent",
}

DATASET_OUT_DIR = Path(
    os.getenv("DATASET_OUT_DIR", "./datasets/ed_triage")
)


# ===================================================================
# 1.  EXTRACT ED COHORT
# ===================================================================
def extract_ed_cohort(mongo: MongoManager) -> pd.DataFrame:
    """Return admissions where ``edregtime`` is not null (ED visits).

    Fields pulled: subject_id, hadm_id, admittime, dischtime,
    edregtime, edouttime, admission_type, admission_location,
    discharge_location, insurance, marital_status, race,
    hospital_expire_flag.
    """
    logger.info("Fetching ED admissions (edregtime != null) ...")
    docs = mongo.fetch_admissions(
        filters={"edregtime": {"$ne": None}},
        fields={
            "_id": 0,
            "subject_id": 1,
            "hadm_id": 1,
            "admittime": 1,
            "dischtime": 1,
            "edregtime": 1,
            "edouttime": 1,
            "admission_type": 1,
            "admission_location": 1,
            "discharge_location": 1,
            "insurance": 1,
            "marital_status": 1,
            "race": 1,
            "hospital_expire_flag": 1,
        },
    )
    df = pd.DataFrame(docs)
    logger.info("  Raw ED admissions: %d", len(df))

    # Parse dates
    for col in ("admittime", "dischtime", "edregtime", "edouttime"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # Drop rows missing essential timestamps
    df = df.dropna(subset=["edregtime", "hadm_id"]).copy()
    logger.info("  After dropping nulls: %d", len(df))
    return df


# ===================================================================
# 2.  EXTRACT PATIENT DEMOGRAPHICS
# ===================================================================
def attach_demographics(df: pd.DataFrame, mongo: MongoManager) -> pd.DataFrame:
    """Attach age and gender from the patients collection."""
    logger.info("Fetching patient demographics ...")
    subject_ids = df["subject_id"].unique().tolist()

    patients_docs = list(
        mongo.mimic["patients"].find(
            {"subject_id": {"$in": subject_ids}},
            {"_id": 0, "subject_id": 1, "gender": 1, "anchor_age": 1, "anchor_year": 1},
        )
    )
    pat_df = pd.DataFrame(patients_docs)

    if pat_df.empty:
        df["age"] = np.nan
        df["gender"] = "Unknown"
        return df

    df = df.merge(pat_df, on="subject_id", how="left")

    # Approximate age at admission = anchor_age + (admit_year - anchor_year)
    if "anchor_age" in df.columns and "anchor_year" in df.columns:
        admit_year = df["admittime"].dt.year
        df["age"] = df["anchor_age"] + (admit_year - df["anchor_year"])
        df["age"] = df["age"].clip(lower=18, upper=100)
        df.drop(columns=["anchor_age", "anchor_year"], inplace=True, errors="ignore")
    else:
        df["age"] = np.nan

    df["gender"] = df.get("gender", pd.Series("Unknown", index=df.index))
    return df


# ===================================================================
# 3.  EXTRACT FIRST VITALS WITHIN 2 h OF ED REGISTRATION
# ===================================================================
def attach_vitals(df: pd.DataFrame, mongo: MongoManager) -> pd.DataFrame:
    """Join first-available vital signs within 2 hours of edregtime.

    Vitals come from MIMIC_ICU.chartevents.  We first need to map
    hadm_id -> stay_id via MIMIC_ICU.icustays, then query chartevents.
    For patients who never reach ICU, vitals may be absent (handled later).
    """
    logger.info("Fetching vital signs ...")
    hadm_ids = df["hadm_id"].dropna().astype(int).unique().tolist()

    # hadm_id -> stay_id mapping
    icu_docs = list(
        mongo.mimic_icu["icustays"].find(
            {"hadm_id": {"$in": hadm_ids}},
            {"_id": 0, "hadm_id": 1, "stay_id": 1, "intime": 1},
        )
    )

    if not icu_docs:
        logger.warning("  No ICU stays found; vital columns will be NaN.")
        for vname in VITAL_ITEMIDS:
            df[vname] = np.nan
            df[f"{vname}_missing"] = 1
        return df

    icu_df = pd.DataFrame(icu_docs)
    # Keep earliest ICU stay per admission
    icu_df["intime"] = pd.to_datetime(icu_df["intime"], errors="coerce")
    icu_df = icu_df.sort_values("intime").drop_duplicates(subset=["hadm_id"], keep="first")

    stay_ids = icu_df["stay_id"].dropna().astype(int).unique().tolist()
    all_vital_itemids = list(VITAL_ITEMIDS.values())

    # Fetch in batches to avoid memory blow-up
    BATCH = 5000
    vital_records: List[Dict[str, Any]] = []
    for i in range(0, len(stay_ids), BATCH):
        batch = stay_ids[i : i + BATCH]
        vital_records.extend(mongo.fetch_vitals(batch, all_vital_itemids))
    logger.info("  Fetched %d vital-sign records", len(vital_records))

    if not vital_records:
        for vname in VITAL_ITEMIDS:
            df[vname] = np.nan
            df[f"{vname}_missing"] = 1
        return df

    vit_df = pd.DataFrame(vital_records)
    vit_df["charttime"] = pd.to_datetime(vit_df["charttime"], errors="coerce")

    # Map stay_id back to hadm_id
    vit_df = vit_df.merge(icu_df[["hadm_id", "stay_id"]], on="stay_id", how="left")

    # Join edregtime for time windowing
    vit_df = vit_df.merge(df[["hadm_id", "edregtime"]], on="hadm_id", how="left")

    # Keep only vitals within 2 hours of edregtime
    vit_df["hours_since_ed"] = (
        vit_df["charttime"] - vit_df["edregtime"]
    ).dt.total_seconds() / 3600.0
    vit_df = vit_df[(vit_df["hours_since_ed"] >= 0) & (vit_df["hours_since_ed"] <= 2)]

    # Pivot: take first (earliest) value per hadm_id per item
    itemid_to_name = {v: k for k, v in VITAL_ITEMIDS.items()}
    vit_df["vital_name"] = vit_df["itemid"].map(itemid_to_name)
    vit_df = vit_df.dropna(subset=["vital_name"])
    vit_df = vit_df.sort_values("charttime")
    first_vitals = (
        vit_df.groupby(["hadm_id", "vital_name"])["valuenum"]
        .first()
        .unstack("vital_name")
    )

    for vname in VITAL_ITEMIDS:
        if vname not in first_vitals.columns:
            first_vitals[vname] = np.nan

    first_vitals = first_vitals.reset_index()

    # Missingness flags
    for vname in VITAL_ITEMIDS:
        first_vitals[f"{vname}_missing"] = first_vitals[vname].isna().astype(int)

    df = df.merge(first_vitals, on="hadm_id", how="left")

    # Fill missingness flags for unmatched rows
    for vname in VITAL_ITEMIDS:
        if f"{vname}_missing" not in df.columns:
            df[f"{vname}_missing"] = 1
        df[f"{vname}_missing"] = df[f"{vname}_missing"].fillna(1).astype(int)

    return df


# ===================================================================
# 4.  EXTRACT FIRST LABS WITHIN 2 h OF ED REGISTRATION
# ===================================================================
def attach_labs(df: pd.DataFrame, mongo: MongoManager) -> pd.DataFrame:
    """Join first-available lab results within 2 hours of edregtime."""
    logger.info("Fetching lab results ...")
    hadm_ids = df["hadm_id"].dropna().astype(int).unique().tolist()
    all_lab_itemids = list(LAB_ITEMIDS.values())

    BATCH = 5000
    lab_records: List[Dict[str, Any]] = []
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
    lab_df = lab_df.merge(df[["hadm_id", "edregtime"]], on="hadm_id", how="left")

    lab_df["hours_since_ed"] = (
        lab_df["charttime"] - lab_df["edregtime"]
    ).dt.total_seconds() / 3600.0
    lab_df = lab_df[(lab_df["hours_since_ed"] >= 0) & (lab_df["hours_since_ed"] <= 2)]

    itemid_to_name = {v: k for k, v in LAB_ITEMIDS.items()}
    lab_df["lab_name"] = lab_df["itemid"].map(itemid_to_name)
    lab_df = lab_df.dropna(subset=["lab_name"])
    lab_df = lab_df.sort_values("charttime")

    first_labs = (
        lab_df.groupby(["hadm_id", "lab_name"])["valuenum"]
        .first()
        .unstack("lab_name")
    )

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
# 5.  ATTACH PRIMARY ICD CODE CATEGORY (chief-complaint proxy)
# ===================================================================
def attach_icd_category(df: pd.DataFrame, mongo: MongoManager) -> pd.DataFrame:
    """Map the primary ICD code to a high-level category for each admission."""
    logger.info("Fetching primary ICD codes ...")
    hadm_ids = df["hadm_id"].dropna().astype(int).unique().tolist()

    BATCH = 5000
    diag_records: List[Dict[str, Any]] = []
    for i in range(0, len(hadm_ids), BATCH):
        batch = hadm_ids[i : i + BATCH]
        diag_records.extend(
            list(
                mongo.mimic["diagnoses_icd"].find(
                    {"hadm_id": {"$in": batch}, "seq_num": 1},
                    {"_id": 0, "hadm_id": 1, "icd_code": 1, "icd_version": 1},
                )
            )
        )
    logger.info("  Fetched %d primary diagnosis records", len(diag_records))

    if not diag_records:
        df["icd_category"] = "Unknown"
        return df

    diag_df = pd.DataFrame(diag_records)
    diag_df["icd_category"] = diag_df["icd_code"].apply(_icd_to_category)
    df = df.merge(diag_df[["hadm_id", "icd_category"]], on="hadm_id", how="left")
    df["icd_category"] = df["icd_category"].fillna("Unknown")
    return df


def _icd_to_category(code: Any) -> str:
    """Map an ICD-9/10 code to a high-level clinical category.

    Uses the first character(s) to bucket into broad groups.
    """
    if pd.isna(code) or not isinstance(code, str) or len(code) == 0:
        return "Unknown"

    c = code[0].upper()
    # ICD-10 first-character mapping
    mapping = {
        "A": "Infectious",
        "B": "Infectious",
        "C": "Neoplasm",
        "D": "Blood/Neoplasm",
        "E": "Endocrine",
        "F": "Mental",
        "G": "Nervous",
        "H": "Eye/Ear",
        "I": "Circulatory",
        "J": "Respiratory",
        "K": "Digestive",
        "L": "Skin",
        "M": "Musculoskeletal",
        "N": "Genitourinary",
        "O": "Pregnancy",
        "P": "Perinatal",
        "Q": "Congenital",
        "R": "Symptoms",
        "S": "Injury",
        "T": "Injury",
        "V": "External",
        "W": "External",
        "X": "External",
        "Y": "External",
        "Z": "Health_Services",
    }
    # ICD-9 codes start with digits
    if c.isdigit():
        num = int(code[:3]) if len(code) >= 3 and code[:3].isdigit() else int(c)
        if num < 140:
            return "Infectious"
        elif num < 240:
            return "Neoplasm"
        elif num < 280:
            return "Endocrine"
        elif num < 290:
            return "Blood"
        elif num < 320:
            return "Mental"
        elif num < 390:
            return "Nervous"
        elif num < 460:
            return "Circulatory"
        elif num < 520:
            return "Respiratory"
        elif num < 580:
            return "Digestive"
        elif num < 630:
            return "Genitourinary"
        elif num < 680:
            return "Pregnancy"
        elif num < 710:
            return "Skin"
        elif num < 740:
            return "Musculoskeletal"
        elif num < 760:
            return "Congenital"
        elif num < 780:
            return "Perinatal"
        elif num < 800:
            return "Symptoms"
        else:
            return "Injury"

    return mapping.get(c, "Other")


# ===================================================================
# 6.  DERIVE TARGET LABELS
# ===================================================================
def derive_targets(df: pd.DataFrame) -> pd.DataFrame:
    """Create the three prediction targets.

    * **acuity_level** (1--5): derived from admission_type severity.
    * **disposition**: admit_to_inpatient | discharge_home | transfer | expired.
    * **ed_los_hours**: (edouttime - edregtime) in decimal hours.
    """
    logger.info("Deriving target labels ...")

    # --- acuity_level --------------------------------------------------------
    # Map admission_type to rough ESI-equivalent acuity
    acuity_map: Dict[str, int] = {
        # MIMIC-IV admission_type values
        "EW EMER.": 2,
        "URGENT": 3,
        "DIRECT EMER.": 2,
        "AMBULANCE": 2,
        "EU OBSERVATION": 4,
        "OBSERVATION ADMIT": 4,
        "ELECTIVE": 5,
        "SURGICAL SAME DAY ADMIT": 4,
        "DIRECT OBSERVATION": 4,
    }

    df["acuity_level"] = df["admission_type"].map(acuity_map).fillna(3).astype(int)

    # Upgrade acuity for patients who expired
    expired_mask = df.get("hospital_expire_flag", pd.Series(0, index=df.index)) == 1
    df.loc[expired_mask & (df["acuity_level"] > 1), "acuity_level"] = 1

    # Upgrade acuity based on critical vitals (clinical heuristics)
    if "heart_rate" in df.columns:
        critical_hr = (df["heart_rate"] > 130) | (df["heart_rate"] < 40)
        df.loc[critical_hr & (df["acuity_level"] > 2), "acuity_level"] = 2

    if "spo2" in df.columns:
        critical_spo2 = df["spo2"] < 90
        df.loc[critical_spo2 & (df["acuity_level"] > 1), "acuity_level"] = 1

    if "sbp" in df.columns:
        critical_sbp = df["sbp"] < 80
        df.loc[critical_sbp & (df["acuity_level"] > 1), "acuity_level"] = 1

    if "lactate" in df.columns:
        critical_lactate = df["lactate"] > 4.0
        df.loc[critical_lactate & (df["acuity_level"] > 2), "acuity_level"] = 2

    df["acuity_label"] = df["acuity_level"].map(ACUITY_LABELS)

    # --- disposition ---------------------------------------------------------
    def _map_disposition(row: pd.Series) -> str:
        if row.get("hospital_expire_flag") == 1:
            return "expired"
        loc = str(row.get("discharge_location", "")).upper()
        if any(kw in loc for kw in ("HOME", "SELF", "ASSISTED")):
            return "discharge_home"
        if any(kw in loc for kw in ("TRANSFER", "REHAB", "SNF", "PSYCH", "HOSPICE")):
            return "transfer"
        return "admit_to_inpatient"

    df["disposition"] = df.apply(_map_disposition, axis=1)

    # --- ED length of stay ---------------------------------------------------
    if "edouttime" in df.columns:
        ed_los = (df["edouttime"] - df["edregtime"]).dt.total_seconds() / 3600.0
        df["ed_los_hours"] = ed_los.clip(lower=0, upper=72)
    else:
        df["ed_los_hours"] = np.nan

    return df


# ===================================================================
# 7.  FEATURE ENGINEERING & IMPUTATION
# ===================================================================
# Physiologically reasonable ranges for clipping
VITAL_RANGES = {
    "heart_rate": (20, 250),
    "respiratory_rate": (4, 60),
    "spo2": (50, 100),
    "sbp": (40, 300),
    "dbp": (20, 200),
    "temperature": (32, 42),
}

LAB_RANGES = {
    "wbc": (0.1, 100),
    "hemoglobin": (2, 22),
    "platelets": (5, 1500),
    "lactate": (0.1, 30),
    "glucose": (20, 1000),
    "creatinine": (0.1, 30),
    "bun": (1, 200),
    "troponin": (0, 100),
    "sodium": (100, 180),
    "potassium": (1.5, 10),
}


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Clip outliers, impute missing, encode categoricals, select final feature set."""
    logger.info("Engineering features ...")

    # Clip vital signs to physiological ranges
    for col, (lo, hi) in VITAL_RANGES.items():
        if col in df.columns:
            df[col] = df[col].clip(lower=lo, upper=hi)

    for col, (lo, hi) in LAB_RANGES.items():
        if col in df.columns:
            df[col] = df[col].clip(lower=lo, upper=hi)

    # Forward-fill vitals (within same patient if sorted by time)
    vital_cols = list(VITAL_ITEMIDS.keys())
    for col in vital_cols:
        if col in df.columns:
            df[col] = df[col].fillna(df[col].median())

    # Median-impute labs
    lab_cols = list(LAB_ITEMIDS.keys())
    for col in lab_cols:
        if col in df.columns:
            df[col] = df[col].fillna(df[col].median())

    # Encode gender: M=1, F=0
    if "gender" in df.columns:
        df["gender_encoded"] = (df["gender"].str.upper().str.strip() == "M").astype(int)
    else:
        df["gender_encoded"] = 0

    # Encode arrival mode from admission_location
    arrival_categories = [
        "EMERGENCY ROOM",
        "PHYSICIAN REFERRAL",
        "TRANSFER FROM HOSPITAL",
        "WALK-IN/CLINIC REFERRAL",
        "AMBULANCE",
    ]
    if "admission_location" in df.columns:
        df["arrival_mode"] = df["admission_location"].fillna("UNKNOWN").str.upper()
        for cat in arrival_categories:
            safe_cat = cat.replace(" ", "_").replace("/", "_").replace("-", "_").lower()
            df[f"arrival_{safe_cat}"] = df["arrival_mode"].str.contains(
                cat.split("/")[0], case=False, na=False
            ).astype(int)
    else:
        for cat in arrival_categories:
            safe_cat = cat.replace(" ", "_").replace("/", "_").replace("-", "_").lower()
            df[f"arrival_{safe_cat}"] = 0

    # Encode ICD category
    if "icd_category" in df.columns:
        icd_dummies = pd.get_dummies(df["icd_category"], prefix="icd").astype(int)
        df = pd.concat([df, icd_dummies], axis=1)

    # Age imputation
    if "age" in df.columns:
        df["age"] = df["age"].fillna(df["age"].median())

    return df


def select_features(df: pd.DataFrame) -> List[str]:
    """Return the list of feature column names for modelling."""
    base_features = [
        "age",
        "gender_encoded",
        # Vitals
        "heart_rate",
        "respiratory_rate",
        "spo2",
        "sbp",
        "dbp",
        "temperature",
        # Labs
        "wbc",
        "hemoglobin",
        "platelets",
        "lactate",
        "glucose",
        "creatinine",
        "bun",
        "troponin",
        "sodium",
        "potassium",
    ]

    # Missingness indicators
    missing_flags = [c for c in df.columns if c.endswith("_missing")]

    # Arrival dummies
    arrival_flags = [c for c in df.columns if c.startswith("arrival_")]

    # ICD dummies
    icd_flags = [c for c in df.columns if c.startswith("icd_")]

    all_features = base_features + missing_flags + arrival_flags + icd_flags
    return [f for f in all_features if f in df.columns]


# ===================================================================
# 8.  SPLIT & SAVE
# ===================================================================
def split_and_save(
    df: pd.DataFrame,
    feature_cols: List[str],
    out_dir: Path,
    train_frac: float = 0.7,
    val_frac: float = 0.15,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Stratified split by acuity_level and save to Parquet."""
    logger.info("Splitting dataset ...")
    out_dir.mkdir(parents=True, exist_ok=True)

    target_cols = ["acuity_level", "acuity_label", "disposition", "ed_los_hours"]
    keep_cols = feature_cols + [c for c in target_cols if c in df.columns]
    # Also keep hadm_id for traceability
    if "hadm_id" in df.columns:
        keep_cols = ["hadm_id"] + keep_cols
    data = df[keep_cols].copy()

    # Stratified split on acuity_level
    from sklearn.model_selection import train_test_split

    train_df, temp_df = train_test_split(
        data,
        train_size=train_frac,
        stratify=data["acuity_level"],
        random_state=42,
    )
    relative_val = val_frac / (1 - train_frac)
    val_df, test_df = train_test_split(
        temp_df,
        train_size=relative_val,
        stratify=temp_df["acuity_level"],
        random_state=42,
    )

    train_df.to_parquet(out_dir / "train.parquet", index=False)
    val_df.to_parquet(out_dir / "val.parquet", index=False)
    test_df.to_parquet(out_dir / "test.parquet", index=False)
    logger.info(
        "  Saved: train=%d, val=%d, test=%d", len(train_df), len(val_df), len(test_df)
    )

    return train_df, val_df, test_df


# ===================================================================
# 9.  STATISTICS
# ===================================================================
def print_statistics(
    train_df: pd.DataFrame, val_df: pd.DataFrame, test_df: pd.DataFrame
) -> None:
    """Print summary statistics about the dataset."""
    total = len(train_df) + len(val_df) + len(test_df)
    print("\n" + "=" * 60)
    print("ED TRIAGE DATASET STATISTICS")
    print("=" * 60)
    print(f"Total samples:  {total:,}")
    print(f"  Train:        {len(train_df):,} ({len(train_df)/total*100:.1f}%)")
    print(f"  Validation:   {len(val_df):,} ({len(val_df)/total*100:.1f}%)")
    print(f"  Test:         {len(test_df):,} ({len(test_df)/total*100:.1f}%)")
    print()

    print("Acuity Level Distribution (Train):")
    dist = train_df["acuity_level"].value_counts().sort_index()
    for level, count in dist.items():
        label = ACUITY_LABELS.get(level, "?")
        print(f"  ESI {level} ({label:15s}): {count:6,} ({count/len(train_df)*100:.1f}%)")
    print()

    if "disposition" in train_df.columns:
        print("Disposition Distribution (Train):")
        disp = train_df["disposition"].value_counts()
        for d, count in disp.items():
            print(f"  {d:25s}: {count:6,} ({count/len(train_df)*100:.1f}%)")
        print()

    if "ed_los_hours" in train_df.columns:
        los = train_df["ed_los_hours"].dropna()
        print(f"ED Length of Stay (hours):  median={los.median():.1f}, "
              f"mean={los.mean():.1f}, p90={los.quantile(0.9):.1f}")

    # Feature missingness
    print("\nFeature Missingness (Train):")
    missing_cols = [c for c in train_df.columns if c.endswith("_missing")]
    for col in sorted(missing_cols):
        rate = train_df[col].mean() * 100
        print(f"  {col:30s}: {rate:5.1f}%")

    print("=" * 60)


# ===================================================================
# MAIN
# ===================================================================
def main() -> None:
    """End-to-end dataset construction pipeline."""
    logger.info("Starting ED Triage dataset build ...")

    with MongoManager() as mongo:
        # 1. Extract ED cohort
        df = extract_ed_cohort(mongo)

        # 2. Demographics
        df = attach_demographics(df, mongo)

        # 3. Vitals
        df = attach_vitals(df, mongo)

        # 4. Labs
        df = attach_labs(df, mongo)

        # 5. ICD category
        df = attach_icd_category(df, mongo)

    # 6. Targets
    df = derive_targets(df)

    # 7. Feature engineering
    df = engineer_features(df)

    # 8. Select features
    feature_cols = select_features(df)
    logger.info("Selected %d features: %s", len(feature_cols), feature_cols)

    # 9. Split & save
    train_df, val_df, test_df = split_and_save(df, feature_cols, DATASET_OUT_DIR)

    # 10. Statistics
    print_statistics(train_df, val_df, test_df)

    logger.info("Dataset build complete.  Output: %s", DATASET_OUT_DIR)


if __name__ == "__main__":
    main()
