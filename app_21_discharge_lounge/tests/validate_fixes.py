"""End-to-end validation of Discharge Lounge fixes.

Asserts (against the live backend on :8221, bed_management on :8208,
and Mongo-backed event bus):

  V1  /transfer is idempotent on hadm_id
      - First call creates record, subsequent call returns duplicate=True
      - Occupant count grows by 1 (not 2) and arrived_at is preserved
      - initiated_by metadata is persisted
  V2  /transfer triggers bed_management /notify-transfer
      - Seed a ward bed as occupied for the test hadm_id
      - After /transfer, that bed flips to available
  V3  /complete publishes patient_discharged AND bed_released
      - Record presence in MongoDB.MIMIC_SIM.event_log
  V4  discharge_predicted with readiness >= threshold auto-transfers
      - Publish event directly to Mongo event_log (the in-proc handler
        also fires but the Kafka subscriber path covers the cross-process case)
      - Poll /status for the hadm_id to appear
  V5  discharge_predicted with readiness < threshold does NOT auto-transfer
  V6  lounge_full is signalled with capacity + occupied in the error data
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

LOUNGE = "http://127.0.0.1:8221"
BEDS = "http://127.0.0.1:8208"

PASSED: List[str] = []
FAILED: List[Tuple[str, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED.append((name, detail))  # type: ignore[func-returns-value]
         or PASSED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _p(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        PASSED.append(name)
        print(f"  PASS  {name}  {detail}")
    else:
        FAILED.append((name, detail))
        print(f"  FAIL  {name}  {detail}")


async def main() -> int:
    async with httpx.AsyncClient(timeout=10) as client:
        await v_reset(client)
        await v1_idempotent(client)
        await v2_bed_release(client)
        await v3_complete_publishes(client)
        await v4_auto_transfer_fires(client)
        await v5_below_threshold_ignored(client)
        await v_reset(client)  # clear V4 auto-transferred occupant before V6
        await v6_full_capacity_signal(client)
        await v_reset(client)
        await v7_auto_expiry(client)
        await v8_complete_idempotent(client)

    print(f"\n{'=' * 64}")
    print(f"PASSED: {len(PASSED)}   FAILED: {len(FAILED)}")
    if FAILED:
        for n, d in FAILED:
            print(f"  - {n}: {d}")
    print("=" * 64)
    return 1 if FAILED else 0


async def v_reset(client: httpx.AsyncClient) -> None:
    print("\n[V0] Reset lounge")
    r = await client.post(f"{LOUNGE}/reset")
    _p("reset 200", r.status_code == 200)


async def v1_idempotent(client: httpx.AsyncClient) -> None:
    print("\n[V1] /transfer is idempotent")
    hadm = f"TEST-{uuid.uuid4().hex[:8]}"
    r1 = await client.post(
        f"{LOUNGE}/discharge-lounge/transfer",
        json={"hadm_id": hadm, "source_department": "Medicine", "subject_id": 9999},
    )
    _p("first transfer 200", r1.status_code == 200)
    body1 = r1.json().get("data", {})
    _p("first arrived_at present", bool(body1.get("arrived_at")), body1)
    _p("initiated_by == manual", body1.get("initiated_by") == "manual")
    first_arrived = body1.get("arrived_at")

    await asyncio.sleep(0.3)
    r2 = await client.post(
        f"{LOUNGE}/discharge-lounge/transfer",
        json={"hadm_id": hadm, "source_department": "Surgery"},
    )
    body2 = r2.json().get("data", {})
    _p("duplicate transfer returns duplicate=True", body2.get("duplicate") is True, body2)
    _p("arrived_at preserved across duplicates",
       body2.get("arrived_at") == first_arrived,
       f"first={first_arrived} second={body2.get('arrived_at')}")
    _p("source_department preserved (not overwritten)",
       body2.get("source_department") == "Medicine",
       body2)

    status = (await client.get(f"{LOUNGE}/discharge-lounge/status")).json()["data"]
    count = sum(1 for p in status["patients"] if p["hadm_id"] == hadm)
    _p("occupant is present exactly once", count == 1, f"count={count}")


async def v2_bed_release(client: httpx.AsyncClient) -> None:
    print("\n[V2] /transfer releases the ward bed")
    # Seed a ward bed as occupied for a known hadm_id
    hadm = 21010101
    beds_res = await client.get(f"{BEDS}/beds?department=Medicine")
    if beds_res.status_code != 200:
        _p("bed_management reachable", False, f"status={beds_res.status_code}")
        return
    beds = beds_res.json().get("data", [])
    target = next((b for b in beds if b.get("status") == "available"), None)
    if not target:
        _p("found an available Medicine bed", False, "none available")
        return
    bed_id = target["bed_id"]

    # Seed the bed as occupied for our test hadm
    seed = await client.post(f"{BEDS}/beds/{bed_id}/update",
                             json={"status": "occupied", "hadm_id": hadm, "patient_id": 123})
    _p("seeded ward bed occupied", seed.status_code == 200, f"bed={bed_id}")

    # Now transfer into lounge (should flip the ward bed back to available)
    tr = await client.post(
        f"{LOUNGE}/discharge-lounge/transfer",
        json={"hadm_id": str(hadm), "source_department": "Medicine", "subject_id": 123},
    )
    _p("lounge transfer 200", tr.status_code == 200)
    await asyncio.sleep(0.4)

    # Query the same bed — it should have been released
    beds_after = (await client.get(f"{BEDS}/beds?department=Medicine")).json()["data"]
    freed = next((b for b in beds_after if b["bed_id"] == bed_id), None)
    _p("ward bed flipped to available",
       bool(freed) and freed["status"] == "available",
       f"bed={bed_id} status_now={freed.get('status') if freed else '?'}")
    _p("ward bed hadm cleared",
       bool(freed) and not freed.get("hadm_id"),
       f"hadm_now={freed.get('hadm_id') if freed else '?'}")


async def v3_complete_publishes(client: httpx.AsyncClient) -> None:
    print("\n[V3] /complete publishes patient_discharged")
    try:
        from pymongo import MongoClient
    except Exception:
        _p("pymongo importable", False)
        return
    mc = MongoClient("mongodb://localhost:27017/")
    log = mc["MIMIC_SIM"]["event_log"]

    hadm = f"TEST-complete-{uuid.uuid4().hex[:8]}"
    await client.post(
        f"{LOUNGE}/discharge-lounge/transfer",
        json={"hadm_id": hadm, "source_department": "Medicine", "subject_id": 7777},
    )
    t0 = datetime.now(timezone.utc)
    comp = await client.post(
        f"{LOUNGE}/discharge-lounge/complete",
        json={"hadm_id": hadm},
    )
    _p("complete 200", comp.status_code == 200)

    # Give the broker a moment to persist
    await asyncio.sleep(0.6)

    cursor = list(log.find({
        "source_module": "discharge_lounge",
        "timestamp": {"$gte": t0},
        "payload.hadm_id": hadm,
    }))
    topics = {d.get("topic") for d in cursor}
    _p("bed_released published", "bed_released" in topics, f"topics={topics}")
    _p("patient_discharged published", "patient_discharged" in topics, f"topics={topics}")


async def v4_auto_transfer_fires(client: httpx.AsyncClient) -> None:
    print("\n[V4] discharge_predicted (readiness=0.9) auto-transfers")
    try:
        from pymongo import MongoClient
    except Exception:
        _p("pymongo importable", False)
        return
    mc = MongoClient("mongodb://localhost:27017/")
    log = mc["MIMIC_SIM"]["event_log"]

    # Use an in-process publish via HTTP to a side-effect-free endpoint would
    # be cleaner, but we can seed the event_log directly — the Kafka
    # consumer replays from it via the ring buffer.
    hadm = f"TEST-auto-{uuid.uuid4().hex[:8]}"
    marker = log.insert_one({
        "event_id": str(uuid.uuid4()),
        "topic": "discharge_predicted",
        "source_module": "bed_management",
        "timestamp": datetime.now(timezone.utc),
        "payload": {
            "hadm_id": hadm,
            "subject_id": 8888,
            "readiness_score": 0.9,
            "source_department": "Medicine",
        },
    })
    _p("seeded discharge_predicted event", bool(marker.inserted_id))

    # Give the kafka ring-buffer consumer up to 10s to pick it up
    appeared = False
    for _ in range(20):
        status = (await client.get(f"{LOUNGE}/discharge-lounge/status")).json()["data"]
        hit = next((p for p in status["patients"] if p["hadm_id"] == hadm), None)
        if hit:
            appeared = True
            _p("auto-transferred occupant present",
               hit.get("initiated_by", "").startswith("auto:"),
               f"initiated_by={hit.get('initiated_by')}")
            break
        await asyncio.sleep(0.5)
    _p("auto-transfer occurred within 10s", appeared,
       f"hadm={hadm} (not found — Kafka consumer may be disabled)")


async def v5_below_threshold_ignored(client: httpx.AsyncClient) -> None:
    print("\n[V5] discharge_predicted (readiness=0.6) ignored (below threshold)")
    try:
        from pymongo import MongoClient
    except Exception:
        return
    mc = MongoClient("mongodb://localhost:27017/")
    log = mc["MIMIC_SIM"]["event_log"]
    hadm = f"TEST-low-{uuid.uuid4().hex[:8]}"
    log.insert_one({
        "event_id": str(uuid.uuid4()),
        "topic": "discharge_predicted",
        "source_module": "bed_management",
        "timestamp": datetime.now(timezone.utc),
        "payload": {"hadm_id": hadm, "readiness_score": 0.6, "source_department": "Medicine"},
    })
    await asyncio.sleep(3.0)
    status = (await client.get(f"{LOUNGE}/discharge-lounge/status")).json()["data"]
    hit = next((p for p in status["patients"] if p["hadm_id"] == hadm), None)
    _p("low-readiness event did NOT auto-transfer", hit is None,
       f"hit={hit}")


async def v7_auto_expiry(client: httpx.AsyncClient) -> None:
    print("\n[V7] Auto-expiry discharges occupants past expected_departure_h")
    try:
        from pymongo import MongoClient
    except Exception:
        _p("pymongo importable", False)
        return
    mc = MongoClient("mongodb://localhost:27017/")
    log = mc["MIMIC_SIM"]["event_log"]

    hadm = f"TEST-exp-{uuid.uuid4().hex[:8]}"
    # expected_departure_h=0 → immediately past the threshold on arrival.
    # Send a tiny positive value so the arrival record persists for at least
    # one poll cycle before the watcher expires it, making the window observable.
    r = await client.post(
        f"{LOUNGE}/discharge-lounge/transfer",
        json={
            "hadm_id": hadm,
            "subject_id": 42,
            "source_department": "Medicine",
            "expected_departure_h": 0.0005,  # ~1.8s
        },
    )
    _p("seeded expiring occupant", r.status_code == 200, f"hadm={hadm}")

    # Poll for up to 12s — the watcher runs every 5s by default
    deadline = time.time() + 12.0
    t0 = datetime.now(timezone.utc)
    cleared = False
    while time.time() < deadline:
        status = (await client.get(f"{LOUNGE}/discharge-lounge/status")).json()["data"]
        hit = next((p for p in status["patients"] if p["hadm_id"] == hadm), None)
        if hit is None:
            cleared = True
            break
        await asyncio.sleep(0.5)
    _p("occupant removed within 12s after expected-hours elapsed", cleared,
       f"hadm={hadm}")

    # And the event_log should carry patient_discharged with completed_via=auto:expiry
    await asyncio.sleep(0.5)
    events = list(log.find({
        "source_module": "discharge_lounge",
        "timestamp": {"$gte": t0},
        "payload.hadm_id": hadm,
        "topic": "patient_discharged",
    }))
    if events:
        via = events[-1].get("payload", {}).get("completed_via")
        _p("patient_discharged completed_via=auto:expiry",
           via == "auto:expiry", f"completed_via={via}")
    else:
        _p("patient_discharged published for expired occupant", False,
           "no event found")


async def v8_complete_idempotent(client: httpx.AsyncClient) -> None:
    print("\n[V8] /complete is idempotent (no double-publish after race)")
    hadm = f"TEST-idem-{uuid.uuid4().hex[:8]}"
    await client.post(
        f"{LOUNGE}/discharge-lounge/transfer",
        json={"hadm_id": hadm, "source_department": "Medicine", "subject_id": 1},
    )
    r1 = await client.post(f"{LOUNGE}/discharge-lounge/complete", json={"hadm_id": hadm})
    _p("first complete ok", r1.status_code == 200)
    body1 = r1.json().get("data", {})
    _p("first complete marked completed=True", body1.get("completed") is True, body1)

    r2 = await client.post(f"{LOUNGE}/discharge-lounge/complete", json={"hadm_id": hadm})
    body2 = r2.json().get("data", {})
    _p("second complete returns already_discharged, no error",
       r2.json().get("status") == "ok" and body2.get("reason") == "already_discharged",
       body2)


async def v6_full_capacity_signal(client: httpx.AsyncClient) -> None:
    print("\n[V6] lounge_full signal includes capacity + occupied")
    # Fill to capacity
    status = (await client.get(f"{LOUNGE}/discharge-lounge/status")).json()["data"]
    cap = status["capacity"]
    occ = status["occupied"]
    # Add enough to reach capacity
    to_add = cap - occ
    for i in range(to_add):
        await client.post(
            f"{LOUNGE}/discharge-lounge/transfer",
            json={"hadm_id": f"FILL-{uuid.uuid4().hex[:6]}", "source_department": "Medicine"},
        )
    # One more → should get lounge_full
    r = await client.post(
        f"{LOUNGE}/discharge-lounge/transfer",
        json={"hadm_id": f"OVER-{uuid.uuid4().hex[:6]}", "source_department": "Medicine"},
    )
    body = r.json()
    _p("extra transfer rejected with lounge_full",
       body.get("status") == "error" and body.get("error") == "lounge_full",
       f"body={body}")
    data = body.get("data") or {}
    _p("error body includes capacity + occupied",
       data.get("capacity") == cap and data.get("occupied") == cap,
       f"data={data}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
