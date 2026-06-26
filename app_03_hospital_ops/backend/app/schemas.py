"""Pydantic models for the Hospital Operations DES-MARL API."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Simulation configuration
# ---------------------------------------------------------------------------

class DepartmentConfig(BaseModel):
    """Configuration for a single department."""
    name: str = Field(..., description="Department identifier")
    capacity: int = Field(30, ge=1, description="Maximum patients in service")
    initial_doctors: int = Field(2, ge=1, description="Initial doctor count")
    initial_nurses: int = Field(6, ge=1, description="Initial nurse count")


class SimulationConfig(BaseModel):
    """Configuration for starting a simulation."""
    duration_hours: float = Field(168.0, gt=0, description="Total simulation duration in hours")
    step_duration_hours: float = Field(1.0, gt=0, description="Time per simulation step in hours")
    arrival_rate_per_hour: float = Field(12.0, gt=0, description="Base Poisson arrival rate")
    departments: Optional[List[DepartmentConfig]] = Field(
        None, description="Custom department configurations (uses defaults if None)"
    )
    seed: int = Field(42, description="Random seed for reproducibility")
    use_marl: bool = Field(False, description="Use trained MARL agent for staffing decisions")
    model_checkpoint: Optional[str] = Field(None, description="Path to MARL model checkpoint")


class SimulationStartResponse(BaseModel):
    """Response after starting a simulation."""
    simulation_id: str
    status: str = "running"
    config: SimulationConfig
    message: str = "Simulation started"


# ---------------------------------------------------------------------------
# Simulation state and results
# ---------------------------------------------------------------------------

class DepartmentState(BaseModel):
    """Current state of a single department."""
    name: str
    patient_count: int = 0
    patients_in_service: int = 0
    queue_length: int = 0
    capacity: int = 30
    occupancy_ratio: float = 0.0
    avg_wait_time_hours: float = 0.0
    avg_service_time_hours: float = 0.0
    total_served: int = 0
    total_arrivals: int = 0
    staff_doctors: int = 2
    staff_nurses: int = 6


class SimulationState(BaseModel):
    """Full simulation state at a point in time."""
    simulation_id: str
    simulation_time_hours: float
    # ISO 8601 datetime equivalent of ``simulation_time_hours``, anchored to
    # the global SimClock at engine creation. Lets API consumers correlate
    # state snapshots with action_log entries and Kafka events without
    # reasoning about engine-relative float hours.
    simulation_time_iso: Optional[str] = None
    step: int
    departments: List[DepartmentState]
    active_patients: int
    discharged_patients: int
    pending_events: int


class StepRequest(BaseModel):
    """Request to advance the simulation by one step."""
    simulation_id: Optional[str] = None
    actions: Optional[Dict[str, List[float]]] = Field(
        None,
        description="Optional manual actions per department: [doc_adj, nurse_adj, priority, threshold]",
    )


class StepResponse(BaseModel):
    """Response from a simulation step."""
    simulation_id: str
    step: int
    simulation_time_hours: float
    reward: float
    events_processed: int
    state: SimulationState


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class DepartmentMetrics(BaseModel):
    """Performance metrics for a single department."""
    name: str
    avg_wait_time_hours: float = 0.0
    avg_service_time_hours: float = 0.0
    occupancy_ratio: float = 0.0
    throughput: int = 0
    queue_length: int = 0


class PerformanceMetrics(BaseModel):
    """Aggregate performance metrics for the simulation."""
    simulation_id: str
    simulation_time_hours: float
    simulation_time_iso: Optional[str] = None
    total_discharged: int
    mean_total_wait_hours: float
    mean_los_hours: float
    active_patients: int
    departments: List[DepartmentMetrics]


# ---------------------------------------------------------------------------
# Simulate endpoint (full run)
# ---------------------------------------------------------------------------

class SimulateRequest(BaseModel):
    """Request for a complete simulation run."""
    duration_hours: float = Field(168.0, gt=0, description="Duration in hours")
    step_duration_hours: float = Field(1.0, gt=0)
    arrival_rate_per_hour: float = Field(12.0, gt=0)
    seed: int = Field(42)
    use_marl: bool = Field(False)
    model_checkpoint: Optional[str] = None
    collect_snapshots_every: int = Field(
        1, ge=1, description="Collect state snapshots every N steps"
    )


class SimulateResponse(BaseModel):
    """Response from a complete simulation run."""
    simulation_id: str
    total_steps: int
    total_discharged: int
    mean_wait_time_hours: float
    mean_los_hours: float
    department_metrics: List[DepartmentMetrics]
    snapshots: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Time-series state snapshots for charting",
    )


# ---------------------------------------------------------------------------
# WebSocket messages
# ---------------------------------------------------------------------------

class WSMessageType(str, Enum):
    """Types of WebSocket messages."""
    STATE_UPDATE = "state_update"
    METRICS_UPDATE = "metrics_update"
    STEP_COMPLETE = "step_complete"
    SIMULATION_COMPLETE = "simulation_complete"
    ERROR = "error"


class WSMessage(BaseModel):
    """WebSocket message envelope."""
    type: WSMessageType
    data: Dict[str, Any] = Field(default_factory=dict)
    timestamp: float = 0.0
