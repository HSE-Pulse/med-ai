"""Idempotency middleware for FastAPI services.

Problem
-------
When Digital Twin or a circuit-breaker half-opens, a retry can re-submit the
same notification — e.g. two ``/notify-discharge`` calls for the same hadm_id
causing a second free-bed sweep, or two ``/escalate-bed-priority`` calls
stacking bumps. Without idempotency keys, duplicate side effects are silent.

Solution
--------
Services opt-in via a FastAPI ``Depends(idempotent_post)`` on any POST route.
Clients send an ``Idempotency-Key`` header (a UUID, or a stable business key
like ``patient:123:discharge``). The first request within the TTL is handled
and its response is cached; duplicate requests with the same key return the
cached response without executing the handler again.

Cache is in-memory per service (MongoDB-backed cache is a future upgrade).
TTL defaults to 10 minutes — long enough to span realistic retry storms
without exploding memory.

Usage
-----

    from fastapi import Depends
    from shared.integration.idempotency import idempotent_post, IdempotencyCache

    cache = IdempotencyCache(ttl_seconds=600)

    @app.post("/notify-discharge/{hadm_id}")
    async def handler(hadm_id: str, _key=Depends(idempotent_post(cache))):
        ...

Testability
-----------
Pass your own ``IdempotencyCache`` instance in tests so you can inspect its
state or reset between cases.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse


@dataclass
class _CachedResponse:
    body: Any
    status_code: int
    created_at: float

    def is_fresh(self, now: float, ttl: float) -> bool:
        return (now - self.created_at) < ttl


class IdempotencyCache:
    """In-memory keyed response cache with TTL eviction."""

    def __init__(self, ttl_seconds: float = 600.0, max_entries: int = 10_000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: Dict[str, _CachedResponse] = {}
        self._inflight: Dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    async def get_or_reserve(self, key: str) -> Optional[_CachedResponse]:
        """Return a fresh cached response, or reserve the key for this request.

        If the key already has an in-flight request, waits for it and returns
        its cached response. If no prior request exists, reserves the key so
        subsequent duplicates wait.
        """
        async with self._lock:
            cached = self._entries.get(key)
            now = time.time()
            if cached is not None and cached.is_fresh(now, self.ttl_seconds):
                return cached
            # Stale — drop
            if cached is not None:
                self._entries.pop(key, None)

            existing = self._inflight.get(key)
            if existing is not None:
                wait_event = existing
            else:
                self._inflight[key] = asyncio.Event()
                return None

        # Wait outside the lock, then re-check cache under lock
        try:
            await asyncio.wait_for(wait_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            return None
        async with self._lock:
            cached = self._entries.get(key)
            if cached is not None and cached.is_fresh(time.time(), self.ttl_seconds):
                return cached
        return None

    async def store(self, key: str, body: Any, status_code: int) -> None:
        async with self._lock:
            self._entries[key] = _CachedResponse(body=body, status_code=status_code, created_at=time.time())
            # Eviction — drop oldest when over budget
            if len(self._entries) > self.max_entries:
                oldest_key = min(self._entries, key=lambda k: self._entries[k].created_at)
                self._entries.pop(oldest_key, None)
            event = self._inflight.pop(key, None)
            if event is not None:
                event.set()

    async def release(self, key: str) -> None:
        """Release the in-flight reservation without storing a result (error case)."""
        async with self._lock:
            event = self._inflight.pop(key, None)
            if event is not None:
                event.set()

    def reset(self) -> None:
        self._entries.clear()
        self._inflight.clear()

    def size(self) -> int:
        return len(self._entries)


def idempotent_post(cache: IdempotencyCache, *, header: str = "Idempotency-Key") -> Callable:
    """Return a FastAPI dependency that enforces idempotency on the decorated route.

    If the header is absent the request is passed through un-cached (caller is
    responsible for idempotency elsewhere). When present, duplicate requests
    within TTL receive the cached response.

    The dependency yields the idempotency key so handlers can log it.
    """

    async def _dependency(
        request: Request,
        idempotency_key: Optional[str] = Header(default=None, alias=header),
    ):
        if not idempotency_key:
            yield None
            return

        cached = await cache.get_or_reserve(idempotency_key)
        if cached is not None:
            # Short-circuit — raise an HTTPException with the cached payload
            raise _CachedResponseException(cached.body, cached.status_code)

        # We hold the reservation — caller will store the response via middleware
        request.state.idempotency_key = idempotency_key
        request.state.idempotency_cache = cache
        try:
            yield idempotency_key
        except Exception:
            await cache.release(idempotency_key)
            raise

    return _dependency


class _CachedResponseException(HTTPException):
    """Internal — short-circuits a duplicate request with its cached body."""

    def __init__(self, body: Any, status_code: int) -> None:
        super().__init__(status_code=status_code, detail=body)
        self.body = body


def install_idempotency_middleware(app, cache: IdempotencyCache) -> None:
    """Install the response-capture middleware.

    After the route handler completes, captures its response body and stores
    it under the idempotency key. Also converts the internal short-circuit
    exception into a plain JSONResponse.
    """

    @app.exception_handler(_CachedResponseException)
    async def _cached_handler(_request, exc: _CachedResponseException):
        return JSONResponse(status_code=exc.status_code, content=exc.body)

    @app.middleware("http")
    async def _capture(request: Request, call_next):
        response = await call_next(request)
        key = getattr(request.state, "idempotency_key", None)
        store = getattr(request.state, "idempotency_cache", None)
        if key and store is not None and 200 <= response.status_code < 300:
            # Capture body by reading the stream; FastAPI responses are typed
            # either as Response (body_iterator) or direct — handle both.
            body_chunks = []
            async for chunk in response.body_iterator:
                body_chunks.append(chunk)
            body_bytes = b"".join(body_chunks)
            try:
                import json
                body_obj = json.loads(body_bytes.decode("utf-8")) if body_bytes else None
            except (ValueError, UnicodeDecodeError):
                body_obj = body_bytes.decode("utf-8", errors="replace")
            await store.store(key, body_obj, response.status_code)
            # Re-construct response with captured body
            return JSONResponse(status_code=response.status_code, content=body_obj)
        return response


__all__ = [
    "IdempotencyCache",
    "idempotent_post",
    "install_idempotency_middleware",
]
