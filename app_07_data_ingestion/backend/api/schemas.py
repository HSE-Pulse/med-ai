"""Pydantic models for the simulation API request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Requests ─────────────────────────────────────────────────────────


class SpeedRequest(BaseModel):
    """Request body for POST /speed."""
    speed: float = Field(..., gt=0, le=100, description="Simulation speed multiplier (1-100)")


class ResetRequest(BaseModel):
    """Optional parameters for POST /reset."""
    pool_limit: int = Field(500, ge=1, le=5000, description="Admission pool size on restart")


# ── Responses ────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "data-ingestion-simulator"
    sim_running: bool = False


class SimStats(BaseModel):
    total_admissions: int = 0
    total_discharges: int = 0
    total_transfers: int = 0
    total_vitals: int = 0
    total_labs: int = 0
    total_meds: int = 0
    total_diagnoses: int = 0
    total_procedures: int = 0
    total_notes: int = 0


class SimStateResponse(BaseModel):
    running: bool
    sim_time: str
    speed: float
    active_patients: int
    queued_events: int
    stats: SimStats
    last_fired_event_at_wall: Optional[str] = None
    last_fired_event_at_sim: Optional[str] = None
    last_fired_event_type: Optional[str] = None
    seconds_since_last_event: Optional[float] = None


class ActivePatient(BaseModel):
    subject_id: Optional[int] = None
    hadm_id: str
    original_hadm_id: Optional[int] = None
    sim_admittime: Optional[str] = None
    admission_type: Optional[str] = None
    admission_location: Optional[str] = None
    insurance: Optional[str] = None
    race: Optional[str] = None
    status: str = "admitted"


class ActivePatientsResponse(BaseModel):
    count: int
    patients: List[ActivePatient]


class DepartmentCensusResponse(BaseModel):
    census: Dict[str, int]
    total: int


class CollectionStatsResponse(BaseModel):
    collections: Dict[str, int]


class MessageResponse(BaseModel):
    message: str
    state: Optional[Dict[str, Any]] = None
