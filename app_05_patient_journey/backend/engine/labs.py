"""Lab-results engine for the Patient Journey application.

Fetches laboratory results from MIMIC.labevents, organises them into standard
clinical panels (CBC, BMP, LFT, Coag, Cardiac), and flags abnormal values.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from shared.db.mongo import MongoManager
from app_05_patient_journey.backend.engine.timeline import TimelineEngine

# ---------------------------------------------------------------------------
# Lab panel definitions: panel_name -> {lab_name: itemid}
# ---------------------------------------------------------------------------

LAB_PANELS: Dict[str, Dict[str, int]] = {
    "CBC": {
        "WBC": 51301,
        "Hemoglobin": 51222,
        "Platelets": 51265,
        "Hematocrit": 51221,
    },
    "BMP": {
        "Sodium": 50983,
        "Potassium": 50971,
        "Creatinine": 50912,
        "BUN": 51006,
        "Glucose": 50931,
        "Chloride": 50902,
        "Bicarbonate": 50882,
    },
    "LFT": {
        "Bilirubin": 50885,
        "ALT": 50861,
        "AST": 50878,
        "ALP": 50863,
    },
    "Coag": {
        "INR": 51237,
        "PTT": 51275,
    },
    "Cardiac": {
        "Troponin": 51003,
        "Lactate": 50813,
    },
}

# Reverse map: itemid -> (panel, lab_name)
_ITEMID_MAP: Dict[int, Tuple[str, str]] = {}
for _panel, _labs in LAB_PANELS.items():
    for _name, _iid in _labs.items():
        _ITEMID_MAP[_iid] = (_panel, _name)

# Collect all itemids across panels
ALL_LAB_ITEMIDS: List[int] = list(_ITEMID_MAP.keys())


class LabsEngine:
    """Provides grouped lab trends with abnormality flags."""

    def __init__(self, mongo: MongoManager) -> None:
        self.mongo = mongo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_lab_trends(
        self,
        subject_id: int,
        hadm_id: int,
        lab_groups: Optional[List[str]] = None,
    ) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        """Return lab results organised by panel and lab name.

        Parameters
        ----------
        subject_id:
            Patient identifier (unused directly; kept for API consistency).
        hadm_id:
            Hospital admission to query.
        lab_groups:
            Optional subset of panel names (e.g. ``["CBC", "BMP"]``).
            ``None`` returns all panels.

        Returns
        -------
        Nested dict::

            {
                "CBC": {
                    "WBC": [{"time": "...", "value": 11.2, "unit": "K/uL",
                             "flag": "high", "ref_lower": 4.5, "ref_upper": 11.0}, ...]
                },
                ...
            }
        """
        # Determine which itemids to fetch
        wanted_panels: Set[str] = set(lab_groups) if lab_groups else set(LAB_PANELS.keys())
        target_ids: List[int] = []
        for panel_name in wanted_panels:
            panel = LAB_PANELS.get(panel_name)
            if panel:
                target_ids.extend(panel.values())
        if not target_ids:
            return {}

        # Fetch raw labs
        raw = self._fetch_labs(hadm_id, target_ids)

        # Group into panels
        return self._group_labs(raw, wanted_panels)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fetch_labs(
        self, hadm_id: int, itemids: List[int]
    ) -> List[Dict[str, Any]]:
        """Query labevents for a single hadm_id with targeted itemids."""
        cursor = (
            self.mongo.mimic["labevents"]
            .find(
                {"hadm_id": hadm_id, "itemid": {"$in": itemids}},
                {
                    "_id": 0,
                    "hadm_id": 1,
                    "itemid": 1,
                    "charttime": 1,
                    "valuenum": 1,
                    "valueuom": 1,
                    "ref_range_lower": 1,
                    "ref_range_upper": 1,
                    "flag": 1,
                },
            )
            .sort("charttime", 1)
        )
        return list(cursor)

    def _group_labs(
        self,
        raw: List[Dict[str, Any]],
        wanted_panels: Set[str],
    ) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        """Organise raw lab rows into panel -> lab_name -> list of results."""
        result: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        for panel_name in wanted_panels:
            if panel_name in LAB_PANELS:
                result[panel_name] = {}

        for r in raw:
            itemid = r.get("itemid")
            mapping = _ITEMID_MAP.get(itemid)  # type: ignore[arg-type]
            if mapping is None:
                continue
            panel_name, lab_name = mapping
            if panel_name not in wanted_panels:
                continue

            ts = TimelineEngine._parse_time(r.get("charttime"))
            value = r.get("valuenum")
            if ts is None:
                continue

            ref_lower = self._safe_float(r.get("ref_range_lower"))
            ref_upper = self._safe_float(r.get("ref_range_upper"))
            flag = self._flag_abnormal(value, ref_lower, ref_upper)

            entry: Dict[str, Any] = {
                "time": ts.isoformat(),
                "value": value,
                "unit": r.get("valueuom", ""),
                "flag": flag,
                "ref_lower": ref_lower,
                "ref_upper": ref_upper,
            }
            result.setdefault(panel_name, {}).setdefault(lab_name, []).append(entry)

        return result

    @staticmethod
    def _flag_abnormal(
        value: Optional[float],
        ref_lower: Optional[float],
        ref_upper: Optional[float],
    ) -> str:
        """Classify a lab value relative to reference ranges.

        Returns one of ``"normal"``, ``"low"``, ``"high"``, ``"critical"``, or
        ``"unknown"`` if reference ranges are absent.
        """
        if value is None:
            return "unknown"
        if ref_lower is None and ref_upper is None:
            return "unknown"

        # Check critical thresholds (>2x outside range)
        if ref_lower is not None and ref_upper is not None:
            range_width = ref_upper - ref_lower
            if range_width > 0:
                if value < ref_lower - range_width:
                    return "critical"
                if value > ref_upper + range_width:
                    return "critical"

        if ref_lower is not None and value < ref_lower:
            return "low"
        if ref_upper is not None and value > ref_upper:
            return "high"
        return "normal"

    @staticmethod
    def _safe_float(val: Any) -> Optional[float]:
        """Convert a value to float, returning None on failure."""
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None
