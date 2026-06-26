"""Pydantic models for the Sepsis ICU Prediction API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ============================================================================
# Enums
# ============================================================================

class AlertLevel(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"


class SOFAComponent(str, Enum):
    RESPIRATION = "respiration"
    COAGULATION = "coagulation"
    LIVER = "liver"
    CARDIOVASCULAR = "cardiovascular"
    RENAL = "renal"


# ============================================================================
# Input schemas
# ============================================================================

class VitalSignReading(BaseModel):
    """A single time-stamped set of vital sign measurements."""
    timestamp: datetime
    heart_rate: Optional[float] = Field(None, ge=0, le=300, description="bpm")
    respiratory_rate: Optional[float] = Field(None, ge=0, le=80, description="breaths/min")
    spo2: Optional[float] = Field(None, ge=0, le=100, description="Oxygen saturation %")
    sbp: Optional[float] = Field(None, ge=0, le=350, description="Systolic BP mmHg")
    dbp: Optional[float] = Field(None, ge=0, le=250, description="Diastolic BP mmHg")
    temperature: Optional[float] = Field(None, ge=25, le=45, description="Celsius")
    mbp: Optional[float] = Field(None, ge=0, le=300, description="Mean arterial pressure mmHg")

    class Config:
        json_schema_extra = {
            "example": {
                "timestamp": "2026-03-30T10:00:00",
                "heart_rate": 88,
                "respiratory_rate": 18,
                "spo2": 95,
                "sbp": 120,
                "dbp": 75,
                "temperature": 37.2,
                "mbp": 90,
            }
        }


class LabReading(BaseModel):
    """A single time-stamped set of laboratory results."""
    timestamp: datetime
    wbc: Optional[float] = Field(None, ge=0, description="White blood cell count (K/uL)")
    lactate: Optional[float] = Field(None, ge=0, description="Lactate (mmol/L)")
    creatinine: Optional[float] = Field(None, ge=0, description="Creatinine (mg/dL)")
    platelets: Optional[float] = Field(None, ge=0, description="Platelets (K/uL)")
    bilirubin: Optional[float] = Field(None, ge=0, description="Total bilirubin (mg/dL)")

    class Config:
        json_schema_extra = {
            "example": {
                "timestamp": "2026-03-30T06:00:00",
                "wbc": 12.5,
                "lactate": 2.1,
                "creatinine": 1.4,
                "platelets": 180,
                "bilirubin": 0.9,
            }
        }


class PredictionRequest(BaseModel):
    """Input for the /predict endpoint -- a recent window of vitals and labs."""
    stay_id: Optional[int] = Field(None, description="ICU stay identifier (if available)")
    vitals: List[VitalSignReading] = Field(..., min_length=1, max_length=72)
    labs: List[LabReading] = Field(default_factory=list, max_length=72)
    age: float = Field(65.0, ge=0, le=120)
    gender: str = Field("M", pattern="^(M|F)$")
    careunit: str = Field("MICU", description="ICU care unit name")

    class Config:
        json_schema_extra = {
            "example": {
                "stay_id": 30000123,
                "vitals": [
                    {"timestamp": "2026-03-30T08:00:00", "heart_rate": 92, "respiratory_rate": 20, "spo2": 94, "sbp": 110, "dbp": 65, "temperature": 38.1, "mbp": 80},
                    {"timestamp": "2026-03-30T09:00:00", "heart_rate": 98, "respiratory_rate": 22, "spo2": 93, "sbp": 105, "dbp": 60, "temperature": 38.4, "mbp": 75},
                ],
                "labs": [
                    {"timestamp": "2026-03-30T06:00:00", "wbc": 14.2, "lactate": 2.8, "creatinine": 1.6, "platelets": 140, "bilirubin": 1.1},
                ],
                "age": 68,
                "gender": "M",
                "careunit": "MICU",
            }
        }


# ============================================================================
# Output schemas
# ============================================================================

class SOFABreakdown(BaseModel):
    """Individual SOFA component scores."""
    respiration: int = Field(0, ge=0, le=4)
    coagulation: int = Field(0, ge=0, le=4)
    liver: int = Field(0, ge=0, le=4)
    cardiovascular: int = Field(0, ge=0, le=4)
    renal: int = Field(0, ge=0, le=4)
    total: int = Field(0, ge=0, le=20)


class ContributingFactor(BaseModel):
    """A factor contributing to the sepsis risk score."""
    feature: str
    value: float
    description: str
    severity: AlertLevel


class SepsisPrediction(BaseModel):
    """Output from the sepsis prediction model."""
    risk_score: float = Field(..., ge=0.0, le=1.0, description="Probability of sepsis onset within 4 hours")
    alert_level: AlertLevel
    sofa_score: int = Field(..., ge=0, le=20)
    sofa_components: SOFABreakdown
    predicted_onset_hours: Optional[float] = Field(None, description="Estimated hours until onset (if risk > threshold)")
    contributing_factors: List[ContributingFactor]
    model_used: str = Field("ensemble", description="Which model produced this prediction")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class TimelinePoint(BaseModel):
    """A single point in a patient's risk timeline."""
    timestamp: datetime
    risk_score: float = Field(ge=0.0, le=1.0)
    alert_level: AlertLevel
    sofa_score: int
    heart_rate: Optional[float] = None
    respiratory_rate: Optional[float] = None
    spo2: Optional[float] = None
    sbp: Optional[float] = None
    temperature: Optional[float] = None
    lactate: Optional[float] = None


class PatientTimeline(BaseModel):
    """Historical risk timeline for an ICU patient."""
    stay_id: int
    subject_id: Optional[int] = None
    careunit: str
    admission_time: datetime
    timeline: List[TimelinePoint]
    current_alert: AlertLevel
    peak_risk: float
    peak_risk_time: Optional[datetime] = None


class PatientSummary(BaseModel):
    """Brief patient status for the unit overview."""
    stay_id: int
    bed: str
    age: int
    gender: str
    careunit: str
    hours_in_icu: float
    current_risk: float
    alert_level: AlertLevel
    sofa_score: int
    trend: str = Field(description="rising, falling, or stable")
    last_updated: datetime


class UnitOverview(BaseModel):
    """Current status of all patients in the ICU."""
    unit_name: str
    total_patients: int
    red_alerts: int
    orange_alerts: int
    yellow_alerts: int
    patients: List[PatientSummary]
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    model_loaded: bool
    version: str
    uptime_seconds: float
