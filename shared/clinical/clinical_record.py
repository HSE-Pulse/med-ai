"""HIQA-aligned clinical record wrapper.

Part 6.6 of the uplift: HIQA's National Standards for Information Management
require every clinical record to carry:
  (a) provenance metadata (source system, timestamp, operator id)
  (b) data quality flags (is_complete, has_missing_fields[])
  (c) version history for updated records

This module exposes a single helper ``wrap`` that enriches any dict before
it is written to MongoDB, and ``update`` that appends a new version entry.
Wrap all ``MongoManager.insert_*`` / ``update_*`` calls with these helpers.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class Provenance:
    source_system: str
    operator_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    simulation: bool = True  # default: simulation mode is on unless overridden

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_system": self.source_system,
            "operator_id": self.operator_id,
            "timestamp": self.timestamp,
            "simulation": self.simulation,
        }


@dataclass
class DataQuality:
    is_complete: bool
    has_missing_fields: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_complete": self.is_complete,
            "has_missing_fields": list(self.has_missing_fields),
        }


def _check_completeness(record: Dict[str, Any], required_fields: Iterable[str]) -> DataQuality:
    missing = [f for f in required_fields if record.get(f) in (None, "", [], {})]
    return DataQuality(is_complete=not missing, has_missing_fields=missing)


def wrap(
    record: Dict[str, Any],
    *,
    source_system: str,
    operator_id: str = "system",
    required_fields: Optional[Iterable[str]] = None,
    simulation: bool = True,
    safeguarding_flag: Optional[bool] = None,
) -> Dict[str, Any]:
    """Wrap a raw record with HIQA provenance, quality, and version fields.

    Idempotent: passing an already-wrapped record preserves its existing
    version history and only refreshes provenance/quality fields. Use
    :func:`update` to register a new version.
    """
    enriched = copy.deepcopy(record)
    prov = Provenance(
        source_system=source_system,
        operator_id=operator_id,
        simulation=simulation,
    )
    quality = _check_completeness(enriched, required_fields or [])
    enriched["provenance"] = prov.to_dict()
    enriched["data_quality"] = quality.to_dict()
    if "version_history" not in enriched:
        enriched["version_history"] = [
            {"version": 1, "timestamp": prov.timestamp, "operator_id": operator_id}
        ]
    if safeguarding_flag is not None:
        enriched["safeguarding_flag"] = bool(safeguarding_flag)
    return enriched


def update(
    existing_record: Dict[str, Any],
    new_fields: Dict[str, Any],
    *,
    operator_id: str = "system",
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a new record merging ``new_fields`` into ``existing_record``.

    Appends an entry to ``version_history`` with the fields changed and
    bumps the version number.
    """
    merged = copy.deepcopy(existing_record)
    changed_fields = [k for k, v in new_fields.items() if merged.get(k) != v]
    merged.update(new_fields)

    history = merged.setdefault("version_history", [])
    next_version = (history[-1]["version"] if history else 0) + 1
    history.append(
        {
            "version": next_version,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "operator_id": operator_id,
            "changed_fields": changed_fields,
            "reason": reason,
        }
    )

    provenance = merged.get("provenance", {})
    provenance["timestamp"] = datetime.now(timezone.utc).isoformat()
    provenance["operator_id"] = operator_id
    merged["provenance"] = provenance
    return merged


def is_paediatric(age_years: Optional[float]) -> bool:
    """Used by Children First Act 2015 safeguarding auto-flagging."""
    if age_years is None:
        return False
    try:
        return float(age_years) < 18.0
    except (TypeError, ValueError):
        return False


__all__ = [
    "Provenance",
    "DataQuality",
    "wrap",
    "update",
    "is_paediatric",
]
