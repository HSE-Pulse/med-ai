"""Pydantic response schemas for the Patient Journey API.

Defines typed models that mirror the return shapes of each backend engine,
used for OpenAPI documentation and response validation.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

class TimelineEventSchema(BaseModel):
    """A single event on the patient timeline."""
    timestamp: str
    event_type: str
    source_table: str
    category: str
    details: Dict[str, Any] = Field(default_factory=dict)


class TimelineResponse(BaseModel):
    events: List[TimelineEventSchema] = Field(default_factory=list)
    total_count: int = 0


# ---------------------------------------------------------------------------
# Vitals
# ---------------------------------------------------------------------------

class VitalDataPoint(BaseModel):
    time: str
    value: float


class VitalsResponse(BaseModel):
    """Resampled vital-sign series keyed by vital name."""
    vitals: Dict[str, List[VitalDataPoint]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Labs
# ---------------------------------------------------------------------------

class LabDataPoint(BaseModel):
    time: str
    value: Optional[float] = None
    unit: str = ""
    flag: str = "unknown"
    ref_lower: Optional[float] = None
    ref_upper: Optional[float] = None


class LabsResponse(BaseModel):
    """Lab results grouped by panel then lab name."""
    panels: Dict[str, Dict[str, List[LabDataPoint]]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Medications
# ---------------------------------------------------------------------------

class MedicationEntry(BaseModel):
    drug: str
    drug_type: Optional[str] = None
    category: str = "other"
    start_time: Optional[str] = None
    stop_time: Optional[str] = None
    dose_val_rx: Optional[Any] = None
    dose_unit_rx: Optional[str] = None
    route: Optional[str] = None
    prod_strength: Optional[str] = None
    duration_hours: Optional[float] = None


class MedicationResponse(BaseModel):
    medications: List[MedicationEntry] = Field(default_factory=list)
    total_count: int = 0


# ---------------------------------------------------------------------------
# Journey path (transfers + ICU episodes + services)
# ---------------------------------------------------------------------------

class JourneyPathResponse(BaseModel):
    transfers: List[Dict[str, Any]] = Field(default_factory=list)
    icu_episodes: List[Dict[str, Any]] = Field(default_factory=list)
    services: List[Dict[str, Any]] = Field(default_factory=list)
    diagnoses: List[Dict[str, Any]] = Field(default_factory=list)
    procedures: List[Dict[str, Any]] = Field(default_factory=list)
    admission: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Derived metrics
# ---------------------------------------------------------------------------

class MetricsResponse(BaseModel):
    subject_id: int
    hadm_id: int
    total_los_hours: Optional[float] = None
    icu_los_hours: Optional[float] = None
    ed_los_hours: Optional[float] = None
    num_transfers: int = 0
    num_icu_episodes: int = 0
    num_procedures: int = 0
    num_unique_drugs: int = 0
    time_to_first_icu_hours: Optional[float] = None
    mortality: bool = False


# ---------------------------------------------------------------------------
# Patient summary (demographics + admissions list)
# ---------------------------------------------------------------------------

class AdmissionSummary(BaseModel):
    hadm_id: int
    admittime: Optional[str] = None
    dischtime: Optional[str] = None
    admission_type: Optional[str] = None
    admission_location: Optional[str] = None
    discharge_location: Optional[str] = None
    insurance: Optional[str] = None
    race: Optional[str] = None
    hospital_expire_flag: Optional[int] = None
    # Derived state surfaced for the dashboard banner. ``state_label`` is
    # one of "active" / "discharged" / "expired" — the patient page uses
    # it to render an unmissable status banner without re-deriving the
    # state from raw fields. ``is_active`` / ``is_expired`` are the same
    # information in boolean form for convenience.
    is_active: Optional[bool] = None
    is_expired: Optional[bool] = None
    state_label: Optional[str] = None
    discharge_reason: Optional[str] = None


class PatientSummaryResponse(BaseModel):
    subject_id: int
    gender: Optional[str] = None
    anchor_age: Optional[int] = None
    admissions: List[AdmissionSummary] = Field(default_factory=list)
