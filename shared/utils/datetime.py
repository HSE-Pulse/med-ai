"""Shared datetime parsing utilities for MIMIC data."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional


_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d-%m-%Y %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d-%m-%Y",
)


def parse_time(val: Any) -> Optional[datetime]:
    """Robustly parse MIMIC datetime values.

    Handles:
    - ``datetime`` objects (returned as-is)
    - ``'2180-05-06 22:23:00'``  (YYYY-MM-DD HH:MM:SS)
    - ``'2180-05-06T22:23:00'``  (ISO with T)
    - ``'23-07-2180 14:00'``     (DD-MM-YYYY HH:MM)
    - ``'2180-05-06'``           (date only)
    - ``'23-07-2180'``           (DD-MM-YYYY date only)
    - ISO format with timezone info (via fromisoformat fallback)
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if not isinstance(val, str):
        val = str(val)
    val = val.strip()
    if not val:
        return None

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue

    # Last resort: ISO format with timezone
    try:
        return datetime.fromisoformat(val)
    except (ValueError, TypeError):
        return None
