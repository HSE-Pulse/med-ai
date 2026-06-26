"""
Hospital ERP Master Data
========================
Complete reference data for an Irish HSE hospital with 278 beds across
14 departments. Staffing follows HIQA/HSE standards and EWTD-compliant
shift patterns for NCHDs.

All capacity figures, length-of-stay benchmarks, and nurse-patient ratios
are aligned with published Irish hospital performance metrics.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# DEPARTMENTS — 14 Irish HSE departments
# ---------------------------------------------------------------------------

DEPARTMENTS: Dict[str, Dict[str, Any]] = {
    "ED": {
        "name": "ED",
        "full_name": "Emergency Department",
        "type": "emergency",
        "capacity": 30,
        "bed_types": {
            "standard": 10,
            "trolley": 12,
            "resuscitation": 4,
            "monitored": 4,
            "isolation": 0,
        },
        "isolation_beds": 0,
        "los": {"median_h": 6.7, "mean_h": 8.2, "p90_h": 14.0},
        "cleaning_minutes": 15,
        "nedocs_thresholds": {
            "normal": 0,
            "busy": 100,
            "overcrowded": 140,
            "crowded": 180,
            "severe": 200,
        },
        "pet_target_hours": 6,
    },
    "MAU": {
        "name": "MAU",
        "full_name": "Medical Assessment Unit",
        "type": "assessment",
        "capacity": 24,
        "bed_types": {
            "standard": 16,
            "trolley": 4,
            "monitored": 2,
            "isolation": 2,
            "resuscitation": 0,
        },
        "isolation_beds": 2,
        "los": {"median_h": 18.0, "mean_h": 22.0, "p90_h": 48.0},
        "cleaning_minutes": 20,
    },
    "AMAU": {
        "name": "AMAU",
        "full_name": "Acute Medical Assessment Unit",
        "type": "assessment",
        "capacity": 16,
        "bed_types": {
            "standard": 10,
            "trolley": 2,
            "monitored": 2,
            "isolation": 2,
            "resuscitation": 0,
        },
        "isolation_beds": 2,
        "los": {"median_h": 12.0, "mean_h": 14.0, "p90_h": 36.0},
        "cleaning_minutes": 20,
    },
    "SAU": {
        "name": "SAU",
        "full_name": "Surgical Assessment Unit",
        "type": "assessment",
        "capacity": 12,
        "bed_types": {
            "standard": 8,
            "trolley": 2,
            "monitored": 2,
            "isolation": 0,
            "resuscitation": 0,
        },
        "isolation_beds": 0,
        "los": {"median_h": 10.0, "mean_h": 12.0, "p90_h": 30.0},
        "cleaning_minutes": 20,
    },
    "CDU": {
        "name": "CDU",
        "full_name": "Clinical Decision Unit",
        "type": "observation",
        "capacity": 8,
        "bed_types": {
            "standard": 4,
            "trolley": 4,
            "monitored": 0,
            "isolation": 0,
            "resuscitation": 0,
        },
        "isolation_beds": 0,
        "los": {"median_h": 8.0, "mean_h": 10.0, "p90_h": 24.0},
        "cleaning_minutes": 15,
    },
    "Medicine": {
        "name": "Medicine",
        "full_name": "General Medicine Ward",
        "type": "inpatient",
        "capacity": 40,
        "bed_types": {
            "standard": 30,
            "isolation": 6,
            "monitored": 4,
            "trolley": 0,
            "resuscitation": 0,
        },
        "isolation_beds": 6,
        "los": {"median_h": 120.0, "mean_h": 144.0, "p90_h": 288.0},
        "cleaning_minutes": 30,
    },
    "Surgery": {
        "name": "Surgery",
        "full_name": "General Surgery Ward",
        "type": "inpatient",
        "capacity": 36,
        "bed_types": {
            "standard": 28,
            "isolation": 4,
            "monitored": 4,
            "trolley": 0,
            "resuscitation": 0,
        },
        "isolation_beds": 4,
        "los": {"median_h": 96.0, "mean_h": 108.0, "p90_h": 240.0},
        "cleaning_minutes": 30,
    },
    "Cardiology": {
        "name": "Cardiology",
        "full_name": "Cardiology Ward",
        "type": "inpatient",
        "capacity": 20,
        "bed_types": {
            "standard": 10,
            "isolation": 2,
            "monitored": 8,
            "trolley": 0,
            "resuscitation": 0,
        },
        "isolation_beds": 2,
        "los": {"median_h": 72.0, "mean_h": 96.0, "p90_h": 192.0},
        "cleaning_minutes": 25,
    },
    "Respiratory": {
        "name": "Respiratory",
        "full_name": "Respiratory Medicine Ward",
        "type": "inpatient",
        "capacity": 18,
        "bed_types": {
            "standard": 10,
            "isolation": 4,
            "monitored": 4,
            "trolley": 0,
            "resuscitation": 0,
        },
        "isolation_beds": 4,
        "los": {"median_h": 96.0, "mean_h": 120.0, "p90_h": 264.0},
        "cleaning_minutes": 30,
    },
    "Orthopaedics": {
        "name": "Orthopaedics",
        "full_name": "Orthopaedic Surgery Ward",
        "type": "inpatient",
        "capacity": 24,
        "bed_types": {
            "standard": 20,
            "isolation": 2,
            "monitored": 2,
            "trolley": 0,
            "resuscitation": 0,
        },
        "isolation_beds": 2,
        "los": {"median_h": 84.0, "mean_h": 96.0, "p90_h": 216.0},
        "cleaning_minutes": 30,
    },
    "ICU": {
        "name": "ICU",
        "full_name": "Intensive Care Unit",
        "type": "critical",
        "capacity": 12,
        "bed_types": {
            "standard": 0,
            "isolation": 2,
            "monitored": 10,
            "trolley": 0,
            "resuscitation": 0,
        },
        "isolation_beds": 2,
        "los": {"median_h": 72.0, "mean_h": 96.0, "p90_h": 240.0},
        "cleaning_minutes": 45,
    },
    "HDU": {
        "name": "HDU",
        "full_name": "High Dependency Unit",
        "type": "high_dependency",
        "capacity": 8,
        "bed_types": {
            "standard": 0,
            "isolation": 1,
            "monitored": 7,
            "trolley": 0,
            "resuscitation": 0,
        },
        "isolation_beds": 1,
        "los": {"median_h": 48.0, "mean_h": 60.0, "p90_h": 144.0},
        "cleaning_minutes": 40,
    },
    "Day_Ward": {
        "name": "Day_Ward",
        "full_name": "Day Procedures Ward",
        "type": "day_case",
        "capacity": 20,
        "bed_types": {
            "standard": 16,
            "isolation": 0,
            "monitored": 4,
            "trolley": 0,
            "resuscitation": 0,
        },
        "isolation_beds": 0,
        "los": {"median_h": 6.0, "mean_h": 7.0, "p90_h": 10.0},
        "cleaning_minutes": 15,
    },
    "Discharge_Lounge": {
        "name": "Discharge_Lounge",
        "full_name": "Discharge Lounge",
        "type": "discharge",
        "capacity": 10,
        "bed_types": {
            "standard": 6,
            "isolation": 0,
            "monitored": 0,
            "trolley": 4,
            "resuscitation": 0,
        },
        "isolation_beds": 0,
        "los": {"median_h": 2.0, "mean_h": 3.0, "p90_h": 6.0},
        "cleaning_minutes": 10,
    },
}

# ---------------------------------------------------------------------------
# STAFF_REGISTRY — per-department staffing by shift
# ---------------------------------------------------------------------------

STAFF_REGISTRY: Dict[str, Dict[str, Any]] = {
    "ED": {
        "nurse_patient_ratio": "variable",
        "doctor_patient_ratio": "variable",
        "day_shift": {
            "consultant": 3,
            "registrar": 3,
            "sho": 3,
            "intern": 2,
            "cnm": 2,
            "staff_nurse": 10,
            "hca": 4,
        },
        "night_shift": {
            "consultant": 1,
            "registrar": 2,
            "sho": 2,
            "intern": 1,
            "cnm": 1,
            "staff_nurse": 8,
            "hca": 3,
        },
        "weekend_day": {
            "consultant": 2,
            "registrar": 2,
            "sho": 2,
            "intern": 1,
            "cnm": 1,
            "staff_nurse": 8,
            "hca": 3,
        },
    },
    "MAU": {
        "nurse_patient_ratio": "1:4",
        "doctor_patient_ratio": "1:8",
        "day_shift": {
            "consultant": 2,
            "registrar": 1,
            "sho": 2,
            "intern": 1,
            "cnm": 1,
            "staff_nurse": 6,
            "hca": 3,
        },
        "night_shift": {
            "consultant": 0,
            "registrar": 1,
            "sho": 1,
            "intern": 0,
            "cnm": 1,
            "staff_nurse": 5,
            "hca": 2,
        },
        "weekend_day": {
            "consultant": 1,
            "registrar": 1,
            "sho": 1,
            "intern": 1,
            "cnm": 1,
            "staff_nurse": 5,
            "hca": 2,
        },
    },
    "AMAU": {
        "nurse_patient_ratio": "1:4",
        "doctor_patient_ratio": "1:8",
        "day_shift": {
            "consultant": 1,
            "registrar": 1,
            "sho": 1,
            "intern": 1,
            "cnm": 1,
            "staff_nurse": 4,
            "hca": 2,
        },
        "night_shift": {
            "consultant": 0,
            "registrar": 1,
            "sho": 1,
            "intern": 0,
            "cnm": 1,
            "staff_nurse": 3,
            "hca": 1,
        },
        "weekend_day": {
            "consultant": 1,
            "registrar": 1,
            "sho": 1,
            "intern": 0,
            "cnm": 1,
            "staff_nurse": 3,
            "hca": 1,
        },
    },
    "SAU": {
        "nurse_patient_ratio": "1:6",
        "doctor_patient_ratio": "1:6",
        "day_shift": {
            "consultant": 1,
            "registrar": 1,
            "sho": 1,
            "intern": 0,
            "cnm": 1,
            "staff_nurse": 3,
            "hca": 1,
        },
        "night_shift": {
            "consultant": 0,
            "registrar": 1,
            "sho": 0,
            "intern": 0,
            "cnm": 0,
            "staff_nurse": 2,
            "hca": 1,
        },
        "weekend_day": {
            "consultant": 1,
            "registrar": 1,
            "sho": 0,
            "intern": 0,
            "cnm": 0,
            "staff_nurse": 2,
            "hca": 1,
        },
    },
    "CDU": {
        "nurse_patient_ratio": "1:4",
        "doctor_patient_ratio": "1:8",
        "day_shift": {
            "consultant": 0,
            "registrar": 1,
            "sho": 0,
            "intern": 0,
            "cnm": 0,
            "staff_nurse": 2,
            "hca": 1,
        },
        "night_shift": {
            "consultant": 0,
            "registrar": 0,
            "sho": 1,
            "intern": 0,
            "cnm": 0,
            "staff_nurse": 2,
            "hca": 1,
        },
        "weekend_day": {
            "consultant": 0,
            "registrar": 1,
            "sho": 0,
            "intern": 0,
            "cnm": 0,
            "staff_nurse": 2,
            "hca": 1,
        },
    },
    "Medicine": {
        "nurse_patient_ratio": "1:6",
        "doctor_patient_ratio": "1:5",
        "day_shift": {
            "consultant": 2,
            "registrar": 2,
            "sho": 2,
            "intern": 2,
            "cnm": 1,
            "staff_nurse": 7,
            "hca": 4,
        },
        "night_shift": {
            "consultant": 0,
            "registrar": 1,
            "sho": 1,
            "intern": 1,
            "cnm": 1,
            "staff_nurse": 6,
            "hca": 3,
        },
        "weekend_day": {
            "consultant": 1,
            "registrar": 1,
            "sho": 2,
            "intern": 1,
            "cnm": 1,
            "staff_nurse": 6,
            "hca": 3,
        },
    },
    "Surgery": {
        "nurse_patient_ratio": "1:6",
        "doctor_patient_ratio": "1:5",
        "day_shift": {
            "consultant": 2,
            "registrar": 2,
            "sho": 2,
            "intern": 1,
            "cnm": 1,
            "staff_nurse": 6,
            "hca": 3,
        },
        "night_shift": {
            "consultant": 0,
            "registrar": 1,
            "sho": 1,
            "intern": 1,
            "cnm": 1,
            "staff_nurse": 5,
            "hca": 3,
        },
        "weekend_day": {
            "consultant": 1,
            "registrar": 1,
            "sho": 1,
            "intern": 1,
            "cnm": 1,
            "staff_nurse": 5,
            "hca": 3,
        },
    },
    "Cardiology": {
        "nurse_patient_ratio": "1:4",
        "doctor_patient_ratio": "1:5",
        "day_shift": {
            "consultant": 1,
            "registrar": 1,
            "sho": 1,
            "intern": 1,
            "cnm": 1,
            "staff_nurse": 5,
            "hca": 2,
        },
        "night_shift": {
            "consultant": 0,
            "registrar": 1,
            "sho": 1,
            "intern": 0,
            "cnm": 1,
            "staff_nurse": 4,
            "hca": 2,
        },
        "weekend_day": {
            "consultant": 1,
            "registrar": 1,
            "sho": 1,
            "intern": 0,
            "cnm": 1,
            "staff_nurse": 4,
            "hca": 2,
        },
    },
    "Respiratory": {
        "nurse_patient_ratio": "1:4",
        "doctor_patient_ratio": "1:6",
        "day_shift": {
            "consultant": 1,
            "registrar": 1,
            "sho": 1,
            "intern": 1,
            "cnm": 1,
            "staff_nurse": 5,
            "hca": 2,
        },
        "night_shift": {
            "consultant": 0,
            "registrar": 1,
            "sho": 1,
            "intern": 0,
            "cnm": 1,
            "staff_nurse": 4,
            "hca": 2,
        },
        "weekend_day": {
            "consultant": 1,
            "registrar": 1,
            "sho": 1,
            "intern": 0,
            "cnm": 1,
            "staff_nurse": 4,
            "hca": 2,
        },
    },
    "Orthopaedics": {
        "nurse_patient_ratio": "1:6",
        "doctor_patient_ratio": "1:6",
        "day_shift": {
            "consultant": 1,
            "registrar": 1,
            "sho": 1,
            "intern": 1,
            "cnm": 1,
            "staff_nurse": 4,
            "hca": 2,
        },
        "night_shift": {
            "consultant": 0,
            "registrar": 1,
            "sho": 1,
            "intern": 0,
            "cnm": 1,
            "staff_nurse": 3,
            "hca": 2,
        },
        "weekend_day": {
            "consultant": 1,
            "registrar": 1,
            "sho": 1,
            "intern": 0,
            "cnm": 1,
            "staff_nurse": 3,
            "hca": 2,
        },
    },
    "ICU": {
        "nurse_patient_ratio": "1:1",
        "doctor_patient_ratio": "1:3",
        "day_shift": {
            "consultant": 1,
            "registrar": 1,
            "sho": 2,
            "intern": 0,
            "cnm": 1,
            "staff_nurse": 12,
            "hca": 2,
        },
        "night_shift": {
            "consultant": 0,
            "registrar": 1,
            "sho": 1,
            "intern": 0,
            "cnm": 1,
            "staff_nurse": 10,
            "hca": 2,
        },
        "weekend_day": {
            "consultant": 1,
            "registrar": 1,
            "sho": 1,
            "intern": 0,
            "cnm": 1,
            "staff_nurse": 10,
            "hca": 2,
        },
    },
    "HDU": {
        "nurse_patient_ratio": "1:2",
        "doctor_patient_ratio": "1:4",
        "day_shift": {
            "consultant": 1,
            "registrar": 1,
            "sho": 0,
            "intern": 0,
            "cnm": 1,
            "staff_nurse": 4,
            "hca": 1,
        },
        "night_shift": {
            "consultant": 0,
            "registrar": 1,
            "sho": 1,
            "intern": 0,
            "cnm": 1,
            "staff_nurse": 3,
            "hca": 1,
        },
        "weekend_day": {
            "consultant": 1,
            "registrar": 1,
            "sho": 0,
            "intern": 0,
            "cnm": 1,
            "staff_nurse": 3,
            "hca": 1,
        },
    },
    "Day_Ward": {
        "nurse_patient_ratio": "1:5",
        "doctor_patient_ratio": "1:10",
        "day_shift": {
            "consultant": 1,
            "registrar": 1,
            "sho": 0,
            "intern": 0,
            "cnm": 1,
            "staff_nurse": 4,
            "hca": 2,
        },
        "night_shift": {
            "consultant": 0,
            "registrar": 0,
            "sho": 0,
            "intern": 0,
            "cnm": 0,
            "staff_nurse": 0,
            "hca": 0,
        },
        "weekend_day": {
            "consultant": 0,
            "registrar": 0,
            "sho": 0,
            "intern": 0,
            "cnm": 0,
            "staff_nurse": 0,
            "hca": 0,
        },
    },
    "Discharge_Lounge": {
        "nurse_patient_ratio": "1:10",
        "doctor_patient_ratio": "n/a",
        "day_shift": {
            "consultant": 0,
            "registrar": 0,
            "sho": 0,
            "intern": 0,
            "cnm": 0,
            "staff_nurse": 2,
            "hca": 1,
        },
        "night_shift": {
            "consultant": 0,
            "registrar": 0,
            "sho": 0,
            "intern": 0,
            "cnm": 0,
            "staff_nurse": 1,
            "hca": 0,
        },
        "weekend_day": {
            "consultant": 0,
            "registrar": 0,
            "sho": 0,
            "intern": 0,
            "cnm": 0,
            "staff_nurse": 1,
            "hca": 1,
        },
    },
}

# ---------------------------------------------------------------------------
# SHIFT_PATTERNS — standard Irish hospital shift structures
# ---------------------------------------------------------------------------

SHIFT_PATTERNS: Dict[str, List[Dict[str, Any]]] = {
    "nursing_12h": [
        {"name": "Day", "start": "07:00", "end": "19:00"},
        {"name": "Night", "start": "19:00", "end": "07:00"},
    ],
    "nchd_12h": [
        {"name": "Day", "start": "08:00", "end": "20:00"},
        {"name": "Night", "start": "20:00", "end": "08:00"},
    ],
    "consultant": [
        {
            "name": "Weekday",
            "start": "08:00",
            "end": "18:00",
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
        },
    ],
}

# ---------------------------------------------------------------------------
# DEPARTMENT_SHIFT_CONFIG — per-department shift assignments
# ---------------------------------------------------------------------------

DEPARTMENT_SHIFT_CONFIG: Dict[str, Dict[str, Any]] = {
    dept: {
        "nursing_pattern": "nursing_12h",
        "doctor_pattern": "nchd_12h",
        "ewtd_max_weekly_hours": 48,
    }
    for dept in DEPARTMENTS
}

# ---------------------------------------------------------------------------
# HOSPITAL_CONFIG — global hospital parameters
# ---------------------------------------------------------------------------

HOSPITAL_CONFIG: Dict[str, Any] = {
    "name": "Irish HSE Model Hospital",
    "total_beds": 278,
    "pet_target_hours": 6,
    "nedocs_thresholds": {
        "normal": 0,
        "busy": 100,
        "overcrowded": 140,
        "crowded": 180,
        "severe": 200,
    },
    "mts_categories": {
        1: {"name": "Immediate", "color": "red", "max_wait_minutes": 0},
        2: {"name": "Very Urgent", "color": "orange", "max_wait_minutes": 10},
        3: {"name": "Urgent", "color": "yellow", "max_wait_minutes": 60},
        4: {"name": "Standard", "color": "green", "max_wait_minutes": 120},
        5: {"name": "Non-Urgent", "color": "blue", "max_wait_minutes": 240},
    },
    "alert_levels": {
        "green": "Normal operations — capacity within safe limits",
        "amber": "Escalation — approaching capacity, activate contingency",
        "red": "Full capacity — activate surge protocol, consider diversion",
        "black": "Crisis — no capacity, mandatory diversion in effect",
    },
    "default_shift_pattern": "nursing_12h",
    "ewtd_max_weekly_hours": 48,
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def generate_beds(department: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Generate a list of bed dicts for a single department.

    Beds are named ``<DEPT>-001``, ``<DEPT>-002``, etc. and carry the bed
    type and isolation flag derived from the department's ``bed_types``
    breakdown.
    """
    beds: List[Dict[str, Any]] = []
    bed_number = 1
    bed_types: Dict[str, int] = config.get("bed_types", {})
    cleaning_minutes: int = config.get("cleaning_minutes", 20)

    for bed_type, count in bed_types.items():
        for _ in range(count):
            bed_id = f"{department}-{bed_number:03d}"
            beds.append({
                "bed_id": bed_id,
                "department": department,
                "bed_type": bed_type,
                "is_isolation": bed_type == "isolation",
                "cleaning_minutes": cleaning_minutes,
            })
            bed_number += 1

    return beds


def get_all_beds() -> List[Dict[str, Any]]:
    """Return the full 278-bed inventory across all departments."""
    all_beds: List[Dict[str, Any]] = []
    for dept_name, dept_config in DEPARTMENTS.items():
        all_beds.extend(generate_beds(dept_name, dept_config))
    return all_beds


def build_weekly_schedule(department: str) -> List[Dict[str, Any]]:
    """Build a 7-day x 2-shift roster grid for *department*.

    Returns a list of 14 slot dicts (7 days x 2 shifts) each containing
    the day name, shift name, and expected staff counts drawn from
    :data:`STAFF_REGISTRY`.
    """
    staff = STAFF_REGISTRY.get(department)
    if staff is None:
        return []

    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    shifts = ["Day", "Night"]
    schedule: List[Dict[str, Any]] = []

    for day in days:
        is_weekend = day in ("Saturday", "Sunday")
        for shift_name in shifts:
            if is_weekend and shift_name == "Day":
                staff_counts = dict(staff["weekend_day"])
            elif shift_name == "Night":
                staff_counts = dict(staff["night_shift"])
            else:
                staff_counts = dict(staff["day_shift"])

            schedule.append({
                "day": day,
                "shift": shift_name,
                "staff": staff_counts,
            })

    return schedule


def get_current_shift(department: str) -> Dict[str, Any]:
    """Determine the current shift for *department* based on the shared SimClock.

    Returns a dict with ``shift_name``, ``staff``, ``pattern``, and the
    shift ``start``/``end`` times. The sim clock is authoritative — shift
    rotations reflect simulated time (Rule 1, Single Clock).
    """
    from shared.integration.sim_clock import get_sim_time as _sim_now
    now = _sim_now()
    hour = now.hour
    day_name = now.strftime("%A")
    is_weekend = day_name in ("Saturday", "Sunday")

    shift_config = DEPARTMENT_SHIFT_CONFIG.get(department, {})
    nursing_pattern = shift_config.get("nursing_pattern", "nursing_12h")
    pattern_slots = SHIFT_PATTERNS.get(nursing_pattern, SHIFT_PATTERNS["nursing_12h"])

    # Determine which shift slot we are in
    current_slot: Optional[Dict[str, Any]] = None
    for slot in pattern_slots:
        start_h = int(slot["start"].split(":")[0])
        end_h = int(slot["end"].split(":")[0])
        # Handle overnight wrap (e.g. 19:00 – 07:00)
        if start_h > end_h:
            if hour >= start_h or hour < end_h:
                current_slot = slot
                break
        else:
            if start_h <= hour < end_h:
                current_slot = slot
                break

    if current_slot is None:
        current_slot = pattern_slots[0]

    shift_name = current_slot["name"]

    # Pick the right staffing level
    staff = STAFF_REGISTRY.get(department, {})
    if is_weekend and shift_name == "Day":
        staff_counts = staff.get("weekend_day", {})
    elif shift_name == "Night":
        staff_counts = staff.get("night_shift", {})
    else:
        staff_counts = staff.get("day_shift", {})

    return {
        "department": department,
        "shift_name": shift_name,
        "start": current_slot["start"],
        "end": current_slot["end"],
        "pattern": nursing_pattern,
        "is_weekend": is_weekend,
        "staff": dict(staff_counts) if staff_counts else {},
    }
