"""Unit tests for CensusDebouncer and GenericDebouncer."""

from __future__ import annotations

import asyncio
import time

import pytest

from shared.integration.debouncer import CensusDebouncer, GenericDebouncer


@pytest.mark.asyncio
async def test_generic_debouncer_first_call_passes():
    d = GenericDebouncer(cooldown_s=0.1)
    assert await d.should_fire("k") is True


@pytest.mark.asyncio
async def test_generic_debouncer_blocks_within_cooldown():
    d = GenericDebouncer(cooldown_s=0.1)
    await d.should_fire("k")
    assert await d.should_fire("k") is False


@pytest.mark.asyncio
async def test_generic_debouncer_allows_after_cooldown():
    d = GenericDebouncer(cooldown_s=0.05)
    await d.should_fire("k")
    await asyncio.sleep(0.06)
    assert await d.should_fire("k") is True


@pytest.mark.asyncio
async def test_generic_debouncer_keys_are_independent():
    d = GenericDebouncer(cooldown_s=0.1)
    await d.should_fire("a")
    assert await d.should_fire("b") is True
    assert await d.should_fire("a") is False


@pytest.mark.asyncio
async def test_census_debouncer_cooldown():
    d = CensusDebouncer(cooldown_s=0.1)
    assert await d.should_push("ED", {"occupancy": 0.9}) is True
    assert await d.should_push("ED", {"occupancy": 0.95}) is False


@pytest.mark.asyncio
async def test_census_debouncer_suppresses_identical_payload():
    d = CensusDebouncer(cooldown_s=0.01)
    payload = {"departments": [{"name": "ED", "occ": 0.8}]}
    assert await d.should_push("ED", payload) is True
    await asyncio.sleep(0.02)
    # After cooldown, payload unchanged — still suppressed.
    assert await d.should_push("ED", payload) is False


@pytest.mark.asyncio
async def test_census_debouncer_allows_when_payload_changes():
    d = CensusDebouncer(cooldown_s=0.01)
    await d.should_push("ED", {"occupancy": 0.8})
    await asyncio.sleep(0.02)
    assert await d.should_push("ED", {"occupancy": 0.85}) is True


@pytest.mark.asyncio
async def test_census_debouncer_reset():
    d = CensusDebouncer(cooldown_s=5.0)
    await d.should_push("ED", {"x": 1})
    d.reset()
    assert await d.should_push("ED", {"x": 1}) is True
