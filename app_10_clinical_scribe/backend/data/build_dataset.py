"""
Clinical Scribe Dataset Builder
================================
Extracts discharge notes from MIMIC-IV Clinical Notes database and
joins with diagnosis/prescription/procedure ground truth for:
1. ICD-10 multi-label coding (top 50 codes)
2. NER ground truth (medications, procedures)
3. Note section classification

Usage::

    python -m app_10_clinical_scribe.backend.data.build_dataset
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_PROJECT_ROOT))

from shared.db.mongo import MongoManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("clinical_scribe.build_dataset")

DATASET_OUT_DIR = Path(
    os.getenv("DATASET_OUT_DIR", "./datasets/clinical_scribe")
)
BATCH = 5000
MAX_TEXT_LENGTH = 5000
TOP_K_CODES = 50

SECTION_PATTERNS = [
    (r"history of present illness", "hpi"),
    (r"past medical history", "pmh"),
    (r"medications on admission", "medications_admission"),
    (r"discharge medications", "discharge_medications"),
    (r"discharge diagnosis", "discharge_diagnosis"),
    (r"physical exam", "physical_exam"),
    (r"brief hospital course", "hospital_course"),
    (r"discharge instructions", "instructions"),
    (r"discharge condition", "condition"),
    (r"pertinent results", "results"),
    (r"allergies", "allergies"),
    (r"social history", "social_history"),
    (r"family history", "family_history"),
]


# ===================================================================
# 1.  FETCH DISCHARGE NOTES
# ===================================================================
def fetch_notes(mongo: MongoManager) -> pd.DataFrame:
    """Fetch discharge notes from MIMIC_Clinical_Notes.discharge."""
    logger.info("Fetching discharge notes ...")
    cursor = mongo.mimic_notes["discharge"].find(
        {},
        {"_id": 0, "subject_id": 1, "hadm_id": 1, "text": 1},
    )
    records = []
    count = 0
    for doc in cursor:
        text = doc.get("text", "")
        if text and len(text) > 100:  # Skip very short notes
            records.append({
                "subject_id": doc.get("subject_id"),
                "hadm_id": doc.get("hadm_id"),
                "text": text[:MAX_TEXT_LENGTH],
            })
        count += 1
        if count % 50000 == 0:
            logger.info("  Processed %d notes (%d kept)", count, len(records))

    df = pd.DataFrame(records)
    df = df.dropna(subset=["hadm_id"]).drop_duplicates(subset=["hadm_id"]).copy()
    logger.info("  Total notes: %d", len(df))
    return df


# ===================================================================
# 2.  FETCH ICD CODES (GROUND TRUTH)
# ===================================================================
def fetch_icd_codes(mongo: MongoManager, hadm_ids: List[int]) -> pd.DataFrame:
    """Fetch all diagnosis ICD codes for the note hadm_ids."""
    logger.info("Fetching ICD codes for %d admissions ...", len(hadm_ids))
    records: List[Dict] = []
    for i in range(0, len(hadm_ids), BATCH):
        records.extend(list(mongo.mimic["diagnoses_icd"].find(
            {"hadm_id": {"$in": hadm_ids[i:i+BATCH]}},
            {"_id": 0, "hadm_id": 1, "icd_code": 1, "icd_version": 1},
        )))
    logger.info("  Fetched %d ICD records", len(records))
    return pd.DataFrame(records) if records else pd.DataFrame(columns=["hadm_id", "icd_code"])


# ===================================================================
# 3.  BUILD ICD MULTI-LABEL DATASET
# ===================================================================
def build_icd_dataset(notes_df: pd.DataFrame, icd_df: pd.DataFrame, out_dir: Path):
    """Create multi-label ICD coding dataset (top 50 codes)."""
    logger.info("Building ICD coding dataset (top %d codes) ...", TOP_K_CODES)

    # Find top 50 codes
    code_counts = icd_df["icd_code"].value_counts()
    top_codes = code_counts.head(TOP_K_CODES).index.tolist()
    logger.info("  Top code frequencies: %d-%d",
                int(code_counts.iloc[0]), int(code_counts.iloc[TOP_K_CODES-1]))

    # Create binary matrix
    icd_pivot = icd_df[icd_df["icd_code"].isin(top_codes)].copy()
    icd_pivot["present"] = 1
    icd_matrix = icd_pivot.pivot_table(
        index="hadm_id", columns="icd_code", values="present",
        aggfunc="max", fill_value=0,
    ).reset_index()

    # Merge with notes
    merged = notes_df[["hadm_id", "subject_id", "text"]].merge(icd_matrix, on="hadm_id", how="inner")
    logger.info("  Merged dataset: %d notes with ICD labels", len(merged))

    if merged.empty:
        logger.warning("  No merged data. Skipping ICD dataset.")
        return

    # Patient-level split
    unique_patients = merged["subject_id"].unique()
    np.random.seed(42)
    np.random.shuffle(unique_patients)
    n = len(unique_patients)
    train_pats = set(unique_patients[:int(0.7*n)])
    val_pats = set(unique_patients[int(0.7*n):int(0.85*n)])
    test_pats = set(unique_patients[int(0.85*n):])

    train = merged[merged["subject_id"].isin(train_pats)]
    val = merged[merged["subject_id"].isin(val_pats)]
    test = merged[merged["subject_id"].isin(test_pats)]

    train.to_parquet(out_dir / "icd_coding_train.parquet", index=False)
    val.to_parquet(out_dir / "icd_coding_val.parquet", index=False)
    test.to_parquet(out_dir / "icd_coding_test.parquet", index=False)

    # Save code list
    with open(out_dir / "top50_icd_codes.json", "w") as f:
        json.dump({"codes": top_codes, "counts": code_counts.head(TOP_K_CODES).to_dict()}, f, indent=2)

    logger.info("  ICD dataset saved: train=%d, val=%d, test=%d", len(train), len(val), len(test))


# ===================================================================
# 4.  BUILD NER GROUND TRUTH
# ===================================================================
def build_ner_ground_truth(notes_df: pd.DataFrame, mongo: MongoManager, out_dir: Path):
    """Build NER ground truth from prescriptions and procedures."""
    logger.info("Building NER ground truth ...")
    hadm_ids = notes_df["hadm_id"].dropna().astype(int).unique().tolist()

    # Medications
    med_map: Dict[int, List[str]] = {}
    for i in range(0, len(hadm_ids), BATCH):
        batch = hadm_ids[i:i+BATCH]
        cursor = mongo.mimic["prescriptions"].find(
            {"hadm_id": {"$in": batch}}, {"_id": 0, "hadm_id": 1, "drug": 1},
        )
        for doc in cursor:
            hid = doc.get("hadm_id")
            drug = doc.get("drug")
            if hid and drug:
                med_map.setdefault(hid, [])
                if drug not in med_map[hid]:
                    med_map[hid].append(drug)
        if (i // BATCH) % 10 == 0:
            logger.info("  Medications: processed %d/%d", min(i+BATCH, len(hadm_ids)), len(hadm_ids))

    # Procedures
    proc_map: Dict[int, List[str]] = {}
    for i in range(0, len(hadm_ids), BATCH):
        batch = hadm_ids[i:i+BATCH]
        proc_docs = list(mongo.mimic["procedures_icd"].find(
            {"hadm_id": {"$in": batch}}, {"_id": 0, "hadm_id": 1, "icd_code": 1},
        ))
        for doc in proc_docs:
            hid = doc.get("hadm_id")
            code = doc.get("icd_code")
            if hid and code:
                proc_map.setdefault(hid, [])
                proc_map[hid].append(str(code))

    ner_records = []
    for _, row in notes_df.iterrows():
        hid = row["hadm_id"]
        ner_records.append({
            "hadm_id": hid,
            "medications": json.dumps(med_map.get(hid, [])),
            "procedures": json.dumps(proc_map.get(hid, [])),
        })

    ner_df = pd.DataFrame(ner_records)
    ner_df.to_parquet(out_dir / "ner_ground_truth.parquet", index=False)
    logger.info("  NER ground truth: %d rows, %d with meds, %d with procs",
                len(ner_df),
                sum(1 for m in ner_df["medications"] if m != "[]"),
                sum(1 for p in ner_df["procedures"] if p != "[]"))


# ===================================================================
# 5.  BUILD SECTION CLASSIFICATION DATASET
# ===================================================================
def build_section_dataset(notes_df: pd.DataFrame, out_dir: Path):
    """Parse discharge notes into sections and create classification dataset."""
    logger.info("Building section classification dataset ...")

    section_records = []
    for _, row in notes_df.iterrows():
        text = row["text"]
        hadm_id = row["hadm_id"]
        sections = _split_into_sections(text)
        for section_label, section_text in sections:
            if len(section_text.strip()) > 20:
                section_records.append({
                    "hadm_id": hadm_id,
                    "text_chunk": section_text[:2000],
                    "section_label": section_label,
                })

    if not section_records:
        logger.warning("  No sections found. Skipping section dataset.")
        return

    sec_df = pd.DataFrame(section_records)
    logger.info("  Section records: %d, labels: %s",
                len(sec_df), sec_df["section_label"].value_counts().to_dict())

    # Split
    from sklearn.model_selection import train_test_split
    train, temp = train_test_split(sec_df, train_size=0.7, stratify=sec_df["section_label"], random_state=42)
    val, test = train_test_split(temp, train_size=0.5, stratify=temp["section_label"], random_state=42)

    train.to_parquet(out_dir / "note_sections_train.parquet", index=False)
    val.to_parquet(out_dir / "note_sections_val.parquet", index=False)
    test.to_parquet(out_dir / "note_sections_test.parquet", index=False)
    logger.info("  Sections: train=%d, val=%d, test=%d", len(train), len(val), len(test))


def _split_into_sections(text: str) -> List[tuple]:
    """Split discharge note into labeled sections using regex patterns."""
    text_lower = text.lower()
    found_sections = []

    for pattern, label in SECTION_PATTERNS:
        for match in re.finditer(pattern, text_lower):
            found_sections.append((match.start(), label))

    if not found_sections:
        return [("unknown", text)]

    found_sections.sort(key=lambda x: x[0])
    results = []
    for i, (start, label) in enumerate(found_sections):
        end = found_sections[i+1][0] if i+1 < len(found_sections) else len(text)
        section_text = text[start:end]
        # Remove the header line
        lines = section_text.split("\n", 1)
        content = lines[1] if len(lines) > 1 else lines[0]
        results.append((label, content.strip()))
    return results


# ===================================================================
# MAIN
# ===================================================================
def main() -> None:
    logger.info("Starting Clinical Scribe dataset build ...")
    DATASET_OUT_DIR.mkdir(parents=True, exist_ok=True)

    with MongoManager() as mongo:
        notes_df = fetch_notes(mongo)
        hadm_ids = notes_df["hadm_id"].dropna().astype(int).unique().tolist()

        # ICD ground truth
        icd_df = fetch_icd_codes(mongo, hadm_ids)
        build_icd_dataset(notes_df, icd_df, DATASET_OUT_DIR)

        # NER ground truth
        build_ner_ground_truth(notes_df, mongo, DATASET_OUT_DIR)

    # Section classification
    build_section_dataset(notes_df, DATASET_OUT_DIR)

    # Metadata
    metadata = {
        "total_notes": len(notes_df),
        "max_text_length": MAX_TEXT_LENGTH,
        "top_k_codes": TOP_K_CODES,
    }
    with open(DATASET_OUT_DIR / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    print("\n" + "=" * 60)
    print("CLINICAL SCRIBE DATASET STATISTICS")
    print("=" * 60)
    print(f"Total discharge notes: {len(notes_df):,}")
    print(f"ICD coding: top {TOP_K_CODES} codes")
    print(f"Output: {DATASET_OUT_DIR}")
    print("=" * 60)

    logger.info("Dataset build complete.")


if __name__ == "__main__":
    main()
