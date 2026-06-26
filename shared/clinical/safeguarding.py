"""Children First Act 2015 safeguarding pattern detection.

Part 6.8 of the uplift. Auto-flags paediatric ED presentations that match
a small list of concerning patterns and fires a notification to Clinical
Chat via ``POST /safeguarding/notify``.

The patterns here are conservative placeholders — a production system
should plug into a HIQA-aligned triage rule engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from shared.clinical.clinical_record import is_paediatric


@dataclass
class SafeguardingAlert:
    hadm_id: str
    subject_id: str
    reasons: List[str]
    severity: str  # "routine" | "urgent"
    raised_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hadm_id": self.hadm_id,
            "subject_id": self.subject_id,
            "reasons": list(self.reasons),
            "severity": self.severity,
            "raised_at": self.raised_at,
        }


_NIGHT_HOURS = set(range(22, 24)) | set(range(0, 6))


def _is_night(ts: Optional[datetime]) -> bool:
    if ts is None:
        return False
    return ts.hour in _NIGHT_HOURS


def assess(patient: Dict[str, Any], *, now: Optional[datetime] = None) -> Optional[SafeguardingAlert]:
    """Return an alert if the paediatric presentation is concerning.

    Non-paediatric patients always return ``None``.
    """
    age = patient.get("age") or patient.get("age_years") or patient.get("anchor_age")
    if not is_paediatric(age):
        return None

    reasons: List[str] = []

    admittime = patient.get("admittime")
    if isinstance(admittime, datetime) and _is_night(admittime):
        reasons.append("night_time_presentation")

    prior = patient.get("prior_ed_visits_90d") or patient.get("prior_admissions") or 0
    try:
        if int(prior) >= 3:
            reasons.append("frequent_ed_presenter")
    except (TypeError, ValueError):
        pass

    chief = (patient.get("chief_complaint") or "").lower()
    if any(keyword in chief for keyword in ("unexplained", "unwitnessed", "fall from height", "burn")):
        reasons.append(f"concerning_chief_complaint:{chief[:40]}")

    if patient.get("social_flag") or patient.get("tusla_history"):
        reasons.append("tusla_or_social_history")

    if not reasons:
        return None

    severity = "urgent" if len(reasons) >= 2 else "routine"
    alert = SafeguardingAlert(
        hadm_id=str(patient.get("hadm_id", "unknown")),
        subject_id=str(patient.get("subject_id", "unknown")),
        reasons=reasons,
        severity=severity,
        raised_at=(now or datetime.utcnow()).isoformat(),
    )
    return alert


__all__ = ["SafeguardingAlert", "assess"]
