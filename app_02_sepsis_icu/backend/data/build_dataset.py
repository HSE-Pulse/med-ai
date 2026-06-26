"""Sepsis & ICU Deterioration dataset builder.

Extracts ICU cohort from MongoDB (MIMIC-IV), computes hourly SOFA scores,
labels sepsis onset using Sepsis-3 criteria, and produces time-series
windows suitable for sequential and tabular models.

Output
------
Saves train.npz / val.npz / test.npz to ``DATASET_DIR`` containing:
- X_seq   : (N, 6, n_features) float32  -- 6-hour lookback windows
- X_flat  : (N, n_flat_features) float32 -- statistical aggregations
- y       : (N,) int8                    -- 1 = sepsis onset within 4h
- stay_ids: (N,) int64                   -- originating ICU stay
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Allow imports from the shared package two levels up
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[4]  # .
sys.path.insert(0, str(PROJECT_ROOT))

from shared.db.mongo import MongoManager
from shared.db.queries import sepsis_lab_items, vital_sign_items

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATASET_DIR = PROJECT_ROOT / "datasets" / "sepsis_icu"

# Item IDs -- vitals (chartevents)
VITAL_IDS: Dict[str, int] = {
    "HR": 220045,
    "RR": 220210,
    "SpO2": 220277,
    "SBP": 220179,
    "DBP": 220180,
    "Temp": 223761,
    "MBP": 220181,
}

# Item IDs -- labs (labevents)
LAB_IDS: Dict[str, int] = {
    "WBC": 51301,
    "Lactate": 50813,
    "Creatinine": 50912,
    "Platelets": 51265,
    "Bilirubin": 50885,
}

# Sepsis ICD codes
SEPSIS_ICD10 = {"A40", "A400", "A401", "A403", "A408", "A409",
                "A41", "A410", "A411", "A412", "A413", "A414",
                "A4101", "A4102", "A411", "A4150", "A4151", "A4152",
                "A4153", "A4159", "A418", "A4189", "A419"}
SEPSIS_ICD9 = {"99591", "99592", "78552"}

# Feature columns that appear per timestep
TIMESTEP_FEATURES = [
    "HR", "RR", "SpO2", "SBP", "DBP", "Temp", "MBP",
    "WBC", "Lactate", "Creatinine", "Platelets", "Bilirubin",
    "SOFA_resp", "SOFA_coag", "SOFA_liver", "SOFA_cardio", "SOFA_renal",
    "SOFA_total", "delta_SOFA",
]

STATIC_FEATURES = ["age", "gender", "careunit_encoded"]

LOOKBACK_HOURS = 6
PREDICTION_HORIZON_HOURS = 4

MAX_STAYS = 10_000          # cap to avoid loading all 314M chartevents
BATCH_SIZE = 500            # query stays in batches


# ============================================================================
# SOFA helpers
# ============================================================================

def sofa_respiration(spo2: float) -> int:
    """SpO2-based proxy for respiratory SOFA component."""
    if pd.isna(spo2):
        return 0
    if spo2 < 92:
        return 3
    if spo2 <= 96:
        return 1
    return 0


def sofa_coagulation(platelets: float) -> int:
    if pd.isna(platelets):
        return 0
    if platelets < 20:
        return 4
    if platelets < 50:
        return 3
    if platelets < 100:
        return 2
    if platelets < 150:
        return 1
    return 0


def sofa_liver(bilirubin: float) -> int:
    if pd.isna(bilirubin):
        return 0
    if bilirubin > 12:
        return 4
    if bilirubin >= 6:
        return 3
    if bilirubin >= 2:
        return 2
    if bilirubin >= 1.2:
        return 1
    return 0


def sofa_cardiovascular(mbp: float) -> int:
    """MAP-based proxy (vasopressor data would improve this)."""
    if pd.isna(mbp):
        return 0
    if mbp < 70:
        return 1
    return 0


def sofa_renal(creatinine: float) -> int:
    if pd.isna(creatinine):
        return 0
    if creatinine > 5.0:
        return 4
    if creatinine >= 3.5:
        return 3
    if creatinine >= 2.0:
        return 2
    if creatinine >= 1.2:
        return 1
    return 0


def compute_sofa_row(row: pd.Series) -> pd.Series:
    """Compute all SOFA component columns for a single row."""
    resp = sofa_respiration(row.get("SpO2"))
    coag = sofa_coagulation(row.get("Platelets"))
    liver = sofa_liver(row.get("Bilirubin"))
    cardio = sofa_cardiovascular(row.get("MBP"))
    renal = sofa_renal(row.get("Creatinine"))
    total = resp + coag + liver + cardio + renal
    return pd.Series({
        "SOFA_resp": resp,
        "SOFA_coag": coag,
        "SOFA_liver": liver,
        "SOFA_cardio": cardio,
        "SOFA_renal": renal,
        "SOFA_total": total,
    })


# ============================================================================
# Data extraction helpers
# ============================================================================

def fetch_icu_cohort(
    mongo: MongoManager,
    max_stays: int = MAX_STAYS,
) -> pd.DataFrame:
    """Return a DataFrame of ICU stays joined with admission demographics.

    Limits to ``max_stays`` to keep data volume manageable.
    """
    logger.info("Fetching ICU cohort (limit=%d) ...", max_stays)

    stays = list(
        mongo.mimic_icu["icustays"]
        .find({}, {"_id": 0})
        .sort("stay_id", 1)
        .limit(max_stays)
    )
    df_stays = pd.DataFrame(stays)
    logger.info("  ICU stays fetched: %d", len(df_stays))

    # Fetch matching admissions for demographics + mortality label
    hadm_ids = df_stays["hadm_id"].unique().tolist()
    admissions = list(
        mongo.mimic["admissions"].find(
            {"hadm_id": {"$in": hadm_ids}},
            {
                "_id": 0,
                "hadm_id": 1,
                "subject_id": 1,
                "admittime": 1,
                "hospital_expire_flag": 1,
                "admission_type": 1,
                "insurance": 1,
                "race": 1,
            },
        )
    )
    df_adm = pd.DataFrame(admissions)
    logger.info("  Admissions fetched: %d", len(df_adm))

    # Fetch patient demographics for age / gender
    subject_ids = df_stays["subject_id"].unique().tolist()
    patients = list(
        mongo.mimic["patients"].find(
            {"subject_id": {"$in": subject_ids}},
            {"_id": 0, "subject_id": 1, "gender": 1, "anchor_age": 1},
        )
    )
    df_pat = pd.DataFrame(patients) if patients else pd.DataFrame(
        columns=["subject_id", "gender", "anchor_age"]
    )

    # Join
    df = df_stays.merge(df_adm, on=["hadm_id"], suffixes=("", "_adm"), how="left")
    if "subject_id_adm" in df.columns:
        df.drop(columns=["subject_id_adm"], inplace=True)
    df = df.merge(df_pat, on="subject_id", how="left")

    # Derive age (anchor_age is age at anchor_year in MIMIC-IV)
    if "anchor_age" in df.columns:
        df["age"] = df["anchor_age"].fillna(65).astype(float)
    else:
        df["age"] = 65.0

    # Encode gender
    df["gender"] = df.get("gender", pd.Series(["M"] * len(df)))
    df["gender_encoded"] = (df["gender"] == "M").astype(int)

    return df


def fetch_sepsis_flags(
    mongo: MongoManager,
    hadm_ids: Sequence[int],
) -> set:
    """Return the set of hadm_ids that have a sepsis-related ICD code."""
    logger.info("Checking sepsis ICD codes for %d admissions ...", len(hadm_ids))

    all_codes = list(SEPSIS_ICD10) + list(SEPSIS_ICD9)

    diags = list(
        mongo.mimic["diagnoses_icd"].find(
            {
                "hadm_id": {"$in": list(hadm_ids)},
                "icd_code": {"$in": all_codes},
            },
            {"_id": 0, "hadm_id": 1},
        )
    )
    flagged = {d["hadm_id"] for d in diags}
    logger.info("  Sepsis-flagged admissions: %d", len(flagged))
    return flagged


def _parse_charttime(val: Any) -> Optional[datetime]:
    """Robustly parse charttime from MongoDB (may be datetime or string)."""
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        for fmt in (
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f",
            "%d-%m-%Y %H:%M", "%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M",
            "%Y-%m-%d %H:%M", "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(val, fmt)
            except ValueError:
                continue
    return None


def fetch_vitals_batch(
    mongo: MongoManager,
    stay_ids: List[int],
) -> pd.DataFrame:
    """Fetch chartevents for a batch of stay_ids with targeted item IDs."""
    item_ids = list(VITAL_IDS.values())
    cursor = mongo.mimic_icu["chartevents"].find(
        {"stay_id": {"$in": stay_ids}, "itemid": {"$in": item_ids}},
        {"_id": 0, "stay_id": 1, "charttime": 1, "itemid": 1, "valuenum": 1},
    )
    records = list(cursor)
    if not records:
        return pd.DataFrame(columns=["stay_id", "charttime", "itemid", "valuenum"])
    df = pd.DataFrame(records)
    df["charttime"] = df["charttime"].apply(_parse_charttime)
    df.dropna(subset=["charttime", "valuenum"], inplace=True)
    return df


def fetch_labs_batch(
    mongo: MongoManager,
    hadm_ids: List[int],
) -> pd.DataFrame:
    """Fetch labevents for a batch of hadm_ids with targeted item IDs."""
    item_ids = list(LAB_IDS.values())
    cursor = mongo.mimic["labevents"].find(
        {"hadm_id": {"$in": hadm_ids}, "itemid": {"$in": item_ids}},
        {"_id": 0, "hadm_id": 1, "charttime": 1, "itemid": 1, "valuenum": 1},
    )
    records = list(cursor)
    if not records:
        return pd.DataFrame(columns=["hadm_id", "charttime", "itemid", "valuenum"])
    df = pd.DataFrame(records)
    df["charttime"] = df["charttime"].apply(_parse_charttime)
    df.dropna(subset=["charttime", "valuenum"], inplace=True)
    return df


# ============================================================================
# Pivot vitals / labs into hourly time-series per stay
# ============================================================================

# Reverse mappings: itemid -> readable name
_VITAL_ID_TO_NAME = {v: k for k, v in VITAL_IDS.items()}
_LAB_ID_TO_NAME = {v: k for k, v in LAB_IDS.items()}


def build_hourly_timeseries(
    cohort: pd.DataFrame,
    vitals: pd.DataFrame,
    labs: pd.DataFrame,
) -> Dict[int, pd.DataFrame]:
    """Build an hourly-resampled DataFrame for each stay_id.

    Returns
    -------
    dict mapping stay_id -> DataFrame indexed by hour-offset with columns
    for each vital/lab plus SOFA components.
    """
    # Map itemid to readable column names
    if not vitals.empty:
        vitals = vitals.copy()
        vitals["param"] = vitals["itemid"].map(_VITAL_ID_TO_NAME)
        vitals.dropna(subset=["param"], inplace=True)

    if not labs.empty:
        labs = labs.copy()
        labs["param"] = labs["itemid"].map(_LAB_ID_TO_NAME)
        labs.dropna(subset=["param"], inplace=True)

    # Build a lookup: stay_id -> (intime, hadm_id)
    stay_info = {}
    for _, row in cohort.iterrows():
        sid = row["stay_id"]
        intime = _parse_charttime(row["intime"])
        if intime is None:
            continue
        stay_info[sid] = {
            "intime": intime,
            "hadm_id": row["hadm_id"],
            "los_hours": int(row.get("los", 3) * 24),
        }

    result: Dict[int, pd.DataFrame] = {}

    for stay_id, info in stay_info.items():
        intime = info["intime"]
        los_hours = min(info["los_hours"], 336)  # cap at 14 days
        hadm_id = info["hadm_id"]

        # Create hourly index
        hours = pd.date_range(start=intime, periods=max(los_hours, 1), freq="h")
        ts = pd.DataFrame(index=hours)

        # --- vitals ---
        if not vitals.empty:
            v = vitals[vitals["stay_id"] == stay_id].copy()
            if not v.empty:
                v.set_index("charttime", inplace=True)
                for param in v["param"].unique():
                    series = v.loc[v["param"] == param, "valuenum"]
                    series = series[~series.index.duplicated(keep="last")]
                    resampled = series.resample("h").mean()
                    ts[param] = resampled.reindex(ts.index)

        # --- labs ---
        if not labs.empty:
            l = labs[labs["hadm_id"] == hadm_id].copy()
            if not l.empty:
                l.set_index("charttime", inplace=True)
                for param in l["param"].unique():
                    series = l.loc[l["param"] == param, "valuenum"]
                    series = series[~series.index.duplicated(keep="last")]
                    resampled = series.resample("h").mean()
                    ts[param] = resampled.reindex(ts.index)

        # Ensure all expected columns exist
        for col in list(VITAL_IDS.keys()) + list(LAB_IDS.keys()):
            if col not in ts.columns:
                ts[col] = np.nan

        # --- SOFA ---
        sofa_df = ts.apply(compute_sofa_row, axis=1)
        ts = pd.concat([ts, sofa_df], axis=1)

        # --- delta SOFA (change from baseline = first available SOFA) ---
        ts["delta_SOFA"] = ts["SOFA_total"] - ts["SOFA_total"].iloc[0]

        result[stay_id] = ts

    return result


# ============================================================================
# Windowing and labeling
# ============================================================================

def create_windows(
    timeseries: Dict[int, pd.DataFrame],
    cohort: pd.DataFrame,
    sepsis_hadm_ids: set,
    lookback: int = LOOKBACK_HOURS,
    horizon: int = PREDICTION_HORIZON_HOURS,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Slide a window across each stay to produce (X_seq, X_flat, y, stay_ids).

    Label logic:
        y = 1  if within the next ``horizon`` hours the SOFA increases by >= 2
               AND the admission has a sepsis ICD code.
    """
    all_seq: List[np.ndarray] = []
    all_flat: List[np.ndarray] = []
    all_y: List[int] = []
    all_stay: List[int] = []

    # Build hadm_id lookup from cohort
    stay_to_hadm = dict(zip(cohort["stay_id"], cohort["hadm_id"]))
    stay_to_age = dict(zip(cohort["stay_id"], cohort.get("age", 65.0)))
    stay_to_gender = dict(zip(cohort["stay_id"], cohort.get("gender_encoded", 0)))

    # Encode careunit as integer
    careunits = cohort["first_careunit"].astype(str).unique().tolist()
    careunit_map = {c: i for i, c in enumerate(careunits)}
    stay_to_careunit = {
        row["stay_id"]: careunit_map.get(str(row.get("first_careunit", "")), 0)
        for _, row in cohort.iterrows()
    }

    feature_cols = TIMESTEP_FEATURES  # columns to extract per timestep

    for stay_id, ts in timeseries.items():
        hadm_id = stay_to_hadm.get(stay_id)
        has_sepsis_code = hadm_id in sepsis_hadm_ids if hadm_id else False

        n_rows = len(ts)
        if n_rows < lookback + horizon:
            continue

        # Ensure feature columns exist
        for col in feature_cols:
            if col not in ts.columns:
                ts[col] = 0.0

        arr = ts[feature_cols].values.astype(np.float32)

        # Static features
        age = float(stay_to_age.get(stay_id, 65.0))
        gender = int(stay_to_gender.get(stay_id, 0))
        careunit = int(stay_to_careunit.get(stay_id, 0))

        sofa_col_idx = feature_cols.index("SOFA_total")

        for t in range(lookback, n_rows - horizon):
            window = arr[t - lookback: t]  # (lookback, n_features)

            # --- Label: check if SOFA rises by >= 2 in next horizon hours ---
            current_sofa = arr[t, sofa_col_idx]
            future_sofa = arr[t: t + horizon, sofa_col_idx]
            sofa_increase = np.nanmax(future_sofa) - current_sofa
            label = 1 if (sofa_increase >= 2 and has_sepsis_code) else 0

            # --- Impute NaN in window with forward-fill then zero ---
            win = window.copy()
            for col_i in range(win.shape[1]):
                col_data = win[:, col_i]
                mask = np.isnan(col_data)
                if mask.all():
                    col_data[:] = 0.0
                elif mask.any():
                    # forward fill
                    last_valid = np.nan
                    for r in range(len(col_data)):
                        if np.isnan(col_data[r]):
                            if not np.isnan(last_valid):
                                col_data[r] = last_valid
                        else:
                            last_valid = col_data[r]
                    # backward fill remaining leading NaNs
                    first_valid_idx = np.argmax(~np.isnan(col_data))
                    col_data[:first_valid_idx] = col_data[first_valid_idx]

            all_seq.append(win)

            # --- Flat features: statistical aggregations ---
            flat = []
            for col_i in range(win.shape[1]):
                col_data = win[:, col_i]
                flat.extend([
                    np.nanmean(col_data),
                    np.nanstd(col_data),
                    np.nanmin(col_data),
                    np.nanmax(col_data),
                    col_data[-1],           # most recent value
                    col_data[-1] - col_data[0],  # trend
                ])
            flat.extend([age, gender, float(careunit)])
            all_flat.append(np.array(flat, dtype=np.float32))

            all_y.append(label)
            all_stay.append(stay_id)

    X_seq = np.stack(all_seq) if all_seq else np.empty((0, lookback, len(feature_cols)), dtype=np.float32)
    X_flat = np.stack(all_flat) if all_flat else np.empty((0, len(feature_cols) * 6 + 3), dtype=np.float32)
    y = np.array(all_y, dtype=np.int8)
    stay_ids = np.array(all_stay, dtype=np.int64)

    return X_seq, X_flat, y, stay_ids


# ============================================================================
# Median imputation on flat features (fit on train, apply to val/test)
# ============================================================================

def median_impute(
    X_train: np.ndarray,
    X_val: np.ndarray,
    X_test: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Replace any remaining NaNs with per-feature median from training set."""
    medians = np.nanmedian(X_train, axis=0)
    medians = np.where(np.isnan(medians), 0.0, medians)

    for X in (X_train, X_val, X_test):
        nan_mask = np.isnan(X)
        if nan_mask.any():
            X[nan_mask] = np.take(medians, np.where(nan_mask)[1])

    return X_train, X_val, X_test


# ============================================================================
# Main pipeline
# ============================================================================

def build_dataset(
    mongo_uri: Optional[str] = None,
    max_stays: int = MAX_STAYS,
    output_dir: Optional[Path] = None,
) -> None:
    """End-to-end dataset construction pipeline."""
    output_dir = output_dir or DATASET_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    mongo = MongoManager(uri=mongo_uri)

    try:
        # 1. Fetch ICU cohort
        cohort = fetch_icu_cohort(mongo, max_stays=max_stays)
        if cohort.empty:
            logger.error("No ICU stays found. Aborting.")
            return

        stay_ids = cohort["stay_id"].tolist()
        hadm_ids = cohort["hadm_id"].unique().tolist()

        # 2. Fetch sepsis ICD flags
        sepsis_hadms = fetch_sepsis_flags(mongo, hadm_ids)

        # 3. Fetch vitals & labs in batches
        logger.info("Fetching vitals and labs in batches of %d ...", BATCH_SIZE)
        all_vitals = []
        all_labs = []

        for i in range(0, len(stay_ids), BATCH_SIZE):
            batch_stays = stay_ids[i: i + BATCH_SIZE]
            batch_hadms = cohort.loc[
                cohort["stay_id"].isin(batch_stays), "hadm_id"
            ].unique().tolist()

            logger.info(
                "  Batch %d/%d  (stays %d-%d)",
                i // BATCH_SIZE + 1,
                (len(stay_ids) + BATCH_SIZE - 1) // BATCH_SIZE,
                batch_stays[0],
                batch_stays[-1],
            )

            v = fetch_vitals_batch(mongo, batch_stays)
            if not v.empty:
                all_vitals.append(v)

            l = fetch_labs_batch(mongo, batch_hadms)
            if not l.empty:
                all_labs.append(l)

        vitals_df = pd.concat(all_vitals, ignore_index=True) if all_vitals else pd.DataFrame()
        labs_df = pd.concat(all_labs, ignore_index=True) if all_labs else pd.DataFrame()

        logger.info("Total vital records: %d", len(vitals_df))
        logger.info("Total lab records: %d", len(labs_df))

        # 4. Build hourly time-series per stay
        logger.info("Building hourly time-series for each stay ...")
        timeseries = build_hourly_timeseries(cohort, vitals_df, labs_df)
        logger.info("Time-series built for %d stays", len(timeseries))

        # 5. Create windows
        logger.info("Creating %d-hour windows with %d-hour horizon ...", LOOKBACK_HOURS, PREDICTION_HORIZON_HOURS)
        X_seq, X_flat, y, sid = create_windows(timeseries, cohort, sepsis_hadms)
        logger.info("Total samples: %d  (positive: %d, %.2f%%)",
                     len(y), y.sum(), 100.0 * y.mean() if len(y) else 0)

        if len(y) == 0:
            logger.error("No samples generated. Check data availability.")
            return

        # 6. Stratified split: 70/15/15
        from sklearn.model_selection import train_test_split

        idx = np.arange(len(y))
        idx_train, idx_temp, y_train_split, y_temp = train_test_split(
            idx, y, test_size=0.30, random_state=42, stratify=y,
        )
        idx_val, idx_test, _, _ = train_test_split(
            idx_temp, y_temp, test_size=0.50, random_state=42, stratify=y_temp,
        )

        X_seq_train, X_seq_val, X_seq_test = X_seq[idx_train], X_seq[idx_val], X_seq[idx_test]
        X_flat_train, X_flat_val, X_flat_test = X_flat[idx_train], X_flat[idx_val], X_flat[idx_test]
        y_train, y_val, y_test = y[idx_train], y[idx_val], y[idx_test]
        sid_train, sid_val, sid_test = sid[idx_train], sid[idx_val], sid[idx_test]

        # 7. Median imputation on flat features
        X_flat_train, X_flat_val, X_flat_test = median_impute(
            X_flat_train, X_flat_val, X_flat_test,
        )

        # Also impute sequential features (replace NaN with 0 after ffill in windowing)
        for arr in (X_seq_train, X_seq_val, X_seq_test):
            np.nan_to_num(arr, copy=False, nan=0.0)

        # 8. Save
        for split_name, xseq, xflat, yy, ss in [
            ("train", X_seq_train, X_flat_train, y_train, sid_train),
            ("val", X_seq_val, X_flat_val, y_val, sid_val),
            ("test", X_seq_test, X_flat_test, y_test, sid_test),
        ]:
            path = output_dir / f"{split_name}.npz"
            np.savez_compressed(
                path,
                X_seq=xseq,
                X_flat=xflat,
                y=yy,
                stay_ids=ss,
            )
            logger.info("Saved %s  (%d samples)", path, len(yy))

        # Save feature metadata
        flat_feature_names = []
        for feat in TIMESTEP_FEATURES:
            for stat in ["mean", "std", "min", "max", "last", "trend"]:
                flat_feature_names.append(f"{feat}_{stat}")
        flat_feature_names.extend(STATIC_FEATURES)

        meta = {
            "timestep_features": TIMESTEP_FEATURES,
            "static_features": STATIC_FEATURES,
            "flat_feature_names": flat_feature_names,
            "lookback_hours": LOOKBACK_HOURS,
            "prediction_horizon_hours": PREDICTION_HORIZON_HOURS,
            "n_train": len(y_train),
            "n_val": len(y_val),
            "n_test": len(y_test),
            "pos_rate_train": float(y_train.mean()),
            "pos_rate_test": float(y_test.mean()),
        }
        np.savez(output_dir / "metadata.npz", **{k: np.array(v) for k, v in meta.items()})
        logger.info("Dataset build complete.")

    finally:
        mongo.close()


# ============================================================================
# CLI entry-point
# ============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Sepsis ICU dataset from MongoDB")
    parser.add_argument("--mongo-uri", default=None, help="MongoDB URI")
    parser.add_argument("--max-stays", type=int, default=MAX_STAYS, help="Max ICU stays to process")
    parser.add_argument("--output-dir", type=str, default=None, help="Output directory")
    args = parser.parse_args()

    out = Path(args.output_dir) if args.output_dir else None
    build_dataset(mongo_uri=args.mongo_uri, max_stays=args.max_stays, output_dir=out)
