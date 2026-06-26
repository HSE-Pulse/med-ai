"""Build oncology dataset from MIMIC MongoDB collections.

Extracts cancer patient cohort, treatment timelines, and discharge notes
for readmission prediction and treatment pathway optimization.
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from pymongo import MongoClient

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT))

DATASET_DIR = ROOT / "datasets" / "oncology"

# ICD codes for cancer
CANCER_ICD10_PREFIX = [f"C{i:02d}" for i in range(100)]  # C00-C99
CANCER_ICD9_RANGE = (140, 239)  # ICD-9 neoplasm codes

# Chemotherapy drug keywords (case-insensitive matching)
CHEMO_DRUGS = [
    "cisplatin", "carboplatin", "paclitaxel", "docetaxel", "doxorubicin",
    "cyclophosphamide", "methotrexate", "fluorouracil", "5-fu", "gemcitabine",
    "irinotecan", "oxaliplatin", "vincristine", "etoposide", "bleomycin",
    "rituximab", "trastuzumab", "bevacizumab", "pembrolizumab", "nivolumab",
    "atezolizumab", "durvalumab", "ipilimumab", "capecitabine", "pemetrexed",
    "temozolomide", "imatinib", "sunitinib", "sorafenib", "lenalidomide",
    "bortezomib", "ibrutinib", "venetoclax", "azacitidine", "decitabine",
]

# Radiation-related ICD procedure codes
RADIATION_ICD9_CODES = ["9221", "9222", "9223", "9224", "9225", "9226", "9227", "9228", "9229"]

# Cancer type mapping from ICD-10 prefix
CANCER_TYPE_MAP = {
    "C00": "Lip/Oral", "C01": "Lip/Oral", "C02": "Lip/Oral", "C03": "Lip/Oral",
    "C04": "Lip/Oral", "C05": "Lip/Oral", "C06": "Lip/Oral", "C07": "Lip/Oral",
    "C08": "Lip/Oral", "C09": "Lip/Oral", "C10": "Lip/Oral", "C11": "Lip/Oral",
    "C12": "Lip/Oral", "C13": "Lip/Oral", "C14": "Lip/Oral",
    "C15": "Esophageal", "C16": "Gastric",
    "C17": "Small Intestine", "C18": "Colon", "C19": "Colorectal", "C20": "Rectal",
    "C21": "Anal", "C22": "Liver", "C23": "Gallbladder", "C24": "Biliary",
    "C25": "Pancreatic",
    "C30": "Nasal/Sinus", "C31": "Nasal/Sinus", "C32": "Laryngeal",
    "C33": "Tracheal", "C34": "Lung",
    "C40": "Bone", "C41": "Bone",
    "C43": "Melanoma", "C44": "Skin",
    "C45": "Mesothelioma", "C46": "Kaposi",
    "C47": "Peripheral Nerve", "C48": "Retroperitoneal", "C49": "Soft Tissue",
    "C50": "Breast",
    "C51": "Vulvar", "C52": "Vaginal", "C53": "Cervical", "C54": "Uterine",
    "C55": "Uterine", "C56": "Ovarian", "C57": "Female Genital",
    "C58": "Placental",
    "C60": "Penile", "C61": "Prostate", "C62": "Testicular",
    "C63": "Male Genital", "C64": "Renal", "C65": "Renal Pelvis",
    "C66": "Ureter", "C67": "Bladder", "C68": "Urinary",
    "C69": "Eye", "C70": "Meninges", "C71": "Brain", "C72": "CNS",
    "C73": "Thyroid", "C74": "Adrenal", "C75": "Endocrine",
    "C76": "Other", "C77": "Lymph Node Metastasis",
    "C78": "Secondary Respiratory", "C79": "Secondary Other",
    "C80": "Unknown Primary",
    "C81": "Hodgkin Lymphoma", "C82": "Follicular Lymphoma",
    "C83": "Non-Hodgkin Lymphoma", "C84": "T-Cell Lymphoma",
    "C85": "Lymphoma NOS",
    "C90": "Multiple Myeloma", "C91": "Leukemia (Lymphoid)",
    "C92": "Leukemia (Myeloid)", "C93": "Leukemia (Monocytic)",
    "C94": "Leukemia (Other)", "C95": "Leukemia (Unspecified)",
    "C96": "Hematopoietic",
}

ICD9_CANCER_TYPE_MAP = {
    "140": "Lip/Oral", "141": "Lip/Oral", "142": "Lip/Oral", "143": "Lip/Oral",
    "144": "Lip/Oral", "145": "Lip/Oral", "146": "Lip/Oral", "147": "Lip/Oral",
    "148": "Lip/Oral", "149": "Lip/Oral",
    "150": "Esophageal", "151": "Gastric", "152": "Small Intestine",
    "153": "Colon", "154": "Colorectal",
    "155": "Liver", "156": "Gallbladder", "157": "Pancreatic",
    "158": "Retroperitoneal", "159": "GI Other",
    "160": "Nasal/Sinus", "161": "Laryngeal", "162": "Lung", "163": "Pleural",
    "170": "Bone", "171": "Soft Tissue", "172": "Melanoma", "173": "Skin",
    "174": "Breast", "175": "Breast",
    "179": "Uterine", "180": "Cervical", "181": "Placental", "182": "Uterine",
    "183": "Ovarian", "184": "Female Genital",
    "185": "Prostate", "186": "Testicular", "187": "Penile", "188": "Bladder",
    "189": "Renal",
    "190": "Eye", "191": "Brain", "192": "CNS",
    "193": "Thyroid", "194": "Endocrine",
    "200": "Lymphoma", "201": "Hodgkin Lymphoma", "202": "Non-Hodgkin Lymphoma",
    "203": "Multiple Myeloma", "204": "Leukemia (Lymphoid)", "205": "Leukemia (Myeloid)",
    "206": "Leukemia (Monocytic)", "207": "Leukemia (Other)", "208": "Leukemia (Unspecified)",
}


def is_cancer_icd(code: str, version: int) -> bool:
    """Check if ICD code is a cancer diagnosis."""
    code = str(code).strip().upper()
    if version == 10 or version == 0:
        return any(code.startswith(p) for p in CANCER_ICD10_PREFIX)
    elif version == 9:
        try:
            num = int(code[:3])
            return CANCER_ICD9_RANGE[0] <= num <= CANCER_ICD9_RANGE[1]
        except (ValueError, IndexError):
            return False
    return False


def get_cancer_type(code: str, version: int) -> str:
    """Map ICD code to cancer type category."""
    code = str(code).strip().upper()
    if version == 10 or version == 0:
        prefix = code[:3]
        return CANCER_TYPE_MAP.get(prefix, "Other")
    elif version == 9:
        prefix = code[:3]
        return ICD9_CANCER_TYPE_MAP.get(prefix, "Other")
    return "Unknown"


def is_chemo_drug(drug_name) -> bool:
    """Check if drug name matches known chemotherapy agents."""
    if not drug_name or not isinstance(drug_name, str):
        return False
    lower = drug_name.lower()
    return any(chemo in lower for chemo in CHEMO_DRUGS)


def compute_charlson_score(icd_codes: list[dict]) -> int:
    """Simplified Charlson Comorbidity Index from ICD codes.

    Uses broad category matching for common comorbidities.
    """
    score = 0
    codes_str = [str(d.get("icd_code", "")).upper() for d in icd_codes]
    codes_joined = " ".join(codes_str)

    # Myocardial infarction (I21-I22, 410)
    if any(c.startswith(("I21", "I22", "410")) for c in codes_str):
        score += 1
    # Congestive heart failure (I50, 428)
    if any(c.startswith(("I50", "428")) for c in codes_str):
        score += 1
    # Cerebrovascular disease (I60-I69, 430-438)
    if any(c.startswith(("I6", "43")) for c in codes_str):
        score += 1
    # Diabetes (E10-E14, 250)
    if any(c.startswith(("E10", "E11", "E12", "E13", "E14", "250")) for c in codes_str):
        score += 1
    # Chronic pulmonary disease (J40-J47, 490-496)
    if any(c.startswith(("J4", "49")) for c in codes_str):
        score += 1
    # Renal disease (N18-N19, 585-586)
    if any(c.startswith(("N18", "N19", "585", "586")) for c in codes_str):
        score += 2
    # Liver disease (K70-K77, 571)
    if any(c.startswith(("K7", "571")) for c in codes_str):
        score += 1
    # Metastatic cancer (C77-C79, 196-199)
    if any(c.startswith(("C77", "C78", "C79", "196", "197", "198", "199")) for c in codes_str):
        score += 6

    return score


def build_dataset():
    """Build oncology dataset from MongoDB."""
    print("=" * 60)
    print("Building Oncology AI Dataset")
    print("=" * 60)

    client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"))
    mimic = client["MIMIC"]
    notes_db = client["MIMIC_Clinical_Notes"]

    # ------------------------------------------------------------------
    # Step 1: Identify cancer admissions
    # ------------------------------------------------------------------
    print("\n[1/7] Identifying cancer admissions...")

    all_diag = list(mimic.diagnoses_icd.find(
        {}, {"hadm_id": 1, "subject_id": 1, "icd_code": 1, "icd_version": 1, "seq_num": 1, "_id": 0}
    ))
    print(f"  Total diagnosis records: {len(all_diag):,}")

    cancer_hadm_ids = set()
    cancer_primary_dx = {}  # hadm_id -> (icd_code, version, seq_num)

    for d in all_diag:
        code = str(d.get("icd_code", ""))
        version = d.get("icd_version", 0)
        if is_cancer_icd(code, version):
            hid = d["hadm_id"]
            cancer_hadm_ids.add(hid)
            seq = d.get("seq_num", 999)
            if hid not in cancer_primary_dx or seq < cancer_primary_dx[hid][2]:
                cancer_primary_dx[hid] = (code, version, seq)

    print(f"  Cancer admissions found: {len(cancer_hadm_ids):,}")

    # ------------------------------------------------------------------
    # Step 2: Fetch admissions for cancer patients
    # ------------------------------------------------------------------
    print("\n[2/7] Fetching admission details...")

    admissions = list(mimic.admissions.find(
        {"hadm_id": {"$in": list(cancer_hadm_ids)}},
        {"_id": 0, "subject_id": 1, "hadm_id": 1, "admittime": 1, "dischtime": 1,
         "admission_type": 1, "admission_location": 1, "discharge_location": 1,
         "insurance": 1, "hospital_expire_flag": 1}
    ))
    df_adm = pd.DataFrame(admissions)
    print(f"  Admissions fetched: {len(df_adm):,}")

    for col in ["admittime", "dischtime"]:
        df_adm[col] = pd.to_datetime(df_adm[col], errors="coerce")

    df_adm["total_los_days"] = (df_adm["dischtime"] - df_adm["admittime"]).dt.total_seconds() / 86400
    df_adm["total_los_days"] = df_adm["total_los_days"].clip(0, 365)

    # ------------------------------------------------------------------
    # Step 3: Attach patient demographics
    # ------------------------------------------------------------------
    print("\n[3/7] Attaching demographics...")

    subject_ids = df_adm["subject_id"].unique().tolist()
    patients = list(mimic.patients.find(
        {"subject_id": {"$in": subject_ids}},
        {"_id": 0, "subject_id": 1, "gender": 1, "anchor_age": 1, "anchor_year": 1}
    ))
    df_pat = pd.DataFrame(patients)

    if not df_pat.empty:
        df_adm = df_adm.merge(df_pat, on="subject_id", how="left")
        df_adm["age"] = df_adm["anchor_age"]
        if "anchor_year" in df_adm.columns:
            year_offset = df_adm["admittime"].dt.year - df_adm["anchor_year"]
            df_adm["age"] = df_adm["anchor_age"] + year_offset.fillna(0).astype(int)
        df_adm["age"] = df_adm["age"].clip(18, 100)
    else:
        df_adm["age"] = 65
        df_adm["gender"] = "Unknown"

    print(f"  Unique patients: {df_adm['subject_id'].nunique():,}")

    # ------------------------------------------------------------------
    # Step 4: Cancer type + DRG severity
    # ------------------------------------------------------------------
    print("\n[4/7] Mapping cancer types and severity...")

    df_adm["cancer_icd"] = df_adm["hadm_id"].map(
        lambda h: cancer_primary_dx.get(h, ("Unknown", 0, 999))[0]
    )
    df_adm["cancer_icd_version"] = df_adm["hadm_id"].map(
        lambda h: cancer_primary_dx.get(h, ("Unknown", 0, 999))[1]
    )
    df_adm["cancer_type"] = df_adm.apply(
        lambda r: get_cancer_type(r["cancer_icd"], r["cancer_icd_version"]), axis=1
    )

    # DRG severity
    drg_docs = list(mimic.drgcodes.find(
        {"hadm_id": {"$in": list(cancer_hadm_ids)}},
        {"_id": 0, "hadm_id": 1, "drg_severity": 1, "drg_mortality": 1}
    ))
    if drg_docs:
        df_drg = pd.DataFrame(drg_docs).groupby("hadm_id").agg(
            stage_proxy=("drg_severity", "max"),
            drg_mortality=("drg_mortality", "max")
        ).reset_index()
        df_adm = df_adm.merge(df_drg, on="hadm_id", how="left")
    else:
        df_adm["stage_proxy"] = np.nan
        df_adm["drg_mortality"] = np.nan

    df_adm["stage_proxy"] = df_adm["stage_proxy"].fillna(2).astype(int).clip(1, 4)
    df_adm["drg_mortality"] = df_adm["drg_mortality"].fillna(1).astype(int).clip(1, 4)

    print(f"  Cancer type distribution:\n{df_adm['cancer_type'].value_counts().head(15).to_string()}")

    # ------------------------------------------------------------------
    # Step 5: Procedures and prescriptions
    # ------------------------------------------------------------------
    print("\n[5/7] Extracting treatment information...")

    # Procedures
    procedures = list(mimic.procedures_icd.find(
        {"hadm_id": {"$in": list(cancer_hadm_ids)}},
        {"_id": 0, "hadm_id": 1, "subject_id": 1, "icd_code": 1, "icd_version": 1,
         "chartdate": 1, "seq_num": 1}
    ))
    df_proc = pd.DataFrame(procedures) if procedures else pd.DataFrame(
        columns=["hadm_id", "subject_id", "icd_code", "icd_version", "chartdate", "seq_num"]
    )

    # Count procedures per admission
    proc_counts = df_proc.groupby("hadm_id").size().reset_index(name="num_procedures")
    df_adm = df_adm.merge(proc_counts, on="hadm_id", how="left")
    df_adm["num_procedures"] = df_adm["num_procedures"].fillna(0).astype(int)

    # Surgery flag (any non-radiation procedure)
    if not df_proc.empty:
        surgery_hadms = df_proc[
            ~df_proc["icd_code"].astype(str).isin(RADIATION_ICD9_CODES)
        ]["hadm_id"].unique()
        df_adm["has_surgery"] = df_adm["hadm_id"].isin(surgery_hadms).astype(int)

        # Radiation flag
        rad_hadms = df_proc[
            df_proc["icd_code"].astype(str).isin(RADIATION_ICD9_CODES)
        ]["hadm_id"].unique()
        df_adm["has_radiation"] = df_adm["hadm_id"].isin(rad_hadms).astype(int)
    else:
        df_adm["has_surgery"] = 0
        df_adm["has_radiation"] = 0

    # Time to first procedure
    if not df_proc.empty and "chartdate" in df_proc.columns:
        df_proc["chartdate"] = pd.to_datetime(df_proc["chartdate"], errors="coerce")
        first_proc = df_proc.dropna(subset=["chartdate"]).groupby("hadm_id")["chartdate"].min().reset_index()
        first_proc.columns = ["hadm_id", "first_proc_date"]
        df_adm = df_adm.merge(first_proc, on="hadm_id", how="left")
        df_adm["time_to_first_procedure_days"] = (
            df_adm["first_proc_date"] - df_adm["admittime"]
        ).dt.total_seconds() / 86400
        df_adm["time_to_first_procedure_days"] = df_adm["time_to_first_procedure_days"].clip(0, 90)
    else:
        df_adm["time_to_first_procedure_days"] = np.nan

    # Prescriptions - chemotherapy
    print("  Scanning prescriptions for chemotherapy drugs (batched)...")
    chemo_hadms = set()
    chemo_counts = {}
    hadm_list = list(cancer_hadm_ids)
    BATCH = 5000
    for i in range(0, len(hadm_list), BATCH):
        batch = hadm_list[i:i + BATCH]
        cursor = mimic.prescriptions.find(
            {"hadm_id": {"$in": batch}},
            {"_id": 0, "hadm_id": 1, "drug": 1},
        )
        for rx in cursor:
            if is_chemo_drug(rx.get("drug", "")):
                hid = rx["hadm_id"]
                chemo_hadms.add(hid)
                drug_name = str(rx.get("drug", "")).lower().strip()
                chemo_counts.setdefault(hid, set()).add(drug_name)
        if (i // BATCH) % 3 == 0:
            print(f"    Processed {min(i + BATCH, len(hadm_list)):,}/{len(hadm_list):,} hadm_ids")

    df_adm["has_chemotherapy"] = df_adm["hadm_id"].isin(chemo_hadms).astype(int)
    df_adm["chemo_drug_count"] = df_adm["hadm_id"].map(
        lambda h: len(chemo_counts.get(h, set()))
    )
    print(f"  Patients with chemo: {df_adm['has_chemotherapy'].sum():,}")
    print(f"  Patients with surgery: {df_adm['has_surgery'].sum():,}")
    print(f"  Patients with radiation: {df_adm['has_radiation'].sum():,}")

    # ------------------------------------------------------------------
    # Step 6: Comorbidities and Charlson score
    # ------------------------------------------------------------------
    print("\n[6/7] Computing comorbidities...")

    # Group all diagnoses per hadm_id
    diag_by_hadm = {}
    non_cancer_counts = {}
    for d in all_diag:
        hid = d.get("hadm_id")
        if hid in cancer_hadm_ids:
            diag_by_hadm.setdefault(hid, []).append(d)
            code = str(d.get("icd_code", ""))
            version = d.get("icd_version", 0)
            if not is_cancer_icd(code, version):
                non_cancer_counts[hid] = non_cancer_counts.get(hid, 0) + 1

    df_adm["num_comorbidities"] = df_adm["hadm_id"].map(
        lambda h: non_cancer_counts.get(h, 0)
    )
    df_adm["charlson_score"] = df_adm["hadm_id"].map(
        lambda h: compute_charlson_score(diag_by_hadm.get(h, []))
    )

    # ------------------------------------------------------------------
    # Step 7: Labels
    # ------------------------------------------------------------------
    print("\n[7/7] Generating labels...")

    # Hospital mortality
    df_adm["hospital_mortality"] = df_adm["hospital_expire_flag"].fillna(0).astype(int)

    # 30-day readmission
    df_adm = df_adm.sort_values(["subject_id", "admittime"])
    df_adm["next_admittime"] = df_adm.groupby("subject_id")["admittime"].shift(-1)
    df_adm["days_to_readmission"] = (
        df_adm["next_admittime"] - df_adm["dischtime"]
    ).dt.total_seconds() / 86400
    df_adm["readmission_30d"] = (df_adm["days_to_readmission"] <= 30).astype(int)
    df_adm["readmission_30d"] = df_adm["readmission_30d"].fillna(0).astype(int)

    # Prior admissions count
    df_adm["num_prior_admissions"] = df_adm.groupby("subject_id").cumcount()

    # Days since last admission
    df_adm["prev_dischtime"] = df_adm.groupby("subject_id")["dischtime"].shift(1)
    df_adm["days_since_last_admission"] = (
        df_adm["admittime"] - df_adm["prev_dischtime"]
    ).dt.total_seconds() / 86400
    df_adm["days_since_last_admission"] = df_adm["days_since_last_admission"].clip(0, 365).fillna(0)

    # Treatment delay label
    median_ttp = df_adm["time_to_first_procedure_days"].median()
    df_adm["treatment_delay"] = (df_adm["time_to_first_procedure_days"] > median_ttp).astype(int)

    # Long stay label
    p75_los = df_adm["total_los_days"].quantile(0.75)
    df_adm["long_stay"] = (df_adm["total_los_days"] > p75_los).astype(int)

    # ------------------------------------------------------------------
    # Feature selection and encoding
    # ------------------------------------------------------------------
    print("\nEngineering features...")

    df_adm["gender_encoded"] = (df_adm["gender"] == "M").astype(int)
    df_adm["insurance_encoded"] = df_adm["insurance"].fillna("Other").astype("category").cat.codes

    feature_cols = [
        "age", "gender_encoded", "stage_proxy", "drg_mortality",
        "num_procedures", "has_surgery", "has_chemotherapy", "has_radiation",
        "chemo_drug_count", "num_prior_admissions", "days_since_last_admission",
        "total_los_days", "num_comorbidities", "charlson_score",
        "insurance_encoded", "time_to_first_procedure_days",
    ]

    label_cols = ["readmission_30d", "hospital_mortality", "treatment_delay", "long_stay"]
    meta_cols = ["subject_id", "hadm_id", "cancer_type", "cancer_icd", "admittime", "dischtime"]

    # Impute missing
    for col in feature_cols:
        if col in df_adm.columns:
            df_adm[col] = df_adm[col].fillna(df_adm[col].median() if df_adm[col].dtype != object else 0)

    # ------------------------------------------------------------------
    # Train/val/test split (patient-level)
    # ------------------------------------------------------------------
    print("Splitting train/val/test (patient-level)...")

    unique_patients = df_adm["subject_id"].unique()
    np.random.seed(42)
    np.random.shuffle(unique_patients)
    n = len(unique_patients)
    train_pats = set(unique_patients[: int(0.7 * n)])
    val_pats = set(unique_patients[int(0.7 * n): int(0.85 * n)])
    test_pats = set(unique_patients[int(0.85 * n):])

    train_df = df_adm[df_adm["subject_id"].isin(train_pats)].copy()
    val_df = df_adm[df_adm["subject_id"].isin(val_pats)].copy()
    test_df = df_adm[df_adm["subject_id"].isin(test_pats)].copy()

    # ------------------------------------------------------------------
    # Save datasets
    # ------------------------------------------------------------------
    DATASET_DIR.mkdir(parents=True, exist_ok=True)

    save_cols = meta_cols + feature_cols + label_cols
    save_cols = [c for c in save_cols if c in df_adm.columns]

    train_df[save_cols].to_parquet(DATASET_DIR / "train.parquet", index=False)
    val_df[save_cols].to_parquet(DATASET_DIR / "val.parquet", index=False)
    test_df[save_cols].to_parquet(DATASET_DIR / "test.parquet", index=False)

    # ------------------------------------------------------------------
    # Extract treatment timelines
    # ------------------------------------------------------------------
    print("Building treatment timelines...")

    treatments = []
    for _, row in df_adm.iterrows():
        hid = row["hadm_id"]
        base = {
            "subject_id": row["subject_id"],
            "hadm_id": hid,
            "cancer_type": row["cancer_type"],
        }
        # Add procedures
        hadm_procs = df_proc[df_proc["hadm_id"] == hid] if not df_proc.empty else pd.DataFrame()
        for _, p in hadm_procs.iterrows():
            treatments.append({
                **base,
                "event_type": "procedure",
                "event_code": str(p.get("icd_code", "")),
                "event_date": str(p.get("chartdate", "")),
            })
        # Add chemo flag
        if row.get("has_chemotherapy"):
            treatments.append({
                **base,
                "event_type": "chemotherapy",
                "event_code": "chemo",
                "event_date": str(row.get("admittime", "")),
            })

    if treatments:
        df_tx = pd.DataFrame(treatments)
        df_tx.to_parquet(DATASET_DIR / "treatments.parquet", index=False)
        print(f"  Treatment events: {len(df_tx):,}")
    else:
        print("  No treatment events found")

    # ------------------------------------------------------------------
    # Extract discharge notes
    # ------------------------------------------------------------------
    print("Extracting discharge notes for cancer patients...")

    note_hadm_ids = list(cancer_hadm_ids)[:5000]  # limit for performance
    notes = list(notes_db.discharge.find(
        {"hadm_id": {"$in": note_hadm_ids}},
        {"_id": 0, "subject_id": 1, "hadm_id": 1, "note_type": 1, "charttime": 1, "text": 1}
    ))

    if notes:
        df_notes = pd.DataFrame(notes)
        df_notes["text_length"] = df_notes["text"].str.len()
        df_notes.to_parquet(DATASET_DIR / "notes.parquet", index=False)
        print(f"  Discharge notes: {len(df_notes):,}")
    else:
        print("  No discharge notes found for cancer patients")

    # ------------------------------------------------------------------
    # Save metadata
    # ------------------------------------------------------------------
    metadata = {
        "total_cancer_admissions": len(df_adm),
        "unique_patients": int(df_adm["subject_id"].nunique()),
        "train_size": len(train_df),
        "val_size": len(val_df),
        "test_size": len(test_df),
        "feature_columns": feature_cols,
        "label_columns": label_cols,
        "cancer_type_distribution": df_adm["cancer_type"].value_counts().to_dict(),
        "readmission_30d_rate": float(df_adm["readmission_30d"].mean()),
        "hospital_mortality_rate": float(df_adm["hospital_mortality"].mean()),
        "chemo_rate": float(df_adm["has_chemotherapy"].mean()),
        "surgery_rate": float(df_adm["has_surgery"].mean()),
        "radiation_rate": float(df_adm["has_radiation"].mean()),
        "median_los_days": float(df_adm["total_los_days"].median()),
        "median_age": float(df_adm["age"].median()),
    }

    with open(DATASET_DIR / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2, default=str)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("ONCOLOGY DATASET SUMMARY")
    print("=" * 60)
    print(f"  Total admissions:      {len(df_adm):,}")
    print(f"  Unique patients:       {df_adm['subject_id'].nunique():,}")
    print(f"  Train / Val / Test:    {len(train_df):,} / {len(val_df):,} / {len(test_df):,}")
    print(f"  30-day readmission:    {df_adm['readmission_30d'].mean():.1%}")
    print(f"  Hospital mortality:    {df_adm['hospital_mortality'].mean():.1%}")
    print(f"  Chemotherapy rate:     {df_adm['has_chemotherapy'].mean():.1%}")
    print(f"  Surgery rate:          {df_adm['has_surgery'].mean():.1%}")
    print(f"  Median LOS:            {df_adm['total_los_days'].median():.1f} days")
    print(f"  Top cancer types:")
    for ct, cnt in df_adm["cancer_type"].value_counts().head(10).items():
        print(f"    {ct}: {cnt:,} ({cnt/len(df_adm):.1%})")
    print(f"\n  Saved to: {DATASET_DIR}")
    print("=" * 60)

    client.close()


if __name__ == "__main__":
    build_dataset()
