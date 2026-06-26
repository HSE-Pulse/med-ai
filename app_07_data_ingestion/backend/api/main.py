"""FastAPI application for the MIMIC-based hospital simulation engine.

Provides REST endpoints for controlling the simulation (start, stop,
speed, reset) and querying state (active patients, department census,
recent events, collection stats).  A WebSocket endpoint at ``/ws``
streams events in real time.

Run with:
    uvicorn app_07_data_ingestion.backend.api.main:app --host 0.0.0.0 --port 8207
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Query, WebSocket, WebSocketDisconnect

from shared.api.base import create_app

# Ensure project root is on sys.path so shared.* is importable
_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from shared.db.mongo import MongoManager  # noqa: E402
from shared.integration.digital_twin import DigitalTwinOrchestrator  # noqa: E402
from shared.constants.mimic import (  # noqa: E402
    VITAL_ITEMID_TO_SHORT as _VITAL_SHORT,
    VITAL_ITEM_IDS as _VITAL_IDS,
    LAB_ITEMID_TO_NAME as _LAB_NAMES,
    LAB_ITEM_IDS as _LAB_IDS,
    SOFA_LAB_IDS as _SOFA_LAB_IDS,
)
from shared.clinical.risk import compute_sofa as _shared_compute_sofa  # noqa: E402
from shared.clinical.risk import rule_based_acuity  # noqa: E402
from shared.constants.hospital import map_department as _map_dept, CAPACITIES as _DEPT_CAPACITIES  # noqa: E402

from ..engine.sim_clock import SimClock  # noqa: E402
from ..engine.patient_generator import PatientGenerator  # noqa: E402
from ..engine.event_engine import HospitalEventEngine  # noqa: E402
from ..engine.ehr_writer import EHRWriter  # noqa: E402
from .schemas import (  # noqa: E402
    ActivePatientsResponse,
    CollectionStatsResponse,
    DepartmentCensusResponse,
    HealthResponse,
    MessageResponse,
    ResetRequest,
    SimStateResponse,
    SpeedRequest,
)

# ── logging ──────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("sim-api")

# ── FastAPI app ──────────────────────────────────────────────────────

app = create_app(
    title="Data Ingestion Simulator",
    description="MIMIC-based hospital simulation engine with time acceleration",
    version="0.1.0",
)

# ── singleton components ─────────────────────────────────────────────

mongo: Optional[MongoManager] = None
engine: Optional[HospitalEventEngine] = None
ehr_writer: Optional[EHRWriter] = None
digital_twin: Optional[DigitalTwinOrchestrator] = None

# Server-side metrics history — persists across page navigations AND restarts
# (Mongo-backed via ``MIMIC_SIM.metrics_history`` collection)
_metrics_history: list = []
_metrics_prev_discharges: int = 0
_sim_start_hours: float = 0  # Set when first metrics point recorded

# Hourly arrival tracking per department (for dynamic arrival heatmap)
# Key: dept name, Value: list of 24 ints (arrivals per hour of day)
_arrival_counts: dict = {}  # {dept: [0]*24}
_arrival_total_events: int = 0

# MongoDB collection names for durable metrics history
_METRICS_COLL = "metrics_history"
_METRICS_META_COLL = "metrics_history_meta"   # stores _sim_start_hours + last discharge count


def _metrics_coll():
    if mongo is None:
        return None
    try:
        return mongo.client["MIMIC_SIM"][_METRICS_COLL]
    except Exception:
        return None


def _metrics_meta_coll():
    if mongo is None:
        return None
    try:
        return mongo.client["MIMIC_SIM"][_METRICS_META_COLL]
    except Exception:
        return None


def _persist_metrics_point(point: Dict[str, Any]) -> None:
    coll = _metrics_coll()
    if coll is None:
        return
    try:
        coll.insert_one(dict(point))
    except Exception as exc:  # noqa: BLE001
        logger.debug("metrics_persist_failed: %s", exc)


def _persist_metrics_meta() -> None:
    coll = _metrics_meta_coll()
    if coll is None:
        return
    try:
        coll.replace_one(
            {"_id": "metrics_history_meta"},
            {
                "_id": "metrics_history_meta",
                "sim_start_hours": _sim_start_hours,
                "last_prev_discharges": _metrics_prev_discharges,
            },
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("metrics_meta_persist_failed: %s", exc)


def _load_metrics_history_from_mongo() -> None:
    """Rehydrate ``_metrics_history`` + meta from Mongo at startup."""
    global _sim_start_hours, _metrics_prev_discharges
    coll = _metrics_coll()
    if coll is None:
        return
    try:
        docs = list(coll.find({}, {"_id": 0}).sort("sim_hours", 1).limit(5000))
        _metrics_history[:] = docs
        meta_coll = _metrics_meta_coll()
        if meta_coll is not None:
            meta = meta_coll.find_one({"_id": "metrics_history_meta"})
            if meta:
                _sim_start_hours = float(meta.get("sim_start_hours", 0) or 0)
                _metrics_prev_discharges = int(meta.get("last_prev_discharges", 0) or 0)
        logger.info(
            "metrics_history restored %d points (sim_start_hours=%.2f)",
            len(_metrics_history), _sim_start_hours,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("metrics_history_restore_failed: %s", exc)


def _purge_metrics_history_from_mongo() -> None:
    """Drop the persisted metrics — called on simulation reset."""
    for coll in (_metrics_coll(), _metrics_meta_coll()):
        if coll is not None:
            try:
                coll.delete_many({})
            except Exception as exc:  # noqa: BLE001
                logger.debug("metrics_purge_failed: %s", exc)


async def _record_metrics_loop():
    """Background task: record metrics every 5 seconds while sim is running."""
    global _metrics_prev_discharges
    while True:
        await asyncio.sleep(5)
        try:
            if not engine or not engine.running:
                continue
            state_data = engine.get_state()
            stats = state_data.get("stats", {})
            total_discharged = stats.get("total_discharges", 0)
            total_admitted = stats.get("total_admissions", 0)
            active = state_data.get("active_patients", 0)
            sim_time = state_data.get("sim_time", "")

            sim_hours = 0
            if sim_time:
                try:
                    from datetime import datetime
                    st = datetime.fromisoformat(sim_time)
                    sim_hours = round(st.timestamp() / 3600, 2)
                except (ValueError, TypeError):
                    pass

            new_discharges = max(0, total_discharged - _metrics_prev_discharges)
            _metrics_prev_discharges = total_discharged

            if sim_hours > 0:
                global _sim_start_hours
                if _sim_start_hours == 0:
                    _sim_start_hours = sim_hours
                relative_hours = round(sim_hours - _sim_start_hours, 2)
            if sim_hours > 0 and (not _metrics_history or _metrics_history[-1]["sim_hours"] != relative_hours):
                point = {
                    "sim_hours": relative_hours,
                    "total_admitted": total_admitted,
                    "total_discharged": total_discharged,
                    "active_patients": active,
                    "new_discharges": new_discharges,
                    "sim_time": sim_time,
                }
                _metrics_history.append(point)
                _persist_metrics_point(point)
                _persist_metrics_meta()
                if len(_metrics_history) > 2000:
                    _metrics_history.pop(0)
        except Exception:
            pass


@app.on_event("startup")
async def startup() -> None:
    global mongo, engine, ehr_writer, digital_twin

    # Structured JSON logging + Loki push
    try:
        from shared.integration.logging_config import setup_logging
        setup_logging(service_name="data_ingestion")
    except Exception as exc:  # noqa: BLE001
        logger.warning("logging_setup_failed: %s", exc)

    # OpenTelemetry tracing — must run before any other instrumented work
    # so every subsequent span is captured. No-op when OTEL_EXPORTER_OTLP_ENDPOINT
    # is unset.
    try:
        from shared.integration.tracing import setup_tracing
        setup_tracing(app, service_name="data_ingestion")
    except Exception as exc:  # noqa: BLE001
        logger.warning("tracing_setup_failed: %s", exc)

    # Prometheus /metrics for Grafana scraping
    try:
        from shared.integration.prometheus_metrics import install_metrics
        install_metrics(app, service_name="data_ingestion")
    except Exception as exc:  # noqa: BLE001
        logger.warning("prometheus_metrics_install_failed: %s", exc)

    mongo = MongoManager()
    clock = SimClock(speed=5.0)  # default 5x acceleration — 10x saturated data_ingestion CPU + caused dt-propagation timeouts
    generator = PatientGenerator(mongo)

    # Attach MongoDB to the event bus so every publication lands in
    # MIMIC_SIM.event_log — durable record of every simulation event from start.
    # Also brings up the Kafka broker when KAFKA_BOOTSTRAP is set.
    from shared.integration.event_bus import get_event_bus
    try:
        bus = get_event_bus()
        bus.attach_mongo(mongo.client)
        await bus.startup()
        logger.info("Event bus started — events will persist to MIMIC_SIM.event_log")
    except Exception as exc:  # noqa: BLE001
        logger.warning("event_bus_startup_failed: %s", exc)

    # Initialize Digital Twin orchestrator — propagates events to all AI modules
    digital_twin = DigitalTwinOrchestrator()

    # Attach durable outbox so every DT publish survives Kafka/Mongo blips.
    # A background relay re-publishes any event stuck in 'pending' state.
    try:
        from shared.integration.outbox import Outbox
        _outbox = Outbox(mongo.client, service_id="digital_twin")
        _outbox.ensure_indexes()
        digital_twin.attach_outbox(_outbox)
        asyncio.create_task(_outbox.relay_forever(digital_twin.bus, interval_seconds=15))
        app.state._outbox = _outbox  # expose for /outbox-stats endpoint
        logger.info("Outbox attached to DigitalTwinOrchestrator with relay")
    except Exception as exc:  # noqa: BLE001
        logger.warning("outbox_attach_failed: %s", exc)

    engine = HospitalEventEngine(mongo, clock, generator, digital_twin=digital_twin)
    ehr_writer = EHRWriter(mongo)
    ehr_writer.setup_collections()

    # Restore metrics history from MongoDB so the Wait-Time and Throughput
    # charts survive service restarts — the "since sim begin" data the user
    # sees on /hospital-ops is built from _metrics_history.
    _load_metrics_history_from_mongo()

    logger.info("Simulation API ready on port 8207 with Digital Twin orchestrator.")

    # Start background metrics recorder (asyncio imported at module level)
    asyncio.create_task(_record_metrics_loop())

    # Pre-warm WiredTiger cache for the dashboard's hot-path queries so the
    # first /icu-board / /ed-board hit after a service restart isn't a
    # 15-20s cold-cache stall (otherwise users perceive the page as hung).
    asyncio.create_task(_prewarm_mongo_cache())


async def _prewarm_mongo_cache() -> None:
    """Touch the collections + indexes the dashboard hits so WiredTiger
    pulls them into RAM before the first user request."""
    try:
        sim_db = mongo.client["MIMIC_SIM"]
        # Active admissions — small but indexed-by-status; loads catalog page.
        list(sim_db["admissions"].find({"status": {"$ne": "discharged"}}, {"_id": 0}).limit(500))
        # Transfers latest-per-patient — same aggregation /icu-board uses.
        list(sim_db["transfers"].aggregate([
            {"$match": {}},
            {"$sort": {"intime": -1}},
            {"$group": {"_id": "$hadm_id", "careunit": {"$first": "$careunit"}}},
            {"$limit": 500},
        ]))
        # Chartevents latest-vital-per-patient — biggest cold-hit cost.
        list(sim_db["chartevents"].aggregate([
            {"$sort": {"charttime": -1}},
            {"$group": {"_id": {"hadm_id": "$hadm_id", "itemid": "$itemid"}, "v": {"$first": "$valuenum"}}},
            {"$limit": 1000},
        ]))
        # Labevents latest-lab-per-patient.
        list(sim_db["labevents"].aggregate([
            {"$sort": {"charttime": -1}},
            {"$group": {"_id": {"hadm_id": "$hadm_id", "itemid": "$itemid"}, "v": {"$first": "$valuenum"}}},
            {"$limit": 1000},
        ]))
        logger.info("mongo_prewarm_done: indexes loaded into WT cache")
    except Exception as exc:  # noqa: BLE001
        logger.warning("mongo_prewarm_failed: %s", exc)


@app.on_event("shutdown")
async def shutdown() -> None:
    global engine, mongo
    if engine and engine.running:
        await engine.stop()
    if mongo:
        mongo.close()
    logger.info("Simulation API shut down.")


# ── health ───────────────────────────────────────────────────────────


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        service="data-ingestion-simulator",
        sim_running=engine.running if engine else False,
    )


# ── simulation state ─────────────────────────────────────────────────


_STATE_CACHE: Dict[str, Any] = {"value": None, "expires": 0.0}
# Dashboard polls /state every 2-5 s. Setting TTL to 2.5 s ensures every
# subsequent poll inside that window returns the cached snapshot in ~7 ms
# instead of running engine.get_state() + metric persistence (≈ 300 ms cold,
# 2 s under load). A 2.5 s stale snapshot is invisible to the operator at
# 1× speed (sim advances 2.5 s) and barely visible at 10× (25 sim-s).
_STATE_CACHE_TTL_S = 2.5  # tunable via SIM_STATE_CACHE_TTL_S env


@app.get("/state", response_model=SimStateResponse)
async def get_state():
    """Return engine state, served from a 1-second cache.

    The dashboard polls /state every 2-5 s. Without caching, building the
    response under load (220+ active patients, 60k queued events) was
    taking >2 s — half the poll interval — and the System Admin page
    showed Sim Engine response_time = 2527 ms. Caching is safe here
    because the engine ticks at sub-second granularity anyway, so a 1 s
    stale snapshot is invisible to the operator. (Closes N5.)
    """
    import time as _time
    global _metrics_prev_discharges
    now = _time.monotonic()
    cached = _STATE_CACHE
    ttl = float(os.environ.get("SIM_STATE_CACHE_TTL_S", _STATE_CACHE_TTL_S))
    if cached["value"] is not None and now < cached["expires"]:
        return cached["value"]

    state_data = engine.get_state()
    cached["value"] = state_data
    cached["expires"] = now + ttl

    # Record metrics for persistent charts (every poll, ~2-5 seconds)
    if state_data.get("running"):
        sim_time = state_data.get("sim_time", "")
        stats = state_data.get("stats", {})
        total_discharged = stats.get("total_discharges", 0)
        total_admitted = stats.get("total_admissions", 0)
        active = state_data.get("active_patients", 0)

        # Parse sim hours
        sim_hours = 0
        if sim_time:
            try:
                from datetime import datetime
                st = datetime.fromisoformat(sim_time)
                sim_hours = round(st.timestamp() / 3600, 2)
            except (ValueError, TypeError):
                pass

        # Throughput: discharges since last recording
        new_discharges = max(0, total_discharged - _metrics_prev_discharges)
        _metrics_prev_discharges = total_discharged

        # Only record if we have meaningful data and not duplicate time
        if sim_hours > 0:
            if _sim_start_hours == 0:
                pass  # set by background loop
            rel_h = round(sim_hours - _sim_start_hours, 2) if _sim_start_hours > 0 else sim_hours
        if sim_hours > 0 and (not _metrics_history or _metrics_history[-1]["sim_hours"] != rel_h):
            point = {
                "sim_hours": rel_h,
                "total_admitted": total_admitted,
                "total_discharged": total_discharged,
                "active_patients": active,
                "new_discharges": new_discharges,
                "sim_time": sim_time,
            }
            _metrics_history.append(point)
            # Persist via a worker thread so the sync pymongo insert never
            # stalls the async event loop. The dashboard polls /state every
            # 2s while running, so this used to block accept on every poll.
            import asyncio
            asyncio.create_task(asyncio.to_thread(_persist_metrics_point, point))
            asyncio.create_task(asyncio.to_thread(_persist_metrics_meta))
            # Keep max 2000 points in memory (Mongo keeps full history)
            if len(_metrics_history) > 2000:
                _metrics_history.pop(0)

    return state_data


# ── digital twin status ─────────────────────────────────────────────


@app.get("/digital-twin/state")
async def get_digital_twin_state():
    """Return the current state of the digital twin across all AI modules."""
    if digital_twin is None:
        return {"status": "error", "error": "Digital twin not initialized"}
    try:
        state = await digital_twin.get_system_state()
        return {"status": "ok", "data": state}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/digital-twin/patient/{hadm_id}")
async def get_digital_twin_patient(hadm_id: str):
    """Return digital twin context for a specific patient."""
    if digital_twin is None:
        return {"status": "error", "error": "Digital twin not initialized"}
    context = digital_twin.get_patient_context(hadm_id)
    results = digital_twin.get_pipeline_results(hadm_id)
    return {"status": "ok", "data": {"context": context, "pipeline_results": results}}


@app.get("/digital-twin/config")
async def get_dt_config():
    """Return current Digital Twin pipeline configuration and stats."""
    if digital_twin is None:
        return {"status": "error", "error": "Digital twin not initialized"}
    return {"status": "ok", "data": digital_twin.get_config()}


@app.post("/digital-twin/modules/{name}/enable")
async def enable_dt_module(name: str):
    """Enable a pipeline module at runtime."""
    if digital_twin is None:
        return {"status": "error", "error": "Digital twin not initialized"}
    digital_twin.enable_module(name)
    return {"status": "ok", "data": {"enabled": name, "config": digital_twin.get_config()}}


@app.post("/digital-twin/modules/{name}/disable")
async def disable_dt_module(name: str):
    """Disable a pipeline module at runtime (skipped during event processing)."""
    if digital_twin is None:
        return {"status": "error", "error": "Digital twin not initialized"}
    digital_twin.disable_module(name)
    return {"status": "ok", "data": {"disabled": name, "config": digital_twin.get_config()}}


# ── start / stop / speed / reset ─────────────────────────────────────


@app.post("/sim/force-discharge")
async def force_discharge(count: int = 1):
    """Manually discharge up to N active patients — skips the MIMIC-native
    LOS schedule. Useful for demoing the Throughput Over Time chart without
    waiting the full LOS (average ~4 sim-days = ~1h real at 100x speed).
    """
    if engine is None or not engine.running:
        return {"status": "error", "error": "sim not running"}
    active = list(engine.active_patients.items())[: max(1, min(count, 50))]
    dispatched = []
    for sim_hadm, patient in active:
        sid = patient.get("subject_id")
        try:
            await engine._propagate_to_digital_twin("discharge", {
                "hadm_id": sim_hadm,
                "subject_id": sid,
                "discharge_location": "HOME",
                "hospital_expire_flag": 0,
            })
            engine.active_patients.pop(sim_hadm, None)
            engine.sim_db["admissions"].update_one(
                {"hadm_id": sim_hadm},
                {"$set": {"status": "discharged", "sim_dischtime": engine.clock.now().isoformat()}},
            )
            engine.stats["total_discharges"] += 1
            dispatched.append(sim_hadm)
        except Exception as exc:  # noqa: BLE001
            dispatched.append({"hadm_id": sim_hadm, "error": str(exc)})
    return {
        "status": "ok",
        "data": {
            "discharged": dispatched,
            "stats_after": engine.stats,
        },
    }


@app.post("/sim/force-admit")
async def force_admit(count: int = 1):
    """Manually trigger N admissions — useful to populate charts immediately
    instead of waiting the full arrival interval, or to debug arrival-loop
    health when admissions appear stuck.
    """
    if engine is None or not engine.running:
        return {"status": "error", "error": "sim not running"}
    results = []
    for _ in range(max(1, min(count, 50))):
        try:
            await engine._admit_next_patient()
            results.append({"ok": True})
        except Exception as exc:  # noqa: BLE001
            results.append({"ok": False, "error": str(exc)})
    return {
        "status": "ok",
        "data": {
            "count_requested": count,
            "results": results,
            "stats_after": engine.stats,
        },
    }


@app.post("/start", response_model=MessageResponse)
async def start_sim(speed: Optional[float] = None):
    """Start (or resume) the simulation.

    Optional ``speed`` query param accelerates the sim clock; honoured only
    when > 0 and ≤ 100. Default speed is the value the engine already has
    (10× on fresh boot).
    """
    if engine.running:
        return MessageResponse(message="Simulation already running.", state=engine.get_state())
    # Apply speed override before starting so the arrival loop picks it up
    if speed is not None:
        try:
            sp = max(0.1, min(100.0, float(speed)))
            engine.clock.set_speed(sp)
            # When the user explicitly chose 1× speed, reset the clock so
            # sim_time == wall_now from this moment forward. Without this,
            # any sim-time accumulated during a prior fast-forward burst
            # leaves a permanent offset that makes "1× = real time" a lie.
            if abs(sp - 1.0) < 1e-9:
                engine.clock.reset()
                logger.info("Engine clock reset to wall-now for 1x speed")
            logger.info("Speed set to %.1fx via /start query param", sp)
        except (TypeError, ValueError):
            logger.warning("ignoring invalid speed=%r", speed)
    await engine.start()
    # Rule 1 — publish the shared sim-clock anchor so downstream services align
    try:
        from shared.integration.sim_clock import SimClock as _SC
        _SC.get_instance().set_anchor(engine.clock.now(), running=True, speed=engine.clock.speed)
    except Exception:
        logger.exception("shared_clock_sync_failed_on_start")
    return MessageResponse(message="Simulation started.", state=engine.get_state())


@app.post("/stop", response_model=MessageResponse)
async def stop_sim():
    if not engine.running:
        return MessageResponse(message="Simulation is not running.", state=engine.get_state())
    await engine.stop()
    try:
        from shared.integration.sim_clock import SimClock as _SC
        _SC.get_instance().stop()
    except Exception:
        logger.exception("shared_clock_stop_failed")
    return MessageResponse(message="Simulation stopped.", state=engine.get_state())


@app.post("/speed", response_model=MessageResponse)
async def set_speed(req: SpeedRequest):
    engine.clock.set_speed(req.speed)
    # Re-anchor to wall-now whenever the user picks 1× — the user-facing
    # invariant is "at 1× speed, sim_time of each EHR event equals real
    # wall-clock time when the event fires". Without this reset, any
    # offset accumulated during a prior fast-forward (e.g. 100× burst)
    # would be carried forward and silently violate that invariant.
    if abs(float(req.speed) - 1.0) < 1e-9:
        engine.clock.reset()
        logger.info("Engine clock reset to wall-now (speed=1x)")
    logger.info("Speed changed to %.1fx", req.speed)
    # Mirror new speed + anchor to the shared clock so downstream services
    # picking up via attach_remote stay aligned without waiting a full
    # refresh cycle.
    try:
        from shared.integration.sim_clock import SimClock as _SC
        _SC.get_instance().set_anchor(
            engine.clock.now(), running=engine.running, speed=engine.clock.speed,
        )
    except Exception:
        logger.exception("shared_clock_speed_sync_failed")
    return MessageResponse(
        message=f"Speed set to {req.speed}x.",
        state=engine.get_state(),
    )


@app.post("/reset", response_model=MessageResponse)
async def reset_sim(req: Optional[ResetRequest] = None):
    # Rule 6 — acquire distributed reset lock via MongoDB mutex
    holder = await _acquire_reset_lock()
    if holder is None:
        return MessageResponse(
            message="Another reset is in progress; try again shortly.",
            state=engine.get_state() if engine else {},
        )

    global _metrics_prev_discharges, _sim_start_hours, _arrival_total_events
    try:
        # Stop if running
        if engine.running:
            await engine.stop()

        # Clear sim DB
        ehr_writer.reset()

        # Reset simulation clock to current system time
        engine.clock.reset()
        # Mirror to shared clock
        try:
            from shared.integration.sim_clock import SimClock as _SC
            _SC.get_instance().set_anchor(engine.clock.now(), running=False)
        except Exception:
            logger.exception("shared_clock_reset_failed")

        # Clear in-memory state
        engine.active_patients.clear()
        engine.event_queue.clear()
        engine.stats = {k: 0 for k in engine.stats}
        # Reset arrival-loop gate — if this carries over, the loop can
        # stall because the reset clock lands before the prior admission.
        engine._last_admission_sim_time = None

        # Reset metrics history (in-memory + Mongo)
        _metrics_history.clear()
        _metrics_prev_discharges = 0
        _sim_start_hours = 0
        _arrival_counts.clear()
        _arrival_total_events = 0
        _purge_metrics_history_from_mongo()

        # Clear shared event broker event_log so stale events from prior
        # runs don't poison idempotency/replay on the next startup. Each
        # service's lifespan calls replay_missed_events() which reads this
        # collection — leaving it full means a fresh start replays events
        # that the current DB doesn't actually reflect.
        # Also purge the outbox so any stuck 'pending' events don't get
        # re-sent into the new run.
        try:
            from shared.db.mongo import MongoManager
            _mongo = MongoManager()
            _mongo.client["MIMIC_SIM"]["event_log"].delete_many({})
            _mongo.client["MIMIC_SIM"]["event_bus_offsets"].delete_many({})
            _mongo.client["MIMIC_SIM"]["outbox"].delete_many({})
            logger.info("event_log + consumer offsets + outbox purged on reset")
        except Exception as exc:
            logger.warning("event_log_purge_failed: %s", exc)

        # Reset Digital Twin
        if digital_twin:
            digital_twin.reset()

        # Rule 6 — call every module's /reset with retry-once-on-failure
        from shared.integration.service_client import ServiceClient
        _reset_client = ServiceClient()
        # Fan-out reset to every service that holds patient-session state.
        # Each target's /reset endpoint is responsible for wiping its own
        # in-memory state + any Mongo collections it owns. Services not
        # listed here either (a) have no session state to clear, or
        # (b) are intentionally persistent (e.g. GDPR audit trail —
        # must survive sim resets per retention policy).
        reset_targets = [
            ("ed_flow", "/reset"),
            ("bed_management", "/reset"),
            # Full /reset (not /reset-integration) so _sessions is wiped —
            # otherwise the DES population accumulates across sim runs and
            # test cycles start with stale state.
            ("hospital_ops", "/reset"),
            # ed_triage and oncology_ai don't expose /reset — they are
            # stateless predictors. Removed from the cascade so a reset
            # doesn't fire two 404 warnings on every run.
            ("sepsis_icu", "/reset"),
            ("waiting_list", "/reset"),
            ("clinical_scribe", "/reset"),
            ("patient_journey", "/reset"),
            ("clinical_chat", "/reset"),
            ("erp", "/reset"),
            # Clinical alert services — previously orphaned, their
            # active_alerts / escalations / occupants accumulated across runs.
            ("deterioration", "/reset"),
            ("discharge_lounge", "/reset"),
            ("trolley_watch", "/reset"),
            # Observability / gateway services with their own state.
            ("xai", "/reset"),
            ("fhir", "/reset"),
            # GDPR is deliberately NOT reset — its audit trail must survive
            # per data-retention policy. Add it here only if the audit store
            # has been moved out (e.g. to an append-only vault).
        ]
        for svc, path in reset_targets:
            for attempt in range(2):
                try:
                    client = _reset_client._get_client(svc)
                    r = await client.post(path, {})
                    if r.get("status") != "error":
                        logger.info("%s reset ok", svc)
                        break
                except Exception as exc:
                    logger.warning("reset_attempt_failed", extra={"service": svc, "attempt": attempt, "error": str(exc)})

        # Re-initialise generator pool
        pool_limit = req.pool_limit if req else 500
        engine.generator.initialize(limit=pool_limit)

        logger.info("Simulation reset (pool_limit=%d).", pool_limit)
        return MessageResponse(message="Simulation reset.", state=engine.get_state())
    finally:
        await _release_reset_lock(holder)


# ── queries ──────────────────────────────────────────────────────────


@app.get("/active-patients", response_model=ActivePatientsResponse)
async def active_patients():
    patients = engine.get_active_patients()
    return ActivePatientsResponse(count=len(patients), patients=patients)


@app.get("/recent-events")
async def recent_events(limit: int = Query(50, ge=1, le=500)):
    events = ehr_writer.get_recent_events(limit=limit)
    return {"count": len(events), "events": events}


@app.get("/outbox-stats")
async def outbox_stats():
    """Return durable-outbox health — pending/sent counts, oldest stuck event."""
    outbox = getattr(app.state, "_outbox", None)
    if outbox is None:
        return {"attached": False}
    return {"attached": True, **outbox.stats()}


@app.get("/metrics-history")
async def get_metrics_history():
    """Return time-series metrics since simulation start."""
    return {"count": len(_metrics_history), "history": _metrics_history}


_ARRIVAL_PATTERNS_CACHE: Dict[str, Any] = {"value": None, "expires": 0.0}
_ARRIVAL_PATTERNS_TTL_S = 15.0


@app.get("/arrival-patterns")
async def get_arrival_patterns():
    """Return dynamic 24-hour arrival patterns per department from simulation data.

    Tracks actual patient arrivals by hour of day and department,
    replacing the static mimicArrivals data.

    Cached 15 s and the Mongo aggregate runs off the event loop so the
    dashboard's HospitalOps poll can't hang every other endpoint.
    Previously this iterated a sync ``aggregate()`` cursor on the
    asyncio loop, which under load (~130 active patients × hours of
    transfers) blocked sibling endpoints for 10-13 s.
    """
    import time as _time
    _now = _time.monotonic()
    if _ARRIVAL_PATTERNS_CACHE["value"] is not None and _now < _ARRIVAL_PATTERNS_CACHE["expires"]:
        return _ARRIVAL_PATTERNS_CACHE["value"]

    sim_db = engine.mongo.client["MIMIC_SIM"] if engine and engine.mongo else None
    result: dict = {}

    def _run_aggregate():
        local: dict = {}
        if sim_db is None:
            return local
        xfer_pipeline = [
            {"$match": {"eventtype": "admit"}},
            {"$group": {
                "_id": {
                    "careunit": "$careunit",
                    "hour": {"$hour": {"$dateFromString": {"dateString": "$intime"}}},
                },
                "count": {"$sum": 1},
            }},
        ]
        for doc in sim_db["transfers"].aggregate(xfer_pipeline):
            raw_dept = doc["_id"].get("careunit", "Unknown")
            irish_dept = _map_dept(raw_dept)
            hour = doc["_id"].get("hour", 0)
            if hour is None:
                continue
            if irish_dept not in local:
                local[irish_dept] = [0] * 24
            local[irish_dept][hour] += doc["count"]
        return local

    try:
        import asyncio as _aio
        result = await _aio.to_thread(_run_aggregate)
    except Exception:
        result = {}

    # If no DB data, use in-memory fallback
    if not result and _arrival_counts:
        result = dict(_arrival_counts)

    # Normalize each department to proportions (sum to 1.0)
    normalized: dict = {}
    for dept, counts in result.items():
        total = sum(counts)
        if total > 0:
            normalized[dept] = {
                "hourly_profile": [round(c / total, 4) for c in counts],
                "total_arrivals": total,
            }
        else:
            normalized[dept] = {
                "hourly_profile": [round(1 / 24, 4)] * 24,
                "total_arrivals": 0,
            }

    response = {"departments": normalized, "total_events": sum(sum(c) for c in result.values())}
    _ARRIVAL_PATTERNS_CACHE["value"] = response
    _ARRIVAL_PATTERNS_CACHE["expires"] = _now + _ARRIVAL_PATTERNS_TTL_S
    return response


@app.get("/department-census", response_model=DepartmentCensusResponse)
async def department_census():
    # Offload the Mongo aggregation to a thread so an unusually slow
    # query (large active_patients set, cold index) doesn't block the
    # async event loop and starve sibling endpoints that the dashboard
    # is polling concurrently.
    import asyncio
    raw_census = await asyncio.to_thread(engine.get_department_census)
    # Map MIMIC names to Irish and aggregate
    census: dict = {}
    for mimic_dept, count in raw_census.items():
        irish = _map_dept(mimic_dept)
        census[irish] = census.get(irish, 0) + count
    return DepartmentCensusResponse(census=census, total=sum(census.values()))


@app.get("/sim-stats", response_model=CollectionStatsResponse)
async def sim_stats():
    return CollectionStatsResponse(collections=ehr_writer.get_stats())


# ── ED Board: live patient board from sim data ──────────────────────

_ED_BOARD_CACHE: Dict[str, Any] = {"value": None, "expires": 0.0}
_ED_BOARD_TTL_S = 30.0
_ED_BOARD_LITE_TTL_S = 10.0
_ED_BOARD_LOCK: "asyncio.Lock | None" = None
_ED_BOARD_LITE_CACHE: Dict[str, Any] = {"value": None, "expires": 0.0}
_ED_BOARD_LITE_LOCK: "asyncio.Lock | None" = None


@app.get("/ed-board")
async def ed_board(lite: int = 0):
    """Return patients with current department, latest vitals.

    ``lite=1`` skips the heavy chartevents aggregation (which over 1M+
    rows at 280 active patients can take 30-60 s) and returns only the
    fields the hospital map needs: hadm_id, subject_id, department,
    acuity, bed, status, primary_icd, admission_type. The dashboard's
    HospitalMap polls with lite=1 every ~8s.

    Cached:
      * full   — 30 s TTL, serialised via asyncio.Lock
      * lite   — 10 s TTL, separate lock so a slow full-build doesn't
                 block lite consumers

    Cache expiry is anchored to the build *end* (not start) so a build
    that takes longer than the TTL doesn't poison the cache with an
    immediately-stale entry.
    """
    import asyncio as _aio
    import time as _time

    cache = _ED_BOARD_LITE_CACHE if lite else _ED_BOARD_CACHE
    ttl = _ED_BOARD_LITE_TTL_S if lite else _ED_BOARD_TTL_S

    global _ED_BOARD_LITE_LOCK, _ED_BOARD_LOCK
    if lite:
        if _ED_BOARD_LITE_LOCK is None:
            _ED_BOARD_LITE_LOCK = _aio.Lock()
        lock = _ED_BOARD_LITE_LOCK
    else:
        if _ED_BOARD_LOCK is None:
            _ED_BOARD_LOCK = _aio.Lock()
        lock = _ED_BOARD_LOCK

    if cache["value"] is not None and _time.monotonic() < cache["expires"]:
        return cache["value"]

    async with lock:
        # Recheck after acquiring the lock — another coroutine may have
        # just populated the cache while we waited.
        if cache["value"] is not None and _time.monotonic() < cache["expires"]:
            return cache["value"]
        response = await _aio.to_thread(_build_ed_board, bool(lite))
        cache["value"] = response
        cache["expires"] = _time.monotonic() + ttl  # anchored to end-of-build
        return response


def _build_ed_board(lite: bool = False) -> Dict[str, Any]:
    """Synchronous body of /ed-board — runs in a thread.

    ``lite=True`` skips the chartevents and diagnoses lookups. With
    1M+ chartevents and ~280 active patients the vitals aggregation
    dominates total query time (30-60 s). The hospital-map polls with
    lite=1 and doesn't render vitals or primary_icd anyway.
    """
    from app_07_data_ingestion.backend.engine.event_engine import _parse_time

    sim_db = engine.mongo.client["MIMIC_SIM"]
    now = engine.clock.now()

    # 1. Get active admissions — filter out stale ones from old sessions
    # Only include patients admitted before or at the current sim time
    from datetime import timedelta
    cutoff = (now - timedelta(days=30)).isoformat()  # 30-day lookback max
    active_adms = list(sim_db["admissions"].find(
        {"status": {"$ne": "discharged"}, "sim_admittime": {"$gte": cutoff}}, {"_id": 0}
    ))
    if not active_adms:
        return {"count": 0, "sim_time": now.isoformat(), "patients": []}

    hadm_ids = [a["hadm_id"] for a in active_adms]

    # 2. Batch: latest transfer per patient (aggregation) — needed for dept
    dept_map: dict = {}
    xfer_pipeline = [
        {"$match": {"hadm_id": {"$in": hadm_ids}}},
        {"$sort": {"intime": -1}},
        {"$group": {"_id": "$hadm_id", "careunit": {"$first": "$careunit"}, "eventtype": {"$first": "$eventtype"}}},
    ]
    for doc in sim_db["transfers"].aggregate(xfer_pipeline):
        dept_map[doc["_id"]] = doc.get("careunit", "Unknown")

    vitals_map: dict = {}
    diag_map: dict = {}

    if not lite:
        # 3. Batch: latest vital per patient per itemid (heavy — only in
        #    full mode). Constrain by charttime window so the engine
        #    doesn't have to scan the full 1M+ chartevents history.
        item_names = _VITAL_SHORT
        charttime_cutoff = (now - timedelta(hours=24)).isoformat()
        vital_pipeline = [
            {"$match": {
                "hadm_id": {"$in": hadm_ids},
                "itemid": {"$in": list(item_names.keys())},
                "charttime": {"$gte": charttime_cutoff},
            }},
            {"$sort": {"charttime": -1}},
            {"$group": {"_id": {"hadm_id": "$hadm_id", "itemid": "$itemid"}, "val": {"$first": "$valuenum"}}},
        ]
        for doc in sim_db["chartevents"].aggregate(vital_pipeline):
            hid = doc["_id"]["hadm_id"]
            iid = doc["_id"]["itemid"]
            if hid not in vitals_map:
                vitals_map[hid] = {}
            name = item_names.get(iid)
            raw = doc.get("val")
            if name and raw is not None:
                try:
                    vitals_map[hid][name] = round(float(raw), 1)
                except (TypeError, ValueError):
                    continue

        # 4. Batch: primary diagnosis per patient (cheap but skipped in lite)
        diag_pipeline = [
            {"$match": {"hadm_id": {"$in": hadm_ids}, "seq_num": 1}},
            {"$project": {"_id": 0, "hadm_id": 1, "icd_code": 1}},
        ]
        for doc in sim_db["diagnoses_icd"].aggregate(diag_pipeline):
            diag_map[doc["hadm_id"]] = doc.get("icd_code", "")

    # 5. Build patient list
    patients = []
    for adm in active_adms:
        hadm = adm.get("hadm_id", "")
        sid = adm.get("subject_id")
        dept = _map_dept(dept_map.get(hadm, "Admitting"))
        vitals = vitals_map.get(hadm, {})
        primary_icd = diag_map.get(hadm, "")

        admit_dt = _parse_time(adm.get("sim_admittime"))
        wait_min = max(0, int((now - admit_dt).total_seconds() / 60)) if admit_dt else 0

        # Acuity heuristic
        hr = vitals.get("hr")
        spo2 = vitals.get("spo2")
        sbp = vitals.get("sbp")
        acuity = rule_based_acuity(spo2=spo2, sbp=sbp, hr=hr, has_vitals=bool(vitals))

        adm_type = adm.get("admission_type", "")
        status = "In Treatment" if "ICU" in dept.upper() else (
            "Waiting" if wait_min > 30 and not vitals else "In Treatment"
        )
        if ("EMER" in adm_type.upper() or "EW" in adm_type.upper()) and wait_min < 10:
            status = "Just Arrived"

        patients.append({
            "id": hadm[:18], "subject_id": sid, "hadm_id": hadm,
            "bed": dept[:3].upper() + str(len(patients) + 1),
            "department": dept, "acuity": acuity, "wait_minutes": wait_min,
            "status": status, "vitals": vitals, "primary_icd": primary_icd,
            "admission_type": adm_type, "insurance": adm.get("insurance", ""),
        })

    patients.sort(key=lambda p: (p["acuity"], -p["wait_minutes"]))
    return {"count": len(patients), "sim_time": now.isoformat(), "patients": patients}


# ── ICU Board: live ICU patient board with SOFA scores ───────────────


def _compute_sofa(vitals: dict, labs: dict) -> tuple[int, dict]:
    """Compute simplified SOFA score from latest vitals and labs.

    Returns (total_score, component_dict).
    Delegates to shared.clinical.risk.compute_sofa after computing MBP.
    """
    # Prepare vitals with MBP for the shared function
    prepared = dict(vitals)
    sbp = vitals.get("sbp")
    dbp = vitals.get("dbp")
    if sbp is not None and dbp is not None:
        prepared["mbp"] = (sbp + 2 * dbp) / 3

    result = _shared_compute_sofa(prepared, labs)
    total = result.pop("total")
    return total, result


@app.get("/icu-board")
async def icu_board():
    """Return ICU patients with SOFA scores computed from latest vitals/labs."""
    from app_07_data_ingestion.backend.engine.event_engine import _parse_time

    sim_db = engine.mongo.client["MIMIC_SIM"]
    now = engine.clock.now()

    # 1. Active admissions
    active_adms = list(sim_db["admissions"].find(
        {"status": {"$ne": "discharged"}}, {"_id": 0}
    ))
    if not active_adms:
        return {"count": 0, "sim_time": now.isoformat(), "patients": []}

    hadm_ids = [a["hadm_id"] for a in active_adms]

    # 2. Latest transfer per patient → filter to critical care (ICU + HDU)
    dept_map: dict = {}
    xfer_pipeline = [
        {"$match": {"hadm_id": {"$in": hadm_ids}}},
        {"$sort": {"intime": -1}},
        {"$group": {"_id": "$hadm_id", "careunit": {"$first": "$careunit"}}},
    ]
    for doc in sim_db["transfers"].aggregate(xfer_pipeline):
        dept_map[doc["_id"]] = doc.get("careunit", "Unknown")

    # Filter to critical care: map to Irish names first, then filter ICU + HDU
    critical_depts = {"ICU", "HDU"}
    icu_hadm_ids = [
        hid for hid, dept in dept_map.items()
        if _map_dept(dept) in critical_depts
    ]
    if not icu_hadm_ids:
        return {"count": 0, "sim_time": now.isoformat(), "patients": []}

    adm_lookup = {a["hadm_id"]: a for a in active_adms}

    # 3. Batch latest vitals for ICU patients
    vital_itemids = _VITAL_IDS
    item_names = _VITAL_SHORT
    vitals_map: dict = {}
    vital_pipeline = [
        {"$match": {"hadm_id": {"$in": icu_hadm_ids}, "itemid": {"$in": vital_itemids}}},
        {"$sort": {"charttime": -1}},
        {"$group": {"_id": {"hadm_id": "$hadm_id", "itemid": "$itemid"}, "val": {"$first": "$valuenum"}}},
    ]
    for doc in sim_db["chartevents"].aggregate(vital_pipeline):
        hid = doc["_id"]["hadm_id"]
        iid = doc["_id"]["itemid"]
        if hid not in vitals_map:
            vitals_map[hid] = {}
        name = item_names.get(iid)
        raw = doc.get("val")
        if name and raw is not None:
            try:
                vitals_map[hid][name] = round(float(raw), 1)
            except (TypeError, ValueError):
                continue

    # 4. Batch latest labs for SOFA (platelets, bilirubin, creatinine) + extras
    sofa_lab_ids = _SOFA_LAB_IDS
    all_lab_ids = _LAB_IDS
    lab_names = _LAB_NAMES
    labs_map: dict = {}
    lab_pipeline = [
        {"$match": {"hadm_id": {"$in": icu_hadm_ids}, "itemid": {"$in": all_lab_ids}}},
        {"$sort": {"charttime": -1}},
        {"$group": {"_id": {"hadm_id": "$hadm_id", "itemid": "$itemid"}, "val": {"$first": "$valuenum"}}},
    ]
    for doc in sim_db["labevents"].aggregate(lab_pipeline):
        hid = doc["_id"]["hadm_id"]
        iid = doc["_id"]["itemid"]
        if hid not in labs_map:
            labs_map[hid] = {}
        name = lab_names.get(iid)
        raw = doc.get("val")
        if name and raw is not None:
            try:
                labs_map[hid][name] = round(float(raw), 2)
            except (TypeError, ValueError):
                # CSV-imported MIMIC.labevents stores valuenum as strings
                # like "" / "___" / non-numeric flags; skip those.
                continue

    # 5. Build patient list with SOFA
    patients = []
    for hadm in icu_hadm_ids:
        adm = adm_lookup.get(hadm)
        if not adm:
            continue
        vitals = vitals_map.get(hadm, {})
        labs = labs_map.get(hadm, {})
        sofa_total, sofa_components = _compute_sofa(vitals, labs)

        # Risk level
        if sofa_total >= 10:
            risk_level = "critical"
        elif sofa_total >= 6:
            risk_level = "high"
        elif sofa_total >= 3:
            risk_level = "moderate"
        else:
            risk_level = "low"

        # Alerts
        alerts = []
        if vitals.get("spo2") and vitals["spo2"] < 90:
            alerts.append({"type": "SpO2 Critical", "severity": "critical", "value": vitals["spo2"]})
        if vitals.get("hr") and (vitals["hr"] < 40 or vitals["hr"] > 150):
            alerts.append({"type": "HR Critical", "severity": "critical", "value": vitals["hr"]})
        if vitals.get("sbp") and vitals["sbp"] < 80:
            alerts.append({"type": "SBP Critical", "severity": "critical", "value": vitals["sbp"]})
        if sofa_total >= 10:
            alerts.append({"type": "SOFA Critical", "severity": "critical", "value": sofa_total})

        mapped_dept = _map_dept(dept_map.get(hadm, "ICU"))
        patients.append({
            "hadm_id": hadm,
            "subject_id": adm.get("subject_id"),
            "department": mapped_dept,
            "capacity": _DEPT_CAPACITIES.get(mapped_dept, 12),
            "sofa_total": sofa_total,
            "sofa_components": sofa_components,
            "vitals": vitals,
            "labs": labs,
            "risk_level": risk_level,
            "alerts": alerts,
        })

    patients.sort(key=lambda p: -p["sofa_total"])
    # Cap to ERP critical care capacity (ICU + HDU beds)
    max_beds = _DEPT_CAPACITIES.get("ICU", 12) + _DEPT_CAPACITIES.get("HDU", 8)
    capped = patients[:max_beds]
    return {
        "count": len(capped),
        "total_in_sim": len(patients),
        "icu_capacity": _DEPT_CAPACITIES.get("ICU", 12),
        "hdu_capacity": _DEPT_CAPACITIES.get("HDU", 8),
        "sim_time": now.isoformat(),
        "patients": capped,
    }


# ── Oncology Board: cancer patients from sim ─────────────────────────


@app.get("/oncology-board")
async def oncology_board():
    """Return cancer patients (ICD C-codes) from simulation data."""
    sim_db = engine.mongo.client["MIMIC_SIM"]
    now = engine.clock.now()

    # 1. Find all diagnoses with ICD-10 cancer codes (C*)
    cancer_pipeline = [
        {"$match": {"icd_code": {"$regex": "^C"}}},
        {"$group": {
            "_id": {"hadm_id": "$hadm_id", "subject_id": "$subject_id"},
            "cancer_codes": {"$addToSet": "$icd_code"},
        }},
    ]
    cancer_docs = list(sim_db["diagnoses_icd"].aggregate(cancer_pipeline))
    if not cancer_docs:
        return {"count": 0, "sim_time": now.isoformat(), "patients": []}

    cancer_hadm_ids = [d["_id"]["hadm_id"] for d in cancer_docs]
    cancer_lookup = {d["_id"]["hadm_id"]: d for d in cancer_docs}

    # 2. Batch: admission info
    adm_lookup: dict = {}
    for adm in sim_db["admissions"].find({"hadm_id": {"$in": cancer_hadm_ids}}, {"_id": 0}):
        adm_lookup[adm["hadm_id"]] = adm

    # 3. Batch: latest department per patient
    dept_map: dict = {}
    xfer_pipeline = [
        {"$match": {"hadm_id": {"$in": cancer_hadm_ids}}},
        {"$sort": {"intime": -1}},
        {"$group": {"_id": "$hadm_id", "careunit": {"$first": "$careunit"}}},
    ]
    for doc in sim_db["transfers"].aggregate(xfer_pipeline):
        dept_map[doc["_id"]] = doc.get("careunit", "Unknown")

    # 4. Batch: medication count per patient
    med_pipeline = [
        {"$match": {"hadm_id": {"$in": cancer_hadm_ids}}},
        {"$group": {"_id": "$hadm_id", "med_count": {"$sum": 1}}},
    ]
    med_map: dict = {}
    for doc in sim_db["prescriptions"].aggregate(med_pipeline):
        med_map[doc["_id"]] = doc["med_count"]

    # 5. Build patient list
    patients = []
    for hadm in cancer_hadm_ids:
        cd = cancer_lookup[hadm]
        adm = adm_lookup.get(hadm, {})
        cancer_codes = sorted(cd.get("cancer_codes", []))
        patients.append({
            "hadm_id": hadm,
            "subject_id": cd["_id"]["subject_id"],
            "cancer_icd": cancer_codes,
            "department": _map_dept(dept_map.get(hadm, "Unknown")),
            "medications_count": med_map.get(hadm, 0),
            "admission_type": adm.get("admission_type", ""),
            "status": adm.get("status", ""),
        })

    patients.sort(key=lambda p: p["cancer_icd"][0] if p["cancer_icd"] else "Z")
    return {"count": len(patients), "sim_time": now.isoformat(), "patients": patients}


# ── Patient Journey: full timeline for a sim patient ─────────────────


@app.get("/patient/{hadm_id}/journey")
async def patient_journey(hadm_id: str):
    """Return the full timeline for a simulation patient."""
    from app_07_data_ingestion.backend.engine.event_engine import _parse_time

    sim_db = engine.mongo.client["MIMIC_SIM"]
    now = engine.clock.now()

    # Admission
    adm = sim_db["admissions"].find_one({"hadm_id": hadm_id}, {"_id": 0})
    if not adm:
        return {"error": "Patient not found", "hadm_id": hadm_id}

    hids = [hadm_id]

    # Transfers (sorted by intime)
    transfers = list(sim_db["transfers"].find(
        {"hadm_id": hadm_id}, {"_id": 0}
    ).sort("intime", 1))

    # Window logic:
    # - Happy path: [sim_admittime → now]
    # - If sim_admittime is in the future (e.g. previous sim run left stale
    #   admissions in the DB after the clock was reset), fall back to
    #   [earliest chartevent for this patient → latest chartevent]. This
    #   makes the chart show whatever data actually exists for the patient
    #   instead of an empty window.
    admit_dt_for_window = _parse_time(adm.get("sim_admittime"))
    stale_admission = bool(admit_dt_for_window and admit_dt_for_window > now)
    window_start_iso: Optional[str] = None
    window_end_iso: str = now.isoformat()
    skip_time_filter = False
    if admit_dt_for_window and not stale_admission:
        window_start_iso = admit_dt_for_window.isoformat()
    else:
        # Stale admission — use the earliest chartevent's charttime as the
        # window start and the latest as the end so elapsed-time is real.
        skip_time_filter = True
        try:
            first_ce = sim_db["chartevents"].find_one(
                {"hadm_id": hadm_id}, sort=[("charttime", 1)], projection={"charttime": 1, "_id": 0},
            )
            last_ce = sim_db["chartevents"].find_one(
                {"hadm_id": hadm_id}, sort=[("charttime", -1)], projection={"charttime": 1, "_id": 0},
            )
            if first_ce and first_ce.get("charttime"):
                window_start_iso = first_ce["charttime"]
            if last_ce and last_ce.get("charttime"):
                window_end_iso = last_ce["charttime"]
        except Exception:
            pass

    # ── Vitals: full time series since admission + snapshot of latest-per-name.
    vital_itemids = _VITAL_IDS
    item_names = _VITAL_SHORT
    series_match: Dict[str, Any] = {
        "hadm_id": {"$in": hids}, "itemid": {"$in": vital_itemids},
    }
    if not skip_time_filter and window_start_iso:
        # Only filter by lower bound. Upper-bound clipping can drop events
        # whose charttime was written during a prior sim run and is ahead of
        # the freshly-reset clock (they are still valid events for this
        # admission and should be shown).
        series_match["charttime"] = {"$gte": window_start_iso}
    vitals_series: Dict[str, list] = {}
    vitals: Dict[str, float] = {}
    for doc in sim_db["chartevents"].find(
        series_match,
        {"_id": 0, "itemid": 1, "valuenum": 1, "charttime": 1},
    ).sort("charttime", 1):
        name = item_names.get(doc.get("itemid"))
        val = doc.get("valuenum")
        if not name or val is None:
            continue
        vitals_series.setdefault(name, []).append({
            "time": doc.get("charttime"),
            "value": round(float(val), 2),
        })
        # Snapshot = most-recent value seen so far
        vitals[name] = round(float(val), 1)

    # ── Labs: full time series since admission + snapshot.
    all_lab_ids = _LAB_IDS
    lab_names = _LAB_NAMES
    lab_match: Dict[str, Any] = {"hadm_id": {"$in": hids}, "itemid": {"$in": all_lab_ids}}
    if not skip_time_filter and window_start_iso:
        lab_match["charttime"] = {"$gte": window_start_iso}
    labs_series: Dict[str, list] = {}
    labs: Dict[str, float] = {}
    for doc in sim_db["labevents"].find(
        lab_match,
        {"_id": 0, "itemid": 1, "valuenum": 1, "charttime": 1, "flag": 1, "valueuom": 1},
    ).sort("charttime", 1):
        name = lab_names.get(doc.get("itemid"))
        val = doc.get("valuenum")
        if not name or val is None:
            continue
        labs_series.setdefault(name, []).append({
            "time": doc.get("charttime"),
            "value": round(float(val), 3),
            "unit": doc.get("valueuom"),
            "flag": doc.get("flag"),
        })
        labs[name] = round(float(val), 2)

    # Medications
    medications = list(sim_db["prescriptions"].find(
        {"hadm_id": hadm_id}, {"_id": 0}
    ))

    # Diagnoses — enrich with ``long_title`` from MIMIC's d_icd_diagnoses
    from shared.clinical.icd_names import get_icd_resolver
    resolver = get_icd_resolver(engine.mongo.client)
    raw_dx = list(sim_db["diagnoses_icd"].find(
        {"hadm_id": hadm_id}, {"_id": 0}
    ).sort("seq_num", 1))
    diagnoses = resolver.resolve_diagnoses(raw_dx)

    # Procedures — enrich with ``long_title`` from MIMIC's d_icd_procedures
    raw_procs = list(sim_db["procedures_icd"].find(
        {"hadm_id": hadm_id}, {"_id": 0}
    ).sort("seq_num", 1))
    procedures = resolver.resolve_procedures(raw_procs)

    # Build unified timeline
    timeline = []
    admit_dt = _parse_time(adm.get("sim_admittime"))
    if admit_dt:
        timeline.append({
            "time": admit_dt.isoformat(),
            "event": "admission",
            "detail": f"Admitted ({adm.get('admission_type', '')})",
        })

    for xfer in transfers:
        t = _parse_time(xfer.get("intime"))
        timeline.append({
            "time": t.isoformat() if t else None,
            "event": "transfer",
            "detail": f"{xfer.get('eventtype', 'transfer')} → {xfer.get('careunit', 'Unknown')}",
        })

    for med in medications:
        timeline.append({
            "time": None,
            "event": "medication",
            "detail": f"{med.get('action', 'prescribed')}: {med.get('drug', '')} {med.get('dose_val_rx', '')} {med.get('route', '')}",
        })

    for dx in diagnoses:
        title = dx.get("long_title") or ""
        code = dx.get("icd_code", "")
        seq = dx.get("seq_num", "")
        label = f"{code} — {title}" if title else f"ICD {code}"
        timeline.append({
            "time": None,
            "event": "diagnosis",
            "detail": f"{label} (seq {seq})",
        })

    for proc in procedures:
        title = proc.get("long_title") or ""
        code = proc.get("icd_code", "")
        seq = proc.get("seq_num", "")
        label = f"{code} — {title}" if title else f"ICD {code}"
        timeline.append({
            "time": (
                _parse_time(proc.get("chartdate")).isoformat()
                if proc.get("chartdate") and _parse_time(proc.get("chartdate"))
                else None
            ),
            "event": "procedure",
            "detail": f"{label} (seq {seq})",
        })

    disch_dt = _parse_time(adm.get("sim_dischtime"))
    if disch_dt:
        timeline.append({
            "time": disch_dt.isoformat(),
            "event": "discharge",
            "detail": f"Discharged to {adm.get('discharge_location', 'Unknown')}",
        })

    # Sort timeline by time (entries without time go to the end)
    timeline.sort(key=lambda e: (e["time"] or "9999"))

    # Derive a care-path summary for the walk-through view.
    #   • Drop discharge markers (empty careunit).
    #   • Drop future transfers — the sim hasn't reached them yet; they'd
    #     otherwise collapse into the admission time and create duplicate
    #     "Apr 24 11:18 → now" segments on the dashboard.
    #   • Chain ``outtime`` of segment i from the ``intime`` of segment i+1
    #     so each segment shows its real dwell window instead of the
    #     admission time.
    #   • Only the last realised segment is ``ongoing``.
    _pre_path: list = []
    for xfer in transfers:
        t_in = _parse_time(xfer.get("intime"))
        t_out = _parse_time(xfer.get("outtime")) if xfer.get("outtime") else None
        if not skip_time_filter and admit_dt_for_window and t_in and t_in < admit_dt_for_window:
            continue
        careunit = xfer.get("careunit")
        eventtype = xfer.get("eventtype")
        # Skip discharge / empty-careunit markers — they're not a location.
        if not careunit and (eventtype or "").lower() == "discharge":
            continue
        # Hide segments whose intime hasn't arrived yet — but ONLY when the
        # admission is live (not stale). For stale admissions (sim clock
        # fell behind the admission), treat every transfer as realised
        # history so the walkthrough renders the full care-path. Otherwise
        # for a live admission filter forward-looking transfers since they
        # physically haven't happened yet.
        if not skip_time_filter and t_in and t_in > now:
            continue
        raw_in_iso = t_in.isoformat() if t_in else None
        raw_out_iso = t_out.isoformat() if t_out else None
        # Clamp a future-stamped outtime back to "ongoing" only for live
        # admissions; for stale ones, respect the raw outtime.
        if not skip_time_filter and t_out and t_out > now:
            t_out = None
        _pre_path.append({
            "careunit": careunit or eventtype or "Unknown",
            "eventtype": eventtype,
            "intime": t_in.isoformat() if t_in else None,
            "outtime": t_out.isoformat() if t_out else None,
            "intime_raw": raw_in_iso,
            "outtime_raw": raw_out_iso,
            "_t_in": t_in,
            "_t_out": t_out,
        })
    # Sort by start time so chaining is correct even if transfers weren't
    # written in order.
    _pre_path.sort(key=lambda s: s["_t_in"] or admit_dt_for_window or now)
    care_path = []
    for idx, seg in enumerate(_pre_path):
        t_in = seg.pop("_t_in")
        t_out = seg.pop("_t_out")
        # Chain: if this segment has no outtime but there's a next segment
        # whose intime is after t_in, use it as the outtime so the UI
        # renders a proper closed window.
        if t_out is None and idx + 1 < len(_pre_path):
            nxt_in = _pre_path[idx + 1].get("_t_in")
            # _t_in was popped already from earlier segments; re-read from
            # the sibling (which still has it because we pop per-seg).
            try:
                next_raw = _pre_path[idx + 1]["intime"]
                if next_raw:
                    nxt_in_dt = _parse_time(next_raw)
                    if nxt_in_dt and t_in and nxt_in_dt > t_in:
                        t_out = nxt_in_dt
            except Exception:
                pass
        seg["outtime"] = t_out.isoformat() if t_out else None
        seg["ongoing"] = t_out is None  # last segment without chained outtime
        care_path.append(seg)

    return {
        "hadm_id": hadm_id,
        "subject_id": adm.get("subject_id"),
        "sim_time": now.isoformat(),
        "window": {"start": window_start_iso, "end": window_end_iso, "stale": stale_admission},
        "admission": adm,
        "vitals": vitals,
        "vitals_series": vitals_series,
        "labs": labs,
        "labs_series": labs_series,
        "medications": medications,
        "diagnoses": diagnoses,
        "procedures": procedures,
        "transfers": transfers,
        "care_path": care_path,
        "timeline": timeline,
    }


# ── Stats Dashboard: aggregate overview from sim data ────────────────


@app.get("/stats-dashboard")
async def stats_dashboard():
    """Aggregate overview stats from simulation data."""
    from app_07_data_ingestion.backend.engine.event_engine import _parse_time

    sim_db = engine.mongo.client["MIMIC_SIM"]
    now = engine.clock.now()

    # Counts by status (single aggregation)
    status_pipeline = [
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    status_counts: dict = {}
    for doc in sim_db["admissions"].aggregate(status_pipeline):
        status_counts[doc["_id"]] = doc["count"]

    total_active = sum(v for k, v in status_counts.items() if k != "discharged")
    total_discharged = status_counts.get("discharged", 0)

    # Active hadm_ids
    active_adms = list(sim_db["admissions"].find(
        {"status": {"$ne": "discharged"}}, {"_id": 0, "hadm_id": 1}
    ))
    active_hadm_ids = [a["hadm_id"] for a in active_adms]

    # Department distribution (latest transfer per active patient)
    dept_dist: dict = {}
    icu_count = 0
    ed_count = 0
    if active_hadm_ids:
        xfer_pipeline = [
            {"$match": {"hadm_id": {"$in": active_hadm_ids}}},
            {"$sort": {"intime": -1}},
            {"$group": {"_id": "$hadm_id", "careunit": {"$first": "$careunit"}}},
        ]
        for doc in sim_db["transfers"].aggregate(xfer_pipeline):
            dept = _map_dept(doc.get("careunit", "Unknown"))
            dept_dist[dept] = dept_dist.get(dept, 0) + 1
            if dept in ("ICU", "HDU"):
                icu_count += 1
            if dept == "ED":
                ed_count += 1

    # Critical count: SOFA >= 10 for ICU patients (batch vitals/labs)
    critical_count = 0
    if active_hadm_ids:
        # Use acuity-based critical (HR, SpO2, SBP) for all active patients
        vital_pipeline = [
            {"$match": {"hadm_id": {"$in": active_hadm_ids}, "itemid": {"$in": [220045, 220277, 220179, 220180]}}},
            {"$sort": {"charttime": -1}},
            {"$group": {"_id": {"hadm_id": "$hadm_id", "itemid": "$itemid"}, "val": {"$first": "$valuenum"}}},
        ]
        v_map: dict = {}
        for doc in sim_db["chartevents"].aggregate(vital_pipeline):
            hid = doc["_id"]["hadm_id"]
            if hid not in v_map:
                v_map[hid] = {}
            v_map[hid][doc["_id"]["itemid"]] = doc.get("val")

        # Count critical based on acuity (ESI 1-2 thresholds)
        critical_hadms = set()
        for hid, vals in v_map.items():
            spo2 = vals.get(220277)
            hr = vals.get(220045)
            sbp = vals.get(220179)
            if spo2 and spo2 < 90:
                critical_hadms.add(hid)
            elif sbp and sbp < 80:
                critical_hadms.add(hid)
            elif hr and (hr < 40 or hr > 150):
                critical_hadms.add(hid)

        # Also check SOFA >= 10 for ICU patients
        sofa_lab_pipeline = [
            {"$match": {"hadm_id": {"$in": active_hadm_ids}, "itemid": {"$in": [51265, 50885, 50912]}}},
            {"$sort": {"charttime": -1}},
            {"$group": {"_id": {"hadm_id": "$hadm_id", "itemid": "$itemid"}, "val": {"$first": "$valuenum"}}},
        ]
        l_map: dict = {}
        for doc in sim_db["labevents"].aggregate(sofa_lab_pipeline):
            hid = doc["_id"]["hadm_id"]
            if hid not in l_map:
                l_map[hid] = {}
            lab_name_map = {51265: "platelets", 50885: "bilirubin", 50912: "creatinine"}
            name = lab_name_map.get(doc["_id"]["itemid"])
            if name:
                l_map[hid][name] = doc.get("val")

        vital_name_map = {220277: "spo2", 220179: "sbp", 220180: "dbp", 220045: "hr"}
        for hid in active_hadm_ids:
            raw_v = v_map.get(hid, {})
            vitals_named = {}
            for iid, val in raw_v.items():
                n = vital_name_map.get(iid)
                if n and val is not None:
                    vitals_named[n] = val
            labs_named = l_map.get(hid, {})
            sofa, _ = _compute_sofa(vitals_named, labs_named)
            if sofa >= 10:
                critical_hadms.add(hid)

        critical_count = len(critical_hadms)

    # Average LOS for discharged patients
    los_pipeline = [
        {"$match": {"status": "discharged", "sim_admittime": {"$exists": True}, "sim_dischtime": {"$exists": True}}},
        {"$project": {"_id": 0, "sim_admittime": 1, "sim_dischtime": 1}},
    ]
    discharged_docs = list(sim_db["admissions"].aggregate(los_pipeline))
    total_los_hours = 0.0
    los_count = 0
    for d in discharged_docs:
        admit_dt = _parse_time(d.get("sim_admittime"))
        disch_dt = _parse_time(d.get("sim_dischtime"))
        if admit_dt and disch_dt:
            # Both must agree on tz-awareness for the subtraction. Some
            # rows ship naive ISO strings (legacy sim writes), others
            # ship tz-aware (newer Mongo dates). Coerce to naive so the
            # comparison is always defined — the absolute tz offset
            # doesn't matter for an LOS delta.
            if admit_dt.tzinfo is not None:
                admit_dt = admit_dt.replace(tzinfo=None)
            if disch_dt.tzinfo is not None:
                disch_dt = disch_dt.replace(tzinfo=None)
            try:
                los_hours = (disch_dt - admit_dt).total_seconds() / 3600
            except TypeError:
                continue
            if los_hours >= 0:
                total_los_hours += los_hours
                los_count += 1
    avg_los_hours = round(total_los_hours / los_count, 1) if los_count > 0 else 0

    # Acuity distribution (reuse ed-board vitals logic for active patients)
    acuity_dist = {"esi1": 0, "esi2": 0, "esi3": 0, "esi4": 0, "esi5": 0}
    if active_hadm_ids:
        for hid in active_hadm_ids:
            raw_v = v_map.get(hid, {})
            hr = raw_v.get(220045)
            spo2 = raw_v.get(220277)
            sbp = raw_v.get(220179)
            acuity = rule_based_acuity(spo2=spo2, sbp=sbp, hr=hr, has_vitals=bool(raw_v))
            acuity_dist[f"esi{acuity}"] += 1

    # Cancer patients count
    cancer_count_res = list(sim_db["diagnoses_icd"].aggregate([
        {"$match": {"icd_code": {"$regex": "^C"}}},
        {"$group": {"_id": "$hadm_id"}},
        {"$count": "total"},
    ]))
    cancer_patients = cancer_count_res[0]["total"] if cancer_count_res else 0

    # Recent admissions (last 5)
    recent_adms = list(sim_db["admissions"].find(
        {}, {"_id": 0}
    ).sort("sim_admittime", -1).limit(5))

    return {
        "sim_time": now.isoformat(),
        "total_active": total_active,
        "total_discharged": total_discharged,
        "icu_count": icu_count,
        "ed_count": ed_count,
        "critical_count": critical_count,
        "avg_los_hours": avg_los_hours,
        "department_distribution": dept_dist,
        "acuity_distribution": acuity_dist,
        "cancer_patients": cancer_patients,
        "recent_admissions": recent_adms,
    }


# ── Alerts: active clinical alerts from sim patients ─────────────────


@app.get("/alerts")
async def alerts():
    """Return active clinical alerts from simulation patients."""
    sim_db = engine.mongo.client["MIMIC_SIM"]
    now = engine.clock.now()

    # 1. All active admissions
    active_adms = list(sim_db["admissions"].find(
        {"status": {"$ne": "discharged"}}, {"_id": 0, "hadm_id": 1, "subject_id": 1}
    ))
    if not active_adms:
        return {"count": 0, "sim_time": now.isoformat(), "alerts": []}

    hadm_ids = [a["hadm_id"] for a in active_adms]
    sid_lookup = {a["hadm_id"]: a.get("subject_id") for a in active_adms}

    # 2. Latest department per patient
    dept_map: dict = {}
    xfer_pipeline = [
        {"$match": {"hadm_id": {"$in": hadm_ids}}},
        {"$sort": {"intime": -1}},
        {"$group": {"_id": "$hadm_id", "careunit": {"$first": "$careunit"}}},
    ]
    for doc in sim_db["transfers"].aggregate(xfer_pipeline):
        dept_map[doc["_id"]] = doc.get("careunit", "Unknown")

    # 3. Batch latest vitals for ALL active patients
    vital_check_ids = _VITAL_IDS
    item_names = _VITAL_SHORT
    vitals_map: dict = {}
    vital_pipeline = [
        {"$match": {"hadm_id": {"$in": hadm_ids}, "itemid": {"$in": vital_check_ids}}},
        {"$sort": {"charttime": -1}},
        {"$group": {"_id": {"hadm_id": "$hadm_id", "itemid": "$itemid"}, "val": {"$first": "$valuenum"}}},
    ]
    for doc in sim_db["chartevents"].aggregate(vital_pipeline):
        hid = doc["_id"]["hadm_id"]
        iid = doc["_id"]["itemid"]
        if hid not in vitals_map:
            vitals_map[hid] = {}
        name = item_names.get(iid)
        if name and doc.get("val") is not None:
            vitals_map[hid][name] = doc["val"]

    # 4. Batch SOFA labs for ICU patients
    icu_keywords = ["ICU", "MICU", "SICU", "CCU", "CVICU", "TSICU"]
    icu_hadm_ids = [
        hid for hid, dept in dept_map.items()
        if any(kw in dept.upper() for kw in icu_keywords)
    ]
    labs_map: dict = {}
    if icu_hadm_ids:
        sofa_lab_ids = _SOFA_LAB_IDS
        lab_name_map = {51265: "platelets", 50885: "bilirubin", 50912: "creatinine"}
        lab_pipeline = [
            {"$match": {"hadm_id": {"$in": icu_hadm_ids}, "itemid": {"$in": sofa_lab_ids}}},
            {"$sort": {"charttime": -1}},
            {"$group": {"_id": {"hadm_id": "$hadm_id", "itemid": "$itemid"}, "val": {"$first": "$valuenum"}}},
        ]
        for doc in sim_db["labevents"].aggregate(lab_pipeline):
            hid = doc["_id"]["hadm_id"]
            iid = doc["_id"]["itemid"]
            if hid not in labs_map:
                labs_map[hid] = {}
            name = lab_name_map.get(iid)
            if name and doc.get("val") is not None:
                labs_map[hid][name] = doc["val"]

    # 5. Generate alerts
    alert_list = []

    for hid in hadm_ids:
        v = vitals_map.get(hid, {})
        dept = dept_map.get(hid, "Unknown")
        sid = sid_lookup.get(hid)

        # HR critical
        hr = v.get("hr")
        if hr is not None and (hr < 40 or hr > 150):
            alert_list.append({
                "severity": "critical",
                "type": "heart_rate",
                "message": f"Heart rate {'bradycardia' if hr < 40 else 'tachycardia'}: {round(hr, 1)} bpm",
                "hadm_id": hid,
                "subject_id": sid,
                "department": dept,
            })

        # SpO2 critical
        spo2 = v.get("spo2")
        if spo2 is not None and spo2 < 90:
            alert_list.append({
                "severity": "critical",
                "type": "spo2",
                "message": f"SpO2 critically low: {round(spo2, 1)}%",
                "hadm_id": hid,
                "subject_id": sid,
                "department": dept,
            })

        # SBP critical
        sbp = v.get("sbp")
        if sbp is not None and sbp < 80:
            alert_list.append({
                "severity": "critical",
                "type": "blood_pressure",
                "message": f"Systolic BP critically low: {round(sbp, 1)} mmHg",
                "hadm_id": hid,
                "subject_id": sid,
                "department": dept,
            })

        # Temperature warning
        temp = v.get("temp")
        if temp is not None and (temp > 39.5 or temp > 103):
            # Determine if Celsius or Fahrenheit based on value range
            if temp > 50:
                # Fahrenheit
                if temp > 103:
                    alert_list.append({
                        "severity": "warning",
                        "type": "temperature",
                        "message": f"High fever: {round(temp, 1)}°F",
                        "hadm_id": hid,
                        "subject_id": sid,
                        "department": dept,
                    })
            else:
                # Celsius
                if temp > 39.5:
                    alert_list.append({
                        "severity": "warning",
                        "type": "temperature",
                        "message": f"High fever: {round(temp, 1)}°C",
                        "hadm_id": hid,
                        "subject_id": sid,
                        "department": dept,
                    })

    # SOFA alerts for ICU patients
    for hid in icu_hadm_ids:
        v = vitals_map.get(hid, {})
        l = labs_map.get(hid, {})
        sofa_total, _ = _compute_sofa(v, l)
        if sofa_total >= 10:
            alert_list.append({
                "severity": "critical",
                "type": "sofa_score",
                "message": f"SOFA score critically elevated: {sofa_total}",
                "hadm_id": hid,
                "subject_id": sid_lookup.get(hid),
                "department": dept_map.get(hid, "ICU"),
            })

    # Sort: critical first, then warning
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    alert_list.sort(key=lambda a: severity_order.get(a["severity"], 9))

    return {"count": len(alert_list), "sim_time": now.isoformat(), "alerts": alert_list}


# ── WebSocket ────────────────────────────────────────────────────────


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    engine.listeners.append(websocket)
    logger.info("WebSocket client connected (%d total).", len(engine.listeners))
    try:
        while True:
            # Keep the connection alive; ignore incoming messages
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        try:
            engine.listeners.remove(websocket)
        except ValueError:
            pass
        logger.info("WebSocket client disconnected (%d remaining).", len(engine.listeners))


# ── Uplift endpoints: digital-twin health, shared clock, circuit breaker, model registry ─

from shared.integration.sim_clock import SimClock as _SharedSimClock  # noqa: E402


def _sync_shared_clock() -> None:
    """Mirror the engine's sim clock onto the process-wide shared singleton.

    Other services read the shared clock to honour Rule 1 (Single Clock).
    """
    if engine is None:
        return
    try:
        shared = _SharedSimClock.get_instance()
        shared.set_anchor(engine.clock.now(), running=bool(engine.running))
    except Exception:  # noqa: BLE001 — never let clock sync break the API
        logger.exception("shared_clock_sync_failed")


@app.get("/sim/clock")
async def sim_clock_state():
    """Return the current simulation clock state for downstream services.

    Called by every module at startup (and after a simulation reset) so the
    platform shares a single wall-to-sim mapping.
    """
    _sync_shared_clock()
    shared = _SharedSimClock.get_instance()
    return {"status": "ok", "data": shared.snapshot().__dict__}


@app.get("/sim/digital-twin/health")
async def sim_digital_twin_health():
    """Per-module orchestration health (Bug #1 fix).

    Reports each downstream module's last success / failure, consecutive
    failure count, and liveness, plus circuit-breaker state snapshots.
    """
    if digital_twin is None:
        return {"status": "error", "error": "digital twin not initialised"}
    _sync_shared_clock()
    return {"status": "ok", "data": digital_twin.health_snapshot()}


@app.get("/sim/circuit-breaker-status")
async def sim_circuit_breaker_status():
    """Aggregate circuit-breaker state across all outbound service calls."""
    if digital_twin is None:
        return {"status": "error", "error": "digital twin not initialised"}
    return {"status": "ok", "data": digital_twin.client.breaker_snapshot()}


@app.post("/sim/circuit-breaker-reset")
async def sim_circuit_breaker_reset():
    """Force-close every outbound circuit breaker.

    Used after a recoverable infrastructure issue (e.g. services came up
    late on startup, DNS hiccup, misconfigured SSL env var that has now
    been fixed) — avoids waiting for the 30-second half-open probe cycle.
    """
    if digital_twin is None:
        return {"status": "error", "error": "digital twin not initialised"}
    n = digital_twin.client.reset_breakers()
    # Also reset per-module health so /sim/digital-twin/health shows fresh state
    digital_twin.module_health.clear()
    return {"status": "ok", "data": {"breakers_reset": n}}


# ── Model registry (Integration 8) ──────────────────────────────────

from pymongo.errors import PyMongoError  # noqa: E402


@app.post("/models/registry")
async def register_model(payload: dict):
    """Accept a registration from an ML service at startup.

    Expected payload::
        {service_name, model_path, version, features_hash,
         metrics, loaded_at}
    """
    required = {"service_name", "version"}
    if not required.issubset(payload):
        return {"status": "error", "error": "missing required fields"}
    doc = dict(payload)
    doc.setdefault("loaded_at", _SharedSimClock.get_instance().get_sim_time().isoformat())
    if mongo is not None:
        try:
            coll = mongo.client["MIMIC_SIM"]["model_registry"]
            coll.update_one(
                {"service_name": doc["service_name"]},
                {"$set": doc, "$inc": {"registrations": 1}},
                upsert=True,
            )
        except PyMongoError as exc:
            logger.warning("model_registry_write_failed: %s", exc)
    return {"status": "ok", "data": doc}


@app.get("/models/registry")
async def list_models():
    """Aggregate registered-model state from MongoDB for dashboards."""
    if mongo is None:
        return {"status": "error", "error": "mongo unavailable"}
    try:
        coll = mongo.client["MIMIC_SIM"]["model_registry"]
        docs = list(coll.find({}, {"_id": 0}))
        return {"status": "ok", "data": docs}
    except PyMongoError as exc:
        return {"status": "error", "error": str(exc)}


# ── Research governance access log (Item 5.8) ───────────────────────

@app.get("/research-governance/access-log")
async def research_access_log(limit: int = Query(100, ge=1, le=1000)):
    """Return recent MIMIC data-access events with purpose annotations.

    Populated by the ``shared/utils/research_governance.py`` helper whenever
    services query MIMIC-IV via :class:`shared.db.mongo.MongoManager`.
    """
    if mongo is None:
        return {"status": "error", "error": "mongo unavailable"}
    try:
        coll = mongo.client["MIMIC_SIM"]["research_governance_log"]
        docs = list(coll.find({}, {"_id": 0}).sort("timestamp", -1).limit(limit))
        return {"status": "ok", "data": docs}
    except PyMongoError as exc:
        return {"status": "error", "error": str(exc)}


# ── Reset mutex + atomic reset (Rule 6) ─────────────────────────────

import uuid as _uuid  # noqa: E402


async def _acquire_reset_lock() -> Optional[str]:
    """Acquire the cross-process reset lock in MongoDB.

    Returns the holder token on success, ``None`` if already held. The
    document doubles as an audit trail of every reset attempt.
    """
    if mongo is None:
        return "nomongo"
    holder = _uuid.uuid4().hex
    coll = mongo.client["MIMIC_SIM"]["sim_locks"]
    now_iso = _SharedSimClock.get_instance().get_sim_time().isoformat()
    result = coll.find_one_and_update(
        {"_id": "reset_lock", "$or": [{"held": False}, {"held": {"$exists": False}}]},
        {"$set": {"held": True, "holder": holder, "acquired_at": now_iso}},
        upsert=True,
    )
    # find_one_and_update returns the pre-update doc; None here means upsert happened.
    if result is None or result.get("held", False) is False:
        return holder
    return None


async def _release_reset_lock(holder: str) -> None:
    if mongo is None or holder == "nomongo":
        return
    coll = mongo.client["MIMIC_SIM"]["sim_locks"]
    coll.update_one(
        {"_id": "reset_lock", "holder": holder},
        {"$set": {"held": False, "released_at": _SharedSimClock.get_instance().get_sim_time().isoformat()}},
    )


# ── entry point ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app_07_data_ingestion.backend.api.main:app",
        host="0.0.0.0",
        port=8207,
        reload=False,
        log_level="info",
    )
