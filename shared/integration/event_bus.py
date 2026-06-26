"""Event bus for inter-module communication.

Architecture (v3)
-----------------
Every publish goes through THREE tiers:

  1. MongoDB ``MIMIC_SIM.event_log``  — durable, always-on, replay source.
  2. Kafka / Redpanda                 — optional; push-based consumer groups.
  3. In-process subscribers           — for handlers in the same Python
                                         interpreter as the publisher.

The broker (see :mod:`shared.integration.broker`) fans out 1 and 2. Tier 3 is
handled directly by this class. On service startup, call
``replay_since_sim_start(service_id, topics)`` once to deliver any events
produced during the service's downtime.

Usage
-----

    bus = EventBus()
    bus.attach_mongo(mongo_client)  # enables durable persistence + replay
    await bus.startup()             # connects the broker (Kafka if configured)

    bus.subscribe("bed_allocated", my_handler)
    await bus.publish("bed_allocated", {"bed_id": "MAU-101", "patient_id": 123},
                      source_module="bed_management")

    # On startup, after subscribing all handlers:
    n = await bus.replay_since_sim_start("bed_management",
                                         topics=["patient_transferred", "patient_discharged"])
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from shared.integration.broker import BrokerBase, NullBroker, build_broker

logger = logging.getLogger(__name__)


@dataclass
class Event:
    """An event published on the bus."""

    topic: str
    payload: Dict[str, Any]
    source_module: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_id: str = ""

    def __post_init__(self) -> None:
        if not self.event_id:
            self.event_id = str(uuid.uuid4())

    def to_doc(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "topic": self.topic,
            "payload": self.payload,
            "source_module": self.source_module,
            "timestamp": self.timestamp,
        }


# Standard event topics used across modules (unchanged, kept for
# backwards-compat with existing subscribers)
TOPICS = {
    # Bed Management (Module 08) events
    "bed_allocated": "A bed has been assigned to a patient",
    "bed_released": "A bed has been vacated and is available",
    "discharge_predicted": "Discharge prediction updated for a patient",
    "capacity_alert": "Department capacity alert (amber/red/black)",
    "trolley_alert": "Trolley count exceeds threshold",

    # Waiting List (Module 09) events
    "priority_updated": "Patient priority score recalculated",
    "schedule_generated": "New weekly schedule generated",
    "deterioration_alert": "Patient deterioration risk exceeds threshold",
    "referral_triaged": "New referral processed by NLP",

    # Clinical Scribe (Module 10) events
    "note_generated": "Clinical note generated from encounter",
    "note_approved": "Clinician approved generated note",
    "coding_suggested": "ICD-10-AM codes suggested for encounter",

    # ED Flow (Module 14) events
    "pet_breach_risk": "Patient at risk of exceeding 6-hour PET target",
    "lwbs_risk": "Patient at risk of leaving without being seen",
    "surge_alert": "ED crowding surge predicted",
    "bottleneck_detected": "ED bottleneck identified with causal attribution",
    "admission_predicted": "ED patient predicted to require admission",

    # Cross-module events
    "patient_admitted": "Patient admitted to hospital",
    "patient_discharged": "Patient discharged from hospital",
    "patient_transferred": "Patient transferred between departments",
    "admission_complete": "Digital Twin admission pipeline complete",
    "deterioration_critical": "NEWS2 / PEWS / IMEWS critical escalation",
}


class EventBus:
    """Publish/subscribe bus with broker-backed durability and replay."""

    def __init__(self, mongo_client: Optional[Any] = None) -> None:
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)
        self._event_log: List[Event] = []
        self._max_log_size: int = 10_000
        self._mongo = mongo_client
        self._broker: BrokerBase = NullBroker()
        self._broker_started: bool = False

    # ------------------------------------------------------------------ backend
    def attach_mongo(self, mongo_client: Any) -> None:
        """Attach a MongoDB client — drives durable persistence + replay."""
        self._mongo = mongo_client

    async def startup(self) -> None:
        """Construct and start the broker. Idempotent."""
        if self._broker_started:
            return
        self._broker = build_broker(self._mongo)
        await self._broker.start()
        self._broker_started = True

    async def shutdown(self) -> None:
        if self._broker_started:
            await self._broker.stop()
            self._broker_started = False

    # ------------------------------------------------------------------ pubsub
    def subscribe(self, topic: str, handler: Callable) -> None:
        """Register *handler* to be called when *topic* is published."""
        self._subscribers[topic].append(handler)
        logger.debug("Subscribed %s to topic '%s'", getattr(handler, "__name__", "handler"), topic)

    def unsubscribe(self, topic: str, handler: Callable) -> None:
        handlers = self._subscribers.get(topic, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(
        self,
        topic: str,
        payload: Dict[str, Any],
        source_module: str = "",
    ) -> Event:
        """Publish an event.

        Order:
          1. Build the Event record
          2. Write to in-memory ring buffer (for ``get_recent_events``)
          3. Fan out to the broker (MongoDB + Kafka when available)
          4. Dispatch to in-process subscribers
        """
        event = Event(topic=topic, payload=payload, source_module=source_module)

        # In-memory ring for dashboard queries
        self._event_log.append(event)
        if len(self._event_log) > self._max_log_size:
            self._event_log = self._event_log[-self._max_log_size:]

        # Durable + streaming fan-out (never raises)
        try:
            await self._broker.produce(topic, event.to_doc())
        except Exception as exc:  # noqa: BLE001
            logger.warning("broker_produce_failed topic=%s err=%s", topic, exc)

        logger.info("event_published topic=%s source=%s id=%s",
                    topic, source_module, event.event_id)

        for handler in self._subscribers.get(topic, []):
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception(
                    "subscriber_error handler=%s topic=%s",
                    getattr(handler, "__name__", "anon"), topic,
                )

        return event

    # --------------------------------------------------------------- queries
    def get_recent_events(
        self,
        topic: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        events = self._event_log
        if topic:
            events = [e for e in events if e.topic == topic]
        return [
            {
                "event_id": e.event_id,
                "topic": e.topic,
                "source_module": e.source_module,
                "timestamp": e.timestamp.isoformat(),
                "payload": e.payload,
            }
            for e in events[-limit:]
        ]

    # --------------------------------------------------------------- replay
    async def replay_missed_events(
        self,
        service_id: str,
        since: Optional[datetime] = None,
        topics: Optional[List[str]] = None,
    ) -> int:
        """Replay events this service hasn't consumed yet — call on startup.

        Uses the MongoDB ``event_log`` collection as the source of truth,
        dispatches each event to local in-process handlers, then marks the
        event as consumed by ``service_id``.
        """
        if self._mongo is None:
            return 0
        coll = self._mongo["MIMIC_SIM"]["event_log"]
        query: Dict[str, Any] = {"consumed_by": {"$ne": service_id}}
        if since is not None:
            query["timestamp"] = {"$gte": since}
        if topics:
            query["topic"] = {"$in": list(topics)}
        try:
            cursor = coll.find(query).sort("timestamp", 1)
            docs = list(cursor)
        except Exception as exc:
            logger.warning("replay_query_failed: %s", exc)
            return 0
        replayed = 0
        for doc in docs:
            event = Event(
                topic=doc.get("topic", ""),
                payload=doc.get("payload", {}),
                source_module=doc.get("source_module", ""),
                timestamp=doc.get("timestamp", datetime.now(timezone.utc)),
                event_id=doc.get("event_id", ""),
            )
            for handler in self._subscribers.get(event.topic, []):
                try:
                    result = handler(event)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    logger.exception("replay_handler_failed topic=%s", event.topic)
            try:
                coll.update_one(
                    {"_id": doc["_id"]},
                    {"$addToSet": {"consumed_by": service_id}},
                )
            except Exception:
                pass
            replayed += 1
        logger.info("replayed %d events for service=%s", replayed, service_id)
        return replayed

    async def replay_since_sim_start(
        self,
        service_id: str,
        topics: Optional[List[str]] = None,
    ) -> int:
        """Replay every event since the simulation started.

        If the caller has subscribed to topics before calling this, their
        handlers will run for each historical event in timestamp order —
        reconstructing in-memory state from scratch.

        ``SimClock.get_instance().sim_start`` drives the ``since`` cutoff so
        events from earlier simulation sessions (or MIMIC ingest) are
        excluded unless the ``_pre_sim_events`` flag is set.
        """
        since = None
        try:
            from shared.integration.sim_clock import SimClock
            clock = SimClock.get_instance()
            since = getattr(clock, "sim_start", None) or getattr(clock, "_start_time", None)
        except Exception:
            since = None
        return await self.replay_missed_events(service_id, since=since, topics=topics)


# Global singleton bus instance (shared across modules in same process)
_global_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Return the global EventBus singleton."""
    global _global_bus
    if _global_bus is None:
        _global_bus = EventBus()
    return _global_bus
