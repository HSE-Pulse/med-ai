"""Shared clinical keyword lists for NLP-based entity extraction.

Consolidates medication, symptom, and specialty term lists used
across Waiting List (app_09) and Clinical Scribe (app_10).
"""

from __future__ import annotations

from typing import List

# ---------------------------------------------------------------------------
# Medication terms (superset of app_09 + app_10 lists)
# ---------------------------------------------------------------------------

MEDICATION_TERMS: List[str] = [
    "paracetamol", "ibuprofen", "aspirin", "amoxicillin", "metformin",
    "amlodipine", "ramipril", "omeprazole", "salbutamol", "prednisolone",
]

# ---------------------------------------------------------------------------
# Symptom terms (superset of app_09 + app_10 lists)
# ---------------------------------------------------------------------------

SYMPTOM_TERMS: List[str] = [
    "pain", "swelling", "fever", "cough", "breathlessness",
    "nausea", "vomiting", "headache", "dizziness", "fatigue",
    "difficulty", "unable", "weakness", "numbness", "bleeding",
]

# ---------------------------------------------------------------------------
# Specialty detection keywords
# ---------------------------------------------------------------------------

CANCER_TERMS: List[str] = [
    "cancer", "carcinoma", "tumour", "tumor", "malignant", "neoplasm",
]

CARDIAC_TERMS: List[str] = [
    "chest pain", "angina", "mi", "heart failure", "cardiac",
]

ORTHO_TERMS: List[str] = [
    "hip", "knee", "fracture", "joint", "arthritis", "replacement",
]

SPECIALTY_KEYWORDS = {
    "Oncology": CANCER_TERMS,
    "Cardiology": CARDIAC_TERMS,
    "Orthopaedics": ORTHO_TERMS,
}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def find_matches(text: str, terms: List[str]) -> List[str]:
    """Return all terms found in text (case-insensitive)."""
    lower = text.lower()
    return [t for t in terms if t in lower]


def detect_specialty(text: str) -> str | None:
    """Detect medical specialty from text using keyword matching."""
    lower = text.lower()
    for specialty, terms in SPECIALTY_KEYWORDS.items():
        if any(t in lower for t in terms):
            return specialty
    return None
