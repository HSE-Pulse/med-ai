"""Patient Journey API — FastAPI application.

Exposes patient timeline, vitals, labs, medications, journey path, and
derived metrics endpoints over the MIMIC-IV MongoDB backend.

Run:
    uvicorn app_05_patient_journey.backend.api.main:app --port 8205 --reload

Port: 8205
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path so that ``shared.*`` and
# ``app_05_patient_journey.*`` imports resolve correctly.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = str(Path(__file__).resolve().parents[4])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from shared.db.mongo import MongoManager
from shared.api.base import create_app, BaseResponse

from app_05_patient_journey.backend.engine.timeline import TimelineEngine
from app_05_patient_journey.backend.engine.vitals import VitalsEngine
from app_05_patient_journey.backend.engine.labs import LabsEngine
from app_05_patient_journey.backend.engine.medications import MedicationsEngine
from app_05_patient_journey.backend.engine.metrics import DerivedMetrics

from app_05_patient_journey.backend.api.schemas import (
    AdmissionSummary,
    JourneyPathResponse,
    LabsResponse,
    MedicationEntry,
    MedicationResponse,
    MetricsResponse,
    PatientSummaryResponse,
    TimelineEventSchema,
    TimelineResponse,
    VitalsResponse,
)

# ---------------------------------------------------------------------------
# Module-level engine references (populated during lifespan)
# ---------------------------------------------------------------------------
mongo: MongoManager
timeline_engine: TimelineEngine
vitals_engine: VitalsEngine
labs_engine: LabsEngine
medications_engine: MedicationsEngine
metrics_engine: DerivedMetrics


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI):
    """Initialise MongoManager and all engines on startup; close on shutdown."""
    global mongo, timeline_engine, vitals_engine, labs_engine
    global medications_engine, metrics_engine

    # Observability — JSON logging → Loki, OTel tracing, Prometheus /metrics
    import logging as _logging
    _log = _logging.getLogger("patient_journey.api")
    try:
        from shared.integration.logging_config import setup_logging
        setup_logging(service_name="patient_journey")
    except Exception as exc:  # noqa: BLE001
        _log.warning("logging_setup_failed: %s", exc)
    try:
        from shared.integration.tracing import setup_tracing
        setup_tracing(application, service_name="patient_journey")
    except Exception as exc:  # noqa: BLE001
        _log.warning("tracing_setup_failed: %s", exc)
    try:
        from shared.integration.prometheus_metrics import install_metrics
        install_metrics(application, service_name="patient_journey")
    except Exception as exc:  # noqa: BLE001
        _log.warning("prometheus_metrics_install_failed: %s", exc)

    # Anchor SimClock to data_ingestion so timestamps emitted by this
    # service mirror the simulator (and at speed=1× equal real wall-clock).
    try:
        from shared.integration.sim_clock import attach_remote_clock
        await attach_remote_clock()
    except Exception as exc:  # noqa: BLE001
        _log.warning("sim_clock_attach_remote_failed: %s", exc)

    mongo = MongoManager()
    timeline_engine = TimelineEngine(mongo)
    vitals_engine = VitalsEngine(mongo)
    labs_engine = LabsEngine(mongo)
    medications_engine = MedicationsEngine(mongo)
    metrics_engine = DerivedMetrics(mongo)

    # Subscribe to Kafka/broker events. The note_generated / note_approved
    # subscriptions are wired with real handlers (see _on_note_generated below)
    # so notes produced by Clinical Scribe show up in the patient timeline.
    try:
        from shared.integration.kafka_consumer import attach_with_ring_buffer
        from shared.integration.event_bus import get_event_bus

        async def _on_note_generated(topic, payload):
            """Persist a pointer to the new clinical note so the timeline can surface it."""
            try:
                note_id = payload.get("note_id")
                if not note_id:
                    return
                # Best-effort enrichment: pull the full note from scribe to get
                # hadm_id / patient_id / note_type / generated_at. The payload
                # has note_id and patient_id but not hadm_id.
                from shared.integration.service_client import ServiceClient
                client = ServiceClient()
                full = await client.clinical_scribe.get(f"/note/{note_id}")
                data = (full or {}).get("data") if isinstance(full, dict) else None
                if not data:
                    return
                doc = {
                    "note_id": note_id,
                    "note_type": data.get("note_type"),
                    "hadm_id": data.get("hadm_id"),
                    "subject_id": data.get("patient_id"),
                    "generated_at": data.get("generated_at"),
                    "status": data.get("status", "draft"),
                    "source": data.get("source", "synthetic"),
                    "original_charttime": data.get("original_charttime"),
                }
                col = mongo.client["patient_journey"]["notes"]
                col.replace_one({"note_id": note_id}, doc, upsert=True)
            except Exception as exc:  # noqa: BLE001
                import logging
                logging.getLogger("patient_journey.api").debug(
                    "pj_on_note_generated_err: %s", exc,
                )

        async def _on_note_approved(topic, payload):
            try:
                note_id = payload.get("note_id")
                if not note_id:
                    return
                col = mongo.client["patient_journey"]["notes"]
                col.update_one(
                    {"note_id": note_id},
                    {"$set": {"status": "approved", "approved_by": payload.get("approved_by")}},
                )
            except Exception:
                pass

        # Index for fast timeline lookup
        try:
            mongo.client["patient_journey"]["notes"].create_index([("subject_id", 1), ("hadm_id", 1)])
            mongo.client["patient_journey"]["notes"].create_index("note_id", unique=True)
        except Exception:
            pass

        await attach_with_ring_buffer(
            service_id="patient_journey",
            topics=["admission_complete", "patient_transferred", "patient_discharged", "note_generated", "note_approved"],
            mongo_client=mongo.client,
            extra_handlers={
                "note_generated": _on_note_generated,
                "note_approved": _on_note_approved,
            },
        )
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger("patient_journey.api").warning("pj_bus_subscribe_failed: %s", exc)

    yield

    mongo.close()


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = create_app(
    title="Patient Journey API",
    version="0.1.0",
    description="Timeline, vitals, labs, medications, and metrics for MIMIC-IV patient journeys.",
)
app.router.lifespan_context = lifespan


@app.get("/kafka-events", tags=["system"])
async def list_kafka_events(limit: int = 100):
    """Return recent cross-service events consumed via Kafka/broker."""
    from shared.integration.kafka_consumer import get_kafka_events
    return {"status": "ok", "data": get_kafka_events("patient_journey", limit)}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _ok(data: Any) -> Dict[str, Any]:
    """Wrap payload in the standard response envelope."""
    return {"status": "ok", "data": data}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# --- Patient summary -------------------------------------------------------

@app.get(
    "/patient/{subject_id}/summary",
    response_model=BaseResponse,
    tags=["patient"],
    summary="Patient demographics and admissions list",
)
async def patient_summary(subject_id: int):
    """Fetch patient demographics from MIMIC.patients and all admissions."""
    # Demographics (may be absent — patients collection is small)
    patient_doc = mongo.mimic["patients"].find_one(
        {"subject_id": subject_id}, {"_id": 0}
    )

    # All admissions for this patient
    adm_cursor = mongo.mimic["admissions"].find(
        {"subject_id": subject_id}, {"_id": 0}
    ).sort("admittime", 1)
    admissions_raw: List[Dict[str, Any]] = list(adm_cursor)

    if not admissions_raw and patient_doc is None:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "error": f"Patient {subject_id} not found"},
        )

    # Index simulator-side admissions by original_hadm_id so we can mark
    # which MIMIC admissions are currently being replayed live (status =
    # admitted in MIMIC_SIM) vs historical-only.
    sim_admissions: Dict[int, Dict[str, Any]] = {}
    try:
        for a in mongo.client["MIMIC_SIM"]["admissions"].find(
            {"subject_id": subject_id}, {"_id": 0}
        ):
            orig = a.get("original_hadm_id")
            if orig is not None:
                sim_admissions[int(orig)] = a
    except Exception as exc:  # noqa: BLE001
        _log.debug("sim_admissions_lookup_failed: %s", exc)

    admissions = []
    for a in admissions_raw:
        sim = sim_admissions.get(int(a["hadm_id"])) if a.get("hadm_id") is not None else None
        # Expired flag from the source MIMIC record (or sim copy if present)
        ef = a.get("hospital_expire_flag")
        if sim is not None and sim.get("hospital_expire_flag") is not None:
            ef = sim.get("hospital_expire_flag")
        is_expired = str(ef) in ("1", "True", "true")
        # Active flag — true when the simulator currently has this admission
        # in flight. Falls back to "false" for pure-historical records that
        # were never replayed.
        is_active = bool(sim and sim.get("status") == "admitted")
        if is_expired:
            state_label = "expired"
        elif is_active:
            state_label = "active"
        else:
            state_label = "discharged"
        row = AdmissionSummary(
            hadm_id=a["hadm_id"],
            admittime=str(a.get("admittime")) if a.get("admittime") else None,
            dischtime=str(a.get("dischtime")) if a.get("dischtime") else None,
            admission_type=a.get("admission_type"),
            admission_location=a.get("admission_location"),
            discharge_location=a.get("discharge_location"),
            insurance=a.get("insurance"),
            race=a.get("race"),
            hospital_expire_flag=ef,
            is_expired=is_expired,
            is_active=is_active,
            state_label=state_label,
            discharge_reason=(sim.get("discharge_reason") if sim is not None else None),
        )
        admissions.append(row)

    summary = PatientSummaryResponse(
        subject_id=subject_id,
        gender=patient_doc.get("gender") if patient_doc else None,
        anchor_age=patient_doc.get("anchor_age") if patient_doc else None,
        admissions=admissions,
    )
    return _ok(summary.model_dump())


# --- Timeline ---------------------------------------------------------------

@app.get(
    "/patient/{subject_id}/admission/{hadm_id}/timeline",
    response_model=BaseResponse,
    tags=["timeline"],
    summary="Unified clinical event timeline",
)
async def get_timeline(
    subject_id: int,
    hadm_id: int,
    event_types: Optional[str] = Query(
        None,
        description="Comma-separated event types (e.g. vital,lab,transfer). Omit for all.",
    ),
    limit: int = Query(500, ge=1, le=5000, description="Max events per sub-query"),
):
    types_list = [t.strip() for t in event_types.split(",")] if event_types else None
    events = timeline_engine.build_timeline(
        subject_id=subject_id,
        hadm_id=hadm_id,
        event_types=types_list,
        limit=limit,
    )

    # Merge in clinical notes recorded by Scribe (via the note_generated handler).
    # The handler stores them in patient_journey.notes keyed on (subject_id, hadm_id).
    if not types_list or "clinical_note" in types_list or "note" in types_list:
        try:
            note_query = {"subject_id": {"$in": [subject_id, str(subject_id)]},
                          "hadm_id": {"$in": [hadm_id, str(hadm_id)]}}
            for note in mongo.client["patient_journey"]["notes"].find(note_query):
                ts = note.get("original_charttime") or note.get("generated_at")
                if ts is None:
                    continue
                events.append({
                    "timestamp": str(ts),
                    "event_type": "clinical_note",
                    "category": "documentation",
                    "source_table": "scribe.notes",
                    "details": {
                        "note_id": note.get("note_id"),
                        "note_type": note.get("note_type"),
                        "status": note.get("status"),
                        "source": note.get("source"),
                        "approved_by": note.get("approved_by"),
                    },
                })
            # Re-sort by timestamp string (ISO sorts correctly)
            events.sort(key=lambda e: str(e.get("timestamp") or ""))
        except Exception:
            # Don't fail timeline because of a note-merge hiccup
            pass

    response = TimelineResponse(
        events=[TimelineEventSchema(**e) for e in events],
        total_count=len(events),
    )
    return _ok(response.model_dump())


# --- Vitals -----------------------------------------------------------------

@app.get(
    "/patient/{subject_id}/admission/{hadm_id}/vitals",
    response_model=BaseResponse,
    tags=["vitals"],
    summary="Resampled vital-sign time series",
)
async def get_vitals(
    subject_id: int,
    hadm_id: int,
    resample: str = Query("1h", description="Pandas resample frequency (e.g. 1h, 30min)"),
):
    series = vitals_engine.get_vitals_timeseries(
        subject_id=subject_id,
        hadm_id=hadm_id,
        resample=resample,
    )
    response = VitalsResponse(vitals=series)
    return _ok(response.model_dump())


# --- Labs -------------------------------------------------------------------

@app.get(
    "/patient/{subject_id}/admission/{hadm_id}/labs",
    response_model=BaseResponse,
    tags=["labs"],
    summary="Lab results grouped by clinical panel",
)
async def get_labs(
    subject_id: int,
    hadm_id: int,
    panels: Optional[str] = Query(
        None,
        description="Comma-separated panel names (e.g. CBC,BMP). Omit for all.",
    ),
):
    panel_list = [p.strip() for p in panels.split(",")] if panels else None
    lab_data = labs_engine.get_lab_trends(
        subject_id=subject_id,
        hadm_id=hadm_id,
        lab_groups=panel_list,
    )
    response = LabsResponse(panels=lab_data)
    return _ok(response.model_dump())


# --- Medications ------------------------------------------------------------

@app.get(
    "/patient/{subject_id}/admission/{hadm_id}/medications",
    response_model=BaseResponse,
    tags=["medications"],
    summary="Medication timeline with therapeutic categories",
)
async def get_medications(
    subject_id: int,
    hadm_id: int,
):
    meds = medications_engine.get_medication_timeline(
        subject_id=subject_id,
        hadm_id=hadm_id,
    )
    response = MedicationResponse(
        medications=[MedicationEntry(**m) for m in meds],
        total_count=len(meds),
    )
    return _ok(response.model_dump())


# --- Journey path (transfers + ICU + services) ------------------------------

@app.get(
    "/patient/{subject_id}/admission/{hadm_id}/journey",
    response_model=BaseResponse,
    tags=["journey"],
    summary="Hospital journey: transfers, ICU episodes, and services",
)
async def get_journey(
    subject_id: int,
    hadm_id: int,
):
    # Transfers
    transfers_raw = list(
        mongo.mimic["transfers"].find(
            {"subject_id": subject_id, "hadm_id": hadm_id},
            {"_id": 0},
        ).sort("intime", 1)
    )

    # ICU episodes
    icu_raw = list(
        mongo.mimic_icu["icustays"].find(
            {"subject_id": subject_id, "hadm_id": hadm_id},
            {"_id": 0},
        ).sort("intime", 1)
    )

    # Services
    services_raw = list(
        mongo.mimic["services"].find(
            {"subject_id": subject_id, "hadm_id": hadm_id},
            {"_id": 0},
        ).sort("transfertime", 1)
    )

    # Diagnoses (ICD) — join with d_icd_diagnoses for long_title
    dx_raw = list(
        mongo.mimic["diagnoses_icd"].find(
            {"subject_id": subject_id, "hadm_id": hadm_id},
            {"_id": 0},
        ).sort("seq_num", 1)
    )
    _enrich_icd_titles(dx_raw, "d_icd_diagnoses")

    # Procedures (ICD)
    px_raw = list(
        mongo.mimic["procedures_icd"].find(
            {"subject_id": subject_id, "hadm_id": hadm_id},
            {"_id": 0},
        ).sort("seq_num", 1)
    )
    _enrich_icd_titles(px_raw, "d_icd_procedures")

    # Admission header so downstream consumers (FHIR gateway, dashboard
    # journey page) have one-shot access to admit/discharge timestamps.
    admission = mongo.mimic["admissions"].find_one(
        {"subject_id": subject_id, "hadm_id": hadm_id},
        {"_id": 0},
    )

    response = JourneyPathResponse(
        transfers=transfers_raw,
        icu_episodes=icu_raw,
        services=services_raw,
        diagnoses=dx_raw,
        procedures=px_raw,
        admission=admission,
    )
    return _ok(response.model_dump())


def _enrich_icd_titles(rows: List[Dict[str, Any]], dict_coll: str) -> None:
    """Attach ``long_title`` to each diagnosis/procedure row via a single
    aggregated lookup against the MIMIC ICD dictionary collection. Mutates in
    place so FHIR / dashboard consumers see human-readable titles.
    """
    if not rows:
        return
    codes = {str(r.get("icd_code")) for r in rows if r.get("icd_code")}
    if not codes:
        return
    try:
        lookup = {
            d["icd_code"]: d.get("long_title")
            for d in mongo.mimic[dict_coll].find(
                {"icd_code": {"$in": list(codes)}},
                {"_id": 0, "icd_code": 1, "long_title": 1},
            )
        }
    except Exception:
        return
    for r in rows:
        title = lookup.get(str(r.get("icd_code", "")))
        if title:
            r["long_title"] = title


# --- Derived metrics --------------------------------------------------------

@app.get(
    "/patient/{subject_id}/admission/{hadm_id}/metrics",
    response_model=BaseResponse,
    tags=["metrics"],
    summary="Derived journey metrics (LOS, transfers, mortality, etc.)",
)
async def get_metrics(
    subject_id: int,
    hadm_id: int,
):
    metrics_data = metrics_engine.compute_journey_metrics(
        subject_id=subject_id,
        hadm_id=hadm_id,
    )
    if metrics_data.get("error"):
        return JSONResponse(
            status_code=404,
            content={"status": "error", "error": metrics_data["error"]},
        )
    response = MetricsResponse(**metrics_data)
    return _ok(response.model_dump())


# ---------------------------------------------------------------------------
# Digital Twin integration (Integrations 2 + 6, Rule 5 step 3)
# ---------------------------------------------------------------------------

_journey_finalized: Dict[str, Dict[str, Any]] = {}
_high_risk_flags: Dict[str, Dict[str, Any]] = {}


@app.post("/journey/finalize/{hadm_id}")
async def finalize_journey(hadm_id: str, data: dict):
    """Integration 2 — persist a completed-simulation trace for this admission.

    Called by the Digital Twin's ``process_discharge`` (Rule 5 step 3) with
    the full event log, module results, subject id, and discharge metadata.
    The payload is stored in ``patient_journey.completed_simulations`` so
    the dashboard can replay finished journeys.
    """
    from shared.integration.sim_clock import get_sim_time as _sim_now
    record = dict(data)
    record["hadm_id"] = hadm_id
    record["finalized_at"] = _sim_now().isoformat()
    _journey_finalized[hadm_id] = record
    try:
        mongo.client["patient_journey"]["completed_simulations"].update_one(
            {"hadm_id": hadm_id}, {"$set": record}, upsert=True,
        )
    except Exception as exc:
        return _ok({"stored_in_memory": True, "mongo_error": str(exc)})
    return _ok({"stored": True, "hadm_id": hadm_id})


@app.post("/flag-high-risk")
async def flag_high_risk(data: dict):
    """Integration 6 — tag an admission as oncology high-risk.

    Called by Oncology AI when ``readmission_30d_risk > 0.6``. Stored both
    in memory and in ``patient_journey.risk_flags`` so subsequent dashboard
    queries can highlight the case.
    """
    from shared.integration.sim_clock import get_sim_time as _sim_now
    hadm_id = str(data.get("hadm_id", "unknown"))
    flag = {
        "hadm_id": hadm_id,
        "subject_id": data.get("subject_id"),
        "flag": data.get("flag", "oncology_high_risk"),
        "readmission_risk": data.get("readmission_risk"),
        "flagged_at": _sim_now().isoformat(),
    }
    _high_risk_flags[hadm_id] = flag
    try:
        mongo.client["patient_journey"]["risk_flags"].update_one(
            {"hadm_id": hadm_id}, {"$set": flag}, upsert=True,
        )
    except Exception:
        pass
    return _ok({"flagged": True, "record": flag})


@app.get("/journey/high-risk")
async def list_high_risk():
    """List admissions currently tagged high-risk (dashboard helper)."""
    return _ok(list(_high_risk_flags.values()))


@app.post("/reset")
async def reset_journey_state():
    _journey_finalized.clear()
    _high_risk_flags.clear()
    return _ok({"reset": True})


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app_05_patient_journey.backend.api.main:app", host="0.0.0.0", port=8205, reload=True)
