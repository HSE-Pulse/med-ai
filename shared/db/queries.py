"""Pre-built MongoDB aggregation pipelines for common MIMIC-IV cohort queries."""

from __future__ import annotations

from typing import Any, Dict, List


def ed_cohort_pipeline() -> List[Dict[str, Any]]:
    """Admissions that entered through the Emergency Department.

    Filters admissions where ``edregtime`` is not null, then joins with the
    ``transfers`` collection to pull ED-specific transfer rows
    (``careunit == "Emergency Department"``).
    """
    return [
        # 1. Only admissions that have an ED registration time
        {"$match": {"edregtime": {"$ne": None}}},
        # 2. Look up matching transfers for this admission
        {
            "$lookup": {
                "from": "transfers",
                "let": {"hadm": "$hadm_id"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {"$eq": ["$hadm_id", "$$hadm"]},
                            "careunit": "Emergency Department",
                        }
                    }
                ],
                "as": "ed_transfers",
            }
        },
        # 3. Keep only rows that actually have ED transfer records
        {"$match": {"ed_transfers": {"$ne": []}}},
        # 4. Project useful fields
        {
            "$project": {
                "_id": 0,
                "subject_id": 1,
                "hadm_id": 1,
                "admittime": 1,
                "dischtime": 1,
                "admission_type": 1,
                "admission_location": 1,
                "discharge_location": 1,
                "insurance": 1,
                "race": 1,
                "edregtime": 1,
                "edouttime": 1,
                "hospital_expire_flag": 1,
                "ed_transfers": 1,
            }
        },
    ]


def icu_cohort_pipeline() -> List[Dict[str, Any]]:
    """ICU stays joined with admissions to derive a mortality label.

    Runs on the ``MIMIC_ICU.icustays`` collection and looks up the
    corresponding admission record from ``MIMIC.admissions`` to attach
    ``hospital_expire_flag`` as the outcome label.
    """
    return [
        # 1. Lookup the admission for each ICU stay
        {
            "$lookup": {
                "from": "admissions",  # in practice, run against MIMIC db
                "localField": "hadm_id",
                "foreignField": "hadm_id",
                "as": "admission",
            }
        },
        {"$unwind": "$admission"},
        # 2. Project essential ICU + outcome fields
        {
            "$project": {
                "_id": 0,
                "subject_id": 1,
                "hadm_id": 1,
                "stay_id": 1,
                "first_careunit": 1,
                "last_careunit": 1,
                "intime": 1,
                "outtime": 1,
                "los": 1,
                "admittime": "$admission.admittime",
                "dischtime": "$admission.dischtime",
                "hospital_expire_flag": "$admission.hospital_expire_flag",
            }
        },
    ]


def sepsis_lab_items() -> Dict[str, int]:
    """Key lab ``itemid`` values used in sepsis screening.

    Returns a mapping of human-readable lab name to MIMIC-IV ``d_labitems.itemid``.
    """
    return {
        "WBC": 51301,
        "Lactate": 50813,
        "Creatinine": 50912,
        "BUN": 51006,
        "Platelets": 51265,
        "Bilirubin": 50885,
        "Bands": 51144,
        "CRP": 50889,
        "Procalcitonin": 51652,
        "INR": 51237,
        "PTT": 51275,
        "Bicarbonate": 50882,
        "Chloride": 50902,
        "Glucose": 50931,
        "Potassium": 50971,
        "Sodium": 50983,
        "Hemoglobin": 51222,
        "Hematocrit": 51221,
    }


def vital_sign_items() -> Dict[str, int]:
    """Chartevents ``itemid`` values for core vital signs.

    Returns a mapping of vital-sign abbreviation to MIMIC-IV
    ``MIMIC_ICU.d_items.itemid``.
    """
    return {
        "HR": 220045,
        "RR": 220210,
        "SpO2": 220277,
        "SBP": 220179,
        "DBP": 220180,
        "Temp": 223761,
        "MBP": 220181,
        "FiO2": 223835,
        "GCS_Total": 228300,
    }


def oncology_cohort_pipeline() -> List[Dict[str, Any]]:
    """Admissions with at least one ICD-10 cancer diagnosis (codes starting with 'C').

    Runs on ``MIMIC.admissions`` and looks up ``MIMIC.diagnoses_icd`` for
    ICD-10 (``icd_version == 10``) codes whose ``icd_code`` begins with ``'C'``.
    """
    return [
        # 1. Lookup diagnoses for each admission
        {
            "$lookup": {
                "from": "diagnoses_icd",
                "let": {"hadm": "$hadm_id"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {"$eq": ["$hadm_id", "$$hadm"]},
                            "icd_version": 10,
                            "icd_code": {"$regex": "^C"},
                        }
                    }
                ],
                "as": "cancer_diagnoses",
            }
        },
        # 2. Keep only admissions with at least one cancer diagnosis
        {"$match": {"cancer_diagnoses": {"$ne": []}}},
        # 3. Optionally resolve diagnosis descriptions
        {
            "$lookup": {
                "from": "d_icd_diagnoses",
                "let": {
                    "codes": "$cancer_diagnoses.icd_code",
                    "versions": "$cancer_diagnoses.icd_version",
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$in": ["$icd_code", "$$codes"]},
                                    {"$eq": ["$icd_version", 10]},
                                ]
                            }
                        }
                    }
                ],
                "as": "cancer_descriptions",
            }
        },
        # 4. Project
        {
            "$project": {
                "_id": 0,
                "subject_id": 1,
                "hadm_id": 1,
                "admittime": 1,
                "dischtime": 1,
                "admission_type": 1,
                "insurance": 1,
                "race": 1,
                "hospital_expire_flag": 1,
                "cancer_diagnoses": 1,
                "cancer_descriptions": 1,
            }
        },
    ]
