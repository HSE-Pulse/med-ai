"""Core timeline builder for the Patient Journey application.

Assembles a unified, chronologically-sorted stream of clinical events from
multiple MIMIC-IV source tables into a single timeline for a given patient.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Set

from shared.db.mongo import MongoManager

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

from shared.constants.mimic import (
    VITAL_ITEMIDS, LAB_ITEMIDS,
    VITAL_ITEMID_TO_LABEL as _ITEMID_TO_VITAL,
    LAB_ITEMID_TO_LABEL as _ITEMID_TO_LAB,
)

ALL_EVENT_TYPES: Set[str] = {
    "admission",
    "discharge",
    "transfer",
    "icu_in",
    "icu_out",
    "vital",
    "lab",
    "medication_start",
    "medication_stop",
    "procedure",
    "diagnosis",
}

from shared.utils.datetime import parse_time as _shared_parse_time

# Two known MIMIC date formats (kept for backward compat with _fmt)
_FMT_ISO = "%Y-%m-%d %H:%M:%S"
_FMT_DDMM = "%d-%m-%Y %H:%M"


# ---------------------------------------------------------------------------
# TimelineEngine
# ---------------------------------------------------------------------------


class TimelineEngine:
    """Builds a unified clinical timeline for a single patient."""

    def __init__(self, mongo: MongoManager) -> None:
        self.mongo = mongo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_timeline(
        self,
        subject_id: int,
        hadm_id: Optional[int] = None,
        event_types: Optional[Sequence[str]] = None,
        time_range: Optional[Dict[str, str]] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Return a chronologically-sorted list of clinical events.

        Parameters
        ----------
        subject_id:
            Patient identifier.
        hadm_id:
            Optional hospital admission to restrict to.
        event_types:
            Subset of event types to include.  ``None`` means all.
        time_range:
            Optional ``{"start": "<iso>", "end": "<iso>"}`` to filter.
        limit:
            Maximum number of events per sub-query (vitals/labs/meds).
        """
        wanted = set(event_types) if event_types else ALL_EVENT_TYPES
        events: List[Dict[str, Any]] = []

        # Movement events
        if wanted & {"admission", "discharge"}:
            events.extend(self._fetch_admissions(subject_id, hadm_id))
        if "transfer" in wanted:
            events.extend(self._fetch_transfers(subject_id, hadm_id))
        if wanted & {"icu_in", "icu_out"}:
            events.extend(self._fetch_icu_stays(subject_id, hadm_id))

        # Clinical events
        if "vital" in wanted:
            events.extend(self._fetch_vitals(subject_id, hadm_id, limit))
        if "lab" in wanted:
            events.extend(self._fetch_labs(subject_id, hadm_id, limit))
        if wanted & {"medication_start", "medication_stop"}:
            events.extend(self._fetch_medications(subject_id, hadm_id, limit))
        if "procedure" in wanted:
            events.extend(self._fetch_procedures(subject_id, hadm_id))
        if "diagnosis" in wanted:
            events.extend(self._fetch_diagnoses(subject_id, hadm_id))

        # Optional time-range filter
        if time_range:
            start = self._parse_time(time_range.get("start")) if time_range.get("start") else None
            end = self._parse_time(time_range.get("end")) if time_range.get("end") else None
            filtered: List[Dict[str, Any]] = []
            for ev in events:
                ts = self._parse_time(ev["timestamp"])
                if ts is None:
                    continue
                if start and ts < start:
                    continue
                if end and ts > end:
                    continue
                filtered.append(ev)
            events = filtered

        # Sort ascending by timestamp
        events.sort(key=lambda e: e.get("timestamp") or "")

        return events

    # ------------------------------------------------------------------
    # Admission / discharge
    # ------------------------------------------------------------------

    def _fetch_admissions(
        self, subject_id: int, hadm_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        filt: Dict[str, Any] = {"subject_id": subject_id}
        if hadm_id is not None:
            filt["hadm_id"] = hadm_id
        rows = list(
            self.mongo.mimic["admissions"].find(filt, {"_id": 0})
        )
        events: List[Dict[str, Any]] = []
        for r in rows:
            # Admission event
            admit_ts = self._parse_time(r.get("admittime"))
            if admit_ts:
                events.append(
                    self._normalize_event(
                        admit_ts,
                        "admission",
                        "admissions",
                        {
                            "hadm_id": r.get("hadm_id"),
                            "admission_type": r.get("admission_type"),
                            "admission_location": r.get("admission_location"),
                            "insurance": r.get("insurance"),
                            "race": r.get("race"),
                            "edregtime": self._fmt(r.get("edregtime")),
                            "edouttime": self._fmt(r.get("edouttime")),
                        },
                    )
                )
            # Discharge event
            disch_ts = self._parse_time(r.get("dischtime"))
            if disch_ts:
                events.append(
                    self._normalize_event(
                        disch_ts,
                        "discharge",
                        "admissions",
                        {
                            "hadm_id": r.get("hadm_id"),
                            "discharge_location": r.get("discharge_location"),
                            "hospital_expire_flag": r.get("hospital_expire_flag"),
                        },
                    )
                )
        return events

    # ------------------------------------------------------------------
    # Transfers
    # ------------------------------------------------------------------

    def _fetch_transfers(
        self, subject_id: int, hadm_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        filt: Dict[str, Any] = {"subject_id": subject_id}
        if hadm_id is not None:
            filt["hadm_id"] = hadm_id
        rows = list(
            self.mongo.mimic["transfers"].find(filt, {"_id": 0})
        )
        events: List[Dict[str, Any]] = []
        for r in rows:
            ts = self._parse_time(r.get("intime"))
            if ts is None:
                continue
            events.append(
                self._normalize_event(
                    ts,
                    "transfer",
                    "transfers",
                    {
                        "hadm_id": r.get("hadm_id"),
                        "transfer_id": r.get("transfer_id"),
                        "eventtype": r.get("eventtype"),
                        "careunit": r.get("careunit"),
                        "intime": self._fmt(r.get("intime")),
                        "outtime": self._fmt(r.get("outtime")),
                    },
                )
            )
        return events

    # ------------------------------------------------------------------
    # ICU stays
    # ------------------------------------------------------------------

    def _fetch_icu_stays(
        self, subject_id: int, hadm_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        filt: Dict[str, Any] = {"subject_id": subject_id}
        if hadm_id is not None:
            filt["hadm_id"] = hadm_id
        rows = list(
            self.mongo.mimic_icu["icustays"].find(filt, {"_id": 0})
        )
        events: List[Dict[str, Any]] = []
        for r in rows:
            in_ts = self._parse_time(r.get("intime"))
            if in_ts:
                events.append(
                    self._normalize_event(
                        in_ts,
                        "icu_in",
                        "icustays",
                        {
                            "hadm_id": r.get("hadm_id"),
                            "stay_id": r.get("stay_id"),
                            "first_careunit": r.get("first_careunit"),
                            "los": r.get("los"),
                        },
                    )
                )
            out_ts = self._parse_time(r.get("outtime"))
            if out_ts:
                events.append(
                    self._normalize_event(
                        out_ts,
                        "icu_out",
                        "icustays",
                        {
                            "hadm_id": r.get("hadm_id"),
                            "stay_id": r.get("stay_id"),
                            "last_careunit": r.get("last_careunit"),
                            "los": r.get("los"),
                        },
                    )
                )
        return events

    # ------------------------------------------------------------------
    # Vitals  (chartevents via stay_id — never by subject_id)
    # ------------------------------------------------------------------

    def _fetch_vitals(
        self, subject_id: int, hadm_id: Optional[int] = None, limit: int = 500
    ) -> List[Dict[str, Any]]:
        # Step 1: get stay_ids from icustays
        icu_filt: Dict[str, Any] = {"subject_id": subject_id}
        if hadm_id is not None:
            icu_filt["hadm_id"] = hadm_id
        icu_rows = list(
            self.mongo.mimic_icu["icustays"].find(icu_filt, {"_id": 0, "stay_id": 1})
        )
        stay_ids = [r["stay_id"] for r in icu_rows if r.get("stay_id") is not None]
        if not stay_ids:
            return []

        # Step 2: query chartevents by stay_id + vital itemids
        vital_ids = list(VITAL_ITEMIDS.values())
        cursor = (
            self.mongo.mimic_icu["chartevents"]
            .find(
                {"stay_id": {"$in": stay_ids}, "itemid": {"$in": vital_ids}},
                {"_id": 0, "stay_id": 1, "itemid": 1, "charttime": 1, "valuenum": 1, "valueuom": 1},
            )
            .sort("charttime", 1)
            .limit(limit)
        )
        events: List[Dict[str, Any]] = []
        for r in cursor:
            ts = self._parse_time(r.get("charttime"))
            if ts is None or r.get("valuenum") is None:
                continue
            vital_name = _ITEMID_TO_VITAL.get(r["itemid"], str(r["itemid"]))
            events.append(
                self._normalize_event(
                    ts,
                    "vital",
                    "chartevents",
                    {
                        "stay_id": r.get("stay_id"),
                        "vital_name": vital_name,
                        "value": r["valuenum"],
                        "unit": r.get("valueuom", ""),
                        "itemid": r["itemid"],
                    },
                )
            )
        return events

    # ------------------------------------------------------------------
    # Labs
    # ------------------------------------------------------------------

    def _fetch_labs(
        self, subject_id: int, hadm_id: Optional[int] = None, limit: int = 500
    ) -> List[Dict[str, Any]]:
        # Determine hadm_ids to query
        if hadm_id is not None:
            hadm_ids = [hadm_id]
        else:
            adm_rows = list(
                self.mongo.mimic["admissions"].find(
                    {"subject_id": subject_id}, {"_id": 0, "hadm_id": 1}
                )
            )
            hadm_ids = [r["hadm_id"] for r in adm_rows if r.get("hadm_id") is not None]
        if not hadm_ids:
            return []

        lab_ids = list(LAB_ITEMIDS.values())
        cursor = (
            self.mongo.mimic["labevents"]
            .find(
                {"hadm_id": {"$in": hadm_ids}, "itemid": {"$in": lab_ids}},
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
            .limit(limit)
        )
        events: List[Dict[str, Any]] = []
        for r in cursor:
            ts = self._parse_time(r.get("charttime"))
            if ts is None:
                continue
            lab_name = _ITEMID_TO_LAB.get(r["itemid"], str(r["itemid"]))
            events.append(
                self._normalize_event(
                    ts,
                    "lab",
                    "labevents",
                    {
                        "hadm_id": r.get("hadm_id"),
                        "lab_name": lab_name,
                        "value": r.get("valuenum"),
                        "unit": r.get("valueuom", ""),
                        "ref_range_lower": r.get("ref_range_lower"),
                        "ref_range_upper": r.get("ref_range_upper"),
                        "flag": r.get("flag"),
                        "itemid": r["itemid"],
                    },
                )
            )
        return events

    # ------------------------------------------------------------------
    # Medications
    # ------------------------------------------------------------------

    def _fetch_medications(
        self, subject_id: int, hadm_id: Optional[int] = None, limit: int = 500
    ) -> List[Dict[str, Any]]:
        filt: Dict[str, Any] = {"subject_id": subject_id}
        if hadm_id is not None:
            filt["hadm_id"] = hadm_id
        cursor = (
            self.mongo.mimic["prescriptions"]
            .find(filt, {"_id": 0})
            .sort("starttime", 1)
            .limit(limit)
        )
        events: List[Dict[str, Any]] = []
        for r in cursor:
            start_ts = self._parse_time(r.get("starttime"))
            if start_ts:
                events.append(
                    self._normalize_event(
                        start_ts,
                        "medication_start",
                        "prescriptions",
                        {
                            "hadm_id": r.get("hadm_id"),
                            "drug": r.get("drug"),
                            "drug_type": r.get("drug_type"),
                            "prod_strength": r.get("prod_strength"),
                            "dose_val_rx": r.get("dose_val_rx"),
                            "dose_unit_rx": r.get("dose_unit_rx"),
                            "route": r.get("route"),
                        },
                    )
                )
            stop_ts = self._parse_time(r.get("stoptime"))
            if stop_ts:
                events.append(
                    self._normalize_event(
                        stop_ts,
                        "medication_stop",
                        "prescriptions",
                        {
                            "hadm_id": r.get("hadm_id"),
                            "drug": r.get("drug"),
                        },
                    )
                )
        return events

    # ------------------------------------------------------------------
    # Procedures
    # ------------------------------------------------------------------

    def _fetch_procedures(
        self, subject_id: int, hadm_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        from shared.clinical.icd_names import get_icd_resolver
        filt: Dict[str, Any] = {"subject_id": subject_id}
        if hadm_id is not None:
            filt["hadm_id"] = hadm_id
        rows = list(
            self.mongo.mimic["procedures_icd"].find(filt, {"_id": 0})
        )
        enriched = get_icd_resolver(self.mongo.client).resolve_procedures(rows)
        events: List[Dict[str, Any]] = []
        for r in enriched:
            ts = self._parse_time(r.get("chartdate"))
            if ts is None:
                # Fall back: use admission time if chartdate missing
                continue
            events.append(
                self._normalize_event(
                    ts,
                    "procedure",
                    "procedures_icd",
                    {
                        "hadm_id": r.get("hadm_id"),
                        "icd_code": r.get("icd_code"),
                        "icd_version": r.get("icd_version"),
                        "seq_num": r.get("seq_num"),
                        "long_title": r.get("long_title"),
                    },
                )
            )
        return events

    # ------------------------------------------------------------------
    # Diagnoses
    # ------------------------------------------------------------------

    def _fetch_diagnoses(
        self, subject_id: int, hadm_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        from shared.clinical.icd_names import get_icd_resolver
        filt: Dict[str, Any] = {"subject_id": subject_id}
        if hadm_id is not None:
            filt["hadm_id"] = hadm_id
        rows = list(
            self.mongo.mimic["diagnoses_icd"].find(filt, {"_id": 0})
        )
        enriched = get_icd_resolver(self.mongo.client).resolve_diagnoses(rows)
        # Diagnoses don't have their own timestamp; we use the admission time.
        # Build a hadm_id -> admittime map.
        hadm_ids_needed = list({r["hadm_id"] for r in rows if r.get("hadm_id")})
        admit_map: Dict[int, Optional[datetime]] = {}
        if hadm_ids_needed:
            adm_rows = list(
                self.mongo.mimic["admissions"].find(
                    {"hadm_id": {"$in": hadm_ids_needed}},
                    {"_id": 0, "hadm_id": 1, "admittime": 1},
                )
            )
            for a in adm_rows:
                admit_map[a["hadm_id"]] = self._parse_time(a.get("admittime"))

        events: List[Dict[str, Any]] = []
        for r in enriched:
            ts = admit_map.get(r.get("hadm_id"))  # type: ignore[arg-type]
            if ts is None:
                continue
            events.append(
                self._normalize_event(
                    ts,
                    "diagnosis",
                    "diagnoses_icd",
                    {
                        "hadm_id": r.get("hadm_id"),
                        "icd_code": r.get("icd_code"),
                        "icd_version": r.get("icd_version"),
                        "seq_num": r.get("seq_num"),
                        "long_title": r.get("long_title"),
                    },
                )
            )
        return events

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_time(val: Any) -> Optional[datetime]:
        """Robustly parse MIMIC datetime values (delegates to shared utility)."""
        return _shared_parse_time(val)

    @staticmethod
    def _fmt(val: Any) -> Optional[str]:
        """Format a raw MIMIC time value to ISO string, or None."""
        ts = TimelineEngine._parse_time(val)
        if ts is None:
            return None
        return ts.isoformat()

    @staticmethod
    def _normalize_event(
        timestamp: datetime,
        event_type: str,
        source: str,
        details: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create a standardised event dictionary."""
        category_map = {
            "admission": "movement",
            "discharge": "movement",
            "transfer": "movement",
            "icu_in": "movement",
            "icu_out": "movement",
            "vital": "vital_sign",
            "lab": "laboratory",
            "medication_start": "medication",
            "medication_stop": "medication",
            "procedure": "clinical",
            "diagnosis": "clinical",
        }
        return {
            "timestamp": timestamp.isoformat(),
            "event_type": event_type,
            "source_table": source,
            "category": category_map.get(event_type, "other"),
            "details": details,
        }
