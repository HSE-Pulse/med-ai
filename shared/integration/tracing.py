"""OpenTelemetry tracing — opt-in, fail-open, one-line per service.

Why
---
When an admission goes through the Digital Twin cascade, it hops through
10+ services: ed_triage → ed_flow → bed_management → oncology_ai →
clinical_scribe → waiting_list → sepsis_icu → hospital_ops → deterioration.
Today, if one of those calls is slow, the only way to find out is to
read 10 terminal windows. With distributed tracing, one trace_id threads
through every span and a single Jaeger query shows the full waterfall.

Usage
-----

In every service's FastAPI startup:

    from shared.integration.tracing import setup_tracing

    app = FastAPI()
    setup_tracing(app, service_name="bed_management")

That's it. Auto-instrumentation wires up:
  - FastAPI request handlers (span per inbound HTTP)
  - httpx outbound calls (propagates trace context via W3C traceparent)
  - pymongo queries (span per Mongo call)
  - aiokafka produce (we manually inject the trace context into headers;
    consumer-side extract is in kafka_consumer.py)

Opt-in
------
If ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset, ``setup_tracing`` is a no-op.
This means tests and local one-off runs don't need the OTel stack at all.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Import guards — OTel SDK is a big dependency tree. Each service can skip
# installing these packages if tracing isn't needed.
# ---------------------------------------------------------------------------
try:
    from opentelemetry import trace, propagate
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False
    trace = None  # type: ignore

# Auto-instrumentation — each is optional so a service that doesn't use
# a given library (e.g. no Mongo) doesn't need that instrumentation.
try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
except ImportError:
    FastAPIInstrumentor = None  # type: ignore

try:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
except ImportError:
    HTTPXClientInstrumentor = None  # type: ignore

try:
    from opentelemetry.instrumentation.pymongo import PymongoInstrumentor
except ImportError:
    PymongoInstrumentor = None  # type: ignore

try:
    from opentelemetry.instrumentation.logging import LoggingInstrumentor
except ImportError:
    LoggingInstrumentor = None  # type: ignore


_initialised: bool = False
_tracer_provider: Optional[Any] = None


def is_enabled() -> bool:
    """True when OTel endpoint is configured and SDK is installed."""
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")) and _OTEL_AVAILABLE


def setup_tracing(
    app: Optional[Any] = None,
    *,
    service_name: str,
    extra_attributes: Optional[dict] = None,
) -> None:
    """Initialise the OTel SDK for this process and instrument common libs.

    Idempotent — safe to call multiple times per process.

    Parameters
    ----------
    app:
        FastAPI instance to instrument. Optional; when provided the
        FastAPI instrumentor wraps every request handler in a span.
    service_name:
        The ``service.name`` resource attribute — shows up as the service
        label in Jaeger. Use the underscored Python module name (e.g.
        ``bed_management``) for consistency with log messages.
    extra_attributes:
        Additional resource attributes to attach to every span.
    """
    global _initialised, _tracer_provider

    if _initialised:
        if app is not None and FastAPIInstrumentor is not None:
            try:
                FastAPIInstrumentor.instrument_app(app)
            except Exception:  # noqa: BLE001 — already instrumented is fine
                pass
        return

    if not is_enabled():
        logger.info(
            "tracing_disabled reason=%s",
            "no OTEL_EXPORTER_OTLP_ENDPOINT" if _OTEL_AVAILABLE else "otel sdk missing",
        )
        _initialised = True
        return

    endpoint = os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"]

    # Build the resource — everything in every span carries these attrs
    attrs = {
        "service.name": service_name,
        "service.namespace": os.environ.get("OTEL_SERVICE_NAMESPACE", "medai"),
        "service.version": os.environ.get("OTEL_SERVICE_VERSION", "1.0.0"),
        "deployment.environment": os.environ.get(
            "OTEL_DEPLOYMENT_ENV", os.environ.get("DEPLOYMENT_MODE", "dev")
        ),
    }
    if extra_attributes:
        attrs.update(extra_attributes)

    _tracer_provider = TracerProvider(resource=Resource.create(attrs))
    # OTLPSpanExporter auto-reads OTEL_EXPORTER_OTLP_ENDPOINT; passing
    # endpoint explicitly makes this robust against env changes later.
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    _tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(_tracer_provider)

    # Auto-instrument — each in a try/except so a missing library doesn't
    # take down the service.
    if app is not None and FastAPIInstrumentor is not None:
        try:
            # excluded_urls keeps the health-check noise off the trace UI
            FastAPIInstrumentor.instrument_app(
                app,
                excluded_urls=r"/health,/metrics,/kafka-events,/cache-stats",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("tracing_fastapi_instrument_failed: %s", exc)

    if HTTPXClientInstrumentor is not None:
        try:
            HTTPXClientInstrumentor().instrument()
        except Exception as exc:  # noqa: BLE001
            logger.warning("tracing_httpx_instrument_failed: %s", exc)

    if PymongoInstrumentor is not None:
        try:
            PymongoInstrumentor().instrument()
        except Exception as exc:  # noqa: BLE001
            logger.warning("tracing_pymongo_instrument_failed: %s", exc)

    # Log correlation — inject trace_id and span_id into every log record
    # so a grep in Loki / Elasticsearch can hop straight to the trace.
    if LoggingInstrumentor is not None:
        try:
            LoggingInstrumentor().instrument(
                set_logging_format=True,
                log_level=logging.INFO,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("tracing_logging_instrument_failed: %s", exc)

    _initialised = True
    logger.info(
        "tracing_enabled service=%s endpoint=%s env=%s",
        service_name, endpoint, attrs["deployment.environment"],
    )


def get_tracer(name: str = "medai"):
    """Return a tracer for manual span creation."""
    if trace is None:
        return _NullTracer()
    return trace.get_tracer(name)


# ---------------------------------------------------------------------------
# Kafka context propagation — aiokafka lacks a built-in OTel integration.
# We serialise the current trace context into headers on produce, and
# extract it on consume so the consumer-side span becomes a child of the
# producer-side span. One trace now spans producer→Kafka→consumer.
# ---------------------------------------------------------------------------
def inject_kafka_headers(headers: Optional[list] = None) -> list:
    """Return a headers list (list of (key, bytes) tuples) with current trace context."""
    headers = list(headers or [])
    if not is_enabled():
        return headers
    carrier: dict = {}
    try:
        propagate.inject(carrier)
    except Exception:  # noqa: BLE001
        return headers
    for k, v in carrier.items():
        headers.append((k, v.encode() if isinstance(v, str) else v))
    return headers


def extract_kafka_context(headers: Optional[list]) -> Any:
    """Rebuild the trace context from a Kafka message's headers.

    Returns a ``Context`` object suitable for ``with trace.use_context(ctx):``
    blocks in the consumer handler.
    """
    if not is_enabled() or not headers:
        return None
    carrier: dict = {}
    for k, v in headers:
        try:
            carrier[k] = v.decode() if isinstance(v, (bytes, bytearray)) else v
        except Exception:  # noqa: BLE001
            continue
    try:
        return propagate.extract(carrier)
    except Exception:  # noqa: BLE001
        return None


class _NullTracer:
    """Tracer stub used when OTel is unavailable."""
    def start_as_current_span(self, name: str, **kwargs):
        from contextlib import contextmanager
        @contextmanager
        def _cm():
            yield _NullSpan()
        return _cm()


class _NullSpan:
    def set_attribute(self, *a, **k): pass
    def set_attributes(self, *a, **k): pass
    def add_event(self, *a, **k): pass
    def record_exception(self, *a, **k): pass
    def set_status(self, *a, **k): pass
