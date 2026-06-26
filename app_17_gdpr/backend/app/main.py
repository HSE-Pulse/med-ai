"""GDPR Compliance Engine — port 8217.

Central append-only audit trail and subject-rights controller for the
platform. Implements the endpoints required by:

  - GDPR Art. 15 (Subject Access Request) — ``/gdpr/sar/{patient_id}``
  - GDPR Art. 17 (Right to Erasure)       — ``/gdpr/purge/{patient_id}``
  - GDPR Art. 30 (RoPA)                   — ``/gdpr/ropa``
  - GDPR Art. 33 (Breach notification)    — ``/gdpr/breach``
  - GDPR Art. 35 (DPIA)                   — ``/gdpr/dpia/{module}``
  - Internal access log used by ``@gdpr_audit_log`` decorator.
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fastapi import FastAPI, Query

from shared.api.base import BaseResponse, PrivacyNotice, create_app
from shared.db.mongo import MongoManager
from shared.integration.service_client import SERVICE_REGISTRY, ServiceClient
from shared.integration.sim_clock import get_sim_time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gdpr_engine")


PRIVACY = PrivacyNotice(
    data_collected=[
        "access_events", "subject_access_requests", "erasure_tombstones",
        "dpia_records", "breach_incidents",
    ],
    legal_basis="Legal obligation (GDPR Arts. 5, 15, 17, 30, 33, 35)",
    retention_period="7 years (DPC Ireland audit retention)",
)


_DPIA_SEEDS: List[Dict[str, Any]] = [
    {
        "module": "ed_triage",
        "purpose": "Automated acuity scoring (MTS categories 1-5)",
        "lawful_basis": "GDPR Art. 9(2)(h) — provision of health care",
        "necessity_assessment": "Replaces manual triage to reduce wait-to-be-seen variability.",
        "risk_assessment": "Mis-triage could delay immediate care; SHAP explanations mitigate.",
        "mitigating_measures": [
            "clinician override required within 30s", "explainability endpoint",
            "drift monitor triggers re-train at KL>0.1",
        ],
        "risk_level": "high",
    },
    {
        "module": "sepsis_icu",
        "purpose": "Automated sepsis / SOFA alerting from vitals and labs",
        "lawful_basis": "GDPR Art. 9(2)(h)",
        "necessity_assessment": "Sepsis mortality falls ~8% per hour earlier escalation.",
        "risk_assessment": "False positives cause alarm fatigue; false negatives delay.",
        "mitigating_measures": ["clinician confirmation", "continuous audit of override rates"],
        "risk_level": "high",
    },
    {
        "module": "oncology_ai",
        "purpose": "30-day readmission and mortality risk scoring",
        "lawful_basis": "GDPR Art. 9(2)(h)",
        "necessity_assessment": "Supports Sláintecare community-referral pathway planning.",
        "risk_assessment": "Stigmatising risk labels without clinician context.",
        "mitigating_measures": ["human review before flagging", "risk labels not shown to patient"],
        "risk_level": "high",
    },
    {
        "module": "hospital_ops",
        "purpose": "MARL staffing recommendations affecting NCHD/nursing allocation",
        "lawful_basis": "GDPR Art. 6(1)(e) (public task)",
        "necessity_assessment": "Improves response to surges and EWTD compliance.",
        "risk_assessment": "Over-allocation could breach EWTD; under-allocation harms care.",
        "mitigating_measures": ["human oversight queue", "EWTD pre-action guard"],
        "risk_level": "high",
    },
]


_state: Dict[str, Any] = {
    "mongo": None,
    "in_memory_audit": [],
    "dpia": {},
    "sar_requests": {},
    "breaches": [],
}


def _coll(name: str):
    mongo = _state.get("mongo")
    if mongo is None:
        return None
    try:
        return mongo.client["gdpr_audit"][name]
    except Exception:
        return None


def _seed_dpia() -> None:
    _state["dpia"] = {entry["module"]: entry for entry in _DPIA_SEEDS}
    coll = _coll("dpia")
    if coll is None:
        return
    try:
        for entry in _DPIA_SEEDS:
            coll.update_one({"module": entry["module"]}, {"$set": entry}, upsert=True)
    except Exception as exc:
        logger.warning("dpia_seed_failed: %s", exc)


@asynccontextmanager
async def lifespan(application: FastAPI):
    # Observability
    try:
        from shared.integration.logging_config import setup_logging
        setup_logging(service_name="gdpr")
    except Exception as exc:  # noqa: BLE001
        logger.warning("logging_setup_failed: %s", exc)
    try:
        from shared.integration.tracing import setup_tracing
        setup_tracing(application, service_name="gdpr")
    except Exception as exc:  # noqa: BLE001
        logger.warning("tracing_setup_failed: %s", exc)
    try:
        from shared.integration.prometheus_metrics import install_metrics
        install_metrics(application, service_name="gdpr")
    except Exception as exc:  # noqa: BLE001
        logger.warning("prometheus_metrics_install_failed: %s", exc)

    # Anchor SimClock to data_ingestion's authoritative clock.
    try:
        from shared.integration.sim_clock import attach_remote_clock
        await attach_remote_clock()
    except Exception as exc:  # noqa: BLE001
        logger.warning("sim_clock_attach_remote_failed: %s", exc)

    try:
        _state["mongo"] = MongoManager()
    except Exception as exc:
        logger.warning("gdpr_mongo_init_failed: %s", exc)
    _seed_dpia()

    # GDPR listens to ALL patient-related topics for audit trail. Every
    # event is logged to gdpr.event_audit (mirror of event_log but scoped).
    try:
        if _state.get("mongo"):
            from shared.integration.kafka_consumer import attach_with_ring_buffer
            await attach_with_ring_buffer(
                service_id="gdpr",
                topics=[
                    "admission_complete", "patient_transferred", "patient_discharged",
                    "note_generated", "priority_updated", "deterioration_alert",
                    "bed_allocated", "bed_released",
                ],
                mongo_client=_state["mongo"].client,
            )
    except Exception as exc:
        logger.warning("gdpr_bus_subscribe_failed: %s", exc)

    yield


app = create_app(
    title="GDPR Compliance Engine",
    version="1.0.0",
    description="Central audit, DPIA, SAR, erasure, and RoPA controller.",
    privacy_notice=PRIVACY,
)
app.router.lifespan_context = lifespan


@app.get("/kafka-events", response_model=BaseResponse, tags=["system"])
async def list_kafka_events(limit: int = 100) -> BaseResponse:
    """Return recent cross-service events consumed via Kafka/broker."""
    from shared.integration.kafka_consumer import get_kafka_events
    return BaseResponse(data=get_kafka_events("gdpr", limit))


# ---------------------------------------------------------------------------
# Access log (called by @gdpr_audit_log decorator)
# ---------------------------------------------------------------------------
@app.post("/gdpr/access-log", response_model=BaseResponse, tags=["gdpr"])
async def access_log(entry: dict) -> BaseResponse:
    """Append-only sink for data-access events."""
    doc = dict(entry)
    doc.setdefault("timestamp", get_sim_time().isoformat())
    doc.setdefault("event_id", str(uuid.uuid4()))
    _state["in_memory_audit"].append(doc)
    if len(_state["in_memory_audit"]) > 50000:
        _state["in_memory_audit"] = _state["in_memory_audit"][-25000:]
    coll = _coll("access_log")
    if coll is not None:
        try:
            coll.insert_one(dict(doc))
        except Exception as exc:
            logger.warning("gdpr_access_log_persist_failed: %s", exc)
    return BaseResponse(data={"logged": True, "event_id": doc["event_id"]})


@app.get("/gdpr/audit-log", response_model=BaseResponse, tags=["gdpr"])
async def audit_log(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    module: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=5000),
) -> BaseResponse:
    coll = _coll("access_log")
    docs: List[Dict[str, Any]] = []
    if coll is not None:
        query: Dict[str, Any] = {}
        if module:
            query["module"] = module
        if from_:
            query["timestamp"] = {"$gte": from_}
        if to:
            query.setdefault("timestamp", {})["$lte"] = to
        try:
            docs = list(coll.find(query, {"_id": 0}).sort("timestamp", -1).limit(limit))
        except Exception:
            docs = []
    if not docs:
        docs = [d for d in _state["in_memory_audit"] if module is None or d.get("module") == module][-limit:]
    return BaseResponse(data=docs)


# ---------------------------------------------------------------------------
# RoPA / DPIA / breach
# ---------------------------------------------------------------------------
@app.get("/gdpr/ropa", response_model=BaseResponse, tags=["gdpr"])
async def get_ropa() -> BaseResponse:
    """Record of Processing Activities — aggregated across registered modules."""
    activities = []
    # Static entries complemented with live privacy-notice lookups.
    client = ServiceClient()
    for svc, base_url in SERVICE_REGISTRY.items():
        try:
            res = await client._get_client(svc).get("/privacy-notice")
            activities.append({"service": svc, "base_url": base_url, "privacy_notice": res.get("data")})
        except Exception:
            activities.append({"service": svc, "base_url": base_url, "privacy_notice": None})
    return BaseResponse(data=activities)


@app.post("/gdpr/dpia", response_model=BaseResponse, tags=["gdpr"])
async def upsert_dpia(entry: dict) -> BaseResponse:
    module = entry.get("module")
    if not module:
        return BaseResponse(status="error", error="module is required")
    _state["dpia"][module] = entry
    coll = _coll("dpia")
    if coll is not None:
        try:
            coll.update_one({"module": module}, {"$set": entry}, upsert=True)
        except Exception:
            pass
    return BaseResponse(data=entry)


@app.get("/gdpr/dpia/{module}", response_model=BaseResponse, tags=["gdpr"])
async def get_dpia(module: str) -> BaseResponse:
    return BaseResponse(data=_state["dpia"].get(module))


@app.post("/gdpr/breach", response_model=BaseResponse, tags=["gdpr"])
async def log_breach(entry: dict) -> BaseResponse:
    doc = dict(entry)
    doc.setdefault("reported_at", get_sim_time().isoformat())
    _state["breaches"].append(doc)
    coll = _coll("breaches")
    if coll is not None:
        try:
            coll.insert_one(dict(doc))
        except Exception:
            pass
    return BaseResponse(data=doc)


# ---------------------------------------------------------------------------
# Subject Access Request (Art. 15)
# ---------------------------------------------------------------------------
@app.post("/gdpr/sar/{patient_id}", response_model=BaseResponse, tags=["gdpr"])
async def create_sar(patient_id: str) -> BaseResponse:
    request_id = str(uuid.uuid4())[:12]
    _state["sar_requests"][request_id] = {
        "request_id": request_id,
        "patient_id": patient_id,
        "created_at": get_sim_time().isoformat(),
        "status": "received",
        "collected_from": [],
    }
    return BaseResponse(data={"request_id": request_id, "status": "queued"})


@app.get("/gdpr/sar/{request_id}/status", response_model=BaseResponse, tags=["gdpr"])
async def sar_status(request_id: str) -> BaseResponse:
    return BaseResponse(data=_state["sar_requests"].get(request_id, {"status": "not_found"}))


# ---------------------------------------------------------------------------
# Right to Erasure (Art. 17)
# ---------------------------------------------------------------------------
@app.delete("/gdpr/purge/{patient_id}", response_model=BaseResponse, tags=["gdpr"])
async def purge_patient(patient_id: str) -> BaseResponse:
    """Propagate erasure across all registered services and record a tombstone.

    Each service is expected to expose ``DELETE /patient/{id}`` returning
    ``{deleted_count, collections_affected}``. Failures are logged but do
    not abort the cascade — the caller gets the full per-service result.
    """
    client = ServiceClient()
    results: Dict[str, Any] = {}
    for svc in SERVICE_REGISTRY:
        if svc == "gdpr":
            continue
        try:
            res = await client._get_client(svc).delete(f"/patient/{patient_id}")
            results[svc] = res
        except Exception as exc:
            results[svc] = {"status": "error", "error": str(exc)}

    tombstone = {
        "patient_id": patient_id,
        "purged_at": get_sim_time().isoformat(),
        "results": results,
    }
    coll = _coll("erasure_tombstones")
    if coll is not None:
        try:
            coll.insert_one(dict(tombstone))
        except Exception:
            pass
    return BaseResponse(data=tombstone)


@app.post("/reset", response_model=BaseResponse, tags=["system"])
async def reset_gdpr() -> BaseResponse:
    _state["in_memory_audit"].clear()
    _state["sar_requests"].clear()
    _state["breaches"].clear()
    return BaseResponse(data={"reset": True})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8217)
