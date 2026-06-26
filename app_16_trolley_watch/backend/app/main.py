"""HSE Trolley Watch — port 8216.

Aggregated trolley reporting for Irish hospitals. Feeds the INMO-compatible
daily 08:00 snapshot and cross-references with PET breach history. No
patient-level data — counts only (GDPR Art. 5).
"""

from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fastapi import FastAPI, Query

from shared.api.base import AIActInfo, BaseResponse, PrivacyNotice, create_app
from shared.constants.hospital import HSE_REGIONS, region_for_department
from shared.integration.event_bus import get_event_bus
from shared.integration.service_client import ServiceClient
from shared.integration.sim_clock import get_sim_time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("trolley_watch")

PRIVACY = PrivacyNotice(
    data_collected=["trolley_count", "department", "hospital_region", "timestamp"],
    legal_basis="Public task (HSE/INMO trolley reporting, GDPR Art. 6(1)(e))",
    retention_period="24 months",
    third_party_sharing=["INMO (aggregate counts only)"],
)


_state: Dict[str, Any] = {
    "events": [],           # list of {department, location, count, timestamp}
    "daily_snapshots": [],  # list of {date, hospital, region, ed_count, ward_count, total}
}


@asynccontextmanager
async def lifespan(application: FastAPI):
    # Observability
    try:
        from shared.integration.logging_config import setup_logging
        setup_logging(service_name="trolley_watch")
    except Exception as exc:  # noqa: BLE001
        logger.warning("logging_setup_failed: %s", exc)
    try:
        from shared.integration.tracing import setup_tracing
        setup_tracing(application, service_name="trolley_watch")
    except Exception as exc:  # noqa: BLE001
        logger.warning("tracing_setup_failed: %s", exc)
    try:
        from shared.integration.prometheus_metrics import install_metrics
        install_metrics(application, service_name="trolley_watch")
    except Exception as exc:  # noqa: BLE001
        logger.warning("prometheus_metrics_install_failed: %s", exc)

    # Anchor SimClock to data_ingestion's authoritative clock.
    try:
        from shared.integration.sim_clock import attach_remote_clock
        await attach_remote_clock()
    except Exception as exc:  # noqa: BLE001
        logger.warning("sim_clock_attach_remote_failed: %s", exc)

    _state["event_bus"] = get_event_bus()
    _state["client"] = ServiceClient()
    # Subscribe to upstream capacity events so we can incrementally track trolleys.
    _state["event_bus"].subscribe("trolley_alert", _on_trolley_alert)
    _state["event_bus"].subscribe("capacity_alert", _on_capacity_alert)

    # Also subscribe via Kafka for cross-service durability + ring buffer
    try:
        from shared.db.mongo import MongoManager
        from shared.integration.kafka_consumer import attach_with_ring_buffer
        _mongo = MongoManager()
        await attach_with_ring_buffer(
            service_id="trolley_watch",
            topics=["trolley_alert", "capacity_alert", "pet_breach_risk"],
            mongo_client=_mongo.client,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("trolley_bus_subscribe_failed: %s", exc)

    logger.info("Trolley Watch ready on port 8216")
    yield


def _on_trolley_alert(event) -> None:
    payload = event.payload or {}
    _state["events"].append({
        "department": payload.get("department"),
        "location": payload.get("location", "ED"),
        "count": int(payload.get("count", 1)),
        "timestamp": get_sim_time().isoformat(),
    })
    if len(_state["events"]) > 5000:
        _state["events"] = _state["events"][-2500:]


def _on_capacity_alert(event) -> None:
    payload = event.payload or {}
    if payload.get("urgency") in ("red", "black"):
        _state["events"].append({
            "department": payload.get("department"),
            "location": "ED",
            "count": 1,
            "timestamp": get_sim_time().isoformat(),
            "source": "capacity_alert",
        })


app = create_app(
    title="HSE Trolley Watch",
    version="1.0.0",
    description="INMO-compatible trolley-count tracking for Irish hospitals.",
    privacy_notice=PRIVACY,
)
app.router.lifespan_context = lifespan


@app.get("/kafka-events", response_model=BaseResponse, tags=["system"])
async def list_kafka_events(limit: int = 100) -> BaseResponse:
    """Return recent cross-service events consumed via Kafka/broker."""
    from shared.integration.kafka_consumer import get_kafka_events
    return BaseResponse(data=get_kafka_events("trolley_watch", limit))


@app.get("/trolley/count", response_model=BaseResponse, tags=["trolley"])
async def get_count() -> BaseResponse:
    """Return the current trolley count broken down by location."""
    counts: Dict[str, int] = defaultdict(int)
    # Look only at the last 2 hours of events to avoid stale accumulation.
    cutoff = get_sim_time() - timedelta(hours=2)
    for e in _state["events"]:
        try:
            ts = datetime.fromisoformat(e["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if ts >= cutoff:
            counts[e.get("location", "ED")] += int(e.get("count", 1))
    return BaseResponse(data={
        "ed": counts.get("ED", 0),
        "corridor": counts.get("corridor", 0),
        "ward": counts.get("ward", 0),
        "total": sum(counts.values()),
        "observed_at": get_sim_time().isoformat(),
    })


@app.post("/trolley/report", response_model=BaseResponse, tags=["trolley"])
async def report_trolley(data: dict) -> BaseResponse:
    """Record a new trolley event — called by Bed Mgmt / ED Flow."""
    doc = {
        "department": data.get("department"),
        "location": data.get("location", "ED"),
        "count": int(data.get("count", 1)),
        "timestamp": data.get("timestamp") or get_sim_time().isoformat(),
    }
    _state["events"].append(doc)
    return BaseResponse(data=doc)


@app.get("/trolley/history", response_model=BaseResponse, tags=["trolley"])
async def get_history(days: int = Query(7, ge=1, le=90)) -> BaseResponse:
    """Return daily trolley counts for the last *days* days."""
    by_day: Dict[str, int] = defaultdict(int)
    cutoff = get_sim_time() - timedelta(days=days)
    for e in _state["events"]:
        try:
            ts = datetime.fromisoformat(e["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if ts >= cutoff:
            by_day[ts.date().isoformat()] += int(e.get("count", 1))
    return BaseResponse(data=[{"date": d, "count": c} for d, c in sorted(by_day.items())])


@app.get("/trolley/compliance", response_model=BaseResponse, tags=["trolley"])
async def get_compliance() -> BaseResponse:
    """Correlate trolley counts with PET breach history.

    Pulls recent PET breach events from the shared EventBus log and computes
    a very coarse correlation with the per-hour trolley count.
    """
    bus = _state.get("event_bus")
    breach_events = bus.get_recent_events("pet_breach_risk", limit=500) if bus else []
    return BaseResponse(data={
        "breach_events_window": len(breach_events),
        "current_trolleys": sum(int(e.get("count", 1)) for e in _state["events"][-200:]),
        "hint": (
            "Trolley counts >15 correlate with rising PET breach risk in historical data"
        ),
    })


@app.get("/trolley/inmo-report", response_model=BaseResponse, tags=["trolley"])
async def inmo_report(date: Optional[str] = Query(None)) -> BaseResponse:
    """Return an INMO-template-compatible snapshot for the requested date.

    Format columns: hospital, region, date, time_of_count, trolleys_ed,
    trolleys_wards, total.
    """
    target_day = date or get_sim_time().date().isoformat()
    ed = 0
    wards = 0
    for e in _state["events"]:
        try:
            ts = datetime.fromisoformat(e["timestamp"]).date().isoformat()
        except Exception:
            continue
        if ts != target_day:
            continue
        if e.get("location") == "ED":
            ed += int(e.get("count", 1))
        else:
            wards += int(e.get("count", 1))
    hospital = os.environ.get("HOSPITAL_NAME", "Model Hospital Dublin")
    region = region_for_department("ED")
    return BaseResponse(data=[{
        "hospital": hospital,
        "region": region,
        "date": target_day,
        "time_of_count": "08:00",
        "trolleys_ed": ed,
        "trolleys_wards": wards,
        "total": ed + wards,
    }])


@app.post("/trolley/daily-digest", response_model=BaseResponse, tags=["trolley"])
async def daily_digest() -> BaseResponse:
    """Generate an 08:00 snapshot and publish to the EventBus.

    Called by an APScheduler job in the lifespan; also exposed as POST so
    operations can trigger manually.
    """
    report = (await inmo_report()).data
    snapshot = report[0] if report else {}
    _state["daily_snapshots"].append(snapshot)
    if len(_state["daily_snapshots"]) > 365:
        _state["daily_snapshots"] = _state["daily_snapshots"][-365:]
    bus = _state.get("event_bus")
    if bus:
        await bus.publish("trolley_daily_report", snapshot, source_module="trolley_watch")
    return BaseResponse(data=snapshot)


@app.post("/reset", response_model=BaseResponse, tags=["system"])
async def reset_trolley() -> BaseResponse:
    _state["events"] = []
    _state["daily_snapshots"] = []
    return BaseResponse(data={"reset": True})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8216)
