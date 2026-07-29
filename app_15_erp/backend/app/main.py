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
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

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


def _check_capacity_drift() -> None:
    """Warn if ERP's capacity table has drifted from the shared constants.

    ERP keeps its own DEPARTMENTS table because it carries things the shared
    constants do not — bed-type mix, LOS benchmarks, NEDOCS thresholds — and
    beds are generated from that bed-type breakdown. The capacity figure is
    therefore duplicated, and a duplicate is a future disagreement: raising a
    capacity in shared/constants without touching this file is exactly how
    the bed register came to report "10 of 96 occupied, 30 available".

    The two agree today. This says so out loud at startup, and complains the
    moment they stop.
    """
    try:
        from shared.constants.hospital import CAPACITIES
    except Exception as exc:  # noqa: BLE001
        logger.debug("capacity_drift_check_skipped: %s", exc)
        return
    drift = []
    for name, cfg in DEPARTMENTS.items():
        mine = int(cfg.get("capacity", 0))
        shared_val = CAPACITIES.get(name)
        if shared_val is not None and shared_val != mine:
            drift.append(f"{name}: erp={mine} shared={shared_val}")
        bed_types_sum = sum((cfg.get("bed_types") or {}).values())
        if bed_types_sum != mine:
            drift.append(f"{name}: bed_types sum to {bed_types_sum}, capacity {mine}")
    if drift:
        logger.warning("erp_capacity_drift %s", "; ".join(drift))
    else:
        logger.info("erp_capacity_check ok departments=%d total_beds=%d",
                    len(DEPARTMENTS),
                    sum(int(c.get("capacity", 0)) for c in DEPARTMENTS.values()))


@app.on_event("startup")
async def _erp_startup() -> None:
    _check_capacity_drift()
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


# Roles the European Working Time Directive is applied to here. Consultants
# sit outside the NCHD 48-hour averaging arrangement, so including them would
# understate the per-post figure for the doctors the limit actually governs.
NCHD_ROLES = ("registrar", "sho", "intern")


def _shift_hours(start: str, end: str) -> float:
    """Length of a shift in hours, handling the overnight wrap."""
    try:
        sh, sm = (int(x) for x in str(start).split(":")[:2])
        eh, em = (int(x) for x in str(end).split(":")[:2])
    except (ValueError, AttributeError):
        return 0.0
    minutes = (eh * 60 + em) - (sh * 60 + sm)
    if minutes <= 0:                     # 19:00 -> 07:00
        minutes += 24 * 60
    return round(minutes / 60.0, 2)


# Item 6.2 — EWTD / NCHD compliance endpoint
@app.get("/erp/ewtd-compliance", response_model=BaseResponse, tags=["compliance"])
async def ewtd_compliance() -> BaseResponse:
    """Rostered NCHD hours per week against the 48-hour EWTD limit.

    Computed from the published roster: each entry in ``weekly_roster``
    contributes its shift length multiplied by the NCHD headcount rostered
    onto it. That is a real figure the schedule actually asserts.

    What this deliberately does NOT do is claim per-individual hours. The
    previous implementation iterated ``STAFF_REGISTRY[dept]`` as though it
    were a list of staff records; it is a dict of shift -> role -> headcount,
    so every request raised AttributeError: 'str' object has no attribute
    'get' and the endpoint returned 500. Fixing the iteration alone would not
    have made it correct, because the underlying data holds no individuals:
    there are no NCHD ids, names or worked hours anywhere in this service.
    The old code synthesised them — ``hours = 40 + hash(id) % 20`` — and then
    published an ``ewtd_breach`` event for anyone the hash pushed over 48.
    Fabricated breaches of a statutory working-time limit, broadcast to every
    other service on the bus, from a GET request.

    Both are gone. Event publication is removed outright: a GET must not have
    side effects, and a breach signal has to come from real timesheets, not
    from this. ``per_individual_tracking`` is reported as false with the
    reason, so a caller can tell the difference between "compliant" and "not
    measured".

    What is returned is the demand side: hours the roster requires, and the
    minimum number of NCHDs needed to cover them within the weekly limit.
    Whether a department employs that many is a question for the
    establishment record, which this service does not hold.
    """
    from shared.integration.sim_clock import get_sim_time as _sim_now
    now = _sim_now()

    report: List[Dict] = []
    for dept in DEPARTMENTS:
        # Same builder the /schedule endpoint serves from, so the compliance
        # figure is computed against exactly the roster the UI displays.
        sched = _schedule_to_schema(dept).model_dump()
        if not sched:
            continue
        limit = float(sched.get("ewtd_max_weekly_hours") or 48)
        durations = {}
        longest = 0.0
        for sh in sched.get("shifts") or []:
            hours = _shift_hours(sh.get("start"), sh.get("end"))
            # A department may define the same shift name at different times
            # (07:00 and 08:00 starts); keep the longest as the worst case.
            durations[sh.get("name")] = max(durations.get(sh.get("name"), 0.0), hours)
            longest = max(longest, hours)

        rostered_hours = 0.0
        peak_posts = 0
        for slot in sched.get("weekly_roster") or []:
            staff = slot.get("staff") or {}
            heads = sum(int(staff.get(r, 0) or 0) for r in NCHD_ROLES)
            rostered_hours += heads * durations.get(slot.get("shift"), 0.0)
            peak_posts = max(peak_posts, heads)

        if peak_posts == 0:
            continue
        # Minimum NCHDs needed to cover the rota inside the weekly limit.
        # This is the honest direction to compute in. Dividing rostered hours
        # by peak concurrent headcount instead gives "hours per post", which
        # assumes one doctor works every slot their role appears in — that
        # produced 127 h/week for ED and 168 for CDU (one person, 24/7) and
        # flagged all 13 departments as breaching a statutory limit. The
        # roster says how many hours must be covered; it does not say how many
        # doctors exist to cover them, so a breach cannot be derived from it.
        required = math.ceil(rostered_hours / limit) if limit else None
        report.append({
            "department": dept,
            "nchd_posts_peak_concurrent": peak_posts,
            "rostered_nchd_hours_per_week": round(rostered_hours, 1),
            "min_nchds_for_compliance": required,
            "longest_single_shift_hours": longest,
            "weekly_limit_hours": limit,
            # Not "breach": establishment headcount is unknown, so whether any
            # individual exceeds the limit is unknown too.
            "compliance_determinable": False,
        })
    return BaseResponse(data={
        "generated_at": now.isoformat(),
        "basis": "roster-derived, establishment level",
        "per_individual_tracking": False,
        "per_individual_reason": (
            "This service holds role headcounts per shift, not individual "
            "staff records, so hours cannot be attributed to a named NCHD. "
            "Per-person EWTD monitoring needs a timesheet feed."
        ),
        "departments_reported": len(report),
        "total_rostered_nchd_hours_per_week": round(
            sum(r["rostered_nchd_hours_per_week"] for r in report), 1),
        "min_nchds_for_compliance_total": sum(
            r["min_nchds_for_compliance"] or 0 for r in report),
        "report": report,
    })


# Item 6.4 — HSE region census
@app.get("/erp/region-census", response_model=BaseResponse, tags=["compliance"])
async def region_census() -> BaseResponse:
    """Occupancy by HSE region, read from the live bed register.

    This previously reported ``int(capacity * 0.75)`` for every department,
    with a comment claiming it was a placeholder "unless ERP has a live
    value" — no code path ever supplied one, so the endpoint always returned
    exactly 75% occupancy everywhere. It is named region-census and it was
    reporting a constant. Clinical chat can now reach every GET endpoint in
    the estate, so that number was one question away from being quoted as
    fact.

    Occupancy now comes from bed_management, the service that owns bed state.
    If it cannot be reached the counts are reported as null and
    ``occupancy_available`` is false, because "unknown" is an answer and 75%
    is not.
    """
    from shared.constants.hospital import region_for_department, HSE_REGIONS

    occupied_by_dept: Dict[str, int] = {}
    operational_cap_by_dept: Dict[str, int] = {}
    available = False
    try:
        base = os.environ.get("BED_MANAGEMENT_URL", "http://localhost:8208")
        async with httpx.AsyncClient(timeout=10.0, verify=False, trust_env=False) as client:
            body = (await client.get(f"{base}/beds/summary")).json()
        rows = body.get("data", body)
        if isinstance(rows, list):
            for row in rows:
                name = row.get("department")
                if name is not None:
                    occupied_by_dept[str(name)] = int(row.get("occupied") or 0)
                    # Take the denominator from the same service as the
                    # numerator. Occupancy is counted against the operational
                    # bed inventory, so dividing it by ERP's physical
                    # establishment mixes two different bed counts — it
                    # reported 45% where the real figure was 27%.
                    operational_cap_by_dept[str(name)] = int(row.get("capacity") or 0)
            available = bool(occupied_by_dept)
    except Exception as exc:  # noqa: BLE001
        logger.warning("region_census_bed_lookup_failed: %s", exc)

    def _blank() -> Dict[str, Any]:
        return {"physical_capacity": 0, "operational_capacity": 0,
                "occupied": 0 if available else None, "departments": []}

    by_region: Dict[str, Dict[str, Any]] = {r: _blank() for r in HSE_REGIONS}
    for name, cfg in DEPARTMENTS.items():
        region = region_for_department(name)
        if region not in by_region:
            by_region[region] = _blank()
        by_region[region]["physical_capacity"] += int(cfg.get("capacity", 0))
        by_region[region]["operational_capacity"] += operational_cap_by_dept.get(
            name, int(cfg.get("capacity", 0)))
        by_region[region]["departments"].append(name)
        if available:
            by_region[region]["occupied"] += occupied_by_dept.get(name, 0)

    for region, row in by_region.items():
        denom = row["operational_capacity"]
        row["occupancy_rate"] = (
            round(row["occupied"] / denom, 3) if available and denom else None
        )

    return BaseResponse(data={
        "regions": by_region,
        "occupancy_available": available,
        # Every department of this hospital sits in one region; the other five
        # are structurally empty rather than merely unoccupied, and saying so
        # stops a reader mistaking a zero for "no patients today".
        "note": (
            "This is a single hospital. Regions other than the one it belongs "
            "to have no departments here, not zero occupancy."
        ),
        "capacity_note": (
            "physical_capacity is this hospital's bed establishment. "
            "operational_capacity is the denominator the bed register uses "
            "for the MIMIC replay, where several source care units map onto "
            "one Irish department — occupancy_rate uses that one, because it "
            "is what the occupancy count is measured against."
        ),
    })


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
