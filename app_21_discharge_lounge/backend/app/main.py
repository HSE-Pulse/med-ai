"""Discharge Lounge Coordinator — port 8221.

Logistics-only module (no clinical fields): moves ``predict_discharge``-ready
patients from ward beds to the Discharge Lounge to free inpatient capacity
earlier. Also hosts the Sláintecare community-referral queue per Item 6.1.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fastapi import FastAPI, Query

from shared.api.base import BaseResponse, PrivacyNotice, create_app
from shared.constants.hospital import CAPACITIES, LOS_PARAMS
from shared.db.mongo import MongoManager
from shared.integration.event_bus import get_event_bus
from shared.integration.persistent_state import PersistentState
from shared.integration.service_client import ServiceClient
from shared.integration.sim_clock import get_sim_time

logger = logging.getLogger("discharge_lounge")
logger.setLevel(logging.INFO)

# Auto-transfer fires when the bed_management discharge-readiness score for a
# patient reaches this threshold and the lounge has capacity. Tunable via env
# because per-site tolerance for "sounds ready to move" varies.
AUTO_TRANSFER_THRESHOLD = float(os.getenv("DISCHARGE_LOUNGE_AUTO_THRESHOLD", "0.95"))

# Auto-expiry: occupants whose arrived_at + expected_departure_h is past the
# current sim clock are auto-completed. The clinician can still press the
# manual Complete button as an override before expiry fires.
EXPIRY_POLL_SECONDS = float(os.getenv("DISCHARGE_LOUNGE_EXPIRY_POLL_SECONDS", "5"))


PRIVACY = PrivacyNotice(
    data_collected=["discharge_transfers", "community_referrals"],
    legal_basis="GDPR Art. 6(1)(e) (public task, hospital operations)",
    retention_period="12 months",
)


CAPACITY = CAPACITIES.get("Discharge_Lounge", 10)


_state: Dict[str, Any] = {
    "occupants": {},                 # hadm_id → {arrived_at, source_dept}
    "community_referrals": deque(maxlen=1000),
    "metrics_history": deque(maxlen=5000),
    "los_samples": deque(maxlen=200),
    # Each entry: {"ts": iso8601, "los_h": float}. Used to compute true throughput per
    # rolling time window (the bare len(los_samples) saturates at maxlen and is a
    # buffer count, not a throughput rate).
    "discharge_events": deque(maxlen=2000),
    # hadm_ids that have already been completed (popped + patient_discharged
    # published). Without this memory, every subsequent ``discharge_predicted``
    # event for the same patient — and the orchestrator fires one per vital
    # via process_vital — would re-trigger _perform_transfer → auto-expiry →
    # re-publish patient_discharged, creating a feedback loop that emitted
    # 100+ duplicate discharges per real admission.
    "completed_hadms": set(),
    # FIFO bound — keep the last N completed hadm_ids so the set doesn't
    # grow without bound during long-running simulations.
    "completed_order": deque(maxlen=10000),
}


@asynccontextmanager
async def lifespan(application: FastAPI):
    # Observability
    try:
        from shared.integration.logging_config import setup_logging
        setup_logging(service_name="discharge_lounge")
    except Exception as exc:  # noqa: BLE001
        logger.warning("logging_setup_failed: %s", exc)
    try:
        from shared.integration.tracing import setup_tracing
        setup_tracing(application, service_name="discharge_lounge")
    except Exception as exc:  # noqa: BLE001
        logger.warning("tracing_setup_failed: %s", exc)
    try:
        from shared.integration.prometheus_metrics import install_metrics
        install_metrics(application, service_name="discharge_lounge")
    except Exception as exc:  # noqa: BLE001
        logger.warning("prometheus_metrics_install_failed: %s", exc)

    # Anchor SimClock to data_ingestion's authoritative clock.
    try:
        from shared.integration.sim_clock import attach_remote_clock
        await attach_remote_clock()
    except Exception as exc:  # noqa: BLE001
        logger.warning("sim_clock_attach_remote_failed: %s", exc)

    _state["client"] = ServiceClient()

    # Persistent state: discharge lounge occupants + community referral queue
    try:
        mongo = MongoManager()
        _state["mongo"] = mongo
        persistent = PersistentState(
            service_id="discharge_lounge",
            mongo=mongo.client,
            collection_name="discharge_lounge_state",
        )
        _state["persistent"] = persistent

        # Wire event bus broker so in-process handlers receive durable
        # events published before we started
        bus = get_event_bus()
        bus.attach_mongo(mongo.client)
        await bus.startup()
        _state["event_bus"] = bus

        snap = persistent.load_snapshot()
        if snap and snap.get("state"):
            restored = snap["state"]
            for hadm_id, record in (restored.get("occupants") or {}).items():
                _state["occupants"][hadm_id] = record
            for ref in (restored.get("community_referrals") or [])[-1000:]:
                _state["community_referrals"].append(ref)
            for s in (restored.get("los_samples") or [])[-200:]:
                _state["los_samples"].append(s)
            for ev in (restored.get("discharge_events") or [])[-2000:]:
                _state["discharge_events"].append(ev)
            logger.info(
                "Discharge Lounge restored: %d occupants, %d referrals from snapshot v%d",
                len(_state["occupants"]),
                len(_state["community_referrals"]),
                snap.get("version", 0),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("discharge_lounge_persistent_init_failed: %s", exc)
        _state["persistent"] = None
        _state["mongo"] = None

    # Subscribe to Kafka topics that drive lounge lifecycle:
    #   - patient_discharged    → someone confirmed the patient left; free slot
    #   - discharge_predicted   → high-readiness signal; auto-transfer ward→lounge
    try:
        if _state.get("mongo"):
            from shared.integration.kafka_consumer import attach_with_ring_buffer

            async def _on_discharge(_topic, payload):
                hadm_id = str(payload.get("hadm_id") or "")
                if not hadm_id:
                    return
                # Mark as completed so a later/replayed discharge_predicted
                # event for the same hadm doesn't re-admit the patient
                # back into the lounge.
                completed = _state.setdefault("completed_hadms", set())
                order = _state.setdefault("completed_order", deque(maxlen=10000))
                if hadm_id not in completed:
                    completed.add(hadm_id)
                    order.append(hadm_id)
                    while len(completed) > order.maxlen:
                        completed.discard(order[0] if order else None)
                if hadm_id in _state.get("occupants", {}):
                    _state["occupants"].pop(hadm_id, None)
                    logger.info(
                        "lounge slot freed for hadm=%s via Kafka patient_discharged",
                        hadm_id,
                    )

            async def _on_discharge_predicted(_topic, payload):
                try:
                    readiness = float(payload.get("readiness_score") or 0)
                except (TypeError, ValueError):
                    readiness = 0.0
                if readiness < AUTO_TRANSFER_THRESHOLD:
                    return
                hadm_id = str(payload.get("hadm_id") or "")
                if not hadm_id:
                    return
                if hadm_id in _state["occupants"]:
                    return  # already in the lounge
                # Don't re-admit a patient who has already completed their
                # lounge stay. Without this gate, every vital event the
                # orchestrator fires re-publishes ``discharge_predicted``,
                # which would otherwise re-trigger transfer → auto-expire →
                # discharge in an unbounded loop (we measured 159 duplicate
                # patient_discharged events per admission before this fix).
                if hadm_id in _state.get("completed_hadms", set()):
                    return
                if len(_state["occupants"]) >= CAPACITY:
                    logger.info(
                        "auto-transfer deferred: lounge_full hadm=%s readiness=%.2f",
                        hadm_id, readiness,
                    )
                    return
                try:
                    await _perform_transfer(
                        hadm_id=hadm_id,
                        subject_id=payload.get("subject_id"),
                        source_department=payload.get("source_department") or payload.get("department"),
                        expected_departure_h=payload.get("expected_departure_h"),
                        initiated_by=f"auto:discharge_predicted(readiness={readiness:.2f})",
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "auto-transfer failed hadm=%s: %s", hadm_id, exc,
                    )

            async def _on_transfer(_topic, payload):
                """Mirror sim's plain transfers when the destination is the
                Discharge Lounge. Without this, the only path into the
                lounge was the high-readiness ``discharge_predicted``
                auto-transfer rule — but the sim moves patients to
                ``Discharge_Lounge`` (or its MIMIC equivalents) via
                ``patient_transferred``, so bed_management would show 3
                in lounge while this service still reported 0.
                """
                hadm_id = str(payload.get("hadm_id") or "")
                if not hadm_id:
                    return
                dest_raw = (
                    payload.get("to_department")
                    or payload.get("careunit")
                    or payload.get("department")
                    or ""
                )
                d = str(dest_raw).lower()
                # Match Irish dept name + MIMIC variants
                is_lounge = (
                    "discharge lounge" in d
                    or "discharge_lounge" in d
                    or d == "lounge"
                )
                if not is_lounge:
                    return
                if hadm_id in _state.get("completed_hadms", set()):
                    return
                if hadm_id in _state["occupants"]:
                    return
                if len(_state["occupants"]) >= CAPACITY:
                    logger.info(
                        "lounge transfer deferred: full hadm=%s", hadm_id,
                    )
                    return
                try:
                    await _perform_transfer(
                        hadm_id=hadm_id,
                        subject_id=payload.get("subject_id"),
                        source_department=payload.get("from_department")
                            or payload.get("source_department")
                            or payload.get("department"),
                        expected_departure_h=None,
                        initiated_by="auto:patient_transferred",
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "lounge transfer failed hadm=%s: %s", hadm_id, exc,
                    )

            await attach_with_ring_buffer(
                service_id="discharge_lounge",
                topics=["patient_discharged", "discharge_predicted", "patient_transferred"],
                mongo_client=_state["mongo"].client,
                extra_handlers={
                    "patient_discharged": _on_discharge,
                    "discharge_predicted": _on_discharge_predicted,
                    "patient_transferred": _on_transfer,
                },
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("dlounge_bus_subscribe_failed: %s", exc)

    # Broker-independent event_log tailer. Kafka is optional in dev/CI; this
    # guarantees cross-process discharge_predicted events still reach the
    # auto-transfer handler by polling MongoDB.MIMIC_SIM.event_log.
    try:
        if _state.get("mongo") is not None:
            _state["tail_stop"] = asyncio.Event()
            _state["tail_task"] = asyncio.create_task(
                _tail_event_log(_state["mongo"].client, _state["tail_stop"])
            )
            logger.info("event_log tailer started")
    except Exception as exc:  # noqa: BLE001
        logger.warning("event_log tailer failed to start: %s", exc)

    # Auto-expiry watcher (independent of Mongo — works purely against
    # in-memory occupants, so it runs even when MongoDB is unreachable).
    _state["expiry_stop"] = asyncio.Event()
    _state["expiry_task"] = asyncio.create_task(
        _expire_occupants(_state["expiry_stop"])
    )

    # Bed-management hydration loop. Source-of-truth for who is *actually*
    # occupying a Discharge_Lounge bed lives in bed_management's bed
    # registry — bed_mgmt's sim_reconciler allocates lounge beds based on
    # discharge-readiness signals it sees, but those allocations never
    # publish a ``patient_transferred`` Kafka event, so this service
    # would otherwise stay at 0 occupants while bed_mgmt shows several.
    # Poll /beds every 30 s and seed any lounge-occupants we haven't
    # already tracked, then release ones bed_mgmt no longer has there.
    _state["bedmgmt_stop"] = asyncio.Event()
    _state["bedmgmt_task"] = asyncio.create_task(
        _hydrate_from_bed_management(_state["bedmgmt_stop"])
    )

    # Also subscribe in-process so same-interpreter publishes work even with
    # no Kafka broker configured. Safe to double-wire — _perform_transfer
    # is idempotent on hadm_id.
    try:
        bus = _state.get("event_bus")
        if bus is not None:
            async def _in_proc_handler(event):
                payload = event.payload if hasattr(event, "payload") else {}
                try:
                    readiness = float(payload.get("readiness_score") or 0)
                except (TypeError, ValueError):
                    return
                if readiness < AUTO_TRANSFER_THRESHOLD:
                    return
                hadm_id = str(payload.get("hadm_id") or "")
                if not hadm_id or hadm_id in _state["occupants"]:
                    return
                if len(_state["occupants"]) >= CAPACITY:
                    return
                try:
                    await _perform_transfer(
                        hadm_id=hadm_id,
                        subject_id=payload.get("subject_id"),
                        source_department=payload.get("source_department") or payload.get("department"),
                        expected_departure_h=payload.get("expected_departure_h"),
                        initiated_by=f"auto:inproc(readiness={readiness:.2f})",
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("in-proc auto-transfer failed: %s", exc)
            bus.subscribe("discharge_predicted", _in_proc_handler)
    except Exception as exc:  # noqa: BLE001
        logger.debug("in_proc_subscribe_failed: %s", exc)

    yield

    try:
        for stop_key, task_key in (("tail_stop", "tail_task"),
                                    ("expiry_stop", "expiry_task")):
            stop = _state.get(stop_key)
            if stop is not None:
                stop.set()
            t = _state.get(task_key)
            if t is not None:
                try:
                    await asyncio.wait_for(t, timeout=3.0)
                except asyncio.TimeoutError:
                    t.cancel()
    except Exception:
        pass

    try:
        persistent = _state.get("persistent")
        if persistent is not None:
            persistent.save_snapshot({
                "occupants": dict(_state["occupants"]),
                "community_referrals": list(_state["community_referrals"]),
                "los_samples": list(_state["los_samples"]),
                "discharge_events": list(_state["discharge_events"]),
            })
    except Exception:
        pass
    if _state.get("mongo"):
        try:
            _state["mongo"].close()
        except Exception:
            pass


app = create_app(
    title="Discharge Lounge Coordinator",
    version="1.0.0",
    description="Automates ward→lounge transfers to free inpatient beds earlier.",
    privacy_notice=PRIVACY,
)
app.router.lifespan_context = lifespan


@app.get("/kafka-events", response_model=BaseResponse, tags=["system"])
async def list_kafka_events(limit: int = 100) -> BaseResponse:
    """Return recent cross-service events consumed via Kafka/broker."""
    from shared.integration.kafka_consumer import get_kafka_events
    return BaseResponse(data=get_kafka_events("discharge_lounge", limit))


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
@app.get("/discharge-lounge/status", response_model=BaseResponse, tags=["lounge"])
async def status() -> BaseResponse:
    occupants = list(_state["occupants"].values())
    return BaseResponse(data={
        "capacity": CAPACITY,
        "occupied": len(occupants),
        "available": max(0, CAPACITY - len(occupants)),
        "patients": occupants,
        "observed_at": get_sim_time().isoformat(),
    })


async def _perform_transfer(
    *,
    hadm_id: str,
    subject_id: Any = None,
    source_department: Optional[str] = None,
    expected_departure_h: Optional[float] = None,
    initiated_by: str = "manual",
) -> Dict[str, Any]:
    """Shared transfer path used by REST /transfer and the auto-transfer
    subscriber. Idempotent on ``hadm_id``: a duplicate call returns the
    existing occupant record unchanged and does NOT re-trigger downstream
    side-effects.

    Side-effects when the transfer is new:
      * frees the patient's ward bed and allocates a Discharge_Lounge bed
        via bed_management /notify-transfer
      * publishes ``lounge_transfer_requested`` on the event bus
    """
    hadm_id = str(hadm_id or "").strip()
    if not hadm_id:
        raise ValueError("hadm_id required")

    # Idempotent: same hadm_id → return what we have
    existing = _state["occupants"].get(hadm_id)
    if existing is not None:
        return {**existing, "duplicate": True}

    if len(_state["occupants"]) >= CAPACITY:
        raise RuntimeError("lounge_full")

    if expected_departure_h is None:
        expected_departure_h = LOS_PARAMS["Discharge_Lounge"]["median_h"]

    record = {
        "hadm_id": hadm_id,
        "subject_id": subject_id,
        "source_department": source_department,
        "arrived_at": get_sim_time().isoformat(),
        "expected_departure_h": float(expected_departure_h),
        "initiated_by": initiated_by,
    }
    _state["occupants"][hadm_id] = record
    _snapshot()

    # Free the ward bed and allocate a lounge bed via bed_management.
    # Failures are logged (not silently swallowed) so operators can see
    # when ward beds didn't actually release.
    client: ServiceClient = _state.get("client") or ServiceClient()
    try:
        result = await client.bed_management.post("/notify-transfer", {
            "hadm_id": hadm_id,
            "subject_id": subject_id,
            "to_department": "Discharge_Lounge",
            "from_department": source_department,
        })
        # ServiceClient returns a dict; raise-worthy conditions surface as
        # status=error without throwing. Treat them as real failures.
        if isinstance(result, dict) and result.get("status") == "error":
            logger.warning(
                "bed release refused hadm=%s: %s — ward bed still marked occupied",
                hadm_id, result.get("error"),
            )
        else:
            logger.info(
                "ward bed release requested hadm=%s result=%s",
                hadm_id, (result or {}).get("data"),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "bed release raised hadm=%s: %s — ward bed still marked occupied",
            hadm_id, exc,
        )

    # Notify the alert bus so dashboards and downstream modules learn
    try:
        bus = _state.get("event_bus") or get_event_bus()
        await bus.publish(
            "lounge_transfer_requested",
            {
                "hadm_id": hadm_id,
                "subject_id": subject_id,
                "source_department": source_department,
                "expected_departure_h": record["expected_departure_h"],
                "initiated_by": initiated_by,
            },
            source_module="discharge_lounge",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("lounge_transfer_requested publish failed: %s", exc)

    return record


@app.post("/discharge-lounge/transfer", response_model=BaseResponse, tags=["lounge"])
async def transfer(data: dict) -> BaseResponse:
    """Move a patient from their ward to the discharge lounge.

    Idempotent: repeating the call with the same ``hadm_id`` returns the
    existing occupant record (``duplicate=True``) without creating a new
    arrival timestamp or firing downstream side-effects.
    """
    hadm_id = str(data.get("hadm_id", ""))
    if not hadm_id:
        return BaseResponse(status="error", error="hadm_id required")
    try:
        record = await _perform_transfer(
            hadm_id=hadm_id,
            subject_id=data.get("subject_id"),
            source_department=data.get("source_department"),
            expected_departure_h=data.get("expected_departure_h"),
            initiated_by=data.get("initiated_by", "manual"),
        )
    except RuntimeError as exc:
        if str(exc) == "lounge_full":
            return BaseResponse(
                status="error", error="lounge_full",
                data={"capacity": CAPACITY, "occupied": len(_state["occupants"])},
            )
        return BaseResponse(status="error", error=str(exc))
    except ValueError as exc:
        return BaseResponse(status="error", error=str(exc))
    return BaseResponse(data=record)


async def _perform_complete(
    hadm_id: str,
    *,
    completed_via: str = "discharge_lounge",
) -> Optional[Dict[str, Any]]:
    """Shared completion path used by REST /complete and the auto-expiry
    watcher. Pops the occupant, samples LOS, notifies bed_management, and
    publishes ``bed_released`` + ``lounge_completed`` on the event bus.

    Note: the topic is intentionally ``lounge_completed`` (not
    ``patient_discharged``) so it never collides with the engine's true
    MIMIC-driven discharge stream. Lounge completion is a logistics event;
    the canonical clinical discharge is fired by data_ingestion when the
    MIMIC ``dischtime`` is reached.

    Returns the popped occupant record, or ``None`` if the hadm_id wasn't
    in the lounge (idempotent — safe to call twice from racing paths).
    """
    hadm_id = str(hadm_id)
    occupant = _state["occupants"].pop(hadm_id, None)
    if occupant is None:
        return None
    # Record this hadm as completed BEFORE publishing so any concurrent
    # ``discharge_predicted`` event that arrives while we're notifying
    # downstream services already sees it as done.
    completed = _state.setdefault("completed_hadms", set())
    order = _state.setdefault("completed_order", deque(maxlen=10000))
    if hadm_id not in completed:
        completed.add(hadm_id)
        order.append(hadm_id)
        # Maintain set size in sync with the bounded deque
        while len(completed) > order.maxlen:
            completed.discard(order[0] if order else None)

    # LOS sample for metrics
    try:
        arrived = occupant.get("arrived_at")
        arrived_dt = _parse_iso(arrived) if arrived else None
        if arrived_dt is not None:
            los_h = (get_sim_time() - arrived_dt).total_seconds() / 3600
            _state["los_samples"].append(round(los_h, 2))
            _state["discharge_events"].append({
                "ts": get_sim_time().isoformat(),
                "los_h": round(los_h, 2),
            })
    except Exception as exc:  # noqa: BLE001
        logger.debug("los sample failed hadm=%s: %s", hadm_id, exc)

    client: ServiceClient = _state.get("client") or ServiceClient()
    try:
        res = await client.bed_management.post(f"/notify-discharge/{hadm_id}", {})
        if isinstance(res, dict) and res.get("status") == "error":
            logger.warning(
                "notify-discharge refused hadm=%s: %s", hadm_id, res.get("error"),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("notify-discharge raised hadm=%s: %s", hadm_id, exc)

    bus = _state.get("event_bus") or get_event_bus()
    try:
        await bus.publish("bed_released", {
            "hadm_id": hadm_id,
            "source_department": "Discharge_Lounge",
        }, source_module="discharge_lounge")
    except Exception as exc:  # noqa: BLE001
        logger.debug("bed_released publish failed: %s", exc)
    try:
        await bus.publish("lounge_completed", {
            "hadm_id": hadm_id,
            "subject_id": occupant.get("subject_id"),
            "source_department": "Discharge_Lounge",
            "completed_via": completed_via,
        }, source_module="discharge_lounge")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "lounge_completed publish failed hadm=%s: %s", hadm_id, exc,
        )
    _snapshot()
    return occupant


@app.post("/discharge-lounge/complete", response_model=BaseResponse, tags=["lounge"])
async def complete(data: dict) -> BaseResponse:
    """Confirm the patient has left the hospital — frees the lounge bed.

    Idempotent: if the patient was already removed (e.g. by the auto-expiry
    watcher racing with a clinician click), returns ``already_discharged``
    with a 200 — no double-publish of patient_discharged.
    """
    hadm_id = str(data.get("hadm_id", ""))
    if not hadm_id:
        return BaseResponse(status="error", error="hadm_id required")
    occupant = await _perform_complete(hadm_id, completed_via="manual")
    if occupant is None:
        return BaseResponse(status="ok", data={
            "completed": False, "reason": "already_discharged", "hadm_id": hadm_id,
        })
    return BaseResponse(data={"completed": True, "hadm_id": hadm_id})


@app.get("/discharge-lounge/forecast", response_model=BaseResponse, tags=["lounge"])
async def forecast(horizon_h: int = Query(4, ge=1, le=24)) -> BaseResponse:
    """Project lounge occupancy over the next ``horizon_h`` hours.

    Naive model: assumes each occupant departs at their expected_departure_h
    and that the ward feeds in at the typical arrival rate (median LOS / 2).
    """
    from datetime import datetime as _dt, timedelta as _td
    occupants = list(_state["occupants"].values())
    now = get_sim_time()
    projected: List[int] = []
    for hour in range(1, horizon_h + 1):
        horizon = now + _td(hours=hour)
        remaining = 0
        for o in occupants:
            try:
                arrived = _dt.fromisoformat(o["arrived_at"])
                depart_by = arrived + _td(hours=float(o.get("expected_departure_h", 2)))
                if depart_by > horizon:
                    remaining += 1
            except Exception:
                remaining += 1
        projected.append(remaining)
    return BaseResponse(data={"hours": projected, "capacity": CAPACITY})


@app.get("/discharge-lounge/metrics", response_model=BaseResponse, tags=["lounge"])
async def metrics() -> BaseResponse:
    samples = list(_state["los_samples"])
    mean_los = round(sum(samples) / len(samples), 2) if samples else 0

    # True throughput: count of discharges in the trailing 24h window.
    # The bare len(los_samples) saturates at deque.maxlen and is misleading
    # as a throughput indicator (it stops moving once the buffer fills).
    now = get_sim_time()
    cutoff = now - timedelta(hours=24)
    events = list(_state["discharge_events"])
    throughput_24h = 0
    for ev in events:
        try:
            ts = _parse_iso(ev.get("ts")) if isinstance(ev, dict) else None
            if ts is not None and ts >= cutoff:
                throughput_24h += 1
        except Exception:
            continue

    return BaseResponse(data={
        "mean_los_h": mean_los,
        "throughput": throughput_24h,
        "throughput_24h": throughput_24h,
        "samples_count": len(samples),
        "current_occupancy": len(_state["occupants"]),
        "capacity": CAPACITY,
    })


# Item 6.1 — Sláintecare community referral queue
@app.post("/community-referral", response_model=BaseResponse, tags=["slaintecare"])
async def community_referral(data: dict) -> BaseResponse:
    _state["community_referrals"].append({**data, "received_at": get_sim_time().isoformat()})
    _snapshot()
    return BaseResponse(data={"queued": True, "queue_len": len(_state["community_referrals"])})


@app.get("/community-referral/queue", response_model=BaseResponse, tags=["slaintecare"])
async def community_queue() -> BaseResponse:
    return BaseResponse(data=list(_state["community_referrals"]))


def _as_aware(dt: Any) -> Optional[datetime]:
    """Coerce a value (naive or aware datetime, BSON) to an aware UTC datetime."""
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_iso(s: str) -> Optional[datetime]:
    """Parse an ISO-8601 string, tolerating a trailing ``Z`` and forcing
    tz-aware. Used to compare sim-clock timestamps safely."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    return _as_aware(dt)


async def _tail_event_log(mongo_client: Any, stop: asyncio.Event) -> None:
    """Poll MongoDB.MIMIC_SIM.event_log for discharge-relevant events and
    dispatch to the same handlers Kafka would fire. Keeps auto-transfer and
    slot-free-on-discharge working when no Kafka broker is present.

    BSON datetimes are timezone-naive on read. We coerce both sides to
    aware UTC so ``$gt`` and Python-side comparisons stay consistent.
    """
    try:
        coll = mongo_client["MIMIC_SIM"]["event_log"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("tailer cannot open collection: %s", exc)
        return

    last_ts: Optional[datetime] = datetime.now(timezone.utc)
    seen: set = set()
    while not stop.is_set():
        try:
            query: Dict[str, Any] = {
                "topic": {"$in": ["discharge_predicted", "patient_discharged"]},
            }
            # MongoDB stores naive datetimes; strip tzinfo for the $gt compare
            if last_ts is not None:
                query["timestamp"] = {"$gt": last_ts.replace(tzinfo=None)}
            docs = list(coll.find(query).sort("timestamp", 1).limit(200))
            for doc in docs:
                eid = doc.get("event_id")
                if eid and eid in seen:
                    continue
                if eid:
                    seen.add(eid)
                ts = _as_aware(doc.get("timestamp"))
                if ts is not None and (last_ts is None or ts > last_ts):
                    last_ts = ts
                topic = doc.get("topic")
                payload = doc.get("payload") or {}
                if topic == "discharge_predicted":
                    try:
                        readiness = float(payload.get("readiness_score") or 0)
                    except (TypeError, ValueError):
                        continue
                    if readiness < AUTO_TRANSFER_THRESHOLD:
                        continue
                    hadm_id = str(payload.get("hadm_id") or "")
                    if not hadm_id or hadm_id in _state["occupants"]:
                        continue
                    # Don't re-admit a patient who already completed their
                    # lounge stay — same gate as the in-process Kafka path.
                    if hadm_id in _state.get("completed_hadms", set()):
                        continue
                    if len(_state["occupants"]) >= CAPACITY:
                        logger.info(
                            "auto-transfer deferred: lounge_full hadm=%s readiness=%.2f",
                            hadm_id, readiness,
                        )
                        continue
                    try:
                        await _perform_transfer(
                            hadm_id=hadm_id,
                            subject_id=payload.get("subject_id"),
                            source_department=payload.get("source_department") or payload.get("department"),
                            expected_departure_h=payload.get("expected_departure_h"),
                            initiated_by=f"auto:tailer(readiness={readiness:.2f})",
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("tailer auto-transfer failed hadm=%s: %s", hadm_id, exc)
                elif topic == "patient_discharged":
                    hadm_id = str(payload.get("hadm_id") or "")
                    if hadm_id:
                        # Same completion-tracking as the Kafka path so the
                        # tailer can't accidentally re-admit a discharged
                        # patient on a future replay.
                        completed = _state.setdefault("completed_hadms", set())
                        order = _state.setdefault("completed_order", deque(maxlen=10000))
                        if hadm_id not in completed:
                            completed.add(hadm_id)
                            order.append(hadm_id)
                            while len(completed) > order.maxlen:
                                completed.discard(order[0] if order else None)
                        if hadm_id in _state["occupants"]:
                            _state["occupants"].pop(hadm_id, None)
                            logger.info(
                                "lounge slot freed via tailer patient_discharged hadm=%s",
                                hadm_id,
                            )
                # Bound the dedupe set to avoid unbounded memory
                if len(seen) > 10_000:
                    seen.clear()
        except Exception as exc:  # noqa: BLE001
            logger.warning("tail cycle error: %s", exc, exc_info=False)
        try:
            await asyncio.wait_for(stop.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass


async def _expire_occupants(stop: asyncio.Event) -> None:
    """Background watcher: auto-complete occupants whose expected-departure
    has elapsed. Uses the sim clock so time-warped simulations still age out
    correctly. Idempotent against the manual /complete path."""
    logger.info("auto-expiry watcher started (poll=%.1fs)", EXPIRY_POLL_SECONDS)
    while not stop.is_set():
        try:
            # Use whichever clock is further ahead. sim_clock freezes when the
            # sim engine isn't advancing; wall clock always ticks. Using max()
            # means a patient's expected-hours expire based on real elapsed
            # time even in a paused-sim dev session.
            sim_now = get_sim_time()
            wall_now = datetime.now(timezone.utc)
            now = sim_now if sim_now > wall_now else wall_now
            expired: List[str] = []
            # Snapshot keys first so we don't mutate during iteration
            for hadm_id, rec in list(_state["occupants"].items()):
                try:
                    arrived = _parse_iso(rec.get("arrived_at", ""))
                    if arrived is None:
                        continue
                    hours = float(rec.get("expected_departure_h") or 0)
                    depart_by = arrived + timedelta(hours=hours)
                    if depart_by <= now:
                        expired.append(hadm_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("expiry check failed hadm=%s: %s", hadm_id, exc)
            for hadm_id in expired:
                try:
                    rec = await _perform_complete(hadm_id, completed_via="auto:expiry")
                    if rec is not None:
                        logger.info(
                            "auto-expired lounge occupant hadm=%s los_target_h=%s",
                            hadm_id, rec.get("expected_departure_h"),
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("auto-expiry failed hadm=%s: %s", hadm_id, exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("expiry cycle error: %s", exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=EXPIRY_POLL_SECONDS)
        except asyncio.TimeoutError:
            pass


_BED_MGMT_POLL_SECONDS = 30.0


async def _hydrate_from_bed_management(stop: asyncio.Event) -> None:
    """Mirror bed_management's Discharge_Lounge bed registry.

    bed_management is the operational source of truth for which beds are
    physically allocated. Its sim_reconciler assigns Discharge_Lounge
    beds to patients near discharge, but those allocations don't fire
    ``patient_transferred`` Kafka events — so this service would
    otherwise stay at 0 occupants while bed_mgmt shows several.

    Approach: every 30 s pull ``GET /beds`` from bed_mgmt, filter to
    occupied lounge beds, and converge our occupants set:
      • new lounge bed → call ``_perform_transfer`` to admit
      • lounge bed disappeared → call ``_perform_complete`` to free
    Uses ``completed_hadms`` so re-pulls don't re-admit a patient who
    already cycled through.
    """
    logger.info("bed_mgmt hydration loop started (poll=%.1fs)", _BED_MGMT_POLL_SECONDS)
    while not stop.is_set():
        try:
            client = _state.get("client") or ServiceClient()
            res = await client.bed_management.get("/beds")
            beds = (res.get("data") if isinstance(res, dict) else None) or []
            if not isinstance(beds, list):
                beds = []
            lounge_hadms: set[str] = set()
            for b in beds:
                if not isinstance(b, dict):
                    continue
                if b.get("department") != "Discharge_Lounge":
                    continue
                if b.get("status") != "occupied":
                    continue
                hid = b.get("hadm_id")
                if hid:
                    lounge_hadms.add(str(hid))

            occupants = _state.get("occupants", {})
            completed = _state.setdefault("completed_hadms", set())

            # Admit any lounge-occupied patients we don't already track.
            for hadm_id in lounge_hadms:
                if hadm_id in occupants or hadm_id in completed:
                    continue
                if len(occupants) >= CAPACITY:
                    logger.info(
                        "hydrate skipped: full hadm=%s", hadm_id,
                    )
                    break
                # Look up the bed for source dept hint
                source_dept = None
                for b in beds:
                    if str(b.get("hadm_id") or "") == hadm_id:
                        source_dept = b.get("department")
                        break
                try:
                    await _perform_transfer(
                        hadm_id=hadm_id,
                        subject_id=None,
                        source_department=source_dept,
                        expected_departure_h=None,
                        initiated_by="auto:bed_mgmt_hydrate",
                    )
                    logger.info(
                        "hydrated lounge occupant hadm=%s from bed_mgmt", hadm_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "hydrate transfer failed hadm=%s: %s", hadm_id, exc,
                    )

            # Release tracked occupants that bed_mgmt no longer has in lounge.
            stale = [h for h in list(occupants.keys()) if h not in lounge_hadms]
            for hadm_id in stale:
                rec = occupants.get(hadm_id, {})
                # Only release if this entry was hydrated from bed_mgmt — don't
                # auto-complete genuine discharge_predicted-driven entries
                # which the bed_mgmt poll would temporarily miss before its
                # own reconciler caught up.
                if rec.get("initiated_by", "").startswith("auto:bed_mgmt_hydrate"):
                    try:
                        await _perform_complete(hadm_id, completed_via="auto:bed_mgmt_release")
                        logger.info(
                            "released hydrated occupant hadm=%s (no longer in bed_mgmt)", hadm_id,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "hydrate release failed hadm=%s: %s", hadm_id, exc,
                        )
        except Exception as exc:  # noqa: BLE001
            logger.warning("bed_mgmt_hydrate cycle error: %s", exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=_BED_MGMT_POLL_SECONDS)
        except asyncio.TimeoutError:
            pass


def _snapshot() -> None:
    """Persist current state to MongoDB. Called after every mutation."""
    persistent = _state.get("persistent")
    if persistent is None:
        return
    try:
        persistent.save_snapshot({
            "occupants": dict(_state["occupants"]),
            "community_referrals": list(_state["community_referrals"]),
            "los_samples": list(_state["los_samples"]),
            "discharge_events": list(_state["discharge_events"]),
        })
    except Exception as exc:  # noqa: BLE001
        logger.debug("lounge_snapshot_err: %s", exc)


@app.post("/reset", response_model=BaseResponse, tags=["system"])
async def reset_lounge() -> BaseResponse:
    _state["occupants"].clear()
    _state["community_referrals"].clear()
    _state["metrics_history"].clear()
    _state["los_samples"].clear()
    _state["discharge_events"].clear()
    _state.get("completed_hadms", set()).clear()
    _state.get("completed_order", deque()).clear()
    persistent = _state.get("persistent")
    if persistent is not None:
        persistent.clear()
    return BaseResponse(data={"reset": True})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8221)
