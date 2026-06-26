"""Launch all 18 backend services + the React/Vite dashboard.

Usage::

    python scripts/start_all_services.py                 # all services + dashboard
    python scripts/start_all_services.py --no-dashboard  # backend only
    python scripts/start_all_services.py --no-reload     # disable uvicorn --reload
    python scripts/start_all_services.py --only 8201,8220  # just these ports

Press Ctrl+C to stop every process gracefully.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = ROOT / "dashboard"

# (label, import path, port)
SERVICES: List[Tuple[str, str, int]] = [
    ("ED Triage",           "app_01_ed_triage.backend.app.main:app",        8201),
    ("Sepsis ICU",          "app_02_sepsis_icu.backend.app.main:app",       8202),
    ("Hospital Ops",        "app_03_hospital_ops.backend.app.main:app",     8203),
    ("Oncology AI",         "app_04_oncology_ai.backend.app.main:app",      8204),
    ("Patient Journey",     "app_05_patient_journey.backend.api.main:app",  8205),
    ("Clinical Chat",       "app_06_clinical_chat.backend.main:app",        8206),
    ("Data Ingestion",      "app_07_data_ingestion.backend.api.main:app",   8207),
    ("Bed Management",      "app_08_bed_management.backend.app.main:app",   8208),
    ("Waiting List",        "app_09_waiting_list.backend.app.main:app",     8209),
    ("Clinical Scribe",     "app_10_clinical_scribe.backend.app.main:app",  8210),
    ("ED Flow",             "app_14_ed_flow.backend.app.main:app",          8214),
    ("Hospital ERP",        "app_15_erp.backend.app.main:app",              8215),
    ("Trolley Watch",       "app_16_trolley_watch.backend.app.main:app",    8216),
    ("GDPR Compliance",     "app_17_gdpr.backend.app.main:app",             8217),
    ("XAI",                 "app_18_xai.backend.app.main:app",              8218),
    ("FHIR Gateway",        "app_19_fhir.backend.app.main:app",             8219),
    ("Deterioration",       "app_20_deterioration.backend.app.main:app",    8220),
    ("Discharge Lounge",    "app_21_discharge_lounge.backend.app.main:app", 8221),
]

processes: List[Tuple[str, subprocess.Popen]] = []


def _resolve_npm() -> Optional[str]:
    """Find npm — Windows ships it as npm.cmd, Unix as npm."""
    for candidate in ("npm.cmd", "npm"):
        path = shutil.which(candidate)
        if path:
            return path
    return None


def start_backend(reload: bool, only: Optional[set]) -> None:
    for name, module, port in SERVICES:
        if only and port not in only:
            continue
        cmd = [
            sys.executable, "-m", "uvicorn", module,
            "--host", "0.0.0.0",
            "--port", str(port),
        ]
        if reload:
            cmd.append("--reload")
        print(f"  [{port}] {name:<20} -> {module}")
        proc = subprocess.Popen(cmd, cwd=str(ROOT))
        processes.append((f"{name} (:{port})", proc))
        time.sleep(0.4)  # stagger to avoid port-bind races


def start_dashboard() -> None:
    npm = _resolve_npm()
    if npm is None:
        print("  ! npm not found on PATH — skipping dashboard")
        return
    if not DASHBOARD_DIR.exists():
        print(f"  ! dashboard directory missing: {DASHBOARD_DIR}")
        return
    node_modules = DASHBOARD_DIR / "node_modules"
    if not node_modules.exists():
        print("  ! dashboard/node_modules missing — run 'npm install' in dashboard/ first")
        return
    print(f"  [5173] Dashboard           -> Vite dev server")
    proc = subprocess.Popen(
        [npm, "run", "dev"],
        cwd=str(DASHBOARD_DIR),
    )
    processes.append(("Dashboard (:5173)", proc))


def print_banner(dashboard: bool, only: Optional[set]) -> None:
    print("\n" + "=" * 68)
    print("MedAI Platform — running services")
    print("=" * 68)
    for name, _, port in SERVICES:
        if only and port not in only:
            continue
        print(f"  http://localhost:{port:<5}  {name:<20}  /docs -> /health")
    if dashboard and any(p[0].startswith("Dashboard") for p in processes):
        print(f"  http://localhost:5173  Dashboard           (Vite)")
    print("=" * 68)
    print("Ctrl+C to stop all services")
    print("=" * 68 + "\n")


def stop_all(*_a) -> None:
    print("\nStopping all services...")
    for name, proc in processes:
        try:
            if os.name == "nt":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.terminate()
            print(f"  stopped {name}")
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    sys.exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Start all MedAI services + dashboard")
    parser.add_argument("--no-dashboard", action="store_true", help="Skip the Vite dashboard")
    parser.add_argument("--no-reload", action="store_true", help="Disable uvicorn --reload")
    parser.add_argument("--only", type=str, default=None,
                        help="Comma-separated port list to start (backend only)")
    args = parser.parse_args()

    only = None
    if args.only:
        try:
            only = {int(p.strip()) for p in args.only.split(",") if p.strip()}
        except ValueError:
            print("--only must be a comma-separated list of ports")
            sys.exit(2)

    signal.signal(signal.SIGINT, stop_all)
    if os.name != "nt":
        signal.signal(signal.SIGTERM, stop_all)

    print("Launching backend services...")
    start_backend(reload=not args.no_reload, only=only)

    if not args.no_dashboard and not only:
        print("\nLaunching dashboard...")
        start_dashboard()

    print_banner(dashboard=not args.no_dashboard, only=only)

    try:
        while True:
            time.sleep(1)
            # Detect any child crash
            for name, proc in list(processes):
                if proc.poll() is not None:
                    print(f"  ! {name} exited with code {proc.returncode}")
                    processes.remove((name, proc))
            if not processes:
                print("All processes have exited.")
                sys.exit(0)
    except KeyboardInterrupt:
        stop_all()


if __name__ == "__main__":
    main()
