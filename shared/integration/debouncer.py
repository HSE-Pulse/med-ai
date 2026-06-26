"""Debouncers for noisy inter-service notifications.

Bug #4 in the engineering uplift: Bed Management pushes a ``/notify-census``
to Hospital Ops on every ``/beds/summary`` poll, which in turn steps the DES
engine. Without debouncing this inflates simulation metrics and thrashes the
DES.

Exports
-------
- :class:`CensusDebouncer` — per-department cooldown gate
- :class:`GenericDebouncer` — generic key-based cooldown gate
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Dict, Hashable, Mapping

logger = logging.getLogger(__name__)


class GenericDebouncer:
    """Time-based debouncer keyed by an arbitrary hashable."""

    def __init__(self, cooldown_s: float = 5.0) -> None:
        self.cooldown_s = cooldown_s
        self._last: Dict[Hashable, float] = {}
        self._lock = asyncio.Lock()

    async def should_fire(self, key: Hashable) -> bool:
        async with self._lock:
            now = time.time()
            last = self._last.get(key, 0.0)
            if now - last < self.cooldown_s:
                return False
            self._last[key] = now
            return True

    def reset(self) -> None:
        self._last.clear()


class CensusDebouncer:
    """Per-department cooldown for census push-to-Hospital-Ops.

    Also detects payload-identical updates via a rolling digest so Hospital
    Ops can skip a DES step when the census hasn't changed.

    Usage
    -----

    >>> deb = CensusDebouncer(cooldown_s=5)
    >>> if await deb.should_push("ED", payload):
    ...     await client.hospital_ops.post("/notify-census", payload)
    """

    def __init__(self, cooldown_s: float = 5.0) -> None:
        self.cooldown_s = cooldown_s
        self._last_push: Dict[str, float] = {}
        self._last_digest: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def should_push(self, department: str, payload: Mapping[str, Any] | None = None) -> bool:
        async with self._lock:
            now = time.time()
            last = self._last_push.get(department, 0.0)
            if now - last < self.cooldown_s:
                logger.debug(
                    "census_push_debounced",
                    extra={"department": department, "since_last_s": now - last},
                )
                return False

            if payload is not None:
                digest = self._digest(payload)
                if self._last_digest.get(department) == digest:
                    # Payload identical — no need to re-push, but update cooldown
                    # so we don't keep computing digests.
                    self._last_push[department] = now
                    return False
                self._last_digest[department] = digest

            self._last_push[department] = now
            return True

    def reset(self) -> None:
        self._last_push.clear()
        self._last_digest.clear()

    @staticmethod
    def _digest(payload: Mapping[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


class ScoreAwareDebouncer:
    """Debouncer for score-based alerts (NEWS2, PEWS, IMEWS).

    Naive time-only debouncing can mask a deteriorating patient: a NEWS2 of 5
    at 12:00 suppresses a NEWS2 of 7 at 12:03 because the 5-minute cooldown
    hasn't elapsed. This debouncer fires immediately when the score has risen
    since the last observation, and only applies the cooldown when the score
    is stable or declining.

    Usage
    -----

    >>> deb = ScoreAwareDebouncer(cooldown_s=300)
    >>> if await deb.should_fire(hadm_id, score=5):
    ...     alert()
    >>> # 2 minutes later, same score → suppressed
    >>> await deb.should_fire(hadm_id, score=5)  # → False
    >>> # 2 minutes later, rising score → fires immediately
    >>> await deb.should_fire(hadm_id, score=7)  # → True
    """

    def __init__(self, cooldown_s: float = 300.0, rise_threshold: int = 1) -> None:
        self.cooldown_s = cooldown_s
        self.rise_threshold = rise_threshold
        self._last_fire: Dict[Hashable, float] = {}
        self._last_score: Dict[Hashable, int] = {}
        self._lock = asyncio.Lock()

    async def should_fire(self, key: Hashable, *, score: int) -> bool:
        async with self._lock:
            now = time.time()
            prior = self._last_score.get(key)
            last_fire = self._last_fire.get(key, 0.0)

            # Rising by ≥ rise_threshold — fire immediately, no cooldown.
            if prior is not None and score - prior >= self.rise_threshold:
                self._last_fire[key] = now
                self._last_score[key] = score
                return True

            # First observation for this key — fire.
            if prior is None:
                self._last_fire[key] = now
                self._last_score[key] = score
                return True

            # Stable / declining — apply standard cooldown.
            if now - last_fire < self.cooldown_s:
                self._last_score[key] = score
                return False

            self._last_fire[key] = now
            self._last_score[key] = score
            return True

    def record(self, key: Hashable, score: int) -> None:
        """Seed the debouncer with a prior score (e.g., from persisted state)."""
        self._last_score[key] = score

    def reset(self) -> None:
        self._last_fire.clear()
        self._last_score.clear()


__all__ = ["CensusDebouncer", "GenericDebouncer", "ScoreAwareDebouncer"]
