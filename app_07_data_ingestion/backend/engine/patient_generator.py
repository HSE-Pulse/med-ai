"""Patient generator that pulls actual MIMIC patient journeys for replay.

Loads a pool of admissions from the MIMIC database and iterates through
them, fetching full clinical journeys (transfers, vitals, labs, meds,
diagnoses, procedures) on demand.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is importable
_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from shared.db.mongo import MongoManager  # noqa: E402

logger = logging.getLogger(__name__)

# Common MIMIC-IV item IDs (from shared constants)
from shared.constants.mimic import VITAL_ITEM_IDS, LAB_ITEM_IDS

# Projection for admission pool load
_ADMISSION_FIELDS = {
    "_id": 0,
    "subject_id": 1,
    "hadm_id": 1,
    "admittime": 1,
    "dischtime": 1,
    "admission_type": 1,
    "admission_location": 1,
    "discharge_location": 1,
    "insurance": 1,
    "race": 1,
    "hospital_expire_flag": 1,
    "edregtime": 1,
}


class PatientGenerator:
    """Fetches real MIMIC patients for replay in the simulation."""

    def __init__(self, mongo: MongoManager) -> None:
        self.mongo = mongo
        self._admission_pool: List[Dict[str, Any]] = []
        self._cursor_pos: int = 0

    # ------------------------------------------------------------------
    # Pool management
    # ------------------------------------------------------------------

    def initialize(self, limit: int = 500) -> int:
        """Pre-load a pool of admissions sorted by admittime.

        Returns the number of admissions loaded.
        """
        logger.info("Loading admission pool (limit=%d) ...", limit)
        self._admission_pool = list(
            self.mongo.mimic["admissions"]
            .find({}, _ADMISSION_FIELDS)
            .sort("admittime", 1)
            .limit(limit)
        )
        self._cursor_pos = 0
        logger.info("Loaded %d admissions into pool.", len(self._admission_pool))
        return len(self._admission_pool)

    @property
    def pool_size(self) -> int:
        return len(self._admission_pool)

    def next_patient(self) -> Optional[Dict[str, Any]]:
        """Return the next MIMIC patient admission to replay.

        Loops around to the beginning when the pool is exhausted.
        Returns ``None`` only if the pool is empty.
        """
        if not self._admission_pool:
            return None
        if self._cursor_pos >= len(self._admission_pool):
            self._cursor_pos = 0  # wrap around
        adm = self._admission_pool[self._cursor_pos]
        self._cursor_pos += 1
        return adm

    # ------------------------------------------------------------------
    # Full journey fetch
    # ------------------------------------------------------------------

    def fetch_full_journey(self, subject_id: int, hadm_id: int) -> Dict[str, Any]:
        """Fetch ALL clinical data for a patient admission.

        Returns a dict with keys: transfers, icu_stays, vitals, labs,
        medications, diagnoses, procedures, notes.
        """
        journey: Dict[str, Any] = {
            "transfers": [],
            "icu_stays": [],
            "vitals": [],
            "labs": [],
            "medications": [],
            "diagnoses": [],
            "procedures": [],
            "notes": [],
        }

        # --- Transfers ---
        journey["transfers"] = list(
            self.mongo.mimic["transfers"]
            .find({"hadm_id": hadm_id}, {"_id": 0})
            .sort("intime", 1)
        )

        # --- ICU stays ---
        journey["icu_stays"] = list(
            self.mongo.mimic_icu["icustays"]
            .find({"hadm_id": hadm_id}, {"_id": 0})
        )

        # --- Vitals (chartevents via stay_id) ---
        stay_ids = [s["stay_id"] for s in journey["icu_stays"] if "stay_id" in s]
        if stay_ids:
            journey["vitals"] = list(
                self.mongo.mimic_icu["chartevents"]
                .find(
                    {"stay_id": {"$in": stay_ids}, "itemid": {"$in": VITAL_ITEM_IDS}},
                    {"_id": 0, "stay_id": 1, "itemid": 1, "charttime": 1, "valuenum": 1},
                )
                .sort("charttime", 1)
                .limit(2000)
            )

        # --- Labs ---
        journey["labs"] = list(
            self.mongo.mimic["labevents"]
            .find(
                {"hadm_id": hadm_id, "itemid": {"$in": LAB_ITEM_IDS}},
                {"_id": 0, "hadm_id": 1, "itemid": 1, "charttime": 1, "valuenum": 1, "valueuom": 1},
            )
            .sort("charttime", 1)
            .limit(500)
        )

        # --- Medications ---
        journey["medications"] = list(
            self.mongo.mimic["prescriptions"]
            .find(
                {"hadm_id": hadm_id},
                {
                    "_id": 0, "hadm_id": 1, "drug": 1, "starttime": 1, "stoptime": 1,
                    "drug_type": 1, "dose_val_rx": 1, "dose_unit_rx": 1, "route": 1,
                },
            )
            .sort("starttime", 1)
            .limit(200)
        )

        # --- Diagnoses ---
        journey["diagnoses"] = list(
            self.mongo.mimic["diagnoses_icd"]
            .find({"hadm_id": hadm_id}, {"_id": 0})
            .sort("seq_num", 1)
        )

        # --- Procedures ---
        journey["procedures"] = list(
            self.mongo.mimic["procedures_icd"]
            .find({"hadm_id": hadm_id}, {"_id": 0})
            .sort("seq_num", 1)
        )

        # --- Notes (MIMIC-IV-Note) — clinical narrative ordered by charttime
        # so the simulator can stream each note at its real offset from
        # admission instead of dumping every note into clinical_scribe at
        # admission time. Keeps the patient timeline (vitals → notes →
        # discharge summary) honest.
        try:
            journey["notes"] = list(
                self.mongo.mimic_notes["discharge"]
                .find(
                    {"hadm_id": int(hadm_id) if str(hadm_id).isdigit() else hadm_id},
                    {
                        "_id": 0, "note_id": 1, "subject_id": 1, "hadm_id": 1,
                        "note_type": 1, "note_seq": 1, "charttime": 1,
                        "storetime": 1, "text": 1,
                    },
                )
                .sort([("charttime", 1), ("note_seq", 1)])
                .limit(20)
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("notes fetch failed for hadm=%s: %s", hadm_id, exc)
            journey["notes"] = []

        logger.debug(
            "Journey for hadm_id=%s: %d transfers, %d vitals, %d labs, %d meds, %d dx, %d px, %d notes",
            hadm_id,
            len(journey["transfers"]),
            len(journey["vitals"]),
            len(journey["labs"]),
            len(journey["medications"]),
            len(journey["diagnoses"]),
            len(journey["procedures"]),
            len(journey["notes"]),
        )
        return journey
