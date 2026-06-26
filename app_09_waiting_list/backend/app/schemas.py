"""Pydantic schemas for Waiting List Intelligence API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Irish Specialty Configuration
# ---------------------------------------------------------------------------

# Waiting-list departments must mirror the model-hospital ward roster defined
# in ``shared/constants/hospital.py``. Only departments that accept elective /
# scheduled referrals are listed here — ED/MAU/AMAU/SAU/CDU are unscheduled,
# ICU/HDU/Discharge_Lounge do not hold referral queues. Target wait weeks are
# aligned to HSE SDU / Sláintecare: 10d urgent, 12w routine inpatient, 13w
# outpatient, 4w cancer pathway.
IRISH_SPECIALTIES = {
    "Medicine": {"target_wait_weeks": 12, "inpatient_pct": 0.7,
                 "role": "General medicine", "hospital_ward": "Medicine"},
    "Surgery": {"target_wait_weeks": 12, "inpatient_pct": 0.6,
                "role": "General surgery", "hospital_ward": "Surgery"},
    "Cardiology": {"target_wait_weeks": 8, "inpatient_pct": 0.5,
                   "role": "Cardiology", "hospital_ward": "Cardiology"},
    "Respiratory": {"target_wait_weeks": 10, "inpatient_pct": 0.5,
                    "role": "Respiratory medicine", "hospital_ward": "Respiratory"},
    "Orthopaedics": {"target_wait_weeks": 12, "inpatient_pct": 0.7,
                     "role": "Orthopaedics + trauma", "hospital_ward": "Orthopaedics"},
    "Day_Ward": {"target_wait_weeks": 12, "inpatient_pct": 0.1,
                 "role": "Day case / ambulatory procedures", "hospital_ward": "Day_Ward"},
}

PRIORITY_LEVELS = ["urgent", "soon", "routine", "planned"]
PATIENT_STATUSES = ["waiting", "scheduled", "completed", "cancelled", "deteriorated"]


# ---------------------------------------------------------------------------
# Request Schemas
# ---------------------------------------------------------------------------

class AddToWaitingListRequest(BaseModel):
    """Add a patient to the waiting list."""
    patient_id: int
    specialty: str
    procedure_requested: str
    referring_clinician: str = ""
    referral_text: Optional[str] = None
    clinical_urgency: Optional[str] = None  # urgent, soon, routine, planned
    diagnosis: Optional[str] = None
    age: Optional[float] = None
    gender: Optional[str] = None
    comorbidity_count: int = 0
    charlson_score: float = 0.0
    pain_score: Optional[float] = Field(None, ge=0, le=10)
    functional_impact: Optional[str] = None  # none, mild, moderate, severe, complete
    geographic_region: Optional[str] = None


class ClinicalUpdateRequest(BaseModel):
    """Update clinical state of a waiting patient."""
    patient_id: int
    new_vitals: Optional[Dict[str, float]] = None
    new_labs: Optional[Dict[str, float]] = None
    new_symptoms: Optional[List[str]] = None
    pain_score: Optional[float] = None
    functional_impact: Optional[str] = None
    clinical_notes: Optional[str] = None


class ReferralTriageRequest(BaseModel):
    """Submit referral letter for NLP triage."""
    referral_text: str
    referring_gp: Optional[str] = None
    patient_age: Optional[float] = None
    patient_gender: Optional[str] = None


class GenerateScheduleRequest(BaseModel):
    """Request to generate optimal weekly schedule."""
    specialty: str
    week_start_date: str  # YYYY-MM-DD
    available_slots: Optional[int] = None
    surgeon_availability: Optional[Dict[str, List[str]]] = None


# ---------------------------------------------------------------------------
# Response Schemas
# ---------------------------------------------------------------------------

class PriorityScore(BaseModel):
    """Composite priority score breakdown."""
    clinical_urgency_score: float = Field(..., ge=0, le=1)
    functional_impact_score: float = Field(..., ge=0, le=1)
    temporal_score: float = Field(..., ge=0, le=1)
    equity_modifier: float = Field(..., ge=-0.2, le=0.2)
    composite_priority: float = Field(..., ge=0, le=1)
    priority_rank: Optional[int] = None
    priority_level: str = "routine"  # urgent, soon, routine, planned


class WaitingListEntry(BaseModel):
    """A patient on the waiting list."""
    patient_id: int
    specialty: str
    procedure_requested: str
    referral_date: Optional[datetime] = None
    wait_days: int = 0
    target_wait_days: int = 84
    priority: PriorityScore
    deterioration_risk_30d: float = 0.0
    deterioration_risk_90d: float = 0.0
    predicted_wait_days: Optional[int] = None
    status: str = "waiting"
    scheduled_date: Optional[datetime] = None
    nlp_extracted: Optional[Dict[str, Any]] = None


class DeteriorationRisk(BaseModel):
    """Deterioration risk assessment for a waiting patient."""
    patient_id: int
    risk_30d: float = 0.0
    risk_90d: float = 0.0
    risk_180d: float = 0.0
    risk_trajectory: str = "stable"  # improving, stable, worsening
    competing_risks: Dict[str, float] = {}  # deterioration, improvement, dropout
    recommended_action: str = ""
    next_review_date: Optional[datetime] = None


class ReferralTriageResult(BaseModel):
    """NLP triage result for a referral letter."""
    urgency_classification: str = "routine"
    urgency_confidence: float = 0.0
    recommended_specialty: str = ""
    extracted_entities: Dict[str, Any] = {}
    missing_information: List[str] = []
    referral_quality_score: float = 0.0
    suggested_priority_level: str = "routine"


class ScheduleSlot(BaseModel):
    """A scheduled slot for a procedure."""
    slot_date: str
    slot_time: str
    specialty: str
    resource: str
    surgeon: Optional[str] = None
    assigned_patient_id: Optional[int] = None
    assignment_score: float = 0.0
    status: str = "available"


class WaitTimeStats(BaseModel):
    """Waiting time statistics for a specialty."""
    specialty: str
    total_waiting: int
    median_wait_days: float
    p95_wait_days: float
    within_target_pct: float
    breach_count: int
    target_wait_weeks: int
