"""Single-clock abstraction for the Digital Twin simulation.

The simulation engine (SimEngine, port 8207) is the authoritative source of
time. All modules must derive timestamps from ``SimClock.get_sim_time()``
rather than ``datetime.now()`` so that events, LOS, boarding timers, and
compliance audit trails stay consistent across the 12+ microservices.

Typical usage
-------------

>>> from shared.integration.sim_clock import SimClock
>>> clock = SimClock.get_instance()
>>> clock.get_sim_time()
datetime.datetime(2180, 5, 6, 22, 23, tzinfo=datetime.timezone.utc)

Simulation engine seeds the clock on start/reset via ``set_anchor``. Other
services should call ``await clock.attach_remote()`` in their startup
hook, which starts a background poller that anchors this process's
clock to SimEngine's ``/sim/clock`` snapshot AND mirrors the speed
multiplier from ``/state``. Without ``attach_remote``, the local clock
ticks at wall-clock rate (1×) which silently drifts whenever the sim is
running at any other speed.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _wall_now() -> datetime:
    """Wall-clock UTC now. Kept as a single call-site so tests can monkey-patch."""
    return datetime.now(timezone.utc)


@dataclass
class SimClockState:
    """Serialisable snapshot of clock state — shipped between services."""

    sim_time_iso: str
    anchor_wall_iso: str
    offset_seconds: float
    running: bool


class SimClock:
    """Process-local singleton that tracks simulated time.

    The clock works as a linear map from wall-clock to simulated time::

        sim_time = anchor_sim + (wall_now - anchor_wall)

    Starting the clock records the current wall time as the anchor. Setting
    the sim time updates the anchors so subsequent reads move forward at
    wall-clock rate from the new anchor.
    """

    _instance: "SimClock | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._anchor_wall: datetime = _wall_now()
        self._anchor_sim: datetime = self._anchor_wall
        self._running: bool = False
        # Speed multiplier mirrored from data_ingestion. Default 1.0 so a
        # process that never calls ``attach_remote`` still produces a
        # sensible clock — its sim_time advances at wall rate.
        self._speed: float = 1.0
        self._remote_url: Optional[str] = None
        self._remote_task: Optional[asyncio.Task] = None
        self._state_lock = threading.Lock()

    # ------------------------------------------------------------------ singleton
    @classmethod
    def get_instance(cls) -> "SimClock":
        """Return the process-wide clock singleton."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = SimClock()
        return cls._instance

    @classmethod
    def reset_singleton(cls) -> None:
        """Drop the singleton — tests only."""
        with cls._lock:
            cls._instance = None

    # ------------------------------------------------------------------ accessors
    def get_sim_time(self) -> datetime:
        """Return the current simulated time (UTC, timezone-aware).

        Extrapolates from the captured anchor at ``self._speed`` × wall.
        For services attached to a remote authoritative clock (see
        ``attach_remote``), ``self._speed`` is mirrored from
        data_ingestion's ``/state`` so the local clock ticks at the same
        cadence as the simulator. Default speed of 1.0 keeps behaviour
        unchanged for processes that don't attach (e.g. tests, scripts).
        """
        with self._state_lock:
            if not self._running:
                return self._anchor_sim
            elapsed = (_wall_now() - self._anchor_wall).total_seconds()
            return self._anchor_sim + timedelta(seconds=elapsed * self._speed)

    def is_sim_running(self) -> bool:
        with self._state_lock:
            return self._running

    # ------------------------------------------------------------------ control (SimEngine only)
    def start(self) -> None:
        """Start the clock — wall elapsed time begins accumulating as sim time."""
        with self._state_lock:
            if not self._running:
                self._anchor_wall = _wall_now()
                self._running = True

    def stop(self) -> None:
        """Pause the clock; ``get_sim_time`` will return the frozen anchor."""
        with self._state_lock:
            if self._running:
                self._anchor_sim = self._anchor_sim + (_wall_now() - self._anchor_wall)
                self._anchor_wall = _wall_now()
                self._running = False

    def set_anchor(self, sim_time: datetime, running: bool = True, speed: Optional[float] = None) -> None:
        """Seed the clock to ``sim_time`` and optionally start running.

        Called by SimEngine ``/reset`` / start, and by the remote
        refresher in non-source processes (which also passes ``speed``
        so ``get_sim_time`` extrapolates at the same cadence as the
        simulator).
        """
        if sim_time.tzinfo is None:
            sim_time = sim_time.replace(tzinfo=timezone.utc)
        with self._state_lock:
            self._anchor_sim = sim_time
            self._anchor_wall = _wall_now()
            self._running = running
            if speed is not None and speed > 0:
                self._speed = float(speed)

    def set_speed(self, speed: float) -> None:
        """Update the speed multiplier without jumping sim time."""
        if speed <= 0:
            return
        with self._state_lock:
            # Re-anchor to keep current sim_time continuous across the change
            wall_now = _wall_now()
            elapsed = (wall_now - self._anchor_wall).total_seconds()
            current_sim = self._anchor_sim + timedelta(seconds=elapsed * self._speed)
            self._anchor_sim = current_sim
            self._anchor_wall = wall_now
            self._speed = float(speed)

    def set_from_state(self, state: SimClockState) -> None:
        """Hydrate from a snapshot received over the wire."""
        sim_time = datetime.fromisoformat(state.sim_time_iso)
        self.set_anchor(sim_time, running=state.running)

    # ------------------------------------------------------------------ advance (simulation-mode fast-forward)
    def advance(self, delta: timedelta) -> None:
        """Jump the sim clock forward by ``delta`` — SimEngine batch mode."""
        with self._state_lock:
            if self._running:
                # Absorb elapsed wall time × speed into the anchor before
                # adding delta, so the manual jump composes cleanly with
                # whatever the simulator was already accumulating.
                elapsed = (_wall_now() - self._anchor_wall).total_seconds()
                self._anchor_sim = self._anchor_sim + timedelta(seconds=elapsed * self._speed)
                self._anchor_wall = _wall_now()
            self._anchor_sim = self._anchor_sim + delta

    # ------------------------------------------------------------------ remote anchor
    async def attach_remote(
        self,
        clock_url: Optional[str] = None,
        state_url: Optional[str] = None,
        refresh_seconds: float = 1.0,
    ) -> None:
        """Anchor this process's clock to data_ingestion and mirror its speed.

        Spawns a background asyncio task that polls SimEngine every
        ``refresh_seconds`` for both the sim_time anchor and the current
        speed, then updates this singleton. Call once from each non-source
        service's startup hook (FastAPI ``@app.on_event("startup")``).

        Idempotent — calling twice is a no-op.

        Why this exists
        ---------------
        Without it, every service's local SimClock advances at wall rate
        (1×) and silently drifts whenever the simulator runs at any other
        speed. We previously saw a 10-month drift in hospital_ops because
        of exactly this pattern. Centralising the refresher here means
        every service that calls ``get_sim_time()`` gets the authoritative
        time without per-service plumbing.
        """
        # Resolve URLs from env vars when not explicitly given so the same
        # default works on the host (localhost) and in Docker (DATA_INGESTION_URL).
        import os as _os
        base = _os.environ.get("DATA_INGESTION_URL", "http://localhost:8207")
        if clock_url is None:
            clock_url = f"{base}/sim/clock"
        if state_url is None:
            state_url = f"{base}/state"
        with self._state_lock:
            if self._remote_task is not None and not self._remote_task.done():
                return  # already attached
            self._remote_url = clock_url

        async def _loop() -> None:
            try:
                import httpx
            except ImportError:
                logger.warning("sim_clock_attach_remote_skip: httpx not installed")
                return
            while True:
                try:
                    async with httpx.AsyncClient(timeout=2.0) as c:
                        r1 = await c.get(clock_url)
                        r2 = await c.get(state_url)
                    if r1.status_code == 200:
                        body = r1.json()
                        data = body.get("data") if isinstance(body, dict) else None
                        if isinstance(data, dict):
                            iso = data.get("sim_time_iso")
                            running = bool(data.get("running", True))
                        else:
                            iso = None
                            running = True
                        speed = None
                        if r2.status_code == 200:
                            try:
                                speed = float(r2.json().get("speed") or 1.0)
                            except (TypeError, ValueError):
                                speed = None
                        if iso:
                            sim_time = datetime.fromisoformat(iso.replace("Z", "+00:00"))
                            self.set_anchor(sim_time, running=running, speed=speed)
                except Exception as exc:  # noqa: BLE001 — never break the loop
                    logger.debug("sim_clock_remote_refresh_failed: %s", exc)
                try:
                    await asyncio.sleep(refresh_seconds)
                except asyncio.CancelledError:
                    return

        loop = asyncio.get_event_loop()
        task = loop.create_task(_loop(), name="sim-clock-remote-refresh")
        with self._state_lock:
            self._remote_task = task

        # Do one immediate fetch so the very first get_sim_time() call
        # after attach_remote() returns a non-stale value rather than the
        # local default-anchored time. Best-effort — failures are logged
        # but don't block startup.
        try:
            import httpx
            async with httpx.AsyncClient(timeout=2.0) as c:
                r1 = await c.get(clock_url)
                r2 = await c.get(state_url)
            if r1.status_code == 200:
                data = (r1.json() or {}).get("data") or {}
                iso = data.get("sim_time_iso")
                running = bool(data.get("running", True))
                speed = None
                if r2.status_code == 200:
                    try:
                        speed = float(r2.json().get("speed") or 1.0)
                    except (TypeError, ValueError):
                        speed = None
                if iso:
                    sim_time = datetime.fromisoformat(iso.replace("Z", "+00:00"))
                    self.set_anchor(sim_time, running=running, speed=speed)
                    logger.info(
                        "sim_clock_attached_remote anchor=%s speed=%s",
                        iso, speed,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.debug("sim_clock_initial_remote_fetch_failed: %s", exc)

    def detach_remote(self) -> None:
        """Cancel the background refresher (tests / shutdown)."""
        with self._state_lock:
            t = self._remote_task
            self._remote_task = None
            self._remote_url = None
        if t is not None and not t.done():
            t.cancel()

    # ------------------------------------------------------------------ snapshot
    def snapshot(self) -> SimClockState:
        """Return a serialisable snapshot for transmission to other services.

        ``threading.Lock`` is non-reentrant: the prior implementation took
        ``self._state_lock`` and then called ``self.get_sim_time()``, which
        also tries to take ``self._state_lock`` — a guaranteed self-deadlock
        on the very first request. Inline the time computation here so we
        only acquire the lock once. This was the root cause of /sim/clock
        hanging deterministically (and, once tripped, every subsequent
        SimClock-touching endpoint hanging too because the lock was held
        forever).
        """
        with self._state_lock:
            wall_now = _wall_now()
            if self._running:
                elapsed = (wall_now - self._anchor_wall).total_seconds()
                sim_time = self._anchor_sim + timedelta(seconds=elapsed * self._speed)
            else:
                sim_time = self._anchor_sim
            offset = (sim_time - wall_now).total_seconds()
            return SimClockState(
                sim_time_iso=sim_time.isoformat(),
                anchor_wall_iso=self._anchor_wall.isoformat(),
                offset_seconds=offset,
                running=self._running,
            )


# Convenience module-level shortcuts ---------------------------------------

def get_sim_time() -> datetime:
    """Module-level accessor — equivalent to ``SimClock.get_instance().get_sim_time()``."""
    return SimClock.get_instance().get_sim_time()


def is_sim_running() -> bool:
    """Module-level accessor — equivalent to ``SimClock.get_instance().is_sim_running()``."""
    return SimClock.get_instance().is_sim_running()


async def attach_remote_clock(
    clock_url: Optional[str] = None,
    state_url: Optional[str] = None,
    refresh_seconds: float = 1.0,
) -> None:
    """One-line helper: ``await attach_remote_clock()`` from a service's startup.

    Anchors this process's ``SimClock`` singleton to data_ingestion's
    authoritative clock and mirrors its speed multiplier. Every subsequent
    ``get_sim_time()`` call across the service returns the same value
    data_ingestion would.
    """
    await SimClock.get_instance().attach_remote(
        clock_url=clock_url,
        state_url=state_url,
        refresh_seconds=refresh_seconds,
    )


__all__ = [
    "SimClock",
    "SimClockState",
    "attach_remote_clock",
    "get_sim_time",
    "is_sim_running",
]
