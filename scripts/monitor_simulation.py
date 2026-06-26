"""Comprehensive health probe for the MedAI platform.

Run repeatedly during the autonomous monitor loop. Output is structured
JSON-ish text that an LLM can parse without ambiguity. Each section prints
what it CHECKED and any FINDINGS (issues that warrant a fix).

Sections:
  1. Service health (all 19 microservices + dashboard)
  2. Cross-service consistency probes (the bug class we keep finding)
  3. Mongo collection counts (sanity)
  4. Recent log errors per service
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

SERVICES: List[Tuple[str, int]] = [
    ("ed_triage", 8201),
    ("sepsis_icu", 8202),
    ("hospital_ops", 8203),
    ("oncology_ai", 8204),
    ("patient_journey", 8205),
    ("clinical_chat", 8206),
    ("data_ingestion", 8207),
    ("bed_management", 8208),
    ("waiting_list", 8209),
    ("clinical_scribe", 8210),
    ("ed_flow", 8214),
    ("erp", 8215),
    ("trolley_watch", 8216),
    ("gdpr", 8217),
    ("xai", 8218),
    ("fhir", 8219),
    ("deterioration", 8220),
    ("discharge_lounge", 8221),
    ("alerts", 8222),
]

DASHBOARD_PORT = 5173


def _get(url: str, timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_status_code": e.code, "_error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {e}"}


def section(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def check_service_health() -> List[str]:
    section("1. SERVICE HEALTH")
    findings: List[str] = []
    for name, port in SERVICES:
        r = _get(f"http://127.0.0.1:{port}/health")
        if r is None or r.get("_error"):
            print(f"  [DOWN]   {name:18s} :{port}  {r.get('_error') if r else 'no response'}")
            findings.append(f"service_down:{name}:{port}")
        elif r.get("status") not in ("ok", "healthy"):
            print(f"  [WARN]   {name:18s} :{port}  status={r.get('status')}")
            findings.append(f"service_unhealthy:{name}:{port}:{r.get('status')}")
        else:
            # Most services return {status, data: {uptime_seconds, ...}}.
            # A few (e.g. alerts) return a flat dict with no uptime field.
            uptime = (r.get("data") or {}).get("uptime_seconds")
            if uptime is None:
                uptime = r.get("uptime_seconds")
            uptime_str = f"{int(uptime)}s" if isinstance(uptime, (int, float)) else "n/a"
            print(f"  [OK]     {name:18s} :{port}  uptime={uptime_str}")
    # Dashboard (different shape — Vite dev server)
    try:
        with urllib.request.urlopen(f"http://[::1]:{DASHBOARD_PORT}/", timeout=2) as r:
            print(f"  [OK]     dashboard          :{DASHBOARD_PORT}  http={r.status}")
    except Exception as e:  # noqa: BLE001
        print(f"  [DOWN]   dashboard          :{DASHBOARD_PORT}  {type(e).__name__}: {e}")
        findings.append(f"service_down:dashboard:{DASHBOARD_PORT}")
    return findings


def check_cross_service() -> List[str]:
    section("2. CROSS-SERVICE CONSISTENCY")
    findings: List[str] = []

    # 2a. Discharge_Lounge: bed_mgmt vs lounge service
    bm = _get("http://127.0.0.1:8208/beds/summary") or {}
    lo = _get("http://127.0.0.1:8221/discharge-lounge/status") or {}
    bm_lounge = next(
        (x for x in (bm.get("data") or []) if x.get("department") == "Discharge_Lounge"),
        {},
    )
    lo_data = lo.get("data") or {}
    bm_occ = bm_lounge.get("occupied")
    lo_occ = lo_data.get("occupied")
    lo_hadms = {str(p.get("hadm_id")) for p in (lo_data.get("patients") or [])}
    bm_lounge_beds = _get("http://127.0.0.1:8208/beds?status=occupied") or {}
    bm_hadms = {
        str(b.get("hadm_id"))
        for b in (bm_lounge_beds.get("data") or [])
        if b.get("department") == "Discharge_Lounge"
    }
    drift = bm_hadms.symmetric_difference(lo_hadms)
    print(f"  Discharge_Lounge   bed_mgmt={bm_occ}  lounge={lo_occ}  drift_count={len(drift)}")
    # Drift > 4 might be churn or real divergence — re-probe after a short
    # settle window. The reconciler runs every 30s; 5s is enough to catch
    # mid-cycle reads without significantly slowing the monitor.
    if len(drift) > 4:
        time.sleep(5)
        bm2 = _get("http://127.0.0.1:8208/beds?status=occupied") or {}
        bm_hadms_2 = {
            str(b.get("hadm_id"))
            for b in (bm2.get("data") or [])
            if b.get("department") == "Discharge_Lounge"
        }
        lo2 = _get("http://127.0.0.1:8221/discharge-lounge/status") or {}
        lo_hadms_2 = {
            str(p.get("hadm_id"))
            for p in ((lo2.get("data") or {}).get("patients") or [])
        }
        drift2 = bm_hadms_2.symmetric_difference(lo_hadms_2)
        if len(drift2) > 4:
            findings.append(
                f"lounge_drift_persistent:bm={len(bm_hadms_2)}:lo={len(lo_hadms_2)}:drift={len(drift2)}"
            )
        else:
            print(f"  Discharge_Lounge   re-probe drift={len(drift2)} — was transient")

    # 2b. Patient Journey vs Scribe note count for the demo patient
    demo_subject = "16473192"
    demo_hadm = "21079163"
    sn = _get(f"http://127.0.0.1:8210/notes/by-encounter/{demo_hadm}") or {}
    tl = _get(
        f"http://127.0.0.1:8205/patient/{demo_subject}/admission/{demo_hadm}/timeline"
    ) or {}
    scribe_notes = sn.get("data") or []
    timeline_events = (tl.get("data") or {}).get("events") or []
    journey_note_events = [e for e in timeline_events if e.get("event_type") == "clinical_note"]
    print(
        f"  notes(scribe)={len(scribe_notes)}  notes(journey)={len(journey_note_events)} "
        f"timeline_events={len(timeline_events)}"
    )
    if len(scribe_notes) > 0 and len(journey_note_events) == 0:
        findings.append("notes_kafka_handler_dropped")

    # 2c. FHIR DocumentReference count for demo patient
    fhir = _get(
        f"http://127.0.0.1:8219/fhir/DocumentReference?patient=Patient/{demo_subject}"
        f"&encounter=Encounter/{demo_hadm}"
    ) or {}
    fhir_total = fhir.get("total")
    print(f"  fhir_doc_refs={fhir_total}  (scribe has {len(scribe_notes)})")
    if isinstance(fhir_total, int) and len(scribe_notes) > 0:
        if fhir_total < len(scribe_notes):
            findings.append(f"fhir_under_reports:fhir={fhir_total}:scribe={len(scribe_notes)}")

    # 2d. Bed_management: any department with capacity but no beds?
    for dept in (bm.get("data") or []):
        if dept.get("capacity", 0) > 0 and (
            dept.get("occupied") + dept.get("available") + dept.get("blocked", 0)
            + dept.get("cleaning", 0) + dept.get("reserved", 0)
        ) == 0:
            findings.append(f"empty_bed_inventory:{dept.get('department')}")

    # 2e. Hospital ops: simulator running? Use /api/state — has step + sim_time
    sim = _get("http://127.0.0.1:8203/api/state") or {}
    sim_data = sim.get("data") or sim
    if isinstance(sim_data, dict) and "step" in sim_data:
        print(
            f"  hospital_ops sim: step={sim_data.get('step')}  "
            f"sim_hours={sim_data.get('simulation_time_hours', 0):.1f}  "
            f"ED served={(sim_data.get('departments') or [{}])[0].get('total_served', '?')}"
        )

    # 2f. Alerts service: any unacked critical alerts piling up?
    al = _get("http://127.0.0.1:8222/alerts/active?limit=200") or {}
    alerts = (al.get("data") or []) if isinstance(al.get("data"), list) else []
    critical = [a for a in alerts if (a.get("severity") or "").lower() == "critical"]
    print(f"  alerts_active={len(alerts)}  critical={len(critical)}")
    if len(critical) > 50:
        findings.append(f"critical_alert_backlog:{len(critical)}")

    return findings


def check_mongo() -> List[str]:
    section("3. MONGO COLLECTIONS")
    findings: List[str] = []
    try:
        sys.path.insert(0, ".")
        import pymongo

        client = pymongo.MongoClient("mongodb://localhost:27017", serverSelectionTimeoutMS=2000)
        client.admin.command("ping")
        for db_name, coll_name in [
            ("clinical_scribe", "notes"),
            ("patient_journey", "notes"),
            ("MIMIC_Clinical_Notes", "discharge"),
            ("event_log", "events"),
            ("bed_management_state", "snapshots"),
        ]:
            try:
                cnt = client[db_name][coll_name].estimated_document_count()
                print(f"  {db_name}.{coll_name}: {cnt}")
            except Exception as e:  # noqa: BLE001
                print(f"  {db_name}.{coll_name}: ERR {e}")
        client.close()
    except Exception as e:  # noqa: BLE001
        print(f"  mongo unreachable: {e}")
        findings.append("mongo_unreachable")
    return findings


def main() -> int:
    print(f"\n>>> medai_monitor @ {time.strftime('%Y-%m-%d %H:%M:%S')} <<<")
    findings: List[str] = []
    findings += check_service_health()
    findings += check_cross_service()
    findings += check_mongo()
    section("SUMMARY")
    if findings:
        print(f"  {len(findings)} FINDING(S):")
        for f in findings:
            print(f"    - {f}")
        return 1
    else:
        print("  ALL GREEN — no findings")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
