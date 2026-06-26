"""Tiny Prometheus exporter — drop-in for any MedAI FastAPI service.

Why not prometheus_fastapi_instrumentator?
-----------------------------------------
Big dep; most services only need a handful of counters. This helper
gives you:
  - ``/metrics`` endpoint returning OpenMetrics-format text
  - Standard Python process metrics (memory, CPU, fds, threads, GC)
  - A ``Metrics`` namespace you can hang domain gauges off of

Usage::

    from shared.integration.prometheus_metrics import install_metrics, Metrics

    app = create_app(...)
    metrics = install_metrics(app, service_name="hospital_ops")

    # Now a domain gauge:
    metrics.gauge("patient_flow_active_patients", "Active patients in DES now").set(123)

    # Or a counter:
    metrics.counter("patient_flow_admissions_total", "Total admissions since boot").inc()
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
    CONTENT_TYPE_LATEST,
    REGISTRY,
    ProcessCollector,
    PlatformCollector,
    GCCollector,
)

logger = logging.getLogger(__name__)


class Metrics:
    """Small registry wrapper — one instance per service."""

    def __init__(self, service_name: str) -> None:
        self.service_name = service_name
        # Use the global REGISTRY so multiprocess / Process collectors
        # are visible too. Label every gauge/counter we create with the
        # service name so Grafana can partition.
        self._gauges: Dict[str, Gauge] = {}
        self._counters: Dict[str, Counter] = {}
        self._histograms: Dict[str, Histogram] = {}

    def gauge(self, name: str, description: str, labelnames: Optional[list] = None) -> Gauge:
        if name not in self._gauges:
            try:
                self._gauges[name] = Gauge(
                    name, description, labelnames or [],
                )
            except ValueError:
                # Already registered in this process — look it up
                self._gauges[name] = REGISTRY._names_to_collectors[name]  # type: ignore
        return self._gauges[name]

    def counter(self, name: str, description: str, labelnames: Optional[list] = None) -> Counter:
        if name not in self._counters:
            try:
                self._counters[name] = Counter(
                    name, description, labelnames or [],
                )
            except ValueError:
                self._counters[name] = REGISTRY._names_to_collectors[name]  # type: ignore
        return self._counters[name]

    def histogram(
        self,
        name: str,
        description: str,
        labelnames: Optional[list] = None,
        buckets: Optional[tuple] = None,
    ) -> Histogram:
        if name not in self._histograms:
            kwargs: Dict[str, Any] = {"labelnames": labelnames or []}
            if buckets:
                kwargs["buckets"] = buckets
            try:
                self._histograms[name] = Histogram(name, description, **kwargs)
            except ValueError:
                self._histograms[name] = REGISTRY._names_to_collectors[name]  # type: ignore
        return self._histograms[name]


_service_metrics: Dict[str, Metrics] = {}


def install_metrics(app, service_name: str) -> Metrics:
    """Mount a ``GET /metrics`` endpoint on the given FastAPI app.

    The endpoint serves Prometheus exposition-format text. Standard
    process + platform + GC collectors are registered on first call
    (guarded so repeat installs don't double-register).

    Returns a ``Metrics`` object the caller can use to register
    domain-specific gauges / counters / histograms.
    """
    from fastapi import Response

    if service_name not in _service_metrics:
        _service_metrics[service_name] = Metrics(service_name)

        # Standard collectors — one-time per process
        try:
            ProcessCollector()
            PlatformCollector()
            GCCollector()
        except ValueError:
            # Already registered in this process (hot reload, etc.)
            pass

    metrics = _service_metrics[service_name]

    @app.get("/metrics", include_in_schema=False)
    async def _metrics_endpoint():  # noqa: ANN202
        data = generate_latest(REGISTRY)
        return Response(
            content=data,
            media_type=CONTENT_TYPE_LATEST,
        )

    logger.info("prometheus_metrics_installed service=%s endpoint=/metrics", service_name)
    return metrics
