"""Unit tests for shared.integration.outbox.Outbox.

Uses mongomock for an in-memory MongoDB. No network required.
"""

from __future__ import annotations

import asyncio
from typing import List, Tuple

import pytest

pytestmark = pytest.mark.asyncio

try:
    import mongomock  # type: ignore
except ImportError:
    mongomock = None


@pytest.fixture
def mongo():
    """In-memory Mongo client."""
    if mongomock is None:
        pytest.skip("mongomock not installed — pip install mongomock")
    return mongomock.MongoClient()


class _FakeBus:
    """Bus stub that records publishes and can be made to fail on demand."""

    def __init__(self, fail_for: int = 0):
        self.published: List[Tuple[str, dict, str]] = []
        self.fail_remaining = fail_for

    async def publish(self, topic: str, payload: dict, source_module: str = ""):
        if self.fail_remaining > 0:
            self.fail_remaining -= 1
            raise RuntimeError("simulated broker failure")
        self.published.append((topic, payload, source_module))


async def test_publish_happy_path_marks_sent(mongo):
    from shared.integration.outbox import Outbox

    outbox = Outbox(mongo, service_id="test_svc")
    bus = _FakeBus()

    eid = await outbox.publish(bus, "admission_complete", {"hadm_id": "H1"})

    assert bus.published == [("admission_complete", {"hadm_id": "H1"}, "test_svc")]
    doc = mongo["MIMIC_SIM"]["outbox"].find_one({"_id": eid})
    assert doc["status"] == "sent"
    assert doc["attempts"] == 1


async def test_publish_records_pending_when_bus_fails(mongo):
    from shared.integration.outbox import Outbox

    outbox = Outbox(mongo, service_id="test_svc")
    bus = _FakeBus(fail_for=999)  # always fail

    eid = await outbox.publish(bus, "patient_transferred", {"hadm_id": "H2"})

    # Bus never succeeded → event stays pending for the relay to retry
    doc = mongo["MIMIC_SIM"]["outbox"].find_one({"_id": eid})
    assert doc["status"] == "pending"
    assert doc["attempts"] == 1
    assert "simulated broker failure" in doc["last_error"]


async def test_relay_retries_pending_events(mongo):
    from shared.integration.outbox import Outbox

    outbox = Outbox(mongo, service_id="test_svc", retry_after_seconds=0)
    bus = _FakeBus(fail_for=2)  # fail twice, then succeed

    # Enqueue 3 events — the first two will fail, the third will succeed
    for i in range(3):
        await outbox.publish(bus, "topic", {"n": i})

    # Two are pending, one sent
    pending = mongo["MIMIC_SIM"]["outbox"].count_documents({"status": "pending"})
    sent = mongo["MIMIC_SIM"]["outbox"].count_documents({"status": "sent"})
    assert pending == 2
    assert sent == 1

    # Relay once — bus is healthy now so both stuck events get through
    relayed = await outbox.relay_once(bus)
    assert relayed == 2

    sent = mongo["MIMIC_SIM"]["outbox"].count_documents({"status": "sent"})
    assert sent == 3


async def test_relay_respects_max_attempts(mongo):
    from shared.integration.outbox import Outbox

    outbox = Outbox(mongo, service_id="test_svc", retry_after_seconds=0, max_attempts=3)
    bus = _FakeBus(fail_for=999)  # always fail

    eid = await outbox.publish(bus, "t", {})  # attempt 1 (fails inline)
    # Relay keeps failing; each pass increments attempts
    for _ in range(5):
        await outbox.relay_once(bus)

    doc = mongo["MIMIC_SIM"]["outbox"].find_one({"_id": eid})
    # Once attempts >= max_attempts, relay should stop picking it up
    assert doc["attempts"] == outbox.max_attempts
    assert doc["status"] == "pending"  # still pending — dead-letter territory


async def test_stats_returns_counts(mongo):
    from shared.integration.outbox import Outbox

    outbox = Outbox(mongo, service_id="test_svc", retry_after_seconds=0)
    healthy_bus = _FakeBus()
    failing_bus = _FakeBus(fail_for=999)

    await outbox.publish(healthy_bus, "t", {})  # sent
    await outbox.publish(healthy_bus, "t", {})  # sent
    await outbox.publish(failing_bus, "t", {})  # pending

    stats = outbox.stats()
    assert stats["sent"] == 2
    assert stats["pending"] == 1
    assert stats["service_id"] == "test_svc"


async def test_publish_falls_back_when_outbox_unreachable(mongo, monkeypatch):
    """If Mongo is down, publish should still call the bus so we don't lose events."""
    from shared.integration.outbox import Outbox

    outbox = Outbox(mongo, service_id="test_svc")
    bus = _FakeBus()

    # Make the insert fail
    def _raise(*a, **k):
        raise RuntimeError("mongo down")
    monkeypatch.setattr(outbox._coll(), "insert_one", _raise)

    await outbox.publish(bus, "t", {"x": 1})

    # Bus still received the event — at-least-once preserved even without outbox
    assert bus.published == [("t", {"x": 1}, "test_svc")]
