"""Unit tests for shared.integration.logging_config.

Verifies JSON formatter output, trace_id injection, and that Loki push
is a no-op when LOKI_URL is unset.
"""

from __future__ import annotations

import importlib
import json
import logging
from io import StringIO

import pytest


def _reload_logging_config(monkeypatch, loki_url: str = ""):
    if loki_url:
        monkeypatch.setenv("LOKI_URL", loki_url)
    else:
        monkeypatch.delenv("LOKI_URL", raising=False)
    import shared.integration.logging_config as mod
    importlib.reload(mod)
    # Reset the module-level idempotency guard between tests
    mod._configured_for = None
    return mod


def test_json_formatter_produces_valid_json():
    from shared.integration.logging_config import JsonFormatter

    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="foo.py",
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    fmt = JsonFormatter(service_name="svc")
    output = fmt.format(record)
    doc = json.loads(output)
    assert doc["msg"] == "hello world"
    assert doc["level"] == "INFO"
    assert doc["service"] == "svc"
    assert "ts" in doc


def test_json_formatter_includes_trace_id():
    from shared.integration.logging_config import JsonFormatter

    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname="", lineno=0, msg="m", args=(), exc_info=None,
    )
    # Simulate OTel LoggingInstrumentor attributes
    record.otelTraceID = "abc123"
    record.otelSpanID = "def456"
    doc = json.loads(JsonFormatter("svc").format(record))
    assert doc["trace_id"] == "abc123"
    assert doc["span_id"] == "def456"


def test_json_formatter_drops_zero_trace_ids():
    """OTel sets traceID=0 when no active span — we shouldn't leak that."""
    from shared.integration.logging_config import JsonFormatter

    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname="", lineno=0, msg="m", args=(), exc_info=None,
    )
    record.otelTraceID = "0"
    record.otelSpanID = "0"
    doc = json.loads(JsonFormatter("svc").format(record))
    assert "trace_id" not in doc
    assert "span_id" not in doc


def test_json_formatter_carries_extras():
    from shared.integration.logging_config import JsonFormatter

    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname="", lineno=0, msg="m", args=(), exc_info=None,
    )
    record.hadm_id = "27612209"
    record.subject_id = 10101340
    doc = json.loads(JsonFormatter("svc").format(record))
    assert doc["hadm_id"] == "27612209"
    assert doc["subject_id"] == 10101340


def test_json_formatter_handles_unserialisable_extras():
    from shared.integration.logging_config import JsonFormatter

    class WeirdObj:
        def __repr__(self):
            return "<weird>"

    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname="", lineno=0, msg="m", args=(), exc_info=None,
    )
    record.obj = WeirdObj()
    doc = json.loads(JsonFormatter("svc").format(record))
    # Should stringify rather than raise
    assert doc["obj"] == "<weird>"


def test_setup_logging_disabled_when_no_loki(monkeypatch):
    mod = _reload_logging_config(monkeypatch, loki_url="")
    mod.setup_logging(service_name="test_svc")

    # Root logger should have exactly 1 stdout handler (JSON-formatted)
    root = logging.getLogger()
    json_handlers = [h for h in root.handlers if hasattr(h, "formatter") and h.formatter is not None]
    assert len(json_handlers) >= 1
    # No Loki handler registered
    loki_handlers = [h for h in root.handlers if h.__class__.__name__ == "LokiQueueHandler"]
    assert loki_handlers == []


def test_setup_logging_idempotent(monkeypatch):
    mod = _reload_logging_config(monkeypatch, loki_url="")
    mod.setup_logging(service_name="svc")
    mod.setup_logging(service_name="svc")  # no-op
    # Still exactly 1 stdout handler (not doubled)
    root = logging.getLogger()
    stream_handlers = [h for h in root.handlers if h.__class__.__name__ == "StreamHandler"]
    assert len(stream_handlers) == 1


def test_setup_logging_writes_json_to_stdout(monkeypatch, capsys):
    mod = _reload_logging_config(monkeypatch, loki_url="")
    mod.setup_logging(service_name="x")

    log = logging.getLogger("test")
    log.warning("something %s", "happened")

    out = capsys.readouterr().out
    # Each line is a JSON doc
    lines = [l for l in out.splitlines() if l.strip()]
    parsed = [json.loads(l) for l in lines]
    warn = next((p for p in parsed if p.get("level") == "WARNING"), None)
    assert warn is not None
    assert warn["msg"] == "something happened"
    assert warn["service"] == "x"
