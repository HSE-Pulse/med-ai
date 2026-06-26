"""Simulation clock with configurable time acceleration.

Provides a virtual clock that maps real elapsed time to accelerated
simulation time.  At speed=10, one real second equals ten sim-seconds.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta


class SimClock:
    """Thread-safe simulation clock with configurable speed multiplier."""

    def __init__(self, speed: float = 1.0, start_offset_hours: float = 0) -> None:
        if speed <= 0:
            raise ValueError("speed must be positive")
        self._lock = threading.Lock()
        self._speed = speed
        # Anchor points: the real-world timestamp and the sim-world timestamp
        # at the moment we last (re)set speed.
        self._anchor_real = time.time()
        self._anchor_sim = datetime.utcnow() + timedelta(hours=start_offset_hours)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def speed(self) -> float:
        with self._lock:
            return self._speed

    def now(self) -> datetime:
        """Return the current simulation time."""
        with self._lock:
            elapsed_real = time.time() - self._anchor_real
            elapsed_sim = elapsed_real * self._speed
            return self._anchor_sim + timedelta(seconds=elapsed_sim)

    def set_speed(self, speed: float) -> None:
        """Change the speed multiplier without jumping sim time.

        We re-anchor so that the current sim-time is preserved and only
        future progression uses the new speed.
        """
        if speed <= 0:
            raise ValueError("speed must be positive")
        with self._lock:
            # Capture current sim time under the old speed
            elapsed_real = time.time() - self._anchor_real
            elapsed_sim = elapsed_real * self._speed
            current_sim = self._anchor_sim + timedelta(seconds=elapsed_sim)
            # Re-anchor
            self._anchor_real = time.time()
            self._anchor_sim = current_sim
            self._speed = speed

    def reset(self) -> None:
        """Reset simulation clock to current system time (UTC).

        After reset, sim_time == wall_clock_time (UTC). The speed is preserved
        so the simulation continues at the configured rate from now.
        """
        with self._lock:
            self._anchor_real = time.time()
            self._anchor_sim = datetime.utcnow()

    def sim_elapsed_hours(self) -> float:
        """Total hours elapsed in simulation time since creation."""
        with self._lock:
            elapsed_real = time.time() - self._anchor_real
            elapsed_sim = elapsed_real * self._speed
            return elapsed_sim / 3600.0

    def real_seconds_until(self, sim_target: datetime) -> float:
        """How many *real* seconds until the sim clock reaches *sim_target*.

        Returns 0.0 if the target is already in the past.
        """
        with self._lock:
            current_sim = self._anchor_sim + timedelta(
                seconds=(time.time() - self._anchor_real) * self._speed
            )
            remaining_sim = (sim_target - current_sim).total_seconds()
            if remaining_sim <= 0:
                return 0.0
            return remaining_sim / self._speed

    def __repr__(self) -> str:
        return f"SimClock(speed={self.speed}, now={self.now().isoformat()})"
