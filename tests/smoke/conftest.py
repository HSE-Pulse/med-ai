"""Smoke-test fixtures.

Smoke tests hit the live, locally-running stack via HTTP. They exist to
confirm the system is wired end-to-end at the URL level — not to test
business logic. Each test takes <1s.

Auto-skip if the platform isn't running.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# Service URLs (mirror tests/e2e/conftest.py)
SCRIBE = "http://127.0.0.1:8210"
PATIENT_JOURNEY = "http://127.0.0.1:8205"
FHIR = "http://127.0.0.1:8219"
DASHBOARD_API = "http://127.0.0.1:5173"  # Vite dev proxy fronts /api/scribe/*

# Demo patient identified during MIMIC ingestion plan execution
DEMO_SUBJECT = "16473192"
DEMO_HADM = "21079163"


def _alive(url: str, timeout: float = 1.5) -> bool:
    try:
        with httpx.Client(timeout=timeout, verify=False, trust_env=False) as c:
            return c.get(f"{url}/health").status_code == 200
    except (httpx.HTTPError, OSError):
        return False


@pytest.fixture(scope="session", autouse=True)
def require_live_services():
    """Skip the smoke suite unless Scribe + Patient Journey + FHIR are up."""
    missing = [name for name, url in [
        ("clinical_scribe", SCRIBE),
        ("patient_journey", PATIENT_JOURNEY),
        ("fhir_gateway", FHIR),
    ] if not _alive(url)]
    if missing:
        pytest.skip(
            f"smoke tests need live services; missing: {', '.join(missing)}. "
            "Start with `python start_all.py`.",
            allow_module_level=True,
        )


@pytest.fixture(scope="session")
def http() -> httpx.Client:
    with httpx.Client(timeout=5.0, verify=False, trust_env=False) as c:
        yield c
