"""Predictive Deterioration Monitor — port 8220.

Routes vitals through the correct Irish early-warning score:

  - PEWS   — patients under 16 years (NCEC NCG No. 1)
  - IMEWS  — pregnant or ≤ 6 weeks post-partum (NCEC NCG No. 4)
  - NEWS2  — adult inpatients (RCP / HSE NEWS2)

Adds:
  - trended NEWS2 (slope over trailing 4 h)
  - score-aware debouncer (fires immediately on rising score)
  - escalation acknowledgement loop with SBAR capture
  - durable persistence to MongoDB (survives service restart)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fastapi import FastAPI, HTTPException, Query

from shared.api.base import BaseResponse, PrivacyNotice, create_app
from shared.clinical.news2 import compute_news2, compute_news2_trend
from shared.clinical.pews import compute_pews
from shared.clinical.imews import compute_imews
from shared.db.mongo import MongoManager
from shared.integration.debouncer import ScoreAwareDebouncer
from shared.integration.event_bus import get_event_bus
from shared.integration.service_client import ServiceClient
from shared.integration.sim_clock import get_sim_time

logger = logging.getLogger("deterioration_monitor")


PRIVACY = PrivacyNotice(
    data_collected=[
        "vitals", "news2_scores", "pews_scores", "imews_scores",
        "escalation_events", "acknowledgements",
    ],
    legal_basis="GDPR Art. 9(2)(h) (healthcare); HSE clinical governance; NCEC NCG #1, #4",
    retention_period="36 months (Irish clinical governance standards)",
)


_state: Dict[str, Any] = {
    "active_alerts": {},         # hadm_id → latest snapshot
    "history": defaultdict(list),
    "escalations": {},            # escalation_id → record
    "acknowledgements": [],       # ack audit trail
    "client": None,
    "mongo": None,
    "debouncer": None,
}

# ---------------------------------------------------------------------------
# Sim-only auto-ack governance
#
# Under NCEC NCG #1 + EU AI Act Art. 14, every deterioration escalation
# must have a clinician acknowledgement + SBAR note. That's enforced in
# production. For demos we want the unack'd counter to drain so reviewers
# don't look at a pile of red indicators — hence this toggle.
#
# Default policy:
#   * SIMULATION mode → auto-ack ON by default (safe, labelled, audit-filtered)
#   * PRODUCTION mode → auto-ack INERT regardless of flag (hard gate)
#
# Override with DETERIORATION_AUTO_ACK_IN_SIM=0|false|no to disable the
# default-on behaviour in sim.
#
# Guard-rails:
#   1. Only effective when DEPLOYMENT_MODE=simulation (hard gate at check time)
#   2. Synthetic SBAR is clearly marked `auto_ack=True` + clinician.role="sim_autoack"
#      so audit pipelines can filter it out of clinical KPIs
# ---------------------------------------------------------------------------
def _parse_bool_env(name: str, default: bool) -> bool:
    """Parse a boolean env var. Missing or blank → *default*; anything
    else interpreted case-insensitively (``1/true/yes/on`` vs ``0/false/no/off``)."""
    raw = os.environ.get(name, "").strip().lower()
    if raw == "":
        return default
    return raw in {"1", "true", "yes", "on"}


_governance_config: Dict[str, Any] = {
    # Default ON in sim so demos don't pile up PENDING alerts. Override
    # with DETERIORATION_AUTO_ACK_IN_SIM=0 for a strict-manual sim run.
    "auto_ack_in_sim": _parse_bool_env("DETERIORATION_AUTO_ACK_IN_SIM", default=True),
    "auto_ack_delay_seconds": int(os.environ.get("DETERIORATION_AUTO_ACK_DELAY_S", "120")),
    "last_updated_at": None,
    "last_updated_by": "env",
}


def _is_simulation_mode() -> bool:
    """Hard gate — auto-ack can NEVER fire outside simulation mode."""
    return os.environ.get("DEPLOYMENT_MODE", "simulation").lower() == "simulation"

# Collections persisted to MongoDB.MIMIC_SIM
COLL_ACTIVE = "deterioration_active_alerts"
COLL_HISTORY = "deterioration_history"
COLL_ESC = "deterioration_escalations"
COLL_ACK = "deterioration_acknowledgements"


def _now() -> str:
    return get_sim_time().isoformat()


def _wall_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------
def _mongo_db():
    mongo: MongoManager = _state.get("mongo")
    if mongo is None:
        return None
    try:
        return mongo.client["MIMIC_SIM"]
    except Exception:
        return None


def _persist_snapshot(snapshot: Dict[str, Any]) -> None:
    db = _mongo_db()
    if db is None:
        return
    try:
        hadm_id = snapshot["hadm_id"]
        db[COLL_ACTIVE].update_one(
            {"hadm_id": hadm_id},
            {"$set": snapshot},
            upsert=True,
        )
        db[COLL_HISTORY].insert_one({**snapshot, "recorded_at": _wall_now()})
    except Exception as exc:  # noqa: BLE001
        logger.warning("deterioration_persist_failed: %s", exc)


def _persist_escalation(record: Dict[str, Any]) -> None:
    db = _mongo_db()
    if db is None:
        return
    try:
        db[COLL_ESC].insert_one({**record, "recorded_at": _wall_now()})
    except Exception as exc:  # noqa: BLE001
        logger.warning("deterioration_esc_persist_failed: %s", exc)


def _persist_ack(record: Dict[str, Any]) -> None:
    db = _mongo_db()
    if db is None:
        return
    try:
        db[COLL_ACK].insert_one({**record, "recorded_at": _wall_now()})
    except Exception as exc:  # noqa: BLE001
        logger.warning("deterioration_ack_persist_failed: %s", exc)


def _restore_from_mongo() -> None:
    """On startup, rehydrate in-memory state from persisted collections."""
    db = _mongo_db()
    if db is None:
        logger.info("deterioration: no mongo — starting with empty state")
        return
    try:
        for doc in db[COLL_ACTIVE].find({}):
            doc.pop("_id", None)
            hadm = str(doc.get("hadm_id"))
            _state["active_alerts"][hadm] = doc
            # Seed debouncer with prior score to prevent alert storm on restart
            total = (doc.get("score") or {}).get("total", 0)
            _state["debouncer"].record(hadm, score=int(total))

        # Rehydrate the trailing history window (last 2 h per patient) so the
        # trend calculation has enough data on the first post-restart vital.
        cutoff = _wall_now().timestamp() - 2 * 60 * 60
        for doc in db[COLL_HISTORY].find({}).sort("recorded_at", -1).limit(5000):
            doc.pop("_id", None)
            doc.pop("recorded_at", None)
            hadm = str(doc.get("hadm_id"))
            _state["history"][hadm].append(doc)

        # Most recent escalations (last 500)
        for doc in db[COLL_ESC].find({}).sort("recorded_at", -1).limit(500):
            doc.pop("_id", None)
            doc.pop("recorded_at", None)
            eid = doc.get("escalation_id")
            if eid:
                _state["escalations"][eid] = doc

        logger.info(
            "deterioration: restored %d active alerts, %d history entries, %d escalations",
            len(_state["active_alerts"]),
            sum(len(v) for v in _state["history"].values()),
            len(_state["escalations"]),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("deterioration_restore_failed: %s", exc)


@asynccontextmanager
async def lifespan(application: FastAPI):
    # Structured logging
    try:
        from shared.integration.logging_config import setup_logging
        setup_logging(service_name="deterioration")
    except Exception as exc:  # noqa: BLE001
        logger.warning("logging_setup_failed: %s", exc)

    # OpenTelemetry tracing
    try:
        from shared.integration.tracing import setup_tracing
        setup_tracing(application, service_name="deterioration")
    except Exception as exc:  # noqa: BLE001
        logger.warning("tracing_setup_failed: %s", exc)

    # Prometheus /metrics
    try:
        from shared.integration.prometheus_metrics import install_metrics
        install_metrics(application, service_name="deterioration")
    except Exception as exc:  # noqa: BLE001
        logger.warning("prometheus_metrics_install_failed: %s", exc)

    # Anchor SimClock to data_ingestion's authoritative clock.
    try:
        from shared.integration.sim_clock import attach_remote_clock
        await attach_remote_clock()
    except Exception as exc:  # noqa: BLE001
        logger.warning("sim_clock_attach_remote_failed: %s", exc)

    _state["client"] = ServiceClient()
    _state["debouncer"] = ScoreAwareDebouncer(cooldown_s=300, rise_threshold=1)
    try:
        _state["mongo"] = MongoManager()
        _restore_from_mongo()
    except Exception as exc:  # noqa: BLE001
        logger.warning("deterioration_mongo_init_failed: %s", exc)
        _state["mongo"] = None

    # One-shot purge of stale entries — when the service starts, drop any
    # alerts/escalations whose hadm_id is no longer in MIMIC_SIM.admissions
    # with status != "discharged". This catches the historical accumulation
    # from prior runs without a discharge handler (we measured 891 entries
    # against 153 currently-active sim patients, ~5.8× over-count).
    try:
        db = _mongo_db()
        if db is not None:
            mimic_sim = db.client["MIMIC_SIM"]
            active_hadms = {
                str(a.get("hadm_id") or "")
                for a in mimic_sim["admissions"].find(
                    {"status": {"$ne": "discharged"}},
                    {"_id": 0, "hadm_id": 1},
                )
                if a.get("hadm_id")
            }
            stale_alerts = [
                hid for hid in list(_state["active_alerts"].keys())
                if hid not in active_hadms
            ]
            for hid in stale_alerts:
                _state["active_alerts"].pop(hid, None)
            stale_esc = [
                eid for eid, rec in list(_state["escalations"].items())
                if str(rec.get("hadm_id") or "") not in active_hadms
            ]
            for eid in stale_esc:
                _state["escalations"].pop(eid, None)
            try:
                db[COLL_ACTIVE].delete_many({"hadm_id": {"$nin": list(active_hadms)}})
                db[COLL_ESC].update_many(
                    {"hadm_id": {"$nin": list(active_hadms)}, "acknowledged": {"$ne": True}},
                    {"$set": {"acknowledged": True, "acknowledged_via": "auto:startup_purge"}},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("startup_purge_mongo_failed: %s", exc)
            logger.info(
                "startup_purge dropped %d stale alerts + %d escalations (%d active hadms in sim)",
                len(stale_alerts), len(stale_esc), len(active_hadms),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("startup_purge_failed: %s", exc)

    # Subscribe to Kafka — admissions begin tracking, discharges stop.
    # Without a discharge handler, ``_state["active_alerts"]`` would
    # grow unbounded (we measured 891 entries against 153 currently-
    # active sim patients — every patient who was *ever* alerting
    # stayed in the dict). The handler purges per-patient alert state
    # plus any open escalations on discharge.
    try:
        if _state.get("mongo"):
            from shared.integration.kafka_consumer import attach_with_ring_buffer

            def _resolve_hid(payload):
                return str(
                    payload.get("hadm_id")
                    or payload.get("subject_id")
                    or payload.get("patient_id")
                    or ""
                )

            async def _on_discharge(_topic, payload):
                hid = _resolve_hid(payload)
                if not hid:
                    return
                removed = False
                if hid in _state["active_alerts"]:
                    _state["active_alerts"].pop(hid, None)
                    removed = True
                # Drop any open escalations for this hadm so the
                # ``unacknowledged_escalations`` counter doesn't carry
                # stale rows for discharged patients.
                stale_esc = [
                    eid for eid, rec in _state["escalations"].items()
                    if str(rec.get("hadm_id") or "") == hid
                ]
                for eid in stale_esc:
                    _state["escalations"].pop(eid, None)
                # Best-effort Mongo cleanup so a service restart doesn't
                # rehydrate the now-discharged patient.
                db = _mongo_db() if "_mongo_db" in globals() else None
                if db is not None:
                    try:
                        db[COLL_ACTIVE].delete_many({"hadm_id": hid})
                        db[COLL_ESC].update_many(
                            {"hadm_id": hid, "acknowledged": {"$ne": True}},
                            {"$set": {"acknowledged": True, "acknowledged_via": "auto:discharge"}},
                        )
                    except Exception:  # noqa: BLE001
                        pass
                if removed or stale_esc:
                    logger.info(
                        "deterioration purged on discharge hadm=%s alerts=%s escalations=%d",
                        hid, removed, len(stale_esc),
                    )

            await attach_with_ring_buffer(
                service_id="deterioration",
                topics=["admission_complete", "patient_discharged", "patient_transferred"],
                mongo_client=_state["mongo"].client,
                extra_handlers={
                    "patient_discharged": _on_discharge,
                },
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("deterioration_bus_subscribe_failed: %s", exc)
    yield


app = create_app(
    title="Predictive Deterioration Monitor",
    version="2.0.0",
    description=(
        "Adult NEWS2 + Paediatric PEWS + Maternal IMEWS scoring with trend analysis, "
        "score-aware debouncing, durable persistence, and SBAR-driven escalation "
        "acknowledgement. Aligned with HSE iNEWS + NCEC NCG #1 (PEWS) + NCEC NCG #4 (IMEWS)."
    ),
    privacy_notice=PRIVACY,
)
app.router.lifespan_context = lifespan


@app.get("/kafka-events", response_model=BaseResponse, tags=["system"])
async def list_kafka_events(limit: int = 100) -> BaseResponse:
    """Return recent cross-service events consumed via Kafka/broker."""
    from shared.integration.kafka_consumer import get_kafka_events
    return BaseResponse(data=get_kafka_events("deterioration", limit))


# ---------------------------------------------------------------------------
# Routing helper — choose the right score for the patient
# ---------------------------------------------------------------------------
def _pick_scoring_system(payload: Dict[str, Any]) -> str:
    """Return 'pews' | 'imews' | 'news2' based on patient profile."""
    # Paediatric precedence
    age_years = payload.get("age")
    age_months = payload.get("age_months")
    if age_months is None and age_years is not None:
        try:
            age_months = float(age_years) * 12.0
        except (TypeError, ValueError):
            age_months = None
    if age_months is not None and age_months < 16 * 12:
        return "pews"

    # Obstetric
    if payload.get("is_pregnant") or payload.get("gestation_weeks") is not None:
        return "imews"
    pp = payload.get("post_partum_days")
    if pp is not None:
        try:
            if float(pp) <= 42:
                return "imews"
        except (TypeError, ValueError):
            pass

    return "news2"


# ---------------------------------------------------------------------------
# NEWS2 (adult)
# ---------------------------------------------------------------------------
@app.post("/deterioration/screen", response_model=BaseResponse, tags=["news2"])
async def screen_news2(payload: dict) -> BaseResponse:
    """Adult NEWS2. Auto-routes paediatric/obstetric callers to the right score."""
    # Auto-route if caller passed age<16 or pregnancy flag
    system = _pick_scoring_system(payload)
    if system == "pews":
        return await screen_pews(payload)
    if system == "imews":
        return await screen_imews(payload)

    vitals = payload.get("vitals", {}) or {}
    result = compute_news2(
        respiratory_rate=vitals.get("respiratory_rate"),
        spo2=vitals.get("spo2"),
        on_supplemental_o2=bool(vitals.get("on_supplemental_o2", False)),
        scale2_hypercapnic_target=bool(vitals.get("scale2_hypercapnic_target", False)),
        temperature_c=vitals.get("temperature") or vitals.get("temperature_c"),
        systolic_bp=vitals.get("systolic_bp") or vitals.get("sbp"),
        heart_rate=vitals.get("heart_rate") or vitals.get("hr"),
        consciousness=vitals.get("consciousness"),
    ).to_dict()

    hadm_id = str(payload.get("hadm_id", "unknown"))
    observed_at = _now()
    snapshot = {
        "hadm_id": hadm_id,
        "subject_id": payload.get("subject_id"),
        "department": payload.get("department"),
        "scoring_system": "news2",
        "score": result,
        "observed_at": observed_at,
    }

    # Trend analysis over trailing 4 h
    history = _state["history"][hadm_id] + [{**snapshot, "timestamp": observed_at, "total": result["total"]}]
    trend = compute_news2_trend(history, window_minutes=240).to_dict()
    snapshot["trend"] = trend

    _state["active_alerts"][hadm_id] = snapshot
    _state["history"][hadm_id].append(snapshot)
    if len(_state["history"][hadm_id]) > 500:
        _state["history"][hadm_id] = _state["history"][hadm_id][-250:]
    _persist_snapshot(snapshot)

    # Escalate when the score merits it AND the debouncer allows
    should_escalate = (
        result["total"] >= 5
        or result["any_param_eq_3"]
        or trend.get("is_clinically_rising", False)
    )
    if should_escalate and await _state["debouncer"].should_fire(hadm_id, score=result["total"]):
        await _escalate_internal(hadm_id, snapshot)

    return BaseResponse(data=snapshot)


# ---------------------------------------------------------------------------
# PEWS (paediatric)
# ---------------------------------------------------------------------------
@app.post("/deterioration/pews", response_model=BaseResponse, tags=["pews"])
async def screen_pews(payload: dict) -> BaseResponse:
    """Compute PEWS (NCEC NCG #1) for paediatric patients.

    Expected fields::
      hadm_id, subject_id, age (years) OR age_months,
      vitals: {
        heart_rate, respiratory_rate, spo2, on_supplemental_o2,
        systolic_bp, temperature, behaviour, respiratory_effort,
        capillary_refill_s
      }, department
    """
    vitals = payload.get("vitals", {}) or {}

    age_months = payload.get("age_months")
    if age_months is None and payload.get("age") is not None:
        age_months = float(payload["age"]) * 12.0
    if age_months is None:
        raise HTTPException(status_code=400, detail="age or age_months required for PEWS")

    result = compute_pews(
        age_months=float(age_months),
        heart_rate=vitals.get("heart_rate") or vitals.get("hr"),
        respiratory_rate=vitals.get("respiratory_rate"),
        spo2=vitals.get("spo2"),
        on_supplemental_o2=bool(vitals.get("on_supplemental_o2", False)),
        systolic_bp=vitals.get("systolic_bp") or vitals.get("sbp"),
        temperature_c=vitals.get("temperature") or vitals.get("temperature_c"),
        behaviour=vitals.get("behaviour") or vitals.get("consciousness"),
        respiratory_effort=vitals.get("respiratory_effort"),
        capillary_refill_s=vitals.get("capillary_refill_s"),
    ).to_dict()

    hadm_id = str(payload.get("hadm_id", "unknown"))
    snapshot = {
        "hadm_id": hadm_id,
        "subject_id": payload.get("subject_id"),
        "department": payload.get("department"),
        "scoring_system": "pews",
        "score": result,
        "age_months": age_months,
        "observed_at": _now(),
    }
    _state["active_alerts"][hadm_id] = snapshot
    _state["history"][hadm_id].append(snapshot)
    if len(_state["history"][hadm_id]) > 500:
        _state["history"][hadm_id] = _state["history"][hadm_id][-250:]
    _persist_snapshot(snapshot)

    if result["total"] >= 3 or result["any_param_eq_3"]:
        if await _state["debouncer"].should_fire(hadm_id, score=result["total"]):
            await _escalate_internal(hadm_id, snapshot)

    return BaseResponse(data=snapshot)


# ---------------------------------------------------------------------------
# IMEWS (obstetric)
# ---------------------------------------------------------------------------
@app.post("/deterioration/imews", response_model=BaseResponse, tags=["imews"])
async def screen_imews(payload: dict) -> BaseResponse:
    """Compute IMEWS (NCEC NCG #4) for pregnant or post-partum patients.

    Expected fields::
      hadm_id, subject_id, gestation_weeks OR post_partum_days,
      vitals: {
        respiratory_rate, spo2, temperature, systolic_bp, diastolic_bp,
        heart_rate, consciousness, proteinuria, liquor, lochia
      }, department
    """
    vitals = payload.get("vitals", {}) or {}
    result = compute_imews(
        respiratory_rate=vitals.get("respiratory_rate"),
        spo2=vitals.get("spo2"),
        temperature_c=vitals.get("temperature") or vitals.get("temperature_c"),
        systolic_bp=vitals.get("systolic_bp") or vitals.get("sbp"),
        diastolic_bp=vitals.get("diastolic_bp") or vitals.get("dbp"),
        heart_rate=vitals.get("heart_rate") or vitals.get("hr"),
        consciousness=vitals.get("consciousness"),
        proteinuria=vitals.get("proteinuria"),
        liquor=vitals.get("liquor"),
        lochia=vitals.get("lochia"),
        gestation_weeks=payload.get("gestation_weeks"),
        post_partum_days=payload.get("post_partum_days"),
    ).to_dict()

    hadm_id = str(payload.get("hadm_id", "unknown"))
    snapshot = {
        "hadm_id": hadm_id,
        "subject_id": payload.get("subject_id"),
        "department": payload.get("department"),
        "scoring_system": "imews",
        "score": result,
        "gestation_weeks": payload.get("gestation_weeks"),
        "post_partum_days": payload.get("post_partum_days"),
        "observed_at": _now(),
    }
    _state["active_alerts"][hadm_id] = snapshot
    _state["history"][hadm_id].append(snapshot)
    if len(_state["history"][hadm_id]) > 500:
        _state["history"][hadm_id] = _state["history"][hadm_id][-250:]
    _persist_snapshot(snapshot)

    if result["any_pink"] or result["yellow_triggers"] >= 2:
        if await _state["debouncer"].should_fire(hadm_id, score=result["total"]):
            await _escalate_internal(hadm_id, snapshot)

    return BaseResponse(data=snapshot)


# ---------------------------------------------------------------------------
# Escalation (internal helper + public endpoint)
# ---------------------------------------------------------------------------
async def _escalate_internal(hadm_id: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Trigger escalation with cross-service notifications."""
    client: ServiceClient = _state.get("client") or ServiceClient()
    score = snapshot.get("score") or {}
    system = snapshot.get("scoring_system", "news2")
    total = score.get("total", 0)
    escalation_id = str(uuid.uuid4())

    actions: List[str] = []
    try:
        await client.clinical_chat.post("/context-inject", {
            "department": snapshot.get("department", "ALL"),
            "action_taken": f"{system}_alert",
            "reason": f"{system.upper()}={total} — {score.get('recommended_response')}",
            "hadm_id": hadm_id,
            "timestamp": _now(),
        })
        actions.append("clinical_chat_context_injected")
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat_notify_failed: %s", exc)

    # ICU escalation only for adult NEWS2 ≥ 7 or any IMEWS pink
    should_bump_bed = (
        (system == "news2" and total >= 7)
        or (system == "imews" and score.get("pink_triggers", 0) >= 2)
        or (system == "pews" and total >= 5)
    )
    if should_bump_bed:
        try:
            await client.bed_management.post("/escalate-bed-priority", {
                "hadm_id": hadm_id,
                "reason": f"{system}_high",
                "bump": 0.6,
            })
            actions.append("bed_priority_escalated")
        except Exception as exc:  # noqa: BLE001
            logger.warning("bed_mgmt_notify_failed: %s", exc)

    try:
        urgency = "red" if total >= 7 or score.get("any_pink") else "amber"
        await client.hospital_ops.post("/notify-capacity-alert", {
            "department": snapshot.get("department", "ALL"),
            "urgency": urgency,
            "reason": f"{system}_deterioration",
        })
        actions.append("hospital_ops_alerted")
    except Exception as exc:  # noqa: BLE001
        logger.warning("ops_notify_failed: %s", exc)

    record = {
        "escalation_id": escalation_id,
        "hadm_id": hadm_id,
        "scoring_system": system,
        "score": score,
        "actions": actions,
        "escalated_at": _now(),
        "acknowledged": False,
        "acknowledged_at": None,
        "acknowledged_by": None,
        "sbar": None,
        "time_to_ack_seconds": None,
        "auto_ack": False,
    }
    _state["escalations"][escalation_id] = record
    _persist_escalation(record)

    # Publish to the bus so subscribers (XAI audit, Hospital Ops, FHIR,
    # GDPR, alert center) actually see deterioration events. Previously
    # the topic was silent — five consumer groups were attached but the
    # producer was never wired. (Closes audit finding #2.)
    try:
        bus = get_event_bus()
        await bus.publish("deterioration_alert", {
            "escalation_id": escalation_id,
            "hadm_id": hadm_id,
            "subject_id": snapshot.get("subject_id"),
            "department": snapshot.get("department"),
            "scoring_system": system,
            "score": {"total": total, "risk_band": score.get("risk_band")},
            "trend": snapshot.get("trend"),
            "observed_at": snapshot.get("observed_at"),
            "actions": actions,
        }, source_module="deterioration")
        # Critical band gets its own topic for high-priority subscribers.
        if (system == "news2" and total >= 7) or (system == "imews" and score.get("pink_triggers", 0) >= 2):
            await bus.publish("deterioration_critical", {
                "escalation_id": escalation_id,
                "hadm_id": hadm_id,
                "subject_id": snapshot.get("subject_id"),
                "score": total,
                "scoring_system": system,
                "department": snapshot.get("department"),
            }, source_module="deterioration")
    except Exception as exc:  # noqa: BLE001
        logger.warning("deterioration_publish_failed: %s", exc)

    # Sim-only auto-ack — delayed background task so the audit trail still
    # shows a realistic non-zero time_to_ack_seconds. Never runs in prod.
    if _governance_config["auto_ack_in_sim"] and _is_simulation_mode():
        delay = max(1, int(_governance_config.get("auto_ack_delay_seconds", 120)))
        asyncio.create_task(_auto_ack_after_delay(escalation_id, delay))
        record["auto_ack_scheduled_in_s"] = delay

    return record


async def _auto_ack_after_delay(escalation_id: str, delay_seconds: int) -> None:
    """Background task — sleep, then synthesize a sim-mode clinician ack."""
    try:
        await asyncio.sleep(delay_seconds)
    except asyncio.CancelledError:
        return
    record = _state["escalations"].get(escalation_id)
    if record is None or record.get("acknowledged"):
        return  # already acked (manual ack beat us to it) or record dropped
    snapshot = _state["active_alerts"].get(record.get("hadm_id")) or {}
    score = record.get("score") or {}
    system = record.get("scoring_system", "news2")
    sbar = {
        "situation": (
            f"Automated review: {system.upper()} score {score.get('total')} for "
            f"hadm_id={record.get('hadm_id')} in dept "
            f"{snapshot.get('department', 'ALL')}"
        ),
        "background": "Sim-mode auto-acknowledgement — no clinician in the loop.",
        "assessment": score.get("recommended_response", "routine review"),
        "recommendation": "Continue monitoring per protocol; flagged for human review on demand.",
    }
    ack_time = _now()
    try:
        esc_time = datetime.fromisoformat(record["escalated_at"].replace("Z", "+00:00"))
        ack_dt = datetime.fromisoformat(ack_time.replace("Z", "+00:00"))
        time_to_ack = (ack_dt - esc_time).total_seconds()
    except (ValueError, TypeError):
        time_to_ack = float(delay_seconds)

    record["acknowledged"] = True
    record["acknowledged_at"] = ack_time
    record["acknowledged_by"] = {"name": "sim_autoack", "role": "sim_autoack"}
    record["sbar"] = sbar
    record["outcome"] = "auto_acknowledged_sim"
    record["time_to_ack_seconds"] = time_to_ack
    record["auto_ack"] = True

    ack_entry = {
        "escalation_id": escalation_id,
        "hadm_id": record["hadm_id"],
        "acknowledged_at": ack_time,
        "clinician": record["acknowledged_by"],
        "sbar": sbar,
        "outcome": record["outcome"],
        "time_to_ack_seconds": time_to_ack,
        "auto_ack": True,
    }
    _state["acknowledgements"].append(ack_entry)
    if len(_state["acknowledgements"]) > 5000:
        _state["acknowledgements"] = _state["acknowledgements"][-2500:]
    _persist_ack(ack_entry)
    _persist_escalation(record)
    logger.info(
        "auto_ack_fired escalation_id=%s hadm_id=%s delay_s=%d",
        escalation_id, record.get("hadm_id"), int(delay_seconds),
    )


@app.post("/deterioration/escalate", response_model=BaseResponse, tags=["escalation"])
async def escalate(payload: dict) -> BaseResponse:
    """Manual escalation — caller provides hadm_id and optional snapshot override."""
    hadm_id = str(payload.get("hadm_id", "unknown"))
    snapshot = payload.get("snapshot") or _state["active_alerts"].get(hadm_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"no active snapshot for {hadm_id}")
    record = await _escalate_internal(hadm_id, snapshot)
    return BaseResponse(data=record)


@app.post("/deterioration/acknowledge", response_model=BaseResponse, tags=["escalation"])
async def acknowledge(payload: dict) -> BaseResponse:
    """Clinician acknowledgement of an escalation with SBAR capture.

    Expected payload::
      escalation_id,
      clinician: {"name": ..., "role": "NCHD|Registrar|Consultant"},
      sbar: {"situation": ..., "background": ..., "assessment": ..., "recommendation": ...},
      outcome: "reviewed|escalated_further|cco_called|transferred_icu"
    """
    escalation_id = payload.get("escalation_id")
    if not escalation_id:
        raise HTTPException(status_code=400, detail="escalation_id required")
    record = _state["escalations"].get(escalation_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"escalation {escalation_id} not found")

    ack_time = _now()
    try:
        esc_time = datetime.fromisoformat(record["escalated_at"].replace("Z", "+00:00"))
        ack_dt = datetime.fromisoformat(ack_time.replace("Z", "+00:00"))
        time_to_ack = (ack_dt - esc_time).total_seconds()
    except (ValueError, TypeError):
        time_to_ack = None

    record["acknowledged"] = True
    record["acknowledged_at"] = ack_time
    record["acknowledged_by"] = payload.get("clinician") or {}
    record["sbar"] = payload.get("sbar") or {}
    record["outcome"] = payload.get("outcome")
    record["time_to_ack_seconds"] = time_to_ack

    ack_entry = {
        "escalation_id": escalation_id,
        "hadm_id": record["hadm_id"],
        "acknowledged_at": ack_time,
        "clinician": record["acknowledged_by"],
        "sbar": record["sbar"],
        "outcome": record["outcome"],
        "time_to_ack_seconds": time_to_ack,
    }
    _state["acknowledgements"].append(ack_entry)
    if len(_state["acknowledgements"]) > 5000:
        _state["acknowledgements"] = _state["acknowledgements"][-2500:]
    _persist_ack(ack_entry)
    _persist_escalation(record)  # re-persist the updated record

    return BaseResponse(data=record)


# ---------------------------------------------------------------------------
# Governance — sim-mode auto-ack toggle
# ---------------------------------------------------------------------------
@app.get("/deterioration/governance/config", response_model=BaseResponse, tags=["governance"])
async def get_governance_config() -> BaseResponse:
    """Return current auto-ack configuration.

    ``effective`` is the actual runtime flag — always False outside sim.
    """
    cfg = {**_governance_config}
    cfg["deployment_mode"] = os.environ.get("DEPLOYMENT_MODE", "simulation")
    cfg["effective"] = bool(cfg["auto_ack_in_sim"] and _is_simulation_mode())
    return BaseResponse(data=cfg)


@app.post("/deterioration/governance/config", response_model=BaseResponse, tags=["governance"])
async def update_governance_config(payload: dict) -> BaseResponse:
    """Toggle sim-mode auto-ack. In production the flag is accepted but
    inert — ``_is_simulation_mode()`` gates the actual effect."""
    if "auto_ack_in_sim" in payload:
        _governance_config["auto_ack_in_sim"] = bool(payload["auto_ack_in_sim"])
    if "auto_ack_delay_seconds" in payload:
        try:
            delay = int(payload["auto_ack_delay_seconds"])
            if 1 <= delay <= 3600:
                _governance_config["auto_ack_delay_seconds"] = delay
        except (TypeError, ValueError):
            pass
    _governance_config["last_updated_at"] = _now()
    _governance_config["last_updated_by"] = payload.get("updated_by", "api")
    if not _is_simulation_mode() and _governance_config["auto_ack_in_sim"]:
        logger.warning(
            "auto_ack_in_sim=True accepted but NOT effective — "
            "DEPLOYMENT_MODE=%s is not simulation",
            os.environ.get("DEPLOYMENT_MODE"),
        )
    cfg = {**_governance_config}
    cfg["deployment_mode"] = os.environ.get("DEPLOYMENT_MODE", "simulation")
    cfg["effective"] = bool(cfg["auto_ack_in_sim"] and _is_simulation_mode())
    return BaseResponse(data=cfg)


# ---------------------------------------------------------------------------
# Query endpoints
# ---------------------------------------------------------------------------
@app.get("/deterioration/active-alerts", response_model=BaseResponse, tags=["query"])
async def active_alerts() -> BaseResponse:
    alerts = []
    for snap in _state["active_alerts"].values():
        score = snap.get("score", {})
        total = score.get("total", 0)
        system = snap.get("scoring_system", "news2")
        threshold = 5 if system == "news2" else (3 if system == "pews" else 1)
        if total >= threshold or score.get("any_param_eq_3") or score.get("any_pink"):
            alerts.append(snap)
    return BaseResponse(data=alerts)


@app.get("/deterioration/history/{hadm_id}", response_model=BaseResponse, tags=["query"])
async def history(hadm_id: str, limit: int = Query(200, ge=1, le=1000)) -> BaseResponse:
    entries = _state["history"].get(hadm_id, [])[-limit:]
    return BaseResponse(data=entries)


@app.get("/deterioration/trend/{hadm_id}", response_model=BaseResponse, tags=["query"])
async def trend(hadm_id: str, window_minutes: int = Query(240, ge=30, le=1440)) -> BaseResponse:
    """Compute the NEWS2 slope over a trailing window for this patient."""
    entries = _state["history"].get(hadm_id, [])
    history_points = [
        {"timestamp": e.get("observed_at"), "total": (e.get("score") or {}).get("total", 0)}
        for e in entries if e.get("scoring_system") == "news2"
    ]
    result = compute_news2_trend(history_points, window_minutes=window_minutes).to_dict()
    return BaseResponse(data=result)


@app.get("/deterioration/escalations", response_model=BaseResponse, tags=["escalation"])
async def list_escalations(
    limit: int = Query(200, ge=1, le=2000),
    unacknowledged: bool = False,
) -> BaseResponse:
    records = list(_state["escalations"].values())
    if unacknowledged:
        records = [r for r in records if not r.get("acknowledged")]
    records = sorted(records, key=lambda r: r.get("escalated_at", ""), reverse=True)[:limit]
    return BaseResponse(data=records)


@app.get("/deterioration/audit", response_model=BaseResponse, tags=["escalation"])
async def audit(limit: int = Query(200, ge=1, le=2000)) -> BaseResponse:
    # Legacy endpoint retained for dashboard compatibility
    return await list_escalations(limit=limit, unacknowledged=False)


@app.get("/deterioration/stats", response_model=BaseResponse, tags=["query"])
async def stats() -> BaseResponse:
    by_dept: Dict[str, int] = defaultdict(int)
    by_system: Dict[str, int] = defaultdict(int)
    totals: List[int] = []
    for snap in _state["active_alerts"].values():
        dept = snap.get("department") or "UNK"
        by_dept[dept] += 1
        by_system[snap.get("scoring_system", "news2")] += 1
        totals.append((snap.get("score") or {}).get("total", 0))
    mean_score = round(sum(totals) / len(totals), 2) if totals else 0
    unacked = sum(1 for r in _state["escalations"].values() if not r.get("acknowledged"))
    ack_times = [
        r["time_to_ack_seconds"] for r in _state["escalations"].values()
        if r.get("time_to_ack_seconds") is not None
    ]
    mean_ttack = round(sum(ack_times) / len(ack_times), 1) if ack_times else 0.0
    return BaseResponse(data={
        "active_patients": len(_state["active_alerts"]),
        "high_band": sum(1 for t in totals if t >= 7),
        "medium_band": sum(1 for t in totals if 5 <= t < 7),
        "mean_score": mean_score,
        "by_department": dict(by_dept),
        "by_scoring_system": dict(by_system),
        "unacknowledged_escalations": unacked,
        "mean_time_to_ack_seconds": mean_ttack,
    })


@app.post("/reset", response_model=BaseResponse, tags=["system"])
async def reset_deterioration() -> BaseResponse:
    _state["active_alerts"].clear()
    _state["history"].clear()
    _state["escalations"].clear()
    _state["acknowledgements"].clear()
    if _state["debouncer"] is not None:
        _state["debouncer"].reset()
    db = _mongo_db()
    if db is not None:
        try:
            db[COLL_ACTIVE].delete_many({})
            db[COLL_HISTORY].delete_many({})
            db[COLL_ESC].delete_many({})
            db[COLL_ACK].delete_many({})
        except Exception as exc:  # noqa: BLE001
            logger.warning("deterioration_reset_purge_failed: %s", exc)
    return BaseResponse(data={"reset": True})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8220)
