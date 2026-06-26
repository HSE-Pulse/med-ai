"""Explainability & Audit Service (XAI) — port 8218.

EU AI Act Arts. 13 (transparency), 14 (human oversight), 72 (post-market
monitoring). Collects SHAP values from every ML module, surfaces clinician
overrides, and tracks override-rate as a drift proxy.
"""

from __future__ import annotations

import logging
import sys
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fastapi import FastAPI, Query

from shared.api.base import BaseResponse, PrivacyNotice, create_app
from shared.db.mongo import MongoManager
from shared.integration.sim_clock import get_sim_time

logger = logging.getLogger("xai_audit")


PRIVACY = PrivacyNotice(
    data_collected=[
        "model_decisions", "shap_values", "confidence_scores",
        "clinician_overrides", "model_cards",
    ],
    legal_basis="Legal obligation (EU AI Act Art. 13, 14, 72)",
    retention_period="24 months",
)

_MODEL_CARDS = {
    "ed_triage": {
        "name": "ED Triage XGBoost v1",
        "task": "5-level acuity classification (MTS 1-5)",
        "training_data": "MIMIC-IV v2.2 ED cohort, mapped to Irish HSE departments",
        "performance": {"auroc": 0.89, "macro_f1": 0.71},
        "known_limitations": [
            "Trained on US cohort — calibration may shift for Irish presentations",
            "Does not account for cultural/language barriers",
        ],
        "intended_use": "Decision support only — clinician must confirm category",
        "out_of_scope": "Paediatric (<16) and obstetric patients",
    },
    "sepsis_icu": {
        "name": "Sepsis ICU Ensemble (LightGBM + LSTM-Attention)",
        "task": "Sepsis / septic shock risk from vitals+labs time window",
        "training_data": "MIMIC-IV v2.2 ICU cohort",
        "performance": {"auroc": 0.91, "auprc": 0.62},
        "known_limitations": ["Requires 6h observation window"],
        "intended_use": "Early warning support for ICU rounding",
    },
    "oncology_ai": {
        "name": "Oncology Risk XGBoost Dual-Head",
        "task": "30-day readmission + in-hospital mortality",
        "training_data": "MIMIC-IV oncology admissions",
        "performance": {"readmit_auroc": 0.78, "mortality_auroc": 0.85},
        "known_limitations": ["Limited Irish cohort data for calibration"],
        "intended_use": "Community-care referral pathway planning",
    },
    "hospital_ops": {
        "name": "Hospital Ops MADDPG MARL",
        "task": "Department-level staffing allocation",
        "training_data": "Simulated DES environment",
        "performance": {"train_reward": "converges after ~200k steps"},
        "known_limitations": ["Policy may diverge under distribution shift"],
        "intended_use": "Advisory — requires human confirmation via pending-action queue",
    },
    "bed_management": {
        "name": "Bed Mgmt TFT + DeepSurv",
        "task": "Discharge time and LOS prediction",
        "training_data": "MIMIC-IV inpatient episodes",
        "performance": {"auroc_24h": 0.82},
        "known_limitations": ["Sensitive to missing documentation"],
        "intended_use": "Capacity planning and bed allocation",
    },
}

_state: Dict[str, Any] = {
    "mongo": None,
    "decisions": [],             # in-memory ring buffer
    "overrides": [],
    "override_counts": defaultdict(int),
    "total_by_module": defaultdict(int),
}


def _coll(name: str):
    mongo = _state.get("mongo")
    if mongo is None:
        return None
    try:
        return mongo.client["xai_audit"][name]
    except Exception:
        return None


@asynccontextmanager
async def lifespan(application: FastAPI):
    # Observability
    try:
        from shared.integration.logging_config import setup_logging
        setup_logging(service_name="xai")
    except Exception as exc:  # noqa: BLE001
        logger.warning("logging_setup_failed: %s", exc)
    try:
        from shared.integration.tracing import setup_tracing
        setup_tracing(application, service_name="xai")
    except Exception as exc:  # noqa: BLE001
        logger.warning("tracing_setup_failed: %s", exc)
    try:
        from shared.integration.prometheus_metrics import install_metrics
        install_metrics(application, service_name="xai")
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
        # 24-month retention via TTL index on timestamp
        coll = _coll("decisions")
        if coll is not None:
            try:
                coll.create_index("timestamp", expireAfterSeconds=60 * 60 * 24 * 730)
            except Exception:
                pass
    except Exception as exc:
        logger.warning("xai_mongo_init_failed: %s", exc)

    # Subscribe to Kafka — AI decisions / admissions for SHAP explanation cache
    try:
        if _state.get("mongo"):
            from shared.integration.kafka_consumer import attach_with_ring_buffer
            await attach_with_ring_buffer(
                service_id="xai",
                topics=["admission_complete", "deterioration_alert", "priority_updated"],
                mongo_client=_state["mongo"].client,
            )
    except Exception as exc:
        logger.warning("xai_bus_subscribe_failed: %s", exc)

    yield


app = create_app(
    title="Explainability & Audit Service",
    version="1.0.0",
    description="SHAP-based explanations and AI Act audit trail.",
    privacy_notice=PRIVACY,
)
app.router.lifespan_context = lifespan


@app.get("/kafka-events", response_model=BaseResponse, tags=["system"])
async def list_kafka_events(limit: int = 100) -> BaseResponse:
    """Return recent cross-service events consumed via Kafka/broker."""
    from shared.integration.kafka_consumer import get_kafka_events
    return BaseResponse(data=get_kafka_events("xai", limit))


# ---------------------------------------------------------------------------
# Decision logging
# ---------------------------------------------------------------------------
@app.post("/xai/log-decision", response_model=BaseResponse, tags=["xai"])
async def log_decision(payload: dict) -> BaseResponse:
    """Accept a SHAP-enriched prediction record from an ML service."""
    doc = dict(payload)
    doc.setdefault("timestamp", get_sim_time().isoformat())
    doc.setdefault("prediction_id", str(abs(hash(str(doc)))))
    _state["decisions"].append(doc)
    if len(_state["decisions"]) > 10000:
        _state["decisions"] = _state["decisions"][-5000:]
    module = doc.get("module", "unknown")
    _state["total_by_module"][module] += 1
    coll = _coll("decisions")
    if coll is not None:
        try:
            coll.insert_one(dict(doc))
        except Exception:
            pass
    return BaseResponse(data={"logged": True, "prediction_id": doc["prediction_id"]})


@app.get("/xai/decision-log", response_model=BaseResponse, tags=["xai"])
async def decision_log(
    module: Optional[str] = Query(None),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=5000),
) -> BaseResponse:
    coll = _coll("decisions")
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
        docs = [
            d for d in _state["decisions"]
            if module is None or d.get("module") == module
        ][-limit:]
    return BaseResponse(data=docs)


@app.get("/xai/explain/{module}/{prediction_id}", response_model=BaseResponse, tags=["xai"])
async def explain(module: str, prediction_id: str) -> BaseResponse:
    coll = _coll("decisions")
    match = None
    if coll is not None:
        try:
            match = coll.find_one(
                {"module": module, "prediction_id": prediction_id}, {"_id": 0}
            )
        except Exception:
            match = None
    if match is None:
        for d in _state["decisions"]:
            if d.get("module") == module and str(d.get("prediction_id")) == prediction_id:
                match = d
                break
    if match is None:
        return BaseResponse(status="error", error="not found")
    return BaseResponse(data=match)


# ---------------------------------------------------------------------------
# Model cards (Art. 13)
# ---------------------------------------------------------------------------
@app.get("/xai/model-card/{module}", response_model=BaseResponse, tags=["xai"])
async def model_card(module: str) -> BaseResponse:
    card = _MODEL_CARDS.get(module)
    if card is None:
        return BaseResponse(status="error", error="unknown module")
    return BaseResponse(data=card)


# ---------------------------------------------------------------------------
# Human override (Art. 14)
# ---------------------------------------------------------------------------
@app.post("/xai/human-override", response_model=BaseResponse, tags=["xai"])
async def human_override(payload: dict) -> BaseResponse:
    """Record a clinician override of an AI recommendation."""
    doc = dict(payload)
    doc.setdefault("timestamp", get_sim_time().isoformat())
    _state["overrides"].append(doc)
    if len(_state["overrides"]) > 5000:
        _state["overrides"] = _state["overrides"][-2500:]
    module = doc.get("module", "unknown")
    _state["override_counts"][module] += 1
    coll = _coll("overrides")
    if coll is not None:
        try:
            coll.insert_one(dict(doc))
        except Exception:
            pass
    return BaseResponse(data={"recorded": True})


@app.get("/xai/override-stats", response_model=BaseResponse, tags=["xai"])
async def override_stats() -> BaseResponse:
    out = []
    for module in {*_state["total_by_module"], *_state["override_counts"]}:
        total = _state["total_by_module"][module] or 0
        overrides = _state["override_counts"][module] or 0
        rate = (overrides / total) if total else 0.0
        out.append({"module": module, "total_decisions": total, "overrides": overrides, "override_rate": round(rate, 3)})
    return BaseResponse(data=out)


@app.post("/reset", response_model=BaseResponse, tags=["system"])
async def reset_xai() -> BaseResponse:
    _state["decisions"].clear()
    _state["overrides"].clear()
    _state["override_counts"].clear()
    _state["total_by_module"].clear()
    return BaseResponse(data={"reset": True})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8218)
