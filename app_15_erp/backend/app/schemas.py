"""
Hospital ERP Pydantic Schemas
==============================
Typed models for department configuration, staffing, shift schedules,
bed inventory, and top-level hospital configuration.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, computed_field


# ---------------------------------------------------------------------------
# Length-of-Stay
# ---------------------------------------------------------------------------

class LOSConfig(BaseModel):
    """Length-of-stay benchmark for a department (hours)."""

    median_h: float
    mean_h: float
    p90_h: float


# ---------------------------------------------------------------------------
# Bed breakdown
# ---------------------------------------------------------------------------

class BedTypeBreakdown(BaseModel):
    """Count of beds by physical type within a department."""

    standard: int = 0
    isolation: int = 0
    monitored: int = 0
    trolley: int = 0
    resuscitation: int = 0


# ---------------------------------------------------------------------------
# Department configuration
# ---------------------------------------------------------------------------

class DepartmentConfig(BaseModel):
    """Full configuration for a single hospital department."""

    name: str
    full_name: str
    type: str
    capacity: int
    bed_types: BedTypeBreakdown
    isolation_beds: int
    los: LOSConfig
    cleaning_minutes: int
    nedocs_thresholds: Optional[Dict[str, int]] = None
    pet_target_hours: Optional[int] = None


# ---------------------------------------------------------------------------
# Staffing
# ---------------------------------------------------------------------------

class StaffRoleCount(BaseModel):
    """Head-count by role for a single shift."""

    consultant: int = 0
    registrar: int = 0
    sho: int = 0
    intern: int = 0
    cnm: int = 0
    staff_nurse: int = 0
    hca: int = 0

    @computed_field  # type: ignore[misc]
    @property
    def total_doctors(self) -> int:
        return self.consultant + self.registrar + self.sho + self.intern

    @computed_field  # type: ignore[misc]
    @property
    def total_nurses(self) -> int:
        return self.cnm + self.staff_nurse

    @computed_field  # type: ignore[misc]
    @property
    def total(self) -> int:
        return self.total_doctors + self.total_nurses + self.hca


class DepartmentStaff(BaseModel):
    """Staffing template for a department across three shift variants."""

    department: str
    nurse_patient_ratio: str
    doctor_patient_ratio: str
    day_shift: StaffRoleCount
    night_shift: StaffRoleCount
    weekend_day: StaffRoleCount


# ---------------------------------------------------------------------------
# Shift patterns & schedules
# ---------------------------------------------------------------------------

class ShiftSlot(BaseModel):
    """A single named shift window."""

    name: str
    start: str
    end: str
    days: Optional[List[str]] = None


class DepartmentSchedule(BaseModel):
    """Shift configuration and weekly roster for one department."""

    department: str
    nursing_pattern: str
    doctor_pattern: str
    ewtd_max_weekly_hours: int
    shifts: List[ShiftSlot]
    weekly_roster: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Beds
# ---------------------------------------------------------------------------

class BedConfig(BaseModel):
    """A single physical bed in the hospital."""

    bed_id: str
    department: str
    bed_type: str
    is_isolation: bool
    cleaning_minutes: int


# ---------------------------------------------------------------------------
# Hospital-wide config
# ---------------------------------------------------------------------------

class HospitalConfig(BaseModel):
    """Top-level hospital parameters."""

    name: str
    total_beds: int
    pet_target_hours: int
    nedocs_thresholds: Dict[str, int]
    mts_categories: Dict[int, Dict[str, Any]]
    alert_levels: Dict[str, str]
    default_shift_pattern: str
    ewtd_max_weekly_hours: int
