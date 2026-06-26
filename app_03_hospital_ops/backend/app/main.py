"""FastAPI service for Hospital Operations DES-MARL simulation.

Endpoints:
  POST /simulate     - Run a full simulation and return results
  POST /start        - Start an interactive simulation session
  POST /step         - Advance the simulation one step
  GET  /state        - Get the current simulation state
  GET  /metrics      - Get performance metrics
  WS   /ws/simulation - Stream real-time simulation updates
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from shared.api.base import BaseResponse, create_app
from .schemas import (
    DepartmentMetrics,
    DepartmentState,
    PerformanceMetrics,
    SimulateRequest,
    SimulateResponse,
    SimulationConfig,
    SimulationStartResponse,
    SimulationState,
    StepRequest,
    StepResponse,
    WSMessage,
    WSMessageType,
)
from ..simulation.des_engine import DESConfig, DESEngine

from shared.constants.hospital import DEPARTMENTS
STATE_DIM = 12
ACTION_DIM = 4

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = create_app(
    title="Hospital Operations DES-MARL",
    description=(
        "Discrete Event Simulation with Multi-Agent Reinforcement Learning "
        "for hospital operations optimization. Extends HSE Pulse MediSync "
        "to Indian hospital context."
    ),
    version="1.0.0",
)


@app.on_event("startup")
async def _hops_startup():
    """Start the durable event broker + restore census snapshot from Mongo."""
    # Structured JSON logging + Loki push
    try:
        from shared.integration.logging_config import setup_logging
        setup_logging(service_name="hospital_ops")
    except Exception as exc:  # noqa: BLE001
        logger.warning("logging_setup_failed: %s", exc)

    # OpenTelemetry tracing
    try:
        from shared.integration.tracing import setup_tracing
        setup_tracing(app, service_name="hospital_ops")
    except Exception as exc:  # noqa: BLE001
        logger.warning("tracing_setup_failed: %s", exc)

    # Prometheus /metrics with domain gauges for Grafana dashboards
    try:
        from shared.integration.prometheus_metrics import install_metrics
        global _prom_metrics
        _prom_metrics = install_metrics(app, service_name="hospital_ops")
        _prom_metrics.gauge(
            "medai_active_patients", "Active patients in the DES now", ["department"],
        )
        _prom_metrics.gauge(
            "medai_department_occupancy_ratio", "Occupancy ratio per department (0–1)", ["department"],
        )
        _prom_metrics.gauge(
            "medai_department_queue_length", "Waiting queue per department", ["department"],
        )
        _prom_metrics.gauge(
            "medai_department_avg_wait_minutes", "Mean wait per department (min)", ["department"],
        )
        _prom_metrics.counter(
            "medai_admissions_total", "Total admissions injected into hospital_ops since boot",
        )
        _prom_metrics.counter(
            "medai_discharges_total", "Total discharges recorded since boot",
        )
        _prom_metrics.gauge(
            "medai_simulation_time_hours", "Current simulation time in hours",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("prometheus_metrics_install_failed: %s", exc)

    # Load Art. 14 governance config before anything else
    _load_governance_config()
    if _is_production_mode() and not _governance_config["human_oversight_enabled"]:
        logger.warning(
            "governance_override: production mode forces human_oversight_enabled=True"
        )

    try:
        from shared.db.mongo import MongoManager
        from shared.integration.event_bus import get_event_bus
        from shared.integration.kafka_consumer import attach_service_to_bus
        mongo = MongoManager()
        bus = get_event_bus()
        bus.attach_mongo(mongo.client)
        await bus.startup()
        # Restore last census snapshot from action_log so /ops/action-log
        # and _real_census reflect pre-restart state.
        try:
            coll = mongo.client["hospital_ops"]["action_log"]
            recent = list(coll.find({}).sort("_id", -1).limit(500))
            for doc in reversed(recent):
                doc.pop("_id", None)
                _action_log.append(doc)
            logger.info("Hospital Ops restored %d action_log entries from Mongo", len(recent))
        except Exception as exc:  # noqa: BLE001
            logger.debug("hops_action_log_restore_skip: %s", exc)

        # Subscribe to cross-service events for DES mirroring + observability.
        # The MARL engine and the shadow baseline engine both receive every
        # cross-module admission so the simulator stays in sync with the rest
        # of the stack (ED triage, sepsis_icu, data_ingestion) instead of
        # only mirroring bed_management's census.
        async def _on_admission(topic, payload):
            _cross_service_events.append({"topic": topic, "hadm_id": payload.get("hadm_id"), "at": _now_iso_safe()})
            try:
                _mirror_admission_to_engines(payload)
            except Exception as exc:  # noqa: BLE001
                logger.debug("on_admission_mirror_failed: %s", exc)
        async def _on_discharge(topic, payload):
            _cross_service_events.append({"topic": topic, "hadm_id": payload.get("hadm_id"), "at": _now_iso_safe()})
            try:
                _mirror_discharge_to_engines(payload)
            except Exception as exc:  # noqa: BLE001
                logger.debug("on_discharge_mirror_failed: %s", exc)
        async def _on_transfer(topic, payload):
            _cross_service_events.append({"topic": topic, "hadm_id": payload.get("hadm_id"), "at": _now_iso_safe()})
            try:
                _mirror_transfer_to_engines(payload)
            except Exception as exc:  # noqa: BLE001
                logger.debug("on_transfer_mirror_failed: %s", exc)
        await attach_service_to_bus(
            service_id="hospital_ops",
            topic_handlers={
                "admission_complete": _on_admission,
                "patient_discharged": _on_discharge,
                "patient_transferred": _on_transfer,
            },
            mongo_client=mongo.client,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("hospital_ops_startup_broker_err: %s", exc)

    # Anchor SimClock to data_ingestion's authoritative clock — replaces
    # the prior bespoke ``_auth_clock_refresher`` that lived in this module.
    # Now ``get_sim_time()`` returns the simulator-aligned time, ticking at
    # data_ingestion's speed, which is also what ``_authoritative_now``
    # reads via the shared singleton.
    try:
        from shared.integration.sim_clock import attach_remote_clock
        await attach_remote_clock()
    except Exception as exc:  # noqa: BLE001
        logger.warning("sim_clock_attach_remote_failed: %s", exc)

    # One-shot resync from MIMIC_SIM at boot — closes the active-patient
    # gap between this DES engine and bed_management. Without this, every
    # service restart resets the engine to zero and the dashboard's
    # Hospital Ops occupancy / wait time diverges from bed_management's
    # ground truth (e.g. ICU 100% in bed_mgmt vs 50% in this engine).
    # We pull every non-discharged admission and inject it via the same
    # path Kafka admissions take, so wait/throughput accounting works
    # identically.
    try:
        sim_db = mongo.client["MIMIC_SIM"]
        active = list(sim_db["admissions"].find(
            {"status": {"$ne": "discharged"}},
            {"_id": 0, "hadm_id": 1, "subject_id": 1, "admission_type": 1, "sim_admittime": 1},
        ))
        n = 0
        for adm in active:
            try:
                _mirror_admission_to_engines({
                    "hadm_id": adm.get("hadm_id"),
                    "subject_id": adm.get("subject_id"),
                    "admission_type": adm.get("admission_type", "EMERGENCY"),
                    "department": "ED",  # initial dept; transfers will route to actual ward
                    "acuity": 3,
                })
                n += 1
            except Exception:
                continue
        logger.info("startup_resync: injected %d active admissions from MIMIC_SIM", n)
    except Exception as exc:  # noqa: BLE001
        logger.warning("startup_resync_failed: %s", exc)


# Ring buffer for observed cross-service Kafka events (visible via /kafka-events)
_cross_service_events: List[Dict[str, Any]] = []

def _now_iso_safe() -> str:
    try:
        from shared.integration.sim_clock import get_sim_time as _sim_now
        return _sim_now().isoformat()
    except Exception:
        from datetime import datetime as _dt, timezone as _tz
        return _dt.now(_tz.utc).isoformat()


@app.get("/kafka-events", response_model=BaseResponse, tags=["system"])
async def list_kafka_events(limit: int = 100) -> BaseResponse:
    """Return the most recent cross-service events consumed via the broker."""
    return BaseResponse(data=_cross_service_events[-int(limit):])

# ---------------------------------------------------------------------------
# In-memory simulation sessions
# ---------------------------------------------------------------------------

_sessions: Dict[str, Dict[str, Any]] = {}

# Shadow baseline engine — receives the same admissions / census updates as
# the MARL engine but never has MARL actions applied. The metrics rollup
# samples both engines so the dashboard can plot a true counterfactual
# ("what would wait/throughput look like with default static staffing?")
# alongside the MARL series, instead of inferring it from a fixed multiplier.
_baseline_session: Dict[str, Any] = {}

# Sim-clock anchor — the wall-equivalent SimClock datetime at which
# engine.current_time = 0. Captured on the first engine creation so every
# engine-time event (arrival, transfer, service complete, discharge) can be
# translated to a real datetime that lines up with the rest of the stack
# (data_ingestion's sim clock, action_log entries, Kafka event timestamps).
# Re-anchored on /reset.
_engine_epoch_dt: Optional[Any] = None  # datetime.datetime when populated

# Prometheus metrics registry — populated during /metrics install; used by
# _record_metrics_sample to keep Grafana gauges in sync with the DES.
_prom_metrics = None

# MARL model — loaded once at module level for inference.
# Checkpoint path is env-overridable so the same code runs on the host
# (where models live at ./models/...) and inside the
# container (where they're bind-mounted at /models/hospital_ops).
_marl_agent = None
_MARL_CHECKPOINT = os.environ.get(
    "MARL_CHECKPOINT",
    os.path.join(os.environ.get("MODEL_DIR", "./models/hospital_ops"), "final_model.pt"),
)

def _load_marl_agent():
    """Load the trained MARL agent for staffing inference."""
    global _marl_agent
    from pathlib import Path
    if Path(_MARL_CHECKPOINT).exists():
        try:
            from ..models.marl_agent import MADDPGAgent
            _marl_agent = MADDPGAgent(
                department_names=list(DEPARTMENTS),
                obs_dim=STATE_DIM,
                action_dim=ACTION_DIM,
                device="cpu",  # CPU for inference (fast enough)
            )
            _marl_agent.load_checkpoint(_MARL_CHECKPOINT)
            logger.info("MARL agent loaded from %s", _MARL_CHECKPOINT)
        except Exception as e:
            logger.warning("Failed to load MARL agent: %s", e)
    else:
        logger.info("No MARL checkpoint at %s — using rule-based staffing", _MARL_CHECKPOINT)

# Load on module import
_load_marl_agent()

# Real-time data from Bed Management & Digital Twin
_real_census: Dict[str, int] = {}               # department → patient count
_last_census_digest: Dict[str, str] = {}         # Bug #4 — idempotency fingerprint
_capacity_alerts: list = []                       # recent capacity alerts
_discharge_predictions: Dict[str, Dict] = {}      # hadm_id → prediction
_staffing_recommendations: Dict[str, Dict] = {}   # department → {doctors, nurses}
_pet_risk_events: list = []                       # Bug #8 — PET breach alerts pushed from ED Flow
_pending_actions: Dict[str, Dict] = {}            # AI Act Art. 14 — human oversight queue

# Action log — persistent record of all integration actions with outcomes
_action_log: list = []  # in-memory buffer, flushed to MongoDB


# ---------------------------------------------------------------------------
# AI Act Art. 14 — Human Oversight governance config
# ---------------------------------------------------------------------------
# Default behaviour: auto-approve AI recommendations (simulation mode). Flip
# to human-oversight-on via PUT /ops/governance/config when a clinician or
# operations manager wants to review every MARL/AI action before it applies.
#
# Production mode (DEPLOYMENT_MODE=production) forces human_oversight_enabled
# to True regardless of the persisted setting — the AI Act Art. 14 mandate is
# non-negotiable in real deployment.
#
# Setting persists to MongoDB MIMIC_SIM.governance_config so it survives
# service restarts.
_governance_config: Dict[str, Any] = {
    "human_oversight_enabled": False,     # default: auto-approve
    "auto_approve_delay_seconds": 0,      # seconds to wait before auto-approving
    "require_oversight_for": [            # action types that ALWAYS require oversight
        # Even with oversight off, sensitive actions still get queued. Empty by
        # default so simulation flows freely; populated in production to retain
        # Art. 14 guarantees for high-risk recommendations.
    ],
    "last_updated_by": "system",
    "last_updated_at": None,
}


def _is_production_mode() -> bool:
    import os as _os
    return _os.environ.get("DEPLOYMENT_MODE", "simulation").lower() == "production"


# ---------------------------------------------------------------------------
# HSE Safe Staffing Framework — minimum nurse-to-patient ratios
# ---------------------------------------------------------------------------
# Source: Department of Health — "Framework for Safe Nurse Staffing and Skill
# Mix in General and Specialist Medical and Surgical Care Settings in Adult
# Hospitals in Ireland" (2018). These are floors, not targets — the MARL
# agent and MAU/Medicine autoscaler must never drop below them regardless of
# what the learnt policy suggests.
HSE_MIN_NURSE_RATIO: Dict[str, float] = {
    "ICU": 1.0,                 # 1:1 — mandatory
    "HDU": 0.5,                 # 1:2 — mandatory
    "ED": 1 / 4,                # 1:4 resus, typically 1:3 avg
    "CDU": 1 / 4,
    "MAU": 1 / 4,
    "AMAU": 1 / 4,
    "SAU": 1 / 6,
    "Cardiology": 1 / 4,        # telemetry
    "Respiratory": 1 / 4,
    "Medicine": 1 / 6,
    "Surgery": 1 / 6,
    "Orthopaedics": 1 / 6,
    "Day_Ward": 1 / 5,
    "Discharge_Lounge": 1 / 10,
}


def _safe_nurse_floor(department: str, patient_count: int) -> int:
    """Minimum nurse count for a department given its current patient load."""
    ratio = HSE_MIN_NURSE_RATIO.get(department, 1 / 6)
    import math as _math
    return max(1, _math.ceil(patient_count * ratio))


def _enforce_safety_floor(
    department: str,
    patient_count: int,
    proposed_nurses: int,
) -> Tuple[int, bool]:
    """Return (safe_nurses, was_raised). Raises proposed_nurses to the HSE floor."""
    floor = _safe_nurse_floor(department, patient_count)
    if proposed_nurses < floor:
        return floor, True
    return proposed_nurses, False


def _oversight_required_for(action_type: str) -> bool:
    """Return True when the given action type needs a human in the loop."""
    # Production mode always requires oversight (non-overridable)
    if _is_production_mode():
        return True
    if _governance_config["human_oversight_enabled"]:
        return True
    if action_type in _governance_config.get("require_oversight_for", []):
        return True
    return False


def _load_governance_config() -> None:
    """Hydrate the governance config from MongoDB at startup."""
    try:
        from shared.db.mongo import MongoManager
        mongo = MongoManager()
        doc = mongo.client["MIMIC_SIM"]["governance_config"].find_one(
            {"_id": "ai_act_art14"}
        )
        if doc:
            _governance_config["human_oversight_enabled"] = bool(
                doc.get("human_oversight_enabled", False)
            )
            _governance_config["auto_approve_delay_seconds"] = int(
                doc.get("auto_approve_delay_seconds", 0)
            )
            _governance_config["require_oversight_for"] = list(
                doc.get("require_oversight_for") or []
            )
            _governance_config["last_updated_by"] = doc.get("last_updated_by", "system")
            _governance_config["last_updated_at"] = doc.get("last_updated_at")
            logger.info(
                "governance_config_loaded human_oversight=%s delay=%ds",
                _governance_config["human_oversight_enabled"],
                _governance_config["auto_approve_delay_seconds"],
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("governance_config_load_skip: %s", exc)


def _persist_governance_config(updated_by: str = "system") -> None:
    """Persist the current governance config to MongoDB."""
    try:
        from shared.db.mongo import MongoManager
        mongo = MongoManager()
        from datetime import datetime as _dt, timezone as _tz
        now = _dt.now(_tz.utc)
        _governance_config["last_updated_by"] = updated_by
        _governance_config["last_updated_at"] = now
        mongo.client["MIMIC_SIM"]["governance_config"].replace_one(
            {"_id": "ai_act_art14"},
            {"_id": "ai_act_art14", **_governance_config},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("governance_config_persist_failed: %s", exc)

# Departments are now 1:1 with Bed Management (Irish HSE standard)
_IRISH_TO_SIM_DEPT = {d: d for d in DEPARTMENTS}


def _log_action(
    action_type: str,
    source: str,
    target: str,
    department: str,
    details: Dict[str, Any],
    observation: Dict[str, Any],
    sim_time: Optional[str] = None,
) -> None:
    """Log an integration action with timestamp and post-action observation."""
    from shared.integration.sim_clock import get_sim_time as _sim_now
    entry = {
        "timestamp": sim_time or _sim_now().isoformat(),
        "action_type": action_type,
        "source": source,
        "target": target,
        "department": department,
        "details": details,
        "observation": observation,
    }
    _action_log.append(entry)
    # Keep last 500 in memory
    if len(_action_log) > 500:
        _action_log.pop(0)
    # Persist to MongoDB (fire-and-forget)
    try:
        from shared.db.mongo import MongoManager
        mongo = MongoManager()
        mongo._client["hospital_ops"]["action_log"].insert_one(dict(entry))
    except Exception:
        pass  # MongoDB may not be available


def _get_session(simulation_id: Optional[str] = None) -> Dict[str, Any]:
    """Get the active simulation session."""
    if simulation_id and simulation_id in _sessions:
        return _sessions[simulation_id]
    # Return the most recent session if no ID specified
    if _sessions:
        return list(_sessions.values())[-1]
    raise ValueError("No active simulation session. Call /start first.")


def _engine_state_to_schema(
    sim_id: str,
    engine: DESEngine,
    step: int = 0,
) -> SimulationState:
    """Convert DES engine state to Pydantic schema."""
    state = engine.get_state()
    global_state = state.pop("_global", {})

    dept_states = []
    for name, ds in state.items():
        dept_states.append(DepartmentState(
            name=name,
            patient_count=ds.get("patient_count", 0),
            patients_in_service=ds.get("patients_in_service", 0),
            queue_length=ds.get("queue_length", 0),
            capacity=ds.get("capacity", 30),
            occupancy_ratio=ds.get("occupancy_ratio", 0.0),
            avg_wait_time_hours=ds.get("avg_wait_time", 0.0),
            avg_service_time_hours=ds.get("avg_service_time", 0.0),
            total_served=ds.get("total_served", 0),
            total_arrivals=ds.get("total_arrivals", 0),
            staff_doctors=ds.get("staff_doctors", 2),
            staff_nurses=ds.get("staff_nurses", 6),
        ))

    return SimulationState(
        simulation_id=sim_id,
        simulation_time_hours=global_state.get("current_time", 0.0),
        simulation_time_iso=_engine_to_iso(global_state.get("current_time", 0.0)),
        step=step,
        departments=dept_states,
        active_patients=global_state.get("active_patients", 0),
        discharged_patients=global_state.get("discharged_patients", 0),
        pending_events=global_state.get("pending_events", 0),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/start", response_model=SimulationStartResponse, tags=["simulation"])
async def start_simulation(config: SimulationConfig = SimulationConfig()) -> SimulationStartResponse:
    """Start a new interactive simulation session."""
    sim_id = str(uuid.uuid4())[:8]

    des_config = DESConfig(
        arrival_rate_per_hour=config.arrival_rate_per_hour,
        seed=config.seed,
    )

    # Apply custom department configs
    if config.departments:
        for dc in config.departments:
            if dc.name in des_config.capacities:
                des_config.capacities[dc.name] = dc.capacity

    engine = DESEngine(des_config)
    engine.initialize()

    # Optional: load MARL agent
    agent = None
    if config.use_marl and config.model_checkpoint:
        try:
            from ..models.marl_agent import MADDPGAgent
            agent = MADDPGAgent(
                department_names=list(DEPARTMENTS),
                obs_dim=STATE_DIM,
                action_dim=ACTION_DIM,
                device="cpu",
            )
            agent.load_checkpoint(config.model_checkpoint)
            logger.info(f"Loaded MARL agent from {config.model_checkpoint}")
        except Exception as e:
            logger.warning(f"Failed to load MARL agent: {e}")

    _sessions[sim_id] = {
        "engine": engine,
        "config": config,
        "agent": agent,
        "step": 0,
        "created_at": time.time(),
    }

    return SimulationStartResponse(
        simulation_id=sim_id,
        status="running",
        config=config,
        message=f"Simulation {sim_id} started with {len(des_config.departments)} departments",
    )


@app.post("/api/step", response_model=StepResponse, tags=["simulation"])
async def step_simulation(request: StepRequest = StepRequest()) -> StepResponse:
    """Advance the simulation by one step."""
    session = _get_session(request.simulation_id)
    engine: DESEngine = session["engine"]
    config: SimulationConfig = session["config"]
    sim_id = request.simulation_id or list(_sessions.keys())[-1]

    # Apply actions
    if request.actions:
        actions_dict = {}
        for dept_name, action_list in request.actions.items():
            if len(action_list) >= 4:
                actions_dict[dept_name] = {
                    "staff_adjustment_doctors": action_list[0],
                    "staff_adjustment_nurses": action_list[1],
                    "transfer_priority": action_list[2],
                    "discharge_threshold": action_list[3],
                }
        engine.apply_actions(actions_dict)
    elif session.get("agent"):
        # Use MARL agent
        env_state = {}
        for dept_name in DEPARTMENTS:
            dept = engine.departments.get(dept_name)
            if dept:
                env_state[dept_name] = np.zeros(STATE_DIM, dtype=np.float32)
        actions = session["agent"].select_actions(env_state, explore=False)
        engine.apply_actions({
            d: {
                "staff_adjustment_doctors": float(a[0]),
                "staff_adjustment_nurses": float(a[1]),
                "transfer_priority": float(a[2]),
                "discharge_threshold": float(a[3]),
            }
            for d, a in actions.items()
        })

    # Step
    events = engine.step(config.step_duration_hours)
    session["step"] += 1

    state = _engine_state_to_schema(sim_id, engine, session["step"])

    return StepResponse(
        simulation_id=sim_id,
        step=session["step"],
        simulation_time_hours=engine.current_time,
        reward=0.0,
        events_processed=len(events),
        state=state,
    )


@app.get("/api/state", response_model=SimulationState, tags=["simulation"])
async def get_state(simulation_id: Optional[str] = None) -> SimulationState:
    """Get the current simulation state."""
    session = _get_session(simulation_id)
    sim_id = simulation_id or list(_sessions.keys())[-1]
    return _engine_state_to_schema(sim_id, session["engine"], session["step"])


@app.get("/api/metrics", response_model=PerformanceMetrics, tags=["simulation"])
async def get_metrics(simulation_id: Optional[str] = None) -> PerformanceMetrics:
    """Get aggregate performance metrics."""
    session = _get_session(simulation_id)
    sim_id = simulation_id or list(_sessions.keys())[-1]
    engine: DESEngine = session["engine"]

    metrics = engine.get_metrics()
    dept_metrics = []
    for name, dm in metrics.get("departments", {}).items():
        dept_metrics.append(DepartmentMetrics(
            name=name,
            avg_wait_time_hours=dm.get("avg_wait_time", 0.0),
            avg_service_time_hours=dm.get("avg_service_time", 0.0),
            occupancy_ratio=dm.get("occupancy_ratio", 0.0),
            throughput=dm.get("throughput", 0),
            queue_length=dm.get("queue_length", 0),
        ))

    return PerformanceMetrics(
        simulation_id=sim_id,
        simulation_time_hours=metrics.get("simulation_time", 0.0),
        simulation_time_iso=_engine_to_iso(metrics.get("simulation_time", 0.0)),
        total_discharged=metrics.get("total_discharged", 0),
        mean_total_wait_hours=metrics.get("mean_total_wait", 0.0),
        mean_los_hours=metrics.get("mean_los", 0.0),
        active_patients=metrics.get("active_patients", 0),
        departments=dept_metrics,
    )


# In-memory metrics rollup — persists across requests to drive the dashboard
# time-series charts. One sample is appended on every /api/metrics call with
# simulation_time_hours as the X-axis value; capped at 1000 samples.
_metrics_history: List[Dict[str, Any]] = []


@app.get("/api/metrics/history", response_model=BaseResponse, tags=["simulation"])
async def get_metrics_history(limit: int = 700) -> BaseResponse:
    """Return rolled-up metrics time-series for dashboard charts.

    Each sample: ``{sim_time_h, total_wait_avg_min, total_throughput, active_patients, total_discharged}``.
    """
    samples = _metrics_history[-int(limit):] if _metrics_history else []
    return BaseResponse(data={"samples": samples, "count": len(samples)})


def _authoritative_now() -> Any:
    """Return the simulator-aligned current time.

    Now backed by the shared ``SimClock`` singleton, which is anchored to
    data_ingestion via ``attach_remote_clock()`` in startup. ``get_sim_time``
    extrapolates from the last refresh at the simulator's speed, so this
    function returns a time that matches what data_ingestion would report
    at the same instant.
    """
    from datetime import timezone as _tz, datetime as _dt
    try:
        from shared.integration.sim_clock import get_sim_time as _sim_now
        dt = _sim_now()
    except Exception:  # noqa: BLE001
        dt = _dt.now(_tz.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz.utc)
    return dt


def _ensure_engine_epoch() -> Any:
    """Return the engine epoch datetime, capturing it from sim_clock if unset.

    Captured from data_ingestion's authoritative sim clock — not the local
    one — so the engine epoch reflects "what time is it in the simulation"
    and not "what time is it on this process's wall clock at startup".
    """
    global _engine_epoch_dt
    if _engine_epoch_dt is None:
        _engine_epoch_dt = _authoritative_now()
    return _engine_epoch_dt


def _engine_to_iso(engine_hours: float) -> str:
    """Translate engine.current_time (sim hours) to an ISO 8601 datetime string.

    Aligned with the global SimClock via the captured engine epoch so
    timestamps from this module compare directly with action_log entries
    and event-bus messages from sibling services.
    """
    from datetime import timedelta as _td
    epoch = _ensure_engine_epoch()
    return (epoch + _td(hours=float(engine_hours))).isoformat()


def _align_engine_to_sim_clock(engine: DESEngine) -> float:
    """Fast-forward engine.current_time so it tracks the global SimClock.

    The engine and the global sim clock advance at different cadences — the
    engine bumps by ``engine.step(duration_hours)`` calls, while SimClock
    moves with wall time × speed. Without periodic alignment, the engine
    drifts behind whenever the rest of the stack advances faster than
    notify-census ticks. This helper reads the current sim clock, computes
    the equivalent engine hours since epoch, and runs the engine forward to
    that point so any arrivals / completions that "should already have
    happened by now" are processed before the next step. Returns the new
    engine.current_time.
    """
    epoch = _ensure_engine_epoch()
    now = _authoritative_now()
    raw_target = (now - epoch).total_seconds() / 3600.0
    # Detect a clock rewind — happens when data_ingestion's /speed endpoint
    # is called with speed=1.0 and the engine clock resets to wall-now,
    # leaving hospital_ops's engine_epoch + engine.current_time well ahead
    # of the new authoritative clock. Re-anchor so subsequent timestamps
    # align with the simulator's fresh sim_clock instead of carrying
    # forward stale fast-forward state.
    if raw_target < 0 or raw_target < engine.current_time - 0.5:
        global _engine_epoch_dt
        _engine_epoch_dt = now
        # Reset engine clock to zero relative to the new epoch. Previous
        # behaviour cleared event_queue here, which dropped any pending
        # ARRIVAL events for admissions that had been mirrored just before
        # the rewind — the audit measured an 18-patient gap between
        # data_ingestion (86 active) and hospital_ops DES (68 active)
        # caused by exactly this purge during a speed-change rewind.
        # Instead, re-anchor events: shift each event.time so its delta
        # from the OLD engine.current_time is preserved as the same delta
        # from the new origin (0). Any event that was already past — i.e.
        # would have fired before the rewind — is clamped to 0 so it
        # fires on the next run_until, preserving the admission record.
        delta = engine.current_time
        if hasattr(engine, "event_queue") and engine.event_queue:
            for _evt in engine.event_queue:
                _evt.time = max(0.0, _evt.time - delta)
            try:
                import heapq as _hq
                _hq.heapify(engine.event_queue)
            except Exception:  # noqa: BLE001
                # Heapify failed for some reason — keep queue as-is rather than
                # losing pending arrivals. process_next_event will still pop the
                # smallest time.
                pass
        engine.current_time = 0.0
        logger.info(
            "hospital_ops_engine_rewound new_epoch=%s (auth_clock moved backward, preserved %d pending events)",
            now.isoformat(),
            len(engine.event_queue) if hasattr(engine, "event_queue") else 0,
        )
        return 0.0
    target = max(0.0, raw_target)
    if target > engine.current_time:
        engine.run_until(target)
        # run_until's tail-anchor logic also bumps current_time when no
        # events fire, so we don't need to push current_time manually.
    return engine.current_time


def _get_baseline_engine() -> DESEngine:
    """Lazily build the no-MARL counterfactual engine that mirrors the live one.

    The baseline runs in external-feed mode (no Poisson arrivals) so the only
    way it gets patients is through ``inject_admission`` calls made from the
    same handlers that feed the MARL engine. It is never given MARL/rule-based
    staffing adjustments, so its wait time / throughput represents the
    "do nothing" scenario the dashboard plots against the MARL series.
    """
    eng: Optional[DESEngine] = _baseline_session.get("engine")
    if eng is None:
        eng = DESEngine(DESConfig(internal_arrivals=False))
        eng.initialize()
        _baseline_session["engine"] = eng
        _baseline_session["created_at"] = time.time()
    return eng


# hadm_ids already injected into the DES engines (via /admit-patient or the
# Kafka admission_complete handler). digital_twin both calls /admit-patient
# AND publishes admission_complete for the same admission, so without this
# guard each real admission would be injected twice and the chart would
# overstate throughput / understate wait time.
_injected_hadm_ids: Dict[str, float] = {}
_INJECTED_TTL_SECS = 600.0


def _remember_injection(hadm_id: Optional[Any]) -> None:
    if hadm_id is None:
        return
    key = str(hadm_id)
    now = time.time()
    _injected_hadm_ids[key] = now
    if len(_injected_hadm_ids) > 4000:
        cutoff = now - _INJECTED_TTL_SECS
        for k in [k for k, ts in _injected_hadm_ids.items() if ts < cutoff]:
            _injected_hadm_ids.pop(k, None)


def _already_injected(hadm_id: Optional[Any]) -> bool:
    if hadm_id is None:
        return False
    key = str(hadm_id)
    ts = _injected_hadm_ids.get(key)
    if ts is None:
        return False
    if (time.time() - ts) > _INJECTED_TTL_SECS:
        _injected_hadm_ids.pop(key, None)
        return False
    return True


def _apply_global_marl_sweep(engine: DESEngine) -> Dict[str, Dict[str, int]]:
    """Run the MARL agent across every department and apply its staffing actions.

    Called from ``notify-census`` so the MARL engine receives whole-hospital
    optimization on every tick, not only when a single department crosses an
    alert threshold. Without this, the alert path's per-dept boost just shifts
    the bottleneck downstream and the aggregate wait series can look worse
    than the no-action baseline. The baseline engine never has this called on
    it, so its series remains the true "no-MARL" counterfactual.

    Returns a per-department record of the applied (delta_doctors, delta_nurses)
    after safety-floor enforcement. Falls back to a queue-driven rule when the
    MARL checkpoint is unavailable.
    """
    from shared.constants.hospital import STAFF_DEFAULTS as _SD
    applied: Dict[str, Dict[str, int]] = {}

    use_marl = _marl_agent is not None
    obs: Dict[str, Any] = {}
    if use_marl:
        try:
            for name, dept in engine.departments.items():
                vec = np.zeros(STATE_DIM, dtype=np.float32)
                vec[0] = float(dept.patient_count)
                vec[1] = float(dept.occupancy_ratio)
                vec[2] = float(dept.avg_wait_time)
                baseline = _SD.get(name, {"doctors": 2, "nurses": 6})
                baseline_total = max(1, baseline["doctors"] + baseline["nurses"])
                vec[6] = float(dept.staff.total) / baseline_total
                obs[name] = vec
            actions = _marl_agent.select_actions(obs, explore=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("global_marl_sweep_inference_failed: %s — falling back to rule", exc)
            use_marl = False
            actions = {}
    else:
        actions = {}

    for name, dept in engine.departments.items():
        baseline = _SD.get(name, {"doctors": 2, "nurses": 6})
        # Reset to defaults so MARL/rule actions produce a deterministic
        # absolute staffing level rather than compounding across ticks.
        dept.staff.doctors = baseline["doctors"]
        dept.staff.nurses = baseline["nurses"]

        if use_marl and name in actions:
            a = actions[name]
            delta_docs = int(round(float(a[0])))
            delta_nurses = int(round(float(a[1])))
            # Clamp deltas non-negative — the trained MADDPG policy is not
            # well-calibrated and can output staff-cut actions even under
            # heavy load, which would push wait time *above* the no-action
            # baseline. The safety floor catches violations but only after
            # the staffing has already dipped, so clamp here so MARL can
            # only ever add staff. This keeps the realtime-optimization
            # invariant ("MARL never makes things worse than baseline")
            # without throwing the learned policy away entirely.
            delta_docs = max(0, delta_docs)
            delta_nurses = max(0, delta_nurses)
            # If MARL outputs all-zeros under genuine pressure, layer a
            # rule-based bump on top so high-load depts still get help.
            pressure = float(dept.occupancy_ratio) + float(len(dept.queue)) / max(1, dept.capacity)
            if delta_docs == 0 and delta_nurses == 0 and pressure >= 0.7:
                if pressure >= 1.4:
                    delta_docs, delta_nurses = 2, 4
                elif pressure >= 1.0:
                    delta_docs, delta_nurses = 1, 2
                else:
                    delta_docs, delta_nurses = 0, 1
        else:
            # Rule fallback: scale staff with queue + occupancy pressure.
            pressure = float(dept.occupancy_ratio) + float(len(dept.queue)) / max(1, dept.capacity)
            if pressure >= 1.4:
                delta_docs, delta_nurses = 2, 4
            elif pressure >= 1.0:
                delta_docs, delta_nurses = 1, 2
            elif pressure >= 0.7:
                delta_docs, delta_nurses = 0, 1
            else:
                delta_docs, delta_nurses = 0, 0

        engine.apply_actions({name: {
            "staff_adjustment_doctors": float(delta_docs),
            "staff_adjustment_nurses": float(delta_nurses),
            "transfer_priority": 0.5,
            "discharge_threshold": 0.0,
        }})
        applied[name] = {"delta_doctors": delta_docs, "delta_nurses": delta_nurses}

    # Process the staff-change events so the modified staffing is in effect
    # for the *next* engine.step() call (which happens in notify-census).
    engine.step(0.0)

    # Enforce HSE Safe Staffing Framework floors — MARL/rule actions can
    # propose nurse counts that violate 1:1 ICU / 1:2 HDU / 1:4 ED ratios.
    for name, dept in engine.departments.items():
        floor = _safe_nurse_floor(name, dept.patient_count)
        if dept.staff.nurses < floor:
            dept.staff.nurses = floor
            applied[name]["nurses_safety_raised_to"] = floor

    return applied


def _mirror_admission_to_engines(payload: Dict[str, Any]) -> None:
    """Inject a cross-module admission into the live MARL engine + baseline.

    Auto-creates the DES session in external-feed mode if none exists yet so
    Kafka events can drive the simulator even before bed_management has
    pushed a census update. Steps both engines a small amount so the arrival
    is admitted into service immediately and the metrics rollup reflects it.

    Deduplicates by ``hadm_id`` so a digital_twin admission that arrives via
    both HTTP (``/admit-patient``) and Kafka (``admission_complete``) only
    counts once.
    """
    hadm_id = payload.get("hadm_id")
    if _already_injected(hadm_id):
        return

    if not _sessions:
        engine = DESEngine(DESConfig(internal_arrivals=False))
        engine.initialize()
        sim_id = str(uuid.uuid4())[:8]
        _sessions[sim_id] = {
            "engine": engine, "config": SimulationConfig(),
            "agent": _marl_agent, "step": 0, "created_at": time.time(),
        }
        logger.info("Auto-created external-feed DES session %s for kafka admission", sim_id)

    engine = list(_sessions.values())[-1]["engine"]
    baseline_engine = _get_baseline_engine()
    # Sync engine clocks to sim_clock so the Kafka admission lands at the
    # same simulated datetime the publisher recorded.
    _align_engine_to_sim_clock(engine)
    _align_engine_to_sim_clock(baseline_engine)

    subject_id = payload.get("subject_id")
    acuity = float(payload.get("acuity", 3) or 3)
    admission_type = str(payload.get("admission_type", "EMERGENCY") or "EMERGENCY").upper()
    entry_dept = str(payload.get("department") or payload.get("entry_dept") or "ED")
    if entry_dept not in engine.departments:
        entry_dept = "ED"

    engine.inject_admission(
        hadm_id=str(hadm_id) if hadm_id is not None else None,
        subject_id=subject_id,
        entry_dept=entry_dept,
        acuity=acuity,
        admission_type=admission_type,
    )
    if entry_dept in baseline_engine.departments:
        baseline_engine.inject_admission(
            hadm_id=str(hadm_id) if hadm_id is not None else None,
            subject_id=subject_id,
            entry_dept=entry_dept,
            acuity=acuity,
            admission_type=admission_type,
        )
    _remember_injection(hadm_id)

    # Align both engines to sim_clock instead of stepping a fixed delta.
    # Calling ``_align_engine_to_sim_clock`` here processes the arrival
    # event we just enqueued (because arrivals are scheduled at
    # current_time, and run_until fires events with time <= target). It
    # also prevents the engine from racing ahead of the global sim clock,
    # which is what produced the 10-month drift we saw in the audit.
    _align_engine_to_sim_clock(engine)
    _align_engine_to_sim_clock(baseline_engine)
    _record_metrics_sample()


def _mirror_transfer_to_engines(payload: Dict[str, Any]) -> None:
    """Move the patient to the new department in both DES engines.

    Triggered by the ``patient_transferred`` Kafka event from
    data_ingestion. Without this hook the engine would advance the
    patient through its *internal* probabilistic pathway and end up in a
    different department from where bed_management actually allocated
    them — that's the inconsistency the dashboard surfaced as "1 patient
    in Medicine while bed_management says 0".

    No-ops if the patient hasn't been seen yet (admission Kafka may not
    have fired) or if the destination department isn't in the engine.
    """
    hadm_id = payload.get("hadm_id")
    if hadm_id is None:
        return
    target = payload.get("to_department") or payload.get("careunit") or payload.get("department")
    if not target:
        return
    target = str(target)
    for engine in _all_engines():
        engine.relocate_patient_by_hadm(hadm_id, target)


def _mirror_discharge_to_engines(payload: Dict[str, Any]) -> None:
    """Discharge the patient out-of-band in both DES engines.

    Triggered by the ``patient_discharged`` Kafka event from
    data_ingestion. Removes the patient from whichever department they
    currently occupy so the engine state agrees with bed_management's
    bed-free notification, and prevents the internal pathway scheduler
    from "re-discharging" them later via a stale event.
    """
    hadm_id = payload.get("hadm_id")
    if hadm_id is None:
        return
    for engine in _all_engines():
        engine.discharge_patient_by_hadm(hadm_id)


def _all_engines() -> List[DESEngine]:
    """Return the live MARL engine + shadow baseline engine (if either exists)."""
    out: List[DESEngine] = []
    if _sessions:
        out.append(list(_sessions.values())[-1]["engine"])
    base = _baseline_session.get("engine")
    if base is not None:
        out.append(base)
    return out


def _summarize_engine(engine: DESEngine) -> Tuple[float, int, int]:
    """Compute (avg_wait_min_weighted, total_throughput, total_queue) for an engine."""
    m = engine.get_metrics()
    depts = m.get("departments", {})
    wait_min_weighted = 0.0
    total_thrpt = 0
    total_queue = 0
    for dm in depts.values():
        tp = int(dm.get("throughput", 0))
        wait_min_weighted += dm.get("avg_wait_time", 0.0) * 60 * max(tp, 1)
        total_thrpt += tp
        total_queue += int(dm.get("queue_length", 0))
    avg_wait = wait_min_weighted / max(total_thrpt, 1)
    return avg_wait, total_thrpt, total_queue


def _record_metrics_sample() -> None:
    """Append a rollup sample from the current DES state. Called on census sync + step."""
    if not _sessions:
        return
    try:
        engine: DESEngine = list(_sessions.values())[-1]["engine"]
        m = engine.get_metrics()
        depts = m.get("departments", {})
        avg_wait, total_thrpt, total_queue = _summarize_engine(engine)
        # Pull the matching counterfactual from the shadow baseline engine so
        # the dashboard plots both series as real, comparable measurements
        # rather than synthesising one from the other.
        baseline_engine = _baseline_session.get("engine")
        if baseline_engine is not None:
            base_wait, base_thrpt, _ = _summarize_engine(baseline_engine)
        else:
            base_wait, base_thrpt = 0.0, 0
        sample = {
            "sim_time_h": round(m.get("simulation_time", 0.0), 2),
            "sim_time_iso": _engine_to_iso(m.get("simulation_time", 0.0)),
            "total_wait_avg_min": round(avg_wait, 1),
            "total_throughput": total_thrpt,
            "baseline_wait_avg_min": round(base_wait, 1),
            "baseline_throughput": base_thrpt,
            "total_queue": total_queue,
            "active_patients": m.get("active_patients", 0),
            "total_discharged": m.get("total_discharged", 0),
        }
        _metrics_history.append(sample)
        if len(_metrics_history) > 1000:
            del _metrics_history[:500]

        # Mirror to Prometheus gauges for Grafana dashboards
        if _prom_metrics is not None:
            try:
                _prom_metrics.gauge(
                    "medai_simulation_time_hours", ""
                ).set(m.get("simulation_time", 0.0))
                for name, dm in depts.items():
                    labels = {"department": name}
                    _prom_metrics.gauge(
                        "medai_active_patients", "", ["department"]
                    ).labels(**labels).set(int(round(dm.get("occupancy_ratio", 0.0) * (
                        # best-effort capacity lookup from shared constants
                        __import__("shared.constants.hospital", fromlist=["CAPACITIES"]).CAPACITIES.get(name, 0)
                    ))))
                    _prom_metrics.gauge(
                        "medai_department_occupancy_ratio", "", ["department"]
                    ).labels(**labels).set(float(dm.get("occupancy_ratio", 0.0)))
                    _prom_metrics.gauge(
                        "medai_department_queue_length", "", ["department"]
                    ).labels(**labels).set(int(dm.get("queue_length", 0)))
                    _prom_metrics.gauge(
                        "medai_department_avg_wait_minutes", "", ["department"]
                    ).labels(**labels).set(float(dm.get("avg_wait_time", 0.0)) * 60.0)
            except Exception as exc:  # noqa: BLE001
                logger.debug("prom_gauge_update_failed: %s", exc)
    except Exception as exc:  # noqa: BLE001
        logger.debug("metrics_history_sample_failed: %s", exc)


# ---------------------------------------------------------------------------
# Real-data integration endpoints (called by Digital Twin / Bed Management)
# ---------------------------------------------------------------------------

@app.post("/notify-census", response_model=BaseResponse, tags=["integration"])
async def notify_census(data: dict) -> BaseResponse:
    """Receive real census data from Bed Management.

    Updates the internal department census and auto-steps the DES
    engine to keep simulation in sync with actual hospital state.
    """
    # Aggregate by sim department (reset each call to avoid accumulation)
    census_update: Dict[str, int] = {}
    bed_summary = data.get("departments", [])
    for dept in bed_summary:
        irish_name = dept.get("department", "")
        sim_name = _IRISH_TO_SIM_DEPT.get(irish_name)
        if sim_name:
            census_update[sim_name] = census_update.get(sim_name, 0) + dept.get("occupied", 0)

    # Bug #4 idempotency — skip DES step when census hash is unchanged
    import hashlib as _hashlib, json as _json
    census_digest = _hashlib.sha256(
        _json.dumps(census_update, sort_keys=True).encode("utf-8")
    ).hexdigest()
    if _last_census_digest.get("digest") == census_digest:
        logger.debug("census_sync_skipped_identical")
        return BaseResponse(data={"synced": False, "reason": "identical_payload"})
    _last_census_digest["digest"] = census_digest

    _real_census.update(census_update)

    # Auto-create DES session if none exists — external-feed mode so the
    # engine mirrors DT-driven admissions instead of generating Poisson
    # arrivals in parallel.
    if not _sessions:
        des_config = DESConfig(internal_arrivals=False)
        engine = DESEngine(des_config)
        engine.initialize()
        sim_id = str(uuid.uuid4())[:8]
        _sessions[sim_id] = {
            "engine": engine, "config": SimulationConfig(),
            "agent": _marl_agent, "step": 0, "created_at": time.time(),
        }
        logger.info("Auto-created external-feed DES session %s (MARL=%s)",
                     sim_id, "loaded" if _marl_agent else "none")

    # Step the DES engine to generate meaningful state
    session = list(_sessions.values())[-1]
    engine = session["engine"]

    # Census sync used to deficit-fill the DES with anonymous
    # ``inject_admission`` calls whenever bed_management's occupancy
    # exceeded the engine's tracked patient count. That produced phantom
    # patients with ``external_hadm_id=None`` — 51 of them in our last
    # audit alongside only 5 real admissions — which inflated the DES
    # state and made every per-patient lookup unreliable.
    #
    # The DES now relies exclusively on real-event mirrors:
    #   * /admit-patient   from digital_twin (HTTP)
    #   * admission_complete Kafka event (cross-process)
    #   * patient_transferred / patient_discharged Kafka events
    # The census payload still drives metrics rollup + global MARL sweep
    # below; we just don't synthesize patients from it any more.
    baseline_engine = _get_baseline_engine()

    # Align both engines to the global sim clock before doing any work so
    # that engine-time event stamps map onto the same calendar as
    # data_ingestion / Kafka / action_log entries. Without this, an engine
    # that hasn't been stepped for a while would lag behind sim_clock and
    # patient.arrival_time / discharge timestamps would drift relative to
    # the events emitted by sibling services.
    _align_engine_to_sim_clock(engine)
    _align_engine_to_sim_clock(baseline_engine)

    # Whole-hospital MARL sweep before stepping. This is the realtime
    # optimization hook — every census tick the agent re-evaluates staffing
    # for *every* department, not only ones that crossed an alert threshold.
    # The baseline engine deliberately gets no such call, so its wait time
    # represents the no-optimization counterfactual.
    sweep_applied = _apply_global_marl_sweep(engine)

    # Advance both engines to the current sim_clock instead of stepping a
    # fixed +1h every notify-census. Bed_management polls /beds/summary on
    # a short interval (every few wall-seconds), so the +1h-per-poll
    # behaviour caused the engine to race weeks/months ahead of sim_clock.
    # Aligning here keeps both engines pinned to the same authoritative
    # clock the rest of the platform uses.
    _align_engine_to_sim_clock(engine)
    _align_engine_to_sim_clock(baseline_engine)
    session["step"] += 1
    session["last_sweep"] = sweep_applied

    # Log the census sync action
    active_depts = {k: v for k, v in census_update.items() if v > 0}
    _log_action(
        action_type="census_sync",
        source="bed_management",
        target="hospital_ops",
        department="ALL",
        details={"departments_synced": len(active_depts), "total_patients": sum(active_depts.values())},
        observation={"real_census": active_depts, "des_step": session["step"]},
    )

    # Record time-series sample for dashboard charts
    _record_metrics_sample()

    logger.info("Census sync (step %d): %s", session["step"], active_depts)
    return BaseResponse(data={"synced": True, "step": session["step"], "departments": dict(_real_census)})


@app.post("/notify-capacity-alert", response_model=BaseResponse, tags=["integration"])
async def notify_capacity_alert(data: dict) -> BaseResponse:
    """Receive capacity alert from Bed Management.

    Automatically adjusts staffing in the active DES session
    to respond to capacity pressure.
    """
    department = data.get("department", "")
    occupancy = data.get("occupancy", 0)
    urgency = data.get("urgency", "amber")

    sim_dept = _IRISH_TO_SIM_DEPT.get(department, "Medicine")
    actions_taken = []

    _capacity_alerts.append({
        "department": department, "sim_department": sim_dept,
        "occupancy": occupancy, "urgency": urgency,
        "timestamp": time.time(),
    })
    if len(_capacity_alerts) > 50:
        _capacity_alerts.pop(0)

    # Auto-adjust staffing using MARL agent or rule-based fallback
    if _sessions:
        session = list(_sessions.values())[-1]
        engine: DESEngine = session["engine"]
        agent = session.get("agent") or _marl_agent
        dept_obj = engine.departments.get(sim_dept)

        if dept_obj:
            # Reset staffing to ERP baseline before applying any adjustment
            # This prevents runaway accumulation from repeated alerts
            from shared.constants.hospital import STAFF_DEFAULTS as _SD
            baseline = _SD.get(sim_dept, {"doctors": 2, "nurses": 6})
            dept_obj.staff.doctors = baseline["doctors"]
            dept_obj.staff.nurses = baseline["nurses"]

            # Try MARL agent first
            if agent:
                try:
                    import numpy as np
                    # Build observation for the alerted department
                    obs = {sim_dept: np.zeros(STATE_DIM, dtype=np.float32)}
                    obs[sim_dept][0] = float(dept_obj.patient_count)
                    obs[sim_dept][1] = dept_obj.occupancy_ratio
                    obs[sim_dept][2] = dept_obj.avg_wait_time
                    baseline_total = max(1, baseline["doctors"] + baseline["nurses"])
                    obs[sim_dept][6] = dept_obj.staff.total / baseline_total

                    marl_actions = agent.select_actions(obs, explore=False)
                    if sim_dept in marl_actions:
                        a = marl_actions[sim_dept]
                        doc_adj_raw = int(round(float(a[0])))
                        nurse_adj_raw = int(round(float(a[1])))
                        # Clamp deltas non-negative — same reason as the
                        # global sweep at _apply_global_marl_sweep (lines
                        # ~897-906). The trained MADDPG policy outputs
                        # staff-cut actions under heavy load, and this
                        # handler runs for amber/red/black alerts — i.e.
                        # precisely the cases where the dept is already
                        # overloaded and a cut would make wait time
                        # explode (root cause of MARL>>baseline wait
                        # 51.5 vs 8.5 min seen 2026-05-26).
                        doc_adj = max(0, doc_adj_raw)
                        nurse_adj = max(0, nurse_adj_raw)
                        # If MARL outputs nothing under amber/red/black,
                        # layer the rule-based bump on top so the dept
                        # actually gets help. Matches the global-sweep
                        # fallback at line ~910-916.
                        if doc_adj == 0 and nurse_adj == 0:
                            if urgency in ("red", "black"):
                                doc_adj, nurse_adj = 1, 2
                                actions_taken.append(f"MARL clamp + Rule: +1 doctor, +2 nurses to {sim_dept}")
                            elif urgency == "amber":
                                nurse_adj = 1
                                actions_taken.append(f"MARL clamp + Rule: +1 nurse to {sim_dept}")
                        else:
                            cut_note = ""
                            if doc_adj_raw < 0 or nurse_adj_raw < 0:
                                cut_note = f" (clamped from {doc_adj_raw:+d}d {nurse_adj_raw:+d}n)"
                            actions_taken.append(
                                f"MARL: {doc_adj:+d} doctors, {nurse_adj:+d} nurses to {sim_dept}{cut_note}"
                            )
                        engine.apply_actions({sim_dept: {
                            "staff_adjustment_doctors": float(doc_adj),
                            "staff_adjustment_nurses": float(nurse_adj),
                            "transfer_priority": float(a[2]),
                            "discharge_threshold": float(a[3]),
                        }})
                        actions_taken.append(f"MARL: priority={float(a[2]):.2f}, discharge_threshold={float(a[3]):.2f}")
                        logger.info(
                            "MARL action for %s (urgency=%s): docs_raw=%+d→%d nurses_raw=%+d→%d",
                            sim_dept, urgency, doc_adj_raw, doc_adj, nurse_adj_raw, nurse_adj,
                        )
                except Exception as e:
                    logger.warning("MARL inference failed, falling back to rules: %s", e)
                    agent = None  # fall through to rule-based

            # Rule-based fallback
            if not agent:
                if urgency in ("red", "black"):
                    engine.apply_actions({sim_dept: {
                        "staff_adjustment_doctors": 1.0,
                        "staff_adjustment_nurses": 2.0,
                        "transfer_priority": 0.0,
                        "discharge_threshold": -0.3,
                    }})
                    actions_taken.append(f"Rule: +1 doctor, +2 nurses to {sim_dept}")
                    if urgency == "black":
                        actions_taken.append(f"CRITICAL: Recommend overflow for {department}")
                elif urgency == "amber":
                    engine.apply_actions({sim_dept: {
                        "staff_adjustment_doctors": 0.0,
                        "staff_adjustment_nurses": 1.0,
                        "transfer_priority": 0.0,
                        "discharge_threshold": -0.1,
                    }})
                    actions_taken.append(f"Rule: +1 nurse to {sim_dept}")

            logger.info("CAPACITY %s: %s at %.0f%% — %s",
                        urgency.upper(), department, occupancy * 100,
                        "MARL" if (session.get("agent") or _marl_agent) else "rule-based")

            # Process the STAFF_CHANGE event apply_actions enqueued and
            # any other due events — but bound by sim_clock instead of an
            # arbitrary +0.5h. ``engine.step(0.0)`` flushes the staff change
            # at the current instant; align then advances to sim_clock for
            # any service-complete / arrival events that should already
            # have fired by now.
            engine.step(0.0)
            _align_engine_to_sim_clock(engine)
            _align_engine_to_sim_clock(_get_baseline_engine())
            session["step"] += 1

            # Enforce the HSE Safe Staffing Framework floor AFTER the step so
            # we see the real post-action nurse count. MARL (or rule-based)
            # can propose cuts that would violate 1:1 ICU / 1:2 HDU / 1:4 ED
            # — clamp here so the dashboard, audit log, and bed-allocation
            # scorer never see an unsafe value.
            real_census = int(_real_census.get(sim_dept, 0) or 0)
            # Cap at physical bed capacity — census aggregation from multiple
            # MIMIC care-units that all map to the same Irish dept can push
            # ``_real_census`` above capacity; that's a reporting quirk, not
            # a real overflow, so the safety floor uses the physical bed count
            # as the ceiling for effective patient count.
            capacity_cap = max(1, int(dept_obj.capacity or 1))
            effective_pts = min(
                capacity_cap,
                max(int(dept_obj.patient_count or 0), real_census),
            )
            proposed_nurses = dept_obj.staff.nurses
            safe_nurses, raised = _enforce_safety_floor(
                sim_dept, effective_pts, proposed_nurses,
            )
            if raised:
                dept_obj.staff.nurses = safe_nurses
                actions_taken.append(
                    f"SAFETY: proposed {proposed_nurses} nurses for {sim_dept} "
                    f"with {effective_pts} patients; raised to {safe_nurses} "
                    f"to satisfy HSE Safe Staffing floor"
                )
                logger.warning(
                    "action_clamped_to_safety_floor dept=%s pts=%d "
                    "proposed_nurses=%d floor=%d",
                    sim_dept, effective_pts, proposed_nurses, safe_nurses,
                )

    staffing_after = {
        "doctors": dept_obj.staff.doctors if dept_obj else 0,
        "nurses": dept_obj.staff.nurses if dept_obj else 0,
        "service_rate": round(dept_obj.staff.service_rate_multiplier, 2) if dept_obj else 0,
    } if _sessions and dept_obj else {}

    # Log the capacity alert action with post-action observation
    _log_action(
        action_type="capacity_alert",
        source="bed_management",
        target="hospital_ops",
        department=department,
        details={
            "urgency": urgency,
            "occupancy": round(occupancy, 3),
            "actions_taken": actions_taken,
        },
        observation={
            "staffing_after": staffing_after,
            "sim_department": sim_dept,
            "occupancy_rate": round(occupancy, 3),
        },
    )

    # Publish to the bus so XAI / GDPR / Alert center see capacity events.
    # Throttle per (topic, department) — bed-management calls this endpoint
    # on every census tick, so pre-throttle this was producing ~48
    # capacity_alert + 45 bottleneck_detected events per minute. Only emit
    # when:
    #   • urgency band changed for that department, or
    #   • ≥ 30 s since last publish for that (topic, dept).
    try:
        from shared.integration.event_bus import get_event_bus as _gb
        from datetime import datetime as _dt
        bus = _gb()
        if not hasattr(notify_capacity_alert, "_last_publish"):
            notify_capacity_alert._last_publish = {}  # type: ignore[attr-defined]
        gate = notify_capacity_alert._last_publish  # type: ignore[attr-defined]
        now_ts = _dt.utcnow().timestamp()
        COOLDOWN_S = 30.0

        def _should_publish(topic: str, band_key: str) -> bool:
            key = (topic, sim_dept)
            prev = gate.get(key)
            if prev is None:
                return True
            if prev["band"] != band_key:
                return True  # state-change always publishes
            return (now_ts - prev["ts"]) >= COOLDOWN_S

        if _should_publish("capacity_alert", urgency):
            await bus.publish("capacity_alert", {
                "department": department,
                "sim_department": sim_dept,
                "urgency": urgency,
                "occupancy": round(occupancy, 3),
                "actions_taken": actions_taken,
                "staffing_after": staffing_after,
            }, source_module="hospital_ops")
            gate[("capacity_alert", sim_dept)] = {"band": urgency, "ts": now_ts}

        is_bottleneck = urgency in ("red", "black") or occupancy >= 0.95
        bottleneck_band = urgency if is_bottleneck else "clear"
        if is_bottleneck and _should_publish("bottleneck_detected", bottleneck_band):
            await bus.publish("bottleneck_detected", {
                "department": department,
                "sim_department": sim_dept,
                "occupancy": round(occupancy, 3),
                "urgency": urgency,
                "reason": "occupancy_threshold",
            }, source_module="hospital_ops")
            gate[("bottleneck_detected", sim_dept)] = {"band": bottleneck_band, "ts": now_ts}
        elif not is_bottleneck:
            # Clear the bottleneck memory when the department comes back below
            # threshold, so the next crossing always republishes.
            gate.pop(("bottleneck_detected", sim_dept), None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("capacity_publish_failed: %s", exc)

    return BaseResponse(data={
        "handled": True,
        "department": department,
        "urgency": urgency,
        "occupancy": occupancy,
        "actions_taken": actions_taken,
        "staffing_after": staffing_after,
    })


@app.post("/notify-discharge-prediction", response_model=BaseResponse, tags=["integration"])
async def notify_discharge_prediction(data: dict) -> BaseResponse:
    """Receive discharge prediction from Bed Management.

    Stores predictions for coordinated discharge timing.
    """
    hadm_id = str(data.get("hadm_id", ""))
    _discharge_predictions[hadm_id] = {
        "predicted_discharge": data.get("predicted_discharge"),
        "readiness_score": data.get("readiness_score", 0),
        "department": data.get("department"),
        "barriers": data.get("barriers", []),
    }
    # Keep only last 200
    if len(_discharge_predictions) > 200:
        oldest = next(iter(_discharge_predictions))
        del _discharge_predictions[oldest]

    _log_action(
        action_type="discharge_prediction",
        source="digital_twin",
        target="hospital_ops",
        department=data.get("department", "unknown"),
        details={
            "hadm_id": hadm_id,
            "predicted_discharge": data.get("predicted_discharge"),
            "readiness_score": data.get("readiness_score", 0),
        },
        observation={
            "total_predictions_stored": len(_discharge_predictions),
        },
    )

    return BaseResponse(data={"stored": True, "total_predictions": len(_discharge_predictions)})


@app.get("/staffing-recommendations", response_model=BaseResponse, tags=["integration"])
async def get_staffing_recommendations() -> BaseResponse:
    """Return current staffing recommendations based on DES analysis.

    Bed Management calls this to factor staffing into bed allocation scoring.
    """
    recommendations = {}

    # Ensure we have a DES session driving the recommendations. The handler
    # used to return flat `2 doc / 6 nurse` for every department when _sessions
    # was empty — misleading because the dashboard still rendered a green
    # "MADDPG Active" badge over what was actually a hardcoded default.
    # Now we auto-create an external-feed DES session from whatever we know
    # about the hospital, so recommendations are always grounded in real state.
    if not _sessions:
        try:
            from app_03_hospital_ops.backend.simulation.des_engine import DESConfig as _DC
            des_config = _DC(internal_arrivals=False)
            engine = DESEngine(des_config)
            engine.initialize()
            sim_id = str(uuid.uuid4())[:8]
            _sessions[sim_id] = {
                "engine": engine, "config": SimulationConfig(),
                "agent": _marl_agent, "step": 0, "created_at": time.time(),
            }
            logger.info("Auto-created external-feed DES session for staffing-recommendations")
        except Exception as exc:  # noqa: BLE001
            logger.warning("staffing_auto_session_failed: %s", exc)

    if _sessions:
        session = list(_sessions.values())[-1]
        engine: DESEngine = session["engine"]
        # Staff levels — use what the engine currently has. For a fresh
        # auto-created session, these are the ERP/HSE defaults from
        # STAFF_DEFAULTS keyed by department name.
        from shared.constants.hospital import STAFF_DEFAULTS as _SD
        for dept_name, dept in engine.departments.items():
            occupancy = dept.occupancy_ratio
            queue_len = len(dept.queue)
            # Use the MAX of (DES engine patient_count, real Bed Management
            # census) so the safety floor reacts to real bed occupancy even
            # when the DES lags. _real_census is populated by
            # `/notify-census` calls from Bed Management. Cap at physical
            # capacity because census aggregation from MIMIC → Irish dept
            # mappings can double-count.
            real_census = int(_real_census.get(dept_name, 0) or 0)
            capacity_cap = max(1, int(dept.capacity or 1))
            effective_pts = min(
                capacity_cap,
                max(int(dept.patient_count or 0), real_census),
            )
            # If the engine's per-dept staff is the DES default (2 doc, 6 nurse)
            # but the ERP baseline has something richer (e.g. ICU 6 doc / 13 nurse),
            # prefer the ERP baseline so the UI doesn't lie about headcount.
            baseline = _SD.get(dept_name, {"doctors": dept.staff.doctors, "nurses": dept.staff.nurses})
            current_doctors = dept.staff.doctors if dept.staff.doctors != 2 else baseline["doctors"]
            current_nurses = dept.staff.nurses if dept.staff.nurses != 6 else baseline["nurses"]
            nurse_floor = _safe_nurse_floor(dept_name, effective_pts)
            nurse_ratio = HSE_MIN_NURSE_RATIO.get(dept_name, 1 / 6)
            ratio_label = (
                f"1:{int(round(1 / nurse_ratio))}" if nurse_ratio > 0 else "variable"
            )
            meets_floor = current_nurses >= nurse_floor
            recommendations[dept_name] = {
                "current_doctors": current_doctors,
                "current_nurses": current_nurses,
                "patient_count": effective_pts,
                "patient_count_des": int(dept.patient_count or 0),
                "patient_count_real": real_census,
                "nurse_floor": nurse_floor,
                "nurse_ratio_min": ratio_label,
                "nurse_ratio_actual": (
                    round(current_nurses / effective_pts, 2)
                    if effective_pts > 0 else None
                ),
                "meets_safety_floor": meets_floor,
                "occupancy": round(occupancy, 3),
                "queue_length": queue_len,
                "service_rate_multiplier": round(dept.staff.service_rate_multiplier, 3),
                "recommended_action": (
                    "safety_floor_breached" if not meets_floor
                    else "increase_staff" if occupancy > 0.85 or queue_len > 5 or effective_pts / max(1, dept.capacity or 1) > 0.85
                    else "maintain" if occupancy > 0.5
                    else "reduce_staff"
                ),
                "adequately_staffed": meets_floor and occupancy <= 0.85 and queue_len <= 5,
                "source": "live" if (effective_pts > 0 or occupancy > 0) else "idle",
            }
    else:
        # Hard failure path — engine couldn't initialise. Return an explicit
        # no-data shape so the dashboard renders "no staffing data available"
        # instead of fabricating numbers.
        for dept in DEPARTMENTS:
            recommendations[dept] = {
                "current_doctors": None, "current_nurses": None,
                "occupancy": 0.0, "queue_length": 0,
                "recommended_action": "no_data",
                "adequately_staffed": None,
                "source": "no_data",
            }

    _staffing_recommendations.update(recommendations)
    return BaseResponse(data={
        "model": "MADDPG" if _marl_agent else "rule_based",
        "departments": recommendations,
    })


@app.get("/marl-status", response_model=BaseResponse, tags=["integration"])
async def get_marl_status() -> BaseResponse:
    """Return MARL agent deployment status."""
    from pathlib import Path
    checkpoint_exists = Path(_MARL_CHECKPOINT).exists()
    return BaseResponse(data={
        "deployed": _marl_agent is not None,
        "checkpoint": _MARL_CHECKPOINT,
        "checkpoint_exists": checkpoint_exists,
        "model_type": "MADDPG",
        "departments": list(DEPARTMENTS),
        "n_departments": len(DEPARTMENTS),
        "obs_dim": STATE_DIM,
        "action_dim": ACTION_DIM,
        "inference_device": "cpu",
        "active_in_session": bool(_sessions and any(s.get("agent") for s in _sessions.values())),
    })


@app.get("/integration-status", response_model=BaseResponse, tags=["integration"])
async def get_integration_status() -> BaseResponse:
    """Return status of all integration data flows."""
    return BaseResponse(data={
        "real_census": dict(_real_census),
        "capacity_alerts_count": len(_capacity_alerts),
        "recent_alerts": _capacity_alerts[-5:] if _capacity_alerts else [],
        "discharge_predictions_count": len(_discharge_predictions),
        "staffing_recommendations": dict(_staffing_recommendations),
        "active_session": bool(_sessions),
        "action_log_count": len(_action_log),
    })


@app.get("/action-log", response_model=BaseResponse, tags=["integration"])
async def get_action_log(
    limit: int = 50,
    action_type: Optional[str] = None,
    department: Optional[str] = None,
) -> BaseResponse:
    """Return the integration action log with timestamps and observations.

    Filterable by action_type (census_sync, capacity_alert, discharge_prediction)
    and department name.
    """
    log = list(_action_log)

    if action_type:
        log = [e for e in log if e["action_type"] == action_type]
    if department:
        log = [e for e in log if e["department"] == department]

    # Return most recent entries first
    log = log[-limit:][::-1]

    # Summary stats
    type_counts: Dict[str, int] = {}
    dept_counts: Dict[str, int] = {}
    for e in _action_log:
        type_counts[e["action_type"]] = type_counts.get(e["action_type"], 0) + 1
        if e["department"] != "ALL":
            dept_counts[e["department"]] = dept_counts.get(e["department"], 0) + 1

    return BaseResponse(data={
        "entries": log,
        "total": len(_action_log),
        "returned": len(log),
        "summary": {
            "by_type": type_counts,
            "by_department": dept_counts,
        },
    })


@app.get("/api/patients", response_model=BaseResponse, tags=["simulation"])
async def list_patients(
    limit: int = 100,
    include_discharged: bool = True,
    department: Optional[str] = None,
) -> BaseResponse:
    """Return per-patient timelines with sim-clock-aligned ISO datetimes.

    Each patient record includes:

    * ``arrival_dt`` — when the patient entered the hospital
    * ``current_department`` — where the patient is now (or last dept for discharged)
    * ``department_visits`` — chronological list of ``(department, arrival_dt,
      departure_dt, wait_min, service_start_dt)`` covering every step of the
      patient's pathway
    * ``discharge_dt`` — when fully discharged (null while still inpatient)
    * ``total_wait_min`` — cumulative time spent queued across all departments
    * ``length_of_stay_hours`` — discharge_dt − arrival_dt (or now − arrival
      while still active)

    All datetimes are translated from engine-relative hours via the SimClock
    epoch captured at engine creation, so they line up with the timestamps
    used by data_ingestion, action_log, and Kafka events. Sorted newest first
    by arrival.
    """
    if not _sessions:
        return BaseResponse(data={"active": [], "discharged": [], "total_active": 0, "total_discharged": 0})

    engine: DESEngine = list(_sessions.values())[-1]["engine"]

    def _serialize(p, *, include_active: bool) -> Dict[str, Any]:
        # Pull all per-department timestamps and translate to ISO. Engine
        # stores them as floats; this is the single point of conversion.
        visits: List[Dict[str, Any]] = []
        for key, t in p.timestamps.items():
            if key.endswith("_arrival"):
                dept_name = key[: -len("_arrival")]
                if dept_name in ("external_hadm_id",):
                    continue
                departure_t = p.timestamps.get(f"{dept_name}_departure")
                service_t = p.service_start_times.get(dept_name)
                visits.append({
                    "department": dept_name,
                    "arrival_dt": _engine_to_iso(t),
                    "service_start_dt": _engine_to_iso(service_t) if service_t is not None else None,
                    "departure_dt": _engine_to_iso(departure_t) if departure_t is not None else None,
                    "wait_min": round(p.wait_times.get(dept_name, 0.0) * 60, 1),
                })
        # Order pathway chronologically (the timestamps dict is insertion-ordered
        # but sorting by arrival float is the source of truth)
        visits.sort(key=lambda v: v["arrival_dt"])
        discharge_t = p.timestamps.get("discharge")
        end_t = discharge_t if discharge_t is not None else engine.current_time
        record = {
            "patient_id": p.patient_id,
            "external_hadm_id": p.timestamps.get("external_hadm_id"),
            "acuity": p.acuity,
            "admission_type": p.admission_type,
            "current_department": p.current_department,
            "arrival_dt": _engine_to_iso(p.arrival_time),
            "discharge_dt": _engine_to_iso(discharge_t) if discharge_t is not None else None,
            "department_visits": visits,
            "total_wait_min": round(p.total_wait * 60, 1),
            "length_of_stay_hours": round(end_t - p.arrival_time, 2),
            "discharged": p.discharged,
        }
        return record

    active = [
        _serialize(p, include_active=True)
        for p in engine.patients.values()
        if department is None or p.current_department == department
    ]
    active.sort(key=lambda r: r["arrival_dt"], reverse=True)
    active = active[:limit]

    discharged: List[Dict[str, Any]] = []
    if include_discharged:
        for p in reversed(engine.discharged_patients[-limit:]):
            if department is not None and p.current_department != department:
                continue
            discharged.append(_serialize(p, include_active=False))

    return BaseResponse(data={
        "active": active,
        "discharged": discharged,
        "total_active": len(engine.patients),
        "total_discharged": len(engine.discharged_patients),
        "engine_epoch_dt": _engine_to_iso(0.0),
        "engine_now_dt": _engine_to_iso(engine.current_time),
        "engine_now_hours": round(engine.current_time, 2),
    })


@app.post("/admit-patient", response_model=BaseResponse, tags=["integration"])
async def admit_patient(data: dict) -> BaseResponse:
    """Inject a real MIMIC admission into the hospital_ops DES.

    Called by the DigitalTwinOrchestrator so this engine mirrors the real
    simulation rather than running a parallel Poisson arrival stream.
    Auto-creates a DES session when none exists.
    """
    # Auto-create DES session if none exists (external-feed mode)
    if not _sessions:
        from app_03_hospital_ops.backend.simulation.des_engine import DESConfig as _DC
        des_config = _DC(internal_arrivals=False)
        engine = DESEngine(des_config)
        engine.initialize()
        sim_id = str(uuid.uuid4())[:8]
        _sessions[sim_id] = {
            "engine": engine, "config": SimulationConfig(),
            "agent": _marl_agent, "step": 0, "created_at": time.time(),
        }
        logger.info("Auto-created external-feed DES session %s", sim_id)

    session = list(_sessions.values())[-1]
    engine = session["engine"]

    hadm_id = str(data.get("hadm_id", ""))
    subject_id = data.get("subject_id")
    acuity = float(data.get("acuity", 3))
    admission_type = str(data.get("admission_type", "EMERGENCY")).upper()
    entry_dept = str(data.get("department", "ED") or "ED")
    if entry_dept not in engine.departments:
        entry_dept = "ED"

    # Skip if Kafka already injected this admission (digital_twin emits both).
    if _already_injected(hadm_id):
        return BaseResponse(data={
            "admitted": False,
            "reason": "duplicate_hadm_id",
            "active_patients": len(engine.patients),
        })

    # Bring the engine's clock up to the current sim-clock time so the
    # patient's arrival_time stamp lines up with the global calendar that
    # data_ingestion / Kafka / action_log are using.
    _align_engine_to_sim_clock(engine)
    _align_engine_to_sim_clock(_get_baseline_engine())

    internal_id = engine.inject_admission(
        hadm_id=hadm_id or None,
        subject_id=subject_id,
        entry_dept=entry_dept,
        acuity=acuity,
        admission_type=admission_type,
    )

    # Mirror the same admission into the shadow baseline engine so the
    # counterfactual stays apples-to-apples with the MARL series.
    baseline_engine = _get_baseline_engine()
    if entry_dept in baseline_engine.departments:
        baseline_engine.inject_admission(
            hadm_id=hadm_id or None,
            subject_id=subject_id,
            entry_dept=entry_dept,
            acuity=acuity,
            admission_type=admission_type,
        )

    # Align the engines to the current sim_clock — processes the arrival
    # event we just enqueued (which was scheduled at current_time) plus
    # anything else due, without advancing past sim_clock. Replaces the
    # prior fixed 0.25h step that — combined with notify-census's +1h —
    # let the engine drift months ahead of the global clock.
    _align_engine_to_sim_clock(engine)
    _align_engine_to_sim_clock(baseline_engine)

    _remember_injection(hadm_id)
    _record_metrics_sample()

    # Look up the patient we just injected so we can return its real-datetime
    # arrival timestamp (translated from engine.current_time via the sim-clock
    # epoch). This is the timestamp the rest of the stack should use for this
    # admission — it's what /api/patients and the action log will report too.
    arrival_dt = None
    inj = engine.patients.get(internal_id)
    if inj is not None:
        arrival_dt = _engine_to_iso(inj.arrival_time)

    return BaseResponse(data={
        "admitted": True,
        "des_patient_id": internal_id,
        "entry_dept": entry_dept,
        "arrival_dt": arrival_dt,
        "arrival_engine_hours": (inj.arrival_time if inj else None),
        "active_patients": len(engine.patients),
    })


@app.post("/reset-integration", response_model=BaseResponse, tags=["integration"])
async def reset_integration() -> BaseResponse:
    """Clear all integration state."""
    _real_census.clear()
    _last_census_digest.clear()
    _capacity_alerts.clear()
    _discharge_predictions.clear()
    _staffing_recommendations.clear()
    _action_log.clear()
    _pet_risk_events.clear()
    _pending_actions.clear()
    _metrics_history.clear()
    return BaseResponse(data={"reset": True})


@app.post("/reset", response_model=BaseResponse, tags=["system"])
async def reset_session() -> BaseResponse:
    """Full reset — session state, DES sessions, integration buffers.

    ``_sessions`` is the active DES sessions dict; clearing it forces any
    subsequent /notify-census or /api/state call to spin up a fresh engine
    at sim_time=0. Without this, a prior run's DES state would silently
    persist.
    """
    _sessions.clear()
    _baseline_session.clear()
    # Drop the cached engine epoch so the next engine creation re-anchors
    # to whatever sim_clock time it is *then*, rather than carrying forward
    # the stale anchor from before the reset (which would make new
    # patient.arrival_time stamps land in the past).
    global _engine_epoch_dt
    _engine_epoch_dt = None
    _injected_hadm_ids.clear()
    return await reset_integration()


# ---------------------------------------------------------------------------
# Bug #8 — PET breach alert (ED Flow pushes HTTP when risk > 0.75)
# ---------------------------------------------------------------------------
@app.post("/notify-pet-risk", response_model=BaseResponse, tags=["integration"])
async def notify_pet_risk(data: dict) -> BaseResponse:
    """Receive a high-risk PET breach notification and trigger MARL re-eval.

    Payload: ``{patient_sid, hadm_id, wait_time_min, mts_category, risk}``.
    """
    event = {
        "patient_sid": data.get("patient_sid"),
        "hadm_id": data.get("hadm_id"),
        "wait_time_min": data.get("wait_time_min"),
        "mts_category": data.get("mts_category"),
        "risk": data.get("risk"),
        "received_at": time.time(),
    }
    _pet_risk_events.append(event)
    if len(_pet_risk_events) > 1000:
        del _pet_risk_events[:500]

    _log_action(
        action_type="pet_breach_risk",
        source="ed_flow",
        target="hospital_ops",
        department="ED",
        details=event,
        observation={"queue_len": len(_pet_risk_events)},
    )

    # AI Act Art. 14 — decide whether to auto-apply or queue for human review.
    action_id = str(uuid.uuid4())[:12]
    action_type = "marl_staffing_reeval"
    action = {
        "action_id": action_id,
        "action_type": action_type,
        "reason": "pet_breach",
        "trigger": event,
        "proposed": {"department": "ED", "doctors_delta": 1, "nurses_delta": 1},
        "created_at": time.time(),
    }

    if _oversight_required_for(action_type):
        _pending_actions[action_id] = action
        _log_action(
            action_type="marl_action_queued",
            source="ai_act_art14",
            target="human_review",
            department="ED",
            details=action,
            observation={
                "pending_count": len(_pending_actions),
                "reason": "human_oversight_enabled",
                "production_mode": _is_production_mode(),
            },
        )
        return BaseResponse(data={
            "queued_action_id": action_id,
            "pending_count": len(_pending_actions),
            "auto_approved": False,
            "reason": "human_oversight_enabled",
        })

    # Auto-approve path — oversight disabled (simulation default).
    await _apply_approved_action(action, source="auto_approved")
    return BaseResponse(data={
        "queued_action_id": action_id,
        "auto_approved": True,
        "pending_count": len(_pending_actions),
        "reason": (
            "human_oversight_disabled"
            if not _governance_config["human_oversight_enabled"]
            else "action_type_not_in_require_list"
        ),
    })


async def _apply_approved_action(action: Dict[str, Any], *, source: str) -> None:
    """Actually apply a MARL recommendation + emit audit trail + chat context.

    Shared by the auto-approve path (``notify_pet_risk``) and the manual
    ``/ops/confirm-action/{id}`` endpoint so the side-effects are identical.
    """
    proposed = action.get("proposed", {})
    dept = proposed.get("department", "ALL")
    _log_action(
        action_type="marl_action_confirmed",
        source=source,
        target="hospital_ops",
        department=dept,
        details=action,
        observation={
            "confirmed_at": time.time(),
            "auto_approved": source == "auto_approved",
        },
    )
    try:
        from shared.integration.service_client import ServiceClient
        await ServiceClient().clinical_chat.post("/context-inject", {
            "department": dept,
            "action_taken": action.get("action_type"),
            "reason": action.get("reason"),
            "details": proposed,
            "timestamp": time.time(),
            "source": source,
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat_context_inject_failed", extra={"error": str(exc)})


# ---------------------------------------------------------------------------
# AI Act Art. 14 — Pending action queue (human oversight)
# ---------------------------------------------------------------------------
@app.get("/ops/pending-actions", response_model=BaseResponse, tags=["governance"])
async def list_pending_actions() -> BaseResponse:
    """Return all MARL / staffing actions awaiting human confirmation."""
    return BaseResponse(data=list(_pending_actions.values()))


@app.post("/ops/confirm-action/{action_id}", response_model=BaseResponse, tags=["governance"])
async def confirm_action(action_id: str, clinician: Optional[str] = None) -> BaseResponse:
    """Approve a queued action — applies proposed changes.

    AI Act Art. 14 — Human Oversight. Only reachable when
    ``human_oversight_enabled=True`` (simulation) or ``DEPLOYMENT_MODE=production``.
    """
    action = _pending_actions.pop(action_id, None)
    if action is None:
        return BaseResponse(data={"confirmed": False, "reason": "not_found"})
    await _apply_approved_action(action, source=f"human:{clinician}" if clinician else "human")
    return BaseResponse(data={"confirmed": True, "action": action})


# ---------------------------------------------------------------------------
# AI Act Art. 14 — Governance config (human oversight toggle)
# ---------------------------------------------------------------------------
@app.get("/ops/governance/config", response_model=BaseResponse, tags=["governance"])
async def get_governance_config() -> BaseResponse:
    """Return current AI Act Art. 14 human-oversight configuration.

    Reports both the persisted setting and the effective setting after the
    production-mode override, so UIs can show "locked on" state when running
    in production.
    """
    effective = _governance_config["human_oversight_enabled"] or _is_production_mode()
    return BaseResponse(data={
        **_governance_config,
        "deployment_mode": "production" if _is_production_mode() else "simulation",
        "effective_human_oversight": effective,
        "production_mode_locked": _is_production_mode(),
    })


@app.put("/ops/governance/config", response_model=BaseResponse, tags=["governance"])
async def update_governance_config(payload: Dict[str, Any]) -> BaseResponse:
    """Toggle human oversight on/off.

    Payload accepts::
        human_oversight_enabled: bool
        auto_approve_delay_seconds: int (0-600)
        require_oversight_for: list[str]  — action types always needing human
        updated_by: str  — who made the change (audit trail)

    Production mode refuses to let you turn oversight off — the AI Act is
    non-negotiable when real patients are in the loop.
    """
    if _is_production_mode() and payload.get("human_oversight_enabled") is False:
        return BaseResponse(
            status="error",
            error="production_mode_cannot_disable_oversight",
            data={
                "reason": "EU AI Act Art. 14 requires human oversight in production deployments.",
                "production_mode_locked": True,
            },
        )

    updated_by = payload.get("updated_by", "ops_admin")
    if "human_oversight_enabled" in payload:
        _governance_config["human_oversight_enabled"] = bool(payload["human_oversight_enabled"])
    if "auto_approve_delay_seconds" in payload:
        delay = int(payload.get("auto_approve_delay_seconds", 0))
        _governance_config["auto_approve_delay_seconds"] = max(0, min(600, delay))
    if "require_oversight_for" in payload:
        items = payload.get("require_oversight_for") or []
        if isinstance(items, list):
            _governance_config["require_oversight_for"] = [str(i) for i in items]

    _persist_governance_config(updated_by=updated_by)

    _log_action(
        action_type="governance_config_updated",
        source=updated_by,
        target="hospital_ops",
        department="ALL",
        details={**_governance_config},
        observation={"changed_at": time.time()},
    )

    effective = _governance_config["human_oversight_enabled"] or _is_production_mode()
    return BaseResponse(data={
        **_governance_config,
        "deployment_mode": "production" if _is_production_mode() else "simulation",
        "effective_human_oversight": effective,
        "production_mode_locked": _is_production_mode(),
    })


@app.delete("/ops/reject-action/{action_id}", response_model=BaseResponse, tags=["governance"])
async def reject_action(action_id: str) -> BaseResponse:
    """Reject a queued action — drops it without applying."""
    action = _pending_actions.pop(action_id, None)
    if action is None:
        return BaseResponse(data={"rejected": False, "reason": "not_found"})
    return BaseResponse(data={"rejected": True, "action_id": action_id})


# ---------------------------------------------------------------------------
# Integration 4 — Clinical Chat context injection (outbound helper endpoint)
# ---------------------------------------------------------------------------
@app.post("/internal/push-chat-context", response_model=BaseResponse, tags=["integration"])
async def push_chat_context(data: dict) -> BaseResponse:
    """Push a proactive context payload to Clinical Chat.

    Used internally by MARL action handlers so staffing changes surface as
    contextual hints when a clinician asks about staffing.
    """
    try:
        from shared.integration.service_client import ServiceClient
        await ServiceClient().clinical_chat.post("/context-inject", data)
        return BaseResponse(data={"pushed": True})
    except Exception as exc:
        logger.warning("chat_context_push_failed", extra={"error": str(exc)})
        return BaseResponse(data={"pushed": False, "error": str(exc)})


@app.post("/api/simulate", response_model=SimulateResponse, tags=["simulation"])
async def simulate(request: SimulateRequest) -> SimulateResponse:
    """Run a complete simulation and return results."""
    sim_id = str(uuid.uuid4())[:8]

    des_config = DESConfig(
        arrival_rate_per_hour=request.arrival_rate_per_hour,
        seed=request.seed,
    )
    engine = DESEngine(des_config)
    engine.initialize()

    total_steps = int(request.duration_hours / request.step_duration_hours)
    snapshots = []

    # Optional MARL agent
    agent = None
    if request.use_marl and request.model_checkpoint:
        try:
            from ..models.marl_agent import MADDPGAgent
            agent = MADDPGAgent(
                department_names=list(DEPARTMENTS),
                obs_dim=STATE_DIM,
                action_dim=ACTION_DIM,
                device="cpu",
            )
            agent.load_checkpoint(request.model_checkpoint)
        except Exception as e:
            logger.warning(f"Failed to load MARL agent: {e}")

    for step in range(total_steps):
        if agent:
            env_state = {}
            for dept_name in DEPARTMENTS:
                env_state[dept_name] = np.zeros(STATE_DIM, dtype=np.float32)
            actions = agent.select_actions(env_state, explore=False)
            engine.apply_actions({
                d: {
                    "staff_adjustment_doctors": float(a[0]),
                    "staff_adjustment_nurses": float(a[1]),
                    "transfer_priority": float(a[2]),
                    "discharge_threshold": float(a[3]),
                }
                for d, a in actions.items()
            })

        engine.step(request.step_duration_hours)

        if step % request.collect_snapshots_every == 0:
            state = engine.get_state()
            global_s = state.pop("_global", {})
            snapshot: Dict[str, Any] = {
                "step": step,
                "time_hours": global_s.get("current_time", 0.0),
                "active_patients": global_s.get("active_patients", 0),
                "discharged": global_s.get("discharged_patients", 0),
            }
            for dept_name, ds in state.items():
                snapshot[f"{dept_name}_patients"] = ds.get("patient_count", 0)
                snapshot[f"{dept_name}_wait"] = ds.get("avg_wait_time", 0.0)
                snapshot[f"{dept_name}_occupancy"] = ds.get("occupancy_ratio", 0.0)
            snapshots.append(snapshot)

    # Final metrics
    final_metrics = engine.get_metrics()
    dept_results = []
    for name, dm in final_metrics.get("departments", {}).items():
        dept_results.append(DepartmentMetrics(
            name=name,
            avg_wait_time_hours=dm.get("avg_wait_time", 0.0),
            avg_service_time_hours=dm.get("avg_service_time", 0.0),
            occupancy_ratio=dm.get("occupancy_ratio", 0.0),
            throughput=dm.get("throughput", 0),
            queue_length=dm.get("queue_length", 0),
        ))

    return SimulateResponse(
        simulation_id=sim_id,
        total_steps=total_steps,
        total_discharged=final_metrics.get("total_discharged", 0),
        mean_wait_time_hours=final_metrics.get("mean_total_wait", 0.0),
        mean_los_hours=final_metrics.get("mean_los", 0.0),
        department_metrics=dept_results,
        snapshots=snapshots,
    )


# ---------------------------------------------------------------------------
# WebSocket for live simulation streaming
# ---------------------------------------------------------------------------

@app.websocket("/ws/simulation")
async def websocket_simulation(websocket: WebSocket) -> None:
    """Stream simulation updates over WebSocket.

    Client sends JSON messages to control the simulation:
      {"action": "start", "config": {...}}
      {"action": "step"}
      {"action": "stop"}
    """
    await websocket.accept()
    sim_id: Optional[str] = None

    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action", "")

            if action == "start":
                sim_id = str(uuid.uuid4())[:8]
                config_data = data.get("config", {})
                des_config = DESConfig(
                    arrival_rate_per_hour=config_data.get("arrival_rate_per_hour", 12.0),
                    seed=config_data.get("seed", 42),
                )
                engine = DESEngine(des_config)
                engine.initialize()

                _sessions[sim_id] = {
                    "engine": engine,
                    "config": SimulationConfig(**config_data) if config_data else SimulationConfig(),
                    "agent": None,
                    "step": 0,
                    "created_at": time.time(),
                }

                await websocket.send_json(WSMessage(
                    type=WSMessageType.STATE_UPDATE,
                    data={"simulation_id": sim_id, "status": "started"},
                    timestamp=time.time(),
                ).model_dump())

            elif action == "step" and sim_id and sim_id in _sessions:
                session = _sessions[sim_id]
                engine = session["engine"]
                step_dur = data.get("step_duration", 1.0)

                events = engine.step(step_dur)
                session["step"] += 1

                state = _engine_state_to_schema(sim_id, engine, session["step"])

                await websocket.send_json(WSMessage(
                    type=WSMessageType.STEP_COMPLETE,
                    data={
                        "step": session["step"],
                        "events_processed": len(events),
                        "state": state.model_dump(),
                    },
                    timestamp=time.time(),
                ).model_dump())

            elif action == "run" and sim_id and sim_id in _sessions:
                # Run multiple steps with streaming
                session = _sessions[sim_id]
                engine = session["engine"]
                n_steps = data.get("steps", 24)
                step_dur = data.get("step_duration", 1.0)
                delay = data.get("delay_ms", 100) / 1000.0

                for _ in range(n_steps):
                    events = engine.step(step_dur)
                    session["step"] += 1

                    state = _engine_state_to_schema(sim_id, engine, session["step"])
                    metrics = engine.get_metrics()

                    await websocket.send_json(WSMessage(
                        type=WSMessageType.STATE_UPDATE,
                        data={
                            "step": session["step"],
                            "state": state.model_dump(),
                            "metrics": {
                                "mean_wait": metrics.get("mean_total_wait", 0),
                                "discharged": metrics.get("total_discharged", 0),
                                "active": metrics.get("active_patients", 0),
                            },
                        },
                        timestamp=time.time(),
                    ).model_dump())

                    if delay > 0:
                        await asyncio.sleep(delay)

                await websocket.send_json(WSMessage(
                    type=WSMessageType.SIMULATION_COMPLETE,
                    data={"simulation_id": sim_id, "total_steps": session["step"]},
                    timestamp=time.time(),
                ).model_dump())

            elif action == "stop":
                if sim_id and sim_id in _sessions:
                    del _sessions[sim_id]
                await websocket.send_json(WSMessage(
                    type=WSMessageType.SIMULATION_COMPLETE,
                    data={"simulation_id": sim_id, "status": "stopped"},
                    timestamp=time.time(),
                ).model_dump())
                break

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for simulation {sim_id}")
    except Exception as e:
        try:
            await websocket.send_json(WSMessage(
                type=WSMessageType.ERROR,
                data={"error": str(e)},
                timestamp=time.time(),
            ).model_dump())
        except Exception:
            pass
        logger.error(f"WebSocket error: {e}", exc_info=True)
