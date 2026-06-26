"""Pydantic schemas for ED Flow Optimizer API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Irish ED Configuration
# ---------------------------------------------------------------------------

from shared.constants.hospital import MTS_CATEGORIES, PET_TARGET_HOURS, NEDOCS_THRESHOLDS

ED_TRACKS = ["resuscitation", "acute", "rapid_assessment", "fast_track", "ambulatory"]


# ---------------------------------------------------------------------------
# Request Schemas
# ---------------------------------------------------------------------------

class PatientEventRequest(BaseModel):
    """Log an ED event for a patient (triage, labs, imaging, etc.)."""
    event_type: str  # triage, labs_ordered, labs_resulted, imaging_ordered,
                     # imaging_resulted, consult_requested, consult_completed,
                     # treatment, disposition_decision, admitted, discharged
    timestamp: Optional[datetime] = None
    details: Optional[Dict[str, Any]] = None


class WhatIfScenarioRequest(BaseModel):
    """Run a what-if simulation scenario."""
    scenario_type: str  # add_doctor, add_beds, divert_ambulances,
                        # open_overflow, reduce_lab_tat
    parameter_value: float = 1.0  # e.g., number of doctors to add
    simulation_hours: float = 8.0


# ---------------------------------------------------------------------------
# Response Schemas
# ---------------------------------------------------------------------------

class EDPatientFlow(BaseModel):
    """Current state and predictions for an ED patient."""
    patient_id: int
    hadm_id: Optional[int] = None
    arrival_time: Optional[datetime] = None
    mts_category: int = 3
    mts_name: str = "Urgent"
    mts_color: str = "Yellow"
    track: str = "acute"
    current_status: str = "waiting"  # waiting, in_treatment, boarding, discharged
    current_location: str = "ED_Waiting"

    # Time predictions
    time_in_ed_minutes: float = 0
    predicted_disposition_time: Optional[datetime] = None
    predicted_ed_los_minutes: float = 0
    pet_remaining_minutes: float = 360  # 6 hours = 360 minutes
    pet_breach_risk: float = 0.0
    lwbs_risk: float = 0.0

    # Disposition prediction
    predicted_disposition: str = "pending"  # admit, discharge, transfer, lwbs
    disposition_confidence: float = 0.0
    admission_probability: float = 0.0

    # Current bottleneck
    current_bottleneck: Optional[str] = None  # labs, imaging, consult, beds
    bottleneck_wait_minutes: float = 0

    # Events timeline
    events: List[Dict[str, Any]] = []


class EDState(BaseModel):
    """Current state of the Emergency Department."""
    timestamp: Optional[datetime] = None
    total_patients: int = 0
    waiting_count: int = 0
    in_treatment_count: int = 0
    boarding_count: int = 0
    resus_occupied: int = 0
    resus_capacity: int = 3

    # By MTS category
    patients_by_mts: Dict[str, int] = {}

    # Timing metrics
    avg_wait_minutes: float = 0
    longest_wait_minutes: float = 0
    avg_los_minutes: float = 0

    # Crowding
    nedocs_score: float = 0
    crowding_level: str = "normal"  # normal, busy, crowded, severe

    # PET compliance
    pet_compliance_rate: float = 1.0
    patients_at_pet_risk: int = 0

    # LWBS
    lwbs_count_today: int = 0
    lwbs_rate: float = 0.0


class SurgeForecast(BaseModel):
    """ED surge/demand forecast."""
    forecast_time: Optional[datetime] = None
    horizon_hours: int = 4
    predicted_arrivals: float = 0
    predicted_arrivals_lower: float = 0
    predicted_arrivals_upper: float = 0
    predicted_census: float = 0
    predicted_nedocs: float = 0
    predicted_crowding_level: str = "normal"
    surge_probability: float = 0.0


class Bottleneck(BaseModel):
    """Identified ED bottleneck with causal attribution."""
    bottleneck_type: str  # labs, imaging, consult, beds, nursing, registration
    severity: str = "moderate"  # mild, moderate, severe
    affected_patients: int = 0
    avg_delay_minutes: float = 0
    causal_impact_on_los: float = 0.0  # Minutes of LOS attributable to this bottleneck
    is_actionable: bool = True
    recommended_action: str = ""


class EDRecommendation(BaseModel):
    """Actionable recommendation for ED operations."""
    recommendation_type: str  # staffing, surge, flow, escalation
    priority: str = "medium"  # low, medium, high, critical
    title: str = ""
    description: str = ""
    expected_impact: str = ""
    estimated_time_saved_minutes: float = 0


class WhatIfResult(BaseModel):
    """Result of a what-if simulation."""
    scenario_type: str
    parameter_value: float
    simulation_hours: float
    baseline_avg_los: float = 0
    simulated_avg_los: float = 0
    los_reduction_minutes: float = 0
    baseline_pet_compliance: float = 0
    simulated_pet_compliance: float = 0
    baseline_lwbs_rate: float = 0
    simulated_lwbs_rate: float = 0
    summary: str = ""


class PETCompliance(BaseModel):
    """PET (Patient Experience Time) compliance metrics."""
    period: str = "today"
    total_patients: int = 0
    within_6_hours: int = 0
    compliance_rate: float = 0.0
    avg_pet_minutes: float = 0.0
    median_pet_minutes: float = 0.0
    p95_pet_minutes: float = 0.0
    breach_count: int = 0
    breaches_by_mts: Dict[str, int] = {}
