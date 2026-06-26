"""
ED Flow Optimizer FastAPI Service
==================================
Real-time ED patient flow optimization, surge forecasting, bottleneck
detection, and PET compliance tracking for Irish hospitals.

Port: 8214

Usage::
    uvicorn app_14_ed_flow.backend.app.main:app --host 0.0.0.0 --port 8214
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = str(Path(__file__).resolve().parents[4])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fastapi import FastAPI, HTTPException, Query

from shared.api.base import BaseResponse, create_app
from shared.db.mongo import MongoManager
from shared.ml.registry import ModelRegistry
from shared.integration.event_bus import get_event_bus
from shared.integration.service_client import ServiceClient

MODEL_DIR = Path(os.getenv("MODEL_DIR", "./models/ed_flow"))

from app_14_ed_flow.backend.app.schemas import (
    MTS_CATEGORIES,
    PET_TARGET_HOURS,
    NEDOCS_THRESHOLDS,
    Bottleneck,
    EDPatientFlow,
    EDRecommendation,
    EDState,
    PETCompliance,
    PatientEventRequest,
    SurgeForecast,
    WhatIfResult,
    WhatIfScenarioRequest,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("ed_flow.api")

state: Dict[str, Any] = {
    "mongo": None,
    "ed_patients": {},
    "disposition_model": None,
    "los_model": None,
    "pet_model": None,
    "service_client": None,
    "event_bus": None,
    "last_sim_time": None,  # Track latest simulation time for live calculations
}


@asynccontextmanager
async def lifespan(application: FastAPI):
    # Structured logging
    try:
        from shared.integration.logging_config import setup_logging
        setup_logging(service_name="ed_flow")
    except Exception as exc:  # noqa: BLE001
        logger.warning("logging_setup_failed: %s", exc)

    # OpenTelemetry tracing
    try:
        from shared.integration.tracing import setup_tracing
        setup_tracing(application, service_name="ed_flow")
    except Exception as exc:  # noqa: BLE001
        logger.warning("tracing_setup_failed: %s", exc)

    # Prometheus /metrics
    try:
        from shared.integration.prometheus_metrics import install_metrics
        install_metrics(application, service_name="ed_flow")
    except Exception as exc:  # noqa: BLE001
        logger.warning("prometheus_metrics_install_failed: %s", exc)

    # Anchor SimClock to data_ingestion's authoritative clock.
    try:
        from shared.integration.sim_clock import attach_remote_clock
        await attach_remote_clock()
    except Exception as exc:  # noqa: BLE001
        logger.warning("sim_clock_attach_remote_failed: %s", exc)

    state["mongo"] = MongoManager()
    state["service_client"] = ServiceClient()
    state["event_bus"] = get_event_bus()

    # Load ML models
    registry = ModelRegistry(base_path=str(MODEL_DIR))
    for name, key in [("ed_flow_disposition", "disposition_model"),
                      ("ed_flow_los", "los_model"),
                      ("ed_flow_pet_breach", "pet_model")]:
        try:
            state[key], meta = registry.load_model(name)
            logger.info("Loaded %s: %s", name, meta.get("metrics", {}).get("test", {}))
        except FileNotFoundError:
            logger.warning("No %s found; using rule-based fallback.", name)

    # Durable broker + persistent state so ED patient tracking survives restart
    from shared.integration.persistent_state import PersistentState
    try:
        state["event_bus"].attach_mongo(state["mongo"].client)
        await state["event_bus"].startup()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ed_flow_broker_start_failed: %s", exc)

    # Subscribe to Kafka — patient_discharged + patient_transferred so a
    # patient who moves out of ED stops accumulating ``time_in_ed_minutes``.
    # Without this, every active ed_patients entry keeps incrementing
    # wall-clock since admission (sim time × 10×) and avg_wait_minutes /
    # NEDOCS forecast spiral up — that's the 5164-min and NEDOCS-1123
    # numbers we saw on the dashboard.
    try:
        from shared.integration.kafka_consumer import attach_with_ring_buffer

        def _ed_like(careunit: str) -> bool:
            if not careunit:
                return False
            c = str(careunit).lower()
            return ("emergency" in c or "ed" == c or "cdu" == c
                    or "observation" in c)

        def _resolve_id(payload):
            return (payload.get("hadm_id") or payload.get("subject_id")
                    or payload.get("patient_id"))

        async def _on_discharge(topic, payload):
            pid = _resolve_id(payload)
            if pid is None:
                return
            ed_pts = state.get("ed_patients") or {}
            if pid in ed_pts:
                ed_pts[pid]["current_status"] = "discharged"
                logger.info("ED patient %s discharged via Kafka", pid)
            patients = state.get("patients") or {}
            patients.pop(pid, None)

        async def _on_transfer(topic, payload):
            # If the destination is not ED-like, mark as discharged in ED's
            # local state — they've moved on, no more ED clock accrual.
            pid = _resolve_id(payload)
            if pid is None:
                return
            dest = (payload.get("to_department") or payload.get("careunit")
                    or payload.get("department") or "")
            if _ed_like(dest):
                return  # still in ED-like dept (e.g. ED → CDU); keep tracking
            ed_pts = state.get("ed_patients") or {}
            if pid in ed_pts and ed_pts[pid].get("current_status") != "discharged":
                ed_pts[pid]["current_status"] = "discharged"
                logger.info(
                    "ED patient %s tagged discharged (transferred to %s)", pid, dest
                )

        await attach_with_ring_buffer(
            service_id="ed_flow",
            topics=["admission_complete", "patient_discharged", "patient_transferred"],
            mongo_client=state["mongo"].client,
            extra_handlers={
                "patient_discharged": _on_discharge,
                "patient_transferred": _on_transfer,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ed_flow_bus_subscribe_failed: %s", exc)

    persistent = PersistentState(
        service_id="ed_flow",
        mongo=state["mongo"].client,
        collection_name="ed_flow_state",
    )
    state["persistent"] = persistent
    snap = persistent.load_snapshot()
    if snap and snap.get("state"):
        s = snap["state"]
        state["ed_patients"] = s.get("ed_patients") or {}
        state["last_sim_time"] = s.get("last_sim_time")
        logger.info("ED Flow restored %d patients from snapshot v%d",
                    len(state["ed_patients"]), snap.get("version", 0))

    # Subscribe to relevant events from other modules
    bus = state["event_bus"]
    bus.subscribe("bed_allocated", _handle_bed_allocated)
    bus.subscribe("bed_released", _handle_bed_released)

    # Replay missed events since snapshot timestamp
    try:
        n = await persistent.replay_events_since_snapshot(bus, topics=[
            "bed_allocated", "bed_released",
            "patient_transferred", "patient_discharged",
        ])
        if n:
            logger.info("ED Flow replayed %d missed events", n)
    except Exception as exc:  # noqa: BLE001
        logger.debug("ed_flow_replay_err: %s", exc)

    # Periodic snapshot task (every 30 s)
    import asyncio as _a

    # Cap per-patient events at snapshot time. Without this the events
    # list grew unbounded → Mongo "command document too large" failures
    # → repeated retries pegged the process + downstream callers (e.g.
    # data_ingestion's digital_twin propagation) saw ConnectTimeouts and
    # eventually exhausted asyncio resources. Cap at 30 most-recent
    # events per patient — enough for the dashboard's bottleneck panel.
    EVENTS_KEEP_LAST = 30

    def _build_snapshot_state():
        out_patients = {}
        for pid, p in (state.get("ed_patients") or {}).items():
            if isinstance(p, dict) and isinstance(p.get("events"), list):
                evs = p["events"]
                if len(evs) > EVENTS_KEEP_LAST:
                    p = {**p, "events": evs[-EVENTS_KEEP_LAST:]}
            out_patients[pid] = p
        return {
            "ed_patients": out_patients,
            "last_sim_time": state.get("last_sim_time"),
        }

    async def _snapshot_loop():
        while True:
            try:
                await _a.sleep(30)
                persistent.save_snapshot(_build_snapshot_state())
            except _a.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.debug("ed_flow_snapshot_err: %s", exc)

    state["snapshot_task"] = _a.create_task(_snapshot_loop())
    state["_build_snapshot_state"] = _build_snapshot_state

    logger.info("ED Flow Optimizer service ready (PET target: %dh)", PET_TARGET_HOURS)
    yield
    t = state.get("snapshot_task")
    if t is not None:
        t.cancel()
    try:
        builder = state.get("_build_snapshot_state")
        persistent.save_snapshot(builder() if builder else {
            "ed_patients": state.get("ed_patients", {}),
            "last_sim_time": state.get("last_sim_time"),
        })
    except Exception:
        pass
    try:
        await state["event_bus"].shutdown()
    except Exception:
        pass
    if state["mongo"]:
        state["mongo"].close()


app = create_app(
    title="ED Flow Optimizer",
    version="1.0.0",
    description=(
        "Real-time Emergency Department flow optimization for Irish hospitals. "
        "Patient-level time-to-disposition prediction, surge forecasting, "
        "bottleneck detection, and 6-hour PET target compliance tracking."
    ),
)
app.router.lifespan_context = lifespan


@app.get("/kafka-events", response_model=BaseResponse, tags=["system"])
async def list_kafka_events(limit: int = 100) -> BaseResponse:
    """Return recent cross-service events consumed via Kafka/broker."""
    from shared.integration.kafka_consumer import get_kafka_events
    return BaseResponse(data=get_kafka_events("ed_flow", limit))


# ---------------------------------------------------------------------------
# Patient Flow Tracking
# ---------------------------------------------------------------------------
@app.get("/patients", response_model=BaseResponse, tags=["patient-flow"])
async def get_all_ed_patients(
    status: Optional[str] = Query(None, description="Filter by status"),
) -> BaseResponse:
    """Return all current ED patients with flow predictions."""
    patients = list(state.get("ed_patients", {}).values())
    if status:
        patients = [p for p in patients if p.get("current_status") == status]
    # Sort by PET breach risk (highest first)
    patients.sort(key=lambda x: x.get("pet_breach_risk", 0), reverse=True)
    return BaseResponse(data=patients)


@app.get("/patients/{patient_id}", response_model=BaseResponse, tags=["patient-flow"])
async def get_ed_patient(patient_id: int) -> BaseResponse:
    """Return flow state and predictions for a specific ED patient.

    Resolves the URL parameter as either ``hadm_id`` (preferred — unique per
    admission) or ``subject_id`` (legacy MIMIC field, may collide across
    re-admissions). When a subject has multiple admissions, returns the most
    recent (highest hadm_id) record.
    """
    patients = state.get("ed_patients", {})
    # Try direct key match first (works when caller passes hadm_id-as-int or
    # subject_id and the dict happens to be keyed that way), then fall back
    # to scanning by patient_id (subject_id) — the dict is normally keyed
    # by string hadm_id since the simulator's hadm_ids look like
    # "SIM-21081215-1777417563".
    patient = patients.get(patient_id) or patients.get(str(patient_id))
    if not patient:
        candidates = [
            p for p in patients.values()
            if p.get("patient_id") == patient_id
        ]
        if candidates:
            # Prefer the most recent admission (last triage by arrival_time).
            candidates.sort(key=lambda p: p.get("arrival_time") or "", reverse=True)
            patient = candidates[0]
    if not patient:
        raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found in ED")
    return BaseResponse(data=patient)


@app.post("/patients/{patient_id}/event", response_model=BaseResponse, tags=["patient-flow"])
async def log_patient_event(patient_id: int, req: PatientEventRequest) -> BaseResponse:
    """Log an ED event for a patient and update predictions.

    Events trigger re-prediction of time-to-disposition, PET breach risk,
    and LWBS risk. Also triggers bed allocation request when admission predicted.

    Storage key is ``hadm_id`` when supplied in ``details`` (the orchestrator
    always supplies it), otherwise the URL ``patient_id`` (subject_id) for
    backward compatibility. Keying by hadm_id avoids the MIMIC-subject_id
    collision that previously caused returning patients to overwrite their
    earlier admission record (audit measured ~50% registration loss).
    """
    from shared.integration.sim_clock import get_sim_time as _sim_now
    now = req.timestamp or _sim_now()
    # Track latest sim time for live state queries
    if req.timestamp:
        state["last_sim_time"] = now
    patients = state.get("ed_patients", {})

    details = req.details or {}
    hadm_id_raw = details.get("hadm_id")
    # MIMIC simulator hadm_ids are strings like "SIM-21081215-1777417563";
    # accept any non-empty value as a usable key.
    hadm_key = str(hadm_id_raw) if hadm_id_raw not in (None, "", 0) else None
    storage_key = hadm_key if hadm_key is not None else patient_id

    # The pydantic schema declares hadm_id: Optional[int] for backward
    # compatibility with older callers, so coerce when possible. String
    # hadm_ids round-trip via the storage_key dict; the model field stays
    # None for those (we keep the original string in details/events).
    hadm_int: Optional[int] = None
    try:
        if hadm_id_raw is not None:
            hadm_int = int(hadm_id_raw)
    except (TypeError, ValueError):
        hadm_int = None

    if storage_key not in patients:
        if req.event_type == "triage":
            # New patient arriving via triage — create flow record
            patients[storage_key] = EDPatientFlow(
                patient_id=patient_id,
                hadm_id=hadm_int,
                arrival_time=now,
                current_status="waiting",
            ).model_dump()
            # Stash the raw (possibly-string) hadm_id alongside the model
            # so audits and downstream consumers can correlate.
            patients[storage_key]["hadm_id_raw"] = hadm_key
        else:
            # Non-triage event for unknown patient — skip (likely post-restart stale event)
            return BaseResponse(data={"skipped": True, "reason": "patient not triaged into ED"})

    patient = patients[storage_key]

    # Log event. Cap the per-patient events list at 50 entries — without
    # this it grew unbounded as data_ingestion fires vitals continuously
    # for hundreds of patients, eventually blowing past Mongo's 16 MiB
    # snapshot limit and triggering retry storms that hung the asyncio
    # loop. The 30-entry snapshot cap helped the persistence path, but
    # the in-memory list still grew; capping here keeps the process
    # footprint bounded regardless.
    evs = patient.setdefault("events", [])
    evs.append({
        "event_type": req.event_type,
        "timestamp": now.isoformat(),
        "details": req.details or {},
    })
    if len(evs) > 50:
        del evs[: len(evs) - 50]

    # Track time spent waiting (for metrics, even after status changes)
    if patient.get("current_status") == "waiting" and req.event_type not in ("triage",):
        patient["waited_minutes"] = patient.get("time_in_ed_minutes", 0)

    # Update status based on event
    status_map = {
        "triage": "waiting",
        "treatment": "in_treatment",
        "labs_resulted": "in_treatment",
        "disposition_decision": "in_treatment",
        "admitted": "boarding",
        "discharged": "discharged",
    }
    if req.event_type == "transfer" and req.details:
        to_dept = (req.details.get("to_department") or "").lower()
        is_ed = "emergency" in to_dept or to_dept in ("ed", "")
        if patient.get("current_status") != "discharged":
            patient["current_status"] = "in_treatment" if is_ed else "discharged"
    elif req.event_type in status_map:
        if patient.get("current_status") != "discharged":
            patient["current_status"] = status_map[req.event_type]

    # Update MTS category from triage event
    if req.event_type == "triage" and req.details:
        raw_mts = req.details.get("mts_category", 3)
        mts = _acuity_to_mts(raw_mts)
        patient["mts_category"] = mts
        mts_info = MTS_CATEGORIES.get(mts, MTS_CATEGORIES[3])
        patient["mts_name"] = mts_info["name"]
        patient["mts_color"] = mts_info["color"]

        # Fetch acuity from ED Triage module
        client = state.get("service_client")
        if client and req.details.get("vitals"):
            try:
                # ed_triage requires age + gender alongside vitals — without
                # them /predict returns 422 and admission_probability stays at
                # the default 0.0 for every patient.
                triage_payload = {
                    **(req.details.get("vitals") or {}),
                    "age": req.details.get("age", 65),
                    "gender": req.details.get("gender", "U"),
                }
                triage_result = await client.ed_triage.post("/predict", triage_payload)
                if triage_result.get("status") == "ok":
                    triage_data = triage_result.get("data", {})
                    patient["admission_probability"] = 0.7 if triage_data.get("acuity_level", 3) <= 2 else 0.3
            except Exception as exc:
                logger.warning(
                    "cross_service_call_failed",
                    extra={
                        "service": "ed_triage",
                        "endpoint": "/predict",
                        "patient_id": patient_id,
                        "error": str(exc),
                    },
                )

    # Track vitals from simulation events
    if req.event_type == "treatment" and req.details:
        vname = req.details.get("vital")
        vval = req.details.get("value")
        if vname and vval is not None:
            patient.setdefault("current_vitals", {})[vname] = vval

    # Track labs from simulation events
    if req.event_type == "labs_resulted" and req.details:
        lname = req.details.get("lab")
        lval = req.details.get("value")
        if lname and lval is not None:
            patient.setdefault("current_labs", {})[lname] = lval

    # Re-compute predictions
    _update_predictions(patient, now)

    # Check for alerts. Publish on transition only — previously this fired
    # on every event for every patient that was already over-threshold,
    # producing ~20 000 pet_breach_risk events for ~15 patients. We track
    # the last-published *band* (above/below threshold) per (patient, topic)
    # and emit only on the threshold crossing or after a 60 s wall cooldown.
    bus = state.get("event_bus")
    if bus:
        last_alert = state.setdefault("_last_alert_published", {})
        now_ts = datetime.utcnow().timestamp() if 'datetime' in globals() else 0.0
        from datetime import datetime as _dt  # local import: kept narrow on purpose
        now_ts = _dt.utcnow().timestamp()

        def _maybe_publish(topic: str, threshold: float, value: float, payload: dict) -> None:
            key = (patient_id, topic)
            prev = last_alert.get(key)
            above = value > threshold
            cooldown = 60.0  # seconds; minimum spacing for same-patient repeats
            if not above:
                # Below threshold — clear memory so the next crossing republishes.
                last_alert.pop(key, None)
                return
            if prev is not None and prev.get("above") and (now_ts - prev.get("ts", 0)) < cooldown:
                return
            # Either first crossing or post-cooldown — publish.
            return ("publish", payload, now_ts)

        # PET breach risk
        v = patient.get("pet_breach_risk", 0)
        decision = _maybe_publish("pet_breach_risk", 0.7, v, {})
        if decision is not None:
            await bus.publish("pet_breach_risk", {
                "patient_id": patient_id,
                "risk": v,
                "time_remaining_minutes": patient.get("pet_remaining_minutes", 0),
            }, source_module="ed_flow")
            last_alert[(patient_id, "pet_breach_risk")] = {"above": True, "ts": now_ts}

        # LWBS risk
        v = patient.get("lwbs_risk", 0)
        decision = _maybe_publish("lwbs_risk", 0.6, v, {})
        if decision is not None:
            await bus.publish("lwbs_risk", {
                "patient_id": patient_id,
                "risk": v,
            }, source_module="ed_flow")
            last_alert[(patient_id, "lwbs_risk")] = {"above": True, "ts": now_ts}

        # Admission probability
        v = patient.get("admission_probability", 0)
        decision = _maybe_publish("admission_predicted", 0.7, v, {})
        if decision is not None:
            await bus.publish("admission_predicted", {
                "patient_id": patient_id,
                "probability": v,
            }, source_module="ed_flow")
            last_alert[(patient_id, "admission_predicted")] = {"above": True, "ts": now_ts}

    return BaseResponse(data=patient)


@app.get("/patients/pet-at-risk", response_model=BaseResponse, tags=["patient-flow"])
async def patients_at_pet_risk(
    threshold: float = Query(0.5, description="PET breach risk threshold"),
) -> BaseResponse:
    """Return patients at risk of exceeding 6-hour PET target."""
    patients = state.get("ed_patients", {}).values()
    at_risk = [p for p in patients if p.get("pet_breach_risk", 0) >= threshold
               and p.get("current_status") not in ("discharged",)]
    at_risk.sort(key=lambda x: x.get("pet_breach_risk", 0), reverse=True)
    return BaseResponse(data=list(at_risk))


@app.get("/patients/lwbs-at-risk", response_model=BaseResponse, tags=["patient-flow"])
async def patients_at_lwbs_risk(
    threshold: float = Query(0.5),
) -> BaseResponse:
    """Return patients at risk of leaving without being seen."""
    patients = state.get("ed_patients", {}).values()
    at_risk = [p for p in patients if p.get("lwbs_risk", 0) >= threshold
               and p.get("current_status") == "waiting"]
    at_risk.sort(key=lambda x: x.get("lwbs_risk", 0), reverse=True)
    return BaseResponse(data=list(at_risk))


# ---------------------------------------------------------------------------
# ED State
# ---------------------------------------------------------------------------
@app.get("/ed-state", response_model=BaseResponse, tags=["ed-state"])
async def get_ed_state() -> BaseResponse:
    """Return current ED state (census, waits, crowding, PET compliance)."""
    now = _effective_now()
    patients = list(state.get("ed_patients", {}).values())
    active = [p for p in patients if p.get("current_status") not in ("discharged",)]

    # Recompute predictions so wait times and risk scores are live
    for p in active:
        _update_predictions(p, now)

    waiting = [p for p in active if p.get("current_status") == "waiting"]
    treating = [p for p in active if p.get("current_status") == "in_treatment"]
    boarding = [p for p in active if p.get("current_status") == "boarding"]

    # Compute NEDOCS
    nedocs = _compute_nedocs(len(active), waiting, boarding)
    crowding = "normal"
    for level, threshold in sorted(NEDOCS_THRESHOLDS.items(), key=lambda x: x[1], reverse=True):
        if nedocs >= threshold:
            crowding = level
            break

    # PET compliance
    pet_risk = [p for p in active if p.get("pet_breach_risk", 0) > 0.5]

    ed_state = EDState(
        timestamp=now,
        total_patients=len(active),
        waiting_count=len(waiting),
        in_treatment_count=len(treating),
        boarding_count=len(boarding),
        patients_by_mts={
            cat_info["name"]: sum(1 for p in active if p.get("mts_category") == cat)
            for cat, cat_info in MTS_CATEGORIES.items()
        },
        # Clamp per-patient wait at 12h for the dashboard tile — anything
        # above is almost certainly a patient whose transfer-out event
        # was missed (stale entry); the engine-truth ED LOS is ~1.5h.
        # Without the clamp a single 80h-stale patient pushes the avg
        # and longest into the thousands.
        avg_wait_minutes=_avg([min(p.get("time_in_ed_minutes", 0), 12 * 60) for p in active]),
        longest_wait_minutes=max([min(p.get("time_in_ed_minutes", 0), 12 * 60) for p in active], default=0),
        nedocs_score=round(nedocs, 1),
        crowding_level=crowding,
        pet_compliance_rate=1.0 - (len(pet_risk) / max(len(active), 1)),
        patients_at_pet_risk=len(pet_risk),
    )

    return BaseResponse(data=ed_state.model_dump())


@app.get("/ed-state/bottlenecks", response_model=BaseResponse, tags=["ed-state"])
async def get_bottlenecks() -> BaseResponse:
    """Return current top bottlenecks with causal attribution."""
    # Rule-based bottleneck detection (causal model in Phase 2)
    now = _effective_now()
    patients = list(state.get("ed_patients", {}).values())
    active = [p for p in patients if p.get("current_status") not in ("discharged",)]

    # Recompute predictions so wait times are current
    for p in active:
        _update_predictions(p, now)

    bottlenecks = []

    # Check for bed bottleneck
    waiting = [p for p in active if p.get("current_status") == "waiting"]
    boarding = [p for p in active if p.get("current_status") == "boarding"]
    if len(boarding) > 3:
        bottlenecks.append(Bottleneck(
            bottleneck_type="beds",
            severity="severe" if len(boarding) > 8 else "moderate",
            affected_patients=len(boarding),
            avg_delay_minutes=120,
            causal_impact_on_los=90,
            is_actionable=True,
            recommended_action="Request bed management to expedite discharges in inpatient wards",
        ).model_dump())

    # Check for wait time bottleneck
    long_waiters = [p for p in active if p.get("time_in_ed_minutes", 0) > 120
                    and p.get("current_status") == "waiting"]
    if len(long_waiters) > 5:
        bottlenecks.append(Bottleneck(
            bottleneck_type="nursing",
            severity="moderate",
            affected_patients=len(long_waiters),
            avg_delay_minutes=60,
            causal_impact_on_los=45,
            is_actionable=True,
            recommended_action="Consider activating additional triage nurse or fast-track stream",
        ).model_dump())

    # Check for overall overcrowding (high NEDOCS)
    nedocs = _compute_nedocs(len(active), waiting, boarding)
    if nedocs >= NEDOCS_THRESHOLDS.get("crowded", 180):
        severity = "severe" if nedocs >= NEDOCS_THRESHOLDS.get("severe", 200) else "moderate"
        bottlenecks.append(Bottleneck(
            bottleneck_type="overcrowding",
            severity=severity,
            affected_patients=len(active),
            avg_delay_minutes=round(nedocs * 0.3, 0),
            causal_impact_on_los=round(nedocs * 0.2, 0),
            is_actionable=True,
            recommended_action=(
                "NEDOCS score {:.0f} ({}) — activate surge protocol, "
                "consider ambulance diversion and opening overflow area"
            ).format(nedocs, severity),
        ).model_dump())

    return BaseResponse(data=bottlenecks)


# ---------------------------------------------------------------------------
# Forecasting
# ---------------------------------------------------------------------------
def _observed_arrival_rate(patients: Dict[str, Any], now: datetime) -> Optional[float]:
    """Arrival rate per hour derived from actual patients in state.

    Returns ``None`` when there's not enough data to compute a real
    rate — callers should render an empty-state forecast rather than
    fabricate numbers.
    """
    if not patients:
        return None
    times: List[datetime] = []
    for p in patients.values():
        at = p.get("arrival_time")
        if isinstance(at, str):
            try:
                at = datetime.fromisoformat(at)
            except ValueError:
                continue
        if isinstance(at, datetime):
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            times.append(at)
    if len(times) < 2:
        return None
    elapsed_hours = max(0.5, (now - min(times)).total_seconds() / 3600)
    return len(times) / elapsed_hours


@app.get("/forecast/arrivals", response_model=BaseResponse, tags=["forecast"])
async def forecast_arrivals() -> BaseResponse:
    """Predict ED arrivals for next 4/8/12/24 hours based on observed rate.

    Returns an explicit empty-state forecast (zeros + ``source="no_data"``)
    when no live patients are in the system, rather than fabricating
    a synthetic curve. The dashboard renders this as a dashed grey line
    with a "no live data" annotation so clinicians know the curve isn't
    a real projection.
    """
    now = _effective_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    patients = state.get("ed_patients", {})
    base_rate = _observed_arrival_rate(patients, now)
    has_data = base_rate is not None and base_rate > 0

    forecasts = []
    for horizon in [4, 8, 12, 24]:
        if has_data:
            hour = (now.hour + horizon // 2) % 24
            if 8 <= hour <= 12:
                rate = base_rate * 1.4
            elif 12 <= hour <= 20:
                rate = base_rate * 1.2
            elif 20 <= hour or hour <= 6:
                rate = base_rate * 0.6
            else:
                rate = base_rate
            predicted = rate * horizon
            # Project steady-state ED census via Little's Law:
            #   census ≈ arrival_rate × mean_LOS
            # The previous formula multiplied arrivals (rate × horizon) by
            # an LOS-derived factor, which produced cumulative-arrivals,
            # not census. With a 24h horizon and 16/h arrival that gave
            # 374 → NEDOCS 1123. Clamp mean_los to a sane band [1,8]h
            # because ``time_in_ed_minutes`` includes patients who've
            # already moved to wards but aren't yet flagged discharged
            # in this service's local state.
            mean_los_h = _avg([p.get("time_in_ed_minutes", 0) for p in patients.values()]) / 60
            sane_los_h = max(1.0, min(8.0, mean_los_h))
            predicted_census = round(rate * sane_los_h, 1)
        else:
            predicted = 0.0
            predicted_census = 0.0
        forecasts.append({
            **SurgeForecast(
                forecast_time=now + timedelta(hours=horizon),
                horizon_hours=horizon,
                predicted_arrivals=round(predicted, 1),
                predicted_arrivals_lower=round(predicted * 0.7, 1),
                predicted_arrivals_upper=round(predicted * 1.3, 1),
                predicted_census=predicted_census,
                predicted_nedocs=round(_compute_nedocs_from_census(predicted_census), 1),
                predicted_crowding_level=_crowding_level(_compute_nedocs_from_census(predicted_census)),
            ).model_dump(),
            "source": "observed" if has_data else "no_data",
            "observed_arrival_rate_per_hour": round(base_rate, 2) if has_data else None,
        })

    return BaseResponse(data=forecasts)


def _compute_nedocs_from_census(census: float) -> float:
    """Derive a NEDOCS estimate from projected census using the same
    scaling as ``_compute_nedocs`` (3 × patients baseline).

    Capped at 200 — the published NEDOCS scale tops out at "dangerous"
    overcrowding around 180-200, and any larger value is just a runaway
    forecaster (we saw 1123 produced when ``predicted_census`` was a
    cumulative arrival count, not a steady-state census).
    """
    return max(0.0, min(200.0, census * 3.0))


def _crowding_level(nedocs: float) -> str:
    for level, threshold in sorted(NEDOCS_THRESHOLDS.items(), key=lambda x: x[1], reverse=True):
        if nedocs >= threshold:
            return level
    return "normal"


@app.get("/forecast/crowding", response_model=BaseResponse, tags=["forecast"])
async def forecast_crowding() -> BaseResponse:
    """Predict NEDOCS trajectory for next 24 hours.

    Starts from the actual current NEDOCS (computed from live ED state)
    and projects forward using the observed arrival rate + mean LOS
    decay. When the ED is empty this returns a flat zero trajectory
    with ``source="no_data"`` so the dashboard shows "no live forecast"
    rather than a fabricated synthetic peak.
    """
    from shared.integration.sim_clock import get_sim_time as _sim_now
    now = _sim_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    patients_dict = state.get("ed_patients", {}) or {}
    active = [p for p in patients_dict.values() if p.get("current_status") != "discharged"]
    waiting = [p for p in active if p.get("current_status") in ("waiting", "waiting_registration", "triaged")]
    boarding = [p for p in active if p.get("current_status") == "admitted_waiting_bed"]

    current_nedocs = _compute_nedocs(len(active), waiting, boarding)
    arrival_rate = _observed_arrival_rate(patients_dict, now)
    has_live_data = len(active) > 0 or (arrival_rate is not None and arrival_rate > 0)

    # Mean ED LOS gives us the decay constant for current census.
    # Clamp to [1h, 8h] so a stale ``time_in_ed_minutes`` (from patients
    # who already left ED but weren't flagged discharged) doesn't make
    # decay near-zero, which compounds census growth into a runaway
    # NEDOCS-1000+ forecast.
    if active:
        los_values = [p.get("time_in_ed_minutes", 0) for p in active]
        mean_los_min = max(60.0, min(8 * 60.0, _avg(los_values) + 60))
    else:
        mean_los_min = 240.0  # 4h typical ED LOS as decay constant

    trajectory: List[Dict[str, Any]] = []
    projected_census = float(len(active))
    base_rate = arrival_rate or 0.0

    for h in range(1, 25):
        sim_hour = (now.hour + h) % 24
        # Diurnal shape applied only as a modifier to the observed rate —
        # if observed rate is 0, diurnal can't conjure arrivals.
        if 8 <= sim_hour <= 12:
            diurnal = 1.4
        elif 12 <= sim_hour <= 20:
            diurnal = 1.2
        elif 20 <= sim_hour or sim_hour <= 6:
            diurnal = 0.6
        else:
            diurnal = 1.0
        arrivals_this_hour = base_rate * diurnal  # patients per hour
        # Decay existing census toward zero at 1 / mean_los rate, add arrivals
        decay = 60.0 / mean_los_min  # fraction leaving per hour
        projected_census = max(0.0, projected_census * (1 - decay) + arrivals_this_hour)
        # Rough split: 60/20/20 active/waiting/boarding
        nedocs = _compute_nedocs(
            total_patients=int(round(projected_census)),
            waiting=[{} for _ in range(int(projected_census * 0.2))],
            boarding=[{} for _ in range(int(projected_census * 0.2))],
        )
        trajectory.append({
            "hour": h,
            "time": (now + timedelta(hours=h)).isoformat(),
            "predicted_nedocs": round(nedocs, 1),
            "predicted_census": round(projected_census, 1),
            "crowding_level": _crowding_level(nedocs),
            "source": "observed" if has_live_data else "no_data",
        })

    return BaseResponse(data=trajectory)


# ---------------------------------------------------------------------------
# Simulation / What-If
# ---------------------------------------------------------------------------
@app.post("/simulate/what-if", response_model=BaseResponse, tags=["simulation"])
async def simulate_what_if(req: WhatIfScenarioRequest) -> BaseResponse:
    """Run a what-if simulation scenario.

    Uses DES-ML hybrid (reuses App 03 DES engine + ML predictions).
    """
    # Derive baselines from current ED patients (full DES-ML hybrid in Phase 2)
    now = _effective_now()
    patients = list(state.get("ed_patients", {}).values())
    active = [p for p in patients if p.get("current_status") not in ("discharged",)]
    for p in active:
        _update_predictions(p, now)

    if active:
        los_values = [p.get("time_in_ed_minutes", 0) for p in active]
        baseline_los = round(_avg(los_values), 1)
        pet_at_risk = sum(1 for p in active if p.get("pet_breach_risk", 0) > 0.5)
        baseline_pet = round(1.0 - (pet_at_risk / len(active)), 3)
        lwbs_at_risk = sum(1 for p in active if p.get("lwbs_risk", 0) > 0.3)
        baseline_lwbs = round(lwbs_at_risk / len(active), 3)
    else:
        baseline_los = 240  # fallback: 4 hours
        baseline_pet = 0.75
        baseline_lwbs = 0.05

    impact_map = {
        "add_doctor": {"los_reduction": 20, "pet_improvement": 0.05, "lwbs_reduction": 0.01},
        "add_beds": {"los_reduction": 30, "pet_improvement": 0.08, "lwbs_reduction": 0.005},
        "divert_ambulances": {"los_reduction": 15, "pet_improvement": 0.04, "lwbs_reduction": 0.01},
        "open_overflow": {"los_reduction": 25, "pet_improvement": 0.06, "lwbs_reduction": 0.005},
        "reduce_lab_tat": {"los_reduction": 35, "pet_improvement": 0.07, "lwbs_reduction": 0.008},
    }

    impact = impact_map.get(req.scenario_type, {"los_reduction": 0, "pet_improvement": 0, "lwbs_reduction": 0})
    factor = req.parameter_value

    result = WhatIfResult(
        scenario_type=req.scenario_type,
        parameter_value=req.parameter_value,
        simulation_hours=req.simulation_hours,
        baseline_avg_los=baseline_los,
        simulated_avg_los=baseline_los - impact["los_reduction"] * factor,
        los_reduction_minutes=round(impact["los_reduction"] * factor, 1),
        baseline_pet_compliance=baseline_pet,
        simulated_pet_compliance=min(1.0, baseline_pet + impact["pet_improvement"] * factor),
        baseline_lwbs_rate=baseline_lwbs,
        simulated_lwbs_rate=max(0, baseline_lwbs - impact["lwbs_reduction"] * factor),
        summary=f"Adding {factor:.0f} {req.scenario_type.replace('_', ' ')} is predicted to "
                f"reduce avg ED LOS by {impact['los_reduction'] * factor:.0f} minutes and "
                f"improve PET compliance by {impact['pet_improvement'] * factor * 100:.1f}%",
    )

    return BaseResponse(data=result.model_dump())


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------
@app.get("/recommendations", response_model=BaseResponse, tags=["recommendations"])
async def get_recommendations() -> BaseResponse:
    """Return current actionable recommendations based on ED state."""
    ed_state_resp = await get_ed_state()
    ed_data = ed_state_resp.data

    recs = []

    if ed_data.get("crowding_level") in ("crowded", "severe"):
        recs.append(EDRecommendation(
            recommendation_type="surge",
            priority="critical",
            title="Activate surge protocol",
            description=f"NEDOCS score {ed_data.get('nedocs_score', 0):.0f} — ED is {ed_data.get('crowding_level')}",
            expected_impact="Reduce wait times by 15-25%",
            estimated_time_saved_minutes=30,
        ).model_dump())

    if ed_data.get("boarding_count", 0) > 5:
        recs.append(EDRecommendation(
            recommendation_type="flow",
            priority="high",
            title="Request inpatient discharge acceleration",
            description=f"{ed_data.get('boarding_count', 0)} patients boarding in ED awaiting beds",
            expected_impact="Free beds for admitted ED patients",
            estimated_time_saved_minutes=60,
        ).model_dump())

    if ed_data.get("patients_at_pet_risk", 0) > 3:
        recs.append(EDRecommendation(
            recommendation_type="escalation",
            priority="high",
            title="PET breach risk — expedite care for at-risk patients",
            description=f"{ed_data.get('patients_at_pet_risk', 0)} patients at risk of exceeding 6-hour target",
            expected_impact="Improve PET compliance rate",
            estimated_time_saved_minutes=45,
        ).model_dump())

    return BaseResponse(data=recs)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
@app.get("/metrics/pet-compliance", response_model=BaseResponse, tags=["metrics"])
async def pet_compliance_metrics(
    period: str = Query("today", description="today, week, month"),
) -> BaseResponse:
    """Return PET compliance metrics for the specified period."""
    patients = list(state.get("ed_patients", {}).values())
    discharged = [p for p in patients if p.get("current_status") == "discharged"]

    total = len(discharged) or 1
    within_target = sum(1 for p in discharged
                       if p.get("time_in_ed_minutes", 0) <= PET_TARGET_HOURS * 60)

    return BaseResponse(data=PETCompliance(
        period=period,
        total_patients=len(discharged),
        within_6_hours=within_target,
        compliance_rate=round(within_target / total, 3),
    ).model_dump())


@app.get("/metrics/wait-times", response_model=BaseResponse, tags=["metrics"])
async def wait_time_metrics() -> BaseResponse:
    """Return ED wait time statistics."""
    patients = list(state.get("ed_patients", {}).values())
    active = [p for p in patients if p.get("current_status") not in ("discharged",)]
    waits = [p.get("time_in_ed_minutes", 0) for p in active]

    return BaseResponse(data={
        "total_active": len(active),
        "avg_wait_minutes": round(_avg(waits), 1),
        "median_wait_minutes": round(sorted(waits)[len(waits) // 2], 1) if waits else 0,
        "p95_wait_minutes": round(sorted(waits)[int(len(waits) * 0.95)], 1) if waits else 0,
        "max_wait_minutes": max(waits, default=0),
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import random

_MTS_WEIGHTS = {
    1: [0.9, 0.1, 0.0, 0.0, 0.0],
    2: [0.1, 0.5, 0.3, 0.1, 0.0],
    3: [0.0, 0.1, 0.5, 0.3, 0.1],
    4: [0.0, 0.0, 0.1, 0.5, 0.4],
    5: [0.0, 0.0, 0.0, 0.2, 0.8],
}


def _acuity_to_mts(acuity: int) -> int:
    """Map acuity to MTS with realistic weighted variation."""
    w = _MTS_WEIGHTS.get(acuity, _MTS_WEIGHTS[3])
    return random.choices([1, 2, 3, 4, 5], weights=w, k=1)[0]


def _effective_now() -> datetime:
    """Return the latest simulation time if available, otherwise shared SimClock.

    Always trusts ``last_sim_time`` when available — the simulation clock is
    authoritative. Rule 1 (Single Clock): fall back to the shared SimClock
    rather than wall-clock UTC so the platform stays synchronised even if
    this service receives no events.
    """
    sim_time = state.get("last_sim_time")
    if sim_time is not None:
        # Ensure timezone-aware
        if sim_time.tzinfo is None:
            sim_time = sim_time.replace(tzinfo=timezone.utc)
        return sim_time
    from shared.integration.sim_clock import get_sim_time as _get_sim_time
    return _get_sim_time()


def _update_predictions(patient: Dict, now: datetime) -> None:
    """Re-compute flow predictions for a patient (TFT model in Phase 2)."""
    arrival = patient.get("arrival_time")
    if isinstance(arrival, str):
        try:
            arrival = datetime.fromisoformat(arrival)
        except (ValueError, TypeError):
            arrival = None
    if arrival:
        # Ensure both are timezone-aware or both naive for subtraction
        if arrival.tzinfo is None and now.tzinfo is not None:
            arrival = arrival.replace(tzinfo=timezone.utc)
        elif arrival.tzinfo is not None and now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        time_in_ed = max(0, (now - arrival).total_seconds() / 60)
    else:
        time_in_ed = 0

    patient["time_in_ed_minutes"] = round(time_in_ed, 1)
    patient["pet_remaining_minutes"] = round(max(0, PET_TARGET_HOURS * 60 - time_in_ed), 1)

    # PET breach risk (rule-based; TFT in Phase 2)
    pet_fraction = time_in_ed / (PET_TARGET_HOURS * 60)
    if patient.get("current_status") in ("discharged",):
        patient["pet_breach_risk"] = 0
    elif pet_fraction > 1.0:
        patient["pet_breach_risk"] = 1.0
    elif pet_fraction > 0.7:
        patient["pet_breach_risk"] = round(min(1.0, (pet_fraction - 0.5) * 2), 3)
    else:
        patient["pet_breach_risk"] = round(max(0, pet_fraction * 0.3), 3)

    # LWBS risk (rule-based; multi-task model in Phase 2)
    if patient.get("current_status") == "waiting" and time_in_ed > 120:
        mts = patient.get("mts_category", 3)
        base_lwbs = 0.02 * (time_in_ed / 60)
        if mts >= 4:
            base_lwbs *= 2  # Lower acuity more likely to leave
        patient["lwbs_risk"] = round(min(1.0, base_lwbs), 3)
    else:
        patient["lwbs_risk"] = 0


def _compute_nedocs(total_patients: int, waiting: List, boarding: List) -> float:
    """Compute modified NEDOCS score for Irish ED.

    Capped at 200 — published NEDOCS scale tops out at "dangerous"
    overcrowding around 180-200; any larger value just means runaway
    inputs. Also clamps the longest-wait contribution because
    ``time_in_ed_minutes`` is only valid for patients still tracked in
    ED state and can be stale for ones already moved to wards.
    """
    score = total_patients * 3.0
    score += len(waiting) * 5.0
    score += len(boarding) * 8.0
    raw_longest = max([p.get("time_in_ed_minutes", 0) for p in waiting], default=0)
    longest_wait = min(raw_longest, 8 * 60)  # clamp at 8h to limit blast radius
    score += longest_wait * 0.1
    return min(200.0, max(0.0, score))


def _avg(values: List[float]) -> float:
    return sum(values) / max(len(values), 1)


@app.post("/reset", response_model=BaseResponse, tags=["system"])
async def reset_ed_flow() -> BaseResponse:
    """Clear all in-memory ED patient state (called on simulation reset)."""
    state["ed_patients"] = {}
    state["last_sim_time"] = None
    persistent = state.get("persistent")
    if persistent is not None:
        persistent.clear()
    logger.info("ED Flow state reset — all patients cleared, snapshot purged")
    return BaseResponse(data={"reset": True})


@app.post("/notify-bed-allocated", response_model=BaseResponse, tags=["notifications"])
async def notify_bed_allocated(data: Dict[str, Any]) -> BaseResponse:
    """Receive bed allocation notification from Digital Twin (cross-process).

    Integration 3 — payload now carries ``estimated_transfer_time_min`` so ED
    Flow can set the boarding-since timestamp and recompute NEDOCS correctly.
    """
    from shared.integration.sim_clock import get_sim_time as _sim_now
    patient_id = data.get("patient_id")
    bed_id = data.get("bed_id")
    etm = data.get("estimated_transfer_time_min")
    dest_dept = data.get("department")
    patients = state.get("ed_patients", {})
    patient = patients.get(patient_id)
    if patient and patient.get("current_status") != "discharged":
        patient["current_status"] = "boarding"
        patient["current_bottleneck"] = None
        patient["assigned_bed"] = bed_id
        patient["boarding_since"] = _sim_now().isoformat()
        if etm is not None:
            patient["estimated_transfer_time_min"] = etm
        if dest_dept:
            patient["destination_department"] = dest_dept

        # Recalculate NEDOCS so dashboards reflect the new boarding load
        try:
            recalc = state.get("recalc_nedocs")
            if callable(recalc):
                recalc()
        except Exception as exc:
            logger.warning("nedocs_recalc_failed", extra={"error": str(exc)})

        logger.info("Bed %s allocated for ED patient %s (via HTTP, etm=%s)",
                    bed_id, patient_id, etm)
        return BaseResponse(data={"updated": True})
    return BaseResponse(data={"updated": False})


def _handle_bed_allocated(event) -> None:
    """Handle bed allocation events from Bed Management module (in-process)."""
    patient_id = event.payload.get("patient_id")
    if patient_id and patient_id in state.get("ed_patients", {}):
        state["ed_patients"][patient_id]["current_bottleneck"] = None
        logger.info("Bed allocated for ED patient %d", patient_id)


def _handle_bed_released(event) -> None:
    """Handle bed release events — may free capacity for boarding patients."""
    logger.info("Bed released in %s — checking boarding patients", event.payload.get("department"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8214)
