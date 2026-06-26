"""MongoDB connection manager for MIMIC-IV clinical data."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database


class MongoManager:
    """Lazy connection manager for MIMIC MongoDB databases.

    Provides convenience helpers that map directly onto the MIMIC-IV schema
    stored across three Mongo databases: MIMIC, MIMIC_ICU, MIMIC_Clinical_Notes.
    """

    _DB_NAMES = ("MIMIC", "MIMIC_ICU", "MIMIC_Clinical_Notes")

    def __init__(self, uri: Optional[str] = None) -> None:
        self._uri = uri or os.getenv("MONGO_URI", "mongodb://localhost:27017/")
        self._client: Optional[MongoClient] = None
        self._databases: Dict[str, Database] = {}

    # -- connection lifecycle --------------------------------------------------

    @property
    def client(self) -> MongoClient:
        """Return a lazily-initialised MongoClient."""
        if self._client is None:
            self._client = MongoClient(self._uri)
        return self._client

    def _get_db(self, db_name: str) -> Database:
        if db_name not in self._databases:
            self._databases[db_name] = self.client[db_name]
        return self._databases[db_name]

    @property
    def mimic(self) -> Database:
        return self._get_db("MIMIC")

    @property
    def mimic_icu(self) -> Database:
        return self._get_db("MIMIC_ICU")

    @property
    def mimic_notes(self) -> Database:
        return self._get_db("MIMIC_Clinical_Notes")

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._databases.clear()

    # -- context manager -------------------------------------------------------

    def __enter__(self) -> "MongoManager":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    # -- generic access --------------------------------------------------------

    def get_collection(self, db_name: str, collection_name: str) -> Collection:
        """Return a collection handle for the given database and collection."""
        return self._get_db(db_name)[collection_name]

    # -- domain helpers --------------------------------------------------------

    def fetch_admissions(
        self,
        filters: Optional[Dict[str, Any]] = None,
        fields: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Query MIMIC.admissions with optional filter and projection."""
        filters = filters or {}
        fields = fields or {}
        return list(self.mimic["admissions"].find(filters, fields))

    def fetch_icu_stays(
        self,
        filters: Optional[Dict[str, Any]] = None,
        fields: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Query MIMIC_ICU.icustays with optional filter and projection."""
        filters = filters or {}
        fields = fields or {}
        return list(self.mimic_icu["icustays"].find(filters, fields))

    def fetch_vitals(
        self,
        stay_ids: Sequence[int],
        itemids: Optional[Sequence[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch chartevents rows for the given ICU stay IDs.

        Parameters
        ----------
        stay_ids:
            List of ``stay_id`` values to filter on.
        itemids:
            Optional list of ``itemid`` values (e.g. HR, RR, SpO2). If *None*
            all items for those stays are returned.
        """
        query: Dict[str, Any] = {"stay_id": {"$in": list(stay_ids)}}
        if itemids:
            query["itemid"] = {"$in": list(itemids)}
        projection = {"_id": 0, "stay_id": 1, "itemid": 1, "charttime": 1, "valuenum": 1}
        return list(self.mimic_icu["chartevents"].find(query, projection))

    def fetch_labs(
        self,
        hadm_ids: Sequence[int],
        itemids: Optional[Sequence[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch labevents rows for the given hospital admission IDs.

        Parameters
        ----------
        hadm_ids:
            List of ``hadm_id`` values.
        itemids:
            Optional lab item IDs to restrict results to.
        """
        query: Dict[str, Any] = {"hadm_id": {"$in": list(hadm_ids)}}
        if itemids:
            query["itemid"] = {"$in": list(itemids)}
        projection = {"_id": 0, "hadm_id": 1, "itemid": 1, "charttime": 1, "valuenum": 1}
        return list(self.mimic["labevents"].find(query, projection))

    def fetch_transfers(
        self,
        hadm_ids: Sequence[int],
    ) -> List[Dict[str, Any]]:
        """Fetch transfer records for the given hospital admission IDs."""
        return list(
            self.mimic["transfers"].find({"hadm_id": {"$in": list(hadm_ids)}})
        )

    # ------------------------------------------------------------------
    # MIMIC-IV-Note discharge summaries
    #
    # The full MIMIC-IV-Note "discharge" collection lives in the separate
    # MIMIC_Clinical_Notes database (~331k documents, all note_type="DS").
    # Schema: {note_id, subject_id, hadm_id, note_type, note_seq,
    #          charttime, storetime, text}.
    # ------------------------------------------------------------------

    def notes_for_admission(
        self,
        hadm_id: Any,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return MIMIC-IV-Note rows for a given hadm_id, oldest first.

        Designed for the digital-twin admission cascade so it can replay
        a patient's real clinical narrative through the scribe pipeline.
        """
        try:
            hadm_int = int(hadm_id)
        except (TypeError, ValueError):
            return []
        cursor = self.mimic_notes["discharge"].find(
            {"hadm_id": hadm_int},
            {"_id": 0},
            sort=[("charttime", 1), ("note_seq", 1)],
            limit=limit,
        )
        return list(cursor)

    def notes_for_patient(
        self,
        subject_id: Any,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return MIMIC-IV-Note rows for a subject across all admissions."""
        try:
            sid = int(subject_id)
        except (TypeError, ValueError):
            return []
        cursor = self.mimic_notes["discharge"].find(
            {"subject_id": sid},
            {"_id": 0},
            sort=[("charttime", 1), ("note_seq", 1)],
            limit=limit,
        )
        return list(cursor)
