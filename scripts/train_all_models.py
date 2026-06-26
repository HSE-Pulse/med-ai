"""Train models for all 8 applications sequentially."""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPS = [
    ("App 01: ED Triage", "app_01_ed_triage.backend.models.train"),
    ("App 02: Sepsis ICU", "app_02_sepsis_icu.backend.models.train"),
    ("App 03: Hospital Ops", "app_03_hospital_ops.backend.models.train"),
    ("App 04: Oncology AI", "app_04_oncology_ai.backend.models.train"),
    ("App 08: Bed Management", "app_08_bed_management.backend.models.train"),
    ("App 14: ED Flow", "app_14_ed_flow.backend.models.train"),
    ("App 09: Waiting List", "app_09_waiting_list.backend.models.train"),
    ("App 10: Clinical Scribe", "app_10_clinical_scribe.backend.models.train"),
]


def main():
    print("=" * 60)
    print("Training models for all 8 applications")
    print("=" * 60)

    results = {}
    for name, module in APPS:
        print(f"\n{'='*60}")
        print(f"  {name}")
        print(f"{'='*60}\n")
        t0 = time.time()
        ret = subprocess.run(
            [sys.executable, "-m", module],
            cwd=str(ROOT),
            capture_output=False,
        )
        elapsed = time.time() - t0
        status = "OK" if ret.returncode == 0 else "FAILED"
        results[name] = (status, elapsed)
        print(f"\n  -> {status} ({elapsed:.1f}s)")

    print(f"\n{'='*60}")
    print("Summary:")
    print(f"{'='*60}")
    for name, (status, elapsed) in results.items():
        print(f"  {name}: {status} ({elapsed:.1f}s)")


if __name__ == "__main__":
    main()
