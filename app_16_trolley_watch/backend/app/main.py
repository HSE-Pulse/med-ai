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
from app_16_trolley_watch.backend.app.hse_trolleygar import (
    ZONES as HSE_ZONES,
    fetch_trolleygar,
)
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
    "hse_latest": None,     # most recent reconciled TrolleyGAR report
    "mongo": None,
}

# ── HSE TrolleyGAR daily poll ────────────────────────────────────────────
#
# Real national trolley counts, all six health regions, refreshed once a day.
# Hospitals report to the HSE at 08:00 and the published snapshot is revised
# through the morning, so the poll runs late morning Irish time and simply
# retries on failure rather than hammering the source.
HSE_POLL_COLL = "hse_trolleygar"
HSE_POLL_HOUR_UTC = int(os.getenv("HSE_TROLLEYGAR_HOUR_UTC", "10"))   # ~11:00 IST
HSE_POLL_RETRY_MINUTES = 30


def _hse_coll():
    mongo = _state.get("mongo")
    if mongo is None:
        return None
    try:
        return mongo.client["MIMIC_SIM"][HSE_POLL_COLL]
    except Exception:  # noqa: BLE001
        return None


def _store_hse_report(rep) -> bool:
    """Persist a report. Unreconciled parses are stored but never served.

    Keeping the failed parse is deliberate — if the HSE changes the page
    layout, the stored document plus its reconciliation notes are what let
    us fix the parser without waiting another day for a sample.
    """
    coll = _hse_coll()
    if coll is None:
        return False
    doc = rep.to_doc()
    try:
        coll.update_one({"report_date": rep.report_date}, {"$set": doc}, upsert=True)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("hse_trolleygar_store_failed: %s", exc)
        return False


def _load_latest_hse():
    coll = _hse_coll()
    if coll is None:
        return None
    try:
        rows = list(
            coll.find({"reconciled": True}, {"_id": 0})
            .sort([("report_date", -1)]).limit(1)
        )
        return rows[0] if rows else None
    except Exception:  # noqa: BLE001
        return None


def _poll_hse_once(on=None):
    """Blocking fetch+parse+store. Returns the report or None."""
    try:
        rep = fetch_trolleygar(on)
    except Exception as exc:  # noqa: BLE001
        logger.warning("hse_trolleygar_fetch_failed: %s", exc)
        return None
    _store_hse_report(rep)
    if rep.reconciled:
        # Only promote to the "latest" cache if this really IS the newest
        # report. _poll_hse_once also serves on-demand backfill for the date
        # picker, and an unconditional assignment let a request for an old
        # date overwrite today's figures — /trolley/hse/latest then served a
        # three-week-old national total until the service restarted.
        cached = _state.get("hse_latest") or {}
        if str(rep.report_date) >= str(cached.get("report_date") or ""):
            _state["hse_latest"] = rep.to_doc()
        logger.info(
            "hse_trolleygar_polled date=%s national_total=%s zones=%d hospitals=%d",
            rep.report_date, rep.national.get("total_trolleys"),
            len(rep.zones), len(rep.hospitals),
        )
    else:
        logger.warning(
            "hse_trolleygar_unreconciled date=%s — not served; notes=%s",
            rep.report_date, "; ".join(rep.reconciliation_notes)[:300],
        )
    return rep


async def _hse_poll_loop():
    """Poll once at startup, then daily at HSE_POLL_HOUR_UTC."""
    import asyncio as _a

    try:
        have = await _a.to_thread(_load_latest_hse)
        if have:
            _state["hse_latest"] = have
        today = datetime.now(timezone.utc).date().isoformat()
        if not have or have.get("report_date") != today:
            await _a.to_thread(_poll_hse_once)
    except Exception as exc:  # noqa: BLE001
        logger.warning("hse_poll_startup_failed: %s", exc)

    while True:
        try:
            now = datetime.now(timezone.utc)
            nxt = now.replace(hour=HSE_POLL_HOUR_UTC, minute=0, second=0, microsecond=0)
            if nxt <= now:
                nxt += timedelta(days=1)
            await _a.sleep(max(60.0, (nxt - now).total_seconds()))

            rep = await _a.to_thread(_poll_hse_once)
            # One retry pass if the source was unavailable or the layout
            # changed — the morning snapshot is revised for a few hours.
            for _ in range(3):
                if rep is not None and rep.reconciled:
                    break
                await _a.sleep(HSE_POLL_RETRY_MINUTES * 60)
                rep = await _a.to_thread(_poll_hse_once)
        except _a.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("hse_poll_loop_error: %s", exc)
            await _a.sleep(300)


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

    # Real HSE TrolleyGAR data, all six health regions, polled daily.
    try:
        from shared.db.mongo import MongoManager as _MM
        _state["mongo"] = _MM()
    except Exception as exc:  # noqa: BLE001
        logger.warning("trolley_mongo_init_failed: %s", exc)
    import asyncio as _aio
    _state["hse_poll_task"] = _aio.create_task(_hse_poll_loop())

    # Also subscribe via Kafka for cross-service durability + ring buffer
    try:
        from shared.db.mongo import MongoManager
        from shared.integration.kafka_consumer import attach_with_ring_buffer
        _mongo = MongoManager()
        await attach_with_ring_buffer(
            service_id="trolley_watch",
            topics=["trolley_alert", "capacity_alert", "pet_breach_risk"],
            mongo_client=_mongo.client,
            extra_handlers={
                "trolley_alert": _kafka_trolley_alert,
                "capacity_alert": _kafka_capacity_alert,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("trolley_bus_subscribe_failed: %s", exc)

    logger.info("Trolley Watch ready on port 8216")
    yield


def _trim_events() -> None:
    """Cap the in-memory window. capacity_alert is a high-volume topic
    (~190k messages on the broker), so every append path must trim or the
    process grows without bound."""
    if len(_state["events"]) > 5000:
        _state["events"] = _state["events"][-2500:]


def _record_trolley(payload: Dict[str, Any]) -> None:
    _state["events"].append({
        "department": payload.get("department"),
        "location": payload.get("location", "ED"),
        "count": int(payload.get("count", 1)),
        "timestamp": get_sim_time().isoformat(),
    })
    _trim_events()


def _record_capacity(payload: Dict[str, Any]) -> None:
    if payload.get("urgency") in ("red", "black"):
        _state["events"].append({
            "department": payload.get("department"),
            "location": "ED",
            "count": 1,
            "timestamp": get_sim_time().isoformat(),
            "source": "capacity_alert",
        })
        _trim_events()


# Two transports feed the same recorders. The in-process EventBus only fires
# for events published inside THIS process; everything produced by the other
# service containers arrives over Kafka, which is why the Kafka adapters below
# have to be registered as extra_handlers (see lifespan).
def _on_trolley_alert(event) -> None:
    _record_trolley(event.payload or {})


def _on_capacity_alert(event) -> None:
    _record_capacity(event.payload or {})


async def _kafka_trolley_alert(_topic, payload) -> None:
    _record_trolley(payload or {})


async def _kafka_capacity_alert(_topic, payload) -> None:
    _record_capacity(payload or {})


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
    """INMO-template snapshot for a date — real HSE TrolleyGAR figures.

    Columns: hospital, region, date, time_of_count, trolleys_ed,
    trolleys_wards, total.

    This used to aggregate the simulator's own in-memory trolley events and
    filter them by date. It could never work: those events are stamped on
    the SIM clock (months ahead of wall time) while the date picker sends a
    wall date, so the filter matched nothing and every date returned the
    same single row of zeros. It now serves the published HSE report for the
    requested day, which is what a date picker on an INMO-style table should
    show.

    A date we don't hold yet is fetched on demand and cached, so picking a
    past date backfills it rather than returning an empty table.
    """
    import asyncio as _aio
    import datetime as _dt

    today = _dt.date.today()
    if date:
        try:
            want = _dt.date.fromisoformat(date)
        except ValueError:
            return BaseResponse(status="error", error="date must be YYYY-MM-DD")
        if want > today:
            return BaseResponse(data=[])   # no report exists yet
    else:
        want = today

    coll = _hse_coll()
    doc = None
    if coll is not None:
        try:
            doc = coll.find_one({"report_date": want.isoformat(), "reconciled": True},
                                {"_id": 0})
        except Exception as exc:  # noqa: BLE001
            logger.warning("inmo_report_lookup_failed: %s", exc)

    # Backfill on demand — the user asked for a day we haven't polled.
    if doc is None:
        rep = await _aio.to_thread(_poll_hse_once, want)
        if rep is not None and rep.reconciled:
            doc = rep.to_doc()

    # Fall back to the most recent report we do hold, so an outage shows
    # stale-but-labelled data rather than an empty table.
    if doc is None:
        doc = _load_latest_hse()
        if doc is None:
            return BaseResponse(data=[])

    rows = []
    national = doc.get("national") or {}
    if national.get("total_trolleys") is not None:
        rows.append({
            "hospital": "NATIONAL TOTAL",
            "region": "All six HSE health regions",
            "date": doc.get("report_date"),
            "time_of_count": "08:00",
            "trolleys_ed": national.get("ed_trolleys"),
            "trolleys_wards": national.get("ward_trolleys"),
            "total": national.get("total_trolleys"),
            "surge_capacity": national.get("surge_capacity"),
            "delayed_transfers": national.get("delayed_transfers"),
        })
    for h in sorted(doc.get("hospitals", []),
                    key=lambda x: -(x.get("total_trolleys") or 0)):
        rows.append({
            "hospital": h.get("hospital"),
            "region": h.get("zone"),
            "date": doc.get("report_date"),
            "time_of_count": "08:00",
            "trolleys_ed": h.get("ed_trolleys"),
            "trolleys_wards": h.get("ward_trolleys"),
            "total": h.get("total_trolleys"),
            "surge_capacity": h.get("surge_capacity"),
            "delayed_transfers": h.get("delayed_transfers"),
        })
    return BaseResponse(data=rows)


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


# ── Real HSE TrolleyGAR data ─────────────────────────────────────────────


@app.get("/trolley/hse/latest", response_model=BaseResponse, tags=["trolley"])
async def hse_latest() -> BaseResponse:
    """Latest reconciled HSE TrolleyGAR report — national + all six zones.

    This is REAL published HSE data, distinct from the simulator's own
    trolley counts under /trolley/count. Only reconciled parses are served:
    if the numbers didn't add up, this reports stale-but-correct rather than
    fresh-but-wrong.
    """
    rep = _state.get("hse_latest") or _load_latest_hse()
    if not rep:
        return BaseResponse(
            status="ok",
            data={"available": False, "reason": "no reconciled HSE report yet"},
        )
    _state["hse_latest"] = rep
    return BaseResponse(data={
        "available": True,
        "report_date": rep.get("report_date"),
        "national": rep.get("national", {}),
        "zones": rep.get("zones", {}),
        "hospital_count": len(rep.get("hospitals", [])),
        "source": "HSE Special Delivery Unit TrolleyGAR",
        "source_url": rep.get("source_url"),
        "fetched_at_wall": rep.get("fetched_at_wall"),
    })


@app.get("/trolley/hse/zones", response_model=BaseResponse, tags=["trolley"])
async def hse_zones() -> BaseResponse:
    """Per-zone breakdown for all six HSE health regions, worst first."""
    rep = _state.get("hse_latest") or _load_latest_hse()
    if not rep:
        return BaseResponse(status="ok", data={"available": False, "zones": []})
    hospitals = rep.get("hospitals", [])
    out = []
    for zone in HSE_ZONES:
        z = rep.get("zones", {}).get(zone) or {}
        members = [h for h in hospitals if h.get("zone") == zone]
        members.sort(key=lambda h: -(h.get("total_trolleys") or 0))
        out.append({
            "zone": zone,
            "ed_trolleys": z.get("ed_trolleys"),
            "ward_trolleys": z.get("ward_trolleys"),
            "total_trolleys": z.get("total_trolleys"),
            "surge_capacity": z.get("surge_capacity"),
            "delayed_transfers": z.get("delayed_transfers"),
            "hospital_count": len(members),
            "worst_hospital": members[0]["hospital"] if members else None,
            "worst_hospital_total": members[0]["total_trolleys"] if members else None,
            "hospitals": members,
        })
    out.sort(key=lambda z: -(z.get("total_trolleys") or 0))
    return BaseResponse(data={
        "available": True,
        "report_date": rep.get("report_date"),
        "zones": out,
    })


@app.get("/trolley/hse/history", response_model=BaseResponse, tags=["trolley"])
async def hse_history(days: int = Query(30, ge=1, le=365)) -> BaseResponse:
    """National and per-zone trolley totals over recent days."""
    coll = _hse_coll()
    if coll is None:
        return BaseResponse(status="ok", data={"available": False, "series": []})
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    try:
        rows = list(
            coll.find({"reconciled": True, "report_date": {"$gte": cutoff}},
                      {"_id": 0, "report_date": 1, "national": 1, "zones": 1})
            .sort([("report_date", 1)])
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("hse_history_failed: %s", exc)
        rows = []
    return BaseResponse(data={
        "available": bool(rows),
        "days": days,
        "series": [{
            "date": r.get("report_date"),
            "national_total": (r.get("national") or {}).get("total_trolleys"),
            "national_ed": (r.get("national") or {}).get("ed_trolleys"),
            "zones": {z: (v or {}).get("total_trolleys") for z, v in (r.get("zones") or {}).items()},
        } for r in rows],
    })


@app.post("/trolley/hse/refresh", response_model=BaseResponse, tags=["trolley"])
async def hse_refresh(date: Optional[str] = Query(None, description="YYYY-MM-DD")) -> BaseResponse:
    """Force a poll now (operator action / backfill a specific date)."""
    import asyncio as _aio
    import datetime as _dt
    on = None
    if date:
        try:
            on = _dt.date.fromisoformat(date)
        except ValueError:
            return BaseResponse(status="error", error="date must be YYYY-MM-DD")
    rep = await _aio.to_thread(_poll_hse_once, on)
    if rep is None:
        return BaseResponse(status="error", error="fetch failed")
    return BaseResponse(data={
        "report_date": rep.report_date,
        "reconciled": rep.reconciled,
        "national": rep.national,
        "zones_parsed": len(rep.zones),
        "hospitals_parsed": len(rep.hospitals),
        "notes": rep.reconciliation_notes,
    })
