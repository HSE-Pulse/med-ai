"""Medications engine for the Patient Journey application.

Fetches prescriptions from MIMIC.prescriptions, builds medication timelines
with start/stop periods, and categorises drugs by therapeutic class.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from shared.db.mongo import MongoManager
from app_05_patient_journey.backend.engine.timeline import TimelineEngine

# ---------------------------------------------------------------------------
# Drug-category keyword patterns (case-insensitive matching)
# ---------------------------------------------------------------------------

_CATEGORY_PATTERNS: List[tuple[str, re.Pattern]] = [  # type: ignore[type-arg]
    (
        "antibiotic",
        re.compile(
            r"(cef|peni|cillin|mycin|cyclin|azithro|vanco|metro|flagyl|"
            r"pipera|tazo|meropenem|cipro|levo|moxi|amox|augmentin|"
            r"doxy|clinda|trimethoprim|sulfa|nitrofurantoin|linezolid|"
            r"dapto|genta|tobra|aztreo)",
            re.IGNORECASE,
        ),
    ),
    (
        "vasopressor",
        re.compile(
            r"(norepinephrine|vasopressin|phenylephrine|epinephrine|"
            r"dopamine|dobutamine|milrinone|levophed|neosynephrine)",
            re.IGNORECASE,
        ),
    ),
    (
        "sedation",
        re.compile(
            r"(propofol|midazolam|dexmedetomidine|lorazepam|diazepam|"
            r"precedex|ketamine|etomidate)",
            re.IGNORECASE,
        ),
    ),
    (
        "analgesic",
        re.compile(
            r"(fentanyl|morphine|hydromorphone|oxycodone|acetaminophen|"
            r"tylenol|ibuprofen|ketorolac|dilaudid|tramadol|gabapentin|"
            r"pregabalin|celecoxib|naproxen|aspirin)",
            re.IGNORECASE,
        ),
    ),
    (
        "anticoagulant",
        re.compile(
            r"(heparin|enoxaparin|warfarin|apixaban|rivaroxaban|"
            r"fondaparinux|argatroban|bivalirudin|lovenox|coumadin)",
            re.IGNORECASE,
        ),
    ),
    (
        "insulin",
        re.compile(r"(insulin|glargine|lispro|aspart|novolog|lantus|humalog)", re.IGNORECASE),
    ),
    (
        "antihypertensive",
        re.compile(
            r"(metoprolol|atenolol|lisinopril|enalapril|amlodipine|"
            r"losartan|valsartan|hydralazine|labetalol|carvedilol|"
            r"diltiazem|nifedipine|nicardipine|captopril)",
            re.IGNORECASE,
        ),
    ),
]


class MedicationsEngine:
    """Builds a medication timeline for a patient admission."""

    def __init__(self, mongo: MongoManager) -> None:
        self.mongo = mongo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_medication_timeline(
        self,
        subject_id: int,
        hadm_id: int,
    ) -> List[Dict[str, Any]]:
        """Return a list of medication periods for the admission.

        Each entry represents a single prescription with start/stop times,
        drug details, and an inferred therapeutic category.

        Returns
        -------
        List of dicts, sorted by ``start_time``::

            {
                "drug": "Vancomycin",
                "drug_type": "MAIN",
                "category": "antibiotic",
                "start_time": "2180-05-06T22:23:00",
                "stop_time": "2180-05-08T10:00:00",
                "dose_val_rx": "1",
                "dose_unit_rx": "g",
                "route": "IV",
                "prod_strength": "1g Vial",
                "duration_hours": 35.6
            }
        """
        raw = self._fetch_prescriptions(hadm_id)
        medications: List[Dict[str, Any]] = []

        for r in raw:
            drug = r.get("drug") or "Unknown"
            start_ts = TimelineEngine._parse_time(r.get("starttime"))
            stop_ts = TimelineEngine._parse_time(r.get("stoptime"))

            duration_hours: Optional[float] = None
            if start_ts and stop_ts:
                delta = (stop_ts - start_ts).total_seconds()
                duration_hours = round(delta / 3600, 2) if delta > 0 else None

            medications.append(
                {
                    "drug": drug,
                    "drug_type": r.get("drug_type"),
                    "category": self._categorize_drug(drug),
                    "start_time": start_ts.isoformat() if start_ts else None,
                    "stop_time": stop_ts.isoformat() if stop_ts else None,
                    "dose_val_rx": r.get("dose_val_rx"),
                    "dose_unit_rx": r.get("dose_unit_rx"),
                    "route": r.get("route"),
                    "prod_strength": r.get("prod_strength"),
                    "duration_hours": duration_hours,
                }
            )

        # Sort by start_time (nulls last)
        medications.sort(key=lambda m: m.get("start_time") or "9999")
        return medications

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fetch_prescriptions(self, hadm_id: int) -> List[Dict[str, Any]]:
        """Fetch all prescriptions for a hospital admission."""
        cursor = (
            self.mongo.mimic["prescriptions"]
            .find(
                {"hadm_id": hadm_id},
                {
                    "_id": 0,
                    "drug": 1,
                    "drug_type": 1,
                    "prod_strength": 1,
                    "dose_val_rx": 1,
                    "dose_unit_rx": 1,
                    "route": 1,
                    "starttime": 1,
                    "stoptime": 1,
                },
            )
            .sort("starttime", 1)
        )
        return list(cursor)

    @staticmethod
    def _categorize_drug(drug_name: str) -> str:
        """Infer a therapeutic category from the drug name.

        Returns one of: antibiotic, vasopressor, sedation, analgesic,
        anticoagulant, insulin, antihypertensive, or 'other'.
        """
        if not drug_name:
            return "other"
        for category, pattern in _CATEGORY_PATTERNS:
            if pattern.search(drug_name):
                return category
        return "other"
