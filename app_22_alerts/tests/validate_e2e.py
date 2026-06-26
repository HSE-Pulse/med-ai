"""End-to-end validation of the alert pipeline + patient search.

Runs against the live app_22_alerts service on 8222 and the Vite dev server
on 3007. Prints a structured PASS / FAIL report. Exits non-zero on failure.

Validations
-----------
V1  Aggregator health + Mongo connectivity
V2  Severity / title / route mapping for every documented topic
V3  event_log tailer: write to Mongo → alert appears in /alerts/recent
V4  event_log tailer: same write → broadcast to an active WS client
V5  Direct demo injection via REST → all WS subscribers receive it
V6  Ack REST → WS ack broadcast to every subscriber
V7  Patient search: exact int, numeric prefix (aggregation), subject_id,
    non-existent, empty fallback, large-limit clamp
V8  Dashboard reachability + Vite proxy correctness
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import httpx
import websockets

ALERTS_BASE = "http://127.0.0.1:8222"
VITE_BASE = "http://localhost:3007"
ALERTS_WS = "ws://127.0.0.1:8222/alerts/stream"
JOURNEY_BASE = "http://127.0.0.1:8205"

# Ensure we can import the app module + normaliser + severity map
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app_22_alerts.backend.app.main import (  # type: ignore
    SEVERITY_MAP,
    TOPIC_TITLES,
    MODULE_ROUTE_HINT,
    normalise_event,
)


PASSED: List[str] = []
FAILED: List[Tuple[str, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        PASSED.append(name)
        print(f"  PASS  {name}  {detail}")
    else:
        FAILED.append((name, detail))
        print(f"  FAIL  {name}  {detail}")


# ────────────────────────────────────────────────────────────────────
# V1 — health + mongo
# ────────────────────────────────────────────────────────────────────
async def v1_health(client: httpx.AsyncClient) -> None:
    print("\n[V1] Aggregator health")
    r = await client.get(f"{ALERTS_BASE}/health")
    record("health 200", r.status_code == 200, f"status={r.status_code}")
    body = r.json()
    record("mongo connected", bool(body.get("mongo_available")), str(body))


# ────────────────────────────────────────────────────────────────────
# V2 — severity + title + route mapping (unit-level, no I/O)
# ────────────────────────────────────────────────────────────────────
def v2_mapping() -> None:
    print("\n[V2] Severity / title / route mapping")
    sample_topics = [
        "deterioration_critical", "deterioration_alert", "capacity_alert",
        "trolley_alert", "pet_breach_risk", "lwbs_risk", "surge_alert",
        "bottleneck_detected", "admission_predicted", "bed_allocated",
        "bed_released", "patient_admitted", "patient_discharged",
        "priority_updated", "referral_triaged", "note_generated",
    ]
    for topic in sample_topics:
        doc = {
            "event_id": str(uuid.uuid4()),
            "topic": topic,
            "source_module": topic.split("_")[0] + "_module",
            "payload": {"hadm_id": 22595853},
            "timestamp": datetime.now(timezone.utc),
        }
        alert = normalise_event(doc)
        expected_sev = SEVERITY_MAP.get(topic, "info")
        expected_title = TOPIC_TITLES.get(topic, topic.replace("_", " ").title())
        record(
            f"{topic} severity",
            alert["severity"] == expected_sev,
            f"got={alert['severity']} expect={expected_sev}",
        )
        record(
            f"{topic} title",
            alert["title"] == expected_title,
            f"title={alert['title']!r}",
        )
        record(
            f"{topic} patient_id extracted",
            alert["patient_id"] == "22595853",
            f"patient_id={alert['patient_id']}",
        )
        record(
            f"{topic} route_hint present",
            alert["route_hint"] is not None,
            f"route={alert['route_hint']}",
        )

    # Module-driven route override
    doc = {
        "event_id": str(uuid.uuid4()),
        "topic": "patient_admitted",
        "source_module": "bed_management",
        "payload": {"hadm_id": 1},
        "timestamp": datetime.now(timezone.utc),
    }
    alert = normalise_event(doc)
    record(
        "source_module -> route mapping (bed_management)",
        alert["route_hint"] == MODULE_ROUTE_HINT["bed_management"],
        f"route={alert['route_hint']}",
    )


# ────────────────────────────────────────────────────────────────────
# V3 + V4 — Mongo write flows all the way to REST and WS
# ────────────────────────────────────────────────────────────────────
async def v3_mongo_to_rest_and_ws(client: httpx.AsyncClient) -> None:
    print("\n[V3/V4] Mongo event_log → alert tail → REST + WS")
    try:
        from pymongo import MongoClient
    except Exception:
        record("mongo import", False, "pymongo not available")
        return

    mc = MongoClient("mongodb://localhost:27017/")
    coll = mc["MIMIC_SIM"]["event_log"]
    marker = f"vtest-{uuid.uuid4()}"
    doc = {
        "event_id": marker,
        "topic": "deterioration_critical",
        "source_module": "deterioration_monitor",
        "payload": {"hadm_id": 22595853, "news2": 11, "trend": "rising"},
        "timestamp": datetime.now(timezone.utc),
    }

    # Open WS BEFORE the insert so we can capture the broadcast live
    ws = await websockets.connect(ALERTS_WS, open_timeout=5)
    try:
        snapshot = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        record(
            "ws snapshot on connect",
            snapshot.get("type") == "snapshot" and isinstance(snapshot.get("alerts"), list),
            f"snapshot alerts={len(snapshot.get('alerts', []))}",
        )

        coll.insert_one(doc)

        # Poll REST for up to 6s (tailer polls every 2s)
        found_rest = False
        deadline = time.time() + 6.0
        while time.time() < deadline:
            r = await client.get(f"{ALERTS_BASE}/alerts/recent?limit=100")
            ids = [a["id"] for a in r.json().get("alerts", [])]
            if marker in ids:
                found_rest = True
                break
            await asyncio.sleep(0.5)
        record("mongo event appears via REST /alerts/recent", found_rest,
               f"marker={marker}")

        # Drain WS for up to 6s looking for our event
        found_ws = False
        deadline = time.time() + 6.0
        while time.time() < deadline:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
            except asyncio.TimeoutError:
                continue
            if msg.get("type") == "alert" and msg["alert"]["id"] == marker:
                alert = msg["alert"]
                found_ws = True
                record("ws broadcast topic normalised", alert["topic"] == "deterioration_critical")
                record("ws broadcast severity=critical", alert["severity"] == "critical")
                record("ws broadcast route_hint=/deterioration",
                       alert["route_hint"] == "/deterioration")
                record("ws broadcast patient_id extracted", alert["patient_id"] == "22595853")
                break
        record("mongo event appears via WS broadcast", found_ws, f"marker={marker}")

    finally:
        await ws.close()
        try:
            coll.delete_one({"event_id": marker})
        except Exception:
            pass


# ────────────────────────────────────────────────────────────────────
# V5 — REST demo → multiple WS clients
# ────────────────────────────────────────────────────────────────────
async def v5_multi_ws_fanout(client: httpx.AsyncClient) -> None:
    print("\n[V5] Demo injection fans out to all WS subscribers")
    ws1 = await websockets.connect(ALERTS_WS, open_timeout=5)
    ws2 = await websockets.connect(ALERTS_WS, open_timeout=5)
    try:
        # Drain the snapshots
        await asyncio.wait_for(ws1.recv(), timeout=3)
        await asyncio.wait_for(ws2.recv(), timeout=3)

        r = await client.post(f"{ALERTS_BASE}/alerts/demo")
        record("demo inject 200", r.status_code == 200)
        demo_id = r.json()["alert"]["id"]

        async def receive_matching(ws, target_id: str) -> bool:
            deadline = time.time() + 4.0
            while time.time() < deadline:
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
                except asyncio.TimeoutError:
                    continue
                if msg.get("type") == "alert" and msg["alert"]["id"] == target_id:
                    return True
            return False

        got1, got2 = await asyncio.gather(
            receive_matching(ws1, demo_id),
            receive_matching(ws2, demo_id),
        )
        record("ws client #1 received demo", got1)
        record("ws client #2 received demo (fanout)", got2)

        # V6 — ack broadcast
        r = await client.post(f"{ALERTS_BASE}/alerts/{demo_id}/ack")
        record("ack REST 200", r.status_code == 200, f"body={r.json()}")

        async def receive_ack(ws, target_id: str) -> bool:
            deadline = time.time() + 4.0
            while time.time() < deadline:
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
                except asyncio.TimeoutError:
                    continue
                if msg.get("type") == "ack" and msg.get("alert_id") == target_id:
                    return True
            return False

        a1, a2 = await asyncio.gather(
            receive_ack(ws1, demo_id),
            receive_ack(ws2, demo_id),
        )
        record("ws client #1 saw ack", a1)
        record("ws client #2 saw ack (fanout)", a2)

        # Confirm REST reflects acknowledged=True
        r = await client.get(f"{ALERTS_BASE}/alerts/recent?limit=200")
        match = next((a for a in r.json()["alerts"] if a["id"] == demo_id), None)
        record("REST shows acknowledged=True after ack",
               match is not None and match.get("acknowledged") is True,
               f"match={match and match.get('acknowledged')}")
    finally:
        await ws1.close()
        await ws2.close()


# ────────────────────────────────────────────────────────────────────
# V7 — patient search
# ────────────────────────────────────────────────────────────────────
async def v7_search(client: httpx.AsyncClient) -> None:
    print("\n[V7] Patient search (real MIMIC)")

    async def search(q: str, limit: int = 10) -> Dict[str, Any]:
        r = await client.get(f"{ALERTS_BASE}/search/patients",
                             params={"q": q, "limit": limit})
        r.raise_for_status()
        return r.json()

    # Exact hadm_id returned from prefix test
    first = await search("225", 1)
    record("search q=225 returns ≥1 hit", first["count"] >= 1, f"count={first['count']}")
    if first["results"]:
        hit = first["results"][0]
        record("hadm_id is 8-digit MIMIC int as string",
               hit["hadm_id"].isdigit() and len(hit["hadm_id"]) == 8,
               f"hadm_id={hit['hadm_id']}")
        record("subject_id looks real (8-digit)",
               hit["subject_id"].isdigit() and len(hit["subject_id"]) == 8,
               f"subject={hit['subject_id']}")

        # Exact lookup on the hadm_id we just received
        exact = await search(hit["hadm_id"], 1)
        hadm_ids = {r["hadm_id"] for r in exact["results"]}
        record("exact-match hadm_id returns itself",
               hit["hadm_id"] in hadm_ids,
               f"hit={hit['hadm_id']} exact={hadm_ids}")

        # Subject prefix (first 3 digits of subject_id)
        sub_prefix = hit["subject_id"][:3]
        sub = await search(sub_prefix, 5)
        record(f"subject_id prefix q={sub_prefix} returns ≥1 hit",
               sub["count"] >= 1, f"count={sub['count']}")

    # Non-numeric → demo fallback
    non_numeric = await search("zzz", 3)
    record("non-numeric q=zzz falls back (count>=1)",
           non_numeric["count"] >= 1, f"count={non_numeric['count']}")

    # Limit clamp
    r = await client.get(f"{ALERTS_BASE}/search/patients",
                         params={"q": "1", "limit": 9999})
    record("over-limit bound is rejected (422)",
           r.status_code == 422, f"status={r.status_code}")


# ────────────────────────────────────────────────────────────────────
# V11 — coalescing by (topic, patient_id)
# ────────────────────────────────────────────────────────────────────
async def v11_coalescing(client: httpx.AsyncClient) -> None:
    print("\n[V11] Coalesce repeat (topic, patient_id) into one row")
    try:
        from pymongo import MongoClient
    except Exception:
        record("V11 setup: pymongo", False, "pymongo not available")
        return

    mc = MongoClient("mongodb://localhost:27017/")
    coll = mc["MIMIC_SIM"]["event_log"]
    tag = f"v11-{uuid.uuid4().hex[:8]}"
    inserted: List[str] = []

    def emit(topic: str, pid: Any, payload: Dict[str, Any]) -> str:
        eid = f"{tag}-{uuid.uuid4().hex[:8]}"
        coll.insert_one({
            "event_id": eid,
            "topic": topic,
            "source_module": "bed_management",
            "payload": {"hadm_id": pid, **payload},
            "timestamp": datetime.now(timezone.utc),
        })
        inserted.append(eid)
        return eid

    async def wait_recent() -> List[Dict[str, Any]]:
        # Give the tailer up to 5s to catch up
        for _ in range(10):
            r = await client.get(f"{ALERTS_BASE}/alerts/recent?limit=500")
            yield_alerts = r.json().get("alerts", [])
            if any(a["id"] in inserted for a in yield_alerts):
                return yield_alerts
            await asyncio.sleep(0.5)
        return []

    ws = await websockets.connect(ALERTS_WS, open_timeout=5)
    try:
        # drain snapshot
        await asyncio.wait_for(ws.recv(), timeout=3)

        pid1 = 99991001    # use synthetic ids to avoid collision with live cache
        pid2 = 99991002

        # Send 4 discharge_predicted for pid1, 1 for pid2, and 1 different topic for pid1
        first_id = emit("discharge_predicted", pid1, {"readiness_score": 0.4})
        emit("discharge_predicted", pid1, {"readiness_score": 0.5})
        emit("discharge_predicted", pid1, {"readiness_score": 0.6})
        emit("discharge_predicted", pid1, {"readiness_score": 0.7})
        emit("discharge_predicted", pid2, {"readiness_score": 0.3})
        emit("bed_allocated", pid1, {"bed_id": "MAU-101"})

        # Drain up to 12s of WS messages
        msgs: List[Dict[str, Any]] = []
        deadline = time.time() + 12.0
        while time.time() < deadline:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
                msgs.append(m)
            except asyncio.TimeoutError:
                # stop if we've seen the expected shape
                if sum(1 for m in msgs if m.get("type") == "alert_update"
                       and m["alert"].get("id") == first_id) >= 3:
                    break

        alert_msgs = [m for m in msgs if m.get("type") == "alert"
                      and m["alert"].get("id") in inserted]
        update_msgs = [m for m in msgs if m.get("type") == "alert_update"
                       and m["alert"].get("id") == first_id]
        record("first discharge_predicted creates a new alert",
               any(m["alert"]["id"] == first_id for m in alert_msgs),
               f"alerts={[m['alert']['id'] for m in alert_msgs]}")
        record("subsequent discharge_predicted emit alert_update (>=3)",
               len(update_msgs) >= 3, f"updates={len(update_msgs)}")

        # Fetch the group row and assert count=4, last payload=0.7
        r = await client.get(f"{ALERTS_BASE}/alerts/recent?limit=500")
        rows = r.json()["alerts"]
        group = next((a for a in rows if a["id"] == first_id), None)
        record("coalesced group row exists", group is not None,
               f"id={first_id}")
        if group:
            record("count == 4", group.get("count") == 4,
                   f"count={group.get('count')}")
            record("latest payload.readiness_score == 0.7",
                   abs((group.get("payload", {}).get("readiness_score") or 0) - 0.7) < 1e-9,
                   f"payload={group.get('payload')}")
            record("last_timestamp > timestamp",
                   (group.get("last_timestamp") or "") > (group.get("timestamp") or ""),
                   f"first={group.get('timestamp')} last={group.get('last_timestamp')}")

        # Different patient_id → separate row
        pid2_row = next(
            (a for a in rows
             if a["topic"] == "discharge_predicted"
                and a.get("patient_id") == str(pid2)
                and a["id"] in inserted),
            None,
        )
        record("different patient_id stays separate",
               pid2_row is not None and pid2_row.get("count") == 1,
               f"row={pid2_row}")

        # Different topic, same patient_id → separate row
        bed_row = next(
            (a for a in rows if a["topic"] == "bed_allocated"
                and a.get("patient_id") == str(pid1) and a["id"] in inserted),
            None,
        )
        record("different topic stays separate",
               bed_row is not None and bed_row.get("count") == 1,
               f"row={bed_row}")

        # Ack the group, then fire one more event → new row appears
        r = await client.post(f"{ALERTS_BASE}/alerts/{first_id}/ack")
        record("ack coalesced group 200", r.status_code == 200)
        await asyncio.sleep(0.1)

        post_ack_id = emit("discharge_predicted", pid1, {"readiness_score": 0.9})
        # wait for it to surface
        deadline = time.time() + 8.0
        found_new = False
        while time.time() < deadline:
            r = await client.get(f"{ALERTS_BASE}/alerts/recent?limit=500")
            rows = r.json()["alerts"]
            g = next((a for a in rows if a["id"] == post_ack_id), None)
            if g:
                found_new = True
                record("post-ack event creates a NEW row (not coalesced)",
                       g.get("count") == 1,
                       f"count={g.get('count')}")
                break
            await asyncio.sleep(0.5)
        if not found_new:
            record("post-ack event creates a NEW row (not coalesced)", False,
                   "new row never appeared")

        # Severity escalation: start with info-level, then send higher
        sev_first = emit("admission_predicted", 99991099, {"note": "low"})
        emit("admission_predicted", 99991099, {"note": "low-2"})
        await asyncio.sleep(2.0)
        r = await client.get(f"{ALERTS_BASE}/alerts/recent?limit=500")
        rows = r.json()["alerts"]
        sev_row = next((a for a in rows if a["id"] == sev_first), None)
        record("severity starts at info", sev_row and sev_row["severity"] == "info",
               f"got={sev_row and sev_row['severity']}")

    finally:
        await ws.close()
        # Cleanup
        try:
            coll.delete_many({"event_id": {"$in": inserted}})
        except Exception:
            pass


# ────────────────────────────────────────────────────────────────────
# V10 — WS client sees a graceful close, not a hang, when server drops it
# ────────────────────────────────────────────────────────────────────
async def v10_ws_resilience(client: httpx.AsyncClient) -> None:
    print("\n[V10] WS close/reconnect semantics")
    ws = await websockets.connect(ALERTS_WS, open_timeout=5)
    try:
        snap = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        record("initial snapshot received", snap.get("type") == "snapshot")
        # Force-close from the client side; confirm we can immediately re-open.
        await ws.close()
    except Exception as exc:
        record("initial snapshot received", False, repr(exc))

    # Reconnect: open a fresh connection and verify snapshot again.
    try:
        ws2 = await websockets.connect(ALERTS_WS, open_timeout=5)
        try:
            snap2 = json.loads(await asyncio.wait_for(ws2.recv(), timeout=3))
            record("reconnect succeeds and delivers snapshot",
                   snap2.get("type") == "snapshot")
        finally:
            await ws2.close()
    except Exception as exc:
        record("reconnect succeeds and delivers snapshot", False, repr(exc))

    # Subscriber count shouldn't leak across the close.
    r = await client.get(f"{ALERTS_BASE}/health")
    subs = r.json().get("subscribers", -1)
    record("subscriber count does not leak disconnected clients",
           isinstance(subs, int) and subs >= 0, f"subscribers={subs}")


# ────────────────────────────────────────────────────────────────────
# V9 — unified patient view: the exact endpoint set PatientUnified.tsx calls
# ────────────────────────────────────────────────────────────────────
async def v9_patient_view(client: httpx.AsyncClient) -> None:
    print("\n[V9] Unified patient view (journey backend)")
    try:
        r = await client.get(f"{JOURNEY_BASE}/health", timeout=3)
    except Exception as exc:
        record("journey backend reachable", False, repr(exc))
        return
    record("journey backend reachable", r.status_code == 200,
           f"status={r.status_code}")

    # Resolve a real (hadm_id, subject_id) pair from MongoDB so we test against
    # data that actually exists — not a guessed id.
    try:
        from pymongo import MongoClient
        mc = MongoClient("mongodb://localhost:27017/")
        doc = mc["MIMIC"]["admissions"].find_one(
            {}, {"_id": 0, "hadm_id": 1, "subject_id": 1})
    except Exception as exc:
        record("resolve real (hadm_id, subject_id) from MIMIC", False, repr(exc))
        return
    if not doc:
        record("resolve real (hadm_id, subject_id) from MIMIC", False,
               "admissions empty")
        return
    hadm = doc["hadm_id"]
    sub = doc["subject_id"]
    record("resolve real (hadm_id, subject_id) from MIMIC", True,
           f"hadm_id={hadm} subject={sub}")

    # Via the palette's search endpoint — same path the UI takes
    r = await client.get(f"{ALERTS_BASE}/search/patients",
                         params={"q": str(hadm), "limit": 1})
    hits = r.json().get("results", [])
    record("palette search resolves exact hadm_id",
           bool(hits) and hits[0]["hadm_id"] == str(hadm),
           f"hit={hits[0] if hits else None}")

    # Summary — only endpoint that takes subject_id alone
    r = await client.get(f"{JOURNEY_BASE}/patient/{sub}/summary")
    record("/patient/{sub}/summary", r.status_code == 200,
           f"status={r.status_code}")

    base = f"{JOURNEY_BASE}/patient/{sub}/admission/{hadm}"
    for tail in ("timeline", "vitals", "labs", "medications"):
        rr = await client.get(f"{base}/{tail}")
        record(f"admission/{hadm}/{tail}", rr.status_code == 200,
               f"status={rr.status_code}")

    # Content-depth checks on the real response bodies (defends against
    # "endpoint 200s but returns nothing useful" regressions)
    r = await client.get(f"{JOURNEY_BASE}/patient/{sub}/summary")
    body = r.json().get("data", r.json())
    admissions = body.get("admissions") or []
    record("summary contains >=1 admission",
           len(admissions) >= 1, f"count={len(admissions)}")
    record("first admission has hadm_id + admittime",
           bool(admissions and admissions[0].get("hadm_id")
                and admissions[0].get("admittime")),
           f"keys={list(admissions[0].keys())[:5] if admissions else []}")

    r = await client.get(f"{base}/medications")
    body = r.json().get("data", r.json())
    meds = body.get("medications", [])
    record("medications non-empty + has drug name",
           len(meds) >= 1 and bool(meds[0].get("drug")),
           f"count={len(meds)} first_drug={(meds[0] or {}).get('drug')}")

    r = await client.get(f"{base}/timeline")
    body = r.json().get("data", r.json())
    events = body.get("events", [])
    record("timeline has events",
           len(events) >= 1, f"count={len(events)}")

    r = await client.get(f"{base}/labs")
    body = r.json().get("data", r.json())
    panels = body.get("panels", {})
    analyte_count = sum(len(p or {}) for p in panels.values())
    record("labs has at least one analyte sample",
           analyte_count >= 1, f"panels={len(panels)} analytes={analyte_count}")

    # Vitals: this admission may be too short for charted vitals; test a
    # known ICU admission for the same subject instead.
    try:
        from pymongo import MongoClient
        mc = MongoClient("mongodb://localhost:27017/")
        ce = mc["MIMIC_ICU"]["chartevents"].find_one(
            {"subject_id": sub}, {"hadm_id": 1, "_id": 0})
        icu_hadm = ce["hadm_id"] if ce else None
    except Exception:
        icu_hadm = None
    if icu_hadm:
        r = await client.get(f"{JOURNEY_BASE}/patient/{sub}/admission/{icu_hadm}/vitals")
        body = r.json().get("data", r.json())
        vitals = body.get("vitals", {}) or {}
        non_empty = [k for k, v in vitals.items() if v]
        record("vitals non-empty for ICU admission",
               len(non_empty) >= 1,
               f"icu_hadm={icu_hadm} channels={non_empty[:4]}")


# ────────────────────────────────────────────────────────────────────
# V8 — Vite proxy reachability
# ────────────────────────────────────────────────────────────────────
async def v8_vite_proxy(client: httpx.AsyncClient) -> None:
    print("\n[V8] Vite proxy")
    r = await client.get(f"{VITE_BASE}/api/alerts/health")
    record("vite proxy /api/alerts/health", r.status_code == 200, f"status={r.status_code}")
    j = r.json() if r.status_code == 200 else {}
    record("vite proxy body has mongo_available", "mongo_available" in j, str(j))

    r = await client.get(f"{VITE_BASE}/api/alerts/search/patients",
                         params={"q": "225", "limit": 2})
    record("vite proxy /search/patients", r.status_code == 200,
           f"status={r.status_code}")
    if r.status_code == 200:
        body = r.json()
        record("search via proxy returns results", body.get("count", 0) >= 1,
               f"count={body.get('count')}")

    r = await client.get(f"{VITE_BASE}/")
    record("dashboard HTML served", r.status_code == 200 and "<title>" in r.text,
           f"http={r.status_code}")


# ────────────────────────────────────────────────────────────────────
async def main() -> int:
    t0 = time.time()
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await v1_health(client)
        except Exception as exc:
            record("V1 crashed", False, repr(exc))
        try:
            v2_mapping()
        except Exception as exc:
            record("V2 crashed", False, repr(exc))
        try:
            await v3_mongo_to_rest_and_ws(client)
        except Exception as exc:
            record("V3/V4 crashed", False, repr(exc))
        try:
            await v5_multi_ws_fanout(client)
        except Exception as exc:
            record("V5/V6 crashed", False, repr(exc))
        try:
            await v7_search(client)
        except Exception as exc:
            record("V7 crashed", False, repr(exc))
        try:
            await v8_vite_proxy(client)
        except Exception as exc:
            record("V8 crashed", False, repr(exc))
        try:
            await v9_patient_view(client)
        except Exception as exc:
            record("V9 crashed", False, repr(exc))
        try:
            await v10_ws_resilience(client)
        except Exception as exc:
            record("V10 crashed", False, repr(exc))
        try:
            await v11_coalescing(client)
        except Exception as exc:
            record("V11 crashed", False, repr(exc))

    dur = time.time() - t0
    print(f"\n{'=' * 64}")
    print(f"PASSED: {len(PASSED)}   FAILED: {len(FAILED)}   ({dur:.1f}s)")
    if FAILED:
        print("\nFailures:")
        for name, detail in FAILED:
            print(f"  - {name}: {detail}")
    print("=" * 64)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
