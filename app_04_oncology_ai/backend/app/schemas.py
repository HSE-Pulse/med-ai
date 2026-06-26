"""Pydantic schemas for Oncology AI API."""
from __future__ import annotations
from pydantic import BaseModel, Field


class OncologyPatientInput(BaseModel):
    age: int = Field(ge=18, le=100)
    gender: str = Field(pattern="^(M|F)$")
    cancer_type: str = "Unknown"
    cancer_icd_code: str = ""
    stage_proxy: int = Field(default=2, ge=1, le=4)
    num_procedures: int = Field(default=0, ge=0)
    has_surgery: int = Field(default=0, ge=0, le=1)
    has_chemotherapy: int = Field(default=0, ge=0, le=1)
    has_radiation: int = Field(default=0, ge=0, le=1)
    chemo_drug_count: int = Field(default=0, ge=0)
    num_prior_admissions: int = Field(default=0, ge=0)
    days_since_last_admission: float = Field(default=0, ge=0)
    total_los_days: float = Field(default=5, ge=0)
    num_comorbidities: int = Field(default=0, ge=0)
    charlson_score: int = Field(default=0, ge=0)
    insurance: str = "Other"
    time_to_first_procedure_days: float | None = None


class RiskPrediction(BaseModel):
    readmission_30d_risk: float = Field(ge=0, le=1)
    mortality_risk: float = Field(ge=0, le=1)
    risk_level: str  # low, moderate, high, critical
    risk_color: str  # green, yellow, orange, red
    contributing_factors: list[str]
    recommendations: list[str]


class PathwayRecommendation(BaseModel):
    cancer_type: str
    recommended_treatments: list[str]
    treatment_sequence: list[TreatmentStep]
    estimated_duration_days: int
    urgency_score: float = Field(ge=0, le=1)
    notes: list[str]


class TreatmentStep(BaseModel):
    step: int
    treatment: str
    category: str  # surgery, chemotherapy, radiation, supportive
    estimated_days: int
    priority: str  # immediate, scheduled, follow-up


# Fix forward reference
PathwayRecommendation.model_rebuild()


class PatientTimeline(BaseModel):
    subject_id: int
    cancer_type: str
    admissions: list[AdmissionEvent]
    treatments: list[TreatmentEvent]


class AdmissionEvent(BaseModel):
    hadm_id: int
    admittime: str
    dischtime: str
    los_days: float
    mortality: int


class TreatmentEvent(BaseModel):
    event_type: str
    event_code: str
    event_date: str


PatientTimeline.model_rebuild()


class CohortStats(BaseModel):
    total_patients: int
    total_admissions: int
    cancer_type_distribution: dict[str, int]
    readmission_rate: float
    mortality_rate: float
    chemo_rate: float
    surgery_rate: float
    radiation_rate: float
    median_age: float
    median_los_days: float


class NoteAnalysis(BaseModel):
    cancer_mentions: list[str]
    treatment_mentions: list[str]
    medication_mentions: list[str]
    risk_indicators: list[str]
    summary: str
