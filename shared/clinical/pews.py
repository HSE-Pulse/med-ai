"""PEWS (Paediatric Early Warning Score) — Irish deterioration scoring for children.

Implements the age-band-aware PEWS specification aligned with the Irish
National Clinical Effectiveness Committee (NCEC) National Clinical Guideline
No. 1: *A National Paediatric Early Warning Score (PEWS) for Ireland*.

Scoring differs from NEWS2 because paediatric normal ranges are age-dependent.
Five age bands are used:

    0      - under 3 months
    1      - 3-12 months
    2      - 1-4 years
    3      - 5-11 years
    4      - 12-17 years (adolescent; thresholds closer to adult)

Aggregate score thresholds (NCEC NCG No. 1):
  - 0          routine observations
  - 1-2        increased monitoring (ward nurse to review)
  - 3-4        urgent clinical review (NCHD)
  - ≥5 or any single parameter = 3   immediate medical review + registrar

Notes
-----
The Irish PEWS chart (2015, revised 2019) also scores:
  - behaviour (alert/irritable/lethargic/unresponsive)
  - respiratory effort (none/mild/moderate/severe)
  - capillary refill time (≤2s/3s/≥4s)

Those qualitative parameters are supported but default to 0 when unknown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


# Normal vital-sign ranges per age band (heart rate, respiratory rate, systolic BP).
# Thresholds derived from RCPCH/NCEC PEWS chart. Values are inclusive boundaries
# for the "0-score" (normal) band. Outside these: see _score_* functions.
_AGE_BANDS: Tuple[Tuple[str, int, int], ...] = (
    # (label, lower_age_months_inclusive, upper_age_months_exclusive)
    ("0-3mo", 0, 3),
    ("3-12mo", 3, 12),
    ("1-4y", 12, 60),
    ("5-11y", 60, 144),
    ("12-17y", 144, 216),
)

# Heart-rate normal ranges (bpm) per age band.
_HR_NORMAL: Dict[str, Tuple[int, int]] = {
    "0-3mo": (100, 180),
    "3-12mo": (100, 170),
    "1-4y": (90, 150),
    "5-11y": (70, 130),
    "12-17y": (60, 120),
}

# Respiratory-rate normal ranges (breaths/min).
_RR_NORMAL: Dict[str, Tuple[int, int]] = {
    "0-3mo": (30, 60),
    "3-12mo": (25, 50),
    "1-4y": (20, 40),
    "5-11y": (16, 30),
    "12-17y": (12, 20),
}

# Systolic BP lower-safety thresholds (mmHg). Above upper is rarely paediatric emergency.
_SBP_NORMAL: Dict[str, Tuple[int, int]] = {
    "0-3mo": (65, 105),
    "3-12mo": (70, 110),
    "1-4y": (75, 115),
    "5-11y": (85, 120),
    "12-17y": (95, 135),
}


@dataclass
class PEWSResult:
    total: int
    components: Dict[str, int]
    age_band: str
    any_param_eq_3: bool
    risk_band: str
    recommended_response: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "components": self.components,
            "age_band": self.age_band,
            "any_param_eq_3": self.any_param_eq_3,
            "risk_band": self.risk_band,
            "recommended_response": self.recommended_response,
        }


def _pick_age_band(age_months: float) -> str:
    for label, lo, hi in _AGE_BANDS:
        if lo <= age_months < hi:
            return label
    # ≥18y clinically out-of-scope for PEWS — caller should route to NEWS2.
    return "12-17y"


def _score_hr(hr: Optional[float], band: str) -> int:
    if hr is None:
        return 0
    lo, hi = _HR_NORMAL[band]
    # 3-point zones: <= 0.7 * lo or >= 1.3 * hi (severe brady/tachycardia).
    if hr <= 0.7 * lo or hr >= 1.3 * hi:
        return 3
    # 2-point zones: outside normal but not severe.
    if hr < lo * 0.85 or hr > hi * 1.15:
        return 2
    # 1-point zones: edge-of-normal.
    if hr < lo or hr > hi:
        return 1
    return 0


def _score_rr(rr: Optional[float], band: str) -> int:
    if rr is None:
        return 0
    lo, hi = _RR_NORMAL[band]
    if rr <= 0.6 * lo or rr >= 1.5 * hi:
        return 3
    if rr < lo * 0.85 or rr > hi * 1.2:
        return 2
    if rr < lo or rr > hi:
        return 1
    return 0


def _score_sbp(sbp: Optional[float], band: str) -> int:
    if sbp is None:
        return 0
    lo, hi = _SBP_NORMAL[band]
    if sbp <= 0.8 * lo:
        return 3
    if sbp < lo:
        return 2
    if sbp > hi * 1.15:
        return 1
    return 0


def _score_spo2(spo2: Optional[float]) -> int:
    if spo2 is None:
        return 0
    if spo2 <= 90:
        return 3
    if spo2 <= 93:
        return 2
    if spo2 <= 95:
        return 1
    return 0


def _score_supplemental_o2(on_supplemental: bool) -> int:
    # On any supplemental O2 adds 1 point (PEWS differs from NEWS2's +2).
    return 1 if on_supplemental else 0


def _score_temp(temp_c: Optional[float]) -> int:
    if temp_c is None:
        return 0
    if temp_c <= 35.0 or temp_c >= 39.5:
        return 3
    if temp_c <= 35.5 or temp_c >= 38.5:
        return 2
    if temp_c <= 36.0 or temp_c >= 38.0:
        return 1
    return 0


def _score_behaviour(level: Optional[str]) -> int:
    """Paediatric AVPU + behaviour: Alert/Irritable/Lethargic/Unresponsive."""
    if not level:
        return 0
    normalised = level.strip().upper()
    if normalised in {"A", "ALERT", "PLAYING", "NORMAL"}:
        return 0
    if normalised in {"I", "IRRITABLE", "FUSSY"}:
        return 1
    if normalised in {"L", "LETHARGIC", "V", "VOICE"}:
        return 2
    # Unresponsive, seizing, or responds only to pain.
    return 3


def _score_respiratory_effort(effort: Optional[str]) -> int:
    """Accessory muscle use / retractions scoring."""
    if not effort:
        return 0
    e = effort.strip().upper()
    if e in {"NONE", "NORMAL"}:
        return 0
    if e == "MILD":
        return 1
    if e == "MODERATE":
        return 2
    # severe / grunting / head-bobbing / apnoea
    return 3


def _score_capillary_refill(crt_s: Optional[float]) -> int:
    if crt_s is None:
        return 0
    if crt_s >= 4:
        return 3
    if crt_s >= 3:
        return 1
    return 0


def _risk_band(total: int, any_three: bool) -> Tuple[str, str]:
    if total >= 5 or any_three:
        return (
            "high",
            "Immediate medical review by registrar/consultant; consider PICU outreach",
        )
    if total >= 3:
        return (
            "medium",
            "Urgent clinical review by NCHD within 30 minutes",
        )
    if total >= 1:
        return (
            "low",
            "Increased monitoring; ward nurse review; repeat obs within 1 hour",
        )
    return ("none", "Routine paediatric observations")


def compute_pews(
    *,
    age_months: float,
    heart_rate: Optional[float] = None,
    respiratory_rate: Optional[float] = None,
    spo2: Optional[float] = None,
    on_supplemental_o2: bool = False,
    systolic_bp: Optional[float] = None,
    temperature_c: Optional[float] = None,
    behaviour: Optional[str] = None,
    respiratory_effort: Optional[str] = None,
    capillary_refill_s: Optional[float] = None,
) -> PEWSResult:
    """Compute a PEWS result from paediatric vitals.

    ``age_months`` drives the age band used for HR/RR/SBP normal ranges.
    For adolescents (≥ 12 y) the adult-proximal band is used; callers whose
    patients are ≥ 18 y should route to :func:`shared.clinical.news2.compute_news2`
    instead.
    """
    band = _pick_age_band(age_months)
    components = {
        "heart_rate": _score_hr(heart_rate, band),
        "respiratory_rate": _score_rr(respiratory_rate, band),
        "spo2": _score_spo2(spo2),
        "supplemental_o2": _score_supplemental_o2(on_supplemental_o2),
        "systolic_bp": _score_sbp(systolic_bp, band),
        "temperature": _score_temp(temperature_c),
        "behaviour": _score_behaviour(behaviour),
        "respiratory_effort": _score_respiratory_effort(respiratory_effort),
        "capillary_refill": _score_capillary_refill(capillary_refill_s),
    }
    total = sum(components.values())
    any_three = any(v == 3 for v in components.values())
    risk, response = _risk_band(total, any_three)
    return PEWSResult(
        total=total,
        components=components,
        age_band=band,
        any_param_eq_3=any_three,
        risk_band=risk,
        recommended_response=response,
    )


__all__ = ["compute_pews", "PEWSResult"]
