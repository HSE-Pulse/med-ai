"""Integration test fixtures.

Integration tests here drive real components (Mongo + FastAPI ASGI app)
without going over the network. They auto-skip if Mongo is not reachable.

Kafka is NOT required: tests that need to bypass the broker call the
service handlers directly or hit endpoints whose state lives in Mongo.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _mongo_alive(uri: str = "mongodb://localhost:27017", timeout_ms: int = 1500) -> bool:
    try:
        import pymongo

        client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=timeout_ms)
        client.admin.command("ping")
        client.close()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def mongo_uri() -> str:
    return os.environ.get("MONGO_URI", "mongodb://localhost:27017")


@pytest.fixture(scope="session", autouse=True)
def require_mongo(mongo_uri):
    """Skip integration tests if Mongo isn't reachable."""
    if not _mongo_alive(mongo_uri):
        pytest.skip(
            f"Mongo not reachable at {mongo_uri} — integration tests need a live "
            "MongoDB. Start with `docker compose -f docker-compose.kafka.yml up -d mongo` "
            "or skip via `pytest tests/unit`.",
            allow_module_level=True,
        )


@pytest.fixture
def mongo_client(mongo_uri):
    import pymongo

    client = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=2000)
    yield client
    client.close()


@pytest.fixture
def clean_scribe_notes(mongo_client):
    """Delete test-marked notes before and after each test."""
    coll = mongo_client["clinical_scribe"]["notes"]
    coll.delete_many({"_test_marker": "integration"})
    yield coll
    coll.delete_many({"_test_marker": "integration"})


@pytest.fixture
def clean_journey_notes(mongo_client):
    coll = mongo_client["patient_journey"]["notes"]
    coll.delete_many({"_test_marker": "integration"})
    yield coll
    coll.delete_many({"_test_marker": "integration"})
