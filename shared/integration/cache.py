"""Shared Redis cache layer — opt-in, fail-open.

Why
---
Today, every service runs the same heavy queries repeatedly:
  - ``DigitalTwinOrchestrator.patient_context`` is a per-process dict;
    after restart, the context for an in-flight patient vanishes.
  - ``bed_management /beds/summary`` is polled every few seconds by 5+
    services; each poll walks the full ``_beds`` dict.
  - ``clinical_chat`` computes the same LLM prompt many times for the
    same patient.
  - ``waiting_list /by-department`` re-aggregates N entries on every hit.

This module provides an async cache wrapper that:

  - Uses Redis when ``REDIS_URL`` is set and reachable
  - Falls back to a no-op (returns ``None`` / passes through to origin)
    when Redis is absent or temporarily unavailable
  - Never raises to the caller — cache failures are warnings, not errors
  - Serialises via JSON so any client (including redis-cli) can inspect

Usage
-----

    from shared.integration.cache import get_cache

    cache = get_cache()
    await cache.start()  # once on service startup (idempotent)

    # Raw get/set
    await cache.set("bed:summary", summary_dict, ttl=2)
    cached = await cache.get("bed:summary")

    # Memoize a callable
    async def expensive():
        return await fetch_from_mongo()

    result = await cache.get_or_compute("bed:summary", expensive, ttl=2)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

try:
    import redis.asyncio as aioredis  # type: ignore
    _REDIS_AVAILABLE = True
except ImportError:
    aioredis = None  # type: ignore
    _REDIS_AVAILABLE = False


REDIS_URL = os.environ.get("REDIS_URL", "").strip()


class Cache:
    """Thin async wrapper around redis.asyncio with fail-open semantics.

    A service can call every method even when Redis is absent or down —
    the cache will simply miss and the caller falls back to the origin.
    """

    def __init__(self, url: str = "") -> None:
        self.url = url or REDIS_URL
        self._client: Optional[Any] = None  # aioredis.Redis
        self._enabled = False
        self._hits = 0
        self._misses = 0
        self._errors = 0

    async def start(self) -> None:
        """Connect to Redis. Idempotent; safe to call multiple times."""
        if self._enabled:
            return
        if not self.url or not _REDIS_AVAILABLE:
            logger.info("cache_disabled reason=%s",
                        "no REDIS_URL" if not self.url else "redis lib missing")
            return
        try:
            self._client = aioredis.from_url(
                self.url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=1.0,
                socket_connect_timeout=1.0,
            )
            # Ping to verify reachability
            await asyncio.wait_for(self._client.ping(), timeout=1.5)
            self._enabled = True
            logger.info("cache_enabled url=%s", self.url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cache_connect_failed url=%s err=%s — running without cache", self.url, exc)
            self._client = None

    async def stop(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._client = None
            self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def get(self, key: str) -> Optional[Any]:
        """Return deserialised value or None on miss / error."""
        if not self._enabled or self._client is None:
            return None
        try:
            raw = await self._client.get(key)
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            logger.debug("cache_get_failed key=%s err=%s", key, exc)
            return None
        if raw is None:
            self._misses += 1
            return None
        self._hits += 1
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return raw

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Store JSON-encoded value with optional TTL (seconds). Returns success flag."""
        if not self._enabled or self._client is None:
            return False
        try:
            payload = json.dumps(value, default=str)
        except (TypeError, ValueError):
            logger.debug("cache_set_skip_unserialisable key=%s", key)
            return False
        try:
            if ttl is not None:
                await self._client.setex(key, int(ttl), payload)
            else:
                await self._client.set(key, payload)
            return True
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            logger.debug("cache_set_failed key=%s err=%s", key, exc)
            return False

    async def delete(self, key: str) -> bool:
        if not self._enabled or self._client is None:
            return False
        try:
            await self._client.delete(key)
            return True
        except Exception:  # noqa: BLE001
            return False

    async def invalidate_pattern(self, pattern: str) -> int:
        """Delete every key matching *pattern*. Returns deleted count.

        Uses SCAN, not KEYS, so it's safe on large keyspaces.
        """
        if not self._enabled or self._client is None:
            return 0
        deleted = 0
        try:
            async for key in self._client.scan_iter(match=pattern, count=500):
                try:
                    await self._client.delete(key)
                    deleted += 1
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            return deleted
        return deleted

    async def get_or_compute(
        self,
        key: str,
        loader: Callable[[], Awaitable[Any]],
        ttl: Optional[int] = None,
    ) -> Any:
        """Return cached value; if miss, call *loader* and cache its result.

        Never swallows exceptions raised by *loader* — cache errors only
        cause a miss, never prevent the caller from getting real data.
        """
        cached = await self.get(key)
        if cached is not None:
            return cached
        value = await loader()
        await self.set(key, value, ttl=ttl)
        return value

    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "enabled": self._enabled,
            "url": self.url if self._enabled else None,
            "hits": self._hits,
            "misses": self._misses,
            "errors": self._errors,
            "hit_rate": round(self._hits / total, 3) if total else None,
        }


_singleton: Optional[Cache] = None


def get_cache() -> Cache:
    """Return the process-wide Cache singleton."""
    global _singleton
    if _singleton is None:
        _singleton = Cache()
    return _singleton
