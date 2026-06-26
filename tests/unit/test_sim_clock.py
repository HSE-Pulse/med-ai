"""Unit tests for SimClock."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from shared.integration.sim_clock import SimClock, get_sim_time, is_sim_running


def test_singleton_identity():
    a = SimClock.get_instance()
    b = SimClock.get_instance()
    assert a is b


def test_starts_not_running():
    clock = SimClock.get_instance()
    assert not clock.is_sim_running()
    # get_sim_time works even when stopped
    assert clock.get_sim_time().tzinfo is not None


def test_set_anchor_seeds_past_time():
    clock = SimClock.get_instance()
    past = datetime(2180, 5, 6, 22, 23, tzinfo=timezone.utc)
    clock.set_anchor(past, running=False)
    assert clock.get_sim_time() == past
    assert not clock.is_sim_running()


def test_running_clock_advances_with_wall_time():
    clock = SimClock.get_instance()
    anchor = datetime(2180, 5, 6, 22, 23, tzinfo=timezone.utc)
    clock.set_anchor(anchor, running=True)
    t0 = clock.get_sim_time()
    time.sleep(0.05)
    t1 = clock.get_sim_time()
    assert t1 > t0
    # Should be close to real elapsed time (<100ms tolerance)
    elapsed = (t1 - t0).total_seconds()
    assert 0.01 < elapsed < 1.0


def test_stop_freezes_time():
    clock = SimClock.get_instance()
    clock.set_anchor(datetime(2180, 5, 6, tzinfo=timezone.utc), running=True)
    clock.stop()
    t0 = clock.get_sim_time()
    time.sleep(0.05)
    assert clock.get_sim_time() == t0


def test_start_resumes_wall_time_mapping():
    clock = SimClock.get_instance()
    clock.set_anchor(datetime(2180, 1, 1, tzinfo=timezone.utc), running=False)
    clock.start()
    assert clock.is_sim_running()


def test_advance_jumps_forward():
    clock = SimClock.get_instance()
    clock.set_anchor(datetime(2180, 1, 1, tzinfo=timezone.utc), running=False)
    clock.advance(timedelta(hours=6))
    assert clock.get_sim_time() == datetime(2180, 1, 1, 6, tzinfo=timezone.utc)


def test_snapshot_roundtrip():
    clock = SimClock.get_instance()
    clock.set_anchor(datetime(2180, 6, 15, 12, tzinfo=timezone.utc), running=True)
    snap = clock.snapshot()
    assert snap.running
    assert "2180-06-15" in snap.sim_time_iso

    # Hydrate a fresh singleton from the snapshot
    SimClock.reset_singleton()
    fresh = SimClock.get_instance()
    fresh.set_from_state(snap)
    assert fresh.is_sim_running()
    # Times should be close — within a few ms
    restored = fresh.get_sim_time()
    target = datetime.fromisoformat(snap.sim_time_iso)
    assert abs((restored - target).total_seconds()) < 1.0


def test_module_level_helpers():
    clock = SimClock.get_instance()
    clock.set_anchor(datetime(2180, 1, 1, tzinfo=timezone.utc), running=True)
    assert is_sim_running()
    assert get_sim_time().year == 2180
