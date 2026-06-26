"""Unit tests for shared.integration.tracing.

These tests verify the tracing helper degrades gracefully when:
  - OTEL_EXPORTER_OTLP_ENDPOINT is not set
  - The OTel SDK is not installed
  - Kafka context inject/extract is called without a live trace
"""

from __future__ import annotations

import importlib
import pytest


def _reload_tracing(monkeypatch, endpoint: str = ""):
    """Reload the tracing module after patching env — forces re-evaluation
    of the OTEL_EXPORTER_OTLP_ENDPOINT constant."""
    if endpoint:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", endpoint)
    else:
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    import shared.integration.tracing as mod
    importlib.reload(mod)
    return mod


def test_disabled_when_no_endpoint(monkeypatch):
    mod = _reload_tracing(monkeypatch, endpoint="")
    assert mod.is_enabled() is False

    # Setup is a no-op
    mod.setup_tracing(app=None, service_name="test_svc")

    # Kafka helpers return empty
    assert mod.inject_kafka_headers() == []
    assert mod.extract_kafka_context([("traceparent", b"abc")]) is None


def test_get_tracer_returns_null_when_disabled(monkeypatch):
    mod = _reload_tracing(monkeypatch, endpoint="")
    tracer = mod.get_tracer("x")
    # Should be the _NullTracer shim — starts-as-current-span is a no-op
    with tracer.start_as_current_span("test") as span:
        span.set_attribute("k", "v")  # doesn't raise
        span.add_event("ev")           # doesn't raise
        span.record_exception(ValueError("x"))  # doesn't raise


def test_kafka_inject_appends_to_existing_headers(monkeypatch):
    """When tracing is disabled, inject_kafka_headers preserves existing headers."""
    mod = _reload_tracing(monkeypatch, endpoint="")
    existing = [("my-header", b"value")]
    out = mod.inject_kafka_headers(existing)
    # Tracing disabled → returns input unchanged (as a new list)
    assert out == existing


def test_kafka_extract_handles_malformed_headers(monkeypatch):
    """extract_kafka_context must not raise on garbage input."""
    mod = _reload_tracing(monkeypatch, endpoint="http://fake:4317")
    # Tracing is "enabled" in this test by env, but OTel may or may not
    # have the propagator registered. Whatever happens, no exception.
    try:
        result = mod.extract_kafka_context([(123, None), ("", b"")])
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"extract_kafka_context raised on malformed input: {exc}")
    # Result is either None (no valid context) or a Context object — both OK.


def test_setup_tracing_idempotent(monkeypatch):
    """Calling setup_tracing twice must not blow up."""
    mod = _reload_tracing(monkeypatch, endpoint="")
    mod.setup_tracing(app=None, service_name="test_svc")
    mod.setup_tracing(app=None, service_name="test_svc")  # second call — no error
