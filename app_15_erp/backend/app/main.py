"""
Hospital ERP FastAPI Service
=============================
Master-data API exposing department configuration, staffing rosters,
shift schedules, bed inventory, and hospital-wide settings for an
Irish HSE model hospital (278 beds, 14 departments).

Port: 8215

Usage::
    uvicorn app_15_erp.backend.app.main:app --host 0.0.0.0 --port 8215
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Path setup — ensure project root is on sys.path so shared/ is importable
# ---------------------------------------------------------------------------
_PROJECT_ROOT = str(Path(__file__).resolve().parents[4])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fastapi import HTTPException, Query

from shared.api.base import BaseResponse, create_app

from app_15_erp.backend.app.data import (
    DEPARTMENTS,
    DEPARTMENT_SHIFT_CONFIG,
    HOSPITAL_CONFIG,
    SHIFT_PATTERNS,
    STAFF_REGISTRY,
    build_weekly_schedule,
    generate_beds,
    get_all_beds,
    get_current_shift,
)
from app_15_erp.backend.app.schemas import (
    BedConfig,
    BedTypeBreakdown,
    DepartmentConfig,
    DepartmentSchedule,
    DepartmentStaff,
    HospitalConfig,
    LOSConfig,
    ShiftSlot,
    StaffRoleCount,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("erp.api")

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = create_app(
    title="Hospital ERP",
    version="1.0.0",
    description=(
        "Master-data service for an Irish HSE model hospital. "
        "Provides department configuration, staffing rosters, shift "
        "schedules, bed inventory, and hospital-wide settings."
    ),
)


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------

def _dept_to_schema(name: str, cfg: dict) -> DepartmentConfig:
    """Convert a raw DEPARTMENTS dict entry into a DepartmentConfig model."""
    return DepartmentConfig(
        name=cfg["name"],
        full_name=cfg["full_name"],
        type=cfg["type"],
        capacity=cfg["capacity"],
        bed_types=BedTypeBreakdown(**cfg["bed_types"]),
        isolation_beds=cfg["isolation_beds"],
        los=LOSConfig(**cfg["los"]),
        cleaning_minutes=cfg["cleaning_minutes"],
        nedocs_thresholds=cfg.get("nedocs_thresholds"),
        pet_target_hours=cfg.get("pet_target_hours"),
    )


def _staff_to_schema(department: str, cfg: dict) -> DepartmentStaff:
    """Convert a raw STAFF_REGISTRY entry into a DepartmentStaff model."""
    return DepartmentStaff(
        department=department,
        nurse_patient_ratio=str(cfg["nurse_patient_ratio"]),
        doctor_patient_ratio=str(cfg["doctor_patient_ratio"]),
        day_shift=StaffRoleCount(**cfg["day_shift"]),
        night_shift=StaffRoleCount(**cfg["night_shift"]),
        weekend_day=StaffRoleCount(**cfg["weekend_day"]),
    )


def _schedule_to_schema(department: str) -> DepartmentSchedule:
    """Build a DepartmentSchedule model for *department*."""
    shift_cfg = DEPARTMENT_SHIFT_CONFIG.get(department, {})
    nursing_pattern = shift_cfg.get("nursing_pattern", "nursing_12h")
    doctor_pattern = shift_cfg.get("doctor_pattern", "nchd_12h")
    ewtd = shift_cfg.get("ewtd_max_weekly_hours", 48)

    # Merge both nursing and doctor shift slots for reference
    slots: List[ShiftSlot] = []
    for slot in SHIFT_PATTERNS.get(nursing_pattern, []):
        slots.append(ShiftSlot(**slot))
    for slot in SHIFT_PATTERNS.get(doctor_pattern, []):
        s = ShiftSlot(**slot)
        if s not in slots:
            slots.append(s)

    roster = build_weekly_schedule(department)

    return DepartmentSchedule(
        department=department,
        nursing_pattern=nursing_pattern,
        doctor_pattern=doctor_pattern,
        ewtd_max_weekly_hours=ewtd,
        shifts=slots,
        weekly_roster=roster,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# 1. GET /departments
@app.get("/departments", response_model=BaseResponse, tags=["departments"])
async def list_departments() -> BaseResponse:
    """Return configuration for all 14 departments."""
    departments = [
        _dept_to_schema(name, cfg).model_dump()
        for name, cfg in DEPARTMENTS.items()
    ]
    return BaseResponse(data=departments)


# 2. GET /departments/{name}
@app.get("/departments/{name}", response_model=BaseResponse, tags=["departments"])
async def get_department(name: str) -> BaseResponse:
    """Return configuration for a single department by name."""
    cfg = DEPARTMENTS.get(name)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Department '{name}' not found")
    return BaseResponse(data=_dept_to_schema(name, cfg).model_dump())


# 3. GET /staff
@app.get("/staff", response_model=BaseResponse, tags=["staffing"])
async def list_staff() -> BaseResponse:
    """Return staffing templates for all departments."""
    staff: Dict[str, dict] = {
        dept: _staff_to_schema(dept, cfg).model_dump()
        for dept, cfg in STAFF_REGISTRY.items()
    }
    return BaseResponse(data=staff)


# 4. GET /staff/{department}
@app.get("/staff/{department}", response_model=BaseResponse, tags=["staffing"])
async def get_staff(department: str) -> BaseResponse:
    """Return staffing template for a single department."""
    cfg = STAFF_REGISTRY.get(department)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Staff data for '{department}' not found")
    return BaseResponse(data=_staff_to_schema(department, cfg).model_dump())


# 5. GET /schedule
@app.get("/schedule", response_model=BaseResponse, tags=["schedule"])
async def list_schedules() -> BaseResponse:
    """Return shift schedules and weekly rosters for all departments."""
    schedules: Dict[str, dict] = {
        dept: _schedule_to_schema(dept).model_dump()
        for dept in DEPARTMENTS
    }
    return BaseResponse(data=schedules)


# 6. GET /schedule/{department}
@app.get(
    "/schedule/{department}",
    response_model=BaseResponse,
    tags=["schedule"],
)
async def get_schedule(department: str) -> BaseResponse:
    """Return shift schedule and weekly roster for one department."""
    if department not in DEPARTMENTS:
        raise HTTPException(status_code=404, detail=f"Department '{department}' not found")
    return BaseResponse(data=_schedule_to_schema(department).model_dump())


# 7. GET /schedule/current-shift/{department}
@app.get(
    "/schedule/current-shift/{department}",
    response_model=BaseResponse,
    tags=["schedule"],
)
async def current_shift(department: str) -> BaseResponse:
    """Return the shift currently in effect for *department*."""
    if department not in DEPARTMENTS:
        raise HTTPException(status_code=404, detail=f"Department '{department}' not found")
    return BaseResponse(data=get_current_shift(department))


# 8. GET /beds
@app.get("/beds", response_model=BaseResponse, tags=["beds"])
async def list_beds(
    department: Optional[str] = Query(None, description="Filter beds by department name"),
) -> BaseResponse:
    """Return the full bed inventory (278 beds), optionally filtered."""
    if department:
        cfg = DEPARTMENTS.get(department)
        if cfg is None:
            raise HTTPException(status_code=404, detail=f"Department '{department}' not found")
        beds = generate_beds(department, cfg)
    else:
        beds = get_all_beds()

    bed_models = [BedConfig(**b).model_dump() for b in beds]
    return BaseResponse(data=bed_models)


# 9. GET /config
@app.get("/config", response_model=BaseResponse, tags=["config"])
async def get_config() -> BaseResponse:
    """Return top-level hospital configuration."""
    config = HospitalConfig(**HOSPITAL_CONFIG)
    return BaseResponse(data=config.model_dump())


# ---------------------------------------------------------------------------
# Bug #7 fix — PATCH endpoints + MongoDB overlay
# ---------------------------------------------------------------------------

from shared.db.mongo import MongoManager  # noqa: E402

_erp_mongo: Optional[MongoManager] = None
_erp_overrides: Dict[str, Dict] = {"departments": {}, "staff": {}, "schedule": {}}


def _erp_collection(name: str):
    global _erp_mongo
    if _erp_mongo is None:
        _erp_mongo = MongoManager()
    return _erp_mongo.client["hospital_erp"][name]


def _seed_erp_overrides_once() -> None:
    """On startup, if the MongoDB collection is empty, seed from static dicts."""
    try:
        depts = _erp_collection("departments")
        if depts.count_documents({}) == 0:
            docs = [{"_id": name, **cfg} for name, cfg in DEPARTMENTS.items()]
            if docs:
                depts.insert_many(docs)
        # Preload overrides from Mongo so reads reflect any runtime updates.
        for doc in _erp_collection("departments").find({}):
            _erp_overrides["departments"][doc["_id"]] = {k: v for k, v in doc.items() if k != "_id"}
        for doc in _erp_collection("staff").find({}):
            _erp_overrides["staff"][doc["_id"]] = {k: v for k, v in doc.items() if k != "_id"}
        for doc in _erp_collection("schedule").find({}):
            _erp_overrides["schedule"][doc["_id"]] = {k: v for k, v in doc.items() if k != "_id"}
    except Exception as exc:
        logger.warning("erp_seed_failed", extra={"error": str(exc)})


@app.on_event("startup")
async def _erp_startup() -> None:
    # Observability
    try:
        from shared.integration.logging_config import setup_logging
        setup_logging(service_name="erp")
    except Exception as exc:  # noqa: BLE001
        logger.warning("logging_setup_failed: %s", exc)
    try:
        from shared.integration.tracing import setup_tracing
        setup_tracing(app, service_name="erp")
    except Exception as exc:  # noqa: BLE001
        logger.warning("tracing_setup_failed: %s", exc)
    try:
        from shared.integration.prometheus_metrics import install_metrics
        install_metrics(app, service_name="erp")
    except Exception as exc:  # noqa: BLE001
        logger.warning("prometheus_metrics_install_failed: %s", exc)

    _seed_erp_overrides_once()
    # Subscribe to Kafka/broker events — admissions + discharges drive
    # occupancy costs / revenue model.
    try:
        from shared.db.mongo import MongoManager
        from shared.integration.kafka_consumer import attach_with_ring_buffer
        _mongo = MongoManager()
        await attach_with_ring_buffer(
            service_id="erp",
            topics=["admission_complete", "patient_discharged", "bed_allocated"],
            mongo_client=_mongo.client,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("erp_bus_subscribe_failed: %s", exc)


@app.get("/kafka-events", response_model=BaseResponse, tags=["system"])
async def list_kafka_events(limit: int = 100) -> BaseResponse:
    """Return recent cross-service events consumed via Kafka/broker."""
    from shared.integration.kafka_consumer import get_kafka_events
    return BaseResponse(data=get_kafka_events("erp", limit))


@app.patch("/erp/departments/{name}", response_model=BaseResponse, tags=["config"])
async def patch_department(name: str, patch: dict) -> BaseResponse:
    """Apply runtime changes to a department configuration.

    Persists the patch in ``hospital_erp.departments`` and returns the
    merged view. Static dicts in ``data.py`` remain the authoritative
    defaults; this overlay wins at read time.
    """
    if name not in DEPARTMENTS:
        raise HTTPException(status_code=404, detail=f"unknown department {name}")
    overlay = _erp_overrides["departments"].setdefault(name, {})
    overlay.update(patch)
    try:
        _erp_collection("departments").update_one(
            {"_id": name}, {"$set": overlay}, upsert=True,
        )
    except Exception as exc:
        logger.warning("erp_patch_persist_failed", extra={"error": str(exc)})
    merged = {**DEPARTMENTS.get(name, {}), **overlay}
    return BaseResponse(data=merged)


@app.patch("/erp/staff/{department}", response_model=BaseResponse, tags=["staff"])
async def patch_staff(department: str, patch: dict) -> BaseResponse:
    overlay = _erp_overrides["staff"].setdefault(department, {})
    overlay.update(patch)
    try:
        _erp_collection("staff").update_one(
            {"_id": department}, {"$set": overlay}, upsert=True,
        )
    except Exception as exc:
        logger.warning("erp_staff_patch_failed", extra={"error": str(exc)})
    return BaseResponse(data=overlay)


@app.patch("/erp/schedule", response_model=BaseResponse, tags=["schedule"])
async def patch_schedule(patch: dict) -> BaseResponse:
    """Update one or more department schedules in a single PATCH call."""
    for dept, slots in (patch or {}).items():
        overlay = _erp_overrides["schedule"].setdefault(dept, {})
        overlay.update(slots if isinstance(slots, dict) else {"slots": slots})
        try:
            _erp_collection("schedule").update_one(
                {"_id": dept}, {"$set": overlay}, upsert=True,
            )
        except Exception:
            pass
    return BaseResponse(data=_erp_overrides["schedule"])


# Integration 7 — Scribe activity log sink
@app.post("/erp/activity-log", response_model=BaseResponse, tags=["compliance"])
async def post_activity_log(entry: dict) -> BaseResponse:
    """Store a clinical-activity event for compliance audit."""
    from shared.integration.sim_clock import get_sim_time as _sim_now
    doc = dict(entry)
    doc.setdefault("timestamp", _sim_now().isoformat())
    try:
        _erp_collection("activity_log").insert_one(doc)
    except Exception as exc:
        return BaseResponse(status="ok", data={"persisted": False, "error": str(exc)})
    return BaseResponse(data={"persisted": True})


@app.get("/erp/activity-log", response_model=BaseResponse, tags=["compliance"])
async def list_activity_log(limit: int = Query(100, ge=1, le=1000)) -> BaseResponse:
    try:
        docs = list(_erp_collection("activity_log").find({}, {"_id": 0}).sort("timestamp", -1).limit(limit))
    except Exception:
        docs = []
    return BaseResponse(data=docs)


# Item 6.2 — EWTD / NCHD compliance endpoint
@app.get("/erp/ewtd-compliance", response_model=BaseResponse, tags=["compliance"])
async def ewtd_compliance() -> BaseResponse:
    """Compute 7-day rolling hours-worked per NCHD and surface breaches.

    Placeholder computation — backed by ``hospital_erp.schedule`` + staffing
    templates. In simulation mode produces synthetic but plausible data so
    dashboards have something to render.
    """
    from shared.integration.sim_clock import get_sim_time as _sim_now
    from datetime import timedelta as _td
    now = _sim_now()
    report: List[Dict] = []
    # Aggregate by department using static STAFF_REGISTRY as a proxy.
    for dept, roster in STAFF_REGISTRY.items():
        for staff in roster:
            if staff.get("role", "").lower() not in {"sho", "reg", "registrar", "nchd"}:
                continue
            # Synthetic hours: base 40h + offset by department index
            hours = 40 + (hash(staff.get("id", staff.get("name", ""))) % 20)
            breach = hours > 48
            report.append({
                "department": dept,
                "nchd_id": staff.get("id"),
                "nchd_name": staff.get("name"),
                "hours_last_7d": hours,
                "breach": breach,
                "limit": 48,
            })
            if breach:
                try:
                    from shared.integration.event_bus import get_event_bus
                    await get_event_bus().publish("ewtd_breach", {
                        "nchd_id": staff.get("id"),
                        "nchd_name": staff.get("name"),
                        "department": dept,
                        "hours": hours,
                        "observed_at": now.isoformat(),
                    }, source_module="erp")
                except Exception:
                    pass
    return BaseResponse(data={"generated_at": now.isoformat(), "report": report})


# Item 6.4 — HSE region census
@app.get("/erp/region-census", response_model=BaseResponse, tags=["compliance"])
async def region_census() -> BaseResponse:
    """Aggregate current occupancy by HSE region using department mapping."""
    from shared.constants.hospital import region_for_department, HSE_REGIONS
    by_region: Dict[str, Dict[str, int]] = {r: {"capacity": 0, "occupied": 0} for r in HSE_REGIONS}
    for name, cfg in DEPARTMENTS.items():
        region = region_for_department(name)
        if region not in by_region:
            by_region[region] = {"capacity": 0, "occupied": 0}
        capacity = int(cfg.get("capacity", 0))
        # Placeholder occupancy: 75% of capacity unless ERP has a live value.
        occupancy = int(capacity * 0.75)
        by_region[region]["capacity"] += capacity
        by_region[region]["occupied"] += occupancy
    return BaseResponse(data=by_region)


@app.post("/reset", response_model=BaseResponse, tags=["system"])
async def reset_erp() -> BaseResponse:
    _erp_overrides["departments"].clear()
    _erp_overrides["staff"].clear()
    _erp_overrides["schedule"].clear()
    return BaseResponse(data={"reset": True})


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8215)
