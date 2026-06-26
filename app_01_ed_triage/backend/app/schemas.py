"""
Pydantic request / response schemas for the ED Triage API.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ===================================================================
# Enums
# ===================================================================
class AcuityLevel(IntEnum):
    """ESI-equivalent 5-level acuity scale."""

    RESUSCITATION = 1
    EMERGENT = 2
    URGENT = 3
    LESS_URGENT = 4
    NON_URGENT = 5


ACUITY_LABELS = {
    1: "Resuscitation",
    2: "Emergent",
    3: "Urgent",
    4: "Less Urgent",
    5: "Non-urgent",
}

ACUITY_COLORS = {
    1: "#DC2626",  # red-600
    2: "#F97316",  # orange-500
    3: "#EAB308",  # yellow-500
    4: "#22C55E",  # green-500
    5: "#3B82F6",  # blue-500
}


# ===================================================================
# Request schemas
# ===================================================================
class TriageInput(BaseModel):
    """Single patient triage assessment input.

    All vital signs and labs are optional; the model handles missingness
    internally via imputation and missingness indicators.
    """

    # Demographics
    age: float = Field(..., ge=0, le=120, description="Patient age in years")
    gender: str = Field(
        ..., pattern="^(M|F|Male|Female|Other)$", description="Patient gender"
    )

    # Vital signs
    heart_rate: Optional[float] = Field(
        None, ge=20, le=300, description="Heart rate (bpm)"
    )
    respiratory_rate: Optional[float] = Field(
        None, ge=0, le=80, description="Respiratory rate (breaths/min)"
    )
    spo2: Optional[float] = Field(
        None, ge=50, le=100, description="Oxygen saturation (%)"
    )
    sbp: Optional[float] = Field(
        None, ge=30, le=350, description="Systolic blood pressure (mmHg)"
    )
    dbp: Optional[float] = Field(
        None, ge=10, le=250, description="Diastolic blood pressure (mmHg)"
    )
    temperature: Optional[float] = Field(
        None, ge=30, le=113, description="Body temperature (Celsius or Fahrenheit — auto-converted)"
    )

    # Key lab results
    wbc: Optional[float] = Field(None, ge=0, description="White blood cell count (K/uL)")
    hemoglobin: Optional[float] = Field(None, ge=0, description="Hemoglobin (g/dL)")
    lactate: Optional[float] = Field(None, ge=0, description="Lactate (mmol/L)")
    glucose: Optional[float] = Field(None, ge=0, description="Glucose (mg/dL)")
    creatinine: Optional[float] = Field(None, ge=0, description="Creatinine (mg/dL)")

    # Arrival context
    arrival_mode: Optional[str] = Field(
        "UNKNOWN",
        description="Arrival mode: AMBULANCE, WALK_IN, TRANSFER, PHYSICIAN_REFERRAL",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "age": 65,
                "gender": "M",
                "heart_rate": 110,
                "respiratory_rate": 24,
                "spo2": 92,
                "sbp": 90,
                "dbp": 55,
                "temperature": 38.5,
                "wbc": 15.2,
                "hemoglobin": 10.1,
                "lactate": 3.2,
                "glucose": 180,
                "creatinine": 1.8,
                "arrival_mode": "AMBULANCE",
            }
        }


class BatchTriageInput(BaseModel):
    """Batch of triage inputs for bulk prediction."""

    patients: List[TriageInput] = Field(
        ..., min_length=1, max_length=500, description="List of patient inputs"
    )


# ===================================================================
# Response schemas
# ===================================================================
class TriagePrediction(BaseModel):
    """Single patient triage prediction output."""

    acuity_level: int = Field(..., ge=1, le=5, description="ESI-equivalent acuity level (1-5)")
    acuity_label: str = Field(..., description="Human-readable acuity label")
    acuity_color: str = Field(..., description="Color hex code for UI display")
    confidence: float = Field(
        ..., ge=0, le=1, description="Model confidence in the predicted acuity level"
    )
    class_probabilities: Dict[str, float] = Field(
        ..., description="Probability for each acuity level"
    )
    disposition: str = Field(
        ..., description="Predicted disposition: admit_to_inpatient, discharge_home, transfer, expired"
    )
    ed_los_estimate_hours: float = Field(
        ..., description="Estimated ED length of stay in hours"
    )
    risk_factors: List[str] = Field(
        default_factory=list, description="Identified risk factors driving the prediction"
    )


class BatchTriagePrediction(BaseModel):
    """Batch triage prediction response."""

    predictions: List[TriagePrediction]
    count: int


class ModelInfoResponse(BaseModel):
    """Model metadata and performance metrics."""

    model_name: str
    model_type: str
    version: Optional[str] = None
    metrics: Dict[str, object] = {}
    feature_count: int = 0
    feature_names: List[str] = []
    class_labels: Dict[str, str] = {}


class DatasetStatsResponse(BaseModel):
    """Dataset statistics summary."""

    total_samples: int
    class_distribution: Dict[str, int]
    feature_importance: Dict[str, float] = {}
    missing_rates: Dict[str, float] = {}
