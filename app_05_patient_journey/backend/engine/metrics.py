"""Derived metrics engine for the Patient Journey application.

Computes summary statistics for a patient admission: length-of-stay figures,
transfer counts, ICU episodes, drug counts, time-to-first-ICU, and mortality.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from shared.db.mongo import MongoManager
from app_05_patient_journey.backend.engine.timeline import TimelineEngine

_parse = TimelineEngine._parse_time


class DerivedMetrics:
    """Computes aggregate journey metrics for a single hospital admission."""

    def __init__(self, mongo: MongoManager) -> None:
        self.mongo = mongo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_journey_metrics(
        self, subject_id: int, hadm_id: int
    ) -> Dict[str, Any]:
        """Return a dictionary of derived metrics for the admission.

        Metrics
        -------
        - total_los_hours: total hospital length of stay
        - icu_los_hours: total ICU length of stay (sum of all episodes)
        - ed_los_hours: time spent in ED (edregtime -> edouttime)
        - num_transfers: number of intra-hospital transfers
        - num_icu_episodes: count of ICU stays
        - num_procedures: number of coded procedures
        - num_unique_drugs: count of distinct drug names
        - time_to_first_icu_hours: admission -> first ICU entry
        - mortality: in-hospital death flag (bool)
        """
        admission = self._get_admission(subject_id, hadm_id)
        if admission is None:
            return {"error": "admission_not_found"}

        admit_ts = _parse(admission.get("admittime"))
        disch_ts = _parse(admission.get("dischtime"))

        # Total LOS
        total_los_hours: Optional[float] = None
        if admit_ts and disch_ts:
            total_los_hours = round(
                (disch_ts - admit_ts).total_seconds() / 3600, 2
            )

        # ED LOS
        ed_los_hours: Optional[float] = None
        edreg = _parse(admission.get("edregtime"))
        edout = _parse(admission.get("edouttime"))
        if edreg and edout:
            ed_los_hours = round(
                (edout - edreg).total_seconds() / 3600, 2
            )

        # Mortality
        mortality = bool(admission.get("hospital_expire_flag"))

        # ICU stays
        icu_rows = list(
            self.mongo.mimic_icu["icustays"].find(
                {"subject_id": subject_id, "hadm_id": hadm_id},
                {"_id": 0, "stay_id": 1, "intime": 1, "outtime": 1, "los": 1},
            )
        )
        num_icu_episodes = len(icu_rows)
        icu_los_hours = round(
            sum(float(r.get("los") or 0) for r in icu_rows) * 24, 2
        )

        # Time to first ICU
        time_to_first_icu_hours: Optional[float] = None
        if admit_ts and icu_rows:
            icu_intimes = [_parse(r.get("intime")) for r in icu_rows]
            icu_intimes = [t for t in icu_intimes if t is not None]
            if icu_intimes:
                first_icu = min(icu_intimes)
                time_to_first_icu_hours = round(
                    (first_icu - admit_ts).total_seconds() / 3600, 2
                )

        # Transfers
        num_transfers = self.mongo.mimic["transfers"].count_documents(
            {"subject_id": subject_id, "hadm_id": hadm_id}
        )

        # Procedures
        num_procedures = self.mongo.mimic["procedures_icd"].count_documents(
            {"subject_id": subject_id, "hadm_id": hadm_id}
        )

        # Unique drugs
        drug_cursor = self.mongo.mimic["prescriptions"].find(
            {"hadm_id": hadm_id},
            {"_id": 0, "drug": 1},
        )
        unique_drugs: set[str] = set()
        for row in drug_cursor:
            drug = row.get("drug")
            if drug:
                unique_drugs.add(drug)
        num_unique_drugs = len(unique_drugs)

        return {
            "subject_id": subject_id,
            "hadm_id": hadm_id,
            "total_los_hours": total_los_hours,
            "icu_los_hours": icu_los_hours,
            "ed_los_hours": ed_los_hours,
            "num_transfers": num_transfers,
            "num_icu_episodes": num_icu_episodes,
            "num_procedures": num_procedures,
            "num_unique_drugs": num_unique_drugs,
            "time_to_first_icu_hours": time_to_first_icu_hours,
            "mortality": mortality,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_admission(
        self, subject_id: int, hadm_id: int
    ) -> Optional[Dict[str, Any]]:
        """Fetch a single admission record."""
        return self.mongo.mimic["admissions"].find_one(
            {"subject_id": subject_id, "hadm_id": hadm_id},
            {"_id": 0},
        )
