"""EHR writer that manages the MIMIC_SIM MongoDB database.

Creates indexes for efficient querying and provides utilities for
resetting the simulation state and gathering collection statistics.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from shared.db.mongo import MongoManager  # noqa: E402

logger = logging.getLogger(__name__)

# All collections used by the simulation
SIM_COLLECTIONS: List[str] = [
    "admissions",
    "transfers",
    "chartevents",
    "labevents",
    "prescriptions",
    "diagnoses_icd",
    "procedures_icd",
    "notes",  # MIMIC-IV-Note streams persisted via _fire_event("note", ...)
]


class EHRWriter:
    """Manages the MIMIC_SIM MongoDB database for simulated data."""

    def __init__(self, mongo: MongoManager) -> None:
        self.sim_db = mongo.client["MIMIC_SIM"]

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def setup_collections(self) -> None:
        """Create indexes on simulation collections for efficient queries."""
        logger.info("Setting up MIMIC_SIM indexes ...")

        self.sim_db["admissions"].create_index("hadm_id", unique=True)
        self.sim_db["admissions"].create_index("subject_id")
        self.sim_db["admissions"].create_index("status")
        self.sim_db["admissions"].create_index("sim_admittime")

        self.sim_db["transfers"].create_index([("hadm_id", 1), ("intime", 1)])
        self.sim_db["transfers"].create_index("careunit")

        self.sim_db["chartevents"].create_index([("hadm_id", 1), ("itemid", 1)])
        self.sim_db["chartevents"].create_index("sim_time")

        self.sim_db["labevents"].create_index([("hadm_id", 1), ("itemid", 1)])
        self.sim_db["labevents"].create_index("sim_time")

        self.sim_db["prescriptions"].create_index("hadm_id")
        self.sim_db["prescriptions"].create_index("sim_time")

        self.sim_db["diagnoses_icd"].create_index("hadm_id")

        self.sim_db["procedures_icd"].create_index("hadm_id")

        logger.info("MIMIC_SIM indexes ready.")

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Drop all sim collections and re-create indexes."""
        logger.warning("Resetting MIMIC_SIM database -- dropping all collections.")
        for name in SIM_COLLECTIONS:
            self.sim_db[name].drop()
        self.setup_collections()

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, int]:
        """Return estimated document counts per simulation collection."""
        return {
            name: self.sim_db[name].estimated_document_count()
            for name in SIM_COLLECTIONS
        }

    def get_recent_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return the most recent events across sim collections (by sim_time)."""
        events: List[Dict[str, Any]] = []

        for coll_name in ["transfers", "chartevents", "labevents", "prescriptions"]:
            docs = list(
                self.sim_db[coll_name]
                .find({}, {"_id": 0})
                .sort("sim_time", -1)
                .limit(limit)
            )
            for d in docs:
                d["_collection"] = coll_name
            events.extend(docs)

        # Sort all combined events by sim_time descending, take top N
        events.sort(key=lambda e: e.get("sim_time", ""), reverse=True)
        return events[:limit]
