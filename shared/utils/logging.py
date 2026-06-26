"""Structured JSON logging via structlog."""

from __future__ import annotations

import logging
import sys
from typing import Optional

import structlog


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog for structured JSON output.

    Call this once at application startup. Subsequent calls update the log
    level without re-adding processors.

    Parameters
    ----------
    level:
        Python log level name (``"DEBUG"``, ``"INFO"``, ``"WARNING"``, etc.).
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Configure stdlib logging (structlog renders through it)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=numeric_level,
        force=True,  # reset handlers on repeated calls
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str, level: Optional[str] = None) -> structlog.stdlib.BoundLogger:
    """Return a named structured logger.

    On the first call in a process this also runs :func:`configure_logging`
    so callers don't need to remember to initialise logging themselves.

    Parameters
    ----------
    name:
        Logger name (typically ``__name__`` of the calling module).
    level:
        Optional override for the root log level.

    Returns
    -------
    A ``structlog.stdlib.BoundLogger`` that emits JSON lines to stdout.
    """
    if not structlog.is_configured():
        configure_logging(level or "INFO")
    elif level:
        configure_logging(level)

    return structlog.get_logger(name)
