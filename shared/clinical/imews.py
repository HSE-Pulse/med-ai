"""IMEWS (Irish Maternity Early Warning Score) — obstetric deterioration scoring.

Implements the Irish National Clinical Effectiveness Committee (NCEC) National
Clinical Guideline No. 4: *The Irish Maternity Early Warning System (IMEWS)*.

IMEWS is mandatory for all pregnant patients from booking through six weeks
post-partum across public maternity services. It differs from NEWS2 in that
physiological norms in pregnancy are different (higher HR, lower SBP, higher
RR late in pregnancy), and the escalation thresholds are tuned for maternal
collapse, pre-eclampsia, sepsis, and haemorrhage.

IMEWS uses a traffic-light triggering system:
  - yellow trigger (any one yellow parameter) → midwife-led review
  - pink trigger (any one pink parameter OR two yellow parameters)
      → obstetric registrar + anaesthetic review within 30 minutes
  - two+ pink triggers → medical emergency team (MET) call

Mapped onto an aggregate numeric score compatible with the existing
deterioration API: yellow = 1 per parameter, pink = 3 per parameter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass
class IMEWSResult:
    total: int
    components: Dict[str, int]
    yellow_triggers: int
    pink_triggers: int
    any_pink: bool
    risk_band: str
    recommended_response: str
    gestational_context: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "components": self.components,
            "yellow_triggers": self.yellow_triggers,
            "pink_triggers": self.pink_triggers,
            "any_pink": self.any_pink,
            "risk_band": self.risk_band,
            "recommended_response": self.recommended_response,
            "gestational_context": self.gestational_context,
        }


# ---------------------------------------------------------------------------
# Per-parameter triggers (from IMEWS chart, NCEC NCG #4, 2014 rev. 2019)
# Returns: 0 (normal), 1 (yellow), 3 (pink)
# ---------------------------------------------------------------------------
def _score_rr(rr: Optional[float]) -> int:
    if rr is None:
        return 0
    if rr < 10 or rr > 30:
        return 3  # pink
    if rr < 12 or rr > 20:
        return 1  # yellow
    return 0


def _score_spo2(spo2: Optional[float]) -> int:
    if spo2 is None:
        return 0
    if spo2 < 95:
        return 3  # pink — pregnancy-specific threshold higher than NEWS2
    if spo2 < 97:
        return 1
    return 0


def _score_temp(t: Optional[float]) -> int:
    if t is None:
        return 0
    if t < 35.0 or t >= 38.0:
        return 3  # pink — sepsis/chorioamnionitis threshold
    if t < 36.0 or t >= 37.5:
        return 1
    return 0


def _score_sbp(sbp: Optional[float]) -> int:
    if sbp is None:
        return 0
    # Pink: severe hypotension (haemorrhage) or severe hypertension (pre-eclampsia)
    if sbp < 90 or sbp >= 160:
        return 3
    if sbp < 100 or sbp >= 150:
        return 1
    return 0


def _score_dbp(dbp: Optional[float]) -> int:
    if dbp is None:
        return 0
    # IMEWS tracks DBP explicitly — pre-eclampsia marker
    if dbp >= 110:
        return 3
    if dbp >= 90:
        return 1
    return 0


def _score_hr(hr: Optional[float]) -> int:
    if hr is None:
        return 0
    if hr < 50 or hr >= 120:
        return 3  # pink — maternal collapse
    if hr < 60 or hr >= 100:
        return 1
    return 0


def _score_consciousness(level: Optional[str]) -> int:
    """ACVPU as used on IMEWS chart."""
    if not level:
        return 0
    n = level.strip().upper()
    if n in {"A", "ALERT"}:
        return 0
    # Anything other than alert is a pink trigger in IMEWS
    return 3


def _score_proteinuria(level: Optional[str]) -> int:
    """Proteinuria dipstick result — pre-eclampsia screening.

    ``level`` expected one of: "negative", "trace", "1+", "2+", "3+", "4+"
    """
    if not level:
        return 0
    n = level.strip().replace(" ", "").upper()
    if n in {"NEGATIVE", "NEG", "0"}:
        return 0
    if n in {"TRACE", "1+", "+"}:
        return 1
    # 2+, 3+, 4+ — significant proteinuria, pre-eclampsia concern
    return 3


def _score_liquor(state: Optional[str]) -> int:
    """Amniotic fluid colour — meconium staining is yellow/pink."""
    if not state:
        return 0
    s = state.strip().upper()
    if s in {"CLEAR", "NORMAL"}:
        return 0
    if s in {"PINK", "BLOOD_STAINED", "BLOOD-STAINED"}:
        return 1
    # heavy meconium, fresh blood
    return 3


def _score_lochia(state: Optional[str]) -> int:
    """Post-partum lochia — heavy / clots = yellow/pink (haemorrhage)."""
    if not state:
        return 0
    s = state.strip().upper()
    if s in {"MINIMAL", "NORMAL", "LIGHT"}:
        return 0
    if s in {"MODERATE", "CLOTS"}:
        return 1
    # heavy, flooding
    return 3


def _gestational_context(
    gestation_weeks: Optional[float],
    post_partum_days: Optional[float],
) -> str:
    if post_partum_days is not None and post_partum_days >= 0:
        if post_partum_days <= 1:
            return "immediate_postpartum"
        if post_partum_days <= 7:
            return "early_postpartum"
        if post_partum_days <= 42:
            return "postpartum_6wk"
        return "postpartum_late"
    if gestation_weeks is None:
        return "pregnancy_unknown_gestation"
    if gestation_weeks < 12:
        return "first_trimester"
    if gestation_weeks < 28:
        return "second_trimester"
    if gestation_weeks < 37:
        return "third_trimester_preterm"
    return "term"


def _risk_band(
    yellow: int,
    pink: int,
    any_pink: bool,
) -> Tuple[str, str]:
    if pink >= 2:
        return (
            "critical",
            "Maternal emergency — activate MET / obstetric emergency call",
        )
    if any_pink or yellow >= 2:
        return (
            "high",
            "Obstetric registrar + anaesthetic review within 30 minutes",
        )
    if yellow == 1:
        return (
            "medium",
            "Midwife in charge to review; repeat observations within 30 minutes",
        )
    return ("none", "Routine maternity observations per local protocol")


def compute_imews(
    *,
    respiratory_rate: Optional[float] = None,
    spo2: Optional[float] = None,
    temperature_c: Optional[float] = None,
    systolic_bp: Optional[float] = None,
    diastolic_bp: Optional[float] = None,
    heart_rate: Optional[float] = None,
    consciousness: Optional[str] = None,
    proteinuria: Optional[str] = None,
    liquor: Optional[str] = None,
    lochia: Optional[str] = None,
    gestation_weeks: Optional[float] = None,
    post_partum_days: Optional[float] = None,
) -> IMEWSResult:
    """Compute an IMEWS result for a pregnant or post-partum patient.

    At least one of ``gestation_weeks`` or ``post_partum_days`` should be
    provided — both govern which obstetric-specific rules (proteinuria, liquor,
    lochia) are meaningful. Missing values default to 0 (not scoring).
    """
    components = {
        "respiratory_rate": _score_rr(respiratory_rate),
        "spo2": _score_spo2(spo2),
        "temperature": _score_temp(temperature_c),
        "systolic_bp": _score_sbp(systolic_bp),
        "diastolic_bp": _score_dbp(diastolic_bp),
        "heart_rate": _score_hr(heart_rate),
        "consciousness": _score_consciousness(consciousness),
        "proteinuria": _score_proteinuria(proteinuria),
        "liquor": _score_liquor(liquor),
        "lochia": _score_lochia(lochia),
    }
    pink_triggers = sum(1 for v in components.values() if v == 3)
    yellow_triggers = sum(1 for v in components.values() if v == 1)
    any_pink = pink_triggers >= 1
    total = sum(components.values())
    band, response = _risk_band(yellow_triggers, pink_triggers, any_pink)
    context = _gestational_context(gestation_weeks, post_partum_days)
    return IMEWSResult(
        total=total,
        components=components,
        yellow_triggers=yellow_triggers,
        pink_triggers=pink_triggers,
        any_pink=any_pink,
        risk_band=band,
        recommended_response=response,
        gestational_context=context,
    )


__all__ = ["compute_imews", "IMEWSResult"]
