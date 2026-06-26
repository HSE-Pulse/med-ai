"""
Digital Twin Simulation Orchestrator
=====================================
Propagates simulated patient events through all AI modules in sequence,
creating a complete digital twin of hospital operations.

When a patient arrives, the orchestrator:
1. ED Triage (8201) → acuity + disposition prediction
2. ED Flow (8214) → flow prediction, PET breach risk, LWBS risk
3. Sepsis ICU (8202) → sepsis risk screening (if ICU-bound)
4. Bed Management (8208) → discharge prediction + bed allocation
5. Oncology AI (8204) → cancer risk (if oncology diagnosis)
6. Waiting List (8209) → priority scoring (if elective)
7. Clinical Scribe (8210) → auto-document the encounter
8. Patient Journey (8205) → full timeline query
9. Clinical Chat (8206) → context available for queries

Each event (admission, transfer, vital, lab, discharge) triggers the
relevant subset of modules, creating a cascading data flow.

Usage::

    from shared.integration.digital_twin import DigitalTwinOrchestrator
    orchestrator = DigitalTwinOrchestrator()
    await orchestrator.process_admission(patient_data)
    await orchestrator.process_vital(vital_data)
    await orchestrator.process_discharge(discharge_data)
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from shared.constants.hospital import map_department as _map_dept
from shared.integration.event_bus import Event, get_event_bus
from shared.integration.service_client import ServiceClient
from shared.integration.sim_clock import SimClock
from shared.integration.tracing import get_tracer  # for manual cross-service span chaining

logger = logging.getLogger("digital_twin.orchestrator")

from shared.constants.mimic import VITAL_ITEMID_TO_NAME as _VITAL_ITEMID_MAP
from shared.constants.mimic import LAB_ITEMID_TO_NAME as _LAB_ITEMID_MAP


class DigitalTwinOrchestrator:
    """Orchestrates data flow through all modules for each patient event.

    Acts as the central nervous system of the digital twin, routing
    events to the correct modules in the correct sequence and
    collecting responses for downstream consumers.
    """

    # Module processing pipeline — ordered by dependency
    ADMISSION_PIPELINE = [
        "ed_triage",        # Step 1: Acuity + disposition prediction
        "ed_flow",          # Step 2: Flow prediction, PET/LWBS risk
        "bed_management",   # Step 3: Discharge prediction + bed allocation
        "oncology_ai",      # Step 4: Cancer risk (conditional)
        "clinical_scribe",  # Step 5: Auto-document encounter
    ]

    def __init__(self, service_client: Optional[ServiceClient] = None) -> None:
        self.client = service_client or ServiceClient()
        self.bus = get_event_bus()
        self.patient_context: Dict[str, Dict[str, Any]] = {}  # hadm_id → context
        self._pipeline_results: Dict[str, List[Dict]] = {}  # hadm_id → results log
        self.disabled_modules: Set[str] = set()  # runtime-disabled modules
        # Concurrency cap on the per-vital fan-out. Without this the
        # orchestrator would issue ~5 cross-service calls per vital × 220
        # active patients × 1 vital/sec ≈ 1100 in-flight HTTP requests
        # against bed_management alone — which then started returning
        # ReadTimeout / ConnectTimeout (~3/s during the 15-min audit). The
        # semaphore is acquired around each per-vital task list. (Closes N3.)
        self._vital_semaphore = asyncio.Semaphore(
            int(os.environ.get("DIGITAL_TWIN_VITAL_CONCURRENCY", "16")),
        )
        # hadm_ids that have already been processed through ``process_discharge``.
        # Vitals continue to flow from the simulator after discharge (the engine's
        # vital_drift_loop fires on every active patient), and ``process_vital``
        # used to lazily re-hydrate ``patient_context`` for them — which then
        # called /predict-discharge on every drift tick, which then republished
        # ``discharge_predicted``, which auto-re-admitted them to the lounge,
        # which re-fired patient_discharged. Tracking discharged hadms shuts
        # that loop down at the source.
        self.discharged_hadms: Set[str] = set()
        self._discharged_order: List[str] = []  # FIFO bound for the set

        # Bug #1 — per-module health tracking so a single downstream failure
        # can't abort the admission pipeline. Exposed via the SimEngine
        # ``GET /sim/digital-twin/health`` endpoint.
        self.module_health: Dict[str, Dict[str, Any]] = {}

        # Optional outbox — when attached, publish() goes through the
        # durable outbox so Kafka/Mongo blips don't drop events.
        self._outbox = None  # type: Optional[Any]

    def attach_outbox(self, outbox) -> None:
        """Wire a durable Outbox in front of bus.publish — at-least-once delivery."""
        self._outbox = outbox

    async def _publish(self, topic: str, payload: Dict[str, Any]) -> None:
        """Publish via outbox when attached, direct otherwise."""
        if self._outbox is not None:
            await self._outbox.publish(self.bus, topic, payload, source_module="digital_twin")
        else:
            await self.bus.publish(topic, payload, source_module="digital_twin")

    # ------------------------------------------------------------------
    # Resilient call wrapper — Bug #1 fix.
    # ------------------------------------------------------------------
    async def _safe_call(
        self,
        module: str,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        timeout_s: Optional[float] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Call ``module``'s endpoint, logging failures and never raising.

        Updates ``self.module_health[module]`` after every attempt so the
        health endpoint can report per-service last-success timestamps and
        consecutive-failure counts.

        When ``idempotency_key`` is provided it is forwarded as the
        ``Idempotency-Key`` header so retries from circuit-breaker half-opens
        don't cause duplicate side effects downstream.
        """
        if module in self.disabled_modules:
            return {"status": "error", "error": f"{module} disabled"}

        mc = self.client._get_client(module)
        try:
            if timeout_s is not None:
                mc.timeout = timeout_s
            if method.upper() == "GET":
                result = await mc.get(path, params=payload)
            elif method.upper() == "POST":
                result = await mc.post(path, data=payload, idempotency_key=idempotency_key)
            elif method.upper() == "DELETE":
                result = await mc.delete(path)
            elif method.upper() == "PATCH":
                result = await mc.patch(path, data=payload, idempotency_key=idempotency_key)
            else:
                raise ValueError(f"unsupported method: {method}")
        except Exception as exc:  # noqa: BLE001 — never abort the pipeline
            self._record_call(module, success=False, error=str(exc))
            logger.warning(
                "cross_service_call_failed",
                extra={
                    "service": module,
                    "endpoint": path,
                    "error": type(exc).__name__,
                    "detail": str(exc),
                },
            )
            return {"status": "error", "error": str(exc)}

        is_error = result.get("status") == "error"
        self._record_call(module, success=not is_error, error=result.get("error"))
        return result

    def _record_call(self, module: str, *, success: bool, error: Optional[str] = None) -> None:
        entry = self.module_health.setdefault(
            module,
            {
                "last_success": None,
                "last_failure": None,
                "failure_count": 0,
                "consecutive_failures": 0,
                "is_healthy": True,
            },
        )
        now = datetime.now(timezone.utc).isoformat()
        if success:
            entry["last_success"] = now
            entry["consecutive_failures"] = 0
            entry["is_healthy"] = True
        else:
            entry["last_failure"] = now
            entry["failure_count"] += 1
            entry["consecutive_failures"] += 1
            entry["last_error"] = error
            # A module is considered unhealthy after 3 consecutive failures
            if entry["consecutive_failures"] >= 3:
                entry["is_healthy"] = False

    def health_snapshot(self) -> Dict[str, Any]:
        """Return per-module health for the ``/sim/digital-twin/health`` endpoint."""
        return {
            "orchestrator": {
                "active_patients": len(self.patient_context),
                "disabled_modules": list(self.disabled_modules),
                "sim_time": SimClock.get_instance().get_sim_time().isoformat(),
                "sim_running": SimClock.get_instance().is_sim_running(),
            },
            "modules": self.module_health,
            "circuit_breakers": self.client.breaker_snapshot(),
        }

    # ------------------------------------------------------------------
    # Runtime configuration
    # ------------------------------------------------------------------
    def enable_module(self, name: str) -> None:
        """Re-enable a previously disabled pipeline module."""
        self.disabled_modules.discard(name)
        logger.info("Digital Twin: Module '%s' enabled", name)

    def disable_module(self, name: str) -> None:
        """Disable a pipeline module (skipped during event processing)."""
        self.disabled_modules.add(name)
        logger.info("Digital Twin: Module '%s' disabled", name)

    def get_config(self) -> Dict[str, Any]:
        """Return current pipeline configuration and stats."""
        return {
            "pipeline": list(self.ADMISSION_PIPELINE),
            "disabled_modules": list(self.disabled_modules),
            "active_patients": len(self.patient_context),
            "total_processed": sum(len(v) for v in self._pipeline_results.values()),
        }

    def reset(self) -> None:
        """Clear all patient contexts and pipeline results."""
        self.patient_context.clear()
        self._pipeline_results.clear()
        logger.info("Digital Twin reset — all patient contexts cleared")

    # ------------------------------------------------------------------
    # 1. ADMISSION EVENT — triggers full pipeline
    # ------------------------------------------------------------------
    async def process_admission(self, patient: Dict[str, Any]) -> Dict[str, Any]:
        """Process a new patient admission through all relevant modules.

        Parameters
        ----------
        patient : dict
            Must include: subject_id, hadm_id, age (or anchor_age),
            gender, admission_type, and optionally vitals/labs.

        Returns
        -------
        dict with results from each module in the pipeline.
        """
        hadm_id = str(patient.get("hadm_id", "unknown"))

        # Wrap the whole cascade in a parent span so Jaeger shows the full
        # pipeline as a single trace. httpx auto-instrumentation generates
        # child spans per downstream call for free.
        try:
            from shared.integration.tracing import get_tracer
            tracer = get_tracer("digital_twin")
            cascade_span_cm = tracer.start_as_current_span(
                "dt.process_admission",
                attributes={
                    "patient.hadm_id": hadm_id,
                    "patient.subject_id": str(patient.get("subject_id", "")),
                    "patient.admission_type": str(patient.get("admission_type", "")),
                },
            )
        except Exception:  # noqa: BLE001
            from contextlib import nullcontext
            cascade_span_cm = nullcontext()

        with cascade_span_cm:
            return await self._process_admission_traced(patient, hadm_id)

    async def _process_admission_traced(self, patient: Dict[str, Any], hadm_id: str) -> Dict[str, Any]:
        """Actual admission pipeline — wrapped by ``process_admission`` for tracing."""
        logger.info("Digital Twin: Processing admission %s", hadm_id)

        results: Dict[str, Any] = {"hadm_id": hadm_id, "timestamp": _now_iso()}
        self.patient_context[hadm_id] = {**patient, "events": [], "module_results": {}}

        # ── Step 1: ED Triage ──────────────────────────────────────
        acuity = 3
        disposition = "unknown"
        if "ed_triage" not in self.disabled_modules:
            triage_input = _build_triage_input(patient)
            triage_result = await self._safe_call("ed_triage", "POST", "/predict", triage_input)
            results["ed_triage"] = triage_result.get("data", {})
            self.patient_context[hadm_id]["module_results"]["ed_triage"] = results["ed_triage"]
            acuity = results["ed_triage"].get("acuity_level", 3)
            disposition = results["ed_triage"].get("disposition", "unknown")
        logger.info("  ED Triage: acuity=%d, disposition=%s", acuity, disposition)

        # ── Step 2: ED Flow ────────────────────────────────────────
        # Bound this call with an explicit 2s timeout. Without it, a slow
        # ed_flow response could eat into the orchestrator's 15s outer
        # timeout and starve later steps. We previously saw the audit
        # report 3 of 5 admissions tracked in ED Flow — almost certainly
        # because the 2 lost ones hit the outer timeout on a different
        # step before reaching this code or got starved by a long
        # downstream call. timeout_s ensures every admission gets at
        # least a 2-second slot for the ed_flow registration.
        pet_risk = 0.0
        if "ed_flow" not in self.disabled_modules:
            flow_event = {
                "event_type": "triage",
                "timestamp": patient.get("admittime"),
                "details": {
                    "mts_category": min(acuity, 5),
                    "vitals": patient.get("vitals", {}),
                    "hadm_id": patient.get("hadm_id"),
                    "subject_id": patient.get("subject_id"),
                    # ed_triage's /predict requires age and gender; without
                    # them every call returned 422 and admission_probability
                    # stayed at the default 0.0 for every patient.
                    "age": patient.get("age", 65),
                    "gender": patient.get("gender", "U"),
                },
            }
            flow_result = await self._safe_call(
                "ed_flow", "POST",
                f"/patients/{patient.get('subject_id', 0)}/event", flow_event,
                timeout_s=2.0,
            )
            results["ed_flow"] = flow_result.get("data", {})
            self.patient_context[hadm_id]["module_results"]["ed_flow"] = results["ed_flow"]

            pet_risk = results["ed_flow"].get("pet_breach_risk", 0)
            lwbs_risk = results["ed_flow"].get("lwbs_risk", 0)
            logger.info("  ED Flow: PET risk=%.2f, LWBS risk=%.2f", pet_risk, lwbs_risk)

        # ── Step 3: Bed Management ─────────────────────────────────
        if disposition in ("admit_to_inpatient", "transfer") and "bed_management" not in self.disabled_modules:
            discharge_input = {
                "patient_id": patient.get("subject_id", 0),
                "hadm_id": patient.get("hadm_id", 0),
                "department": patient.get("department", "Medicine"),
                "admission_time": patient.get("admittime", _now_iso()),
                "sim_time": patient.get("admittime"),
                "age": patient.get("age", 65),
                "gender": patient.get("gender", "U"),
                "current_vitals": patient.get("vitals"),
                "current_labs": patient.get("labs"),
            }
            disch_result = await self._safe_call(
                "bed_management", "POST", "/predict-discharge", discharge_input
            )
            results["bed_management"] = disch_result.get("data", {})
            self.patient_context[hadm_id]["module_results"]["bed_management"] = results["bed_management"]

            # Request bed allocation
            alloc_input = {
                "patient_id": patient.get("subject_id", 0),
                "hadm_id": patient.get("hadm_id", 0),
                "acuity": float(acuity),
                "department_preference": patient.get("department"),
                "gender": patient.get("gender", "U"),
                "admission_type": patient.get("admission_type", "EMERGENCY"),
                "sim_time": patient.get("admittime"),
            }
            alloc_result = await self._safe_call("bed_management", "POST", "/allocate", alloc_input)
            results["bed_allocation"] = alloc_result.get("data", {})

            # Fan out the post-allocation notifications in parallel — they
            # don't depend on each other's results, so awaiting them serially
            # used to add ~3× latency to every admission. With ``gather``
            # the slowest call dictates the wall time instead of the sum.
            recommended_bed = results["bed_allocation"].get("recommended_bed")
            from shared.constants.hospital import LOS_PARAMS
            dept = patient.get("department", "Medicine")
            etm = max(15, int(LOS_PARAMS.get(dept, {}).get("median_h", 24) * 10))

            fanout: List[Any] = [
                self._safe_call("hospital_ops", "POST", "/notify-discharge-prediction", {
                    "hadm_id": patient.get("hadm_id"),
                    "predicted_discharge": results["bed_management"].get("predicted_discharge_time"),
                    "readiness_score": results["bed_management"].get("discharge_readiness_score", 0),
                    "department": patient.get("department"),
                }),
            ]
            if recommended_bed:
                fanout.append(self._safe_call("ed_flow", "POST", "/notify-bed-allocated", {
                    "patient_id": patient.get("subject_id", 0),
                    "bed_id": recommended_bed,
                    "estimated_transfer_time_min": etm,
                    "department": dept,
                }))
            # Inject this admission into hospital_ops DES so its population
            # mirrors the real simulation instead of running a parallel
            # Poisson stream. Only call for admitted patients (acuity ≤ 3
            # or clear inpatient disposition).
            if disposition in ("admit_to_inpatient", "transfer") or acuity <= 3:
                fanout.append(self._safe_call("hospital_ops", "POST", "/admit-patient", {
                    "hadm_id": patient.get("hadm_id"),
                    "subject_id": patient.get("subject_id"),
                    "acuity": acuity,
                    "admission_type": patient.get("admission_type", "EMERGENCY"),
                    "department": patient.get("department", "ED"),
                }))

            import asyncio as _aio
            await _aio.gather(*fanout, return_exceptions=True)

            logger.info("  Bed Mgmt: discharge_24h=%.2f, bed=%s",
                        results["bed_management"].get("discharge_probability_24h", 0),
                        recommended_bed or "none")

        # ── Step 4 + Step 5 in parallel ────────────────────────────
        # Oncology AI and Clinical Scribe are independent: they read
        # patient + early-step results but never each other's outputs, so
        # awaiting them serially just stacked their latencies. Run both
        # concurrently and merge into ``results`` once both settle.
        diagnoses = patient.get("diagnoses", [])
        is_cancer = any(
            str(d.get("icd_code", "")).upper().startswith("C")
            for d in diagnoses
        ) if diagnoses else False

        async def _oncology_step() -> None:
            if not is_cancer or "oncology_ai" in self.disabled_modules:
                return
            onc_input = {
                "age": patient.get("age", 65),
                "gender": patient.get("gender", "M"),
                "cancer_type": "Unknown",
                "stage_proxy": 2,
                "num_prior_admissions": 0,
            }
            onc_result = await self._safe_call("oncology_ai", "POST", "/predict-risk", onc_input)
            results["oncology_ai"] = onc_result.get("data", {})
            logger.info("  Oncology: risk assessed")

            # Integration 6 — high-risk onco flag (these two cross-service
            # bumps are themselves independent → fire them in parallel too).
            readmit_risk = results["oncology_ai"].get("readmission_30d_risk", 0) or 0
            if readmit_risk > 0.6:
                import asyncio as _aio2
                await _aio2.gather(
                    self._safe_call("patient_journey", "POST", "/flag-high-risk", {
                        "hadm_id": patient.get("hadm_id"),
                        "subject_id": patient.get("subject_id"),
                        "flag": "oncology_high_risk",
                        "readmission_risk": readmit_risk,
                    }),
                    self._safe_call("waiting_list", "POST", "/bump-priority", {
                        "hadm_id": patient.get("hadm_id"),
                        "subject_id": patient.get("subject_id"),
                        "bump": 0.3,
                        "reason": "oncology_high_risk",
                    }),
                    return_exceptions=True,
                )

        async def _scribe_step() -> None:
            # MIMIC-IV-Note narrative now streams per-charttime through
            # ``process_note`` instead of being burst-ingested here, so this
            # step is only responsible for the SIM-only fallback admission
            # note. If the patient has any MIMIC notes queued, we leave
            # ingestion to the streaming pipeline and just record the count.
            if "clinical_scribe" in self.disabled_modules:
                return
            import asyncio as _aio_notes
            # Look up MIMIC notes by the ORIGINAL hadm_id (an int) — the
            # SIM-prefixed sim_hadm wouldn't match MIMIC's int hadm column.
            # data_ingestion's _admit_next_patient now carries the original
            # alongside the simulated hadm in dt_patient.
            lookup_hadm = patient.get("original_hadm_id") or patient.get("hadm_id")
            mimic_notes = await _aio_notes.to_thread(_fetch_mimic_notes, lookup_hadm)
            if mimic_notes:
                results["clinical_scribe"] = {
                    "source": "mimic_iv_note",
                    "queued": len(mimic_notes),
                    "delivery": "streamed_at_charttime",
                }
                return
            # Fallback: synthesise the admission note from cascade context
            # (SIM-only patients have no MIMIC narrative to stream).
            scribe_text = _build_encounter_text(patient, results)
            scribe_result = await self._safe_call(
                "clinical_scribe", "POST", "/generate-note/from-text",
                {"clinical_text": scribe_text,
                 "patient_id": patient.get("subject_id"),
                 "hadm_id": patient.get("hadm_id"),
                 "note_type": "admission_note",
                 "source": "synthetic"},
            )
            results["clinical_scribe"] = {
                "note_id": scribe_result.get("data", {}).get("note_id"),
                "quality_score": scribe_result.get("data", {}).get("quality_score"),
                "source": "synthetic",
            }

        # Run Oncology + Scribe concurrently — Step 4 and Step 5 are
        # mutually independent. With them serialized the pipeline took
        # ~5s per admission; in parallel the slower of the two dictates
        # the wall time.
        import asyncio as _aio_step45
        await _aio_step45.gather(
            _oncology_step(),
            _scribe_step(),
            return_exceptions=True,
        )

        # ── Step 6: Waiting List (Bug #5 + Integration 1) ──────────
        if acuity <= 3 and "waiting_list" not in self.disabled_modules:
            estimated_wait = results.get("ed_flow", {}).get("predicted_los_hours", 4) * 60
            await self._safe_call("waiting_list", "POST", "/notify-admission", {
                "hadm_id": patient.get("hadm_id"),
                "subject_id": patient.get("subject_id"),
                "acuity": acuity,
                "estimated_wait_min": int(estimated_wait),
                "pathway": disposition,
                "department": patient.get("department"),
                "age": patient.get("age"),
                "gender": patient.get("gender"),
                "primary_diagnosis": (diagnoses or [{}])[0].get("long_title") if diagnoses else None,
                "icd_codes": [str(d.get("icd_code", "")).upper() for d in diagnoses] if diagnoses else [],
            })
            results["waiting_list_notified"] = True

        # ── Step 7: Sepsis ICU screen (Bug #2 + Integration 1) ─────
        sofa_score = (
            results.get("bed_management", {}).get("sofa_score")
            or results.get("ed_triage", {}).get("sofa")
            or 0
        )
        icd_codes = [str(d.get("icd_code", "")).upper() for d in diagnoses] if diagnoses else []
        sepsis_codes_present = any(c.startswith(("A40", "A41", "R65")) for c in icd_codes)
        if (sofa_score > 0 or sepsis_codes_present) and "sepsis_icu" not in self.disabled_modules:
            sepsis_result = await self._safe_call("sepsis_icu", "POST", "/sepsis-screen", {
                "hadm_id": patient.get("hadm_id"),
                "subject_id": patient.get("subject_id"),
                "age": patient.get("age", 65),
                "gender": patient.get("gender", "M"),
                "vitals": patient.get("vitals", {}),
                "labs": patient.get("labs", {}),
                "care_unit": patient.get("department"),
            })
            results["sepsis_icu"] = sepsis_result.get("data", {})
            # Integration 5 — Sepsis → Bed Mgmt escalation
            alert = results["sepsis_icu"].get("alert_level", "").upper()
            if alert in ("ORANGE", "RED"):
                await self._safe_call("bed_management", "POST", "/escalate-bed-priority", {
                    "hadm_id": patient.get("hadm_id"),
                    "reason": f"sepsis_alert_{alert.lower()}",
                    "bump": 0.5,
                })

        # ── Step 8: Publish admission events ──
        # patient_admitted = bare admission notification consumed by
        # alerts / patient_journey / ed_flow / hospital_ops as a "the
        # patient now exists" trigger. admission_complete is the heavier
        # downstream event with full context once every module has run.
        # Both are needed; pre-fix only admission_complete was published,
        # so the medai.patient_admitted topic had zero events despite
        # multiple subscribers.
        try:
            await self._publish("patient_admitted", {
                "hadm_id": hadm_id,
                "subject_id": patient.get("subject_id"),
                "department": patient.get("department"),
                "admission_type": patient.get("admission_type"),
                "acuity": acuity,
                "admittime": patient.get("admittime"),
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug("patient_admitted_publish_failed: %s", exc)

        try:
            await self._publish("admission_complete", {
                "hadm_id": hadm_id,
                # Hoist core identifiers to the top level so subscribers
                # (hospital_ops mirror, ed_flow notify-bed-allocated, alerts)
                # don't have to know that patient_context is the bag they're
                # nested in. The audit found every event_log entry had
                # subject=None, dept=None because subscribers only read the
                # top level.
                "subject_id": patient.get("subject_id"),
                "department": patient.get("department"),
                "admission_type": patient.get("admission_type"),
                "patient_context": self.patient_context[hadm_id],
                "module_results": results,
                "acuity": acuity,
                "disposition": disposition,
                "pet_breach_risk": pet_risk,
                "modules_triggered": list(results.keys()),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("admission_complete_publish_failed hadm=%s: %s", hadm_id, exc)

        try:
            # Legacy event name for compatibility
            await self._publish("digital_twin_admission_complete", {
                "hadm_id": hadm_id,
                "subject_id": patient.get("subject_id"),
                "department": patient.get("department"),
                "acuity": acuity,
                "disposition": disposition,
                "pet_breach_risk": pet_risk,
                "modules_triggered": list(results.keys()),
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug("digital_twin_admission_complete_publish_failed: %s", exc)

        self._pipeline_results.setdefault(hadm_id, []).append(results)
        logger.info("  Digital Twin: Admission pipeline complete for %s (%d modules)",
                     hadm_id, len(results))
        return results

    # ------------------------------------------------------------------
    # 2. VITAL SIGN EVENT — update ED Flow + Bed Mgmt + accumulate
    # ------------------------------------------------------------------
    async def process_vital(self, vital: Dict[str, Any]) -> Dict[str, Any]:
        """Process a new vital — Rule 3 cascade via asyncio.gather.

        Propagates to ED Flow, Sepsis ICU (if ICU/HDU or prior SOFA > 0),
        Bed Management (if admitted), Clinical Scribe (update-vitals), and
        Deterioration Monitor (NEWS2 for non-ICU inpatients). All calls run
        concurrently with a 2-second per-call timeout.

        Wrapped in a parent OTel span so the four child httpx calls roll
        up under one trace in Jaeger. (Closes N4: previously this code-path
        ran outside any span context, so each httpx call became its own
        orphan single-span trace.)
        """
        hadm_id = str(vital.get("hadm_id", "unknown"))
        try:
            tracer = get_tracer("digital_twin")
            cascade_cm = tracer.start_as_current_span(
                "dt.process_vital",
                attributes={
                    "patient.hadm_id": hadm_id,
                    "vital.name": str(vital.get("vital_name") or ""),
                },
            )
        except Exception:  # noqa: BLE001
            from contextlib import nullcontext
            cascade_cm = nullcontext()
        with cascade_cm:
            return await self._process_vital_traced(vital, hadm_id)

    async def _process_vital_traced(self, vital: Dict[str, Any], hadm_id: str) -> Dict[str, Any]:
        sid = vital.get("subject_id", 0)
        vital_name = vital.get("vital_name") or _VITAL_ITEMID_MAP.get(vital.get("itemid"))
        value = vital.get("valuenum")
        results: Dict[str, Any] = {"hadm_id": hadm_id, "event": "vital"}

        # Discharged patients still receive vital_drift ticks from the
        # simulator engine. Skip the entire cascade for them — there's no
        # value in screening, predicting discharge, or annotating notes for
        # someone who has already left.
        if hadm_id in self.discharged_hadms:
            return {"hadm_id": hadm_id, "event": "vital", "skipped": "discharged"}

        # Lazily create a patient_context shell for rehydrated (pre-DT)
        # patients so their vitals still reach NEWS2 / PEWS / IMEWS etc.
        # Without this, process_vital would see ``ctx = {}`` for every
        # admission that existed before the DT was instantiated and the
        # deterioration / sepsis gates would never fire.
        if hadm_id not in self.patient_context:
            self.patient_context[hadm_id] = {
                "hadm_id": hadm_id,
                "subject_id": sid,
                "vitals": {},
                "labs": {},
                "rehydrated": True,
            }
        ctx = self.patient_context[hadm_id]
        if vital_name and value is not None:
            ctx.setdefault("vitals", {})[vital_name] = value

        raw_dept = ctx.get("current_department") or ctx.get("department") or ""
        current_dept = _map_dept(raw_dept) if raw_dept else ""
        is_icu_bound = current_dept in ("ICU", "HDU")
        is_admitted = bool(ctx.get("module_results", {}).get("bed_management"))
        prior_sofa = ctx.get("module_results", {}).get("sepsis_icu", {}).get("sofa_total", 0)

        # Build the concurrent call plan
        tasks: List[asyncio.Task] = []
        labels: List[str] = []

        # (a) ED Flow — always
        flow_event = {
            "event_type": "treatment",
            "timestamp": vital.get("sim_time"),
            "details": {
                "vital": vital_name,
                "value": value,
                "hadm_id": hadm_id,
                "subject_id": sid,
            },
        }
        tasks.append(asyncio.create_task(self._safe_call(
            "ed_flow", "POST", f"/patients/{sid}/event", flow_event, timeout_s=2.0,
        )))
        labels.append("ed_flow")

        # (b) Sepsis ICU — ICU/HDU or SOFA > 0 (Bug #2)
        if is_icu_bound or prior_sofa > 0:
            tasks.append(asyncio.create_task(self._safe_call(
                "sepsis_icu", "POST", "/screen", {
                    "hadm_id": hadm_id,
                    "subject_id": sid,
                    "vitals": ctx.get("vitals", {}),
                    "labs": ctx.get("labs", {}),
                    "care_unit": current_dept or "Medicine",
                }, timeout_s=2.0,
            )))
            labels.append("sepsis_icu")

        # (c) Bed Management — admitted only, throttled to one
        #     /predict-discharge call per patient every 5 vital ticks.
        #     The previous design fired on every vital, which produced
        #     ~25 discharge_predicted events per patient — most of them
        #     redundant since vitals don't change discharge readiness on
        #     a sub-tick basis. Throttling cuts the bus volume by 5×
        #     while still re-evaluating frequently enough that a real
        #     change in clinical state propagates within ~5 vital cycles.
        if is_admitted:
            tick = ctx.get("_predict_discharge_tick", 0) + 1
            ctx["_predict_discharge_tick"] = tick
            if tick % 5 == 1:
                tasks.append(asyncio.create_task(self._safe_call(
                    "bed_management", "POST", "/predict-discharge", {
                        "patient_id": sid, "hadm_id": hadm_id,
                        "department": ctx.get("current_department", "Medicine"),
                        "admission_time": ctx.get("admittime", _now_iso()),
                        "age": ctx.get("age", 65), "gender": ctx.get("gender", "U"),
                        "current_vitals": ctx.get("vitals", {}),
                        "current_labs": ctx.get("labs", {}),
                        "has_iv": False, "has_oxygen": False, "procedures_pending": 0,
                    }, timeout_s=2.0,
                )))
                labels.append("bed_management")

        # (d) Clinical Scribe update-vitals (Rule 3)
        tasks.append(asyncio.create_task(self._safe_call(
            "clinical_scribe", "POST", "/update-vitals", {
                "hadm_id": hadm_id,
                "subject_id": sid,
                "vital": vital_name,
                "value": value,
                "timestamp": vital.get("sim_time"),
            }, timeout_s=2.0,
        )))
        labels.append("clinical_scribe")

        # (e) Deterioration Monitor — always for non-ICU patients. The
        # service auto-routes to NEWS2 / PEWS / IMEWS based on age and
        # pregnancy metadata in the payload; ICU / HDU skip because they
        # have continuous hemodynamic monitoring by protocol. We drop the
        # prior ``is_admitted`` gate so rehydrated patients (pre-DT
        # admissions replayed via the vital drift loop) also get screened.
        if not is_icu_bound:
            screen_payload: Dict[str, Any] = {
                "hadm_id": hadm_id,
                "subject_id": sid,
                "vitals": ctx.get("vitals", {}),
                "department": ctx.get("current_department") or "Medicine",
            }
            # Forward routing hints when known
            if ctx.get("age") is not None:
                screen_payload["age"] = ctx.get("age")
            if ctx.get("age_months") is not None:
                screen_payload["age_months"] = ctx.get("age_months")
            if ctx.get("gestation_weeks") is not None:
                screen_payload["gestation_weeks"] = ctx.get("gestation_weeks")
            if ctx.get("post_partum_days") is not None:
                screen_payload["post_partum_days"] = ctx.get("post_partum_days")
            tasks.append(asyncio.create_task(self._safe_call(
                "deterioration", "POST", "/deterioration/screen",
                screen_payload, timeout_s=2.0,
            )))
            labels.append("deterioration")

        # Cap concurrent fan-out to keep downstream services from being
        # flattened. The semaphore is acquired here (around the gather)
        # rather than inside _safe_call so a single vital still gets all
        # its modules called in parallel — but two vitals can't issue
        # 2× the call rate. See _vital_semaphore docstring.
        async with self._vital_semaphore:
            gathered = await asyncio.gather(*tasks, return_exceptions=True)
        for label, res in zip(labels, gathered):
            if isinstance(res, Exception):
                results[label] = {"status": "error", "error": str(res)}
            else:
                results[label] = res.get("data", res) if isinstance(res, dict) else res

        # Downstream reactions — pet_breach_risk has a per-patient cooldown
        # gate to match the one in app_14_ed_flow's track-event handler.
        # Without it, every vital tick for a high-risk patient republished
        # the same alert (~19 events/min steady-state at 220 patients).
        pet_risk = (results.get("ed_flow") or {}).get("pet_breach_risk", 0) or 0
        if pet_risk > 0.75:
            from datetime import datetime as _dt
            now_ts = _dt.utcnow().timestamp()
            if not hasattr(self, "_pet_alert_state"):
                self._pet_alert_state: Dict[str, Dict[str, Any]] = {}
            prev = self._pet_alert_state.get(hadm_id)
            COOLDOWN_S = 60.0
            should_publish = prev is None or (now_ts - prev.get("ts", 0)) >= COOLDOWN_S
            if should_publish:
                await self._publish("pet_breach_risk", {
                    "hadm_id": hadm_id, "risk": pet_risk,
                })
                # Bug #8 — HTTP push to Hospital Ops (also gated on cooldown
                # so we don't keep hammering the same endpoint on every tick).
                await self._safe_call("hospital_ops", "POST", "/notify-pet-risk", {
                    "patient_sid": sid,
                    "hadm_id": hadm_id,
                    "wait_time_min": (results.get("ed_flow") or {}).get("wait_time_min"),
                    "mts_category": (results.get("ed_flow") or {}).get("mts_category"),
                    "risk": pet_risk,
                })
                self._pet_alert_state[hadm_id] = {"ts": now_ts}
        else:
            # Below threshold — clear memory so the next crossing republishes.
            if hasattr(self, "_pet_alert_state"):
                self._pet_alert_state.pop(hadm_id, None)

        # Sepsis escalation from the streaming /screen result
        sepsis_alert = (results.get("sepsis_icu") or {}).get("alert_level", "").upper()
        if sepsis_alert in ("ORANGE", "RED"):
            await self._safe_call("bed_management", "POST", "/escalate-bed-priority", {
                "hadm_id": hadm_id, "reason": f"sepsis_alert_{sepsis_alert.lower()}",
                "bump": 0.5,
            })

        return results

    # ------------------------------------------------------------------
    # 3. LAB RESULT EVENT — update ED Flow + Bed Mgmt + accumulate
    # ------------------------------------------------------------------
    async def process_lab(self, lab: Dict[str, Any]) -> Dict[str, Any]:
        """Process a lab result — propagate to ED Flow, Bed Management."""
        hadm_id = str(lab.get("hadm_id", "unknown"))
        sid = lab.get("subject_id", 0)
        lab_name = lab.get("lab_name") or _LAB_ITEMID_MAP.get(lab.get("itemid"))
        value = lab.get("valuenum")
        results: Dict[str, Any] = {"hadm_id": hadm_id, "event": "lab"}

        # Accumulate in patient context
        ctx = self.patient_context.get(hadm_id)
        if ctx and lab_name and value is not None:
            ctx.setdefault("labs", {})[lab_name] = value

        # 1. Update ED Flow
        flow_event = {
            "event_type": "labs_resulted",
            "timestamp": lab.get("sim_time"),
            "details": {
                "lab": lab_name,
                "value": value,
                "hadm_id": hadm_id,
                "subject_id": sid,
            },
        }
        flow_result = await self._safe_call(
            "ed_flow", "POST", f"/patients/{sid}/event", flow_event,
        )
        results["ed_flow"] = flow_result.get("data", {})

        return results

    # ------------------------------------------------------------------
    # 3b. NOTE EVENT — stream a single MIMIC clinical note at its real charttime
    # ------------------------------------------------------------------
    async def process_note(self, note: Dict[str, Any]) -> Dict[str, Any]:
        """Forward one streamed note to clinical_scribe at its real sim_time.

        Replaces the prior batch ingest at admission (where every note for
        the patient was sent to scribe in a single burst regardless of when
        it was originally written). Now each note arrives at the offset
        MIMIC recorded as ``charttime``, so the platform sees clinical
        narrative unfold the way it would on a real ward — vitals first,
        then progress notes, then a discharge summary.
        """
        hadm_id = str(note.get("hadm_id", "unknown"))
        sid = note.get("subject_id", 0)
        results: Dict[str, Any] = {"hadm_id": hadm_id, "event": "note"}

        if "clinical_scribe" in self.disabled_modules:
            return results

        # Skip if the patient has already been discharged (see process_vital).
        # Late-arriving notes for completed admissions still get persisted in
        # data_ingestion's MIMIC_SIM.notes, but we don't re-wake the cascade.
        if hadm_id in self.discharged_hadms:
            results["skipped"] = "discharged"
            return results

        body = {
            "clinical_text": note.get("text") or "",
            "patient_id": sid,
            "hadm_id": note.get("original_hadm_id") or note.get("hadm_id"),
            "note_type": _scribe_note_type_for(note.get("note_type")),
            "specialty": _infer_specialty(
                (self.patient_context.get(hadm_id) or {}).get("diagnoses")
            ),
            "source": "mimic_iv_note",
            "original_note_id": str(note.get("note_id") or ""),
            "original_charttime": str(note.get("charttime") or ""),
            "sim_time": note.get("charttime"),
        }
        scribe_result = await self._safe_call(
            "clinical_scribe", "POST", "/generate-note/from-text", body,
        )
        results["clinical_scribe"] = scribe_result.get("data", {}) or {}

        # Publish a `note_streamed` event so any cross-process consumer
        # (audit trail, downstream NLP services, dashboard subscribers)
        # can react to the note as a first-class event. Previously the
        # only Kafka topic touched by note flow was ``note_generated``,
        # which is the scribe's downstream ack — no upstream "a clinical
        # note was just streamed" signal existed.
        try:
            await self._publish("note_streamed", {
                "hadm_id": hadm_id,
                "subject_id": sid,
                "note_id": note.get("note_id"),
                "note_type": note.get("note_type"),
                "charttime": note.get("charttime"),
                "scribe_note_id": (results["clinical_scribe"] or {}).get("note_id"),
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug("note_streamed_publish_failed: %s", exc)
        return results

    # ------------------------------------------------------------------
    # 4. TRANSFER EVENT — update Bed Management + ED Flow + Hospital Ops
    # ------------------------------------------------------------------
    async def process_transfer(self, transfer: Dict[str, Any]) -> Dict[str, Any]:
        """Process a patient transfer — Rule 4 cascade (bed, ED flow, sepsis, ops)."""
        hadm_id = str(transfer.get("hadm_id", "unknown"))
        sid = transfer.get("subject_id", 0)
        raw_careunit = transfer.get("careunit", "Unknown")
        # Map MIMIC raw careunit (e.g. "Surgical Intensive Care Unit (SICU)") to Irish HSE dept code
        careunit = _map_dept(raw_careunit) if raw_careunit else raw_careunit
        results: Dict[str, Any] = {"hadm_id": hadm_id, "event": "transfer"}

        # Update patient context
        ctx = self.patient_context.get(hadm_id)
        if ctx:
            ctx["current_department"] = careunit
            ctx["raw_careunit"] = raw_careunit

        # 1. Notify ED Flow (patient may have left ED)
        flow_event = {
            "event_type": "transfer",
            "timestamp": transfer.get("sim_time"),
            "details": {
                "to_department": careunit,
                "hadm_id": transfer.get("hadm_id"),
                "subject_id": sid,
            },
        }
        await self._safe_call("ed_flow", "POST", f"/patients/{sid}/event", flow_event)

        # 2. Notify Bed Management via HTTP
        await self._safe_call("bed_management", "POST", "/notify-transfer", {
            "hadm_id": transfer.get("hadm_id"),
            "subject_id": sid,
            "to_department": careunit,
        })

        # 3. Bug #2 — Sepsis ICU admit handoff when destination is ICU/HDU
        if careunit in ("ICU", "HDU"):
            vitals = (ctx or {}).get("vitals", {})
            labs = (ctx or {}).get("labs", {})
            await self._safe_call("sepsis_icu", "POST", "/admit-patient", {
                "hadm_id": transfer.get("hadm_id"),
                "subject_id": sid,
                "care_unit": careunit,
                "raw_careunit": raw_careunit,
                "vitals": vitals,
                "labs": labs,
                "age": (ctx or {}).get("age", 65),
                "gender": (ctx or {}).get("gender", "M"),
            })

        # 4. Publish for in-process subscribers + Kafka fanout (via outbox)
        await self._publish("patient_transferred", {
            "hadm_id": hadm_id, "subject_id": sid,
            "to_department": careunit,
            "raw_careunit": raw_careunit,
        })

        return results

    # ------------------------------------------------------------------
    # 5. DISCHARGE EVENT — finalize predictions, update all modules
    # ------------------------------------------------------------------
    async def process_discharge(self, discharge: Dict[str, Any]) -> Dict[str, Any]:
        """Process patient discharge — Rule 5 complete 6-step cascade."""
        hadm_id = str(discharge.get("hadm_id", "unknown"))
        sid = discharge.get("subject_id", 0)
        results: Dict[str, Any] = {"hadm_id": hadm_id, "event": "discharge"}

        ctx = self.patient_context.get(hadm_id, {})

        # Step 1 — Bed Mgmt: free the bed (idempotent — retries via circuit-breaker
        # half-open or Digital Twin re-dispatch won't re-free a reallocated bed).
        idem_key = f"discharge:{hadm_id}"
        await self._safe_call(
            "bed_management", "POST", f"/notify-discharge/{hadm_id}", {},
            idempotency_key=idem_key,
        )
        if sid:
            await self._safe_call(
                "bed_management", "POST", f"/notify-discharge/{sid}", {},
                idempotency_key=f"discharge:{hadm_id}:sid:{sid}",
            )

        # Step 2 — ED Flow: remove patient
        flow_event = {
            "event_type": "discharged",
            "timestamp": discharge.get("sim_time"),
            "details": {
                **discharge,
                "hadm_id": discharge.get("hadm_id"),
                "subject_id": sid,
            },
        }
        await self._safe_call("ed_flow", "POST", f"/patients/{sid}/event", flow_event)

        # Step 3 — Patient Journey finalize (Integration 2)
        await self._safe_call("patient_journey", "POST", f"/journey/finalize/{hadm_id}", {
            "hadm_id": hadm_id,
            "subject_id": sid,
            "discharge_location": discharge.get("discharge_location"),
            "event_log": ctx.get("events", []),
            "module_results": ctx.get("module_results", {}),
            "sim_time": discharge.get("sim_time"),
        })

        # Step 4 — Publish patient_discharged (via outbox for durability)
        await self._publish("patient_discharged", {
            "hadm_id": hadm_id,
            "subject_id": sid,
            "discharge_location": discharge.get("discharge_location"),
        })

        # Step 5 — Waiting List re-rank
        await self._safe_call("waiting_list", "POST", "/notify-discharge-re-rank", {
            "hadm_id": hadm_id,
            "department": ctx.get("current_department"),
        })

        # Step 6 — Hospital Ops census is refreshed via Bed Mgmt debouncer on next /beds/summary poll.
        # Explicit notify in case Bed Mgmt /beds/summary isn't hit soon.
        await self._safe_call("hospital_ops", "POST", "/notify-discharge-prediction", {
            "hadm_id": hadm_id,
            "finalized": True,
            "discharge_location": discharge.get("discharge_location"),
            "department": ctx.get("current_department"),
        })

        # Discharge summary note. Prefer the real MIMIC-IV-Note discharge
        # summary if one exists for this hadm_id; fall back to synthesised text.
        mimic_ds = _fetch_mimic_notes(discharge.get("hadm_id"))
        if mimic_ds:
            ds = mimic_ds[-1]  # latest charttime is the discharge doc
            await self._safe_call(
                "clinical_scribe", "POST", "/generate-note/from-text",
                {"clinical_text": ds.get("text") or "",
                 "patient_id": sid,
                 "hadm_id": discharge.get("hadm_id"),
                 "note_type": "discharge_summary",
                 "source": "mimic_iv_note",
                 "original_note_id": str(ds.get("note_id") or ""),
                 "original_charttime": str(ds.get("charttime") or "")},
            )
        else:
            summary_text = _build_discharge_summary(ctx, discharge)
            await self._safe_call(
                "clinical_scribe", "POST", "/generate-note/from-text",
                {"clinical_text": summary_text, "patient_id": sid,
                 "hadm_id": discharge.get("hadm_id"),
                 "note_type": "discharge_summary",
                 "source": "synthetic"},
            )

        # Elective follow-up candidate
        if str(discharge.get("discharge_location", "")).upper() in ("HOME", "HOME HEALTH CARE"):
            results["waiting_list_candidate"] = True

        # Sláintecare integrated-care candidate (Item 6.1)
        pathway = [e.get("to_department") for e in ctx.get("events", []) if e.get("event_type") == "transfer"]
        if "Medicine" in pathway and "Discharge_Lounge" in pathway:
            await self._safe_call("discharge_lounge", "POST", "/community-referral", {
                "hadm_id": hadm_id,
                "subject_id": sid,
                "reason": "slaintecare_pathway",
            })
            results["community_referral_candidate"] = True

        # Clean up context + remember this hadm so post-discharge vital
        # drift events don't re-trigger the cascade. Bounded to keep
        # memory flat over a long-running simulation.
        self.patient_context.pop(hadm_id, None)
        if hadm_id not in self.discharged_hadms:
            self.discharged_hadms.add(hadm_id)
            self._discharged_order.append(hadm_id)
            while len(self._discharged_order) > 10000:
                evicted = self._discharged_order.pop(0)
                self.discharged_hadms.discard(evicted)
        logger.info("  Digital Twin: Discharge complete for %s", hadm_id)
        return results

    # ------------------------------------------------------------------
    # 6. BATCH SIMULATION — run N patients through full lifecycle
    # ------------------------------------------------------------------
    async def simulate_batch(
        self,
        patients: List[Dict[str, Any]],
        include_vitals: bool = True,
        include_labs: bool = True,
    ) -> Dict[str, Any]:
        """Run a batch of patients through the full digital twin pipeline.

        Useful for demo, testing, and what-if analysis.

        Returns aggregate metrics from all modules.
        """
        logger.info("Digital Twin: Simulating batch of %d patients", len(patients))
        all_results = []

        for i, patient in enumerate(patients):
            try:
                result = await self.process_admission(patient)
                all_results.append(result)
            except Exception as e:
                logger.error("  Error processing patient %d: %s", i, e)
                all_results.append({"error": str(e)})

            if (i + 1) % 10 == 0:
                logger.info("  Processed %d/%d patients", i + 1, len(patients))

        # Aggregate metrics
        successful = [r for r in all_results if "error" not in r]
        metrics = {
            "total_patients": len(patients),
            "successful": len(successful),
            "failed": len(patients) - len(successful),
            "avg_acuity": _safe_avg([r.get("ed_triage", {}).get("acuity_level") for r in successful]),
            "avg_pet_risk": _safe_avg([r.get("ed_flow", {}).get("pet_breach_risk") for r in successful]),
            "admit_rate": _safe_avg([
                1 if r.get("ed_triage", {}).get("disposition") == "admit_to_inpatient" else 0
                for r in successful
            ]),
            "beds_allocated": sum(
                1 for r in successful if r.get("bed_allocation", {}).get("recommended_bed")
            ),
            "notes_generated": sum(
                1 for r in successful if r.get("clinical_scribe", {}).get("note_id")
            ),
        }

        logger.info("Digital Twin batch complete: %s", metrics)
        return {"metrics": metrics, "results": all_results}

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------
    def get_patient_context(self, hadm_id: str) -> Dict[str, Any]:
        """Return accumulated context for a patient across all modules."""
        return self.patient_context.get(hadm_id, {})

    def get_pipeline_results(self, hadm_id: str) -> List[Dict]:
        """Return all pipeline results for a patient."""
        return self._pipeline_results.get(hadm_id, [])

    # Cache for /digital-twin/state — same pattern as data_ingestion's /state.
    # Dashboard polls this every 2-5s; the underlying view fans out to 3
    # downstream services + health-probes all modules, so without a cache
    # each poll would do 20+ network round-trips on the request path.
    _SYSTEM_STATE_CACHE: Dict[str, Any] = {"value": None, "expires": 0.0}
    _SYSTEM_STATE_TTL_S = 2.5
    _SYSTEM_STATE_TIMEOUT_S = 2.0

    async def get_system_state(self) -> Dict[str, Any]:
        """Query all modules for current state — full digital twin snapshot.

        Parallelised (asyncio.gather) and cached (2.5 s TTL). Previously
        this ran 3 downstream calls serially with no timeout, so the
        Simulation page's poll could stall 5-20 s whenever any one of
        ed_flow / bed_management / the health fan-out went slow. (Closes
        the residual /digital-twin/state spike found 2026-05-25.)
        """
        import time as _time

        now = _time.monotonic()
        cached = self._SYSTEM_STATE_CACHE
        if cached["value"] is not None and now < cached["expires"]:
            return cached["value"]

        T = self._SYSTEM_STATE_TIMEOUT_S

        async def _bounded(coro):
            try:
                return await asyncio.wait_for(coro, timeout=T)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                return None

        ed_resp, bed_resp, health = await asyncio.gather(
            _bounded(self.client.ed_flow.get("/ed-state")),
            _bounded(self.client.bed_management.get("/beds/summary")),
            _bounded(self.client.health_check_all()),
        )

        state = {
            "ed": (ed_resp or {}).get("data", {}) if isinstance(ed_resp, dict) else {},
            "beds": (bed_resp or {}).get("data", []) if isinstance(bed_resp, dict) else [],
            "active_patients": len(self.patient_context),
            "total_processed": sum(len(v) for v in self._pipeline_results.values()),
            "module_health": health or {},
        }

        cached["value"] = state
        cached["expires"] = now + self._SYSTEM_STATE_TTL_S
        return state


# ── Helper functions ──────────────────────────────────────────────────

def _now_iso() -> str:
    """SimClock-derived ISO timestamp so all orchestration events share a clock."""
    return SimClock.get_instance().get_sim_time().isoformat()


def _safe_avg(values: List) -> float:
    nums = [v for v in values if v is not None and isinstance(v, (int, float))]
    return round(sum(nums) / len(nums), 3) if nums else 0.0


def _build_triage_input(patient: Dict) -> Dict:
    """Convert patient data to ED Triage API input format."""
    vitals = patient.get("vitals", {})
    labs = patient.get("labs", {})
    return {
        "age": patient.get("age", 65),
        "gender": patient.get("gender", "M"),
        "heart_rate": vitals.get("heart_rate"),
        "respiratory_rate": vitals.get("respiratory_rate"),
        "spo2": vitals.get("spo2"),
        "sbp": vitals.get("sbp"),
        "dbp": vitals.get("dbp"),
        "temperature": vitals.get("temperature"),
        "wbc": labs.get("wbc"),
        "hemoglobin": labs.get("hemoglobin"),
        "lactate": labs.get("lactate"),
        "glucose": labs.get("glucose"),
        "creatinine": labs.get("creatinine"),
        "arrival_mode": patient.get("admission_location", "EMERGENCY ROOM"),
    }


# Module-level shared MongoManager for note replay. Lazily initialised so
# importing this module doesn't open a Mongo connection on services that
# don't need one. Reset to None to force a re-open after a Mongo restart.
_NOTES_MGR = None


def _fetch_mimic_notes(hadm_id) -> List[Dict[str, Any]]:
    """Return MIMIC-IV-Note discharge summaries for a hadm_id, oldest first.

    Returns [] for SIM-only patients (their hadm_ids don't exist in MIMIC).
    """
    global _NOTES_MGR
    if hadm_id is None:
        return []
    try:
        if _NOTES_MGR is None:
            from shared.db.mongo import MongoManager
            _NOTES_MGR = MongoManager()
        return _NOTES_MGR.notes_for_admission(hadm_id, limit=5)
    except Exception as exc:  # noqa: BLE001
        logger.debug("digital_twin notes fetch failed for hadm=%s: %s", hadm_id, exc)
        return []


# Map MIMIC-IV-Note types ("DS" = discharge summary, "RR" = radiology) to the
# scribe note_type vocabulary used by IRISH_NOTE_TYPES on app_10's schemas.
_MIMIC_TO_SCRIBE_NOTE_TYPE = {
    "DS": "discharge_summary",
    "RR": "procedure_note",  # closest fit; radiology not yet a first-class type
}


def _scribe_note_type_for(mimic_note_type: Optional[str]) -> str:
    return _MIMIC_TO_SCRIBE_NOTE_TYPE.get((mimic_note_type or "").upper(), "admission_note")


def _infer_specialty(diagnoses: Optional[List[Dict]]) -> Optional[str]:
    """Best-effort specialty inference from diagnosis ICD prefixes."""
    if not diagnoses:
        return None
    first = (diagnoses[0] or {}).get("icd_code", "")
    if not first:
        return None
    prefix = str(first)[:1].upper()
    return {
        "I": "cardiology", "J": "respiratory", "K": "gastroenterology",
        "C": "oncology", "N": "nephrology", "G": "neurology",
        "F": "psychiatry", "M": "musculoskeletal", "S": "trauma",
    }.get(prefix)


def _build_encounter_text(patient: Dict, results: Dict) -> str:
    """Build encounter text from patient data + module results for scribe."""
    parts = []
    parts.append(f"Patient: {patient.get('age', 'unknown')} year old {patient.get('gender', 'unknown')}")
    parts.append(f"Admission type: {patient.get('admission_type', 'unknown')}")

    triage = results.get("ed_triage", {})
    if triage:
        parts.append(f"Triage acuity: {triage.get('acuity_level', 'unknown')}")
        parts.append(f"Disposition: {triage.get('disposition', 'unknown')}")
        risk_factors = triage.get("risk_factors", [])
        if risk_factors:
            parts.append(f"Risk factors: {', '.join(risk_factors)}")

    vitals = patient.get("vitals", {})
    if vitals:
        vital_str = ", ".join(f"{k}={v}" for k, v in vitals.items() if v is not None)
        parts.append(f"Vitals: {vital_str}")

    labs = patient.get("labs", {})
    if labs:
        lab_str = ", ".join(f"{k}={v}" for k, v in labs.items() if v is not None)
        parts.append(f"Labs: {lab_str}")

    return ". ".join(parts)


def _build_discharge_summary(context: Dict, discharge: Dict) -> str:
    """Build discharge summary text from accumulated context."""
    parts = []
    parts.append(f"Discharge summary for patient {context.get('subject_id', 'unknown')}")
    parts.append(f"Admission type: {context.get('admission_type', 'unknown')}")
    parts.append(f"Discharge location: {discharge.get('discharge_location', 'unknown')}")

    module_results = context.get("module_results", {})
    triage = module_results.get("ed_triage", {})
    if triage:
        parts.append(f"ED triage acuity: {triage.get('acuity_level')}")
        parts.append(f"Disposition: {triage.get('disposition')}")

    bed = module_results.get("bed_management", {})
    if bed:
        parts.append(f"Predicted LOS: {bed.get('current_los_hours', 'unknown')} hours")

    return ". ".join(parts)
