"""Alert Aggregator + Patient Search — port 8222.

Fan-in of events from the shared event bus into a normalised Alert stream
that the dashboard subscribes to via WebSocket. Also exposes patient search
over MIMIC admissions for the global command palette.

Endpoints
---------
  GET  /alerts/recent                 list recent alerts (polling fallback)
  POST /alerts/{alert_id}/ack         mark an alert acknowledged
  WS   /alerts/stream                 live stream of normalised alerts
  GET  /search/patients?q=...         search patients by hadm_id / subject_id
  GET  /health                        readiness probe
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

try:
    from shared.db.mongo import MongoManager
    _HAS_MONGO = True
except Exception:
    _HAS_MONGO = False

# Wire structured JSON + Loki logging like the other 18 services. Without
# this call the alerts service stayed on basicConfig stdout and was the
# only service missing from Loki labels.
from shared.integration.logging_config import setup_logging as _setup_logging
_setup_logging("alerts")

logger = logging.getLogger("alerts_aggregator")


# ──────────────────────────────────────────────────────────────────────
# Topic → severity + display mapping
# ──────────────────────────────────────────────────────────────────────
SEVERITY_MAP: Dict[str, str] = {
    "deterioration_critical": "critical",
    "deterioration_alert": "high",
    "capacity_alert": "high",
    "trolley_alert": "high",
    "pet_breach_risk": "high",
    "lwbs_risk": "medium",
    "surge_alert": "high",
    "bottleneck_detected": "medium",
    "admission_predicted": "info",
    "bed_allocated": "info",
    "bed_released": "info",
    "patient_admitted": "info",
    "patient_discharged": "info",
    "patient_transferred": "info",
    "priority_updated": "medium",
    "schedule_generated": "info",
    "referral_triaged": "info",
    "note_generated": "info",
    "note_approved": "info",
    "coding_suggested": "info",
    "admission_complete": "info",
}

MODULE_ROUTE_HINT: Dict[str, str] = {
    "deterioration_monitor": "/deterioration",
    "bed_management": "/bed-management",
    "trolley_watch": "/trolley",
    "ed_flow": "/ed-flow",
    "ed_triage": "/ed-triage",
    "sepsis_icu": "/sepsis",
    "hospital_ops": "/hospital-ops",
    "oncology_ai": "/oncology",
    "waiting_list": "/waiting-list",
    "clinical_scribe": "/clinical-scribe",
    "discharge_lounge": "/discharge-lounge",
    "gdpr_compliance": "/gdpr",
    "xai_audit": "/xai",
    "fhir_gateway": "/fhir",
}

TOPIC_TITLES: Dict[str, str] = {
    "deterioration_critical": "Critical deterioration (NEWS2)",
    "deterioration_alert": "Deterioration risk rising",
    "capacity_alert": "Department capacity",
    "trolley_alert": "Trolley count exceeds threshold",
    "pet_breach_risk": "PET 6-hour target at risk",
    "lwbs_risk": "Patient at risk of LWBS",
    "surge_alert": "ED surge predicted",
    "bottleneck_detected": "ED bottleneck detected",
    "admission_predicted": "Admission predicted",
    "bed_allocated": "Bed allocated",
    "bed_released": "Bed released",
    "patient_admitted": "Patient admitted",
    "patient_discharged": "Patient discharged",
    "patient_transferred": "Patient transferred",
    "priority_updated": "Priority updated",
    "schedule_generated": "Schedule generated",
    "referral_triaged": "Referral triaged",
    "note_generated": "Clinical note generated",
    "note_approved": "Note approved",
    "coding_suggested": "ICD codes suggested",
    "admission_complete": "Admission pipeline complete",
}


def _iso(ts: Any) -> str:
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.isoformat()
    if isinstance(ts, str):
        return ts
    return datetime.now(timezone.utc).isoformat()


def _extract_patient_id(payload: Dict[str, Any]) -> Optional[str]:
    for key in ("patient_id", "hadm_id", "subject_id", "stay_id"):
        if key in payload and payload[key] is not None:
            return str(payload[key])
    return None


def normalise_event(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Turn an event_log document into an Alert envelope."""
    topic = doc.get("topic", "unknown")
    payload = doc.get("payload") or {}
    source = doc.get("source_module", "")
    severity = SEVERITY_MAP.get(topic, "info")
    title = TOPIC_TITLES.get(topic, topic.replace("_", " ").title())
    patient_id = _extract_patient_id(payload)
    route = MODULE_ROUTE_HINT.get(source)
    # Topic-based fallback — covers cases where source_module is absent or
    # is a publisher we don't know about. Order matters: more specific prefixes first.
    if route is None:
        if topic.startswith("deterioration"):
            route = "/deterioration"
        elif topic.startswith("trolley") or topic == "capacity_alert":
            route = "/trolley"
        elif topic.startswith("bed"):
            route = "/bed-management"
        elif topic in ("pet_breach_risk", "lwbs_risk", "surge_alert",
                        "bottleneck_detected", "admission_predicted"):
            route = "/ed-flow"
        elif topic in ("priority_updated", "referral_triaged", "schedule_generated"):
            route = "/waiting-list"
        elif topic in ("note_generated", "note_approved", "coding_suggested"):
            route = "/clinical-scribe"
        elif topic in ("patient_admitted", "patient_discharged",
                        "patient_transferred", "admission_complete"):
            route = "/patient-journey"

    ts = _iso(doc.get("timestamp"))
    return {
        "id": doc.get("event_id") or str(uuid.uuid4()),
        "topic": topic,
        "severity": severity,
        "title": title,
        "source_module": source,
        "patient_id": patient_id,
        "payload": payload,
        "timestamp": ts,            # first event in the group
        "last_timestamp": ts,       # most recent event in the group
        "count": 1,                 # number of coalesced events
        "route_hint": route,
        "acknowledged": False,
    }


# ──────────────────────────────────────────────────────────────────────
# In-memory alert store + broadcaster
# ──────────────────────────────────────────────────────────────────────
SEVERITY_RANK = {"info": 0, "medium": 1, "high": 2, "critical": 3}


def _parse_ts(iso: str) -> datetime:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


class AlertStore:
    """Alerts are coalesced by (topic, patient_id) inside a rolling window
    and as long as the group is still unacknowledged. This keeps the bell from
    drowning in repeat predictions (e.g. discharge_predicted on every vital)
    without losing the signal — the group's count, latest payload, and peak
    severity are kept on the row that represents it."""

    MAX_ALERTS = 500
    COALESCE_WINDOW_SECONDS = 600   # 10 minutes
    GC_EVERY = 100                   # prune stale group keys this often

    def __init__(self) -> None:
        self._alerts: Deque[Dict[str, Any]] = deque(maxlen=self.MAX_ALERTS)
        self._seen: Set[str] = set()
        self._ack: Set[str] = set()                       # alert_id (group id) → acked
        self._groups: Dict[Tuple[str, str], Dict[str, Any]] = {}  # (topic, pid) → latest row
        self._lock = asyncio.Lock()
        self._subscribers: Set[asyncio.Queue] = set()
        self._ingest_counter: int = 0

    def _group_key(self, alert: Dict[str, Any]) -> Optional[Tuple[str, str]]:
        pid = alert.get("patient_id")
        return (alert["topic"], str(pid)) if pid else None

    def _gc_stale_groups(self) -> None:
        """Drop group pointers whose last_timestamp is outside the window.
        Cheap O(groups) scan; runs rarely (every N ingests)."""
        if not self._groups:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=self.COALESCE_WINDOW_SECONDS
        )
        dead = [k for k, a in self._groups.items()
                if _parse_ts(a.get("last_timestamp", a["timestamp"])) < cutoff]
        for k in dead:
            self._groups.pop(k, None)

    async def ingest(self, alert: Dict[str, Any]) -> bool:
        """Add or coalesce an alert. Returns True if the store changed."""
        async with self._lock:
            if alert["id"] in self._seen:
                return False
            self._seen.add(alert["id"])
            self._ingest_counter += 1
            if self._ingest_counter % self.GC_EVERY == 0:
                self._gc_stale_groups()

            key = self._group_key(alert)
            now = _parse_ts(alert["timestamp"])

            # Coalesce into an existing, unacked, in-window group
            if key is not None:
                existing = self._groups.get(key)
                if existing and not existing.get("acknowledged"):
                    last = _parse_ts(existing.get("last_timestamp", existing["timestamp"]))
                    if (now - last).total_seconds() <= self.COALESCE_WINDOW_SECONDS:
                        existing["count"] = existing.get("count", 1) + 1
                        existing["last_timestamp"] = alert["timestamp"]
                        existing["payload"] = alert["payload"]
                        # Bubble up severity if this event is worse
                        if SEVERITY_RANK.get(alert["severity"], 0) > \
                           SEVERITY_RANK.get(existing["severity"], 0):
                            existing["severity"] = alert["severity"]
                            existing["title"] = alert["title"]
                        # route_hint stays stable (first event wins)
                        await self._broadcast({"type": "alert_update",
                                              "alert": dict(existing)})
                        return True

            # New row — either no group, acked group (start fresh), or out-of-window
            if alert["id"] in self._ack:
                alert["acknowledged"] = True
            self._alerts.append(alert)
            if key is not None:
                self._groups[key] = alert
        await self._broadcast({"type": "alert", "alert": alert})
        return True

    async def ack(self, alert_id: str) -> bool:
        async with self._lock:
            self._ack.add(alert_id)
            hit = None
            for a in self._alerts:
                if a["id"] == alert_id:
                    a["acknowledged"] = True
                    hit = a
                    break
            if hit is None:
                return False
            # Detach from the active group so the next event for this
            # (topic, pid) starts a brand-new incident row
            key = self._group_key(hit)
            if key is not None and self._groups.get(key) is hit:
                self._groups.pop(key, None)
        await self._broadcast({"type": "ack", "alert_id": alert_id})
        return True

    def recent(self, limit: int = 50, severity: Optional[str] = None) -> List[Dict[str, Any]]:
        items = list(self._alerts)
        if severity:
            items = [a for a in items if a["severity"] == severity]
        items.reverse()
        return items[:limit]

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def _broadcast(self, msg: Dict[str, Any]) -> None:
        dead: List[asyncio.Queue] = []
        for q in list(self._subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers.discard(q)


STORE = AlertStore()


# ──────────────────────────────────────────────────────────────────────
# Event-log tailer (polling; MongoDB change streams need a replica set)
# ──────────────────────────────────────────────────────────────────────
async def tail_event_log(stop: asyncio.Event) -> None:
    """Poll MongoDB.MIMIC_SIM.event_log for new events and ingest as alerts."""
    if not _HAS_MONGO:
        logger.warning("Mongo not importable; alert tail disabled.")
        return
    try:
        mm = MongoManager()
        coll = mm.get_collection("MIMIC_SIM", "event_log")
    except Exception as exc:
        logger.warning("event_log collection unavailable: %s", exc)
        return

    last_ts: Optional[datetime] = None
    logger.info("alert tailer started")
    while not stop.is_set():
        try:
            query: Dict[str, Any] = {}
            if last_ts is not None:
                query["timestamp"] = {"$gt": last_ts}
            docs = list(coll.find(query).sort("timestamp", 1).limit(200))
            for doc in docs:
                alert = normalise_event(doc)
                await STORE.ingest(alert)
                ts = doc.get("timestamp")
                if isinstance(ts, datetime):
                    last_ts = ts if last_ts is None or ts > last_ts else last_ts
        except Exception as exc:
            logger.debug("tail cycle failed: %s", exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass


# ──────────────────────────────────────────────────────────────────────
# App lifespan
# ──────────────────────────────────────────────────────────────────────
_stop = asyncio.Event()
_tail_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tail_task
    _stop.clear()
    _tail_task = asyncio.create_task(tail_event_log(_stop))
    yield
    _stop.set()
    if _tail_task:
        try:
            await asyncio.wait_for(_tail_task, timeout=5.0)
        except asyncio.TimeoutError:
            _tail_task.cancel()


app = FastAPI(
    title="Alerts Aggregator",
    description="Fan-in of cross-module events into a normalised alert stream with patient search.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────────────────────────────
# REST endpoints
# ──────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "mongo_available": _HAS_MONGO,
        "alerts_cached": len(STORE._alerts),
        "subscribers": len(STORE._subscribers),
    }


@app.get("/alerts/recent")
async def alerts_recent(
    limit: int = Query(50, ge=1, le=500),
    severity: Optional[str] = Query(None, pattern="^(critical|high|medium|info)$"),
) -> Dict[str, Any]:
    return {"alerts": STORE.recent(limit=limit, severity=severity)}


@app.post("/alerts/{alert_id}/ack")
async def alerts_ack(alert_id: str) -> Dict[str, Any]:
    ok = await STORE.ack(alert_id)
    if not ok:
        raise HTTPException(status_code=404, detail="alert not found")
    return {"status": "ok", "alert_id": alert_id}


@app.post("/alerts/demo")
async def alerts_demo() -> Dict[str, Any]:
    """Inject a demo alert. Useful when the simulator isn't running."""
    demo = normalise_event({
        "event_id": str(uuid.uuid4()),
        "topic": "deterioration_critical",
        "source_module": "deterioration_monitor",
        "timestamp": datetime.now(timezone.utc),
        "payload": {"hadm_id": "demo-" + str(int(time.time()) % 9999), "news2": 9, "trend": "rising"},
    })
    await STORE.ingest(demo)
    return {"status": "ok", "alert": demo}


# ──────────────────────────────────────────────────────────────────────
# WebSocket stream
# ──────────────────────────────────────────────────────────────────────
@app.websocket("/alerts/stream")
async def alerts_stream(ws: WebSocket) -> None:
    await ws.accept()
    q = STORE.subscribe()
    # Send the recent backlog first so new clients have immediate context
    try:
        await ws.send_text(json.dumps({
            "type": "snapshot",
            "alerts": STORE.recent(limit=50),
        }))
        while True:
            msg = await q.get()
            await ws.send_text(json.dumps(msg))
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("ws error: %s", exc)
    finally:
        STORE.unsubscribe(q)


# ──────────────────────────────────────────────────────────────────────
# Patient search (for command palette)
# ──────────────────────────────────────────────────────────────────────
@app.get("/search/patients")
async def search_patients(
    q: str = Query(..., min_length=1, max_length=40),
    limit: int = Query(10, ge=1, le=50),
) -> Dict[str, Any]:
    """Search MIMIC admissions by hadm_id / subject_id prefix.

    Falls back to a small stub list when MongoDB is unreachable so the
    dashboard palette still works in dev/demo mode.
    """
    results: List[Dict[str, Any]] = []
    if _HAS_MONGO:
        try:
            mm = MongoManager()
            coll = mm.get_collection("MIMIC", "admissions")
            projection = {
                "_id": 0,
                "hadm_id": 1,
                "subject_id": 1,
                "admittime": 1,
                "dischtime": 1,
                "admission_type": 1,
                "admission_location": 1,
            }
            docs: List[Dict[str, Any]] = []
            if q.isdigit():
                val = int(q)
                # First try exact match
                docs = list(
                    coll.find({"$or": [{"hadm_id": val}, {"subject_id": val}]}, projection).limit(limit)
                )
                if not docs:
                    # Prefix match via $toString aggregation (MIMIC IDs are ints)
                    pipeline = [
                        {"$addFields": {
                            "_hs": {"$toString": "$hadm_id"},
                            "_ss": {"$toString": "$subject_id"},
                        }},
                        {"$match": {"$or": [
                            {"_hs": {"$regex": f"^{q}"}},
                            {"_ss": {"$regex": f"^{q}"}},
                        ]}},
                        {"$project": projection},
                        {"$limit": limit},
                    ]
                    docs = list(coll.aggregate(pipeline, allowDiskUse=False, maxTimeMS=3000))
            else:
                docs = list(coll.find({"$or": [
                    {"hadm_id": {"$regex": f"^{q}", "$options": "i"}},
                    {"subject_id": {"$regex": f"^{q}", "$options": "i"}},
                ]}, projection).limit(limit))

            for doc in docs:
                results.append({
                    "hadm_id": str(doc.get("hadm_id", "")),
                    "subject_id": str(doc.get("subject_id", "")),
                    "admission_type": doc.get("admission_type"),
                    "admission_location": doc.get("admission_location"),
                    "admittime": _iso(doc.get("admittime")) if doc.get("admittime") else None,
                    "dischtime": _iso(doc.get("dischtime")) if doc.get("dischtime") else None,
                })
        except Exception as exc:
            logger.debug("patient search mongo failed: %s", exc)

    # Demo fallback so the palette renders something even without Mongo
    if not results:
        for i in range(min(limit, 5)):
            results.append({
                "hadm_id": f"{q}{i:02d}",
                "subject_id": f"demo-{1000 + i}",
                "admission_type": "EMERGENCY",
                "admission_location": "EMERGENCY ROOM",
                "admittime": datetime.now(timezone.utc).isoformat(),
                "dischtime": None,
            })

    return {"query": q, "count": len(results), "results": results}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8222)
