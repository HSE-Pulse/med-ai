"""Tests for NEWS2 trend analysis and score-aware debouncer."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from shared.clinical.news2 import compute_news2_trend
from shared.integration.debouncer import ScoreAwareDebouncer


def _ts(minutes_ago: float, now: datetime) -> str:
    return (now - timedelta(minutes=minutes_ago)).isoformat()


def test_trend_empty_history_returns_insufficient_data():
    r = compute_news2_trend([])
    assert r.trajectory == "insufficient_data"
    assert r.num_points_in_window == 0


def test_trend_rising_score_detected():
    now = datetime.now(timezone.utc)
    history = [
        {"timestamp": _ts(180, now), "total": 2},
        {"timestamp": _ts(120, now), "total": 4},
        {"timestamp": _ts(60, now), "total": 6},
        {"timestamp": _ts(0, now), "total": 7},
    ]
    r = compute_news2_trend(history, window_minutes=240, now=now)
    assert r.trajectory == "rising"
    assert r.is_clinically_rising
    assert r.delta >= 5
    assert r.slope_per_hour > 1.0


def test_trend_stable_score_not_rising():
    now = datetime.now(timezone.utc)
    history = [
        {"timestamp": _ts(180, now), "total": 4},
        {"timestamp": _ts(120, now), "total": 4},
        {"timestamp": _ts(60, now), "total": 4},
        {"timestamp": _ts(0, now), "total": 4},
    ]
    r = compute_news2_trend(history, window_minutes=240, now=now)
    assert r.trajectory == "stable"
    assert not r.is_clinically_rising


def test_trend_falling_score():
    now = datetime.now(timezone.utc)
    history = [
        {"timestamp": _ts(180, now), "total": 7},
        {"timestamp": _ts(120, now), "total": 5},
        {"timestamp": _ts(60, now), "total": 3},
        {"timestamp": _ts(0, now), "total": 2},
    ]
    r = compute_news2_trend(history, window_minutes=240, now=now)
    assert r.trajectory == "falling"
    assert r.delta <= -2


def test_trend_nested_news2_structure():
    """History items may wrap the total in a ``news2`` sub-dict."""
    now = datetime.now(timezone.utc)
    history = [
        {"timestamp": _ts(60, now), "news2": {"total": 3}},
        {"timestamp": _ts(0, now), "news2": {"total": 6}},
    ]
    r = compute_news2_trend(history, window_minutes=120, now=now)
    assert r.current_score == 6
    assert r.prior_score == 3
    assert r.is_clinically_rising


def test_trend_window_excludes_old_points():
    now = datetime.now(timezone.utc)
    history = [
        # Out of window
        {"timestamp": _ts(600, now), "total": 1},
        # In window
        {"timestamp": _ts(30, now), "total": 4},
        {"timestamp": _ts(0, now), "total": 5},
    ]
    r = compute_news2_trend(history, window_minutes=60, now=now)
    assert r.num_points_in_window == 2
    assert r.prior_score == 4  # Not the out-of-window 1


# ---------------------------------------------------------------------------
# Score-aware debouncer
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_score_aware_fires_on_rise_inside_cooldown():
    deb = ScoreAwareDebouncer(cooldown_s=300)
    # First fire
    assert await deb.should_fire("p1", score=5) is True
    # Stable — suppressed
    assert await deb.should_fire("p1", score=5) is False
    # Rising — fires immediately despite cooldown
    assert await deb.should_fire("p1", score=7) is True


@pytest.mark.asyncio
async def test_score_aware_falling_suppressed_by_cooldown():
    deb = ScoreAwareDebouncer(cooldown_s=300)
    assert await deb.should_fire("p1", score=6) is True
    assert await deb.should_fire("p1", score=4) is False  # Falling; cooldown active
    assert await deb.should_fire("p1", score=3) is False  # Still within cooldown


@pytest.mark.asyncio
async def test_score_aware_keys_independent():
    deb = ScoreAwareDebouncer(cooldown_s=300)
    assert await deb.should_fire("p1", score=5) is True
    # Different patient — fresh state
    assert await deb.should_fire("p2", score=5) is True


@pytest.mark.asyncio
async def test_score_aware_rise_threshold_configurable():
    deb = ScoreAwareDebouncer(cooldown_s=300, rise_threshold=2)
    assert await deb.should_fire("p1", score=5) is True
    # Rise of 1 — suppressed because threshold is 2
    assert await deb.should_fire("p1", score=6) is False
    # Rise of 2 from last score — fires
    assert await deb.should_fire("p1", score=8) is True


@pytest.mark.asyncio
async def test_record_seeds_prior_score():
    deb = ScoreAwareDebouncer(cooldown_s=300)
    deb.record("p1", score=4)
    # First call with same score — suppressed because we already know prior is 4
    # (but no prior fire, so it fires)
    assert await deb.should_fire("p1", score=4) is True
    # Now same score should suppress
    assert await deb.should_fire("p1", score=4) is False


@pytest.mark.asyncio
async def test_reset_clears_state():
    deb = ScoreAwareDebouncer(cooldown_s=300)
    await deb.should_fire("p1", score=5)
    deb.reset()
    assert await deb.should_fire("p1", score=5) is True
