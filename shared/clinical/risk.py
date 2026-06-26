"""Shared clinical risk assessment utilities.

Consolidates gender encoding, risk-level classification, risk factor
identification, temperature conversion, and SOFA scoring used across
ED Triage, Sepsis ICU, Oncology AI, and other modules.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from shared.constants.mimic import RISK_COLORS


# ---------------------------------------------------------------------------
# Gender encoding
# ---------------------------------------------------------------------------

def encode_gender(gender: str) -> int:
    """Encode gender as binary (M=1, F=0). Handles various input formats."""
    return 1 if gender.upper().startswith("M") else 0


# ---------------------------------------------------------------------------
# Temperature conversion
# ---------------------------------------------------------------------------

def fahrenheit_to_celsius(temp: Optional[float]) -> Optional[float]:
    """Convert Fahrenheit to Celsius if value appears to be in Fahrenheit (>50)."""
    if temp is None:
        return None
    if temp > 50:
        return (temp - 32) * 5 / 9
    return temp


# ---------------------------------------------------------------------------
# Risk level classification
# ---------------------------------------------------------------------------

def get_risk_level(score: float) -> Tuple[str, str]:
    """Convert a 0-1 risk score to (level_name, hex_color).

    Thresholds: >=0.75 critical, >=0.50 high, >=0.25 moderate, else low.
    """
    if score >= 0.75:
        return "critical", RISK_COLORS["critical"]
    elif score >= 0.50:
        return "high", RISK_COLORS["high"]
    elif score >= 0.25:
        return "moderate", RISK_COLORS["moderate"]
    return "low", RISK_COLORS["low"]


# ---------------------------------------------------------------------------
# Vital-sign risk factor identification
# ---------------------------------------------------------------------------

def identify_vital_risk_factors(
    *,
    heart_rate: Optional[float] = None,
    respiratory_rate: Optional[float] = None,
    spo2: Optional[float] = None,
    sbp: Optional[float] = None,
    temperature: Optional[float] = None,
    lactate: Optional[float] = None,
    wbc: Optional[float] = None,
    creatinine: Optional[float] = None,
    age: Optional[float] = None,
) -> List[str]:
    """Identify clinical risk factors from vital signs and basic labs.

    Returns a list of human-readable risk factor strings.
    Used by ED Triage, Sepsis ICU, and other modules.
    """
    risks: List[str] = []

    if heart_rate is not None:
        if heart_rate > 120:
            risks.append("Tachycardia (HR > 120)")
        elif heart_rate < 50:
            risks.append("Bradycardia (HR < 50)")

    if respiratory_rate is not None and respiratory_rate > 24:
        risks.append("Tachypnea (RR > 24)")

    if spo2 is not None and spo2 < 92:
        risks.append(f"Hypoxemia (SpO2 = {spo2}%)")

    if sbp is not None:
        if sbp < 90:
            risks.append(f"Hypotension (SBP = {sbp} mmHg)")
        elif sbp > 180:
            risks.append(f"Hypertensive crisis (SBP = {sbp} mmHg)")

    if temperature is not None:
        if temperature > 38.5:
            risks.append(f"Fever ({temperature} C)")
        elif temperature < 35.0:
            risks.append(f"Hypothermia ({temperature} C)")

    if lactate is not None and lactate > 2.0:
        risks.append(f"Elevated lactate ({lactate} mmol/L)")

    if wbc is not None:
        if wbc > 12.0:
            risks.append(f"Leukocytosis (WBC = {wbc} K/uL)")
        elif wbc < 4.0:
            risks.append(f"Leukopenia (WBC = {wbc} K/uL)")

    if creatinine is not None and creatinine > 1.5:
        risks.append(f"Elevated creatinine ({creatinine} mg/dL)")

    if age is not None and age > 75:
        risks.append("Advanced age (>75 years)")

    return risks


# ---------------------------------------------------------------------------
# SOFA score computation
# ---------------------------------------------------------------------------

def sofa_resp(spo2: Optional[float]) -> int:
    """SOFA respiration component from SpO2."""
    if spo2 is None:
        return 0
    if spo2 < 92:
        return 3
    if spo2 <= 96:
        return 1
    return 0


def sofa_coag(platelets: Optional[float]) -> int:
    """SOFA coagulation component from platelet count."""
    if platelets is None:
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


def sofa_liver(bilirubin: Optional[float]) -> int:
    """SOFA liver component from bilirubin."""
    if bilirubin is None:
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


def sofa_cardio(mbp: Optional[float]) -> int:
    """SOFA cardiovascular component from mean arterial pressure."""
    if mbp is None:
        return 0
    if mbp < 70:
        return 1
    return 0


def sofa_renal(creatinine: Optional[float]) -> int:
    """SOFA renal component from creatinine."""
    if creatinine is None:
        return 0
    if creatinine > 5:
        return 4
    if creatinine >= 3.5:
        return 3
    if creatinine >= 2.0:
        return 2
    if creatinine >= 1.2:
        return 1
    return 0


def _as_float(v: Any) -> Optional[float]:
    """Coerce CSV-imported strings (e.g. "", "___", "12.5") to float; None on failure."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def compute_sofa(vitals: Dict[str, Any], labs: Dict[str, Any]) -> Dict[str, int]:
    """Compute full SOFA breakdown from vitals and labs dicts.

    Returns dict with keys: respiration, coagulation, liver,
    cardiovascular, renal, total.
    """
    resp = sofa_resp(_as_float(vitals.get("spo2")))
    coag = sofa_coag(_as_float(labs.get("platelets")))
    liver = sofa_liver(_as_float(labs.get("bilirubin")))
    cardio = sofa_cardio(_as_float(vitals.get("mbp")))
    renal = sofa_renal(_as_float(labs.get("creatinine")))
    return {
        "respiration": resp,
        "coagulation": coag,
        "liver": liver,
        "cardiovascular": cardio,
        "renal": renal,
        "total": resp + coag + liver + cardio + renal,
    }


# ---------------------------------------------------------------------------
# Acuity calculation (rule-based)
# ---------------------------------------------------------------------------

def rule_based_acuity(
    *,
    spo2: Optional[float] = None,
    sbp: Optional[float] = None,
    hr: Optional[float] = None,
    has_vitals: bool = True,
) -> int:
    """Rule-based ESI-equivalent acuity (1=most critical, 5=least).

    Used as fallback when no ML model is available and by the
    simulation engine for inline acuity classification.
    """
    if spo2 is not None and spo2 < 90:
        return 1
    if sbp is not None and sbp < 80:
        return 1
    if hr is not None and (hr < 40 or hr > 150):
        return 2
    if spo2 is not None and spo2 < 94:
        return 2
    if hr is not None and hr > 100:
        return 3
    if not has_vitals:
        return 4
    return 3
