"""Typed HTTP client for inter-module API communication.

Provides a unified interface for modules to call each other's APIs
with circuit-breaker protection, structured error logging, and
graceful degradation.

Usage::

    client = ServiceClient()
    # Get acuity prediction from ED Triage
    result = await client.ed_triage.post("/predict", patient_data)
    # Get bed availability from Bed Management
    beds = await client.bed_management.get("/beds", params={"dept": "MAU"})
    # Aggregate circuit-breaker state
    snapshot = client.breaker_snapshot()

Services that are not running (or that have tripped their breaker) return
``{"status":"error","error":"..."}`` dicts rather than raising.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import httpx

from shared.integration.circuit_breaker import BreakerState, CircuitBreaker

logger = logging.getLogger(__name__)


# Service registry: module name → base URL.
# Each entry can be overridden via env var (e.g. ED_TRIAGE_URL=http://app_01_ed_triage:8201)
# so the same code runs both on the host (localhost defaults) and in
# Docker (sibling-service DNS).
def _svc(name: str, default_port: int) -> str:
    env = f"{name.upper()}_URL"
    return os.environ.get(env, f"http://localhost:{default_port}")


SERVICE_REGISTRY = {
    "ed_triage": _svc("ed_triage", 8201),
    "sepsis_icu": _svc("sepsis_icu", 8202),
    "hospital_ops": _svc("hospital_ops", 8203),
    "oncology_ai": _svc("oncology_ai", 8204),
    "patient_journey": _svc("patient_journey", 8205),
    "clinical_chat": _svc("clinical_chat", 8206),
    "data_ingestion": _svc("data_ingestion", 8207),
    "bed_management": _svc("bed_management", 8208),
    "waiting_list": _svc("waiting_list", 8209),
    "clinical_scribe": _svc("clinical_scribe", 8210),
    "ed_flow": _svc("ed_flow", 8214),
    "erp": _svc("erp", 8215),
    # Uplift Part 4 additions (6 new services)
    "trolley_watch": _svc("trolley_watch", 8216),
    "gdpr": _svc("gdpr", 8217),
    "xai": _svc("xai", 8218),
    "fhir": _svc("fhir", 8219),
    "deterioration": _svc("deterioration", 8220),
    "discharge_lounge": _svc("discharge_lounge", 8221),
}


class ModuleClient:
    """HTTP client for a specific module, wrapped in a circuit breaker."""

    def __init__(
        self,
        base_url: str,
        module_name: str,
        timeout: float = 10.0,
        breaker: Optional[CircuitBreaker] = None,
    ) -> None:
        self.base_url = base_url
        self.module_name = module_name
        self.timeout = timeout
        self.breaker = breaker or CircuitBreaker(module_name)

    async def get(self, path: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        return await self._call("GET", path, params=params)

    async def post(
        self,
        path: str,
        data: Optional[Dict] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self._call("POST", path, data=data, idempotency_key=idempotency_key)

    async def delete(self, path: str) -> Dict[str, Any]:
        return await self._call("DELETE", path)

    async def patch(
        self,
        path: str,
        data: Optional[Dict] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self._call("PATCH", path, data=data, idempotency_key=idempotency_key)

    async def _call(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not await self.breaker.allow():
            logger.warning(
                "cross_service_call_short_circuited",
                extra={"service": self.module_name, "endpoint": path},
            )
            return {
                "status": "error",
                "error": f"{self.module_name} circuit open",
                "circuit": "open",
            }

        url = f"{self.base_url}{path}"
        headers: Dict[str, str] = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            # trust_env=False prevents httpx from reading SSL_CERT_FILE /
            # REQUESTS_CA_BUNDLE / HTTP(S)_PROXY from the environment. This
            # matters because a misconfigured SSL_CERT_FILE (common on conda
            # Windows envs where the path points at a non-existent file)
            # otherwise crashes AsyncClient instantiation before a single
            # request can fly, tripping every circuit breaker with zero
            # useful signal.
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
                if method == "GET":
                    resp = await client.get(url, params=params, headers=headers)
                elif method == "POST":
                    resp = await client.post(url, json=data or {}, headers=headers)
                elif method == "DELETE":
                    resp = await client.delete(url, headers=headers)
                elif method == "PATCH":
                    resp = await client.patch(url, json=data or {}, headers=headers)
                else:
                    raise ValueError(f"unsupported method {method}")
                resp.raise_for_status()
                await self.breaker.record_success()
                return resp.json()
        except httpx.ConnectError as exc:
            await self.breaker.record_failure()
            logger.warning(
                "cross_service_call_failed",
                extra={
                    "service": self.module_name,
                    "endpoint": path,
                    "error": "connect_error",
                    "detail": str(exc),
                },
            )
            return {"status": "error", "error": f"{self.module_name} unavailable"}
        except Exception as exc:
            await self.breaker.record_failure()
            logger.warning(
                "cross_service_call_failed",
                extra={
                    "service": self.module_name,
                    "endpoint": path,
                    "error": type(exc).__name__,
                    "detail": str(exc),
                },
            )
            return {"status": "error", "error": str(exc)}

    async def is_healthy(self) -> bool:
        """Check if the module is running and healthy."""
        result = await self.get("/health")
        return result.get("status") == "ok"


class ServiceClient:
    """Unified client for all platform modules.

    Each module gets its own :class:`CircuitBreaker` instance. The client
    exposes :meth:`breaker_snapshot` for dashboards and compliance endpoints
    (``GET /sim/circuit-breaker-status``).
    """

    def __init__(self, overrides: Optional[Dict[str, str]] = None) -> None:
        self._registry = {**SERVICE_REGISTRY, **(overrides or {})}
        self._clients: Dict[str, ModuleClient] = {}
        self._breakers: Dict[str, CircuitBreaker] = {}

    def _get_breaker(self, name: str) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(name)
        return self._breakers[name]

    def reset_breakers(self) -> int:
        """Force every breaker to CLOSED. Used after a recoverable issue
        (e.g. services came up late on startup) so the caller doesn't have
        to wait for the 30-second half-open probe cycle."""
        from shared.integration.circuit_breaker import BreakerState, BreakerStats
        n = 0
        for br in self._breakers.values():
            br._stats = BreakerStats()
            n += 1
        return n

    def _get_client(self, name: str) -> ModuleClient:
        if name not in self._clients:
            url = self._registry.get(name, "http://localhost:8200")
            self._clients[name] = ModuleClient(url, name, breaker=self._get_breaker(name))
        return self._clients[name]

    # ------------------------------------------------------------------ typed accessors
    @property
    def ed_triage(self) -> ModuleClient:
        return self._get_client("ed_triage")

    @property
    def sepsis_icu(self) -> ModuleClient:
        return self._get_client("sepsis_icu")

    @property
    def hospital_ops(self) -> ModuleClient:
        return self._get_client("hospital_ops")

    @property
    def oncology_ai(self) -> ModuleClient:
        return self._get_client("oncology_ai")

    @property
    def patient_journey(self) -> ModuleClient:
        return self._get_client("patient_journey")

    @property
    def clinical_chat(self) -> ModuleClient:
        return self._get_client("clinical_chat")

    @property
    def data_ingestion(self) -> ModuleClient:
        return self._get_client("data_ingestion")

    @property
    def bed_management(self) -> ModuleClient:
        return self._get_client("bed_management")

    @property
    def waiting_list(self) -> ModuleClient:
        return self._get_client("waiting_list")

    @property
    def clinical_scribe(self) -> ModuleClient:
        return self._get_client("clinical_scribe")

    @property
    def ed_flow(self) -> ModuleClient:
        return self._get_client("ed_flow")

    @property
    def erp(self) -> ModuleClient:
        return self._get_client("erp")

    # ------------------------------------------------------------------ new Part 4 modules
    @property
    def trolley_watch(self) -> ModuleClient:
        return self._get_client("trolley_watch")

    @property
    def gdpr(self) -> ModuleClient:
        return self._get_client("gdpr")

    @property
    def xai(self) -> ModuleClient:
        return self._get_client("xai")

    @property
    def fhir(self) -> ModuleClient:
        return self._get_client("fhir")

    @property
    def deterioration(self) -> ModuleClient:
        return self._get_client("deterioration")

    @property
    def discharge_lounge(self) -> ModuleClient:
        return self._get_client("discharge_lounge")

    # ------------------------------------------------------------------ dashboards
    async def health_check_all(self) -> Dict[str, bool]:
        """Check health of all registered modules in parallel.

        Previously this iterated services and awaited each ``is_healthy()``
        sequentially, so one unresponsive module could block the whole call
        for the full per-request timeout (multiple seconds × N modules).
        That was enough to lock up the data_ingestion event loop whenever
        the dashboard's /simulation page kept polling /digital-twin/state.
        Running checks concurrently with ``asyncio.gather`` and adding a
        short hard timeout per probe keeps the aggregate latency bounded
        regardless of how many services are down.
        """
        import asyncio

        names = list(self._registry)

        async def _probe(name: str) -> bool:
            try:
                return await asyncio.wait_for(self._get_client(name).is_healthy(), timeout=2.0)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                return False

        outcomes = await asyncio.gather(*[_probe(n) for n in names], return_exceptions=False)
        return dict(zip(names, outcomes))

    def breaker_snapshot(self) -> List[Dict[str, Any]]:
        """Return the current state of every circuit breaker for dashboards."""
        return [b.snapshot() for b in self._breakers.values()]


# --------------------------------------------------------------------------- ERP config helper
_ERP_CONFIG_CACHE: Dict[str, Any] = {}
_ERP_CACHE_EXPIRY: Dict[str, float] = {}
_ERP_CACHE_TTL_S: float = 300.0


async def get_erp_config(
    section: str,
    *,
    client: Optional[ServiceClient] = None,
    ttl_s: float = _ERP_CACHE_TTL_S,
) -> Dict[str, Any]:
    """Fetch a master-data section from the ERP service with process-local TTL caching.

    Parameters
    ----------
    section:
        The ERP config section to fetch — one of ``"departments"``, ``"staff"``,
        ``"schedule"``, ``"config"``.
    client:
        Optional pre-built :class:`ServiceClient`; if absent a singleton is used.
    ttl_s:
        Cache time-to-live in seconds. Defaults to 5 minutes.

    Returns
    -------
    Dict with the ERP config payload or an ``{"status":"error"}`` response on
    failure. Callers should treat errors as soft — fall back to the static
    constants in :mod:`shared.constants.hospital`.
    """
    import time as _time

    now = _time.time()
    cached = _ERP_CONFIG_CACHE.get(section)
    if cached is not None and _ERP_CACHE_EXPIRY.get(section, 0) > now:
        return cached

    c = client or _get_default_client()
    result = await c.erp.get(f"/{section}")
    if result.get("status") != "error":
        _ERP_CONFIG_CACHE[section] = result
        _ERP_CACHE_EXPIRY[section] = now + ttl_s
    return result


_default_client_singleton: Optional[ServiceClient] = None


def _get_default_client() -> ServiceClient:
    global _default_client_singleton
    if _default_client_singleton is None:
        _default_client_singleton = ServiceClient()
    return _default_client_singleton
