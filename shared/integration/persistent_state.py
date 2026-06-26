"""Per-service state snapshot + restore + replay helper.

Goal
----
Every service holds some operational state in memory:
  * Bed Management     — bed inventory, allocations
  * ED Flow            — tracked ED patients
  * Hospital Ops       — DES engine, census, action log
  * Discharge Lounge   — discharge plans, follow-ups
  * Waiting List       — queue + priority scores
  * Deterioration      — (already persisted by its own module)

Historically, all of these were lost on pod restart. This helper gives every
service a uniform way to:

  1. **Snapshot** its state to MongoDB whenever it changes (or periodically).
  2. **Restore** the latest snapshot on startup before accepting traffic.
  3. **Replay** events produced after the snapshot's timestamp so any events
     the service missed are re-applied in order.

Usage
-----

    from shared.integration.persistent_state import PersistentState

    persistent = PersistentState(
        service_id="bed_management",
        mongo=state["mongo"].client,
        collection_name="bed_management_state",
    )

    # Restore the most recent snapshot at startup
    snap = persistent.load_snapshot()
    if snap:
        apply_snapshot_to_in_memory(snap)

    # Snapshot after any mutation
    persistent.save_snapshot(build_snapshot_dict())

    # Replay any events newer than the snapshot, after subscribers are wired
    await persistent.replay_events_since_snapshot(bus, topics=["patient_transferred"])
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PersistentState:
    """Snapshot + restore + replay primitive for per-service state.

    Snapshots are stored as a single document per service in
    ``MIMIC_SIM.<collection_name>`` with fields::

        {
          "_id": "<service_id>",
          "service_id": "<service_id>",
          "snapshot_at": <utc datetime>,
          "state": { ... },
          "sim_time": "<optional>",
          "version": <int>,
        }

    We keep only the most recent snapshot (upsert). Audit history is
    maintained by appending to a sibling ``<collection_name>_history``
    collection when ``keep_history=True``.
    """

    def __init__(
        self,
        service_id: str,
        mongo: Optional[Any] = None,
        *,
        collection_name: Optional[str] = None,
        keep_history: bool = True,
    ) -> None:
        self.service_id = service_id
        self._mongo = mongo
        self.collection_name = collection_name or f"{service_id}_state"
        self.keep_history = keep_history
        self._version: int = 0

    # ---------------------------------------------------------------- backend
    def _db(self):
        if self._mongo is None:
            return None
        try:
            return self._mongo["MIMIC_SIM"]
        except Exception:
            return None

    def _coll(self):
        db = self._db()
        return db[self.collection_name] if db is not None else None

    def _history_coll(self):
        db = self._db()
        return db[f"{self.collection_name}_history"] if db is not None else None

    # --------------------------------------------------------------- snapshot
    # Mongo BSON document hard limit is 16 MiB. We reject snapshots above
    # 14 MiB up-front so we don't burn a Mongo round-trip + cascade into
    # downstream backpressure when a service's state grows unbounded
    # (e.g. ed_flow accumulating per-patient events) — was the root cause
    # of the data_ingestion asyncio hang on 2026-05-26.
    _MAX_SNAPSHOT_BYTES = 14 * 1024 * 1024

    def save_snapshot(
        self,
        state: Dict[str, Any],
        *,
        sim_time: Optional[str] = None,
    ) -> bool:
        """Persist ``state`` as the current snapshot for this service.

        Returns True on success, False if Mongo is unavailable, the
        snapshot exceeds 14 MiB, or any other error.  Never raises.
        """
        coll = self._coll()
        if coll is None:
            return False
        sanitized = _sanitize(state)
        # Cheap pre-flight size estimate. JSON length is a close upper
        # bound on the BSON encoded size for typical state dicts.
        try:
            est_size = len(json.dumps(sanitized, default=str))
        except (TypeError, ValueError):
            est_size = -1
        if est_size > self._MAX_SNAPSHOT_BYTES:
            logger.warning(
                "snapshot_oversize service=%s size=%dB max=%dB — skipped",
                self.service_id, est_size, self._MAX_SNAPSHOT_BYTES,
            )
            return False
        self._version += 1
        doc = {
            "_id": self.service_id,
            "service_id": self.service_id,
            "snapshot_at": _now(),
            "state": sanitized,
            "sim_time": sim_time,
            "version": self._version,
        }
        try:
            coll.replace_one({"_id": self.service_id}, doc, upsert=True)
            if self.keep_history:
                hist = self._history_coll()
                if hist is not None:
                    hist_doc = dict(doc)
                    hist_doc.pop("_id", None)
                    hist.insert_one(hist_doc)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("snapshot_save_failed service=%s err=%s", self.service_id, exc)
            return False

    def load_snapshot(self) -> Optional[Dict[str, Any]]:
        """Return the most recent snapshot for this service, or None."""
        coll = self._coll()
        if coll is None:
            return None
        try:
            doc = coll.find_one({"_id": self.service_id})
            if doc is None:
                return None
            self._version = int(doc.get("version", 0))
            return doc
        except Exception as exc:  # noqa: BLE001
            logger.warning("snapshot_load_failed service=%s err=%s", self.service_id, exc)
            return None

    def snapshot_timestamp(self) -> Optional[datetime]:
        snap = self.load_snapshot()
        if snap is None:
            return None
        ts = snap.get("snapshot_at")
        if isinstance(ts, datetime):
            return ts
        return None

    # ----------------------------------------------------------------- replay
    async def replay_events_since_snapshot(
        self,
        bus,
        topics: Optional[List[str]] = None,
    ) -> int:
        """Replay events newer than our last snapshot to the given EventBus.

        Handlers must already be subscribed. Returns number of events
        dispatched. If no snapshot exists, replays from simulation start.
        """
        since = self.snapshot_timestamp()
        if since is None:
            return await bus.replay_since_sim_start(self.service_id, topics=topics)
        return await bus.replay_missed_events(self.service_id, since=since, topics=topics)

    # -------------------------------------------------------------- housekeep
    def clear(self) -> None:
        """Drop the snapshot (used by /reset endpoints)."""
        coll = self._coll()
        if coll is None:
            return
        try:
            coll.delete_many({"service_id": self.service_id})
            if self.keep_history:
                hist = self._history_coll()
                if hist is not None:
                    hist.delete_many({"service_id": self.service_id})
        except Exception as exc:  # noqa: BLE001
            logger.warning("snapshot_clear_failed service=%s err=%s", self.service_id, exc)

    def history(self, limit: int = 50) -> List[Dict[str, Any]]:
        hist = self._history_coll()
        if hist is None:
            return []
        try:
            return list(hist.find({"service_id": self.service_id})
                            .sort("snapshot_at", -1).limit(limit))
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def _sanitize(value: Any) -> Any:
    """Recursively convert non-BSON-safe values into JSON-safe equivalents.

    Pydantic models → dict, datetimes pass through (pymongo accepts them),
    sets → lists, anything else → str if not serialisable.
    """
    if value is None or isinstance(value, (bool, int, float, str, datetime)):
        return value
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    if isinstance(value, set):
        return [_sanitize(v) for v in value]
    # Pydantic v2
    if hasattr(value, "model_dump"):
        try:
            return _sanitize(value.model_dump())
        except Exception:
            pass
    # Dataclass
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict
        try:
            return _sanitize(asdict(value))
        except Exception:
            pass
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


__all__ = ["PersistentState"]
