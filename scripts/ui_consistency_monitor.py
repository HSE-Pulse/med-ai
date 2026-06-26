"""Poll the endpoints the dashboard consumes and emit consistency events.

Each line on stdout is one event the Monitor tool surfaces as a notification.
The script emits selectively:
  * BASELINE on first cycle
  * DRIFT when any tracked count moves by >= threshold
  * INCONSISTENT when a cross-module rule fails
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime

ENDPOINTS = {
    "ed_flow":       ("http://localhost:8214/ed-state",          ["data", "total_patients"]),
    "ed_waiting":    ("http://localhost:8214/ed-state",          ["data", "waiting_count"]),
    "ed_boarding":   ("http://localhost:8214/ed-state",          ["data", "boarding_count"]),
    "bed_summary":   ("http://localhost:8208/beds/summary",      ["data"]),  # list, custom
    "sepsis_icu":    ("http://localhost:8202/unit-overview",     ["total_patients"]),
    "deterioration": ("http://localhost:8220/deterioration/stats", ["data", "active_patients"]),
    "sim_state":     ("http://localhost:8207/state",             ["active_patients"]),
    "hosp_ops":      ("http://localhost:8203/api/metrics",       ["active_patients"]),
    "hosp_ops_step": ("http://localhost:8203/api/state",         ["step"]),
}

DRIFT_THRESHOLD = 5  # only emit when a count moves by this much
PRINT_EVERY_N = 6    # also emit a heartbeat every N cycles (~3 min at 30s)
SLEEP_S = 30


def fetch(url: str):
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"_err": str(e)}


def deep_get(d, path):
    for k in path:
        if d is None:
            return None
        d = d.get(k) if isinstance(d, dict) else None
    return d


def gather():
    out = {}
    bed_resp = fetch(ENDPOINTS["bed_summary"][0])
    bed_data = bed_resp.get("data", []) if isinstance(bed_resp, dict) else []
    out["bed_total"] = sum(x.get("occupied", 0) for x in bed_data) if isinstance(bed_data, list) else 0
    out["bed_capacity"] = sum(x.get("capacity", 0) for x in bed_data) if isinstance(bed_data, list) else 0
    out["bed_icu"] = next((x.get("occupied", 0) for x in bed_data if x.get("department") == "ICU"), 0)
    out["bed_full_depts"] = sum(1 for x in bed_data if x.get("alert_level") == "black") if isinstance(bed_data, list) else 0

    for key in ("ed_flow", "ed_waiting", "ed_boarding", "sepsis_icu", "deterioration",
                "sim_state", "hosp_ops", "hosp_ops_step"):
        url, path = ENDPOINTS[key]
        out[key] = deep_get(fetch(url), path)
    return out


def consistency_issues(s):
    issues = []
    if s["sim_state"] is not None and s["bed_total"] > s["sim_state"] + 5:
        issues.append(f"bed_total({s['bed_total']}) > sim_active({s['sim_state']})+5")
    if s["sepsis_icu"] is not None and s["bed_icu"] is not None:
        # sepsis-icu reports all ICU sim patients (incl those over capacity);
        # bed-icu is capped at 12. Flag only if sepsis_icu < bed_icu (impossible).
        if s["sepsis_icu"] < s["bed_icu"]:
            issues.append(f"sepsis_icu({s['sepsis_icu']}) < bed_icu({s['bed_icu']})")
    if s["ed_flow"] is not None and s["ed_waiting"] is not None and s["ed_boarding"] is not None:
        if s["ed_flow"] < s["ed_waiting"] + s["ed_boarding"]:
            issues.append(
                f"ed_total({s['ed_flow']}) < waiting({s['ed_waiting']})+boarding({s['ed_boarding']})"
            )
    # Hospital Ops MARL is a parallel scenario simulator with its own timeline
    # (independent from the live event-driven simulator on 8207). Drift from
    # live state is tracked via DRIFT events but not treated as an inconsistency.
    return issues


def fmt(s):
    return (
        f"sim={s['sim_state']} det={s['deterioration']} bed={s['bed_total']}/{s['bed_capacity']} "
        f"icu={s['bed_icu']}(sepsis={s['sepsis_icu']}) ed={s['ed_flow']} "
        f"full_depts={s['bed_full_depts']} "
        f"marl={s['hosp_ops']}@step{s['hosp_ops_step']}"
    )


def main():
    last = None
    cycle = 0
    while True:
        cycle += 1
        cur = gather()
        now = datetime.utcnow().isoformat(timespec="seconds")
        issues = consistency_issues(cur)

        if cycle == 1:
            print(f"BASELINE {now} {fmt(cur)}", flush=True)
        else:
            drift_keys = [
                k for k in ("sim_state", "deterioration", "bed_total", "bed_icu",
                            "sepsis_icu", "ed_flow", "hosp_ops")
                if last and cur.get(k) is not None and last.get(k) is not None
                and abs(cur[k] - last[k]) >= DRIFT_THRESHOLD
            ]
            if drift_keys:
                deltas = " ".join(f"{k}:{last[k]}->{cur[k]}" for k in drift_keys)
                print(f"DRIFT {now} {deltas} | {fmt(cur)}", flush=True)
            elif cycle % PRINT_EVERY_N == 0:
                print(f"HEARTBEAT {now} {fmt(cur)}", flush=True)

        if issues:
            print(f"INCONSISTENT {now} {'; '.join(issues)} | {fmt(cur)}", flush=True)

        last = cur
        time.sleep(SLEEP_S)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
