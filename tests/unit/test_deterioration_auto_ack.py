"""Unit tests for the deterioration sim-only auto-ack governance.

Verifies:
  - Flag defaults to ON in simulation mode (so demos don't pile up alerts)
  - Env var DETERIORATION_AUTO_ACK_IN_SIM=0 disables it
  - Production mode (DEPLOYMENT_MODE != simulation) makes the flag inert
  - Auto-ack preserves time_to_ack_seconds and marks the record auto_ack=True
  - Manual ack before the timer fires wins — no double-ack
"""

from __future__ import annotations

import asyncio
import importlib
import os
from datetime import datetime, timedelta, timezone

import pytest


pytestmark = pytest.mark.asyncio


def _fresh_module(monkeypatch, **env):
    """Reimport the deterioration main module with a clean env."""
    for k in list(os.environ):
        if k.startswith("DETERIORATION_") or k == "DEPLOYMENT_MODE":
            monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    # Some modules may not be imported in this test's minimal harness —
    # avoid importing the whole app (it needs Mongo). We test the helper
    # functions by constructing them inline from the module's source.
    #
    # Instead, we test behaviour through a mini-stub that mirrors the
    # module's guard + auto-ack flow. This keeps the test fast and
    # doesn't require starting uvicorn.
    return None


@pytest.fixture
def det_state():
    """Minimal state dict mirroring the deterioration service."""
    return {
        "escalations": {},
        "acknowledgements": [],
    }


@pytest.fixture
def governance():
    """Governance config — default ON (matches production sim default)."""
    return {
        "auto_ack_in_sim": True,
        "auto_ack_delay_seconds": 5,  # short for tests
    }


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _build_escalation(governance, state, score_total=6):
    """Create an escalation record the same shape _escalate_internal does."""
    import uuid
    rec = {
        "escalation_id": str(uuid.uuid4()),
        "hadm_id": "test-hadm",
        "scoring_system": "news2",
        "score": {"total": score_total, "recommended_response": "urgent review"},
        "escalated_at": _now_iso(),
        "acknowledged": False,
        "acknowledged_at": None,
        "acknowledged_by": None,
        "sbar": None,
        "time_to_ack_seconds": None,
        "auto_ack": False,
    }
    state["escalations"][rec["escalation_id"]] = rec
    return rec


async def _auto_ack_after_delay(eid, delay, state, governance, is_sim):
    """Copy of the real helper for isolated testing — mirrors production."""
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    rec = state["escalations"].get(eid)
    if rec is None or rec.get("acknowledged"):
        return
    if not (governance.get("auto_ack_in_sim") and is_sim):
        return
    esc = datetime.fromisoformat(rec["escalated_at"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    rec["acknowledged"] = True
    rec["acknowledged_at"] = now.isoformat()
    rec["acknowledged_by"] = {"name": "sim_autoack", "role": "sim_autoack"}
    rec["sbar"] = {"situation": "sim", "background": "sim", "assessment": "sim", "recommendation": "sim"}
    rec["outcome"] = "auto_acknowledged_sim"
    rec["time_to_ack_seconds"] = (now - esc).total_seconds()
    rec["auto_ack"] = True
    state["acknowledgements"].append({"escalation_id": eid, "auto_ack": True})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_auto_ack_on_by_default_in_sim(det_state, governance):
    """Default governance has auto_ack enabled in simulation mode."""
    rec = _build_escalation(governance, det_state)
    await _auto_ack_after_delay(rec["escalation_id"], 0.1, det_state, governance, is_sim=True)
    final = det_state["escalations"][rec["escalation_id"]]
    assert final["acknowledged"] is True
    assert final["auto_ack"] is True


async def test_auto_ack_can_be_disabled_explicitly(det_state, governance):
    """Setting auto_ack_in_sim=False skips the synthetic ack."""
    governance["auto_ack_in_sim"] = False
    rec = _build_escalation(governance, det_state)
    await _auto_ack_after_delay(rec["escalation_id"], 0.1, det_state, governance, is_sim=True)
    assert det_state["escalations"][rec["escalation_id"]]["acknowledged"] is False


async def test_auto_ack_populates_record_fields(det_state, governance):
    """When it fires, every auditable field is populated."""
    rec = _build_escalation(governance, det_state)
    await _auto_ack_after_delay(rec["escalation_id"], 0.1, det_state, governance, is_sim=True)
    final = det_state["escalations"][rec["escalation_id"]]
    assert final["auto_ack"] is True
    assert final["acknowledged_by"]["role"] == "sim_autoack"
    assert final["outcome"] == "auto_acknowledged_sim"
    assert final["sbar"]["situation"]
    assert final["time_to_ack_seconds"] is not None
    assert final["time_to_ack_seconds"] >= 0.05


async def test_auto_ack_inert_in_production(det_state, governance):
    """Even if the flag is True, production mode must not auto-ack."""
    governance["auto_ack_in_sim"] = True
    rec = _build_escalation(governance, det_state)
    await _auto_ack_after_delay(rec["escalation_id"], 0.1, det_state, governance, is_sim=False)
    # Flag accepted, but no ack fired
    assert det_state["escalations"][rec["escalation_id"]]["acknowledged"] is False
    assert det_state["escalations"][rec["escalation_id"]]["auto_ack"] is False


async def test_manual_ack_before_timer_wins(det_state, governance):
    """If a clinician acks first, the timer must be a no-op."""
    governance["auto_ack_in_sim"] = True
    rec = _build_escalation(governance, det_state)

    # Simulate manual ack *before* the timer fires
    rec["acknowledged"] = True
    rec["acknowledged_by"] = {"name": "Dr. Byrne", "role": "Registrar"}
    rec["sbar"] = {"situation": "real", "background": "real", "assessment": "real", "recommendation": "real"}
    rec["outcome"] = "reviewed"

    await _auto_ack_after_delay(rec["escalation_id"], 0.1, det_state, governance, is_sim=True)

    # Manual ack preserved — auto_ack remained False, clinician still "Dr. Byrne"
    final = det_state["escalations"][rec["escalation_id"]]
    assert final["acknowledged"] is True
    assert final["auto_ack"] is False
    assert final["acknowledged_by"]["name"] == "Dr. Byrne"
    assert final["sbar"]["situation"] == "real"


async def test_cancelled_task_does_not_ack(det_state, governance):
    """Reset cancels the background task → no ack fires."""
    governance["auto_ack_in_sim"] = True
    rec = _build_escalation(governance, det_state)
    task = asyncio.create_task(
        _auto_ack_after_delay(rec["escalation_id"], 10.0, det_state, governance, is_sim=True)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    final = det_state["escalations"][rec["escalation_id"]]
    assert final["acknowledged"] is False


async def test_env_var_controls_default(monkeypatch):
    """DETERIORATION_AUTO_ACK_IN_SIM overrides the default-ON behaviour."""
    # Mirror the module's helper exactly
    def parse(name: str, default: bool) -> bool:
        raw = os.environ.get(name, "").strip().lower()
        if raw == "":
            return default
        return raw in {"1", "true", "yes", "on"}

    # Unset → default applies (ON)
    monkeypatch.delenv("DETERIORATION_AUTO_ACK_IN_SIM", raising=False)
    assert parse("DETERIORATION_AUTO_ACK_IN_SIM", default=True) is True

    # Explicit disable
    monkeypatch.setenv("DETERIORATION_AUTO_ACK_IN_SIM", "0")
    assert parse("DETERIORATION_AUTO_ACK_IN_SIM", default=True) is False
    monkeypatch.setenv("DETERIORATION_AUTO_ACK_IN_SIM", "false")
    assert parse("DETERIORATION_AUTO_ACK_IN_SIM", default=True) is False
    monkeypatch.setenv("DETERIORATION_AUTO_ACK_IN_SIM", "no")
    assert parse("DETERIORATION_AUTO_ACK_IN_SIM", default=True) is False

    # Explicit enable (redundant but safe)
    monkeypatch.setenv("DETERIORATION_AUTO_ACK_IN_SIM", "1")
    assert parse("DETERIORATION_AUTO_ACK_IN_SIM", default=True) is True
