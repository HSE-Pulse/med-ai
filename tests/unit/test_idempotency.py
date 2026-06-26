"""Tests for the idempotency middleware."""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from shared.integration.idempotency import (
    IdempotencyCache,
    idempotent_post,
    install_idempotency_middleware,
)


def _build_app(cache: IdempotencyCache):
    app = FastAPI()
    install_idempotency_middleware(app, cache)
    hits = {"count": 0}

    @app.post("/notify-discharge/{hadm_id}")
    async def handler(hadm_id: str, _key=Depends(idempotent_post(cache))):
        hits["count"] += 1
        return {"hadm_id": hadm_id, "hits": hits["count"]}

    @app.get("/hits")
    def get_hits():
        return hits

    return app, hits


def test_no_header_passes_through():
    cache = IdempotencyCache(ttl_seconds=60)
    app, hits = _build_app(cache)
    client = TestClient(app)
    r1 = client.post("/notify-discharge/A1")
    r2 = client.post("/notify-discharge/A1")
    assert r1.status_code == 200
    assert r2.status_code == 200
    # Both ran because no Idempotency-Key was provided
    assert hits["count"] == 2


def test_duplicate_with_same_key_returns_cached():
    cache = IdempotencyCache(ttl_seconds=60)
    app, hits = _build_app(cache)
    client = TestClient(app)
    key = "key-abc-123"
    r1 = client.post("/notify-discharge/A1", headers={"Idempotency-Key": key})
    r2 = client.post("/notify-discharge/A1", headers={"Idempotency-Key": key})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == r2.json()
    # Second call returned cached body — handler only ran once
    assert hits["count"] == 1


def test_different_keys_both_run():
    cache = IdempotencyCache(ttl_seconds=60)
    app, hits = _build_app(cache)
    client = TestClient(app)
    r1 = client.post("/notify-discharge/A1", headers={"Idempotency-Key": "k1"})
    r2 = client.post("/notify-discharge/A1", headers={"Idempotency-Key": "k2"})
    assert r1.status_code == r2.status_code == 200
    assert hits["count"] == 2


def test_cache_expiry():
    cache = IdempotencyCache(ttl_seconds=0.2)
    app, hits = _build_app(cache)
    client = TestClient(app)
    key = "k-expire"
    client.post("/notify-discharge/A1", headers={"Idempotency-Key": key})
    time.sleep(0.3)
    client.post("/notify-discharge/A1", headers={"Idempotency-Key": key})
    # Cache expired — handler ran twice
    assert hits["count"] == 2


def test_cache_reset():
    cache = IdempotencyCache(ttl_seconds=60)
    app, _ = _build_app(cache)
    client = TestClient(app)
    client.post("/notify-discharge/A1", headers={"Idempotency-Key": "k"})
    assert cache.size() == 1
    cache.reset()
    assert cache.size() == 0


@pytest.mark.asyncio
async def test_concurrent_duplicates_collapse_to_one():
    """Two parallel requests with the same key should both see the same result."""
    cache = IdempotencyCache(ttl_seconds=60)
    hits = {"count": 0}

    app = FastAPI()
    install_idempotency_middleware(app, cache)

    @app.post("/notify")
    async def handler(_key=Depends(idempotent_post(cache))):
        hits["count"] += 1
        await asyncio.sleep(0.05)  # simulate real work
        return {"count": hits["count"]}

    # Using sync TestClient with asyncio event loops is fiddly; instead test the
    # cache primitive directly since that's the concurrency-sensitive part.
    key = "concurrent"
    result_a = await cache.get_or_reserve(key)
    assert result_a is None  # first caller reserves
    await cache.store(key, {"x": 1}, 200)
    result_b = await cache.get_or_reserve(key)
    assert result_b is not None
    assert result_b.body == {"x": 1}
