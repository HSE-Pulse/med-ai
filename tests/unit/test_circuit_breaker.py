"""Unit tests for the circuit breaker."""

from __future__ import annotations

import asyncio
import time

import pytest

from shared.integration.circuit_breaker import BreakerState, CircuitBreaker


@pytest.mark.asyncio
async def test_starts_closed_and_allows():
    br = CircuitBreaker("svc", failure_threshold=3, cooldown_s=0.1)
    assert br.state == BreakerState.CLOSED
    assert await br.allow() is True


@pytest.mark.asyncio
async def test_opens_after_threshold_failures():
    br = CircuitBreaker("svc", failure_threshold=3, cooldown_s=0.1)
    for _ in range(3):
        await br.record_failure()
    assert br.state == BreakerState.OPEN
    assert await br.allow() is False


@pytest.mark.asyncio
async def test_cooldown_moves_to_half_open():
    br = CircuitBreaker("svc", failure_threshold=2, cooldown_s=0.05)
    await br.record_failure()
    await br.record_failure()
    assert br.state == BreakerState.OPEN
    await asyncio.sleep(0.06)
    assert await br.allow() is True  # moves to half-open
    assert br.state == BreakerState.HALF_OPEN


@pytest.mark.asyncio
async def test_success_in_half_open_closes_breaker():
    br = CircuitBreaker("svc", failure_threshold=2, cooldown_s=0.05)
    await br.record_failure()
    await br.record_failure()
    await asyncio.sleep(0.06)
    await br.allow()  # probes -> half-open
    await br.record_success()
    assert br.state == BreakerState.CLOSED


@pytest.mark.asyncio
async def test_failure_in_half_open_reopens():
    br = CircuitBreaker("svc", failure_threshold=2, cooldown_s=0.05)
    await br.record_failure()
    await br.record_failure()
    await asyncio.sleep(0.06)
    await br.allow()
    assert br.state == BreakerState.HALF_OPEN
    await br.record_failure()
    assert br.state == BreakerState.OPEN


@pytest.mark.asyncio
async def test_success_resets_failure_count():
    br = CircuitBreaker("svc", failure_threshold=5, cooldown_s=1.0)
    await br.record_failure()
    await br.record_failure()
    await br.record_success()
    # Should still be closed; subsequent failures don't trip early.
    for _ in range(4):
        await br.record_failure()
    assert br.state == BreakerState.CLOSED


@pytest.mark.asyncio
async def test_transition_callback_fires():
    events = []
    br = CircuitBreaker(
        "svc",
        failure_threshold=2,
        cooldown_s=0.05,
        on_transition=lambda n, s: events.append((n, s)),
    )
    await br.record_failure()
    await br.record_failure()
    assert any(e[1] == BreakerState.OPEN for e in events)


@pytest.mark.asyncio
async def test_window_resets_old_failures():
    br = CircuitBreaker("svc", failure_threshold=3, failure_window_s=0.05, cooldown_s=0.1)
    await br.record_failure()
    await asyncio.sleep(0.06)  # Window closes
    await br.record_failure()
    await br.record_failure()
    # Only the 2 recent failures count — still below threshold
    assert br.state == BreakerState.CLOSED
