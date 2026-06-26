"""Discrete Event Simulation engine for hospital patient flow.

Implements a priority-queue-based DES with:
  - Patient arrivals via Poisson processes calibrated on MIMIC data
  - Service times from log-normal distributions fitted to actual LOS
  - Department queues with capacity constraints and staffing levels
  - Transfer pathways derived from MIMIC transfer sequences

Usage:
    engine = DESEngine(config)
    engine.initialize()
    engine.run_until(sim_time=480.0)  # 8 hours
"""

from __future__ import annotations

import heapq
import logging
import math
import random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from shared.constants.hospital import (
    CAPACITIES as _HOSPITAL_CAPACITIES,
    SERVICE_PARAMS as _HOSPITAL_SERVICE_PARAMS,
    STAFF_DEFAULTS,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

class EventType(Enum):
    """Types of events in the hospital DES."""
    ARRIVAL = auto()
    TRANSFER = auto()
    DISCHARGE = auto()
    STAFF_CHANGE = auto()
    SERVICE_COMPLETE = auto()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Patient:
    """Represents a patient flowing through the hospital."""
    patient_id: int
    acuity: float  # 1.0 (low) to 5.0 (critical)
    arrival_time: float
    admission_type: str = "EMERGENCY"
    pathway: List[str] = field(default_factory=list)
    pathway_index: int = 0
    current_department: Optional[str] = None
    timestamps: Dict[str, float] = field(default_factory=dict)
    wait_times: Dict[str, float] = field(default_factory=dict)
    service_start_times: Dict[str, float] = field(default_factory=dict)
    total_wait: float = 0.0
    discharged: bool = False
    admit_status: str = ""  # "service" | "queued" | "rejected" (set by Department.admit_patient)

    def next_department(self) -> Optional[str]:
        """Return the next department in the patient's pathway, or None."""
        if self.pathway_index < len(self.pathway):
            return self.pathway[self.pathway_index]
        return None

    def advance_pathway(self) -> None:
        """Move to the next department in the pathway."""
        self.pathway_index += 1


@dataclass(order=True)
class Event:
    """A simulation event, ordered by time for the priority queue."""
    time: float
    event_type: EventType = field(compare=False)
    department: str = field(compare=False)
    patient: Optional[Patient] = field(compare=False, default=None)
    data: Dict[str, Any] = field(compare=False, default_factory=dict)


@dataclass
class StaffLevel:
    """Staffing levels for a department."""
    doctors: int = 2
    nurses: int = 6

    @property
    def total(self) -> int:
        return self.doctors + self.nurses

    @property
    def service_rate_multiplier(self) -> float:
        """Staffing affects service speed: more staff = faster service."""
        baseline_total = 8  # 2 doctors + 6 nurses
        ratio = self.total / baseline_total
        # Diminishing returns: sqrt scaling
        return max(0.3, min(2.0, math.sqrt(ratio)))


class Department:
    """Represents a hospital department with capacity, queue, and staff."""

    def __init__(
        self,
        name: str,
        capacity: int,
        staff: Optional[StaffLevel] = None,
        service_time_params: Optional[Tuple[float, float]] = None,
    ) -> None:
        """
        Parameters
        ----------
        name:
            Department identifier (e.g., "ED", "ICU").
        capacity:
            Maximum number of patients that can be served simultaneously.
        staff:
            Initial staffing levels.
        service_time_params:
            (log_mean, log_std) for log-normal service time distribution in hours.
        """
        self.name = name
        self.capacity = capacity
        defaults = STAFF_DEFAULTS.get(name, {"doctors": 2, "nurses": 6})
        self.staff = staff or StaffLevel(doctors=defaults["doctors"], nurses=defaults["nurses"])
        self.service_time_params = service_time_params or (1.5, 0.8)

        # State
        self.patients_in_service: List[Patient] = []
        self.queue: List[Patient] = []
        self.total_served: int = 0
        self.total_arrivals: int = 0
        self.cumulative_wait: float = 0.0
        self.cumulative_service: float = 0.0

    @property
    def patient_count(self) -> int:
        return len(self.patients_in_service) + len(self.queue)

    @property
    def occupancy_ratio(self) -> float:
        return len(self.patients_in_service) / max(1, self.capacity)

    @property
    def is_full(self) -> bool:
        return len(self.patients_in_service) >= self.capacity

    @property
    def avg_wait_time(self) -> float:
        if self.total_served == 0:
            return 0.0
        return self.cumulative_wait / self.total_served

    @property
    def avg_service_time(self) -> float:
        if self.total_served == 0:
            return 0.0
        return self.cumulative_service / self.total_served

    def sample_service_time(self, acuity: float = 3.0, rng: Optional[np.random.Generator] = None) -> float:
        """Sample a service time in hours from the log-normal distribution.

        Higher acuity patients take longer.
        """
        rng = rng or np.random.default_rng()
        log_mean, log_std = self.service_time_params
        # Acuity scaling: higher acuity = longer stay
        acuity_factor = 0.5 + (acuity / 5.0) * 1.0  # range [0.7, 1.5]
        base_time = rng.lognormal(log_mean, log_std)
        adjusted = base_time * acuity_factor / self.staff.service_rate_multiplier
        return max(0.1, adjusted)  # minimum 6 minutes

    # Cap each department's queue at 2× capacity so an overloaded ward
    # cannot grow its backlog without bound. Overflow is rejected at admit;
    # the caller is responsible for diverting the patient (e.g. direct
    # discharge, or transfer to a different ward).
    MAX_QUEUE_MULTIPLIER: float = 2.0

    def admit_patient(self, patient: Patient, current_time: float) -> bool:
        """Attempt to admit a patient. Returns True if admitted to service, False if queued.

        When the queue is already at ``MAX_QUEUE_MULTIPLIER × capacity``, the
        admission is refused entirely. Check ``admit_status`` on the patient
        or use ``can_admit()`` to distinguish queued-vs-rejected.
        """
        patient.timestamps[f"{self.name}_arrival"] = current_time

        if not self.is_full:
            self.total_arrivals += 1
            self.patients_in_service.append(patient)
            patient.service_start_times[self.name] = current_time
            patient.admit_status = "service"
            return True

        # Queue cap — reject rather than grow unbounded
        max_queue = int(self.capacity * self.MAX_QUEUE_MULTIPLIER)
        if len(self.queue) >= max_queue:
            patient.admit_status = "rejected"
            return False

        self.total_arrivals += 1
        self.queue.append(patient)
        patient.admit_status = "queued"
        return False

    def can_admit(self) -> bool:
        """True when the department has room in service or queue."""
        if not self.is_full:
            return True
        return len(self.queue) < int(self.capacity * self.MAX_QUEUE_MULTIPLIER)

    def try_dequeue(self, current_time: float) -> Optional[Patient]:
        """Try to move a queued patient into service. Returns the patient or None."""
        if self.queue and not self.is_full:
            patient = self.queue.pop(0)
            wait = current_time - patient.timestamps.get(f"{self.name}_arrival", current_time)
            patient.wait_times[self.name] = wait
            patient.total_wait += wait
            self.cumulative_wait += wait
            self.patients_in_service.append(patient)
            patient.service_start_times[self.name] = current_time
            return patient
        return None

    def discharge_patient(self, patient: Patient, current_time: float) -> None:
        """Remove a patient from service.

        Wait-time accounting: when an admission goes straight to service
        (no queueing, the common case while no dept is full),
        ``try_dequeue`` never fires and the per-dept ``cumulative_wait``
        stays at 0 → dashboard "Mean Wait Time" shows 0. Since the
        Kafka-driven flow (relocate / discharge) gives the engine a real
        time delta sourced from MIMIC ``transfers.intime`` /
        ``transfers.outtime`` (or ``admissions.dischtime``), we fall back
        to recording the time-in-dept as the patient's "wait" for this
        segment when no queue wait was tracked. This makes the dashboard
        reflect real MIMIC dept dwell times instead of zero, while
        leaving the original queue-wait logic intact for the rare cases
        when a dept actually fills up.
        """
        if patient in self.patients_in_service:
            self.patients_in_service.remove(patient)
            service_start = patient.service_start_times.get(self.name, current_time)
            service_time = current_time - service_start
            self.cumulative_service += service_time
            if self.name not in patient.wait_times:
                patient.wait_times[self.name] = service_time
                patient.total_wait += service_time
                self.cumulative_wait += service_time
            self.total_served += 1
            patient.timestamps[f"{self.name}_departure"] = current_time

    def reset(self) -> None:
        """Reset department state for a new simulation episode."""
        self.patients_in_service.clear()
        self.queue.clear()
        self.total_served = 0
        self.total_arrivals = 0
        self.cumulative_wait = 0.0
        self.cumulative_service = 0.0


# ---------------------------------------------------------------------------
# Pathway generator
# ---------------------------------------------------------------------------

# Default transition probabilities — Irish HSE hospital flow patterns
DEFAULT_TRANSITIONS: Dict[str, Dict[str, float]] = {
    "ED": {
        "MAU": 0.15,           # Medical assessment
        "SAU": 0.08,           # Surgical assessment
        "CDU": 0.10,           # Short-stay observation
        "Medicine": 0.20,      # Direct admit to Medicine
        "Surgery": 0.10,       # Direct admit to Surgery
        "Cardiology": 0.05,
        "ICU": 0.08,           # Critical patients
        "HDU": 0.04,           # High dependency
        "Discharge_Lounge": 0.20,  # Discharged from ED
    },
    "MAU": {
        "Medicine": 0.40,      # Admitted after assessment
        "Respiratory": 0.10,
        "Cardiology": 0.10,
        "ICU": 0.05,
        "Discharge_Lounge": 0.35,  # Discharged after assessment
    },
    "AMAU": {
        "Medicine": 0.35,
        "Cardiology": 0.10,
        "Respiratory": 0.10,
        "Discharge_Lounge": 0.45,
    },
    "SAU": {
        "Surgery": 0.40,       # Admitted for surgery
        "Orthopaedics": 0.15,
        "ICU": 0.05,
        "Discharge_Lounge": 0.40,
    },
    "CDU": {
        "Medicine": 0.25,
        "MAU": 0.15,
        "Discharge_Lounge": 0.60,  # Most CDU patients go home
    },
    "Medicine": {
        "ICU": 0.08,
        "HDU": 0.05,
        "Day_Ward": 0.02,
        "Discharge_Lounge": 0.85,
    },
    "Surgery": {
        "ICU": 0.10,
        "HDU": 0.08,
        "Discharge_Lounge": 0.82,
    },
    "Cardiology": {
        "ICU": 0.12,
        "HDU": 0.08,
        "Medicine": 0.05,
        "Discharge_Lounge": 0.75,
    },
    "Respiratory": {
        "ICU": 0.10,
        "HDU": 0.08,
        "Medicine": 0.02,
        "Discharge_Lounge": 0.80,
    },
    "Orthopaedics": {
        "ICU": 0.05,
        "HDU": 0.05,
        "Discharge_Lounge": 0.90,
    },
    "ICU": {
        "Medicine": 0.25,
        "Surgery": 0.15,
        "Cardiology": 0.10,
        "Respiratory": 0.10,
        "HDU": 0.15,           # Step-down to HDU
        "Discharge_Lounge": 0.25,
    },
    "HDU": {
        "Medicine": 0.20,
        "Surgery": 0.10,
        "Discharge_Lounge": 0.70,
    },
    "Day_Ward": {
        "Discharge_Lounge": 1.0,  # Day cases always discharge
    },
    "Discharge_Lounge": {},  # terminal
}


def generate_patient_pathway(
    entry_dept: str = "ED",
    max_transfers: int = 6,
    transitions: Optional[Dict[str, Dict[str, float]]] = None,
    rng: Optional[np.random.Generator] = None,
) -> List[str]:
    """Generate a patient pathway through hospital departments.

    Uses Markov chain transitions calibrated from MIMIC transfer data.
    """
    rng = rng or np.random.default_rng()
    transitions = transitions or DEFAULT_TRANSITIONS

    pathway = [entry_dept]
    current = entry_dept

    for _ in range(max_transfers):
        trans = transitions.get(current, {})
        if not trans:
            break

        depts = list(trans.keys())
        probs = list(trans.values())
        total = sum(probs)
        if total == 0:
            break
        probs = [p / total for p in probs]

        next_dept = rng.choice(depts, p=probs)
        pathway.append(next_dept)

        if next_dept == "Discharge_Lounge":
            break
        current = next_dept

    return pathway


# ---------------------------------------------------------------------------
# Default service time parameters (log-normal: log_mean, log_std in hours)
# Calibrated from MIMIC data patterns
# ---------------------------------------------------------------------------

# Imported from shared constants — single source of truth
DEFAULT_SERVICE_PARAMS: Dict[str, Tuple[float, float]] = dict(_HOSPITAL_SERVICE_PARAMS)
DEFAULT_CAPACITIES: Dict[str, int] = dict(_HOSPITAL_CAPACITIES)


# ---------------------------------------------------------------------------
# DES Engine
# ---------------------------------------------------------------------------

@dataclass
class DESConfig:
    """Configuration for the Discrete Event Simulation engine."""
    departments: List[str] = field(default_factory=lambda: [
        "ED", "MAU", "AMAU", "SAU", "CDU",
        "Medicine", "Surgery", "Cardiology", "Respiratory", "Orthopaedics",
        "ICU", "HDU", "Day_Ward", "Discharge_Lounge",
    ])
    capacities: Dict[str, int] = field(default_factory=lambda: dict(DEFAULT_CAPACITIES))
    service_params: Dict[str, Tuple[float, float]] = field(
        default_factory=lambda: dict(DEFAULT_SERVICE_PARAMS)
    )
    arrival_rate_per_hour: float = 12.0  # Base Poisson rate for ED arrivals
    admission_type_probs: Dict[str, float] = field(default_factory=lambda: {
        "EMERGENCY": 0.55,
        "URGENT": 0.25,
        "ELECTIVE": 0.15,
        "OBSERVATION": 0.05,
    })
    seed: int = 42
    max_patients: int = 10000
    # When True, the engine runs its own Poisson arrival stream.
    # When False, admissions must be injected from outside (e.g. DT's
    # process_admission forwards MIMIC admissions via /admit-patient) so
    # this DES mirrors the real simulation rather than running a parallel
    # synthetic arrival process.
    internal_arrivals: bool = False


class DESEngine:
    """Priority-queue-based Discrete Event Simulation for hospital operations.

    The engine manages:
      - Patient arrival events (Poisson process)
      - Service completion and transfer events
      - Department queues and capacity constraints
      - Staff level changes from MARL agent actions
    """

    def __init__(self, config: Optional[DESConfig] = None) -> None:
        self.config = config or DESConfig()
        self.rng = np.random.default_rng(self.config.seed)

        # Create departments
        self.departments: Dict[str, Department] = {}
        for dept_name in self.config.departments:
            self.departments[dept_name] = Department(
                name=dept_name,
                capacity=self.config.capacities.get(dept_name, 30),
                service_time_params=self.config.service_params.get(dept_name, (2.0, 0.8)),
            )

        # Event queue and state
        self.event_queue: List[Event] = []
        self.current_time: float = 0.0
        self.patients: Dict[int, Patient] = {}
        self.discharged_patients: List[Patient] = []
        self._next_patient_id: int = 0
        self._initialized: bool = False

        # Metrics
        self.metrics_history: List[Dict[str, Any]] = []

    def initialize(self) -> None:
        """Set up initial state and schedule first arrival events."""
        self.current_time = 0.0
        self.event_queue.clear()
        self.patients.clear()
        self.discharged_patients.clear()
        self._next_patient_id = 0

        for dept in self.departments.values():
            dept.reset()

        # Schedule first arrival only when the engine owns its arrival
        # stream. In external-feed mode, admissions land via inject_admission().
        if self.config.internal_arrivals:
            self._schedule_arrival(0.0)
        self._initialized = True

    def inject_admission(
        self,
        *,
        hadm_id: Optional[str] = None,
        subject_id: Optional[int] = None,
        entry_dept: str = "ED",
        acuity: float = 3.0,
        admission_type: str = "EMERGENCY",
        pathway: Optional[List[str]] = None,
    ) -> int:
        """Inject a real MIMIC admission into the DES.

        Bypasses the internal Poisson arrival process. Used when this engine
        is configured with ``internal_arrivals=False`` and admissions come
        from the shared simulation (data_ingestion / DigitalTwinOrchestrator).
        Returns the internal patient_id assigned by the DES.
        """
        patient = Patient(
            patient_id=self._next_patient_id,
            acuity=float(acuity),
            arrival_time=self.current_time,
            admission_type=admission_type,
        )
        patient.pathway = pathway or generate_patient_pathway(
            entry_dept=entry_dept,
            rng=self.rng,
        )
        if hadm_id is not None:
            patient.timestamps["external_hadm_id"] = hadm_id  # trace back to source
        self._next_patient_id += 1

        evt = Event(
            time=self.current_time,
            event_type=EventType.ARRIVAL,
            department=patient.pathway[0] if patient.pathway else entry_dept,
            patient=patient,
        )
        heapq.heappush(self.event_queue, evt)
        return patient.patient_id

    def reset(self) -> None:
        """Full reset for a new episode."""
        self.initialize()
        self.metrics_history.clear()

    def _schedule_arrival(self, after_time: float) -> None:
        """Schedule the next patient arrival using Poisson process."""
        if self._next_patient_id >= self.config.max_patients:
            return

        # Poisson inter-arrival time (exponential)
        # Modulate rate by time of day (sinusoidal pattern)
        hour_of_day = (after_time % 24.0)
        # Peak at 10am and 6pm, trough at 4am
        time_factor = 1.0 + 0.4 * math.sin(2 * math.pi * (hour_of_day - 4) / 24)
        rate = self.config.arrival_rate_per_hour * time_factor
        inter_arrival = self.rng.exponential(1.0 / max(0.1, rate))
        arrival_time = after_time + inter_arrival

        # Choose admission type
        types = list(self.config.admission_type_probs.keys())
        probs = list(self.config.admission_type_probs.values())
        total = sum(probs)
        probs = [p / total for p in probs]
        adm_type = self.rng.choice(types, p=probs)

        # Create patient
        patient = Patient(
            patient_id=self._next_patient_id,
            acuity=float(self.rng.uniform(1.0, 5.0)),
            arrival_time=arrival_time,
            admission_type=adm_type,
        )

        # Generate pathway
        entry = "ED" if adm_type in ("EMERGENCY", "URGENT") else self.rng.choice(
            ["ED", "MAU", "Medicine", "Day_Ward"]
        )
        patient.pathway = generate_patient_pathway(
            entry_dept=entry,
            rng=self.rng,
        )

        self._next_patient_id += 1

        event = Event(
            time=arrival_time,
            event_type=EventType.ARRIVAL,
            department=patient.pathway[0] if patient.pathway else "ED",
            patient=patient,
        )
        heapq.heappush(self.event_queue, event)

    def _handle_arrival(self, event: Event) -> None:
        """Process a patient arrival event."""
        patient = event.patient
        if patient is None:
            return

        dept_name = event.department
        dept = self.departments.get(dept_name)
        if dept is None:
            logger.warning(f"Unknown department: {dept_name}")
            return

        patient.current_department = dept_name
        self.patients[patient.patient_id] = patient

        admitted = dept.admit_patient(patient, self.current_time)

        if admitted:
            # Schedule service completion
            service_time = dept.sample_service_time(patient.acuity, self.rng)
            completion_event = Event(
                time=self.current_time + service_time,
                event_type=EventType.SERVICE_COMPLETE,
                department=dept_name,
                patient=patient,
            )
            heapq.heappush(self.event_queue, completion_event)

        # Schedule next arrival only when running the internal Poisson stream
        if self.config.internal_arrivals:
            self._schedule_arrival(self.current_time)

    def _handle_service_complete(self, event: Event) -> None:
        """Handle service completion: transfer or discharge."""
        patient = event.patient
        if patient is None:
            return

        dept_name = event.department
        dept = self.departments.get(dept_name)
        if dept is None:
            return

        # Remove from current department
        dept.discharge_patient(patient, self.current_time)

        # Advance pathway
        patient.advance_pathway()
        next_dept = patient.next_department()

        if next_dept is None or next_dept == "Discharge_Lounge":
            # Handle discharge lounge or final discharge
            if next_dept == "Discharge_Lounge":
                dl = self.departments.get("Discharge_Lounge")
                if dl:
                    # Bypass semantics: Discharge_Lounge is a short-stay
                    # transitional area, not a bottleneck. If it is full,
                    # the patient skips it and discharges directly — this
                    # mirrors real-world hospitals where DL overflow means
                    # the patient just goes home without waiting for a seat.
                    if dl.is_full:
                        self._discharge_patient(patient)
                        return
                    admitted = dl.admit_patient(patient, self.current_time)
                    if admitted:
                        svc = dl.sample_service_time(patient.acuity, self.rng)
                        heapq.heappush(self.event_queue, Event(
                            time=self.current_time + svc,
                            event_type=EventType.DISCHARGE,
                            department="Discharge_Lounge",
                            patient=patient,
                        ))
                        return
                    # Admit returned False: queue pathology — direct discharge.
                    self._discharge_patient(patient)
                    return

            # Final discharge
            self._discharge_patient(patient)
        else:
            # Transfer to next department
            self._transfer_patient(patient, next_dept)

        # Try to dequeue waiting patients in the vacated department
        dequeued = dept.try_dequeue(self.current_time)
        if dequeued:
            service_time = dept.sample_service_time(dequeued.acuity, self.rng)
            heapq.heappush(self.event_queue, Event(
                time=self.current_time + service_time,
                event_type=EventType.SERVICE_COMPLETE,
                department=dept_name,
                patient=dequeued,
            ))

    def _transfer_patient(self, patient: Patient, target_dept: str) -> None:
        """Transfer a patient to the target department.

        If the target is saturated (queue at cap), the patient is diverted
        to Discharge_Lounge — this models HSE hospital pressure release
        where a patient whose ward is full is either held short-stay or
        sent home early. Discharge_Lounge itself bypasses when full.
        """
        dept = self.departments.get(target_dept)
        if dept is None:
            self._discharge_patient(patient)
            return

        # Overflow handling — reject before queue grows without bound
        if not dept.can_admit():
            if target_dept == "Discharge_Lounge":
                self._discharge_patient(patient)
            else:
                # Divert to Discharge_Lounge (which bypasses if also full)
                self._transfer_patient(patient, "Discharge_Lounge")
            return

        patient.current_department = target_dept
        admitted = dept.admit_patient(patient, self.current_time)

        if admitted:
            service_time = dept.sample_service_time(patient.acuity, self.rng)
            evt_type = EventType.SERVICE_COMPLETE
            if target_dept == "Discharge_Lounge":
                evt_type = EventType.DISCHARGE
            heapq.heappush(self.event_queue, Event(
                time=self.current_time + service_time,
                event_type=evt_type,
                department=target_dept,
                patient=patient,
            ))

    def _discharge_patient(self, patient: Patient) -> None:
        """Fully discharge a patient from the hospital."""
        patient.discharged = True
        patient.timestamps["discharge"] = self.current_time
        self.discharged_patients.append(patient)
        self.patients.pop(patient.patient_id, None)

    # ------------------------------------------------------------------
    # External event hooks — Kafka transfer / discharge events from
    # data_ingestion drive these so the engine's view of "where each
    # patient is" stays anchored to the real MIMIC trajectory rather
    # than diverging via the internal pathway-based scheduler.
    # ------------------------------------------------------------------
    def find_patient_by_hadm(self, hadm_id: Any) -> Optional[Patient]:
        """Look up an active patient by their external (MIMIC) hadm_id."""
        if hadm_id is None:
            return None
        target = str(hadm_id)
        for p in self.patients.values():
            if str(p.timestamps.get("external_hadm_id", "")) == target:
                return p
        return None

    def _purge_patient_events(self, patient: Patient) -> None:
        """Drop any pending events scheduled for this patient.

        Called before relocating or discharging out-of-band so the
        previously-enqueued service-completion / discharge events don't
        fire later and double-handle the patient (e.g. discharging them
        again after they've already been moved on).
        """
        if not self.event_queue:
            return
        kept = [e for e in self.event_queue if e.patient is not patient]
        if len(kept) != len(self.event_queue):
            self.event_queue = kept
            heapq.heapify(self.event_queue)

    def relocate_patient_by_hadm(self, hadm_id: Any, target_dept: str) -> bool:
        """Move a patient to ``target_dept`` immediately.

        Triggered by the ``patient_transferred`` Kafka event from
        data_ingestion. Removes the patient from their current
        department's service list / queue, cancels any pending
        service-complete event, and admits them into the target — all at
        ``current_time`` so the wait-time accounting stays sensible.

        Returns ``False`` when the patient isn't tracked here yet (e.g.
        the admission Kafka event hasn't arrived) or when the target
        department is unknown.
        """
        patient = self.find_patient_by_hadm(hadm_id)
        if patient is None:
            return False
        if target_dept not in self.departments:
            return False

        current = self.departments.get(patient.current_department) if patient.current_department else None
        if current is not None:
            if patient in current.patients_in_service:
                current.discharge_patient(patient, self.current_time)
            elif patient in current.queue:
                current.queue.remove(patient)
        self._purge_patient_events(patient)
        self._transfer_patient(patient, target_dept)
        return True

    def discharge_patient_by_hadm(self, hadm_id: Any) -> bool:
        """Fully discharge a patient triggered by an external event.

        Mirrors the bookkeeping of ``_discharge_patient`` but starts from
        a hadm_id and works regardless of which department currently
        holds the patient.
        """
        patient = self.find_patient_by_hadm(hadm_id)
        if patient is None:
            return False
        current = self.departments.get(patient.current_department) if patient.current_department else None
        if current is not None:
            if patient in current.patients_in_service:
                current.discharge_patient(patient, self.current_time)
            elif patient in current.queue:
                current.queue.remove(patient)
        self._purge_patient_events(patient)
        self._discharge_patient(patient)
        return True

    def _handle_discharge(self, event: Event) -> None:
        """Handle explicit discharge event (from discharge lounge)."""
        patient = event.patient
        if patient is None:
            return

        dept = self.departments.get(event.department)
        if dept:
            dept.discharge_patient(patient, self.current_time)
            # Dequeue
            dequeued = dept.try_dequeue(self.current_time)
            if dequeued:
                service_time = dept.sample_service_time(dequeued.acuity, self.rng)
                heapq.heappush(self.event_queue, Event(
                    time=self.current_time + service_time,
                    event_type=EventType.DISCHARGE,
                    department=event.department,
                    patient=dequeued,
                ))

        self._discharge_patient(patient)

    def _handle_staff_change(self, event: Event) -> None:
        """Apply a staffing change to a department."""
        dept = self.departments.get(event.department)
        if dept is None:
            return

        delta_doctors = event.data.get("delta_doctors", 0)
        delta_nurses = event.data.get("delta_nurses", 0)
        # Get ERP baseline to clamp within ±50%
        defaults = STAFF_DEFAULTS.get(event.department, {"doctors": 2, "nurses": 6})
        base_docs = defaults["doctors"]
        base_nurses = defaults["nurses"]
        dept.staff.doctors = max(
            max(1, int(base_docs * 0.5)),
            min(int(base_docs * 1.5), dept.staff.doctors + delta_doctors)
        )
        dept.staff.nurses = max(
            max(1, int(base_nurses * 0.5)),
            min(int(base_nurses * 1.5), dept.staff.nurses + delta_nurses)
        )

    def process_next_event(self) -> Optional[Event]:
        """Process the next event in the queue. Returns the event or None."""
        if not self.event_queue:
            return None

        event = heapq.heappop(self.event_queue)
        self.current_time = event.time

        handlers = {
            EventType.ARRIVAL: self._handle_arrival,
            EventType.SERVICE_COMPLETE: self._handle_service_complete,
            EventType.DISCHARGE: self._handle_discharge,
            EventType.STAFF_CHANGE: self._handle_staff_change,
        }

        handler = handlers.get(event.event_type)
        if handler:
            handler(event)

        return event

    def run_until(self, sim_time: float) -> List[Event]:
        """Run the simulation until the given simulation time.

        Returns a list of all processed events.
        """
        if not self._initialized:
            self.initialize()

        processed: List[Event] = []
        while self.event_queue and self.event_queue[0].time <= sim_time:
            event = self.process_next_event()
            if event:
                processed.append(event)

        # Advance the clock to ``sim_time`` even when no events fired during
        # this window. ``current_time`` is otherwise only updated by
        # ``process_next_event``, which would leave the clock pinned at zero
        # whenever a step() runs against an empty queue (external-feed mode
        # before any admissions arrive). The metrics rollup uses
        # ``current_time`` as the chart x-axis, so a stuck clock makes
        # downstream Wait/Throughput series flatline at sim_time_h=0.
        if sim_time > self.current_time:
            self.current_time = sim_time

        return processed

    def step(self, duration: float = 1.0) -> List[Event]:
        """Advance the simulation by ``duration`` hours.

        Returns the list of events processed during this step.
        """
        if not self._initialized:
            self.initialize()

        target_time = self.current_time + duration
        return self.run_until(target_time)

    def apply_actions(self, actions: Dict[str, Dict[str, float]]) -> None:
        """Apply MARL agent actions to departments.

        Parameters
        ----------
        actions:
            Dict mapping department name to action dict with keys:
            - staff_adjustment_doctors: float (rounded to int change)
            - staff_adjustment_nurses: float (rounded to int change)
            - transfer_priority: float (0-1, not yet used)
            - discharge_threshold: float (0-1, not yet used)
        """
        for dept_name, action in actions.items():
            dept = self.departments.get(dept_name)
            if dept is None:
                continue

            delta_docs = int(round(action.get("staff_adjustment_doctors", 0)))
            delta_nurses = int(round(action.get("staff_adjustment_nurses", 0)))

            if delta_docs != 0 or delta_nurses != 0:
                heapq.heappush(self.event_queue, Event(
                    time=self.current_time,
                    event_type=EventType.STAFF_CHANGE,
                    department=dept_name,
                    data={"delta_doctors": delta_docs, "delta_nurses": delta_nurses},
                ))

    def get_state(self) -> Dict[str, Dict[str, Any]]:
        """Get the current state of all departments.

        Returns a dict mapping department name to state dict.
        """
        state = {}
        for name, dept in self.departments.items():
            state[name] = {
                "patient_count": dept.patient_count,
                "patients_in_service": len(dept.patients_in_service),
                "queue_length": len(dept.queue),
                "capacity": dept.capacity,
                "occupancy_ratio": dept.occupancy_ratio,
                "avg_wait_time": dept.avg_wait_time,
                "avg_service_time": dept.avg_service_time,
                "total_served": dept.total_served,
                "total_arrivals": dept.total_arrivals,
                "staff_doctors": dept.staff.doctors,
                "staff_nurses": dept.staff.nurses,
            }
        state["_global"] = {
            "current_time": self.current_time,
            "active_patients": len(self.patients),
            "discharged_patients": len(self.discharged_patients),
            "pending_events": len(self.event_queue),
        }
        return state

    def get_metrics(self) -> Dict[str, Any]:
        """Compute aggregate performance metrics."""
        total_wait = sum(p.total_wait for p in self.discharged_patients)
        n_discharged = len(self.discharged_patients)

        dept_metrics = {}
        for name, dept in self.departments.items():
            dept_metrics[name] = {
                "avg_wait_time": dept.avg_wait_time,
                "avg_service_time": dept.avg_service_time,
                "occupancy_ratio": dept.occupancy_ratio,
                "throughput": dept.total_served,
                "queue_length": len(dept.queue),
            }

        return {
            "simulation_time": self.current_time,
            "total_discharged": n_discharged,
            "mean_total_wait": total_wait / max(1, n_discharged),
            "mean_los": (
                sum(
                    p.timestamps.get("discharge", 0) - p.arrival_time
                    for p in self.discharged_patients
                ) / max(1, n_discharged)
            ),
            "active_patients": len(self.patients),
            "departments": dept_metrics,
        }
