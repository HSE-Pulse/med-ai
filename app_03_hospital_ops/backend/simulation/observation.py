"""Shared MARL observation builder.

Deliberately free of any gymnasium dependency so the live service can import
it. `environment.py` pulls in gymnasium, which is present in the training
image but NOT in the per-service runtime image — importing the env from
`app/main.py` crashes hospital_ops on startup, which is why main.py used to
redeclare STATE_DIM locally and hand-roll its own observation vector.

That divergence is exactly what this module exists to prevent: training and
inference now build the observation from one definition, and the definition
lives somewhere both can reach.

State layout (12-dim), matching the trained MADDPG checkpoint::

    [patient_count, capacity_ratio, avg_wait_time, avg_los,
     admission_rate_1h, admission_rate_4h, staffing_ratio,
     pending_transfers_in, pending_transfers_out, acuity_mean,
     time_of_day_sin, time_of_day_cos]
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Optional

import numpy as np

STATE_DIM = 12
ACTION_DIM = 4


def build_dept_observation(
    engine: Any,
    dept_name: str,
    recent_arrivals: Optional[Iterable[float]] = None,
    patient_count_override: Optional[int] = None,
) -> np.ndarray:
    """Compute the 12-dimensional observation for a single department.

    The live service previously filled only indices 0/1/2/6 and left the
    other eight at zero. Two of those zeros are unreachable during training:
    ``acuity_mean`` never drops below 1.0 (it defaults to 3.0 for an empty
    ward) and ``tod_sin``/``tod_cos`` satisfy sin^2 + cos^2 = 1, so (0, 0)
    cannot occur. The policy was therefore evaluated well outside its
    training distribution, producing incoherent actions — "shed 3 doctors,
    add 5 nurses" on a 0%-occupancy ward — which earlier code suppressed
    with non-negative clamps and a rule-based fallback instead of fixing
    the input.

    ``recent_arrivals`` is this department's arrival-timestamp list in engine
    time units. Callers that cannot supply it get arrivals_1h/4h = 0; every
    other feature is still populated faithfully.
    """
    dept = engine.departments.get(dept_name)
    if dept is None:
        return np.zeros(STATE_DIM, dtype=np.float32)

    # Basic counts.
    #
    # `patient_count_override` carries the REAL hospital census. It exists
    # because notify-census deliberately stopped injecting patients into the
    # DES (that produced phantom admissions), so the engine's own
    # patient_count no longer tracks the live hospital — the policy was
    # observing a near-empty simulation while wards actually held 28-38
    # patients. The staffing display already reconciled the two via
    # max(des, real); the policy did not, which is why staffing never moved
    # when census tripled.
    #
    # Both derived values are kept inside the training distribution:
    #   * patient_count = in_service + queue during training, so it MAY
    #     exceed capacity — no clamp.
    #   * occupancy_ratio = in_service / capacity, and in_service is capped
    #     at capacity, so it never exceeded 1.0 in training — clamped here.
    capacity = max(1, int(getattr(dept, "capacity", 1) or 1))
    if patient_count_override is not None:
        patient_count = float(max(int(patient_count_override), int(dept.patient_count)))
        capacity_ratio = min(1.0, patient_count / capacity)
    else:
        patient_count = float(dept.patient_count)
        capacity_ratio = dept.occupancy_ratio
    avg_wait = dept.avg_wait_time
    avg_los = dept.avg_service_time

    # Admission rates (from recent arrival tracking)
    recent = list(recent_arrivals or [])
    current_time = engine.current_time
    arrivals_1h = sum(1 for t in recent if current_time - t <= 1.0)
    arrivals_4h = sum(1 for t in recent if current_time - t <= 4.0)

    # Staffing ratio — use actual department defaults, not flat 8
    from shared.constants.hospital import STAFF_DEFAULTS
    defaults = STAFF_DEFAULTS.get(dept_name, {"doctors": 2, "nurses": 6})
    baseline_staff = max(1, defaults["doctors"] + defaults["nurses"])
    staffing_ratio = dept.staff.total / baseline_staff

    # Pending transfers
    pending_in = 0
    pending_out = 0
    for evt in engine.event_queue:
        if evt.event_type.name == "TRANSFER":
            if evt.department == dept_name:
                pending_in += 1
        elif evt.event_type.name == "SERVICE_COMPLETE":
            if evt.department == dept_name:
                pending_out += 1

    # Mean acuity
    all_patients = dept.patients_in_service + dept.queue
    acuity_mean = (
        float(np.mean([p.acuity for p in all_patients]))
        if all_patients else 3.0
    )

    # Time of day encoding
    hour = current_time % 24.0
    tod_sin = math.sin(2 * math.pi * hour / 24.0)
    tod_cos = math.cos(2 * math.pi * hour / 24.0)

    return np.array([
        patient_count,
        capacity_ratio,
        avg_wait,
        avg_los,
        float(arrivals_1h),
        float(arrivals_4h),
        staffing_ratio,
        float(pending_in),
        float(pending_out),
        acuity_mean,
        tod_sin,
        tod_cos,
    ], dtype=np.float32)


class ArrivalWindow:
    """Reconstruct windowed arrival timestamps from cumulative counters.

    The training env appended an arrival timestamp per admission event. The
    live service has no such hook, only each department's monotonic
    ``total_arrivals``. Diffing that counter each tick recovers the same
    signal closely enough for arrivals_1h / arrivals_4h, which would
    otherwise be the last two features still pinned at zero.
    """

    def __init__(self, window_hours: float = 4.0, max_per_tick: int = 500) -> None:
        self.window_hours = window_hours
        self.max_per_tick = max_per_tick
        self._times: Dict[str, list] = {}
        self._last_count: Dict[str, int] = {}

    def update(self, engine: Any) -> Dict[str, list]:
        now = float(getattr(engine, "current_time", 0.0) or 0.0)
        for name, dept in engine.departments.items():
            seen = int(getattr(dept, "total_arrivals", 0) or 0)
            prev = self._last_count.get(name, seen)
            if seen > prev:
                self._times.setdefault(name, []).extend(
                    [now] * min(seen - prev, self.max_per_tick)
                )
            self._last_count[name] = seen
            if name in self._times:
                self._times[name] = [
                    t for t in self._times[name] if now - t <= self.window_hours
                ]
        return self._times

    def get(self, dept_name: str) -> list:
        return self._times.get(dept_name, [])
