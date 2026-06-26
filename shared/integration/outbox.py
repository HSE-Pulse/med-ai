"""Transactional outbox — guarantees at-least-once delivery of events
to the broker even when Kafka/Mongo is momentarily unavailable.

Why
---
Today, a service updates its own state (e.g. ``bed.status = allocated``),
then calls ``bus.publish(...)``. Between those two calls:

  - The process can crash → state updated, event never published, bed is
    allocated in Mongo but every downstream consumer thinks the bed is
    still free.
  - Kafka can be unreachable → Mongo event_log has the event, Kafka
    topic doesn't; services that only subscribe via Kafka miss it.

Pattern
-------
1. **Enqueue**: ``outbox.enqueue(topic, payload, source_module)`` writes
   a ``pending`` document to ``MIMIC_SIM.outbox`` *before* calling the
   bus. This is the durable record of "I intend to publish this."
2. **Publish**: the same call then invokes ``bus.publish(...)``. On
   success, the document moves to ``sent``.
3. **Relay**: a background task polls ``outbox`` every N seconds for
   documents stuck in ``pending`` older than the retry window and
   re-publishes them. Duplicates on Kafka side are idempotent because
   consumers use the stable ``event_id`` from the bus event record.

This is the "outbox-lite" pattern that works without Mongo transactions
(single-node Mongo doesn't support them). A full ACID outbox would
require a replica set — roadmap item for multi-node deployment.

Usage
-----

    from shared.integration.outbox import Outbox

    outbox = Outbox(mongo_client, service_id="digital_twin")
    # Replace bus.publish(...) with:
    await outbox.publish(bus, "admission_complete", {...}, source_module="digital_twin")

    # On service startup, start the relay so stuck events recover:
    asyncio.create_task(outbox.relay_forever(bus))
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class Outbox:
    """Durable outbox + relay wrapping an EventBus.

    Construct once per service with a shared MongoClient. Pass the bus
    instance into ``publish`` and ``relay_forever`` so the outbox stays
    unaware of bus internals.
    """

    COLLECTION = "outbox"

    def __init__(
        self,
        mongo_client: Any,
        service_id: str,
        retry_after_seconds: int = 30,
        max_attempts: int = 10,
    ) -> None:
        self._client = mongo_client
        self.service_id = service_id
        self.retry_after = timedelta(seconds=retry_after_seconds)
        self.max_attempts = max_attempts

    def _coll(self):
        return self._client["MIMIC_SIM"][self.COLLECTION]

    def ensure_indexes(self) -> None:
        """Create indexes on first use. Safe to call multiple times."""
        try:
            self._coll().create_index([("status", 1), ("created_at", 1)])
            self._coll().create_index([("service_id", 1)])
            # TTL: prune sent entries older than 7 days
            self._coll().create_index(
                "sent_at",
                expireAfterSeconds=7 * 24 * 3600,
                partialFilterExpression={"status": "sent"},
            )
        except Exception as exc:  # noqa: BLE001 — index creation is best-effort
            logger.debug("outbox_index_skip: %s", exc)

    async def publish(
        self,
        bus,
        topic: str,
        payload: Dict[str, Any],
        source_module: Optional[str] = None,
    ) -> str:
        """Durable publish: record to outbox, then publish via bus.

        Returns the outbox document's ``_id``. Safe to call from sync code
        paths via ``asyncio.run_coroutine_threadsafe`` if needed — the
        outbox write itself is synchronous.
        """
        entry_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        doc = {
            "_id": entry_id,
            "topic": topic,
            "payload": payload,
            "source_module": source_module or self.service_id,
            "service_id": self.service_id,
            "status": "pending",
            "attempts": 0,
            "created_at": now,
            "last_attempt_at": None,
            "sent_at": None,
            "last_error": None,
        }

        # Step 1 — durable record (synchronous Mongo write)
        try:
            self._coll().insert_one(doc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("outbox_enqueue_failed topic=%s err=%s — falling back to direct publish", topic, exc)
            # If outbox is unreachable, fall back to direct publish so we
            # don't block the caller. We'd rather lose the retry guarantee
            # than drop the event entirely.
            await bus.publish(topic, payload, source_module=source_module or self.service_id)
            return entry_id

        # Step 2 — best-effort publish via bus
        try:
            await bus.publish(topic, payload, source_module=source_module or self.service_id)
            self._coll().update_one(
                {"_id": entry_id},
                {"$set": {
                    "status": "sent",
                    "sent_at": datetime.now(timezone.utc),
                    "attempts": 1,
                }},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("outbox_publish_failed topic=%s err=%s — relay will retry", topic, exc)
            self._coll().update_one(
                {"_id": entry_id},
                {"$set": {
                    "attempts": 1,
                    "last_attempt_at": datetime.now(timezone.utc),
                    "last_error": str(exc)[:500],
                }},
            )

        return entry_id

    async def relay_once(self, bus) -> int:
        """Single relay pass — retries stuck ``pending`` documents.

        Returns the number of documents successfully relayed.
        """
        cutoff = datetime.now(timezone.utc) - self.retry_after
        query = {
            "status": "pending",
            "created_at": {"$lte": cutoff},
            "attempts": {"$lt": self.max_attempts},
        }
        relayed = 0
        try:
            cursor = list(self._coll().find(query).limit(100))
        except Exception as exc:  # noqa: BLE001
            logger.warning("outbox_relay_query_failed: %s", exc)
            return 0

        for doc in cursor:
            try:
                await bus.publish(
                    doc["topic"],
                    doc.get("payload") or {},
                    source_module=doc.get("source_module") or self.service_id,
                )
                self._coll().update_one(
                    {"_id": doc["_id"]},
                    {"$set": {
                        "status": "sent",
                        "sent_at": datetime.now(timezone.utc),
                    }, "$inc": {"attempts": 1}},
                )
                relayed += 1
            except Exception as exc:  # noqa: BLE001
                self._coll().update_one(
                    {"_id": doc["_id"]},
                    {"$set": {
                        "last_attempt_at": datetime.now(timezone.utc),
                        "last_error": str(exc)[:500],
                    }, "$inc": {"attempts": 1}},
                )
        if relayed:
            logger.info("outbox_relay: relayed %d stuck events", relayed)
        return relayed

    async def relay_forever(self, bus, interval_seconds: int = 15) -> None:
        """Background loop — polls the outbox for stuck events every N seconds."""
        logger.info(
            "outbox_relay_started service=%s interval=%ss retry_after=%ss",
            self.service_id, interval_seconds, int(self.retry_after.total_seconds()),
        )
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                try:
                    await self.relay_once(bus)
                except Exception:
                    logger.exception("outbox_relay_tick_failed")
        except asyncio.CancelledError:
            logger.info("outbox_relay_cancelled service=%s", self.service_id)
            raise

    def stats(self) -> Dict[str, Any]:
        """Return counts by status — used by /outbox-stats endpoint."""
        try:
            counts = {s: self._coll().count_documents({"status": s, "service_id": self.service_id})
                      for s in ("pending", "sent")}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "service_id": self.service_id}

        # Oldest-pending age is best-effort — mongomock strips tzinfo so
        # comparisons can blow up. Isolate it from the counts query.
        age_s = None
        try:
            oldest_pending = self._coll().find_one(
                {"status": "pending", "service_id": self.service_id},
                sort=[("created_at", 1)],
            )
            if oldest_pending and oldest_pending.get("created_at"):
                created = oldest_pending["created_at"]
                now = datetime.now(timezone.utc)
                # Normalise both sides — mongomock returns naive datetimes
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age_s = (now - created).total_seconds()
        except Exception:
            pass

        return {
            "service_id": self.service_id,
            "pending": counts["pending"],
            "sent": counts["sent"],
            "oldest_pending_age_s": age_s,
        }
