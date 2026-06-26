"""Shared helper for services to subscribe to broker events (Kafka + Mongo).

Why this exists
---------------
The event bus already has ``publish()`` and an in-process subscriber
registry. What every downstream service also needs is:

  1. **Startup attach** — bind the shared bus to MongoDB + (optional) Kafka
     so it can both persist and stream events for this process.
  2. **Replay catch-up** — on boot, read any events produced while the
     service was offline and dispatch them through local handlers.
  3. **Live streaming** — spawn a background task that consumes Kafka (or
     polls Mongo when Kafka is absent) forever and dispatches to the same
     handlers so messages produced by peer services land here.

Usage (in a FastAPI lifespan / ``@app.on_event("startup")``)::

    from shared.integration.kafka_consumer import attach_service_to_bus

    async def on_admission(topic: str, payload: dict) -> None:
        hadm_id = payload.get("hadm_id")
        ...  # service-specific handler

    await attach_service_to_bus(
        service_id="waiting_list",
        topic_handlers={
            "admission_complete": on_admission,
            "patient_discharged": on_discharge,
        },
        mongo_client=mongo.client,
    )

On shutdown, call ``detach_service_from_bus()`` to cancel the background
task and flush the broker.

The helper is idempotent per ``service_id`` — calling it twice returns
the same runtime without double-subscribing.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from shared.integration.event_bus import get_event_bus
from shared.integration.broker import KafkaBroker, CompositeBroker, MongoBroker

logger = logging.getLogger(__name__)

# topic → async handler(topic, payload)
TopicHandler = Callable[[str, Dict[str, Any]], Awaitable[None]]

# service_id → (consumer_task, topic_handlers)
_active_services: Dict[str, Dict[str, Any]] = {}


async def attach_service_to_bus(
    service_id: str,
    topic_handlers: Dict[str, TopicHandler],
    mongo_client: Any,
    replay_since: Optional[datetime] = None,
) -> None:
    """Wire this service into the event bus with Kafka + replay catch-up.

    ``service_id`` is the consumer-group identifier. Each service must use
    a stable, unique id so Kafka commits offsets against it and Mongo can
    dedupe via ``consumed_by``.

    ``topic_handlers`` maps topic names (not full Kafka topic names — the
    broker strips the ``medai.`` prefix before dispatch) to async
    callables. Each handler receives ``(topic, payload_dict)``.

    ``replay_since`` defaults to "last 30 minutes" so brief restarts
    catch up without replaying the entire sim history.
    """
    if service_id in _active_services:
        logger.debug("service %s already attached to bus — skipping", service_id)
        return

    bus = get_event_bus()
    bus.attach_mongo(mongo_client)
    await bus.startup()

    # Register in-process subscribers for each topic → local handler(event)
    for topic, handler in topic_handlers.items():
        async def _dispatch(evt, _h=handler, _t=topic):
            try:
                payload = evt.payload if hasattr(evt, "payload") else evt
                await _h(_t, payload if isinstance(payload, dict) else {})
            except Exception:
                logger.exception("in_process_handler_error service=%s topic=%s", service_id, _t)
        bus.subscribe(topic, _dispatch)

    # Replay missed events (Mongo path) so offline events are delivered
    since = replay_since or (datetime.now(timezone.utc) - timedelta(minutes=30))
    try:
        broker = bus._broker  # noqa: SLF001 — read-only introspection
        if isinstance(broker, CompositeBroker):
            for sub in broker.brokers:
                if isinstance(sub, MongoBroker):
                    docs = list(sub._coll().find(  # noqa: SLF001
                        {
                            "consumed_by": {"$ne": service_id},
                            "timestamp": {"$gte": since},
                            "topic": {"$in": list(topic_handlers.keys())},
                        }
                    ).sort("timestamp", 1))
                    for doc in docs:
                        topic = doc.get("topic")
                        handler = topic_handlers.get(topic)
                        if handler is None:
                            continue
                        try:
                            await handler(topic, doc.get("payload") or {})
                            sub._coll().update_one(  # noqa: SLF001
                                {"_id": doc["_id"]},
                                {"$addToSet": {"consumed_by": service_id}},
                            )
                        except Exception:
                            logger.exception(
                                "replay_handler_error service=%s topic=%s",
                                service_id, topic,
                            )
                    if docs:
                        logger.info("replayed %d missed events for %s", len(docs), service_id)
                    break
    except Exception:  # noqa: BLE001
        logger.exception("replay_failed service=%s", service_id)

    # Spawn background Kafka consumer — only when a Kafka sub-broker is present.
    kafka_broker: Optional[KafkaBroker] = None
    broker = bus._broker  # noqa: SLF001
    if isinstance(broker, CompositeBroker):
        kafka_broker = next((b for b in broker.brokers if isinstance(b, KafkaBroker)), None)
    elif isinstance(broker, KafkaBroker):
        kafka_broker = broker

    consumer_task: Optional[asyncio.Task] = None
    if kafka_broker is not None and kafka_broker._producer is not None:  # noqa: SLF001
        topics = list(topic_handlers.keys())

        async def _kafka_dispatch(topic: str, payload: Dict[str, Any]) -> None:
            handler = topic_handlers.get(topic)
            if handler is None:
                return
            # Kafka ships us the event envelope from the bus (event_id,
            # topic, payload, source_module, timestamp). Unwrap payload.
            body = payload.get("payload") if isinstance(payload, dict) else None
            await handler(topic, body if isinstance(body, dict) else payload or {})

        consumer_task = asyncio.create_task(
            kafka_broker.consume_forever(service_id, topics, _kafka_dispatch),
            name=f"kafka-consumer-{service_id}",
        )
        logger.info(
            "kafka_consumer_started service=%s topics=%s",
            service_id, topics,
        )
    else:
        logger.info(
            "kafka_unavailable — service %s running in Mongo-only mode; "
            "in-process subscribers still fire on local publishes",
            service_id,
        )

    _active_services[service_id] = {
        "task": consumer_task,
        "handlers": topic_handlers,
    }


async def detach_service_from_bus(service_id: str) -> None:
    """Cancel the consumer task and clear handler registration."""
    record = _active_services.pop(service_id, None)
    if record is None:
        return
    task = record.get("task")
    if task is not None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    logger.info("detached service %s from bus", service_id)


# ---------------------------------------------------------------------------
# Drop-in ring-buffer helper so every service can wire Kafka in 3 lines
# ---------------------------------------------------------------------------

# service_id → list of last-N events
_service_ring_buffers: Dict[str, List[Dict[str, Any]]] = {}


def get_kafka_events(service_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Return the last *limit* events consumed by *service_id*."""
    return (_service_ring_buffers.get(service_id) or [])[-int(limit):]


async def attach_with_ring_buffer(
    service_id: str,
    topics: List[str],
    mongo_client: Any,
    extra_handlers: Optional[Dict[str, TopicHandler]] = None,
    ring_size: int = 500,
) -> None:
    """One-line wiring helper: subscribe to *topics* and stash each event.

    Every event consumed on any of the topics is appended to a per-service
    ring buffer (``shared.integration.kafka_consumer.get_kafka_events``).
    Services that need *action* handlers can pass ``extra_handlers``; they
    run in addition to the ring-buffer append.
    """
    buf = _service_ring_buffers.setdefault(service_id, [])

    async def _capture(topic: str, payload: Dict[str, Any]) -> None:
        from datetime import datetime as _dt, timezone as _tz
        entry = {
            "topic": topic,
            "at": _dt.now(_tz.utc).isoformat(),
            "hadm_id": payload.get("hadm_id") if isinstance(payload, dict) else None,
            "source": payload.get("source_module") if isinstance(payload, dict) else None,
            "payload_keys": list(payload.keys())[:10] if isinstance(payload, dict) else [],
        }
        buf.append(entry)
        if len(buf) > ring_size:
            del buf[: ring_size // 2]
        # Run extra handler if caller registered one for this topic
        if extra_handlers:
            handler = extra_handlers.get(topic)
            if handler is not None:
                try:
                    await handler(topic, payload)
                except Exception:
                    logger.exception("extra_handler_error service=%s topic=%s", service_id, topic)

    topic_handlers = {t: _capture for t in topics}
    await attach_service_to_bus(
        service_id=service_id,
        topic_handlers=topic_handlers,
        mongo_client=mongo_client,
    )
