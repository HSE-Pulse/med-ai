"""Vital-sign time-series engine for the Patient Journey application.

Fetches vital signs from MIMIC_ICU.chartevents (always via stay_id), resamples
to a configurable frequency, and applies physiological outlier detection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from shared.db.mongo import MongoManager
from app_05_patient_journey.backend.engine.timeline import TimelineEngine

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

from shared.constants.mimic import (
    VITAL_ITEMIDS,
    VITAL_ITEMID_TO_LABEL as _ITEMID_TO_VITAL,
)

# Physiological plausibility ranges for outlier detection
VITAL_RANGES: Dict[str, Tuple[float, float]] = {
    "Heart Rate": (20, 250),
    "Respiratory Rate": (4, 60),
    "SpO2": (50, 100),
    "SBP": (40, 300),
    "DBP": (20, 200),
    "Temperature": (30, 42),
    "Mean BP": (30, 250),
}


class VitalsEngine:
    """Provides resampled vital-sign time series for a patient admission."""

    def __init__(self, mongo: MongoManager) -> None:
        self.mongo = mongo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_vitals_timeseries(
        self,
        subject_id: int,
        hadm_id: int,
        resample: str = "1h",
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Return resampled vital-sign series keyed by vital name.

        Parameters
        ----------
        subject_id:
            Patient identifier (used to find ICU stays).
        hadm_id:
            Hospital admission to restrict to.
        resample:
            Pandas frequency string for resampling (e.g. ``'1h'``, ``'30min'``).

        Returns
        -------
        ``{ "Heart Rate": [{"time": "...", "value": 80.0}, ...], ... }``
        """
        # 1. Resolve stay_ids for this admission
        icu_rows = list(
            self.mongo.mimic_icu["icustays"].find(
                {"subject_id": subject_id, "hadm_id": hadm_id},
                {"_id": 0, "stay_id": 1},
            )
        )
        stay_ids = [r["stay_id"] for r in icu_rows if r.get("stay_id") is not None]
        if not stay_ids:
            return {}

        # 2. Fetch raw vitals
        raw = self._fetch_raw_vitals(stay_ids, list(VITAL_ITEMIDS.values()))
        if not raw:
            return {}

        # 3. Organise by vital name into pandas Series, resample, filter
        result: Dict[str, List[Dict[str, Any]]] = {}
        grouped: Dict[str, List[Tuple[datetime, float]]] = {}
        for r in raw:
            vital_name = _ITEMID_TO_VITAL.get(r["itemid"])
            if vital_name is None:
                continue
            ts = TimelineEngine._parse_time(r.get("charttime"))
            val = r.get("valuenum")
            if ts is None or val is None:
                continue
            # Outlier check
            if self._detect_outliers(val, vital_name):
                continue
            grouped.setdefault(vital_name, []).append((ts, float(val)))

        for vital_name, points in grouped.items():
            series = self._resample(points, resample)
            result[vital_name] = series

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_raw_vitals(
        self,
        stay_ids: List[int],
        vital_itemids: List[int],
    ) -> List[Dict[str, Any]]:
        """Query chartevents by stay_id (batch) with targeted itemids."""
        cursor = self.mongo.mimic_icu["chartevents"].find(
            {"stay_id": {"$in": stay_ids}, "itemid": {"$in": vital_itemids}},
            {
                "_id": 0,
                "stay_id": 1,
                "itemid": 1,
                "charttime": 1,
                "valuenum": 1,
                "valueuom": 1,
            },
        )
        return list(cursor)

    @staticmethod
    def _resample(
        points: List[Tuple[datetime, float]],
        freq: str,
    ) -> List[Dict[str, Any]]:
        """Resample (time, value) pairs to a regular frequency.

        Uses mean aggregation and forward-fill for gaps.
        """
        if not points:
            return []
        df = pd.DataFrame(points, columns=["time", "value"])
        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time").set_index("time")
        # Resample with mean, then forward-fill short gaps
        resampled = df.resample(freq).mean()
        resampled = resampled.ffill(limit=3)
        resampled = resampled.dropna(subset=["value"])
        result: List[Dict[str, Any]] = []
        for ts, row in resampled.iterrows():
            result.append(
                {
                    "time": ts.isoformat(),  # type: ignore[union-attr]
                    "value": round(float(row["value"]), 2),
                }
            )
        return result

    @staticmethod
    def _detect_outliers(value: float, vital_name: str) -> bool:
        """Return True if the value is outside physiological plausibility.

        Parameters
        ----------
        value:
            The measured numeric value.
        vital_name:
            Human-readable vital name (must be a key in ``VITAL_RANGES``).

        Returns
        -------
        ``True`` if the value should be considered an outlier and excluded.
        """
        bounds = VITAL_RANGES.get(vital_name)
        if bounds is None:
            return False
        lo, hi = bounds
        return value < lo or value > hi
