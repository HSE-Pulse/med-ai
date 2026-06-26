"""Plain-language mapper for patient-facing endpoints.

Implements the Irish Sign Language / accessibility requirement in Part 6.7
of the engineering uplift. When ``accessibility_mode=true`` is passed on
patient-facing endpoints (Patient Journey, Clinical Chat, Bed Mgmt), the
response payload is passed through this mapper to strip medical jargon.

This is a first-cut curated dictionary; a production system should source
ICD-10 descriptions from the WHO ICD-10 dataset.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional


# --------------------------------------------------------------------------- ICD-10 (curated subset — expand from WHO dataset in production)
_ICD10_PLAIN = {
    # Sepsis
    "A40": "Blood infection caused by streptococcal bacteria",
    "A41": "Blood infection caused by other bacteria",
    "R65": "Sepsis-related complications",
    # Respiratory
    "J18": "Pneumonia",
    "J44": "Long-term lung disease (COPD)",
    "J45": "Asthma",
    "J96": "Breathing failure",
    "I26": "Blood clot in the lungs",
    # Cardiac
    "I21": "Heart attack",
    "I20": "Chest pain from reduced blood flow to the heart",
    "I48": "Irregular heartbeat (atrial fibrillation)",
    "I50": "Heart failure",
    "I63": "Stroke caused by a blocked artery",
    "I61": "Stroke caused by bleeding in the brain",
    # GI / abdominal
    "K92": "Bleeding from the stomach or bowel",
    "K35": "Appendicitis",
    "K80": "Gallstones",
    # Endocrine
    "E10": "Type 1 diabetes",
    "E11": "Type 2 diabetes",
    "E87": "Electrolyte imbalance",
    # Renal
    "N17": "Sudden reduction in kidney function",
    "N18": "Long-term kidney disease",
    # Cancer (selected C codes)
    "C18": "Bowel cancer",
    "C34": "Lung cancer",
    "C50": "Breast cancer",
    "C61": "Prostate cancer",
    "C78": "Cancer that has spread to other organs",
    # Neuro
    "G40": "Epilepsy",
    "G45": "Mini-stroke (TIA)",
    # Trauma / injury
    "S72": "Broken hip",
    "S06": "Head injury",
}

# --------------------------------------------------------------------------- SOFA / risk
_SOFA_BANDS = (
    (0, 1, "No organ stress — normal observations"),
    (2, 5, "Mild organ stress — keep watching closely"),
    (6, 9, "Moderate organ stress — doctor should review soon"),
    (10, 14, "Severe organ stress — urgent review needed"),
    (15, 24, "Very severe organ failure — critical care attention required"),
)


def sofa_to_plain(score: Optional[float]) -> str:
    if score is None:
        return "Risk information not yet available"
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "Risk information not yet available"
    for lo, hi, message in _SOFA_BANDS:
        if lo <= s <= hi:
            return message
    return "Severe organ failure — critical care attention required"


# --------------------------------------------------------------------------- MTS (Manchester Triage)
_MTS_PLAIN = {
    1: "Seen immediately — life-threatening",
    2: "Seen within about 10 minutes — very urgent",
    3: "Seen within about 1 hour — urgent",
    4: "Seen within about 2 hours — standard wait",
    5: "Seen within about 4 hours — not urgent",
}


def mts_to_plain(category: Optional[int]) -> str:
    if category is None:
        return "Wait time not yet assessed"
    return _MTS_PLAIN.get(int(category), "Wait time not yet assessed")


# --------------------------------------------------------------------------- NEWS2
def news2_to_plain(total: Optional[int]) -> str:
    if total is None:
        return "Early warning score not yet calculated"
    if total >= 7:
        return "Early warning score is high — urgent senior doctor review needed"
    if total >= 5:
        return "Early warning score is raised — doctor review within 30 minutes"
    if total >= 1:
        return "Early warning score is slightly raised — ward team will monitor"
    return "Early warning score is normal"


# --------------------------------------------------------------------------- ICD lookup
def icd_to_plain(code: Optional[str]) -> str:
    if not code:
        return "Diagnosis not recorded"
    token = str(code).strip().upper()
    # Try progressively shorter prefixes (e.g. C18.2 -> C18)
    for length in range(len(token), 1, -1):
        prefix = token[:length]
        if prefix in _ICD10_PLAIN:
            return _ICD10_PLAIN[prefix]
        head = prefix.split(".")[0]
        if head in _ICD10_PLAIN:
            return _ICD10_PLAIN[head]
    return f"Medical code {token}"


# --------------------------------------------------------------------------- PlainLanguageMapper
_JARGON_REPLACEMENTS = [
    (re.compile(r"\bSOFA\b", re.I), "organ-stress score"),
    (re.compile(r"\bqSOFA\b", re.I), "quick organ-stress check"),
    (re.compile(r"\bNEWS2\b", re.I), "early warning score"),
    (re.compile(r"\bLOS\b"), "length of stay"),
    (re.compile(r"\bICU\b"), "intensive care unit"),
    (re.compile(r"\bHDU\b"), "high dependency unit"),
    (re.compile(r"\bMAU\b"), "medical assessment unit"),
    (re.compile(r"\bAMAU\b"), "acute medical assessment unit"),
    (re.compile(r"\bCDU\b"), "clinical decision unit"),
    (re.compile(r"\bED\b"), "emergency department"),
    (re.compile(r"\bPET\b"), "patient experience time (6-hour ED target)"),
    (re.compile(r"\bLWBS\b"), "left without being seen"),
    (re.compile(r"\bMTS\b"), "Manchester triage category"),
    (re.compile(r"\bNEDOCS\b"), "emergency department crowding score"),
]


class PlainLanguageMapper:
    """Strip medical jargon from free text and structured fields.

    Usage
    -----

    >>> mapper = PlainLanguageMapper()
    >>> mapper.transform({"icd_codes": ["A40"], "sofa": 7})
    {'icd_codes': ['Blood infection caused by streptococcal bacteria'],
     'sofa': 'Moderate organ stress — doctor should review soon'}
    """

    # --------- scalar transforms ---------
    @staticmethod
    def icd(code: str) -> str:
        return icd_to_plain(code)

    @staticmethod
    def sofa(score: float) -> str:
        return sofa_to_plain(score)

    @staticmethod
    def mts(category: int) -> str:
        return mts_to_plain(category)

    @staticmethod
    def news2(total: int) -> str:
        return news2_to_plain(total)

    @staticmethod
    def strip_jargon(text: str) -> str:
        if not text:
            return text
        for pattern, replacement in _JARGON_REPLACEMENTS:
            text = pattern.sub(replacement, text)
        return text

    # --------- structured transforms ---------
    def transform(self, obj: Any) -> Any:
        """Recursively transform a dict/list, replacing known jargon fields."""
        if isinstance(obj, dict):
            return {k: self._transform_field(k, v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.transform(v) for v in obj]
        if isinstance(obj, str):
            return self.strip_jargon(obj)
        return obj

    def _transform_field(self, key: str, value: Any) -> Any:
        lower = key.lower()
        if lower in {"icd_code", "icd"}:
            return icd_to_plain(str(value))
        if lower in {"icd_codes"} and isinstance(value, Iterable) and not isinstance(value, str):
            return [icd_to_plain(c) for c in value]
        if lower in {"sofa", "sofa_score", "sofa_total"}:
            return sofa_to_plain(value)
        if lower in {"mts", "mts_category", "triage_category"}:
            return mts_to_plain(value)
        if lower in {"news2", "news2_score", "news2_total"}:
            return news2_to_plain(value)
        return self.transform(value)


__all__ = [
    "PlainLanguageMapper",
    "icd_to_plain",
    "sofa_to_plain",
    "mts_to_plain",
    "news2_to_plain",
]
