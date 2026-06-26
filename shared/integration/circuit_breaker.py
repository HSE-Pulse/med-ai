"""Circuit breaker for inter-service HTTP calls.

Required by HSE cyber-incident-response guidance (post-2021 Conti attack):
if a downstream service starts failing repeatedly, stop calling it for a
cooldown period rather than cascading latency back to the caller. Implements
the classic three-state machine — closed / open / half-open.

The breaker is wired into ``ModuleClient`` in
``shared/integration/service_client.py``. Each service gets its own breaker
instance keyed by module name.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class BreakerState(str, Enum):
    CLOSED = "closed"      # healthy, calls go through
    OPEN = "open"          # tripped, calls fail fast
    HALF_OPEN = "half_open"  # probe window — allow one call


@dataclass
class BreakerStats:
    state: BreakerState = BreakerState.CLOSED
    consecutive_failures: int = 0
    failure_window_start: float = 0.0
    opened_at: float = 0.0
    last_transition: float = field(default_factory=time.time)
    trip_count: int = 0


class CircuitOpenError(RuntimeError):
    """Raised when a call is short-circuited because the breaker is open."""


class CircuitBreaker:
    """Per-service breaker with failure counting and timed cooldown.

    Parameters
    ----------
    name:
        Identifier for logging — typically the module/service name.
    failure_threshold:
        Consecutive failures inside ``failure_window_s`` that open the breaker.
    failure_window_s:
        Sliding window over which failures are counted.
    cooldown_s:
        Time to stay open before moving to half-open.
    on_transition:
        Optional callback invoked with (name, new_state) on every transition.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        failure_window_s: float = 60.0,
        cooldown_s: float = 30.0,
        on_transition: Optional[Callable[[str, BreakerState], None]] = None,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.failure_window_s = failure_window_s
        self.cooldown_s = cooldown_s
        self._on_transition = on_transition
        self._stats = BreakerStats()
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ state accessors
    @property
    def state(self) -> BreakerState:
        return self._stats.state

    def snapshot(self) -> dict:
        s = self._stats
        return {
            "name": self.name,
            "state": s.state.value,
            "consecutive_failures": s.consecutive_failures,
            "trip_count": s.trip_count,
            "last_transition": s.last_transition,
            "opened_at": s.opened_at,
        }

    # ------------------------------------------------------------------ gating
    async def allow(self) -> bool:
        """Return True if a call may proceed; flips to half-open when cooldown expires."""
        async with self._lock:
            now = time.time()
            if self._stats.state == BreakerState.OPEN:
                if now - self._stats.opened_at >= self.cooldown_s:
                    self._transition(BreakerState.HALF_OPEN)
                    return True
                return False
            return True

    async def record_success(self) -> None:
        async with self._lock:
            if self._stats.state in (BreakerState.OPEN, BreakerState.HALF_OPEN):
                logger.info("circuit_breaker_closed", extra={"service": self.name})
                self._transition(BreakerState.CLOSED)
            self._stats.consecutive_failures = 0
            self._stats.failure_window_start = 0.0

    async def record_failure(self) -> None:
        async with self._lock:
            now = time.time()
            # Reset rolling window if outside bounds
            if (
                self._stats.failure_window_start == 0
                or now - self._stats.failure_window_start > self.failure_window_s
            ):
                self._stats.failure_window_start = now
                self._stats.consecutive_failures = 0

            self._stats.consecutive_failures += 1

            if self._stats.state == BreakerState.HALF_OPEN:
                logger.warning("circuit_breaker_reopened", extra={"service": self.name})
                self._stats.opened_at = now
                self._transition(BreakerState.OPEN)
                return

            if (
                self._stats.state == BreakerState.CLOSED
                and self._stats.consecutive_failures >= self.failure_threshold
            ):
                logger.warning(
                    "circuit_breaker_opened",
                    extra={
                        "service": self.name,
                        "failures": self._stats.consecutive_failures,
                    },
                )
                self._stats.opened_at = now
                self._stats.trip_count += 1
                self._transition(BreakerState.OPEN)

    # ------------------------------------------------------------------ internal
    def _transition(self, new_state: BreakerState) -> None:
        self._stats.state = new_state
        self._stats.last_transition = time.time()
        if self._on_transition:
            try:
                self._on_transition(self.name, new_state)
            except Exception:  # noqa: BLE001 - callback must not propagate
                logger.exception("circuit_breaker_callback_failed")


__all__ = ["CircuitBreaker", "BreakerState", "BreakerStats", "CircuitOpenError"]
