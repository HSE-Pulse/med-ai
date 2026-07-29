"""Live catalogue of every endpoint the estate exposes.

Clinical chat used to reach a hand-written list of four services. There are
nineteen, carrying 187 GET endpoints between them, and that number moves
whenever a service ships. Rather than enumerate them in code — which is stale
the moment someone adds a route — this module discovers them at runtime from
each service's own ``/openapi.json`` and refreshes on a timer, so a new
endpoint becomes reachable from chat without a code change here.

Read-only by design
-------------------
Only GET operations are catalogued. The estate also exposes 108 write
endpoints, among them ``/reset`` on data_ingestion, ed_flow and
patient_journey, ``/admit-patient`` on hospital_ops, and the GDPR erasure
routes. This chat is reachable from a public dashboard, so a sentence typed
by an anonymous visitor must not be able to reset the simulation or mutate
patient state. Writes are therefore not discoverable here at all; exposing
any of them should be a deliberate allow-list, not a side effect of
discovery.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

REFRESH_SECONDS = float(os.environ.get("CATALOG_REFRESH_SECONDS", "600"))
DISCOVERY_TIMEOUT = float(os.environ.get("CATALOG_TIMEOUT", "8"))

# Env vars that name a service but are not HTTP services we can introspect.
_SKIP = {"mongo", "redis", "loki", "clinical_chat"}

# Endpoints that are noise for question answering.
_SKIP_PATHS = re.compile(r"^/(health|metrics|openapi\.json|docs|redoc|kafka-events)$")


@dataclass
class Endpoint:
    service: str
    base_url: str
    path: str
    summary: str = ""
    description: str = ""
    tags: List[str] = field(default_factory=list)
    path_params: List[Dict[str, Any]] = field(default_factory=list)
    query_params: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.service}{self.path}"

    def signature(self) -> str:
        """One compact line describing this endpoint to a model."""
        bits = [f"{self.key}"]
        if self.path_params:
            bits.append("path:" + ",".join(p["name"] for p in self.path_params))
        required_q = [p["name"] for p in self.query_params if p.get("required")]
        optional_q = [p["name"] for p in self.query_params if not p.get("required")]
        if required_q:
            bits.append("required:" + ",".join(required_q))
        if optional_q:
            bits.append("optional:" + ",".join(optional_q[:6]))
        text = (self.summary or self.description or "").strip().split("\n")[0]
        if text:
            bits.append("— " + text[:110])
        return " ".join(bits)


class ServiceCatalog:
    """Discovers and caches every GET endpoint across the estate."""

    def __init__(self, services: Optional[Dict[str, str]] = None):
        self.services = services if services is not None else self._services_from_env()
        self.endpoints: List[Endpoint] = []
        self.errors: Dict[str, str] = {}
        self.refreshed_at: float = 0.0
        self._task: Optional[asyncio.Task] = None

    @staticmethod
    def _services_from_env() -> Dict[str, str]:
        out = {}
        for key, val in os.environ.items():
            if not key.endswith("_URL") or not isinstance(val, str):
                continue
            if not val.startswith("http"):
                continue
            name = key[:-4].lower()
            if name in _SKIP:
                continue
            out[name] = val.rstrip("/")
        return out

    # ── discovery ────────────────────────────────────────────────────
    async def refresh(self) -> int:
        found: List[Endpoint] = []
        errors: Dict[str, str] = {}

        async with httpx.AsyncClient(
            timeout=DISCOVERY_TIMEOUT, verify=False, trust_env=False
        ) as client:
            async def one(name: str, base: str):
                try:
                    spec = (await client.get(f"{base}/openapi.json")).json()
                except Exception as exc:  # noqa: BLE001
                    errors[name] = type(exc).__name__
                    return
                for path, ops in (spec.get("paths") or {}).items():
                    if _SKIP_PATHS.match(path):
                        continue
                    op = ops.get("get")
                    if not op:
                        continue          # writes are deliberately not catalogued
                    params = op.get("parameters") or []
                    found.append(Endpoint(
                        service=name,
                        base_url=base,
                        path=path,
                        summary=(op.get("summary") or "").strip(),
                        description=(op.get("description") or "").strip(),
                        tags=list(op.get("tags") or []),
                        path_params=[
                            {"name": p["name"], "type": (p.get("schema") or {}).get("type", "string")}
                            for p in params if p.get("in") == "path"
                        ],
                        query_params=[
                            {"name": p["name"],
                             "type": (p.get("schema") or {}).get("type", "string"),
                             "required": bool(p.get("required"))}
                            for p in params if p.get("in") == "query"
                        ],
                    ))

            await asyncio.gather(*[one(n, b) for n, b in self.services.items()])

        found.sort(key=lambda e: e.key)
        self.endpoints = found
        self.errors = errors
        self.refreshed_at = time.time()
        logger.info(
            "service_catalog_refreshed endpoints=%d services=%d unreachable=%s",
            len(found), len(self.services) - len(errors),
            ",".join(sorted(errors)) or "none",
        )
        return len(found)

    async def start(self) -> None:
        await self.refresh()
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(REFRESH_SECONDS)
                await self.refresh()
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("service_catalog_refresh_failed: %s", exc)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    # ── search ───────────────────────────────────────────────────────
    _STOP = {
        "the", "a", "an", "is", "are", "was", "were", "of", "in", "on", "for",
        "to", "and", "or", "what", "how", "many", "much", "me", "show", "give",
        "get", "list", "all", "current", "currently", "now", "today", "please",
        "do", "does", "with", "from", "by", "at", "it", "this", "that",
    }

    def _tokens(self, text: str) -> List[str]:
        return [t for t in re.split(r"[^a-z0-9]+", text.lower())
                if len(t) > 2 and t not in self._STOP]

    def search(self, query: str, limit: int = 12) -> List[Endpoint]:
        return [ep for _, ep in self.search_scored(query, limit)]

    def search_scored(self, query: str, limit: int = 12) -> List[tuple]:
        """Rank endpoints against a natural-language question, with scores."""
        terms = self._tokens(query)
        if not terms:
            return []
        scored = []
        for ep in self.endpoints:
            hay = " ".join([
                ep.service.replace("_", " "), ep.path.replace("/", " ").replace("_", " "),
                ep.summary, ep.description[:200], " ".join(ep.tags),
            ]).lower()
            hay_tokens = set(self._tokens(hay))
            score = 0
            for t in terms:
                if t in hay_tokens:
                    score += 3                      # whole-token hit
                elif t in hay:
                    score += 1                      # substring hit
            if ep.path_params:
                score -= 1        # needs an id we may not have; prefer listings
            if score > 0:
                scored.append((score, ep))
        scored.sort(key=lambda pair: (-pair[0], pair[1].key))
        return scored[:limit]

    def get(self, service: str, path: str) -> Optional[Endpoint]:
        for ep in self.endpoints:
            if ep.service == service and ep.path == path:
                return ep
        return None

    def snapshot(self) -> Dict[str, Any]:
        by_service: Dict[str, int] = {}
        for ep in self.endpoints:
            by_service[ep.service] = by_service.get(ep.service, 0) + 1
        return {
            "endpoint_count": len(self.endpoints),
            "services": by_service,
            "unreachable": self.errors,
            "refreshed_at": self.refreshed_at,
            "refresh_seconds": REFRESH_SECONDS,
            "read_only": True,
        }
