"""SimContextBroker — in-process cache of live hospital state.

The chat engine prepends this snapshot to the LLM system prompt so queries
like "how's the ED right now?", "what's ICU occupancy?", "what are the latest
escalations?" skip the per-request tool-fetch phase entirely.

Background task refreshes every REFRESH_SEC seconds from:
    /beds/summary                   (:8208)
    /ops/staffing-recommendations   (:8203)
    /sim/stats-dashboard            (:8207)
    /deterioration/active-alerts    (:8220)

The snapshot is compressed to ~2 KB so it fits in the Ollama context window
without eating into the user query token budget.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("clinical_chat.sim_context")

REFRESH_SEC = 5.0
HTTP_TIMEOUT = 3.0


class SimContextBroker:
    """Keeps a live snapshot of hospital state for LLM prompt injection."""

    def __init__(
        self,
        beds_url: Optional[str] = None,
        ops_url: Optional[str] = None,
        sim_url: Optional[str] = None,
        det_url: Optional[str] = None,
    ) -> None:
        import os as _os
        self.beds_url = beds_url or _os.environ.get("BED_MANAGEMENT_URL", "http://localhost:8208")
        self.ops_url = ops_url or _os.environ.get("HOSPITAL_OPS_URL", "http://localhost:8203")
        self.sim_url = sim_url or _os.environ.get("DATA_INGESTION_URL", "http://localhost:8207")
        self.det_url = det_url or _os.environ.get("DETERIORATION_URL", "http://localhost:8220")
        self._snapshot: Dict[str, Any] = {"ready": False}
        self._task: Optional[asyncio.Task] = None
        self._last_refresh: Optional[datetime] = None

    @property
    def snapshot(self) -> Dict[str, Any]:
        return self._snapshot

    @property
    def is_fresh(self) -> bool:
        if self._last_refresh is None:
            return False
        age = (datetime.now(timezone.utc) - self._last_refresh).total_seconds()
        return age < (REFRESH_SEC * 3)

    # ------------------------------------------------------------------ loop
    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.refresh()
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                logger.debug("sim_context_refresh_error: %s", exc)
            await asyncio.sleep(REFRESH_SEC)

    # ------------------------------------------------------------------ fetch
    async def refresh(self) -> Dict[str, Any]:
        """Pull the four source endpoints concurrently and build a snapshot."""
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, verify=False, trust_env=False) as client:
            beds_t = client.get(f"{self.beds_url}/beds/summary")
            ops_t = client.get(f"{self.ops_url}/staffing-recommendations")
            sim_t = client.get(f"{self.sim_url}/stats-dashboard")
            det_t = client.get(f"{self.det_url}/deterioration/active-alerts")
            beds_r, ops_r, sim_r, det_r = await asyncio.gather(
                beds_t, ops_t, sim_t, det_t, return_exceptions=True,
            )

        snapshot: Dict[str, Any] = {
            "ready": True,
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "departments": [],
            "staffing": {},
            "sim": {},
            "active_deterioration_alerts": [],
        }

        # /beds/summary
        if isinstance(beds_r, httpx.Response) and beds_r.status_code == 200:
            try:
                body = beds_r.json().get("data", [])
                snapshot["departments"] = [
                    {
                        "dept": s.get("department"),
                        "occ": s.get("occupied", 0),
                        "cap": s.get("capacity", 0),
                        "alert": s.get("alert_level"),
                    }
                    for s in body
                ]
            except Exception as exc:  # noqa: BLE001
                logger.debug("beds parse: %s", exc)

        # /staffing-recommendations
        if isinstance(ops_r, httpx.Response) and ops_r.status_code == 200:
            try:
                body = ops_r.json().get("data", {})
                raw_depts = body.get("departments", {})
                compact: Dict[str, Dict[str, Any]] = {}
                for name, info in raw_depts.items():
                    if not isinstance(info, dict):
                        continue
                    compact[name] = {
                        "doc": info.get("current_doctors"),
                        "nurse": info.get("current_nurses"),
                        "pts": info.get("patient_count"),
                        "floor": info.get("nurse_floor"),
                        "safe": info.get("meets_safety_floor", True),
                        "action": info.get("recommended_action"),
                    }
                snapshot["staffing"] = compact
            except Exception as exc:  # noqa: BLE001
                logger.debug("ops parse: %s", exc)

        # /stats-dashboard
        if isinstance(sim_r, httpx.Response) and sim_r.status_code == 200:
            try:
                body = sim_r.json()
                snapshot["sim"] = {
                    "sim_time": body.get("sim_time"),
                    "total_active": body.get("total_active"),
                    "total_discharged": body.get("total_discharged"),
                    "icu_count": body.get("icu_count"),
                    "ed_count": body.get("ed_count"),
                    "critical_count": body.get("critical_count"),
                    "avg_los_hours": body.get("avg_los_hours"),
                }
            except Exception as exc:  # noqa: BLE001
                logger.debug("sim parse: %s", exc)

        # /deterioration/active-alerts
        if isinstance(det_r, httpx.Response) and det_r.status_code == 200:
            try:
                alerts = det_r.json().get("data", [])
                # Only keep the 5 most recent + highest-score alerts
                top = sorted(
                    alerts,
                    key=lambda a: (
                        (a.get("score") or {}).get("total", 0),
                        a.get("observed_at", ""),
                    ),
                    reverse=True,
                )[:5]
                snapshot["active_deterioration_alerts"] = [
                    {
                        "hadm_id": a.get("hadm_id"),
                        "dept": a.get("department"),
                        "system": a.get("scoring_system"),
                        "score": (a.get("score") or {}).get("total"),
                        "risk_band": (a.get("score") or {}).get("risk_band"),
                    }
                    for a in top
                ]
            except Exception as exc:  # noqa: BLE001
                logger.debug("det parse: %s", exc)

        self._snapshot = snapshot
        self._last_refresh = datetime.now(timezone.utc)
        return snapshot

    # ------------------------------------------------------------------ prompt
    def to_prompt_fragment(self) -> str:
        """Render the snapshot as a concise system-prompt fragment."""
        if not self._snapshot.get("ready"):
            return "(live hospital state not yet available)"

        lines: List[str] = ["### LIVE HOSPITAL STATE"]
        sim = self._snapshot.get("sim") or {}
        if sim.get("sim_time"):
            lines.append(f"sim_time={sim.get('sim_time')}")
        lines.append(
            f"active_patients={sim.get('total_active', '?')} "
            f"discharged={sim.get('total_discharged', '?')} "
            f"ed={sim.get('ed_count', '?')} icu={sim.get('icu_count', '?')}"
        )

        # Compact bed-by-dept line
        depts = self._snapshot.get("departments") or []
        if depts:
            busy = [d for d in depts if (d.get("occ") or 0) > 0]
            if busy:
                dept_bits = ", ".join(
                    f"{d['dept']}={d['occ']}/{d['cap']}" for d in busy
                )
                lines.append(f"BEDS: {dept_bits}")

        # Staffing with safety floor
        staffing = self._snapshot.get("staffing") or {}
        unsafe = [
            f"{name}(nurse={info.get('nurse')}/{info.get('floor')})"
            for name, info in staffing.items()
            if info.get("safe") is False
        ]
        if unsafe:
            lines.append("UNSAFE STAFFING: " + ", ".join(unsafe))

        # Active deterioration alerts
        alerts = self._snapshot.get("active_deterioration_alerts") or []
        if alerts:
            bits = [
                f"{a.get('dept','?')}/{a.get('system','?')}={a.get('score')}({a.get('risk_band')})"
                for a in alerts
            ]
            lines.append("ACTIVE DETERIORATION ALERTS: " + ", ".join(bits))

        lines.append(f"(refreshed {self._snapshot.get('refreshed_at')})")
        return "\n".join(lines)


__all__ = ["SimContextBroker"]
