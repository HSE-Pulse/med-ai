"""
Bed Management FastAPI Service
==============================
Real-time bed tracking, discharge prediction, capacity forecasting,
and optimal bed allocation for Irish hospitals.

Endpoints:
    GET  /beds                    -- All beds with current state
    GET  /beds/{department}       -- Beds in specific department
    GET  /beds/summary            -- Department-level occupancy summary
    POST /beds/{bed_id}/update    -- Update bed status
    POST /predict-discharge       -- Single patient discharge prediction
    POST /batch-predict-discharge -- Batch discharge predictions
    GET  /discharge-board         -- All patients with discharge predictions
    GET  /forecast/{department}   -- Capacity forecast for department
    GET  /forecast/hospital       -- Hospital-wide capacity forecast
    POST /allocate                -- Request optimal bed for patient
    GET  /metrics/trolley-hours   -- Trolley metrics (INMO-compatible)
    GET  /health                  -- Health check

Port: 8208

Usage::
    uvicorn app_08_bed_management.backend.app.main:app --host 0.0.0.0 --port 8208
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

_PROJECT_ROOT = str(Path(__file__).resolve().parents[4])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fastapi import Depends, FastAPI, HTTPException, Query

from shared.api.base import BaseResponse, create_app
from shared.db.mongo import MongoManager
from shared.ml.registry import ModelRegistry
from shared.integration.event_bus import get_event_bus
from shared.integration.idempotency import (
    IdempotencyCache,
    idempotent_post,
    install_idempotency_middleware,
)
from shared.integration.persistent_state import PersistentState
from shared.integration.service_client import ServiceClient

MODEL_DIR = Path(os.getenv("MODEL_DIR", "./models/bed_management"))
DATASET_DIR = Path(os.getenv("DATASET_DIR", "./datasets/bed_management"))

from app_08_bed_management.backend.app.schemas import (
    IRISH_DEPARTMENTS,
    BedAllocation,
    BedAllocationRequest,
    BedState,
    BedUpdateRequest,
    CapacityForecast,
    DepartmentSummary,
    DischargePrediction,
    DischargePredictionRequest,
    HorizonForecast,
    TrolleyMetrics,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("bed_management.api")

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------
state: Dict[str, Any] = {
    "mongo": None,
    "discharge_model": None,   # DischargeClassifier (ML)
    "los_model": None,          # LOSRegressor (ML)
    "capacity_model": None,     # CapacityForecaster (ML)
    "feature_names": [],        # Feature column names for ML models
    "service_client": None,
    "event_bus": None,
}


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(application: FastAPI):
    """Initialize engines and connections on startup."""
    logger.info("Starting Bed Management service...")

    # Structured JSON logging + Loki push (must run before tracing so
    # trace_id injection from LoggingInstrumentor lands in JSON fields)
    try:
        from shared.integration.logging_config import setup_logging
        setup_logging(service_name="bed_management")
    except Exception as exc:  # noqa: BLE001
        logger.warning("logging_setup_failed: %s", exc)

    # OpenTelemetry tracing — call before any other startup work
    try:
        from shared.integration.tracing import setup_tracing
        setup_tracing(application, service_name="bed_management")
    except Exception as exc:  # noqa: BLE001
        logger.warning("tracing_setup_failed: %s", exc)

    # Prometheus /metrics
    try:
        from shared.integration.prometheus_metrics import install_metrics
        install_metrics(application, service_name="bed_management")
    except Exception as exc:  # noqa: BLE001
        logger.warning("prometheus_metrics_install_failed: %s", exc)

    state["mongo"] = MongoManager()
    state["service_client"] = ServiceClient()
    state["event_bus"] = get_event_bus()

    # Anchor this process's SimClock to data_ingestion's authoritative clock
    # so every ``get_sim_time()`` call returns a time aligned with the
    # simulator (and at speed=1× equals real wall-clock time). Without this,
    # the local clock would tick at 1× wall rate regardless of the
    # simulator's speed multiplier.
    try:
        from shared.integration.sim_clock import attach_remote_clock
        await attach_remote_clock()
    except Exception as exc:  # noqa: BLE001
        logger.warning("sim_clock_attach_remote_failed: %s", exc)

    # Bug #4 fix — debounce census pushes to Hospital Ops so DES doesn't thrash
    from shared.integration.debouncer import CensusDebouncer
    state["census_debouncer"] = CensusDebouncer(cooldown_s=5.0)

    # Start the durable event broker (MongoDB always, Kafka when KAFKA_BOOTSTRAP set)
    try:
        state["event_bus"].attach_mongo(state["mongo"].client)
        await state["event_bus"].startup()
    except Exception as exc:  # noqa: BLE001
        logger.warning("event_bus_startup_failed: %s", exc)

    # Start Redis cache — fail-open if REDIS_URL unset or unreachable
    try:
        from shared.integration.cache import get_cache
        await get_cache().start()
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache_startup_failed: %s", exc)

    # Subscribe to cross-service events. On patient_discharged we release
    # the patient's bed in-memory even if the HTTP /release-bed call was
    # missed — Kafka acts as a durability safety net.
    try:
        from shared.integration.kafka_consumer import attach_with_ring_buffer

        async def _on_discharge(topic, payload):
            # NB: BedState fields are `hadm_id`, `patient_id`, `admission_time`.
            # Earlier code referenced bed.assigned_hadm_id / bed.assigned_at,
            # which don't exist on BedState — that handler was a silent no-op,
            # leaving stale "occupied" records when upstream services freed beds.
            hadm_id = payload.get("hadm_id")
            if not hadm_id:
                return
            target = str(hadm_id)
            beds = state.get("beds") or {}
            for bed_id, bed in list(beds.items()):
                bed_hadm = str(bed.hadm_id) if bed.hadm_id else ""
                if bed_hadm and bed_hadm == target:
                    bed.status = "available"
                    bed.hadm_id = None
                    bed.patient_id = None
                    bed.admission_time = None
                    logger.info("bed %s released via Kafka patient_discharged event", bed_id)
                    break

        await attach_with_ring_buffer(
            service_id="bed_management",
            topics=["admission_complete", "patient_discharged", "patient_transferred"],
            mongo_client=state["mongo"].client,
            extra_handlers={"patient_discharged": _on_discharge},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("bed_mgmt_bus_subscribe_failed: %s", exc)

    # Persistent state manager — snapshots bed inventory + allocations so
    # restarts don't lose the in-memory truth. Load the latest snapshot
    # first; if absent, initialise from the hospital configuration.
    persistent = PersistentState(
        service_id="bed_management",
        mongo=state["mongo"].client,
        collection_name="bed_management_state",
    )
    state["persistent"] = persistent

    snap = persistent.load_snapshot()
    if snap and snap.get("state", {}).get("beds"):
        beds_dict = {}
        for bed_id, bed_doc in snap["state"]["beds"].items():
            try:
                beds_dict[bed_id] = BedState(**bed_doc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("bed_snapshot_parse_failed %s: %s", bed_id, exc)
        if beds_dict:
            state["beds"] = beds_dict
            logger.info("Restored %d beds from snapshot (v=%d, at=%s)",
                        len(beds_dict), snap.get("version", 0), snap.get("snapshot_at"))
        else:
            state["beds"] = _initialize_beds()
    else:
        state["beds"] = _initialize_beds()

    # Load ML models
    registry = ModelRegistry(base_path=str(MODEL_DIR))
    try:
        state["discharge_model"], meta = registry.load_model("bed_discharge_24h")
        logger.info("Loaded DischargeClassifier: %s", meta.get("metrics", {}).get("test", {}))
    except FileNotFoundError:
        logger.warning("No DischargeClassifier found; using rule-based fallback.")

    try:
        state["los_model"], meta = registry.load_model("bed_los_regressor")
        logger.info("Loaded LOSRegressor: %s", meta.get("metrics", {}).get("test", {}))
    except FileNotFoundError:
        logger.warning("No LOSRegressor found; using rule-based fallback.")

    try:
        state["capacity_model"], meta = registry.load_model("bed_capacity_forecast")
        logger.info("Loaded CapacityForecaster.")
    except FileNotFoundError:
        logger.warning("No CapacityForecaster found; using rule-based fallback.")

    # Load feature names from training metadata
    meta_path = DATASET_DIR / "metadata.json"
    if meta_path.exists():
        import json
        with open(meta_path) as f:
            ds_meta = json.load(f)
        state["feature_names"] = ds_meta.get("feature_columns", [])

    # Subscribe to simulation events for real-time bed state sync
    bus = state["event_bus"]
    bus.subscribe("patient_transferred", _handle_transfer)
    bus.subscribe("patient_discharged", _handle_discharge_event)
    bus.subscribe("admission_predicted", _handle_admission_predicted)
    bus.subscribe("digital_twin_admission_complete", _handle_dt_admission)

    # Replay any events produced while this service was down so state is
    # fully caught up before we accept traffic.
    try:
        n = await persistent.replay_events_since_snapshot(bus, topics=[
            "patient_transferred", "patient_discharged",
            "admission_predicted", "digital_twin_admission_complete",
        ])
        if n:
            logger.info("Bed Management replayed %d missed events since snapshot", n)
    except Exception as exc:  # noqa: BLE001
        logger.warning("bed_mgmt_replay_failed: %s", exc)

    # Periodic snapshot task — every 30 s write the current bed inventory
    # to Mongo so a crash never loses more than ~30 s of change history.
    async def _snapshot_loop():
        while True:
            try:
                await asyncio.sleep(30)
                persistent.save_snapshot({
                    "beds": {bid: b.model_dump() for bid, b in state.get("beds", {}).items()},
                })
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.debug("bed_snapshot_loop_err: %s", exc)

    state["snapshot_task"] = asyncio.create_task(_snapshot_loop())

    # Periodic Discharge_Lounge reconciler — every 30 s force the local
    # Discharge_Lounge bed registry to match the discharge_lounge service's
    # canonical occupants list. This is belt-and-braces against any future
    # event-bus drift between the two services (the Kafka safety-net handler
    # already releases beds on patient_discharged events; this loop is the
    # backstop if a publish is missed).
    async def _lounge_reconciler():
        from shared.integration.service_client import ServiceClient as _SC
        client = state.get("service_client") or _SC()
        while True:
            try:
                await asyncio.sleep(30)
                # Pull current lounge occupants
                resp = await client.discharge_lounge.get("/discharge-lounge/status")
                if not isinstance(resp, dict) or resp.get("status") != "ok":
                    continue
                data = resp.get("data") or {}
                live_patients = {
                    str(p.get("hadm_id")): p
                    for p in (data.get("patients") or [])
                    if p.get("hadm_id") is not None
                }
                live_hadm_ids = set(live_patients.keys())
                beds = state.get("beds") or {}
                released = 0
                bed_hadm_ids: set = set()
                # Pass 1: release Discharge_Lounge beds whose hadm isn't in lounge
                for bed_id, bed in list(beds.items()):
                    if bed.department != "Discharge_Lounge":
                        continue
                    if bed.status != "occupied":
                        continue
                    bed_hadm = str(bed.hadm_id) if bed.hadm_id else ""
                    if bed_hadm not in live_hadm_ids:
                        bed.status = "available"
                        bed.hadm_id = None
                        bed.patient_id = None
                        bed.admission_time = None
                        released += 1
                    else:
                        bed_hadm_ids.add(bed_hadm)
                # Pass 2: lounge has patients with no bed_management bed →
                # acquire one. Without this, bed_mgmt under-reports lounge
                # occupancy whenever the /notify-transfer call was missed
                # (lounge service down, restart, snapshot drift).
                missing = live_hadm_ids - bed_hadm_ids
                acquired = 0
                if missing:
                    for hadm in missing:
                        for bed in beds.values():
                            if bed.department != "Discharge_Lounge":
                                continue
                            if bed.status != "available":
                                continue
                            bed.status = "occupied"
                            bed.hadm_id = hadm
                            patient_info = live_patients.get(hadm) or {}
                            bed.patient_id = patient_info.get("subject_id")
                            acquired += 1
                            break
                if released or acquired:
                    logger.info(
                        "lounge_reconciler synced: released=%d acquired=%d (lounge has %d live occupants)",
                        released, acquired, len(live_hadm_ids),
                    )
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("lounge_reconciler_err: %s: %s", type(exc).__name__, exc)

    state["lounge_reconciler_task"] = asyncio.create_task(_lounge_reconciler())

    # Simulator reconciler — every 10 s, rebuild bed occupancy from the
    # simulator's authoritative patient roster (MIMIC_SIM.admissions +
    # MIMIC_SIM.transfers). This is the backstop when the Kafka chain
    # admission_complete → bed allocation drops events on transfers, so
    # AMAU/SAU/CDU/Cardiology stay populated when the simulator moves
    # patients around. Releases beds whose hadm is no longer admitted,
    # moves patients whose careunit changed, allocates beds for newly
    # admitted patients.
    async def _simulator_reconciler():
        from datetime import datetime
        from shared.constants.hospital import map_department
        sim_db = state["mongo"].client["MIMIC_SIM"]
        while True:
            try:
                await asyncio.sleep(10)

                target: Dict[str, Dict[str, Any]] = {}
                admitted = sim_db["admissions"].find(
                    {"status": "admitted"},
                    {"_id": 0, "hadm_id": 1, "subject_id": 1, "sim_admittime": 1},
                )
                for adm in admitted:
                    hadm = adm.get("hadm_id")
                    if not hadm:
                        continue
                    last_xfer = sim_db["transfers"].find_one(
                        {"hadm_id": hadm},
                        sort=[("intime", -1)],
                    )
                    careunit = (last_xfer or {}).get("careunit") or "Emergency Department"
                    target[str(hadm)] = {
                        "dept": map_department(careunit),
                        "subject_id": adm.get("subject_id"),
                        "admit_time": adm.get("sim_admittime"),
                    }

                beds = state.get("beds") or {}
                current: Dict[str, str] = {}
                for bed_id, bed in beds.items():
                    if bed.status == "occupied" and bed.hadm_id:
                        current[str(bed.hadm_id)] = bed_id

                released = moved = allocated = 0

                # Pass 1: free beds for hadms no longer admitted, or whose dept changed
                for hadm, bed_id in list(current.items()):
                    bed = beds[bed_id]
                    if hadm not in target:
                        bed.status = "available"
                        bed.hadm_id = None
                        bed.patient_id = None
                        bed.admission_time = None
                        released += 1
                        del current[hadm]
                        continue
                    if bed.department != target[hadm]["dept"]:
                        bed.status = "available"
                        bed.hadm_id = None
                        bed.patient_id = None
                        bed.admission_time = None
                        moved += 1
                        del current[hadm]

                # Pass 2: allocate beds for admitted hadms without one.
                # Rebuild ``current`` from a fresh bed scan first — between
                # the original snapshot above and this point, another path
                # (e.g. /allocate or /notify-transfer) may have written
                # bed.hadm_id for a hadm we'd otherwise treat as un-bedded
                # and double-allocate. Audit measured one hadm holding 4
                # beds because of this race; rebuild closes it.
                current = {
                    str(b.hadm_id): bid
                    for bid, b in beds.items()
                    if b.status == "occupied" and b.hadm_id
                }
                from shared.integration.sim_clock import get_sim_time as _now_sim_clock
                for hadm, info in target.items():
                    if hadm in current:
                        continue
                    wanted = info["dept"]
                    for bed in beds.values():
                        if bed.department != wanted or bed.status != "available":
                            continue
                        bed.status = "occupied"
                        bed.hadm_id = hadm
                        bed.patient_id = info.get("subject_id")
                        admit_raw = info.get("admit_time")
                        # Always set admission_time — fall back to the
                        # current sim-clock when admit_raw is missing or
                        # un-parseable. Leaving it None lets downstream
                        # LOS / mortality calculations divide by zero or
                        # render "Admitted: —" on the patient page, which
                        # was tripping the audit.
                        bed.admission_time = None
                        if isinstance(admit_raw, str):
                            try:
                                bed.admission_time = datetime.fromisoformat(
                                    admit_raw.replace("Z", "")
                                )
                            except Exception:
                                bed.admission_time = None
                        if bed.admission_time is None:
                            try:
                                bed.admission_time = _now_sim_clock()
                            except Exception:
                                from datetime import datetime as _dt, timezone as _tz
                                bed.admission_time = _dt.now(_tz.utc)
                        allocated += 1
                        break

                if released or moved or allocated:
                    logger.info(
                        "sim_reconciler synced: released=%d moved=%d allocated=%d "
                        "(target=%d patients across %d depts)",
                        released, moved, allocated, len(target),
                        len({v["dept"] for v in target.values()}),
                    )
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("sim_reconciler_err: %s: %s", type(exc).__name__, exc)

    state["sim_reconciler_task"] = asyncio.create_task(_simulator_reconciler())

    logger.info("Bed Management service ready with %d departments, %d beds",
                len(IRISH_DEPARTMENTS),
                sum(d["capacity"] for d in IRISH_DEPARTMENTS.values()))

    yield

    for task_key in ("snapshot_task", "lounge_reconciler_task", "sim_reconciler_task"):
        task = state.get(task_key)
        if task is not None:
            task.cancel()
    # Final snapshot on shutdown
    try:
        persistent.save_snapshot({
            "beds": {bid: b.model_dump() for bid, b in state.get("beds", {}).items()},
        })
    except Exception:
        pass
    await state["event_bus"].shutdown()
    if state["mongo"]:
        state["mongo"].close()
    logger.info("Bed Management service shut down.")


def _initialize_beds() -> Dict[str, BedState]:
    """Create initial bed inventory from Irish department configuration.

    Each bed is also assigned an orthogonal ``category`` (isolation,
    paediatric, bariatric, stroke_thrombolysis, …) from BED_CATEGORY_MIX so
    that allocation can honour clinical-suitability constraints like
    infection control and HIQA standards.
    """
    from shared.constants.hospital import resolve_bed_category_for

    beds = {}
    for dept_name, dept_config in IRISH_DEPARTMENTS.items():
        for i in range(1, dept_config["capacity"] + 1):
            bed_id = f"{dept_name}-{i:03d}"
            beds[bed_id] = BedState(
                bed_id=bed_id,
                department=dept_name,
                bed_type=dept_config["type"],
                category=resolve_bed_category_for(dept_name, i),
                status="available",
            )
    return beds


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
app = create_app(
    title="Bed Management AI",
    version="1.1.0",
    description=(
        "Real-time bed management with bed-category taxonomy "
        "(isolation/paediatric/bariatric/stroke), discharge prediction, "
        "and capacity forecasting for Irish hospitals. Idempotent notification "
        "endpoints prevent duplicate side effects during circuit-breaker retries."
    ),
)
app.router.lifespan_context = lifespan

# Idempotency cache for /notify-* endpoints — 10-minute TTL is ample to
# cover circuit-breaker half-open retries and Digital Twin re-dispatches.
_idem_cache = IdempotencyCache(ttl_seconds=600)
install_idempotency_middleware(app, _idem_cache)


# ---------------------------------------------------------------------------
# Bed State Endpoints
# ---------------------------------------------------------------------------
@app.get("/beds", response_model=BaseResponse, tags=["bed-state"])
async def get_all_beds(
    status: Optional[str] = Query(None, description="Filter by status"),
) -> BaseResponse:
    """Return all beds with current state."""
    beds = state.get("beds", {})
    result = list(beds.values())
    if status:
        result = [b for b in result if b.status == status]
    return BaseResponse(data=[b.model_dump() for b in result])


@app.get("/beds/summary", response_model=BaseResponse, tags=["bed-state"])
async def get_beds_summary() -> BaseResponse:
    """Return department-level occupancy summary.

    Polled by 5+ services every few seconds so it's cached in Redis for
    2s. The cache is invalidated whenever a bed is assigned/released
    (see assign_bed, notify_discharge) so stale reads can't linger past
    a bed state change — but within the 2s TTL, identical reads collapse
    to a single Mongo walk.
    """
    from shared.integration.cache import get_cache
    cache = get_cache()

    async def _compute_summaries():
        beds_snapshot = state.get("beds", {})
        out = []
        for dept_name, dept_config in IRISH_DEPARTMENTS.items():
            dept_beds = [b for b in beds_snapshot.values() if b.department == dept_name]
            occupied = sum(1 for b in dept_beds if b.status == "occupied")
            available = sum(1 for b in dept_beds if b.status == "available")
            blocked = sum(1 for b in dept_beds if b.status == "blocked")
            cleaning = sum(1 for b in dept_beds if b.status == "cleaning")
            reserved = sum(1 for b in dept_beds if b.status == "reserved")
            capacity = dept_config["capacity"]
            occupancy_rate = occupied / capacity if capacity > 0 else 0
            if occupancy_rate < 0.75:
                alert_level = "green"
            elif occupancy_rate < 0.85:
                alert_level = "amber"
            elif occupancy_rate < 0.95:
                alert_level = "red"
            else:
                alert_level = "black"
            out.append(DepartmentSummary(
                department=dept_name,
                department_type=dept_config["type"],
                capacity=capacity,
                occupied=occupied,
                available=available,
                blocked=blocked,
                cleaning=cleaning,
                reserved=reserved,
                occupancy_rate=round(occupancy_rate, 3),
                alert_level=alert_level,
            ).model_dump())
        return out

    # Cache hit returns a list of dicts; miss recomputes via the loader above.
    summaries_raw = await cache.get_or_compute(
        "bed:summary:v1",
        _compute_summaries,
        ttl=2,
    )
    summaries = [DepartmentSummary(**s) for s in summaries_raw]

    # Fire-and-forget the notifications so polling clients don't pay the
    # cost of a serial /state + N × /notify-capacity-alert + /notify-census
    # fan-out on every request. Previously these inline awaits could pile
    # up to 10+ s under load (each /state could spike, plus up to 19
    # serial /notify-capacity-alert posts), and the latency was charged
    # to every dashboard poll of /beds/summary even on a cache hit.
    client = state.get("service_client")
    if client:
        _debouncer = state.get("census_debouncer")
        asyncio.create_task(
            _push_capacity_notifications(client, summaries, _debouncer)
        )

    return BaseResponse(data=[s.model_dump() for s in summaries])


async def _push_capacity_notifications(client, summaries, debouncer):
    """Send capacity + census notifications to Hospital Ops in the background.

    Runs as a fire-and-forget task spawned from get_beds_summary so the
    /beds/summary response never blocks on these side-effect calls.
    Capacity alerts fan out in parallel; failures are logged but don't
    propagate.
    """
    sim_time_str = None
    try:
        sim_state = await asyncio.wait_for(
            client._get_client("data_ingestion").get("/state"),
            timeout=2.0,
        )
        sim_time_str = sim_state.get("sim_time")
    except Exception as exc:
        logger.warning(
            "cross_service_call_failed",
            extra={"service": "data_ingestion", "endpoint": "/state", "error": str(exc)},
        )

    async def _notify(s):
        try:
            await asyncio.wait_for(
                client.hospital_ops.post("/notify-capacity-alert", {
                    "department": s.department,
                    "occupancy": s.occupancy_rate,
                    "urgency": s.alert_level,
                    "current_census": s.occupied,
                    "capacity": s.capacity,
                    "sim_time": sim_time_str,
                }),
                timeout=2.0,
            )
        except Exception as exc:
            logger.warning(
                "cross_service_call_failed",
                extra={
                    "service": "hospital_ops",
                    "endpoint": "/notify-capacity-alert",
                    "department": s.department,
                    "error": str(exc),
                },
            )

    alerts = [s for s in summaries if s.alert_level in ("amber", "red", "black")]
    if alerts:
        await asyncio.gather(*[_notify(s) for s in alerts], return_exceptions=True)

    payload = {"departments": [s.model_dump() for s in summaries]}
    should_push = True
    if debouncer is not None:
        try:
            should_push = await debouncer.should_push("__all__", payload)
        except Exception:
            should_push = True
    if should_push:
        try:
            await asyncio.wait_for(
                client.hospital_ops.post("/notify-census", payload),
                timeout=2.0,
            )
        except Exception as exc:
            logger.warning(
                "cross_service_call_failed",
                extra={"service": "hospital_ops", "endpoint": "/notify-census", "error": str(exc)},
            )


@app.get("/beds/categories", response_model=BaseResponse, tags=["bed-state"])
async def get_bed_categories(
    department: Optional[str] = Query(None, description="Filter to single dept"),
) -> BaseResponse:
    """Return bed-category availability breakdown (HIQA infection-control friendly).

    Clinicians looking for an isolation bed, bariatric bed, paediatric bed, or
    stroke-capable bed need a per-category count — not a generic department
    occupancy. This endpoint reports available/occupied/total per category
    (optionally filtered to a single department).
    """
    from collections import defaultdict

    beds = state.get("beds", {}).values()
    if department:
        beds = [b for b in beds if b.department == department]

    by_cat: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"total": 0, "occupied": 0, "available": 0, "blocked": 0}
    )
    for bed in beds:
        cat = getattr(bed, "category", "general") or "general"
        slot = by_cat[cat]
        slot["total"] += 1
        if bed.status == "occupied":
            slot["occupied"] += 1
        elif bed.status == "available":
            slot["available"] += 1
        elif bed.status in ("blocked", "cleaning", "reserved"):
            slot["blocked"] += 1
    return BaseResponse(data=dict(by_cat))


@app.get("/beds/{department}", response_model=BaseResponse, tags=["bed-state"])
async def get_department_beds(department: str) -> BaseResponse:
    """Return beds for a specific department."""
    beds = state.get("beds", {})
    dept_beds = [b for b in beds.values() if b.department == department]
    if not dept_beds:
        raise HTTPException(status_code=404, detail=f"Department '{department}' not found")
    return BaseResponse(data=[b.model_dump() for b in dept_beds])


@app.post("/beds/{bed_id}/update", response_model=BaseResponse, tags=["bed-state"])
async def update_bed(bed_id: str, req: BedUpdateRequest) -> BaseResponse:
    """Update a bed's status (admit, discharge, clean, block)."""
    beds = state.get("beds", {})
    if bed_id not in beds:
        raise HTTPException(status_code=404, detail=f"Bed '{bed_id}' not found")

    bed = beds[bed_id]
    old_status = bed.status
    bed.status = req.status
    bed.patient_id = req.patient_id
    bed.hadm_id = req.hadm_id
    bed.acuity = req.acuity

    # Publish events
    bus = state.get("event_bus")
    if bus:
        if req.status == "available" and old_status == "occupied":
            await bus.publish("bed_released", {
                "bed_id": bed_id,
                "department": bed.department,
            }, source_module="bed_management")
        elif req.status == "occupied" and old_status != "occupied":
            await bus.publish("bed_allocated", {
                "bed_id": bed_id,
                "department": bed.department,
                "patient_id": req.patient_id,
            }, source_module="bed_management")

    logger.info("Bed %s updated: %s → %s", bed_id, old_status, req.status)
    return BaseResponse(data=bed.model_dump())


# ---------------------------------------------------------------------------
# Discharge Prediction Endpoints
# ---------------------------------------------------------------------------
@app.post("/predict-discharge", response_model=BaseResponse, tags=["discharge"])
async def predict_discharge(req: DischargePredictionRequest) -> BaseResponse:
    """Predict discharge timing for a single patient.

    Uses TFT model (primary), DeepSurv (secondary), or XGBoost (fallback).
    Enriches prediction with data from Patient Journey module.
    """
    from datetime import datetime, timezone, timedelta
    from shared.integration.sim_clock import get_sim_time as _sim_now

    def _to_aware(dt):
        if dt is None:
            return None
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    now = _to_aware(req.sim_time) or _to_aware(_sim_now())
    admit = _to_aware(req.admission_time) or now
    los_hours = max(0, (now - admit).total_seconds() / 3600)

    # Attempt to fetch enrichment data from Patient Journey
    client = state.get("service_client")
    patient_context = {}
    if client:
        try:
            patient_context = await client.patient_journey.get(
                f"/patient/{req.patient_id}/summary"
            )
        except Exception:
            logger.debug("Could not fetch patient context from Patient Journey")

    # Identify barriers
    barriers = []
    if req.has_iv:
        barriers.append("Active IV therapy")
    if req.has_oxygen:
        barriers.append("Supplemental oxygen requirement")
    if req.procedures_pending > 0:
        barriers.append(f"{req.procedures_pending} procedures pending")
    if req.news2_score and req.news2_score > 5:
        barriers.append(f"Elevated NEWS2 score ({req.news2_score})")

    # --- ML prediction path ---
    discharge_model = state.get("discharge_model")
    los_model = state.get("los_model")
    model_used = "rule_based_v1"
    base_los = _estimate_department_los(req.department)

    ml_success = False
    if discharge_model is not None and los_model is not None:
        try:
            features = _build_ml_features(req, los_hours)
            features.pop("icd_category", None)
            X = pd.DataFrame([features])
            prob_24h = float(discharge_model.predict_proba(X)[0])
            prob_48h = min(1.0, prob_24h * 1.6)
            remaining = float(los_model.predict(X)[0])
            readiness = max(0, min(1.0, prob_24h))
            model_used = "xgboost_v1"
            factors = [
                f"ML discharge probability (24h): {prob_24h:.2%}",
                f"ML predicted remaining LOS: {remaining:.1f}h",
                f"Current LOS: {los_hours:.1f}h",
            ]
            ml_success = True
        except Exception as e:
            logger.warning("ML predict failed, using rule-based: %s", str(e)[:150])

    if not ml_success:
        # Rule-based fallback
        remaining = max(0, base_los - los_hours)
        if req.news2_score and req.news2_score > 5:
            remaining *= 1.5
        if req.has_iv:
            remaining = max(remaining, 12)
        if req.procedures_pending > 0:
            remaining += req.procedures_pending * 4
        prob_24h = min(1.0, 24 / max(remaining, 1))
        prob_48h = min(1.0, 48 / max(remaining, 1))
        readiness = max(0, min(1.0, 1.0 - (remaining / max(base_los, 1))))
        factors = [
            f"Department typical LOS: {base_los:.0f}h",
            f"Current LOS: {los_hours:.1f}h",
        ]
        if req.current_vitals:
            factors.append("Vitals available for trend analysis")

    # Dampen readiness for newly-admitted patients. The XGBoost model
    # consistently emits prob_24h ≈ 1.0 for fresh admissions because the
    # training distribution has too few "just-admitted" rows. Without this
    # dampening, every admission triggered a discharge_predicted event with
    # readiness=1.0, which the discharge_lounge auto-transfer threshold
    # (0.85) immediately turned into a ward→lounge transfer at admit time
    # — i.e. 100% of patients went to the lounge before any clinical
    # workflow ran. Linear ramp from 0 → full readiness over the
    # department's typical median LOS makes "imminent discharge" mean what
    # a clinician would expect.
    los_factor = min(1.0, los_hours / max(1.0, base_los * 0.5))
    readiness = readiness * los_factor
    prob_24h = prob_24h * los_factor
    prob_48h = prob_48h * los_factor

    predicted_discharge = now + timedelta(hours=max(0, remaining))

    prediction = DischargePrediction(
        patient_id=req.patient_id,
        hadm_id=req.hadm_id,
        department=req.department,
        current_los_hours=round(los_hours, 1),
        predicted_discharge_time=predicted_discharge,
        discharge_probability_24h=round(prob_24h, 4),
        discharge_probability_48h=round(prob_48h, 4),
        discharge_readiness_score=round(readiness, 3),
        confidence_lower=now + timedelta(hours=max(0, remaining * 0.7)),
        confidence_upper=now + timedelta(hours=remaining * 1.4),
        key_factors=factors,
        barriers_to_discharge=barriers,
        model_used=model_used,
    )

    # Publish prediction event — only on *meaningful* transitions, not on
    # incremental drift. Subscribers (discharge-lounge auto-transfer, alerts)
    # only act on band-crossings (0.5 = "rising", 0.85 = "lounge-ready"), so
    # republishing every <5 % move was producing ~6 000 events/patient. We now
    # gate on three conditions, all of which must collectively be met by
    # *triggering at least one*:
    #   1. first publish for this hadm (always),
    #   2. readiness crossed a band boundary (0.5 or 0.85), or
    #   3. ≥ 10 % absolute change AND ≥ 30 s wall-time since the last publish.
    bus = state.get("event_bus")
    if bus and req.hadm_id:
        last_state = state.setdefault("_last_readiness_published", {})
        key = str(req.hadm_id)
        prev = last_state.get(key)  # dict: {readiness, ts, band}
        BANDS = (0.5, 0.85)
        def _band(r: float) -> int:
            for i, edge in enumerate(BANDS):
                if r < edge:
                    return i
            return len(BANDS)
        cur_band = _band(readiness)
        now_ts = datetime.utcnow().timestamp()

        publish = False
        reason = ""
        if prev is None:
            publish, reason = True, "first"
        elif _band(prev["readiness"]) != cur_band:
            publish, reason = True, f"band_change_{prev['readiness']:.2f}->{readiness:.2f}"
        elif abs(readiness - prev["readiness"]) >= 0.10 and (now_ts - prev["ts"]) >= 30.0:
            publish, reason = True, f"delta_{readiness - prev['readiness']:+.2f}"

        if publish:
            await bus.publish("discharge_predicted", {
                "patient_id": req.patient_id,
                "hadm_id": req.hadm_id,
                "predicted_discharge": predicted_discharge.isoformat(),
                "readiness_score": readiness,
                "_publish_reason": reason,
            }, source_module="bed_management")
            last_state[key] = {"readiness": readiness, "ts": now_ts, "band": cur_band}

    return BaseResponse(data=prediction.model_dump())


@app.get("/discharge-board", response_model=BaseResponse, tags=["discharge"])
async def discharge_board(
    department: Optional[str] = Query(None),
) -> BaseResponse:
    """Return all occupied beds with discharge predictions."""
    beds = state.get("beds", {})
    occupied = [b for b in beds.values() if b.status == "occupied"]
    if department:
        occupied = [b for b in occupied if b.department == department]

    board = []
    for bed in occupied:
        board.append({
            "bed_id": bed.bed_id,
            "department": bed.department,
            "patient_id": bed.patient_id,
            "acuity": bed.acuity,
            "predicted_discharge": bed.predicted_discharge.isoformat() if bed.predicted_discharge else None,
            "discharge_readiness": bed.discharge_readiness_score,
        })

    return BaseResponse(data=board)


# ---------------------------------------------------------------------------
# Capacity Forecasting Endpoints
# ---------------------------------------------------------------------------
@app.get("/forecast/{department}", response_model=BaseResponse, tags=["forecast"])
async def forecast_department(department: str) -> BaseResponse:
    """Return capacity forecast for a department (4/8/12/24/48/72h horizons)."""
    if department not in IRISH_DEPARTMENTS:
        raise HTTPException(status_code=404, detail=f"Department '{department}' not found")

    beds = state.get("beds", {})
    dept_beds = [b for b in beds.values() if b.department == department]
    capacity = IRISH_DEPARTMENTS[department]["capacity"]
    current_census = sum(1 for b in dept_beds if b.status == "occupied")

    # ML capacity forecast or rule-based fallback
    capacity_model = state.get("capacity_model")
    forecasts = []
    use_ml = False

    if capacity_model is not None:
        from shared.integration.sim_clock import get_sim_time as _sim_now
        now = _sim_now()
        try:
            ml_forecasts = capacity_model.predict(
                department, horizon_hours=72,
                start_hour=now.hour, start_dow=now.weekday(),
            )
            # Sanity check: ML model must produce varied, realistic predictions
            if ml_forecasts and len(ml_forecasts) >= 6:
                values = [f["predicted_census"] for f in ml_forecasts[:6]]
                if len(set(values)) > 1 and max(values) > 1.5:
                    use_ml = True
                    for horizon in [4, 8, 12, 24, 48, 72]:
                        fc = ml_forecasts[horizon - 1]
                        forecasts.append(HorizonForecast(
                            horizon_hours=horizon,
                            predicted_census=fc["predicted_census"],
                            lower_bound_90=fc["lower_bound_90"],
                            upper_bound_90=fc["upper_bound_90"],
                            predicted_occupancy=round(fc["predicted_census"] / capacity, 3) if capacity > 0 else 0,
                        ))
        except Exception as e:
            logger.warning("ML capacity forecast failed for %s: %s", department, e)

    if not use_ml:
        # Rule-based: exponential convergence toward 80% capacity equilibrium
        # with diurnal variation and uncertainty growing with horizon
        equilibrium = capacity * 0.8
        for horizon in [4, 8, 12, 24, 48, 72]:
            # Converge from current census toward equilibrium
            pred = current_census + (equilibrium - current_census) * (1 - 0.95 ** horizon)
            # Add diurnal pattern (busier during daytime) — sim-clock-aware
            from shared.integration.sim_clock import get_sim_time as _sim_now
            hour = (_sim_now().hour + horizon // 2) % 24
            if 8 <= hour <= 18:
                pred *= 1.05  # daytime bump
            elif hour >= 22 or hour <= 5:
                pred *= 0.95  # nighttime dip
            pred = max(1, min(capacity, pred))
            # Uncertainty grows with horizon
            uncertainty = max(1, current_census * 0.15 * (horizon / 4) ** 0.5)
            forecasts.append(HorizonForecast(
                horizon_hours=horizon,
                predicted_census=round(pred, 1),
                lower_bound_90=round(max(0, pred - uncertainty), 1),
                upper_bound_90=round(min(capacity, pred + uncertainty), 1),
                predicted_occupancy=round(pred / capacity, 3) if capacity > 0 else 0,
            ))

    occupancy = current_census / capacity if capacity > 0 else 0
    if occupancy < 0.75:
        alert = "green"
    elif occupancy < 0.85:
        alert = "amber"
    elif occupancy < 0.95:
        alert = "red"
    else:
        alert = "black"

    result = CapacityForecast(
        department=department,
        current_census=current_census,
        current_capacity=capacity,
        alert_level=alert,
        forecasts=forecasts,
        recommended_actions=_get_capacity_recommendations(occupancy, department),
    )

    # Publish capacity alert to Hospital Ops if occupancy is concerning
    if alert in ("amber", "red", "black"):
        client = state.get("service_client")
        if client:
            try:
                await client.hospital_ops.post("/notify-capacity-alert", {
                    "department": department,
                    "occupancy": round(occupancy, 3),
                    "urgency": alert,
                    "current_census": current_census,
                    "capacity": capacity,
                })
            except Exception as exc:
                logger.warning(
                    "cross_service_call_failed",
                    extra={
                        "service": "hospital_ops",
                        "endpoint": "/notify-capacity-alert",
                        "department": department,
                        "error": str(exc),
                    },
                )

    return BaseResponse(data=result.model_dump())


@app.get("/forecast/hospital", response_model=BaseResponse, tags=["forecast"])
async def forecast_hospital() -> BaseResponse:
    """Return hospital-wide capacity forecast."""
    forecasts = []
    for dept in IRISH_DEPARTMENTS:
        result = await forecast_department(dept)
        forecasts.append(result.data)
    return BaseResponse(data=forecasts)


# ---------------------------------------------------------------------------
# Bed Allocation Endpoint
# ---------------------------------------------------------------------------
@app.post("/allocate", response_model=BaseResponse, tags=["allocation"])
async def allocate_bed(req: BedAllocationRequest) -> BaseResponse:
    """Find and recommend optimal bed allocation for a patient.

    Uses multi-objective optimization considering acuity, department
    preference, isolation needs, gender separation, and capacity.
    """
    # Refresh staffing data from Hospital Ops before scoring
    await _get_staffing_data()

    beds = state.get("beds", {})

    # Idempotent guard — if this hadm already holds a bed (because /allocate
    # was racing with a transfer-driven allocation, or the orchestrator
    # retried), return the existing bed instead of grabbing a second one.
    # GAP-1/GAP-11: pre-fix audits saw the same hadm holding 2-4 beds when
    # transfers fired before /allocate landed in compressed-LOS demos.
    if req.hadm_id:
        existing = next(
            (b for b in beds.values()
             if b.status == "occupied" and str(b.hadm_id) == str(req.hadm_id)),
            None,
        )
        if existing is not None:
            return BaseResponse(data=BedAllocation(
                patient_id=req.patient_id,
                recommended_bed=existing.bed_id,
                recommended_department=existing.department,
                priority_score=1.0,
                wait_time_estimate_minutes=0,
                allocation_reason=f"Existing bed: {existing.department} ({existing.bed_type})",
            ).model_dump())

    available_beds = [b for b in beds.values() if b.status == "available"]

    if not available_beds:
        # No beds available — create trolley alert
        bus = state.get("event_bus")
        if bus:
            await bus.publish("trolley_alert", {
                "patient_id": req.patient_id,
                "acuity": req.acuity,
                "message": "No beds available — patient on trolley",
            }, source_module="bed_management")

        return BaseResponse(data=BedAllocation(
            patient_id=req.patient_id,
            recommended_department="ED_Trolley",
            priority_score=req.acuity / 5.0,
            wait_time_estimate_minutes=120,
            allocation_reason="No beds currently available",
        ).model_dump())

    # Score each available bed
    scored_beds = []
    for bed in available_beds:
        score = _score_bed_match(bed, req)
        scored_beds.append((score, bed))

    scored_beds.sort(key=lambda x: x[0], reverse=True)
    best_score, best_bed = scored_beds[0]

    # Actually assign the bed
    from datetime import datetime, timezone, timedelta
    from shared.integration.sim_clock import get_sim_time as _sim_now_fn
    best_bed.status = "occupied"
    best_bed.patient_id = req.patient_id
    best_bed.hadm_id = req.hadm_id
    best_bed.acuity = req.acuity
    sim_now = req.sim_time or _sim_now_fn()
    # Always store timezone-aware datetimes so downstream arithmetic works
    if sim_now.tzinfo is None:
        sim_now = sim_now.replace(tzinfo=timezone.utc)
    best_bed.admission_time = sim_now
    los_hours = {1: 120, 2: 72, 3: 48, 4: 24, 5: 12}.get(int(req.acuity), 48)
    best_bed.predicted_discharge = sim_now + timedelta(hours=los_hours)
    best_bed.discharge_readiness_score = max(0, 1.0 - req.acuity / 5.0)
    logger.info("Bed %s assigned to patient %s (hadm=%s)",
                best_bed.bed_id, req.patient_id, req.hadm_id)

    # Invalidate bed-summary cache so the next poll sees the new census
    try:
        from shared.integration.cache import get_cache
        await get_cache().invalidate_pattern("bed:summary:*")
    except Exception:  # noqa: BLE001
        pass

    alternatives = [
        {"bed_id": b.bed_id, "department": b.department, "score": round(s, 3)}
        for s, b in scored_beds[1:4]
    ]

    result = BedAllocation(
        patient_id=req.patient_id,
        recommended_bed=best_bed.bed_id,
        recommended_department=best_bed.department,
        priority_score=round(best_score, 3),
        wait_time_estimate_minutes=0,
        alternative_beds=alternatives,
        allocation_reason=f"Best match: {best_bed.department} ({best_bed.bed_type})",
    )

    # Publish to the bus so subscribers (alerts, hospital_ops mirror,
    # patient_journey timeline) actually see allocations. Pre-fix the
    # medai.bed_allocated topic had subscribers but no producer — we
    # observed 0 events in 15 min of sustained sim. (Closes N6.)
    bus = state.get("event_bus")
    if bus is not None:
        try:
            await bus.publish("bed_allocated", {
                "patient_id": req.patient_id,
                "hadm_id": getattr(req, "hadm_id", None),
                "bed_id": best_bed.bed_id,
                "department": best_bed.department,
                "bed_type": best_bed.bed_type,
                "priority_score": round(best_score, 3),
            }, source_module="bed_management")
        except Exception as exc:  # noqa: BLE001
            logger.debug("bed_allocated_publish_failed: %s", exc)

    return BaseResponse(data=result.model_dump())


# ---------------------------------------------------------------------------
# Discharge Notification (called by Digital Twin via HTTP)
# ---------------------------------------------------------------------------
@app.post("/reset", response_model=BaseResponse, tags=["system"])
async def reset_bed_management() -> BaseResponse:
    """Reset all beds to available (called on simulation reset).

    Also clears the persisted snapshot so a subsequent restart starts from a
    clean state rather than restoring the pre-reset allocations.
    """
    state["beds"] = _initialize_beds()
    # Drop any cross-cutting state that would otherwise survive a reset
    # and surface in the dashboard as ghost data from a prior simulation
    # — most visibly the priority-escalations list, which kept showing
    # the same SIM-* hadm_id stacked six deep after every reset.
    state.pop("priority_escalations", None)
    state.pop("admission_notifications", None)
    state.pop("capacity_alerts", None)
    persistent = state.get("persistent")
    if persistent is not None:
        persistent.clear()
    # Seed a fresh snapshot representing the clean state
    if persistent is not None:
        persistent.save_snapshot({
            "beds": {bid: b.model_dump() for bid, b in state["beds"].items()},
        })
    logger.info("Bed Management reset — all beds available, snapshot cleared")
    return BaseResponse(data={"reset": True})


@app.get("/kafka-events", response_model=BaseResponse, tags=["system"])
async def list_kafka_events(limit: int = 100) -> BaseResponse:
    """Return recent cross-service events consumed via Kafka/broker."""
    from shared.integration.kafka_consumer import get_kafka_events
    return BaseResponse(data=get_kafka_events("bed_management", limit))


@app.get("/cache-stats", response_model=BaseResponse, tags=["system"])
async def list_cache_stats() -> BaseResponse:
    """Return Redis cache hit/miss stats for this service."""
    from shared.integration.cache import get_cache
    return BaseResponse(data=get_cache().stats())


def is_simulation_patient(hadm_id: str) -> bool:
    """Return True if ``hadm_id`` refers to a simulation-originated admission.

    Bug #3 fix — replaces the former substring check that could false-match
    real HADM IDs. Prefers an exact match in the sim admissions collection;
    falls back to the ``SIM-`` prefix convention when MongoDB isn't
    reachable.
    """
    if not hadm_id:
        return False
    text = str(hadm_id)
    mongo = state.get("mongo") if state else None
    if mongo is not None:
        try:
            coll = mongo.client["MIMIC_SIM"]["admissions"]
            if coll.find_one({"hadm_id": text}) is not None:
                return True
        except Exception:
            pass
    return text.startswith("SIM-")


@app.post("/notify-discharge/{hadm_id}", response_model=BaseResponse, tags=["discharge"])
async def notify_discharge(
    hadm_id: Any,
    _idem_key: Any = Depends(idempotent_post(_idem_cache)),
) -> BaseResponse:
    """Free the bed occupied by the given hadm_id.

    Bug #3 fix — exact hadm_id / patient_id match only. Substring matching is
    replaced with :func:`is_simulation_patient` for SIM-prefix semantics.

    Idempotent: clients may supply an ``Idempotency-Key`` header so retries
    (from circuit breakers or Digital Twin re-dispatch) don't double-free
    a bed that has already been reallocated.
    """
    beds = state.get("beds", {})
    search = str(hadm_id)

    for bed in beds.values():
        if bed.status != "occupied":
            continue
        bed_hadm = str(bed.hadm_id) if bed.hadm_id else ""
        # Match: exact hadm_id OR exact patient_id OR simulation-prefix match
        matched = (
            bed_hadm == search
            or str(bed.patient_id) == search
            or (is_simulation_patient(search) and bed_hadm == search)
        )
        if matched:
            bed.status = "available"
            old_patient = bed.patient_id
            old_hadm = bed.hadm_id
            bed.patient_id = None
            bed.hadm_id = None
            bed.acuity = None
            bed.predicted_discharge = None
            bed.discharge_readiness_score = 0
            logger.info("Bed %s freed via discharge (hadm=%s, patient=%s)",
                        bed.bed_id, old_hadm, old_patient)
            try:
                from shared.integration.cache import get_cache
                await get_cache().invalidate_pattern("bed:summary:*")
            except Exception:  # noqa: BLE001
                pass
            return BaseResponse(data={"bed_id": bed.bed_id, "freed": True})
    return BaseResponse(data={"freed": False, "reason": f"No bed found for hadm_id={hadm_id}"})


@app.get("/escalations", response_model=BaseResponse, tags=["notifications"])
async def list_escalations(limit: int = 200) -> BaseResponse:
    """Return the log of ``/escalate-bed-priority`` calls for dashboard display."""
    log = state.get("priority_escalations", []) or []
    return BaseResponse(data=log[-int(limit):])


@app.post("/escalate-bed-priority", response_model=BaseResponse, tags=["notifications"])
async def escalate_bed_priority(data: dict) -> BaseResponse:
    """Integration 5 — raise a patient's bed allocation priority.

    Called by Sepsis ICU (and indirectly by the Digital Twin) when a sepsis
    alert, deterioration, or similar clinical event should reshuffle the
    queue. The bump is additive on top of any existing priority score and
    capped at 1.0.
    """
    hadm_id = str(data.get("hadm_id", ""))
    reason = str(data.get("reason", "clinical_escalation"))
    bump = float(data.get("bump", 0.5))
    target = None
    for bed in state.get("beds", {}).values():
        if bed.status == "occupied" and str(bed.hadm_id) == hadm_id:
            target = bed
            break
    if target is None:
        return BaseResponse(
            data={"escalated": False, "reason": f"no bed currently holds {hadm_id}"}
        )
    current = float(target.discharge_readiness_score or 0)
    target.discharge_readiness_score = max(0.0, min(1.0, current + bump))
    # Dedupe by (hadm_id, reason). Sepsis ICU and the NEWS2 watchdog re-fire
    # the same alert on every detection tick while a clinical condition
    # persists, so without dedupe the dashboard's escalations table fills
    # with identical rows (six copies of the same SIM-* hadm with
    # sepsis_alert_red, etc). Keep at most one entry per pair, refreshing
    # its score and bump in place so the table always reflects the latest
    # state for that patient/reason rather than a paper trail of duplicates.
    escalation_log = state.setdefault("priority_escalations", [])
    entry = {
        "hadm_id": hadm_id,
        "bed_id": target.bed_id,
        "reason": reason,
        "bump": bump,
        "new_score": target.discharge_readiness_score,
    }
    replaced = False
    for i, existing in enumerate(escalation_log):
        if existing.get("hadm_id") == hadm_id and existing.get("reason") == reason:
            escalation_log[i] = entry
            replaced = True
            break
    if not replaced:
        escalation_log.append(entry)
    if len(escalation_log) > 500:
        del escalation_log[:200]
    logger.info(
        "bed_priority_escalated",
        extra={"hadm_id": hadm_id, "reason": reason, "bump": bump},
    )
    return BaseResponse(
        data={
            "escalated": True,
            "bed_id": target.bed_id,
            "new_priority": target.discharge_readiness_score,
            "reason": reason,
        }
    )


@app.post("/notify-transfer", response_model=BaseResponse, tags=["notifications"])
async def notify_transfer(data: dict) -> BaseResponse:
    """Handle patient transfer: free source bed, allocate target bed."""
    hadm_id = data.get("hadm_id")
    to_dept = data.get("to_department")
    subject_id = data.get("subject_id")
    beds = state.get("beds", {})

    # Free source bed. Match on string form of hadm_id to tolerate int/str
    # mismatches between callers (SIM-prefixed ids, MIMIC ints). Same fix
    # as /notify-discharge.
    search = str(hadm_id)
    for bed in beds.values():
        bed_hadm = str(bed.hadm_id) if bed.hadm_id else ""
        if bed_hadm == search and bed.status == "occupied":
            bed.status = "available"
            bed.patient_id = None
            bed.hadm_id = None
            bed.acuity = None
            logger.info("Bed %s freed on transfer (hadm=%s)", bed.bed_id, hadm_id)
            break

    # Allocate target bed (map MIMIC dept name to Irish config). Always
    # stamp ``admission_time`` so dashboards / LOS calculations don't see
    # a None value on transfer-only beds (the previous gap left 27 of 80
    # occupied beds with admission_time=None at the audit).
    from shared.integration.sim_clock import get_sim_time as _now_sim_clock
    allocated_bed = None
    mapped_dept = _map_department(to_dept) if to_dept else None
    if mapped_dept:
        for bed in beds.values():
            if bed.department == mapped_dept and bed.status == "available":
                bed.status = "occupied"
                bed.patient_id = subject_id
                bed.hadm_id = hadm_id
                try:
                    bed.admission_time = _now_sim_clock()
                except Exception:
                    from datetime import datetime as _dt, timezone as _tz
                    bed.admission_time = _dt.now(_tz.utc)
                logger.info("Bed %s allocated for transfer to %s (hadm=%s)",
                            bed.bed_id, to_dept, hadm_id)
                allocated_bed = bed.bed_id
                break

    return BaseResponse(data={"transferred": True, "new_bed": allocated_bed})


# ---------------------------------------------------------------------------
# Trolley Metrics Endpoint
# ---------------------------------------------------------------------------
@app.get("/metrics/trolley-hours", response_model=BaseResponse, tags=["metrics"])
async def trolley_metrics() -> BaseResponse:
    """Return trolley metrics compatible with INMO TrolleyGAR reporting."""
    # Placeholder — populated by real-time tracking in production
    return BaseResponse(data=TrolleyMetrics().model_dump())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# MIMIC → Irish mapping imported from shared constants (single source of truth)
from shared.constants.hospital import map_department as _map_department


def _build_ml_features(req: DischargePredictionRequest, los_hours: float) -> Dict[str, float]:
    """Build feature dict matching the training schema for ML models."""
    features: Dict[str, float] = {}
    features["age"] = req.age
    features["gender_encoded"] = 1.0 if req.gender.upper().startswith("M") else 0.0
    features["admission_type_encoded"] = 0.0  # default emergency
    features["days_since_admission"] = los_hours / 24.0
    features["careunit_encoded"] = 0.0  # encoded dept (0=unknown)
    features["num_transfers"] = 0.0
    features["day_of_week"] = 0.0
    features["hour_of_day"] = 12.0
    features["num_diagnoses"] = 0.0
    features["num_procedures"] = req.procedures_pending

    # Vitals
    vitals = req.current_vitals or {}
    for vname in ["heart_rate", "respiratory_rate", "spo2", "sbp", "dbp", "temperature", "mbp"]:
        val = vitals.get(vname)
        if val is not None:
            features[vname] = val
            features[f"{vname}_missing"] = 0.0
        else:
            features[vname] = 0.0
            features[f"{vname}_missing"] = 1.0

    # Labs
    labs = req.current_labs or {}
    for lname in ["wbc", "hemoglobin", "platelets", "creatinine", "bun", "sodium", "potassium", "glucose"]:
        val = labs.get(lname)
        if val is not None:
            features[lname] = val
            features[f"{lname}_missing"] = 0.0
        else:
            features[lname] = 0.0
            features[f"{lname}_missing"] = 1.0

    # Add ICD dummy columns (all zero — no diagnosis info from API request)
    icd_categories = [
        "icd_Blood", "icd_Circulatory", "icd_Digestive", "icd_Endocrine",
        "icd_Eye_Ear", "icd_Genitourinary", "icd_Health_Services", "icd_Infectious",
        "icd_Injury", "icd_Mental", "icd_Musculoskeletal", "icd_Neoplasm",
        "icd_Nervous", "icd_Other", "icd_Pregnancy", "icd_Respiratory",
        "icd_Skin", "icd_Symptoms", "icd_Unknown",
    ]
    for cat in icd_categories:
        features[cat] = 0.0

    # Filter to training features only
    known_features = state.get("feature_names", [])
    if known_features:
        return {k: features.get(k, 0.0) for k in known_features}
    return features


def _estimate_department_los(department: str) -> float:
    """Estimate typical LOS (hours) by department."""
    los_map = {
        "ED": 6.0, "MAU": 24.0, "AMAU": 18.0, "SAU": 24.0,
        "CDU": 12.0, "Medicine": 120.0, "Surgery": 96.0,
        "Cardiology": 96.0, "Respiratory": 120.0, "Orthopaedics": 144.0,
        "ICU": 72.0, "HDU": 48.0, "Day_Ward": 8.0, "Discharge_Lounge": 4.0,
    }
    return los_map.get(department, 72.0)


# Cached staffing data from Hospital Ops (refreshed periodically)
_staffing_cache: Dict[str, Dict] = {}
_staffing_cache_time: float = 0


async def _get_staffing_data() -> Dict[str, Dict]:
    """Fetch staffing recommendations from Hospital Ops (cached 30s)."""
    global _staffing_cache, _staffing_cache_time
    import time as _time
    if _time.time() - _staffing_cache_time < 30 and _staffing_cache:
        return _staffing_cache
    client = state.get("service_client")
    if client:
        try:
            result = await client.hospital_ops.get("/staffing-recommendations")
            if result.get("status") == "ok":
                _staffing_cache = result.get("data", {})
                _staffing_cache_time = _time.time()
        except Exception as exc:
            logger.warning(
                "cross_service_call_failed",
                extra={
                    "service": "hospital_ops",
                    "endpoint": "/staffing-recommendations",
                    "error": str(exc),
                },
            )
    return _staffing_cache


def _score_bed_match(bed: BedState, req: BedAllocationRequest) -> float:
    """Score how well a bed matches a patient's needs (0-1).

    Scores department preference, acuity-bed type match, clinical-suitability
    category (isolation, paediatric, bariatric, stroke, maternity, monitoring),
    and current staffing. Hard-incompatibilities (e.g. paediatric patient on an
    adult-only bed) force the score toward zero.
    """
    from shared.constants.hospital import (
        ISOLATION_COMPATIBLE, MONITORING_COMPATIBLE,
        PAEDIATRIC_COMPATIBLE, MATERNITY_COMPATIBLE, BARIATRIC_COMPATIBLE,
    )

    score = 0.5

    # Department preference match
    if req.department_preference and bed.department == req.department_preference:
        score += 0.3
    elif req.department_preference:
        score -= 0.1

    # Acuity-bed type matching
    if req.acuity >= 4 and bed.bed_type == "critical":
        score += 0.2
    elif req.acuity >= 3 and bed.bed_type in ("high_dependency", "inpatient"):
        score += 0.15
    elif req.acuity <= 2 and bed.bed_type in ("inpatient", "assessment"):
        score += 0.1

    category = getattr(bed, "category", "general") or "general"

    # Isolation — single-room with en-suite
    if req.requires_isolation:
        if category in ISOLATION_COMPATIBLE:
            score += 0.25
        else:
            # Non-isolation bed is a hard NO for an infectious patient
            return 0.0

    # Paediatric — hard requirement
    if req.requires_paediatric:
        if category in PAEDIATRIC_COMPATIBLE:
            score += 0.3
        else:
            return 0.0

    # Maternity — hard requirement
    if req.requires_maternity:
        if category in MATERNITY_COMPATIBLE:
            score += 0.3
        else:
            return 0.0

    # Bariatric — strong preference (not absolute; emergency override possible)
    if req.requires_bariatric:
        if category in BARIATRIC_COMPATIBLE:
            score += 0.25
        else:
            score -= 0.2

    # Stroke-capable — strong preference for acute stroke patients
    if getattr(req, "requires_stroke_capable", False):
        if category == "stroke_thrombolysis":
            score += 0.3
        else:
            score -= 0.15

    # Monitoring requirement — cardiac/critical/HDU/stroke-telemetry all count
    if req.requires_monitoring:
        if category in MONITORING_COMPATIBLE or bed.bed_type in ("critical", "high_dependency"):
            score += 0.2
        else:
            score -= 0.1

    # Staffing adequacy (from Hospital Ops — departments are now 1:1)
    sim_dept = bed.department
    if sim_dept in _staffing_cache:
        staff_info = _staffing_cache[sim_dept]
        if staff_info.get("adequately_staffed"):
            score += 0.1  # bonus for well-staffed department
        elif staff_info.get("recommended_action") == "increase_staff":
            score -= 0.1  # penalty for understaffed department

    return max(0, min(1.0, score))


def _get_capacity_recommendations(occupancy: float, department: str) -> List[str]:
    """Generate actionable recommendations based on occupancy level."""
    recs = []
    if occupancy >= 0.95:
        recs.append(f"CRITICAL: {department} at {occupancy:.0%} occupancy — activate overflow protocol")
        recs.append("Consider early discharge review for stable patients")
        recs.append("Alert bed management coordinator")
    elif occupancy >= 0.85:
        recs.append(f"WARNING: {department} at {occupancy:.0%} occupancy — review pending discharges")
        recs.append("Prepare overflow capacity")
    elif occupancy >= 0.75:
        recs.append(f"CAUTION: {department} approaching 85% threshold ({occupancy:.0%})")
    return recs


# ---------------------------------------------------------------------------
# EventBus handlers — sync bed state with simulation events
# ---------------------------------------------------------------------------

def _handle_transfer(event) -> None:
    """Update bed occupancy when a patient transfers between departments."""
    dept = event.payload.get("to_department")
    hadm_id = event.payload.get("hadm_id")
    if dept and hadm_id:
        beds = state.get("beds", {})
        # Find an available bed in the target department
        for bed in beds.values():
            if bed.department == dept and bed.status == "available":
                bed.status = "occupied"
                bed.patient_id = event.payload.get("subject_id")
                bed.hadm_id = hadm_id
                logger.info("Bed %s allocated for transfer to %s (hadm=%s)",
                            bed.bed_id, dept, hadm_id)
                break


def _handle_discharge_event(event) -> None:
    """Free bed when a patient is discharged."""
    hadm_id = event.payload.get("hadm_id")
    if hadm_id:
        beds = state.get("beds", {})
        for bed in beds.values():
            if bed.hadm_id == hadm_id and bed.status == "occupied":
                bed.status = "available"
                bed.patient_id = None
                bed.hadm_id = None
                bed.acuity = None
                logger.info("Bed %s freed on discharge (hadm=%s)", bed.bed_id, hadm_id)
                break


def _handle_admission_predicted(event) -> None:
    """Pre-allocate a bed when ED Flow predicts admission."""
    hadm_id = event.payload.get("hadm_id")
    prob = event.payload.get("probability", 0)
    if prob > 0.7 and hadm_id:
        beds = state.get("beds", {})
        for bed in beds.values():
            if bed.department == "Medicine" and bed.status == "available":
                bed.status = "reserved"
                bed.hadm_id = hadm_id
                logger.info("Bed %s pre-reserved for predicted admission (hadm=%s, prob=%.2f)",
                            bed.bed_id, hadm_id, prob)
                break


def _handle_dt_admission(event) -> None:
    """Track new admission from digital twin in bed state."""
    hadm_id = event.payload.get("hadm_id")
    acuity = event.payload.get("acuity")
    if hadm_id:
        beds = state.get("beds", {})
        # Allocate ED bed
        for bed in beds.values():
            if bed.department == "ED" and bed.status == "available":
                bed.status = "occupied"
                bed.hadm_id = hadm_id
                bed.acuity = acuity
                break


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8208)
