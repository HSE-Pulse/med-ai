"""Structured JSON logging + Loki push — opt-in, fail-open, one-line per service.

Why
---
Today every service logs plain text to stdout. To debug a specific
admission you'd have to grep 18 terminal windows. With this module:

- All logs are JSON-serialised with service / trace_id / span_id fields
- Logs are shipped to Loki (the centralised store running in Docker)
- A Grafana panel queries ``{service="bed_management"} |= "hadm_id=X"``
  to see exactly what that service did for patient X
- Click a Jaeger trace → copy the trace_id → paste into Grafana Logs
  → see every log line from every service for that single request

Usage
-----

In every service's FastAPI startup (after setup_tracing):

    from shared.integration.logging_config import setup_logging

    setup_logging(service_name="bed_management")

That's it. When ``LOKI_URL`` is unset, logs still go to stdout as JSON;
when it's set, they also push to Loki every second.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

LOKI_URL = os.environ.get("LOKI_URL", "").strip()


class JsonFormatter(logging.Formatter):
    """Serialise each log record to a single-line JSON document.

    Adds trace_id/span_id when present (the OTel LoggingInstrumentor
    attaches them as record attributes). Everything else in the record's
    ``__dict__`` that isn't a standard field is carried through as extras.
    """

    _RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "taskName",
        # OTel injected fields we handle explicitly
        "otelTraceID", "otelSpanID", "otelServiceName", "otelTraceSampled",
    }

    def __init__(self, service_name: str = "") -> None:
        super().__init__()
        self.service_name = service_name
        self.hostname = socket.gethostname()

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        # strftime lacks %f on Windows — build ISO-8601 timestamp manually.
        # ``record.created`` is a float epoch; ``record.msecs`` is the
        # fractional millisecond component.
        import datetime as _dt
        dt = _dt.datetime.fromtimestamp(record.created, tz=_dt.timezone.utc)
        payload: Dict[str, Any] = {
            "ts": dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        if self.service_name:
            payload["service"] = self.service_name
        payload["host"] = self.hostname

        # OTel trace correlation (present when LoggingInstrumentor ran)
        trace_id = getattr(record, "otelTraceID", None) or getattr(record, "trace_id", None)
        span_id = getattr(record, "otelSpanID", None) or getattr(record, "span_id", None)
        if trace_id and trace_id != "0":
            payload["trace_id"] = trace_id
        if span_id and span_id != "0":
            payload["span_id"] = span_id

        # Exception traceback
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        # Any extras the caller passed via `logger.info(..., extra={...})`
        for k, v in record.__dict__.items():
            if k in self._RESERVED or k.startswith("_"):
                continue
            if k in payload:
                continue
            # Only keep JSON-serialisable values
            try:
                json.dumps(v, default=str)
                payload[k] = v
            except Exception:  # noqa: BLE001
                payload[k] = str(v)

        return json.dumps(payload, default=str)


def _build_loki_handler(service_name: str, url: str) -> Optional[logging.Handler]:
    """Return a logging-to-Loki handler, or None if the dep is missing.

    Note on severity casing
    -----------------------
    `python-logging-loki` lowercases `levelname` before sending, so log
    records arrive in Loki as ``severity="info" | "warning" | "error"``,
    NOT ``"INFO" | "WARNING" | "ERROR"``. Grafana / LogQL queries against
    these labels MUST use lowercase:

        {service="data_ingestion", severity="warning"}    # ✅ matches
        {service="data_ingestion", severity="WARNING"}    # ❌ no rows

    The 15-min audit found queries silently returning zero results because
    of this mismatch — flagged as N7. This docstring is the canonical home
    for the convention.
    """
    try:
        from logging_loki import LokiQueueHandler  # type: ignore
        from multiprocessing import Queue
    except ImportError:
        logger.warning(
            "logging_loki not installed — structured logs will stay local only"
        )
        return None

    # Accept either a base URL (http://localhost:3100) or a full push URL
    # (http://localhost:3100/loki/api/v1/push). Pre-fix this always
    # appended the suffix unconditionally, producing
    # …/loki/api/v1/push/loki/api/v1/push (404) when operators set the
    # full URL — which is what the docs / docker-compose comments
    # actually instruct. Detect and normalise.
    url_clean = url.rstrip("/")
    push_path = "/loki/api/v1/push"
    full_url = url_clean if url_clean.endswith(push_path) else url_clean + push_path
    try:
        handler = LokiQueueHandler(
            Queue(-1),                             # unbounded in-memory queue
            url=full_url,
            tags={
                "service": service_name,
                "source": "medai",
                "env": os.environ.get("DEPLOYMENT_MODE", "dev"),
            },
            version="1",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("loki_handler_init_failed: %s", exc)
        return None

    handler.setFormatter(JsonFormatter(service_name=service_name))
    return handler


_configured_for: Optional[str] = None


def setup_logging(service_name: str, level: str = "INFO") -> None:
    """Configure structured JSON logging and (if ``LOKI_URL`` is set) push to Loki.

    Idempotent per process — second call with the same service name no-ops.
    Calling with a different service name reconfigures (shouldn't happen
    in practice; each process is one service).

    Parameters
    ----------
    service_name:
        The ``service`` label attached to every log record.
    level:
        Root logger level. Default INFO. Set via env var ``LOG_LEVEL`` if
        the caller prefers.
    """
    global _configured_for

    level = os.environ.get("LOG_LEVEL", level).upper()
    if _configured_for == service_name:
        return

    root = logging.getLogger()
    # Strip any handlers installed by uvicorn / libraries so our format wins
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(level)

    # Stdout — JSON (piped to Docker logs / stdout in prod)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(JsonFormatter(service_name=service_name))
    stdout_handler.setLevel(level)
    root.addHandler(stdout_handler)

    # Loki — push JSON records directly via HTTP queue handler
    if LOKI_URL:
        loki_handler = _build_loki_handler(service_name, LOKI_URL)
        if loki_handler is not None:
            loki_handler.setLevel(level)
            root.addHandler(loki_handler)
            logger.info(
                "loki_logging_enabled service=%s url=%s", service_name, LOKI_URL,
            )
    else:
        logger.info("loki_logging_disabled service=%s reason=no LOKI_URL", service_name)

    # Calm down the noisy libraries
    for noisy in ("urllib3", "httpx", "uvicorn.access", "httpcore"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, logging.getLogger().level))

    _configured_for = service_name
    logger.info("json_logging_configured service=%s level=%s", service_name, level)
