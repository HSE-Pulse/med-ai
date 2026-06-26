"""Extract patient flow data from MIMIC-IV MongoDB for DES-MARL hospital operations.

Produces three Parquet files:
  - patient_flows.parquet: per-admission transfer sequences with timing
  - dept_capacity.parquet: hourly concurrent patient counts per department
  - arrival_patterns.parquet: hourly/daily admission rates by type

Usage:
    python -m app_03_hospital_ops.backend.data.build_dataset [--mongo-uri URI] [--output-dir DIR]
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from shared.db.mongo import MongoManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEPARTMENT_MAP: Dict[str, str] = {
    "Emergency Department": "ED",
    "ED Observation": "ED_Observation",
    "Medicine": "Medicine",
    "Med/Surg": "Med_Surg",
    "Medicine/Cardiology": "Cardiology",
    "Neurology": "Neurology",
    "Hematology/Oncology": "Hematology_Oncology",
    "Vascular": "Vascular",
    "Transplant": "Transplant",
    "Discharge Lounge": "Discharge_Lounge",
}

# Departments for the simulation environment (subset used by MARL)
SIM_DEPARTMENTS = [
    "ED", "ED_Observation", "Medicine", "Med_Surg",
    "Cardiology", "Neurology", "ICU", "Discharge_Lounge",
]

DEFAULT_OUTPUT = Path("./datasets/hospital_ops")

BATCH_SIZE = 5000


# ---------------------------------------------------------------------------
# Data extraction functions
# ---------------------------------------------------------------------------

def _parse_datetime(val: Any) -> Optional[datetime]:
    """Safely parse a datetime value from MongoDB."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        for fmt in (
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
            "%d-%m-%Y %H:%M", "%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M",
            "%Y-%m-%d %H:%M",
        ):
            try:
                return datetime.strptime(val, fmt)
            except ValueError:
                continue
    return None


def _map_careunit(careunit: Optional[str]) -> str:
    """Map raw careunit name to standardised department name."""
    if careunit is None:
        return "Unknown"
    # Direct mapping
    if careunit in DEPARTMENT_MAP:
        return DEPARTMENT_MAP[careunit]
    # ICU units
    icu_keywords = ["ICU", "SICU", "MICU", "CCU", "CVICU", "TSICU", "Neuro ICU"]
    for kw in icu_keywords:
        if kw.lower() in careunit.lower():
            return "ICU"
    return careunit.replace("/", "_").replace(" ", "_")


def extract_patient_flows(mongo: MongoManager) -> pd.DataFrame:
    """Extract transfer sequences for each admission, computing timing metrics.

    Returns a DataFrame with columns:
        subject_id, hadm_id, transfer_seq, department, intime, outtime,
        time_in_department_hours, wait_time_hours, admission_type, total_los_hours
    """
    logger.info("Extracting patient flow data from MIMIC.transfers + admissions...")

    # Fetch admissions
    admissions_coll = mongo.mimic["admissions"]
    admissions_cursor = admissions_coll.find(
        {},
        {
            "_id": 0, "subject_id": 1, "hadm_id": 1,
            "admittime": 1, "dischtime": 1, "admission_type": 1,
        },
    )
    admissions_df = pd.DataFrame(list(admissions_cursor))
    logger.info(f"Loaded {len(admissions_df)} admissions")

    if admissions_df.empty:
        return pd.DataFrame()

    admissions_df["admittime"] = admissions_df["admittime"].apply(_parse_datetime)
    admissions_df["dischtime"] = admissions_df["dischtime"].apply(_parse_datetime)

    # Build hadm_id -> admission info lookup
    adm_lookup: Dict[int, Dict[str, Any]] = {}
    for _, row in admissions_df.iterrows():
        if pd.notna(row.get("hadm_id")):
            adm_lookup[int(row["hadm_id"])] = {
                "subject_id": row.get("subject_id"),
                "admission_type": row.get("admission_type"),
                "admittime": row["admittime"],
                "dischtime": row["dischtime"],
            }

    # Process transfers in batches
    transfers_coll = mongo.mimic["transfers"]
    total_transfers = transfers_coll.count_documents({})
    logger.info(f"Total transfers in DB: {total_transfers}")

    rows: List[Dict[str, Any]] = []
    hadm_ids = list(adm_lookup.keys())

    for batch_start in range(0, len(hadm_ids), BATCH_SIZE):
        batch_ids = hadm_ids[batch_start : batch_start + BATCH_SIZE]
        transfers = list(transfers_coll.find(
            {"hadm_id": {"$in": batch_ids}},
            {"_id": 0, "hadm_id": 1, "careunit": 1, "intime": 1, "outtime": 1, "eventtype": 1},
        ))

        # Group by hadm_id
        from collections import defaultdict
        grouped: Dict[int, List[Dict]] = defaultdict(list)
        for t in transfers:
            hid = t.get("hadm_id")
            if hid is not None:
                grouped[int(hid)].append(t)

        for hadm_id, xfers in grouped.items():
            adm_info = adm_lookup.get(hadm_id)
            if adm_info is None:
                continue

            # Sort by intime
            for x in xfers:
                x["_intime"] = _parse_datetime(x.get("intime"))
                x["_outtime"] = _parse_datetime(x.get("outtime"))

            xfers.sort(key=lambda x: x["_intime"] or datetime.min)

            # Compute total LOS
            total_los_hours: Optional[float] = None
            if adm_info["admittime"] and adm_info["dischtime"]:
                delta = adm_info["dischtime"] - adm_info["admittime"]
                total_los_hours = delta.total_seconds() / 3600.0

            prev_outtime: Optional[datetime] = None

            for seq_idx, xfer in enumerate(xfers):
                intime = xfer["_intime"]
                outtime = xfer["_outtime"]
                dept = _map_careunit(xfer.get("careunit"))

                # Time in department
                time_in_dept: Optional[float] = None
                if intime and outtime:
                    time_in_dept = (outtime - intime).total_seconds() / 3600.0

                # Wait time from previous department's outtime to this intime
                wait_hours: Optional[float] = None
                if prev_outtime and intime:
                    wait_hours = max(0.0, (intime - prev_outtime).total_seconds() / 3600.0)

                rows.append({
                    "subject_id": adm_info["subject_id"],
                    "hadm_id": hadm_id,
                    "transfer_seq": seq_idx,
                    "department": dept,
                    "intime": intime,
                    "outtime": outtime,
                    "time_in_department_hours": time_in_dept,
                    "wait_time_hours": wait_hours,
                    "admission_type": adm_info["admission_type"],
                    "total_los_hours": total_los_hours,
                })

                prev_outtime = outtime

        if batch_start % (BATCH_SIZE * 10) == 0:
            logger.info(f"Processed {batch_start + len(batch_ids)}/{len(hadm_ids)} admissions")

    df = pd.DataFrame(rows)
    logger.info(f"Extracted {len(df)} transfer records for {df['hadm_id'].nunique()} admissions")
    return df


def build_dept_capacity(flows_df: pd.DataFrame) -> pd.DataFrame:
    """Compute hourly concurrent patient counts per department.

    Parameters
    ----------
    flows_df:
        Output of ``extract_patient_flows``.

    Returns
    -------
    DataFrame with columns: hour, department, concurrent_patients, capacity_utilization
    """
    logger.info("Building department capacity profiles...")

    if flows_df.empty:
        return pd.DataFrame()

    valid = flows_df.dropna(subset=["intime", "outtime"]).copy()
    if valid.empty:
        return pd.DataFrame()

    # Determine time range
    min_time = valid["intime"].min()
    max_time = valid["outtime"].max()

    # Generate hourly bins
    hours = pd.date_range(start=min_time.floor("h"), end=max_time.ceil("h"), freq="h")

    records: List[Dict[str, Any]] = []
    departments = valid["department"].unique()

    for dept in departments:
        dept_data = valid[valid["department"] == dept]
        for hour in hours:
            # Count patients present during this hour
            mask = (dept_data["intime"] <= hour + timedelta(hours=1)) & (dept_data["outtime"] > hour)
            count = mask.sum()
            records.append({
                "hour": hour,
                "department": dept,
                "concurrent_patients": count,
            })

    df = pd.DataFrame(records)

    # Compute capacity utilization (relative to 95th percentile as proxy for capacity)
    if not df.empty:
        capacity_95 = df.groupby("department")["concurrent_patients"].transform(
            lambda x: x.quantile(0.95)
        )
        df["capacity_utilization"] = np.where(
            capacity_95 > 0,
            df["concurrent_patients"] / capacity_95,
            0.0,
        )

    logger.info(f"Built capacity profiles: {len(df)} hourly records across {len(departments)} departments")
    return df


def build_arrival_patterns(mongo: MongoManager) -> pd.DataFrame:
    """Compute hourly and daily admission rates by type.

    Returns
    -------
    DataFrame with columns: hour_of_day, day_of_week, admission_type,
        mean_arrivals_per_hour, std_arrivals_per_hour, total_arrivals
    """
    logger.info("Building arrival patterns from MIMIC.admissions...")

    admissions_coll = mongo.mimic["admissions"]
    cursor = admissions_coll.find(
        {},
        {"_id": 0, "admittime": 1, "admission_type": 1},
    )
    records = list(cursor)
    df = pd.DataFrame(records)

    if df.empty:
        return pd.DataFrame()

    df["admittime"] = df["admittime"].apply(_parse_datetime)
    df = df.dropna(subset=["admittime"])

    df["hour_of_day"] = df["admittime"].dt.hour
    df["day_of_week"] = df["admittime"].dt.dayofweek
    df["date"] = df["admittime"].dt.date

    # Count arrivals per (date, hour, admission_type)
    hourly = (
        df.groupby(["date", "hour_of_day", "day_of_week", "admission_type"])
        .size()
        .reset_index(name="arrivals")
    )

    # Aggregate to mean/std per (hour_of_day, day_of_week, admission_type)
    patterns = (
        hourly.groupby(["hour_of_day", "day_of_week", "admission_type"])["arrivals"]
        .agg(["mean", "std", "sum"])
        .reset_index()
    )
    patterns.columns = [
        "hour_of_day", "day_of_week", "admission_type",
        "mean_arrivals_per_hour", "std_arrivals_per_hour", "total_arrivals",
    ]
    patterns["std_arrivals_per_hour"] = patterns["std_arrivals_per_hour"].fillna(0.0)

    logger.info(f"Built arrival patterns: {len(patterns)} rows")
    return patterns


def compute_service_time_distributions(flows_df: pd.DataFrame) -> pd.DataFrame:
    """Fit log-normal parameters to actual LOS per department.

    Returns
    -------
    DataFrame with columns: department, log_mean, log_std, median_hours, mean_hours, n_obs
    """
    logger.info("Computing service time distributions per department...")

    if flows_df.empty:
        return pd.DataFrame()

    valid = flows_df.dropna(subset=["time_in_department_hours"]).copy()
    valid = valid[valid["time_in_department_hours"] > 0]

    records: List[Dict[str, Any]] = []
    for dept, group in valid.groupby("department"):
        los_hours = group["time_in_department_hours"].values
        # Fit log-normal: take log of positive values
        log_los = np.log(los_hours)
        records.append({
            "department": dept,
            "log_mean": float(np.mean(log_los)),
            "log_std": float(np.std(log_los)),
            "median_hours": float(np.median(los_hours)),
            "mean_hours": float(np.mean(los_hours)),
            "n_obs": len(los_hours),
        })

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_all(
    mongo_uri: Optional[str] = None,
    output_dir: Optional[Path] = None,
) -> Dict[str, Path]:
    """Run the full dataset build pipeline.

    Returns a dict mapping dataset name to its saved file path.
    """
    output_dir = output_dir or DEFAULT_OUTPUT
    output_dir.mkdir(parents=True, exist_ok=True)

    saved: Dict[str, Path] = {}

    with MongoManager(uri=mongo_uri) as mongo:
        # 1. Patient flows
        flows_df = extract_patient_flows(mongo)
        flows_path = output_dir / "patient_flows.parquet"
        flows_df.to_parquet(flows_path, index=False)
        saved["patient_flows"] = flows_path
        logger.info(f"Saved {flows_path}")

        # 2. Department capacity
        capacity_df = build_dept_capacity(flows_df)
        capacity_path = output_dir / "dept_capacity.parquet"
        capacity_df.to_parquet(capacity_path, index=False)
        saved["dept_capacity"] = capacity_path
        logger.info(f"Saved {capacity_path}")

        # 3. Arrival patterns
        arrival_df = build_arrival_patterns(mongo)
        arrival_path = output_dir / "arrival_patterns.parquet"
        arrival_df.to_parquet(arrival_path, index=False)
        saved["arrival_patterns"] = arrival_path
        logger.info(f"Saved {arrival_path}")

        # 4. Service time distributions (bonus for DES calibration)
        svc_df = compute_service_time_distributions(flows_df)
        svc_path = output_dir / "service_time_distributions.parquet"
        svc_df.to_parquet(svc_path, index=False)
        saved["service_time_distributions"] = svc_path
        logger.info(f"Saved {svc_path}")

    return saved


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Build DES-MARL hospital ops dataset from MIMIC-IV")
    parser.add_argument("--mongo-uri", type=str, default=None, help="MongoDB connection URI")
    parser.add_argument("--output-dir", type=str, default=None, help="Output directory for Parquet files")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    output_dir = Path(args.output_dir) if args.output_dir else None
    saved = build_all(mongo_uri=args.mongo_uri, output_dir=output_dir)

    print("\nDataset build complete:")
    for name, path in saved.items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
