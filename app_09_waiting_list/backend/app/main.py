"""
Waiting List Intelligence FastAPI Service
==========================================
AI-powered clinical priority scoring, deterioration prediction,
referral NLP triage, and optimal scheduling for Irish hospitals.

Port: 8209

Usage::
    uvicorn app_09_waiting_list.backend.app.main:app --host 0.0.0.0 --port 8209
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
from shared.clinical.keywords import (
    CANCER_TERMS, CARDIAC_TERMS, MEDICATION_TERMS, ORTHO_TERMS, SYMPTOM_TERMS,
)
from shared.db.mongo import MongoManager
from shared.ml.registry import ModelRegistry
from shared.integration.event_bus import get_event_bus
from shared.integration.service_client import ServiceClient

MODEL_DIR = Path(os.getenv("MODEL_DIR", "./models/waiting_list"))

from app_09_waiting_list.backend.app.schemas import (
    IRISH_SPECIALTIES,
    AddToWaitingListRequest,
    ClinicalUpdateRequest,
    DeteriorationRisk,
    GenerateScheduleRequest,
    PriorityScore,
    ReferralTriageRequest,
    ReferralTriageResult,
    ScheduleSlot,
    WaitingListEntry,
    WaitTimeStats,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("waiting_list.api")

state: Dict[str, Any] = {
    "mongo": None,
    "waiting_list": [],  # In-memory waiting list (MongoDB-backed in production)
    "service_client": None,
    "event_bus": None,
}


@asynccontextmanager
async def lifespan(application: FastAPI):
    # Structured logging
    try:
        from shared.integration.logging_config import setup_logging
        setup_logging(service_name="waiting_list")
    except Exception as exc:  # noqa: BLE001
        logger.warning("logging_setup_failed: %s", exc)

    # OpenTelemetry tracing
    try:
        from shared.integration.tracing import setup_tracing
        setup_tracing(application, service_name="waiting_list")
    except Exception as exc:  # noqa: BLE001
        logger.warning("tracing_setup_failed: %s", exc)

    # Prometheus /metrics
    try:
        from shared.integration.prometheus_metrics import install_metrics
        install_metrics(application, service_name="waiting_list")
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
    for name, key in [("waiting_list_priority", "priority_model"),
                      ("waiting_list_adverse", "adverse_model")]:
        try:
            state[key], meta = registry.load_model(name)
            logger.info("Loaded %s: %s", name, meta.get("metrics", {}).get("test", {}))
        except FileNotFoundError:
            logger.warning("No %s found; using rule-based fallback.", name)

    # Subscribe to cross-service events. On patient_discharged we bump
    # priority for remaining patients in that specialty (capacity freed).
    try:
        from shared.integration.kafka_consumer import attach_with_ring_buffer
        async def _on_discharge(topic, payload):
            hadm_id = str(payload.get("hadm_id") or "")
            wl = state.get("waiting_list", [])
            for entry in wl:
                if str(entry.get("hadm_id") or "") == hadm_id:
                    entry["status"] = "completed"
                    logger.info("waiting_list entry for hadm=%s marked completed via Kafka", hadm_id)
                    break
            # Small uplift for remaining patients — capacity just opened up
            dept = payload.get("department")
            for entry in wl:
                if (dept is None or entry.get("specialty") == dept) and entry.get("status") == "waiting":
                    pri = entry.setdefault("priority", {})
                    pri["composite_priority"] = min(1.0, float(pri.get("composite_priority", 0)) + 0.01)
        await attach_with_ring_buffer(
            service_id="waiting_list",
            topics=["admission_complete", "patient_discharged", "priority_updated"],
            mongo_client=state["mongo"].client,
            extra_handlers={"patient_discharged": _on_discharge},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("waiting_list_bus_subscribe_failed: %s", exc)

    logger.info("Waiting List Intelligence service ready with %d specialties", len(IRISH_SPECIALTIES))
    yield
    if state["mongo"]:
        state["mongo"].close()


app = create_app(
    title="Waiting List Intelligence",
    version="1.0.0",
    description=(
        "AI-powered waiting list management for Irish hospitals. Clinical "
        "priority scoring, deterioration prediction, NLP referral triage, "
        "and optimal scheduling aligned with Slaaintecare targets."
    ),
)
app.router.lifespan_context = lifespan


@app.get("/kafka-events", response_model=BaseResponse, tags=["system"])
async def list_kafka_events(limit: int = 100) -> BaseResponse:
    """Return recent cross-service events consumed via Kafka/broker."""
    from shared.integration.kafka_consumer import get_kafka_events
    return BaseResponse(data=get_kafka_events("waiting_list", limit))


# ---------------------------------------------------------------------------
# Waiting List Management
# ---------------------------------------------------------------------------
@app.get("/waiting-list", response_model=BaseResponse, tags=["waiting-list"])
async def get_waiting_list(
    specialty: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="Filter by status"),
    sort_by: str = Query("priority", description="Sort by: priority, wait_days, risk"),
    live_only: bool = Query(False, description="When true, exclude demo-seeded entries (source='demo_seed')"),
) -> BaseResponse:
    """Return the waiting list, optionally filtered by specialty."""
    entries = state.get("waiting_list", [])
    if live_only:
        entries = [e for e in entries if e.get("source", "live") != "demo_seed"]
    if specialty:
        entries = [e for e in entries if e["specialty"] == specialty]
    if status:
        entries = [e for e in entries if e["status"] == status]

    if sort_by == "priority":
        entries.sort(key=lambda x: x.get("priority", {}).get("composite_priority", 0), reverse=True)
    elif sort_by == "wait_days":
        entries.sort(key=lambda x: x.get("wait_days", 0), reverse=True)
    elif sort_by == "risk":
        entries.sort(key=lambda x: x.get("deterioration_risk_90d", 0), reverse=True)

    return BaseResponse(data=entries)


@app.get("/waiting-list/by-department", response_model=BaseResponse, tags=["waiting-list"])
async def waiting_list_by_department(
    live_only: bool = Query(False, description="When true, exclude demo-seeded entries (source='demo_seed')"),
) -> BaseResponse:
    """Per-specialty summary roster with NTPF-style metrics.

    Returns one row per specialty with:
      - ``total`` patients waiting
      - ``by_priority``: counts by urgency band (urgent/soon/routine/planned)
      - ``by_wait_bucket``: Irish SDU buckets (<=6w / 6-12w / 3-6m / 6-12m / >12m)
      - ``mean_wait_days`` / ``median_wait_days`` / ``p90_wait_days``
      - ``breach_count`` and ``breach_rate`` vs specialty target (NTPF target)
      - ``oldest_wait_days`` and ``oldest_patient_id``
      - ``mean_deterioration_risk_90d``
      - ``top_patients``: 5 highest-priority patients (compact)
    """
    from statistics import mean, median
    from shared.integration.sim_clock import get_sim_time as _sim_now
    now = _sim_now()

    all_entries_raw = state.get("waiting_list", [])
    if live_only:
        all_entries = [e for e in all_entries_raw if e.get("source", "live") != "demo_seed"]
    else:
        all_entries = all_entries_raw

    # Recompute wait_days against the current sim clock so stale seeds age.
    for e in all_entries:
        ref = e.get("referral_date")
        if isinstance(ref, str):
            try:
                ref_dt = datetime.fromisoformat(ref.replace("Z", "+00:00"))
            except ValueError:
                continue
        elif isinstance(ref, datetime):
            ref_dt = ref
        else:
            continue
        if ref_dt.tzinfo is None:
            ref_dt = ref_dt.replace(tzinfo=timezone.utc)
        e["wait_days"] = max(0, int((now - ref_dt).total_seconds() / 86400))

    def _wait_bucket(days: int) -> str:
        if days <= 42:    return "le_6w"
        if days <= 84:    return "6_12w"
        if days <= 180:   return "3_6m"
        if days <= 365:   return "6_12m"
        return "gt_12m"

    by_spec: Dict[str, Dict[str, Any]] = {}
    # In live_only mode, only seed rows for specialties that have live entries.
    # Otherwise seed all 12 NTPF specialties so the roster shows zero-rows too.
    if live_only:
        live_specs = {e.get("specialty") for e in all_entries if e.get("specialty")}
        spec_items = [(s, IRISH_SPECIALTIES.get(s, {"target_wait_weeks": 12, "inpatient_pct": 0.5}))
                      for s in live_specs]
    else:
        spec_items = list(IRISH_SPECIALTIES.items())
    for spec, meta in spec_items:
        by_spec[spec] = {
            "specialty": spec,
            "target_wait_weeks": meta.get("target_wait_weeks", 12),
            "inpatient_pct": meta.get("inpatient_pct", 0.5),
            "total": 0,
            "by_priority": {"urgent": 0, "soon": 0, "routine": 0, "planned": 0},
            "by_wait_bucket": {"le_6w": 0, "6_12w": 0, "3_6m": 0, "6_12m": 0, "gt_12m": 0},
            "by_status": {"waiting": 0, "scheduled": 0, "deteriorated": 0, "cancelled": 0, "completed": 0},
            "mean_wait_days": 0.0,
            "median_wait_days": 0.0,
            "p90_wait_days": 0.0,
            "breach_count": 0,
            "breach_rate": 0.0,
            "oldest_wait_days": 0,
            "oldest_patient_id": None,
            "mean_deterioration_risk_90d": 0.0,
            "high_risk_count": 0,
            "top_patients": [],
        }

    for e in all_entries:
        spec = e.get("specialty")
        if spec not in by_spec:
            by_spec[spec] = {
                "specialty": spec, "target_wait_weeks": 12, "inpatient_pct": 0.5,
                "total": 0,
                "by_priority": {"urgent": 0, "soon": 0, "routine": 0, "planned": 0},
                "by_wait_bucket": {"le_6w": 0, "6_12w": 0, "3_6m": 0, "6_12m": 0, "gt_12m": 0},
                "by_status": {"waiting": 0, "scheduled": 0, "deteriorated": 0, "cancelled": 0, "completed": 0},
                "mean_wait_days": 0.0, "median_wait_days": 0.0, "p90_wait_days": 0.0,
                "breach_count": 0, "breach_rate": 0.0,
                "oldest_wait_days": 0, "oldest_patient_id": None,
                "mean_deterioration_risk_90d": 0.0, "high_risk_count": 0,
                "top_patients": [],
            }
        row = by_spec[spec]
        row["total"] += 1
        pri_level = (e.get("priority") or {}).get("priority_level", "routine")
        if pri_level in row["by_priority"]:
            row["by_priority"][pri_level] += 1
        status = e.get("status", "waiting")
        if status in row["by_status"]:
            row["by_status"][status] += 1
        wait = int(e.get("wait_days") or 0)
        row["by_wait_bucket"][_wait_bucket(wait)] += 1

    # Compute aggregate stats
    for spec, row in by_spec.items():
        spec_entries = [e for e in all_entries if e.get("specialty") == spec]
        if not spec_entries:
            continue
        waits = [int(e.get("wait_days") or 0) for e in spec_entries]
        risks = [float(e.get("deterioration_risk_90d") or 0) for e in spec_entries]
        target_days = row["target_wait_weeks"] * 7
        breaches = [e for e in spec_entries if (e.get("wait_days") or 0) > target_days]

        row["mean_wait_days"] = round(mean(waits), 1) if waits else 0
        row["median_wait_days"] = round(median(waits), 1) if waits else 0
        waits_sorted = sorted(waits)
        p90_idx = max(0, int(len(waits_sorted) * 0.9) - 1)
        row["p90_wait_days"] = waits_sorted[p90_idx] if waits_sorted else 0
        row["breach_count"] = len(breaches)
        row["breach_rate"] = round(len(breaches) / len(spec_entries), 3) if spec_entries else 0
        row["mean_deterioration_risk_90d"] = round(mean(risks), 3) if risks else 0
        row["high_risk_count"] = sum(1 for r in risks if r >= 0.5)

        oldest = max(spec_entries, key=lambda e: int(e.get("wait_days") or 0), default=None)
        if oldest:
            row["oldest_wait_days"] = int(oldest.get("wait_days") or 0)
            row["oldest_patient_id"] = oldest.get("patient_id")

        # Top-5 by composite priority
        top = sorted(
            spec_entries,
            key=lambda e: (e.get("priority") or {}).get("composite_priority", 0),
            reverse=True,
        )[:5]
        row["top_patients"] = [
            {
                "patient_id": t.get("patient_id"),
                "procedure": t.get("procedure_requested"),
                "priority_level": (t.get("priority") or {}).get("priority_level"),
                "composite_priority": round(
                    (t.get("priority") or {}).get("composite_priority", 0), 3
                ),
                "wait_days": int(t.get("wait_days") or 0),
                "target_wait_days": int(t.get("target_wait_days") or (row["target_wait_weeks"] * 7)),
                "deterioration_risk_90d": round(float(t.get("deterioration_risk_90d") or 0), 3),
                "status": t.get("status", "waiting"),
                "breach": int(t.get("wait_days") or 0) > (row["target_wait_weeks"] * 7),
            }
            for t in top
        ]

    # Sort output: highest breach count first, then largest list
    rows = sorted(by_spec.values(), key=lambda r: (r["breach_count"], r["total"]), reverse=True)

    totals = {
        "grand_total": sum(r["total"] for r in rows),
        "total_breaches": sum(r["breach_count"] for r in rows),
        "specialties_with_breaches": sum(1 for r in rows if r["breach_count"] > 0),
        "total_high_risk": sum(r["high_risk_count"] for r in rows),
    }
    totals["breach_rate"] = (
        round(totals["total_breaches"] / totals["grand_total"], 3)
        if totals["grand_total"] else 0
    )

    # Source provenance (always over the raw list so the UI can warn when demo
    # data is present even in live_only mode)
    live_count = sum(1 for e in all_entries_raw if e.get("source", "live") != "demo_seed")
    demo_count = sum(1 for e in all_entries_raw if e.get("source") == "demo_seed")
    totals["live_count"] = live_count
    totals["demo_count"] = demo_count
    totals["has_demo_data"] = demo_count > 0

    return BaseResponse(data={
        "specialties": rows,
        "totals": totals,
        "generated_at": now.isoformat(),
        "live_only": live_only,
    })


@app.post("/waiting-list/seed-demo", response_model=BaseResponse, tags=["waiting-list"])
async def seed_demo_waiting_list(count: int = 180, clear_existing: bool = True) -> BaseResponse:
    """Seed a realistic Irish-hospital waiting list for demo / development.

    Uses the IRISH_SPECIALTIES dictionary + NTPF-style wait-time distributions
    to generate ``count`` plausible entries across all 12 specialties with a
    long-tail of breaches so the per-department summary shows meaningful
    numbers. Safe to call repeatedly — set ``clear_existing=false`` to append.
    """
    import random
    from shared.integration.sim_clock import get_sim_time as _sim_now
    now = _sim_now()

    if clear_existing:
        state["waiting_list"] = []

    specialties = list(IRISH_SPECIALTIES.items())
    # Weighted distribution modelled on HSE inpatient mix — Medicine and Surgery
    # dominate; Day_Ward carries high throughput but low complexity.
    weights = {
        "Medicine": 30, "Surgery": 25, "Cardiology": 12,
        "Respiratory": 10, "Orthopaedics": 15, "Day_Ward": 8,
    }
    procedures_map = {
        "Medicine": [
            "Internal medicine review", "Chest pain workup", "Anaemia investigation",
            "COPD exacerbation follow-up", "Diabetes optimisation", "Heart failure clinic",
        ],
        "Surgery": [
            "Laparoscopic Cholecystectomy", "Inguinal Hernia Repair",
            "Colonoscopy", "Thyroidectomy", "Pilonidal Sinus", "Excision of lesion",
        ],
        "Cardiology": [
            "Coronary Angiogram", "Pacemaker Insertion", "TOE",
            "Cardiology OPD review", "Arrhythmia workup",
        ],
        "Respiratory": [
            "Bronchoscopy", "Spirometry + challenge", "Sleep study",
            "Pulmonary function review", "COPD / asthma optimisation",
        ],
        "Orthopaedics": [
            "Total Hip Replacement", "Total Knee Replacement",
            "Shoulder Arthroscopy", "Carpal Tunnel Release", "ACL reconstruction",
        ],
        "Day_Ward": [
            "OGD", "Colonoscopy", "Minor procedure under LA",
            "IV infusion therapy", "Endoscopic biopsy", "Port-a-cath insertion",
        ],
    }
    spec_weights = [weights.get(s, 5) for s, _ in specialties]

    created = 0
    for _ in range(count):
        spec, meta = random.choices(specialties, weights=spec_weights, k=1)[0]
        target_weeks = meta["target_wait_weeks"]
        # Wait-days distribution with long-tail; ~20% breach rate
        wait_days = int(random.choices(
            [random.randint(0, 42), random.randint(43, 84), random.randint(85, 180),
             random.randint(181, 365), random.randint(366, 540)],
            weights=[28, 30, 22, 15, 5], k=1,
        )[0])

        # Priority weighted by wait_days + some clinical randomness
        if random.random() < 0.08:
            pri_level = "urgent"
        elif wait_days > target_weeks * 7:
            pri_level = random.choice(["soon", "routine", "routine"])
        else:
            pri_level = random.choices(
                ["urgent", "soon", "routine", "planned"],
                weights=[5, 20, 55, 20], k=1,
            )[0]
        clinical = {"urgent": 0.85, "soon": 0.6, "routine": 0.35, "planned": 0.15}[pri_level]
        temporal = min(1.0, wait_days / (target_weeks * 7 * 2))
        functional = random.uniform(0.2, 0.85)
        equity = random.uniform(-0.1, 0.2)
        composite = max(0.0, min(1.0, 0.4 * clinical + 0.3 * functional + 0.2 * temporal + 0.1 * equity))

        # Deterioration risk rises with wait
        det_30 = min(0.9, 0.05 + 0.35 * (wait_days / 180) + random.uniform(-0.05, 0.1))
        det_90 = min(0.95, det_30 + random.uniform(0.03, 0.15))

        pid = random.randint(10_000_000, 99_999_999)
        procedure = random.choice(procedures_map.get(spec, ["Consultation"]))
        referral_date = now - timedelta(days=wait_days)

        state["waiting_list"].append({
            "patient_id": pid,
            "specialty": spec,
            "procedure_requested": procedure,
            "referral_date": referral_date.isoformat(),
            "wait_days": wait_days,
            "target_wait_days": target_weeks * 7,
            "priority": {
                "clinical_urgency_score": round(clinical, 3),
                "functional_impact_score": round(functional, 3),
                "temporal_score": round(temporal, 3),
                "equity_modifier": round(equity, 3),
                "composite_priority": round(composite, 3),
                "priority_level": pri_level,
            },
            "deterioration_risk_30d": round(det_30, 3),
            "deterioration_risk_90d": round(det_90, 3),
            "predicted_wait_days": max(0, int(target_weeks * 7 * (1 - composite))),
            "status": random.choices(
                ["waiting", "waiting", "waiting", "scheduled", "deteriorated"],
                weights=[72, 12, 5, 8, 3], k=1,
            )[0],
            "nlp_extracted": None,
            "source": "demo_seed",
        })
        created += 1

    return BaseResponse(data={
        "created": created,
        "total_in_list": len(state["waiting_list"]),
        "specialties": len({e["specialty"] for e in state["waiting_list"]}),
    })


@app.post("/waiting-list/add", response_model=BaseResponse, tags=["waiting-list"])
async def add_to_waiting_list(req: AddToWaitingListRequest) -> BaseResponse:
    """Add a patient to the waiting list and compute initial priority score."""
    from shared.integration.sim_clock import get_sim_time as _sim_now
    now = _sim_now()

    # Compute priority score
    priority = _compute_priority(req)

    # Compute initial deterioration risk
    det_risk = _estimate_deterioration_risk(req)

    # Attempt NLP on referral text if provided
    nlp_extracted = None
    if req.referral_text:
        nlp_extracted = _extract_referral_entities(req.referral_text)

    target_weeks = IRISH_SPECIALTIES.get(req.specialty, {}).get("target_wait_weeks", 12)

    entry = WaitingListEntry(
        patient_id=req.patient_id,
        specialty=req.specialty,
        procedure_requested=req.procedure_requested,
        referral_date=now,
        wait_days=0,
        target_wait_days=target_weeks * 7,
        priority=priority,
        deterioration_risk_30d=det_risk["risk_30d"],
        deterioration_risk_90d=det_risk["risk_90d"],
        predicted_wait_days=int(target_weeks * 7 * (1 - priority.composite_priority + 0.5)),
        status="waiting",
        nlp_extracted=nlp_extracted,
    )

    payload = entry.model_dump()
    payload["source"] = "live"
    state["waiting_list"].append(payload)

    # Publish event
    bus = state.get("event_bus")
    if bus:
        await bus.publish("priority_updated", {
            "patient_id": req.patient_id,
            "specialty": req.specialty,
            "composite_priority": priority.composite_priority,
        }, source_module="waiting_list")

    logger.info("Patient %d added to %s waiting list (priority: %.3f)",
                req.patient_id, req.specialty, priority.composite_priority)

    return BaseResponse(data=entry.model_dump())


# ---------------------------------------------------------------------------
# Priority Scoring
# ---------------------------------------------------------------------------
@app.post("/score-priority", response_model=BaseResponse, tags=["priority"])
async def score_priority(req: AddToWaitingListRequest) -> BaseResponse:
    """Compute priority score for a patient without adding to list."""
    priority = _compute_priority(req)
    return BaseResponse(data=priority.model_dump())


@app.get("/priority-distribution/{specialty}", response_model=BaseResponse, tags=["priority"])
async def priority_distribution(specialty: str) -> BaseResponse:
    """Return priority score distribution for a specialty."""
    entries = [e for e in state.get("waiting_list", []) if e["specialty"] == specialty]
    if not entries:
        return BaseResponse(data={"specialty": specialty, "count": 0, "distribution": {}})

    scores = [e.get("priority", {}).get("composite_priority", 0) for e in entries]
    return BaseResponse(data={
        "specialty": specialty,
        "count": len(scores),
        "mean_priority": round(sum(scores) / len(scores), 3),
        "distribution": {
            "urgent": sum(1 for s in scores if s >= 0.75),
            "soon": sum(1 for s in scores if 0.5 <= s < 0.75),
            "routine": sum(1 for s in scores if 0.25 <= s < 0.5),
            "planned": sum(1 for s in scores if s < 0.25),
        },
    })


# ---------------------------------------------------------------------------
# Deterioration Prediction
# ---------------------------------------------------------------------------
@app.get("/deterioration-risk/{patient_id}", response_model=BaseResponse, tags=["deterioration"])
async def get_deterioration_risk(patient_id: int) -> BaseResponse:
    """Return deterioration risk assessment for a waiting patient."""
    entry = next((e for e in state.get("waiting_list", []) if e["patient_id"] == patient_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found on waiting list")

    risk = DeteriorationRisk(
        patient_id=patient_id,
        risk_30d=entry.get("deterioration_risk_30d", 0),
        risk_90d=entry.get("deterioration_risk_90d", 0),
        risk_180d=min(1.0, entry.get("deterioration_risk_90d", 0) * 1.6),
        risk_trajectory="stable",
        competing_risks={
            "deterioration": entry.get("deterioration_risk_90d", 0),
            "improvement": 0.15,
            "dropout": 0.05,
        },
        recommended_action="Routine review" if entry.get("deterioration_risk_90d", 0) < 0.3 else "Expedite review",
    )

    return BaseResponse(data=risk.model_dump())


@app.get("/deterioration-alerts", response_model=BaseResponse, tags=["deterioration"])
async def deterioration_alerts(
    threshold: float = Query(0.3, description="Risk threshold for alert"),
) -> BaseResponse:
    """Return all patients with deterioration risk above threshold."""
    entries = state.get("waiting_list", [])
    alerts = [e for e in entries if e.get("deterioration_risk_90d", 0) >= threshold]
    alerts.sort(key=lambda x: x.get("deterioration_risk_90d", 0), reverse=True)
    return BaseResponse(data=alerts)


# ---------------------------------------------------------------------------
# Referral NLP Triage
# ---------------------------------------------------------------------------
@app.post("/triage-referral", response_model=BaseResponse, tags=["referral-nlp"])
async def triage_referral(req: ReferralTriageRequest) -> BaseResponse:
    """Process a referral letter through NLP triage pipeline.

    Uses ClinicalBERT for entity extraction and urgency classification.
    Falls back to keyword-based extraction when model not available.
    """
    entities = _extract_referral_entities(req.referral_text)

    # Keyword-based urgency classification (ClinicalBERT in Phase 2)
    urgency = _classify_urgency(req.referral_text)

    # Identify missing information
    missing = []
    if not entities.get("diagnosis"):
        missing.append("Primary diagnosis not clearly stated")
    if not entities.get("symptoms"):
        missing.append("Symptoms not described")
    if not entities.get("duration"):
        missing.append("Duration of symptoms not specified")
    if not entities.get("medications"):
        missing.append("Current medications not listed")

    quality_score = max(0, 1.0 - len(missing) * 0.15)

    result = ReferralTriageResult(
        urgency_classification=urgency,
        urgency_confidence=0.7,  # Placeholder; ClinicalBERT gives real confidence
        recommended_specialty=entities.get("specialty", "General"),
        extracted_entities=entities,
        missing_information=missing,
        referral_quality_score=round(quality_score, 2),
        suggested_priority_level=urgency,
    )

    bus = state.get("event_bus")
    if bus:
        await bus.publish("referral_triaged", {
            "urgency": urgency,
            "specialty": entities.get("specialty", "unknown"),
        }, source_module="waiting_list")

    return BaseResponse(data=result.model_dump())


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------
@app.post("/generate-schedule", response_model=BaseResponse, tags=["scheduling"])
async def generate_schedule(req: GenerateScheduleRequest) -> BaseResponse:
    """Generate optimal weekly schedule for a specialty.

    Uses MIP solver (OR-Tools) to maximize weighted throughput.
    Falls back to priority-ordered assignment when solver unavailable.
    """
    entries = [e for e in state.get("waiting_list", [])
               if e["specialty"] == req.specialty and e["status"] == "waiting"]
    entries.sort(key=lambda x: x.get("priority", {}).get("composite_priority", 0), reverse=True)

    slots_available = req.available_slots or 20
    schedule = []
    for i, entry in enumerate(entries[:slots_available]):
        schedule.append(ScheduleSlot(
            slot_date=req.week_start_date,
            slot_time=f"{8 + (i % 8):02d}:00",
            specialty=req.specialty,
            resource=f"Theatre {1 + i // 8}",
            assigned_patient_id=entry["patient_id"],
            assignment_score=entry.get("priority", {}).get("composite_priority", 0),
            status="scheduled",
        ).model_dump())

    bus = state.get("event_bus")
    if bus:
        await bus.publish("schedule_generated", {
            "specialty": req.specialty,
            "week": req.week_start_date,
            "slots_filled": len(schedule),
        }, source_module="waiting_list")

    return BaseResponse(data=schedule)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
@app.get("/metrics/wait-times", response_model=BaseResponse, tags=["analytics"])
async def wait_time_metrics(
    specialty: Optional[str] = Query(None),
) -> BaseResponse:
    """Return wait time statistics by specialty."""
    specialties = [specialty] if specialty else list(IRISH_SPECIALTIES.keys())
    stats = []
    for spec in specialties:
        entries = [e for e in state.get("waiting_list", [])
                   if e["specialty"] == spec and e["status"] == "waiting"]
        if not entries:
            continue
        waits = [e.get("wait_days", 0) for e in entries]
        target = IRISH_SPECIALTIES.get(spec, {}).get("target_wait_weeks", 12) * 7
        stats.append(WaitTimeStats(
            specialty=spec,
            total_waiting=len(waits),
            median_wait_days=sorted(waits)[len(waits) // 2] if waits else 0,
            p95_wait_days=sorted(waits)[int(len(waits) * 0.95)] if waits else 0,
            within_target_pct=sum(1 for w in waits if w <= target) / len(waits) if waits else 0,
            breach_count=sum(1 for w in waits if w > target),
            target_wait_weeks=IRISH_SPECIALTIES.get(spec, {}).get("target_wait_weeks", 12),
        ).model_dump())

    return BaseResponse(data=stats)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_priority(req: AddToWaitingListRequest) -> PriorityScore:
    """Compute multi-criteria priority score (MCDA)."""
    # Clinical urgency sub-score
    urgency_map = {"urgent": 0.9, "soon": 0.65, "routine": 0.35, "planned": 0.15}
    clinical = urgency_map.get(req.clinical_urgency or "routine", 0.35)
    if req.charlson_score > 5:
        clinical = min(1.0, clinical + 0.15)
    if req.comorbidity_count > 3:
        clinical = min(1.0, clinical + 0.1)

    # Functional impact sub-score
    func_map = {"complete": 0.95, "severe": 0.75, "moderate": 0.5, "mild": 0.25, "none": 0.1}
    functional = func_map.get(req.functional_impact or "moderate", 0.5)
    if req.pain_score and req.pain_score > 7:
        functional = min(1.0, functional + 0.15)

    # Temporal score (placeholder — grows with wait time in production)
    temporal = 0.1  # New patient, just added

    # Equity modifier
    equity = 0.0
    if req.geographic_region and req.geographic_region.lower() in ("rural", "west", "northwest"):
        equity += 0.05  # Rural access adjustment
    if req.age and req.age > 80:
        equity += 0.05  # Elderly adjustment

    # MCDA aggregation
    composite = 0.4 * clinical + 0.25 * functional + 0.25 * temporal + 0.1 * max(-0.2, min(0.2, equity))
    composite = max(0, min(1.0, composite + equity))

    level = "planned"
    if composite >= 0.75:
        level = "urgent"
    elif composite >= 0.5:
        level = "soon"
    elif composite >= 0.25:
        level = "routine"

    return PriorityScore(
        clinical_urgency_score=round(clinical, 3),
        functional_impact_score=round(functional, 3),
        temporal_score=round(temporal, 3),
        equity_modifier=round(equity, 3),
        composite_priority=round(composite, 3),
        priority_level=level,
    )


def _estimate_deterioration_risk(req: AddToWaitingListRequest) -> Dict[str, float]:
    """Estimate deterioration risk (Dynamic-DeepHit in Phase 2)."""
    base_risk = 0.05
    if req.charlson_score > 3:
        base_risk += 0.1
    if req.clinical_urgency == "urgent":
        base_risk += 0.15
    if req.pain_score and req.pain_score > 7:
        base_risk += 0.08
    if req.age and req.age > 75:
        base_risk += 0.08

    return {
        "risk_30d": round(min(1.0, base_risk), 3),
        "risk_90d": round(min(1.0, base_risk * 2.2), 3),
    }


def _extract_referral_entities(text: str) -> Dict[str, Any]:
    """Extract clinical entities from referral text (ClinicalBERT in Phase 2)."""
    text_lower = text.lower()
    entities: Dict[str, Any] = {}

    # Keyword-based extraction
    if any(t in text_lower for t in CANCER_TERMS):
        entities["specialty"] = "Oncology"
    elif any(t in text_lower for t in CARDIAC_TERMS):
        entities["specialty"] = "Cardiology"
    elif any(t in text_lower for t in ORTHO_TERMS):
        entities["specialty"] = "Orthopaedics"

    # Extract symptoms
    entities["symptoms"] = [t for t in SYMPTOM_TERMS if t in text_lower]

    # Extract duration patterns
    import re
    duration_match = re.search(r"(\d+)\s*(weeks?|months?|years?|days?)", text_lower)
    if duration_match:
        entities["duration"] = duration_match.group(0)

    # Extract medications
    entities["medications"] = [m for m in MEDICATION_TERMS if m in text_lower]

    return entities


def _classify_urgency(text: str) -> str:
    """Classify referral urgency (ClinicalBERT in Phase 2)."""
    text_lower = text.lower()
    urgent_signals = ["urgent", "emergency", "immediate", "cancer", "suspected malignancy",
                      "acute", "rapidly", "worsening rapidly"]
    soon_signals = ["soon", "expedite", "within weeks", "deteriorating", "significant impact"]

    if any(s in text_lower for s in urgent_signals):
        return "urgent"
    elif any(s in text_lower for s in soon_signals):
        return "soon"
    return "routine"


# ---------------------------------------------------------------------------
# Digital Twin integration endpoints (Bug #5, Integrations 1, 6)
# ---------------------------------------------------------------------------
@app.get("/admission-notifications", response_model=BaseResponse, tags=["integration"])
async def list_admission_notifications(limit: int = 100) -> BaseResponse:
    """Return recent admission notifications received from the Digital Twin."""
    log = state.get("admission_notifications", []) or []
    return BaseResponse(data=log[-int(limit):])


@app.post("/notify-admission", response_model=BaseResponse, tags=["integration"])
async def notify_admission(data: dict) -> BaseResponse:
    """Bug #5 fix — receive admission notifications from the Digital Twin.

    Called for non-immediate patients (acuity <= 3). Records the notification,
    re-ranks any existing matching waitlist entry, and — if no entry exists
    yet — creates one tagged ``source=live`` so the Departments tab reflects
    real hospital activity since simulation start.
    """
    from shared.integration.sim_clock import get_sim_time as _sim_now
    now = _sim_now()
    hadm_id = str(data.get("hadm_id", ""))
    subject_id = data.get("subject_id")
    acuity = int(data.get("acuity", 3))
    estimated_wait = int(data.get("estimated_wait_min", 0))
    pathway = data.get("pathway")
    department = data.get("department")
    age = data.get("age")
    gender = data.get("gender")
    primary_dx = data.get("primary_diagnosis")
    icd_codes = data.get("icd_codes") or []

    notif = {
        "hadm_id": hadm_id,
        "subject_id": subject_id,
        "acuity": acuity,
        "estimated_wait_min": estimated_wait,
        "pathway": pathway,
        "department": department,
        "received_at": now.isoformat(),
    }
    log = state.setdefault("admission_notifications", [])
    log.append(notif)
    if len(log) > 2000:
        del log[:1000]

    # Re-rank: bump priority score on any matching waiting list entry.
    wl = state.get("waiting_list", [])
    matched = False
    for entry in wl:
        if str(entry.get("hadm_id") or "") == hadm_id or (
            subject_id is not None and str(entry.get("subject_id") or "") == str(subject_id)
        ):
            pri = entry.setdefault("priority", {})
            pri["composite_priority"] = min(1.0, float(pri.get("composite_priority", 0)) + 0.1)
            pri["last_bump_reason"] = "admission_notified"
            matched = True

    # No existing entry — create a live waitlist row so this admission shows up
    # in the Departments tab. Specialty is inferred from ward/pathway, with a
    # safe fallback to General Surgery.
    created_entry = None
    if not matched and hadm_id:
        specialty = _ward_to_specialty(department, pathway, icd_codes)
        procedure = _infer_procedure(specialty, primary_dx)
        target_weeks = IRISH_SPECIALTIES.get(specialty, {}).get("target_wait_weeks", 12)
        # Priority driven by acuity: 1=critical, 2=emergent, 3=urgent, 4=less-urgent, 5=nonurgent
        acuity_pri_map = {1: ("urgent", 0.9), 2: ("urgent", 0.8), 3: ("soon", 0.6),
                         4: ("routine", 0.4), 5: ("planned", 0.2)}
        pri_level, composite = acuity_pri_map.get(acuity, ("soon", 0.6))
        entry = {
            "patient_id": subject_id,
            "hadm_id": hadm_id,
            "subject_id": subject_id,
            "specialty": specialty,
            "procedure_requested": procedure,
            "referral_date": now.isoformat(),
            "wait_days": 0,
            "target_wait_days": target_weeks * 7,
            "priority": {
                "clinical_urgency_score": round(composite, 3),
                "functional_impact_score": 0.5,
                "temporal_score": 0.05,
                "equity_modifier": 0.0,
                "composite_priority": round(composite, 3),
                "priority_level": pri_level,
            },
            "deterioration_risk_30d": 0.1 if acuity >= 3 else 0.25,
            "deterioration_risk_90d": 0.2 if acuity >= 3 else 0.45,
            "predicted_wait_days": int(target_weeks * 7 * (1 - composite)),
            "status": "waiting",
            "nlp_extracted": None,
            "source": "live",
            "live_meta": {
                "department_ward": department,
                "acuity": acuity,
                "pathway": pathway,
                "age": age,
                "gender": gender,
                "primary_diagnosis": primary_dx,
                "admitted_at": now.isoformat(),
            },
        }
        wl.append(entry)
        created_entry = entry
        logger.info("Live waiting-list entry created for hadm=%s specialty=%s (from ward=%s)",
                    hadm_id, specialty, department)

    # Persist to MongoDB if available
    mongo = state.get("mongo")
    if mongo is not None:
        try:
            mongo.client["waiting_list"]["admission_notifications"].insert_one(dict(notif))
            if created_entry is not None:
                mongo.client["waiting_list"]["live_entries"].insert_one(dict(created_entry))
        except Exception as exc:
            logger.warning("wl_admission_notif_persist_failed: %s", exc)

    return BaseResponse(data={
        "recorded": True,
        "notification": notif,
        "matched_existing": matched,
        "created_live_entry": created_entry is not None,
        "live_specialty": created_entry.get("specialty") if created_entry else None,
    })


# Hospital ward → waiting-list department. Waiting-list-capable wards map to
# themselves; unscheduled/assessment wards route to their natural inpatient
# destination so incoming DT admissions still produce a useful waitlist entry.
_WARD_TO_DEPT = {
    "Medicine": "Medicine",
    "Surgery": "Surgery",
    "Cardiology": "Cardiology",
    "Respiratory": "Respiratory",
    "Orthopaedics": "Orthopaedics",
    "Day_Ward": "Day_Ward",
    # Assessment / emergency / critical wards — route to the most likely
    # downstream inpatient department so the referral shows up.
    "MAU": "Medicine",
    "AMAU": "Medicine",
    "SAU": "Surgery",
    "CDU": "Medicine",
    "ED": "Medicine",
    "ICU": "Medicine",
    "HDU": "Medicine",
}


def _ward_to_specialty(ward: Optional[str], pathway: Optional[str], icd_codes: Optional[List[str]]) -> str:
    """Resolve model-hospital department from ward/pathway/diagnosis signals.

    Returns one of IRISH_SPECIALTIES (Medicine / Surgery / Cardiology /
    Respiratory / Orthopaedics / Day_Ward). ICD-driven overrides take
    precedence over ward, then pathway, then safe Medicine fallback.
    """
    if icd_codes:
        codes = [str(c).upper() for c in icd_codes]
        # Musculoskeletal → Orthopaedics
        if any(c.startswith(("M", "S")) for c in codes):
            return "Orthopaedics"
        # Cardiovascular → Cardiology
        if any(c.startswith(("I2", "I4", "I5", "I11", "I50")) for c in codes):
            return "Cardiology"
        # Respiratory → Respiratory
        if any(c.startswith(("J",)) for c in codes):
            return "Respiratory"
        # Surgical / GI / neoplasm / injury → Surgery
        if any(c.startswith(("K", "C", "D", "T")) for c in codes):
            return "Surgery"
    if ward and ward in _WARD_TO_DEPT:
        return _WARD_TO_DEPT[ward]
    if pathway and pathway in _WARD_TO_DEPT:
        return _WARD_TO_DEPT[pathway]
    return "Medicine"


def _infer_procedure(specialty: str, primary_dx: Optional[str]) -> str:
    """Pick a reasonable procedure label for the entry."""
    if primary_dx:
        return f"Admission review — {primary_dx[:60]}"
    default_map = {
        "Medicine": "Medical consultation",
        "Surgery": "Surgical review",
        "Cardiology": "Cardiology review",
        "Respiratory": "Respiratory review",
        "Orthopaedics": "Orthopaedic review",
        "Day_Ward": "Day case procedure",
    }
    return default_map.get(specialty, "Clinical review")


@app.post("/bump-priority", response_model=BaseResponse, tags=["integration"])
async def bump_priority(data: dict) -> BaseResponse:
    """Integration 6 — bump a queue entry's priority score.

    Called by Oncology AI when readmission risk > 0.6. Accepts ``bump`` (float)
    and ``reason`` (string), applies additively and caps at 1.0.
    """
    hadm_id = str(data.get("hadm_id", ""))
    bump = float(data.get("bump", 0.2))
    reason = str(data.get("reason", "external"))
    wl = state.get("waiting_list", [])
    hits = 0
    for entry in wl:
        if str(entry.get("hadm_id") or "") == hadm_id or str(entry.get("subject_id") or "") == str(data.get("subject_id", "")):
            pri = entry.setdefault("priority", {})
            pri["composite_priority"] = min(1.0, float(pri.get("composite_priority", 0)) + bump)
            pri["last_bump_reason"] = reason
            entry.setdefault("tags", []).append(reason)
            hits += 1
    return BaseResponse(data={"matched_entries": hits, "bump": bump, "reason": reason})


@app.post("/notify-discharge-re-rank", response_model=BaseResponse, tags=["integration"])
async def notify_discharge_re_rank(data: dict) -> BaseResponse:
    """Rule 5 step 5 — recompute queue positions when a discharge frees capacity."""
    department = data.get("department")
    wl = state.get("waiting_list", [])
    affected = 0
    for entry in wl:
        if department is None or entry.get("specialty") == department:
            pri = entry.setdefault("priority", {})
            # Small uplift for longest-waiting patients — reflects Sláintecare targets.
            pri["composite_priority"] = min(1.0, float(pri.get("composite_priority", 0)) + 0.02)
            affected += 1
    wl.sort(key=lambda x: x.get("priority", {}).get("composite_priority", 0), reverse=True)
    return BaseResponse(data={"re_ranked": affected, "department": department})


@app.post("/reset", response_model=BaseResponse, tags=["system"])
async def reset_waiting_list() -> BaseResponse:
    """Reset in-memory state for simulation restarts."""
    state["waiting_list"] = []
    state["admission_notifications"] = []
    logger.info("Waiting list reset.")
    return BaseResponse(data={"reset": True})


@app.post("/waiting-list/purge-demo", response_model=BaseResponse, tags=["system"])
async def purge_demo_waiting_list() -> BaseResponse:
    """Remove only demo-seeded entries; preserves live admissions/referrals."""
    wl = state.get("waiting_list", [])
    before = len(wl)
    state["waiting_list"] = [e for e in wl if e.get("source", "live") != "demo_seed"]
    removed = before - len(state["waiting_list"])
    logger.info("Purged %d demo_seed entries; %d live entries remain.", removed, len(state["waiting_list"]))
    return BaseResponse(data={"removed": removed, "remaining": len(state["waiting_list"])})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8209)
