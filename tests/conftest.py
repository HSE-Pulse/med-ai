"""Shared pytest fixtures for the MedAI platform test suite.

The fixtures here are deliberately lightweight — they only spin up things
that are free to create (SimClock singletons, buffers, event bus, etc.).
MongoDB-backed fixtures live in ``tests/integration/conftest.py``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


# Ensure the repo root is on sys.path so ``shared.*`` and ``app_*`` imports work
# when tests are invoked via ``pytest tests/``.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(autouse=True)
def reset_sim_clock():
    """Reset the SimClock singleton between tests — prevents state bleed."""
    from shared.integration.sim_clock import SimClock

    SimClock.reset_singleton()
    yield
    SimClock.reset_singleton()


@pytest.fixture
def fresh_event_bus():
    """Return a brand-new EventBus instance — avoids singleton state bleed."""
    from shared.integration.event_bus import EventBus

    return EventBus()


@pytest.fixture
def conversation_buffer():
    """Return a freshly-constructed ConversationBuffer."""
    from shared.integration.conversation_buffer import ConversationBuffer

    return ConversationBuffer(max_turns=10, max_sessions=4)


@pytest.fixture
def sim_mode_env(monkeypatch):
    """Set DEPLOYMENT_MODE=simulation (the default) for the duration of the test."""
    monkeypatch.setenv("DEPLOYMENT_MODE", "simulation")
    yield
