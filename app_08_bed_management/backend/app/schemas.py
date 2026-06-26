"""Pydantic schemas for Bed Management API requests and responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Department & Bed Configuration (Irish Hospital)
# ---------------------------------------------------------------------------

from shared.constants.hospital import CAPACITIES, DEPARTMENT_TYPES, BED_STATUSES, ALERT_LEVELS

IRISH_DEPARTMENTS = {name: {"capacity": cap, "type": DEPARTMENT_TYPES[name]} for name, cap in CAPACITIES.items()}


# ---------------------------------------------------------------------------
# Request Schemas
# ---------------------------------------------------------------------------

class DischargePredictionRequest(BaseModel):
    """Request for discharge prediction of a single patient."""
    patient_id: Any
    hadm_id: Any
    department: str
    admission_time: Optional[datetime] = None
    sim_time: Optional[datetime] = None  # Simulation clock (use instead of wall-clock)
    age: float
    gender: str
    primary_diagnosis: Optional[str] = None
    current_vitals: Optional[Dict[str, float]] = None
    current_labs: Optional[Dict[str, float]] = None
    news2_score: Optional[float] = None
    active_medications_count: Optional[int] = None
    has_iv: bool = False
    has_oxygen: bool = False
    procedures_pending: int = 0


class BedUpdateRequest(BaseModel):
    """Request to update a bed's status."""
    status: str = Field(..., description="New bed status")
    patient_id: Optional[Any] = None
    hadm_id: Optional[Any] = None
    acuity: Optional[float] = None
    reason: Optional[str] = None


class BedAllocationRequest(BaseModel):
    """Request for optimal bed allocation."""
    patient_id: Any
    hadm_id: Any
    acuity: float = Field(..., ge=1.0, le=5.0)
    department_preference: Optional[str] = None
    requires_isolation: bool = False
    requires_monitoring: bool = False
    requires_bariatric: bool = False
    requires_paediatric: bool = False
    requires_maternity: bool = False
    requires_stroke_capable: bool = False
    gender: str = "U"
    sim_time: Optional[datetime] = None  # Simulation clock
    admission_type: str = "EMERGENCY"


# ---------------------------------------------------------------------------
# Response Schemas
# ---------------------------------------------------------------------------

class BedState(BaseModel):
    """Current state of a single bed.

    ``bed_type`` reflects the department type (emergency, inpatient, critical,
    …). ``category`` carries the orthogonal clinical-suitability taxonomy
    (isolation, paediatric, bariatric, stroke_thrombolysis, …) used for
    allocation matching.
    """
    bed_id: str
    department: str
    bed_type: str = "general"
    category: str = "general"
    status: str
    patient_id: Optional[Any] = None
    hadm_id: Optional[Any] = None
    acuity: Optional[float] = None
    admission_time: Optional[datetime] = None
    predicted_discharge: Optional[datetime] = None
    discharge_confidence: Optional[float] = None
    discharge_readiness_score: Optional[float] = None
    last_updated: Optional[datetime] = None


class DepartmentSummary(BaseModel):
    """Occupancy summary for a department."""
    department: str
    department_type: str
    capacity: int
    occupied: int
    available: int
    blocked: int
    cleaning: int
    reserved: int
    occupancy_rate: float
    alert_level: str
    avg_los_hours: Optional[float] = None
    predicted_discharges_24h: int = 0
    predicted_admissions_24h: int = 0


class DischargePrediction(BaseModel):
    """Discharge prediction for a patient."""
    patient_id: Any
    hadm_id: Any
    department: str
    current_los_hours: float
    predicted_discharge_time: Optional[datetime] = None
    discharge_probability_24h: float = 0.0
    discharge_probability_48h: float = 0.0
    discharge_readiness_score: float = 0.0
    confidence_lower: Optional[datetime] = None
    confidence_upper: Optional[datetime] = None
    key_factors: List[str] = []
    barriers_to_discharge: List[str] = []
    model_used: str = "xgboost"


class HorizonForecast(BaseModel):
    """Capacity forecast for a single time horizon."""
    horizon_hours: int
    predicted_census: float
    lower_bound_90: float
    upper_bound_90: float
    predicted_occupancy: float
    predicted_admissions: int = 0
    predicted_discharges: int = 0


class CapacityForecast(BaseModel):
    """Capacity forecast for a department."""
    department: str
    current_census: int
    current_capacity: int
    alert_level: str
    forecasts: List[HorizonForecast]
    recommended_actions: List[str] = []


class BedAllocation(BaseModel):
    """Recommended bed allocation for a patient."""
    patient_id: int
    recommended_bed: Optional[str] = None
    recommended_department: str
    priority_score: float
    wait_time_estimate_minutes: float = 0.0
    alternative_beds: List[Dict[str, Any]] = []
    allocation_reason: str = ""


class TrolleyMetrics(BaseModel):
    """Trolley tracking metrics (INMO-compatible)."""
    current_trolley_count: int = 0
    trolley_patients: List[Dict[str, Any]] = []
    avg_trolley_hours: float = 0.0
    max_trolley_hours: float = 0.0
    total_trolley_hours_today: float = 0.0
