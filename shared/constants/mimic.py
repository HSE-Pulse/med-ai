"""Canonical MIMIC-IV item ID mappings for vitals and labs.

Single source of truth — all modules should import from here
instead of defining their own mappings.
"""

from __future__ import annotations

from typing import Dict, List

# ---------------------------------------------------------------------------
# Vital signs — chartevents
# ---------------------------------------------------------------------------

VITAL_ITEMIDS: Dict[str, int] = {
    "Heart Rate": 220045,
    "Respiratory Rate": 220210,
    "SpO2": 220277,
    "SBP": 220179,
    "DBP": 220180,
    "Temperature": 223761,
    "Mean BP": 220181,
}

VITAL_ITEMID_TO_NAME: Dict[int, str] = {
    220045: "heart_rate",
    220210: "respiratory_rate",
    220277: "spo2",
    220179: "sbp",
    220180: "dbp",
    223761: "temperature",
    220181: "mbp",
}

VITAL_ITEMID_TO_LABEL: Dict[int, str] = {v: k for k, v in VITAL_ITEMIDS.items()}

VITAL_ITEM_IDS: List[int] = list(VITAL_ITEMID_TO_NAME.keys())

# Short names used by the simulation dashboard (ed-board, icu-board, etc.)
VITAL_ITEMID_TO_SHORT: Dict[int, str] = {
    220045: "hr",
    220210: "rr",
    220277: "spo2",
    220179: "sbp",
    220180: "dbp",
    223761: "temp",
}

# ---------------------------------------------------------------------------
# Lab results — labevents
# ---------------------------------------------------------------------------

LAB_ITEMIDS: Dict[str, int] = {
    "WBC": 51301,
    "Hemoglobin": 51222,
    "Platelets": 51265,
    "Sodium": 50983,
    "Potassium": 50971,
    "Creatinine": 50912,
    "BUN": 51006,
    "Glucose": 50931,
    "Lactate": 50813,
    "Bilirubin": 50885,
    "Troponin": 51003,
    "INR": 51237,
}

LAB_ITEMID_TO_NAME: Dict[int, str] = {
    # Hematology
    51301: "wbc",
    51222: "hemoglobin",
    51265: "platelets",
    51221: "hematocrit",
    51249: "mch",
    51279: "rbc",
    # Chemistry — basic metabolic panel
    50931: "glucose",
    50912: "creatinine",
    51006: "bun",
    50983: "sodium",
    50971: "potassium",
    50902: "chloride",
    50882: "bicarbonate",
    50893: "calcium",
    50960: "magnesium",
    50970: "phosphate",
    # Liver / bilirubin
    50885: "bilirubin",
    50878: "ast",
    50861: "alt",
    50863: "alkaline_phosphatase",
    50862: "albumin",
    # Cardiac / sepsis-relevant
    50813: "lactate",
    51003: "troponin",
    51237: "inr",
    51275: "ptt",
    51274: "pt",
    # Blood gas
    50820: "ph",
    50818: "pco2",
    50821: "po2",
    50802: "base_excess",
}

LAB_ITEMID_TO_LABEL: Dict[int, str] = {v: k for k, v in LAB_ITEMIDS.items()}

LAB_ITEM_IDS: List[int] = list(LAB_ITEMID_TO_NAME.keys())

# SOFA-relevant labs
SOFA_LAB_IDS: List[int] = [51265, 50885, 50912]  # platelets, bilirubin, creatinine

# ---------------------------------------------------------------------------
# Risk-level colors (shared across triage, sepsis, oncology)
# ---------------------------------------------------------------------------

RISK_COLORS = {
    "critical": "#DC2626",  # red-600
    "high": "#F97316",      # orange-500
    "moderate": "#EAB308",  # yellow-500
    "low": "#22C55E",       # green-500
    "info": "#3B82F6",      # blue-500
}

ACUITY_COLORS = ["#DC2626", "#F97316", "#EAB308", "#22C55E", "#3B82F6"]
ACUITY_LABELS = ["Resuscitation", "Emergent", "Urgent", "Less Urgent", "Non-Urgent"]
