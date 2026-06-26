"""Durable event broker — Kafka with MongoDB event-log fallback.

Design
------
Every event published by any service goes through three persistence tiers:

1. **MongoDB event_log** (always, synchronous) — primary durable store.
   Collection ``MIMIC_SIM.event_log`` with ``{event_id, topic, payload,
   source_module, timestamp, consumed_by[]}`` per document. This tier is
   always active, so replay works even if Kafka is down. Tracking of which
   service has consumed each event is done via the ``consumed_by`` array.

2. **Kafka / Redpanda** (optional, asynchronous) — push-based delivery with
   native consumer-group offsets. When ``KAFKA_BOOTSTRAP`` env var is set and
   ``aiokafka`` is installed, every publication is also produced to a Kafka
   topic (``medai.<event_type>``) so downstream consumers can subscribe via
   consumer groups. Falls back silently if the broker is unreachable.

3. **In-process subscribers** (always) — for handlers registered in the same
   process as the publisher. These run regardless of the other tiers.

Replay semantics
----------------
``replay_missed_events(service_id, since=None)`` is the portable replay API:

* **MongoDB mode (default)** — query ``event_log`` for documents where
  ``consumed_by`` doesn't include ``service_id``; dispatch each to local
  handlers; append ``service_id`` to ``consumed_by`` on success.
* **Kafka mode** — seek to the last committed offset for the service's
  consumer group and read forward. Offset commits happen in-band.

A service calls ``replay_missed_events`` once during its FastAPI ``lifespan``
startup hook so anything produced during its downtime is delivered before it
accepts traffic.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer  # type: ignore
    _AIOKAFKA_AVAILABLE = True
except ImportError:
    AIOKafkaConsumer = None  # type: ignore
    AIOKafkaProducer = None  # type: ignore
    _AIOKAFKA_AVAILABLE = False


KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "").strip()
KAFKA_TOPIC_PREFIX = os.environ.get("KAFKA_TOPIC_PREFIX", "medai.")


def _topic_for(event_topic: str) -> str:
    """Map a domain event topic (e.g. ``bed_allocated``) to a Kafka topic."""
    return f"{KAFKA_TOPIC_PREFIX}{event_topic}".replace(" ", "_")


def _serialise(doc: Dict[str, Any]) -> bytes:
    def default(o: Any) -> str:
        if isinstance(o, datetime):
            return o.isoformat()
        return str(o)
    return json.dumps(doc, default=default).encode("utf-8")


def _deserialise(raw: bytes) -> Dict[str, Any]:
    if isinstance(raw, (bytes, bytearray)):
        return json.loads(raw.decode("utf-8"))
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw)


# ---------------------------------------------------------------------------
# Broker interface
# ---------------------------------------------------------------------------
class BrokerBase:
    """Abstract async broker. All methods no-op by default."""

    name: str = "null"

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def produce(self, topic: str, payload: Dict[str, Any]) -> None: ...

    async def consume_forever(
        self,
        service_id: str,
        topics: List[str],
        handler: Callable[[str, Dict[str, Any]], Any],
    ) -> None:
        """Subscribe ``service_id`` to ``topics`` and dispatch each to ``handler``."""
        ...

    async def replay_missed(
        self,
        service_id: str,
        topics: Optional[List[str]] = None,
        since: Optional[datetime] = None,
    ) -> int: ...


class NullBroker(BrokerBase):
    """No-op broker used when neither Kafka nor Mongo is available."""
    name = "null"


class MongoBroker(BrokerBase):
    """MongoDB-backed durable broker.

    Acts as the fallback that works in every environment. Each published
    event is written to ``MIMIC_SIM.event_log``; replay reads from this
    collection. Consumer-group semantics are emulated via the
    ``consumed_by`` array — a service ID only dispatches an event once.
    """

    name = "mongo"
    COLLECTION = "event_log"

    def __init__(self, mongo_client: Any) -> None:
        self._client = mongo_client

    def _coll(self):
        return self._client["MIMIC_SIM"][self.COLLECTION]

    async def start(self) -> None:
        coll = self._coll()
        try:
            coll.create_index([("timestamp", 1)])
            coll.create_index([("topic", 1), ("timestamp", 1)])
            coll.create_index([("consumed_by", 1)])
        except Exception as exc:  # noqa: BLE001
            logger.debug("mongo_broker_index_skip: %s", exc)

    async def stop(self) -> None:  # nothing to clean up
        return

    async def produce(self, topic: str, payload: Dict[str, Any]) -> None:
        doc = {
            "event_id": payload.get("event_id"),
            "topic": topic,
            "payload": payload.get("payload", payload),
            "source_module": payload.get("source_module", ""),
            "timestamp": payload.get("timestamp") or datetime.now(timezone.utc),
            "consumed_by": [],
        }
        try:
            self._coll().insert_one(doc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("mongo_broker_produce_failed: %s", exc)

    async def replay_missed(
        self,
        service_id: str,
        topics: Optional[List[str]] = None,
        since: Optional[datetime] = None,
    ) -> int:
        query: Dict[str, Any] = {"consumed_by": {"$ne": service_id}}
        if since is not None:
            query["timestamp"] = {"$gte": since}
        if topics:
            query["topic"] = {"$in": list(topics)}
        try:
            cursor = self._coll().find(query).sort("timestamp", 1)
            docs = list(cursor)
        except Exception as exc:  # noqa: BLE001
            logger.warning("mongo_broker_replay_query_failed: %s", exc)
            return 0
        return len(docs)  # caller does the dispatch; this returns count


class KafkaBroker(BrokerBase):
    """Kafka (or Redpanda) async broker using aiokafka.

    Works alongside MongoBroker — the EventBus writes to both. Use Kafka for
    push-based streaming between services (low-latency consumer-group
    delivery); use Mongo for durable replay from sim start, audit, and
    service-boot catch-up.
    """

    name = "kafka"

    def __init__(self, bootstrap: str, prefix: str = KAFKA_TOPIC_PREFIX) -> None:
        if not _AIOKAFKA_AVAILABLE:
            raise RuntimeError("aiokafka not installed — pip install aiokafka")
        self.bootstrap = bootstrap
        self.prefix = prefix
        self._producer: Optional[AIOKafkaProducer] = None  # type: ignore
        self._consumers: List[AIOKafkaConsumer] = []  # type: ignore
        self._consumer_tasks: List[asyncio.Task] = []

    async def start(self) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap,
            value_serializer=_serialise,
            enable_idempotence=True,
            acks="all",
            max_batch_size=65536,
            linger_ms=5,
        )
        try:
            await self._producer.start()
            logger.info("kafka_broker_started bootstrap=%s", self.bootstrap)
        except Exception as exc:
            logger.warning("kafka_producer_start_failed: %s — broker disabled", exc)
            self._producer = None

    async def stop(self) -> None:
        for task in self._consumer_tasks:
            task.cancel()
        for consumer in self._consumers:
            try:
                await consumer.stop()
            except Exception:
                pass
        if self._producer is not None:
            try:
                await self._producer.stop()
            except Exception:
                pass

    async def produce(self, topic: str, payload: Dict[str, Any]) -> None:
        if self._producer is None:
            return
        # Inject OpenTelemetry trace context into Kafka headers so the
        # consumer-side span becomes a child of the producer's. Fails
        # open — if tracing is disabled, headers is empty.
        try:
            from shared.integration.tracing import inject_kafka_headers
            headers = inject_kafka_headers()
        except Exception:  # noqa: BLE001
            headers = None
        try:
            await self._producer.send_and_wait(
                _topic_for(topic), payload,
                headers=headers if headers else None,
            )
        except Exception as exc:  # noqa: BLE001 — never break publish path
            logger.warning("kafka_produce_failed topic=%s err=%s", topic, exc)

    async def consume_forever(
        self,
        service_id: str,
        topics: List[str],
        handler: Callable[[str, Dict[str, Any]], Any],
    ) -> None:
        kafka_topics = [_topic_for(t) for t in topics]
        consumer = AIOKafkaConsumer(
            *kafka_topics,
            bootstrap_servers=self.bootstrap,
            group_id=f"medai.{service_id}",
            value_deserializer=_deserialise,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
        )
        try:
            await consumer.start()
        except Exception as exc:
            logger.warning("kafka_consumer_start_failed: %s", exc)
            return
        self._consumers.append(consumer)
        # Lazy-import tracing helpers so broker.py doesn't hard-depend on OTel
        try:
            from shared.integration.tracing import (
                extract_kafka_context, get_tracer, is_enabled as _tracing_on,
            )
            tracer = get_tracer("kafka.consumer") if _tracing_on() else None
        except Exception:  # noqa: BLE001
            tracer, extract_kafka_context = None, lambda _h: None
            def _tracing_on(): return False
        try:
            async for msg in consumer:
                topic_name = msg.topic.removeprefix(self.prefix) if msg.topic.startswith(self.prefix) else msg.topic
                # Reconstruct the trace context the producer injected so the
                # handler span is linked to the admission it came from.
                ctx = extract_kafka_context(msg.headers) if tracer is not None else None
                try:
                    if tracer is not None:
                        with tracer.start_as_current_span(
                            f"kafka.consume {topic_name}",
                            context=ctx,
                            attributes={
                                "messaging.system": "kafka",
                                "messaging.destination": msg.topic,
                                "messaging.kafka.partition": msg.partition,
                                "messaging.consumer.group": f"medai.{service_id}",
                                "messaging.kafka.offset": msg.offset,
                            },
                        ):
                            result = handler(topic_name, msg.value)
                            if asyncio.iscoroutine(result):
                                await result
                    else:
                        result = handler(topic_name, msg.value)
                        if asyncio.iscoroutine(result):
                            await result
                    await consumer.commit()
                except Exception:
                    logger.exception("kafka_handler_error topic=%s", topic_name)
        finally:
            await consumer.stop()

    async def replay_missed(
        self,
        service_id: str,
        topics: Optional[List[str]] = None,
        since: Optional[datetime] = None,
    ) -> int:
        # Kafka replay is handled by the consumer-group offset: calling
        # ``consume_forever`` with a service_id that has no committed offsets
        # replays from earliest automatically. This method just reports 0 in
        # Kafka-only mode because the real replay is streaming.
        return 0


class CompositeBroker(BrokerBase):
    """Fan-out to multiple brokers for dual-writes.

    Used so every publish always persists to MongoDB (durability), and is
    also streamed via Kafka (low-latency delivery) when available.
    """

    name = "composite"

    def __init__(self, brokers: List[BrokerBase]) -> None:
        self.brokers = brokers
        # Primary (first) broker drives replay; others mirror writes only.

    async def start(self) -> None:
        for b in self.brokers:
            await b.start()

    async def stop(self) -> None:
        for b in self.brokers:
            await b.stop()

    async def produce(self, topic: str, payload: Dict[str, Any]) -> None:
        await asyncio.gather(
            *[b.produce(topic, payload) for b in self.brokers],
            return_exceptions=True,
        )

    async def consume_forever(
        self,
        service_id: str,
        topics: List[str],
        handler: Callable[[str, Dict[str, Any]], Any],
    ) -> None:
        # Prefer Kafka streaming when present; fall back to primary.
        for b in self.brokers:
            if isinstance(b, KafkaBroker):
                await b.consume_forever(service_id, topics, handler)
                return
        # No Kafka — the caller should poll replay_missed periodically
        await self.brokers[0].consume_forever(service_id, topics, handler)

    async def replay_missed(
        self,
        service_id: str,
        topics: Optional[List[str]] = None,
        since: Optional[datetime] = None,
    ) -> int:
        for b in self.brokers:
            if isinstance(b, MongoBroker):
                return await b.replay_missed(service_id, topics, since)
        return 0


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_broker(mongo_client: Optional[Any] = None) -> BrokerBase:
    """Construct the best broker available in this environment.

    Preferences:
      1. Mongo + Kafka composite when both available
      2. Mongo only when KAFKA_BOOTSTRAP empty or aiokafka missing
      3. NullBroker when Mongo is unreachable too
    """
    mongo_ok = mongo_client is not None
    kafka_ok = bool(KAFKA_BOOTSTRAP) and _AIOKAFKA_AVAILABLE

    if mongo_ok and kafka_ok:
        logger.info("broker: mongo+kafka composite (%s)", KAFKA_BOOTSTRAP)
        return CompositeBroker([MongoBroker(mongo_client), KafkaBroker(KAFKA_BOOTSTRAP)])
    if mongo_ok:
        logger.info("broker: mongo-only (durable event_log)")
        return MongoBroker(mongo_client)
    if kafka_ok:
        logger.info("broker: kafka-only (ephemeral — no mongo durability)")
        return KafkaBroker(KAFKA_BOOTSTRAP)
    logger.info("broker: null (no persistence — dev mode only)")
    return NullBroker()


__all__ = [
    "BrokerBase",
    "NullBroker",
    "MongoBroker",
    "KafkaBroker",
    "CompositeBroker",
    "build_broker",
]
