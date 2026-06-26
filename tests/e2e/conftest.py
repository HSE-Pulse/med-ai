"""End-to-end test fixtures.

These tests exercise the live platform — they assume all 18 services
plus MongoDB plus Redpanda are already running. Each test resets state,
runs the sim, and asserts the expected cascade.

Skipping policy
---------------
If the data_ingestion service at :8207 is unreachable, the tests skip
with a clear message rather than failing. This lets CI run the suite
without a live stack yet still enforce correctness when the stack is up.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import pytest
import httpx

# Ports match scripts/start_all_services.py
DATA_INGEST = "http://localhost:8207"
HOSPITAL_OPS = "http://localhost:8203"
BED_MGMT = "http://localhost:8208"
WAITING_LIST = "http://localhost:8209"
ED_FLOW = "http://localhost:8214"
DETERIORATION = "http://localhost:8220"
DISCHARGE_LOUNGE = "http://localhost:8221"
FHIR = "http://localhost:8219"
GDPR = "http://localhost:8217"

# All services that should be Kafka-subscribed
ALL_CONSUMER_PORTS: List[int] = [
    8201, 8202, 8203, 8204, 8205, 8206,
    8208, 8209, 8210, 8214, 8215, 8216,
    8217, 8218, 8219, 8220, 8221,
]


def _alive(url: str, timeout: float = 2.0) -> bool:
    """Return True iff the service's /health endpoint responds 200."""
    try:
        with httpx.Client(timeout=timeout, verify=False, trust_env=False) as c:
            r = c.get(f"{url}/health")
            return r.status_code == 200
    except (httpx.HTTPError, OSError):
        return False


@pytest.fixture(scope="session", autouse=True)
def require_live_stack():
    """Skip the entire e2e suite unless data_ingestion is reachable."""
    if not _alive(DATA_INGEST):
        pytest.skip(
            "live platform not running — start services with "
            "`python scripts/start_all_services.py` (Redpanda + MongoDB required)",
            allow_module_level=True,
        )


@pytest.fixture(scope="session")
def http() -> httpx.Client:
    """Session-scoped HTTP client tuned for localhost service calls."""
    with httpx.Client(timeout=10.0, verify=False, trust_env=False) as c:
        yield c


@pytest.fixture
def reset_sim(http: httpx.Client):
    """Reset the whole platform before the test runs.

    The data_ingestion /reset endpoint fans out to every service and also
    purges the Mongo event_log + consumer offsets so the test starts from
    a known-clean state.
    """
    r = http.post(f"{DATA_INGEST}/reset")
    assert r.status_code == 200, f"reset failed: {r.status_code} {r.text[:200]}"
    # Small settle window — reset is async across services
    time.sleep(1.0)
    yield


def _get_json(http: httpx.Client, url: str) -> Dict[str, Any]:
    r = http.get(url)
    r.raise_for_status()
    return r.json()


def _post_json(http: httpx.Client, url: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    r = http.post(url, json=body or {})
    r.raise_for_status()
    return r.json()


def wait_for(
    predicate,
    timeout: float = 60.0,
    interval: float = 0.5,
    message: str = "condition not met",
) -> Any:
    """Poll *predicate* until it returns a truthy value or *timeout* seconds pass.

    Returns the truthy value, or raises AssertionError with *message*.
    Prefer this over raw time.sleep so tests run as fast as the system allows.
    """
    deadline = time.time() + timeout
    last: Any = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"{message} (last={last})")


@pytest.fixture
def helpers():
    """Namespace of test helper functions (exposed as a fixture so tests can import)."""
    class _Helpers:
        wait_for = staticmethod(wait_for)
        alive = staticmethod(_alive)
        get_json = staticmethod(_get_json)
        post_json = staticmethod(_post_json)
    return _Helpers
