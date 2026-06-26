"""Unit tests for shared.integration.cache.Cache.

Uses fakeredis (in-memory redis.asyncio emulation) — no real Redis
required. Falls back to skipping if fakeredis isn't installed.
"""

from __future__ import annotations

import asyncio
import pytest

pytestmark = pytest.mark.asyncio

try:
    import fakeredis.aioredis as fake_async  # type: ignore
    _FAKE_AVAILABLE = True
except ImportError:
    _FAKE_AVAILABLE = False


def _patch_redis(monkeypatch, client):
    """Force Cache.start() to use the given fake client instead of a real one."""
    import shared.integration.cache as cache_mod

    class _FakeFromUrl:
        def __init__(self, c):
            self.c = c
        def from_url(self, *a, **k):
            return self.c

    monkeypatch.setattr(cache_mod, "aioredis", _FakeFromUrl(client))
    monkeypatch.setattr(cache_mod, "_REDIS_AVAILABLE", True)


@pytest.fixture
def fake():
    if not _FAKE_AVAILABLE:
        pytest.skip("fakeredis not installed — pip install fakeredis")
    return fake_async.FakeRedis(decode_responses=True)


async def test_disabled_when_no_url():
    from shared.integration.cache import Cache
    c = Cache(url="")
    await c.start()
    assert not c.enabled
    # get/set are no-ops without raising
    assert await c.get("x") is None
    assert await c.set("x", 1) is False


async def test_get_set_roundtrip(monkeypatch, fake):
    from shared.integration.cache import Cache
    _patch_redis(monkeypatch, fake)
    c = Cache(url="redis://fake")
    await c.start()
    assert c.enabled

    assert await c.set("k", {"a": 1, "b": [2, 3]}, ttl=60)
    got = await c.get("k")
    assert got == {"a": 1, "b": [2, 3]}

    stats = c.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 0


async def test_miss_returns_none(monkeypatch, fake):
    from shared.integration.cache import Cache
    _patch_redis(monkeypatch, fake)
    c = Cache(url="redis://fake")
    await c.start()

    assert await c.get("absent") is None
    stats = c.stats()
    assert stats["misses"] == 1
    assert stats["hits"] == 0


async def test_get_or_compute_caches_loader_result(monkeypatch, fake):
    from shared.integration.cache import Cache
    _patch_redis(monkeypatch, fake)
    c = Cache(url="redis://fake")
    await c.start()

    call_count = {"n": 0}
    async def loader():
        call_count["n"] += 1
        return {"computed": True, "n": call_count["n"]}

    v1 = await c.get_or_compute("k1", loader, ttl=60)
    v2 = await c.get_or_compute("k1", loader, ttl=60)

    assert v1 == {"computed": True, "n": 1}
    assert v2 == {"computed": True, "n": 1}  # cached — loader not re-invoked
    assert call_count["n"] == 1


async def test_invalidate_pattern(monkeypatch, fake):
    from shared.integration.cache import Cache
    _patch_redis(monkeypatch, fake)
    c = Cache(url="redis://fake")
    await c.start()

    await c.set("bed:summary:v1", "a")
    await c.set("bed:summary:v2", "b")
    await c.set("other:x", "c")

    removed = await c.invalidate_pattern("bed:summary:*")
    assert removed == 2
    assert await c.get("bed:summary:v1") is None
    assert await c.get("other:x") == "c"


async def test_connect_failure_falls_back_silent(monkeypatch):
    """If Redis is unreachable, cache.enabled stays False and get/set no-op."""
    import shared.integration.cache as cache_mod

    class _ConnectErr:
        async def ping(self):
            raise ConnectionError("nope")
        async def aclose(self):
            pass

    class _FromUrl:
        def from_url(self, *a, **k):
            return _ConnectErr()

    monkeypatch.setattr(cache_mod, "aioredis", _FromUrl())
    monkeypatch.setattr(cache_mod, "_REDIS_AVAILABLE", True)

    c = cache_mod.Cache(url="redis://fake")
    await c.start()
    assert not c.enabled
    assert await c.get("x") is None
    assert await c.set("x", 1) is False


async def test_loader_exceptions_propagate(monkeypatch, fake):
    """Cache errors are swallowed; loader errors are NOT."""
    from shared.integration.cache import Cache
    _patch_redis(monkeypatch, fake)
    c = Cache(url="redis://fake")
    await c.start()

    async def bad_loader():
        raise RuntimeError("origin down")

    with pytest.raises(RuntimeError, match="origin down"):
        await c.get_or_compute("k", bad_loader, ttl=5)
