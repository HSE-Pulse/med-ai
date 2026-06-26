"""Core hospital simulation engine.

Replays real MIMIC patient journeys on an accelerated simulation clock.
New patients arrive at a configurable rate (~30 per sim-day), and each
patient's full clinical journey (transfers, vitals, labs, medications,
diagnoses, procedures, discharge) is scheduled as time-offset events
on a priority queue.

Events are persisted to the ``MIMIC_SIM`` MongoDB database and broadcast
to connected WebSocket listeners in real time.
"""

from __future__ import annotations

import asyncio
import heapq
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# Demo LOS scaler: multiplies every MIMIC offset (transfer/vital/lab/med/note/
# discharge) by this factor so a 5-day MIMIC stay can finish inside a short
# demo window. Default 0.05 (= 20× compression) so a 5-day MIMIC stay
# finishes in ~6 sim-hours, which becomes ~36 wall-minutes at the default
# 10× sim speed — enough time to demo a full admission/discharge cycle
# without operators waiting hours. Set DEMO_LOS_SCALE=1.0 to use real
# MIMIC timing (e.g. for long-running soak tests). Bounded to (0, 1] so
# we never *expand* offsets.
try:
    _RAW_DEMO_LOS_SCALE = float(os.getenv("DEMO_LOS_SCALE", "0.05"))
except (TypeError, ValueError):
    _RAW_DEMO_LOS_SCALE = 0.05
DEMO_LOS_SCALE: float = max(1e-4, min(1.0, _RAW_DEMO_LOS_SCALE))

# Max sim-seconds an event may be scheduled into the future. Caps unbounded
# heap growth: each MIMIC admission schedules ~300 events spread over its
# real LOS; without this cap the queue grew 412 → 2452 in 8 wall-min during
# the audit. Default 7 sim-days = 604_800 s (covers any plausible demo).
try:
    _RAW_MAX_HORIZON = float(os.getenv("EVENT_MAX_SCHEDULE_HORIZON_S", "604800"))
except (TypeError, ValueError):
    _RAW_MAX_HORIZON = 604800.0
EVENT_MAX_SCHEDULE_HORIZON_S: float = max(60.0, _RAW_MAX_HORIZON)

_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from shared.db.mongo import MongoManager  # noqa: E402

from .sim_clock import SimClock  # noqa: E402
from .patient_generator import PatientGenerator  # noqa: E402

logger = logging.getLogger(__name__)

# ── MIMIC itemid → human-readable name mapping ──────────────────────

from shared.constants.mimic import VITAL_ITEMID_TO_NAME, LAB_ITEMID_TO_NAME

# ── date parsing ─────────────────────────────────────────────────────

from shared.utils.datetime import parse_time as _parse_time


# ── event queue entry ────────────────────────────────────────────────
# Tuples are compared element-wise; dicts are not comparable, so we add
# a monotonic sequence counter as a tie-breaker.

_seq_counter: int = 0


def _next_seq() -> int:
    global _seq_counter
    _seq_counter += 1
    return _seq_counter


# ── main engine ──────────────────────────────────────────────────────


class HospitalEventEngine:
    """Continuous hospital simulation engine backed by MIMIC data."""

    # Seconds between arrivals in sim-time.
    # 86400 / 120 = 720 admissions/day — well above realistic volume
    # but the goal here is "the demo is visibly alive at 1× speed". At
    # 1× that's one arrival every 2 wall-minutes; at 10× every 12 sec.
    # Patient stays span hours so the engine is never short of work.
    # Previously 240s (= 4 wall-min between arrivals at 1×) which felt
    # anaemic — a clinician watching the dashboard had to wait 4 min
    # to see a single new admission.
    ARRIVAL_INTERVAL_SIM = 120

    def __init__(
        self,
        mongo: MongoManager,
        clock: SimClock,
        generator: PatientGenerator,
        digital_twin: Optional[Any] = None,
    ) -> None:
        self.mongo = mongo
        self.clock = clock
        self.generator = generator
        self.sim_db = mongo.client["MIMIC_SIM"]
        self.digital_twin = digital_twin  # DigitalTwinOrchestrator (optional)

        self.active_patients: Dict[str, Dict[str, Any]] = {}
        self.event_queue: List[tuple] = []  # heap of (datetime, seq, type, data)
        self.listeners: List[Any] = []  # WebSocket connections
        self.running = False

        # Bounded fire-and-forget propagation so the digital-twin httpx
        # fan-out can never starve the asyncio event loop. Cap concurrent
        # propagations at 8 (chosen to match the typical thread-pool size
        # used by asyncio.to_thread for Mongo writes — see py-spy of
        # 2026-05-26 hang for rationale). Hard cap on PENDING tasks (i.e.
        # before they enter the semaphore) so an event burst can't queue
        # indefinitely.
        self._propagation_semaphore: asyncio.Semaphore = asyncio.Semaphore(8)
        self._propagation_pending: int = 0
        self._PROPAGATION_HARD_CAP: int = 200

        # Per-patient last-propagated vital timestamp (sim seconds) for
        # the throttle in ``_propagate_to_digital_twin``.
        self._last_vital_prop_sim: Dict[str, float] = {}

        self.stats = {
            "total_admissions": 0,
            "total_discharges": 0,
            "total_transfers": 0,
            "total_vitals": 0,
            "total_labs": 0,
            "total_meds": 0,
            "total_diagnoses": 0,
            "total_procedures": 0,
            "total_notes": 0,
        }
        # Liveness telemetry: set by _fire_event on every successful fire.
        # Lets /state consumers detect a stalled engine (queue not draining
        # despite having events <= sim clock) without polling the queue.
        self.last_fired_event_at_wall: Optional[datetime] = None
        self.last_fired_event_at_sim: Optional[datetime] = None
        self.last_fired_event_type: Optional[str] = None

        # Sim-time of the most recent admission — used by _arrival_loop to
        # honour ARRIVAL_INTERVAL_SIM. MUST be cleared on reset otherwise
        # the loop can stall when the reset sim clock lands before this
        # stale value (arrival rate calc produces negative elapsed time).
        self._last_admission_sim_time: Optional[datetime] = None

        self._tasks: List[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the continuous simulation loop."""
        if self.running:
            logger.warning("Engine already running.")
            return

        pool_size = self.generator.initialize(limit=500)
        if pool_size == 0:
            logger.error("No admissions found in MIMIC database -- cannot start.")
            return

        # Rehydrate previously-admitted patients so the drift loop keeps
        # their vital charts alive across service restarts. If an admission's
        # sim_admittime is ahead of the current clock (clock was reset), we
        # re-anchor it to now so the dashboard elapsed-time stays sensible.
        self._rehydrate_active_patients()

        self.running = True
        self._tasks = [
            asyncio.create_task(self._arrival_loop()),
            asyncio.create_task(self._event_processor()),
            asyncio.create_task(self._vital_drift_loop()),
        ]
        logger.info(
            "Simulation started (speed=%.1fx, pool=%d admissions).",
            self.clock.speed,
            pool_size,
        )

    async def stop(self) -> None:
        """Stop the simulation loop gracefully."""
        self.running = False
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        logger.info("Simulation stopped.")

    def _rehydrate_active_patients(self) -> None:
        """Re-populate ``active_patients`` from MongoDB after a restart.

        Looks at every admission in MIMIC_SIM.admissions whose status isn't
        "discharged" and re-anchors ``sim_admittime`` to current clock if it
        is in the future. This lets the vital drift loop keep generating
        data for patients that were already admitted before the restart.
        """
        now = self.clock.now()
        try:
            cursor = self.sim_db["admissions"].find(
                {"status": {"$ne": "discharged"}},
                {"_id": 0},
            )
        except Exception as exc:
            logger.warning("rehydrate: failed to query admissions: %s", exc)
            return

        rehydrated = 0
        for adm in cursor:
            sim_hadm = adm.get("hadm_id")
            if not sim_hadm:
                continue
            # Normalise stale sim_admittime so the journey window is sensible.
            admit_iso = adm.get("sim_admittime")
            try:
                if admit_iso:
                    admit_dt = datetime.fromisoformat(admit_iso.replace("Z", ""))
                    if admit_dt > now:
                        self.sim_db["admissions"].update_one(
                            {"hadm_id": sim_hadm},
                            {"$set": {"sim_admittime": now.isoformat()}},
                        )
                        adm["sim_admittime"] = now.isoformat()
            except Exception:
                pass
            self.active_patients[sim_hadm] = {
                "subject_id": adm.get("subject_id"),
                "hadm_id": sim_hadm,
                "original_hadm_id": adm.get("original_hadm_id"),
                "sim_admittime": adm.get("sim_admittime"),
                "status": adm.get("status", "admitted"),
                "admission_type": adm.get("admission_type"),
            }
            rehydrated += 1
        if rehydrated:
            logger.info("Rehydrated %d active patients from prior sim run.", rehydrated)

    # ------------------------------------------------------------------
    # Arrival loop
    # ------------------------------------------------------------------

    async def _arrival_loop(self) -> None:
        """Generate new patient arrivals at MIMIC-realistic rates.

        Each iteration is wrapped so a single faulty admission never silently
        kills the loop — a prior regression had this exact symptom (admissions
        would stop without a trace after one unhandled exception).
        """
        logger.info("arrival_loop started (interval=%ss sim at speed=%.1fx)",
                    self.ARRIVAL_INTERVAL_SIM, self.clock.speed)
        try:
            while self.running:
                arrival_interval_real = self.ARRIVAL_INTERVAL_SIM / max(self.clock.speed, 0.01)
                # Cap sleep at 5s so speed changes take effect quickly
                arrival_interval_real = min(arrival_interval_real, 5.0)
                await asyncio.sleep(arrival_interval_real)
                if not self.running:
                    return
                # Only fire an actual admission when enough sim-time has
                # elapsed since the last one (honours ARRIVAL_INTERVAL_SIM)
                now = self.clock.now()
                last = getattr(self, "_last_admission_sim_time", None)
                if last is not None:
                    sim_elapsed = (now - last).total_seconds()
                    if sim_elapsed < self.ARRIVAL_INTERVAL_SIM:
                        continue
                try:
                    await self._admit_next_patient()
                    self._last_admission_sim_time = now
                except Exception as exc:  # noqa: BLE001 — keep the loop alive
                    logger.exception("arrival_loop: _admit_next_patient failed: %s", exc)
        except asyncio.CancelledError:
            logger.info("arrival_loop cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("arrival_loop died: %s", exc)

    # ------------------------------------------------------------------
    # Vital drift loop — keeps vitals moving for live charts
    # ------------------------------------------------------------------

    # Per-itemid baseline + noise envelope used when a random walk produces a
    # value outside physiological range (clamp back toward the mean).
    _VITAL_BASELINES: Dict[int, tuple] = {
        220045: (80, 15, 40, 180),    # HR
        220210: (16, 3,  8,  34),     # RR
        220277: (96, 2,  85, 100),    # SpO2
        220179: (120, 15, 70, 210),   # SBP
        220180: (75, 10, 40, 130),    # DBP
        223761: (37.0, 0.4, 34.5, 41.0),  # Temp (°C)
    }

    async def _vital_drift_loop(self) -> None:
        """Emit a fresh vital reading for every active patient every ~30 sim-min.

        MIMIC's charttime offsets are sparse (gaps of hours); this ticker
        interpolates a realistic random walk so live charts stay alive and
        downstream modules (NEWS2, Sepsis screen) get fresh data to react to.
        Each tick emits one vital per patient so the total event-rate stays
        modest. Values random-walk from the last observed reading with a
        mean-reverting pull toward the MIMIC baseline.
        """
        import random as _random

        DRIFT_INTERVAL_SIM = 15 * 60  # 15 sim-minutes between ticks
        # In-memory cache of last (hadm, itemid) -> valuenum so we don't
        # do a sync Mongo find_one on the event loop for every patient ×
        # vital every tick. The cache is updated whenever we emit a new
        # value (random walk continues from the cached value), and
        # populated lazily via asyncio.to_thread on first miss.
        last_vital: Dict[tuple, float] = {}
        try:
            while self.running:
                try:
                    # Sleep in real-time based on current speed. Cap sleep at ~5s
                    # so speed changes take effect quickly.
                    await asyncio.sleep(
                        max(0.5, min(5.0, DRIFT_INTERVAL_SIM / max(1.0, self.clock.speed)))
                    )
                    if not self.running:
                        return
                    now = self.clock.now()
                    for sim_hadm, patient in list(self.active_patients.items()):
                        for itemid, (mean, std, lo, hi) in self._VITAL_BASELINES.items():
                            key = (sim_hadm, itemid)
                            last = last_vital.get(key)
                            if last is None:
                                # First-touch: look up the last persisted
                                # value off the event loop so the loop
                                # doesn't block on Mongo. Subsequent ticks
                                # reuse the cache.
                                try:
                                    doc = await asyncio.to_thread(
                                        self.sim_db["chartevents"].find_one,
                                        {"hadm_id": sim_hadm, "itemid": itemid},
                                        {"valuenum": 1, "_id": 0},
                                    )
                                    if doc and doc.get("valuenum") is not None:
                                        last = float(doc["valuenum"])
                                except Exception as exc:
                                    logger.debug("drift cache prime failed: %s", exc)
                                    last = None
                            base = last if last is not None else mean
                            step = _random.gauss(0, std * 0.3) + (mean - base) * 0.08
                            new_val = round(max(lo, min(hi, base + step)), 1)
                            last_vital[key] = new_val
                            payload = {
                                "hadm_id": sim_hadm,
                                "subject_id": patient.get("subject_id"),
                                "itemid": itemid,
                                "valuenum": new_val,
                                "charttime": now.isoformat(),
                            }
                            try:
                                await self._fire_event("vital", payload, now)
                            except asyncio.CancelledError:
                                raise
                            except Exception as e:
                                logger.debug("drift vital fire error: %s", e)
                    # Drop cache entries for departed patients so we don't leak.
                    if len(last_vital) > 0:
                        live = set(self.active_patients.keys())
                        for k in [k for k in last_vital if k[0] not in live]:
                            last_vital.pop(k, None)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.exception("vital_drift_loop: tick failed, will retry: %s", exc)
                    await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            logger.info("vital_drift_loop cancelled")

    async def _admit_next_patient(self) -> None:
        """Pull the next MIMIC patient, remap times to sim clock, schedule events."""
        adm = self.generator.next_patient()
        if adm is None:
            return

        sid = adm.get("subject_id")
        hadm = adm.get("hadm_id")
        now = self.clock.now()

        # Fetch the complete MIMIC clinical journey OFF the event loop —
        # this issues 6 synchronous PyMongo cursor reads (chartevents,
        # labevents, prescriptions, diagnoses, procedures, notes) and
        # was the dominant cause of /state timeouts under load. py-spy
        # on 2026-05-26 caught the asyncio loop stuck inside
        # ``pymongo.cursor.__next__`` during exactly this call.
        journey = await asyncio.to_thread(self.generator.fetch_full_journey, sid, hadm)

        # Parse the original admission time for offset calculations
        admit_time = _parse_time(adm.get("admittime"))
        if admit_time is None:
            logger.warning("Skipping hadm_id=%s -- unparseable admittime.", hadm)
            return

        # Generate a unique sim hadm_id
        sim_hadm = f"SIM-{hadm}-{int(now.timestamp())}"

        sim_patient: Dict[str, Any] = {
            "subject_id": sid,
            "hadm_id": sim_hadm,
            "original_hadm_id": hadm,
            "sim_admittime": now.isoformat(),
            "admission_type": adm.get("admission_type"),
            "admission_location": adm.get("admission_location"),
            "insurance": adm.get("insurance"),
            "race": adm.get("race"),
            "status": "admitted",
        }

        # Persist to MIMIC_SIM.admissions (off the event loop)
        await asyncio.to_thread(self.sim_db["admissions"].insert_one, {**sim_patient})
        self.stats["total_admissions"] += 1

        # ── schedule transfers ───────────────────────────────────────
        for transfer in journey["transfers"]:
            offset = self._time_offset(admit_time, transfer.get("intime"))
            if offset is not None:
                evt_time = now + timedelta(seconds=offset)
                self._schedule_event(evt_time, "transfer", {
                    "hadm_id": sim_hadm,
                    "subject_id": sid,
                    "careunit": transfer.get("careunit"),
                    "eventtype": transfer.get("eventtype"),
                })

        # ── schedule vitals ──────────────────────────────────────────
        # Write the first reading of each vital type immediately so the
        # patient appears on the ICU board with baseline vitals right away
        # instead of waiting hours (in sim-time) for the first chartevent.
        # Batch the seed inserts into one insert_many call (off the event
        # loop) instead of N synchronous insert_one calls.
        seeded_items: set = set()
        seed_docs: list = []
        for vital in journey["vitals"]:
            iid = vital.get("itemid")
            val = vital.get("valuenum")
            if iid is not None and val is not None and iid not in seeded_items:
                seeded_items.add(iid)
                seed_docs.append({
                    "hadm_id": sim_hadm,
                    "subject_id": sid,
                    "itemid": iid,
                    "valuenum": val,
                    "charttime": now.isoformat(),
                    "sim_time": now.isoformat(),
                })

            offset = self._time_offset(admit_time, vital.get("charttime"))
            if offset is not None:
                evt_time = now + timedelta(seconds=offset)
                self._schedule_event(evt_time, "vital", {
                    "hadm_id": sim_hadm,
                    "subject_id": sid,
                    "itemid": iid,
                    "valuenum": val,
                    "charttime": evt_time.isoformat(),
                })

        # If the patient has no MIMIC vitals (no ICU stay in original data),
        # generate plausible baseline vitals so they aren't blank on the board.
        if not seeded_items:
            import random
            _baseline_vitals = {
                220045: (75, 15),   # HR: mean 75, std 15
                220210: (16, 3),    # RR: mean 16, std 3
                220277: (96, 2),    # SpO2: mean 96, std 2
                220179: (120, 15),  # SBP: mean 120, std 15
                220180: (75, 10),   # DBP: mean 75, std 10
                223761: (37.0, 0.4),  # Temp: mean 37.0, std 0.4
            }
            for iid, (mean, std) in _baseline_vitals.items():
                val = round(random.gauss(mean, std), 1)
                seed_docs.append({
                    "hadm_id": sim_hadm,
                    "subject_id": sid,
                    "itemid": iid,
                    "valuenum": val,
                    "charttime": now.isoformat(),
                    "sim_time": now.isoformat(),
                })

        # Single batched insert for all baseline chartevents — replaces up to
        # ~12 sequential insert_one calls per admission, all of which used to
        # block the event loop on the admit-patient path.
        if seed_docs:
            await asyncio.to_thread(self.sim_db["chartevents"].insert_many, seed_docs)

        # ── schedule labs ────────────────────────────────────────────
        for lab in journey["labs"]:
            offset = self._time_offset(admit_time, lab.get("charttime"))
            if offset is not None:
                evt_time = now + timedelta(seconds=offset)
                self._schedule_event(evt_time, "lab", {
                    "hadm_id": sim_hadm,
                    "subject_id": sid,
                    "itemid": lab.get("itemid"),
                    "valuenum": lab.get("valuenum"),
                    "valueuom": lab.get("valueuom"),
                })

        # ── schedule medications ─────────────────────────────────────
        for med in journey["medications"]:
            offset = self._time_offset(admit_time, med.get("starttime"))
            if offset is not None:
                evt_time = now + timedelta(seconds=offset)
                self._schedule_event(evt_time, "medication", {
                    "hadm_id": sim_hadm,
                    "subject_id": sid,
                    "drug": med.get("drug"),
                    "dose_val_rx": med.get("dose_val_rx"),
                    "dose_unit_rx": med.get("dose_unit_rx"),
                    "route": med.get("route"),
                    "action": "start",
                })
            # Also schedule a stop event if stoptime exists
            stop_offset = self._time_offset(admit_time, med.get("stoptime"))
            if stop_offset is not None:
                stop_time = now + timedelta(seconds=stop_offset)
                self._schedule_event(stop_time, "medication", {
                    "hadm_id": sim_hadm,
                    "subject_id": sid,
                    "drug": med.get("drug"),
                    "action": "stop",
                })

        # ── diagnoses (fire immediately) ─────────────────────────────
        for diag in journey["diagnoses"]:
            self._schedule_event(now, "diagnosis", {
                "hadm_id": sim_hadm,
                "subject_id": sid,
                "icd_code": diag.get("icd_code"),
                "icd_version": diag.get("icd_version"),
                "seq_num": diag.get("seq_num"),
            })

        # ── procedures ───────────────────────────────────────────────
        for proc in journey["procedures"]:
            # Schedule at admission time (seq_num ordering preserved by insert order)
            self._schedule_event(now, "procedure", {
                "hadm_id": sim_hadm,
                "subject_id": sid,
                "icd_code": proc.get("icd_code"),
                "icd_version": proc.get("icd_version"),
                "seq_num": proc.get("seq_num"),
            })

        # ── schedule notes (clinical narrative) ──────────────────────
        # Notes carry their own ``charttime`` from MIMIC-IV-Note. Streaming
        # each at the right offset means a discharge summary written on
        # day 5 of the stay reaches clinical_scribe at sim-day-5 — not
        # all at once at admission like the previous batched flow did.
        for note in journey.get("notes", []):
            offset = self._time_offset(admit_time, note.get("charttime"))
            if offset is None:
                continue
            evt_time = now + timedelta(seconds=offset)
            self._schedule_event(evt_time, "note", {
                "hadm_id": sim_hadm,
                "subject_id": sid,
                "original_hadm_id": hadm,
                "note_id": note.get("note_id"),
                "note_type": note.get("note_type"),
                "note_seq": note.get("note_seq"),
                "charttime": evt_time.isoformat(),
                # Cap text size in the queued payload to avoid bloating the
                # in-memory event queue; the full text is fetched on demand
                # by clinical_scribe via note_id when it processes the event.
                "text_len": len(note.get("text") or ""),
                "text": note.get("text") or "",
            })

        # ── schedule discharge ───────────────────────────────────────
        # For DOA / very-short-stay expired patients (hospital_expire_flag=1
        # AND dischtime within minutes of admittime), fire the discharge
        # event immediately at offset 0 so the dashboard doesn't show them
        # as "still admitted" for hours of sim-time waiting on a near-zero
        # MIMIC offset to elapse. Stays where the patient died after a real
        # hospital course (offset > 5 minutes) keep the original timing.
        disch_time = _parse_time(adm.get("dischtime"))
        is_expired = str(adm.get("hospital_expire_flag", 0)) in ("1", "True", "true")
        if disch_time and admit_time:
            raw_offset = max(0, (disch_time - admit_time).total_seconds())
            if is_expired and raw_offset < 300:  # DOA: dischtime ≤ admittime+5min
                disch_offset = 0
            else:
                disch_offset = raw_offset * DEMO_LOS_SCALE
            self._schedule_event(now + timedelta(seconds=disch_offset), "discharge", {
                "hadm_id": sim_hadm,
                "subject_id": sid,
                "discharge_location": adm.get("discharge_location"),
                "hospital_expire_flag": adm.get("hospital_expire_flag"),
                "discharge_reason": "expired" if is_expired else "routine",
            })

        # Track active patient
        self.active_patients[sim_hadm] = sim_patient

        # Broadcast admission event
        await self._broadcast({
            "event": "admission",
            "sim_time": now.isoformat(),
            "data": {k: v for k, v in sim_patient.items() if k != "_id"},
        })

        # ── Digital Twin: process admission through all AI modules ──
        if self.digital_twin is not None:
            try:
                # Pull seeded vitals + labs in worker threads so the two
                # cursor walks (one per collection) don't block the event
                # loop on the admit-patient path.
                vit_docs = await asyncio.to_thread(
                    lambda: list(self.sim_db["chartevents"].find(
                        {"hadm_id": sim_hadm}, {"itemid": 1, "valuenum": 1, "_id": 0}
                    ))
                )
                sim_vitals: Dict[str, float] = {}
                for vdoc in vit_docs:
                    iid = vdoc.get("itemid")
                    val = vdoc.get("valuenum")
                    vname = VITAL_ITEMID_TO_NAME.get(iid)
                    if vname and val is not None:
                        sim_vitals[vname] = val

                # Fetch any seeded labs
                sim_labs: Dict[str, float] = {}
                lab_docs = await asyncio.to_thread(
                    lambda: list(self.sim_db["labevents"].find(
                        {"hadm_id": sim_hadm}, {"itemid": 1, "valuenum": 1, "_id": 0}
                    ))
                )
                for ldoc in lab_docs:
                    iid = ldoc.get("itemid")
                    val = ldoc.get("valuenum")
                    lname = LAB_ITEMID_TO_NAME.get(iid)
                    if lname and val is not None:
                        sim_labs[lname] = val

                dt_patient = {
                    "subject_id": sid,
                    "hadm_id": sim_hadm,
                    # Carry the original MIMIC hadm so the orchestrator can
                    # query MIMIC-IV-Note for streaming-eligible narrative.
                    # Without this, _fetch_mimic_notes(sim_hadm) returns []
                    # because the SIM-prefixed string can't be cast to int,
                    # and _scribe_step always falls back to synthetic notes
                    # — defeating the streaming pipeline.
                    "original_hadm_id": hadm,
                    "age": adm.get("anchor_age", 65),
                    "gender": adm.get("gender", "M"),
                    "admission_type": adm.get("admission_type", "EMERGENCY"),
                    "admission_location": adm.get("admission_location", "EMERGENCY ROOM"),
                    "admittime": now.isoformat(),
                    "department": "ED",
                    "vitals": sim_vitals,
                    "labs": sim_labs,
                    "diagnoses": journey.get("diagnoses", []),
                }
                # Bound the per-admission pipeline. The orchestrator
                # cascades through ~8 downstream HTTP calls; without this
                # cap a single slow module would block _admit_next_patient
                # — and therefore the arrival_loop coroutine — until every
                # circuit breaker opens. 15s gives the slow path room to
                # finish without holding up subsequent admissions.
                await asyncio.wait_for(
                    self.digital_twin.process_admission(dt_patient),
                    timeout=15.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "digital_twin_admission_timeout hadm_id=%s — pipeline took >15s",
                    sim_hadm,
                )
            except Exception as e:
                logger.debug("Digital twin admission error for %s: %s", sim_hadm, e)

        logger.debug(
            "Admitted %s (original hadm_id=%s), queued %d events.",
            sim_hadm, hadm, len(self.event_queue),
        )

    # ------------------------------------------------------------------
    # Event processor
    # ------------------------------------------------------------------

    async def _event_processor(self) -> None:
        """Continuously process scheduled events as sim clock advances.

        Per-event exceptions are caught and logged so a single bad event
        (Mongo write failure, propagation crash, etc.) can't kill the
        whole task. The previous outer-only ``except CancelledError``
        meant the first unhandled exception silently stopped the loop
        — engine.running stayed True, queued_events kept growing, but
        no events ever fired again. (Closes the engine-silent-death bug
        diagnosed 2026-05-25: last_fired_event_at_wall freezing while
        HTTP layer kept responding.)
        """
        try:
            while self.running:
                try:
                    await asyncio.sleep(0.5)  # check every 500ms real time
                    now = self.clock.now()

                    # Fire all events whose scheduled time has passed
                    while self.event_queue and self.event_queue[0][0] <= now:
                        evt_time, _seq, event_type, data = heapq.heappop(self.event_queue)
                        try:
                            await self._fire_event(event_type, data, evt_time)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:  # noqa: BLE001
                            logger.exception(
                                "event_processor: _fire_event failed event_type=%s hadm_id=%s: %s",
                                event_type, data.get("hadm_id"), exc,
                            )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.exception("event_processor: tick failed, will retry: %s", exc)
                    await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            logger.info("event_processor cancelled")

    async def _fire_event(self, event_type: str, data: Dict[str, Any], event_time: datetime) -> None:
        """Persist event to MongoDB and broadcast via WebSocket.

        Mongo writes go through ``asyncio.to_thread`` so the event-processor
        coroutine doesn't block the API event loop on every event. Without
        this wrapping, processing a burst of queued events (e.g. 232
        chartevents per admission) chains hundreds of synchronous Mongo
        round-trips on the same loop that serves /state, /sim/clock, and the
        dashboard's polling endpoints — which is what was making the API
        time out within minutes of a simulation start.
        """
        import asyncio
        data["sim_time"] = event_time.isoformat()

        if event_type == "transfer":
            data["intime"] = event_time.isoformat()
            await asyncio.to_thread(self.sim_db["transfers"].insert_one, {**data})
            self.stats["total_transfers"] += 1

        elif event_type == "vital":
            await asyncio.to_thread(self.sim_db["chartevents"].insert_one, {**data})
            self.stats["total_vitals"] += 1
            # Enrich with human-readable name for downstream modules
            iid = data.get("itemid")
            if iid and iid in VITAL_ITEMID_TO_NAME:
                data["vital_name"] = VITAL_ITEMID_TO_NAME[iid]

        elif event_type == "lab":
            await asyncio.to_thread(self.sim_db["labevents"].insert_one, {**data})
            self.stats["total_labs"] += 1
            iid = data.get("itemid")
            if iid and iid in LAB_ITEMID_TO_NAME:
                data["lab_name"] = LAB_ITEMID_TO_NAME[iid]

        elif event_type == "medication":
            await asyncio.to_thread(self.sim_db["prescriptions"].insert_one, {**data})
            self.stats["total_meds"] += 1

        elif event_type == "diagnosis":
            await asyncio.to_thread(self.sim_db["diagnoses_icd"].insert_one, {**data})
            self.stats["total_diagnoses"] += 1

        elif event_type == "procedure":
            await asyncio.to_thread(self.sim_db["procedures_icd"].insert_one, {**data})
            self.stats["total_procedures"] += 1

        elif event_type == "note":
            # Notes are streamed at MIMIC charttime offsets — persist into
            # MIMIC_SIM.notes so they're queryable like the rest of the
            # patient timeline, and bump the stat counter the dashboard
            # displays. Propagation to clinical_scribe happens through
            # _propagate_to_digital_twin below.
            await asyncio.to_thread(self.sim_db["notes"].insert_one, {**data})
            self.stats.setdefault("total_notes", 0)
            self.stats["total_notes"] += 1

        elif event_type == "discharge":
            hadm_id = data.get("hadm_id")
            if hadm_id in self.active_patients:
                self.active_patients[hadm_id]["status"] = "discharged"
                del self.active_patients[hadm_id]
            await asyncio.to_thread(
                self.sim_db["admissions"].update_one,
                {"hadm_id": hadm_id},
                {"$set": {
                    "status": "discharged",
                    "sim_dischtime": event_time.isoformat(),
                    "discharge_location": data.get("discharge_location"),
                    "hospital_expire_flag": data.get("hospital_expire_flag"),
                    "discharge_reason": data.get("discharge_reason", "routine"),
                }},
            )
            self.stats["total_discharges"] += 1

        # Liveness telemetry — record the wall and sim timestamps of the
        # latest successful fire so /state consumers can spot stalls.
        try:
            from datetime import timezone as _tz
            self.last_fired_event_at_wall = datetime.now(_tz.utc)
            self.last_fired_event_at_sim = event_time
            self.last_fired_event_type = event_type
        except Exception:  # noqa: BLE001
            pass

        await self._broadcast({
            "event": event_type,
            "sim_time": event_time.isoformat(),
            "data": {k: v for k, v in data.items() if k != "_id"},
        })

        # ── Digital Twin propagation (fire-and-forget, bounded) ───
        # py-spy of a hung data_ingestion (2026-05-26) showed the asyncio
        # event loop was spending essentially all its time inside h11
        # header validation of outbound httpx requests made by
        # ``digital_twin._safe_call``. With 30+ active patients each
        # firing vitals continuously × 5 downstream services per vital,
        # the synchronous-on-the-loop portions of httpx (header parse,
        # connection pool bookkeeping) starve every other coroutine —
        # including the HTTP request handlers for /state, /health etc.
        #
        # We now dispatch propagation as an unawaited task bounded by
        # ``_propagation_semaphore`` so:
        #   * ``_fire_event`` returns immediately, freeing the loop to
        #     handle inbound HTTP requests between sim events
        #   * concurrent in-flight propagations are capped (back-pressure)
        #   * a slow downstream service can't queue indefinitely; if the
        #     semaphore is saturated we DROP the propagation rather than
        #     accumulating tasks
        if self.digital_twin is not None:
            if self._propagation_semaphore.locked() and self._propagation_pending >= self._PROPAGATION_HARD_CAP:
                # Backpressure: drop this propagation to keep the loop healthy.
                logger.debug(
                    "digital_twin_propagation_dropped (hard_cap=%d) event_type=%s hadm_id=%s",
                    self._PROPAGATION_HARD_CAP, event_type, data.get("hadm_id"),
                )
            else:
                self._propagation_pending += 1
                task = asyncio.create_task(
                    self._bounded_propagate(event_type, data),
                    name=f"prop:{event_type}:{data.get('hadm_id', '?')}",
                )
                task.add_done_callback(self._propagation_done)

    # Bounded fire-and-forget propagation helper. Acquires the
    # ``_propagation_semaphore`` so concurrent in-flight calls stay
    # capped — without this, hundreds of vital events per second would
    # each spawn a task and they would all sit in httpx code paths
    # competing for the asyncio loop.
    async def _bounded_propagate(self, event_type: str, data: Dict[str, Any]) -> None:
        async with self._propagation_semaphore:
            try:
                await asyncio.wait_for(
                    self._propagate_to_digital_twin(event_type, data),
                    timeout=8.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "digital_twin_propagation_timeout event_type=%s hadm_id=%s",
                    event_type, data.get("hadm_id"),
                )
            except Exception as e:
                logger.debug("Digital twin propagation error: %s", e)

    def _propagation_done(self, _task: "asyncio.Task[None]") -> None:
        self._propagation_pending = max(0, self._propagation_pending - 1)

    # Per-patient throttle for vital propagation. Vitals fire ~60-100/s
    # at speed=5x across 30 patients × 6 itemids. Each call fans out to
    # 5 downstream services via httpx, and h11 header-validation work is
    # synchronous on the asyncio loop (proven by py-spy 2026-05-26).
    # Throttling to one process_vital per patient per 30 sim-seconds
    # drops the propagation rate by 30-180× without losing freshness:
    # NEWS2/sepsis screens only need vitals every ~60s anyway.
    _VITAL_PROPAGATE_INTERVAL_SIM_S = 30.0

    async def _propagate_to_digital_twin(
        self, event_type: str, data: Dict[str, Any]
    ) -> None:
        """Route simulation events to the DigitalTwinOrchestrator.

        Each event type triggers the appropriate orchestrator method,
        which then cascades through all AI modules in sequence.
        """
        dt = self.digital_twin
        hadm_id = data.get("hadm_id")
        if hadm_id is None:
            return

        if event_type == "vital":
            # Per-patient throttle — see _VITAL_PROPAGATE_INTERVAL_SIM_S
            # docstring above. Allows the first vital after each interval
            # through, drops the rest.
            now_sim = self.clock.now().timestamp()
            last = self._last_vital_prop_sim.get(hadm_id, 0.0)
            if now_sim - last < self._VITAL_PROPAGATE_INTERVAL_SIM_S:
                return
            self._last_vital_prop_sim[hadm_id] = now_sim
            await dt.process_vital(data)
        elif event_type == "lab":
            await dt.process_lab(data)
        elif event_type == "transfer":
            await dt.process_transfer(data)
        elif event_type == "discharge":
            await dt.process_discharge(data)
            # Clear throttle record so a re-admitted hadm_id starts clean
            self._last_vital_prop_sim.pop(hadm_id, None)
        elif event_type == "note":
            # Stream this single note through the orchestrator's note
            # handler — it forwards to clinical_scribe with the correct
            # sim_time on the payload.
            if hasattr(dt, "process_note"):
                await dt.process_note(data)
        # Note: admissions are handled directly in _admit_next_patient

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def _schedule_event(self, sim_time: datetime, event_type: str, data: Dict[str, Any]) -> None:
        """Add an event to the priority queue with a sequence tie-breaker.

        Skips events whose horizon (delta from current sim clock) exceeds
        ``EVENT_MAX_SCHEDULE_HORIZON_S`` to prevent unbounded heap growth
        from MIMIC patients with multi-week ICU stays. Discharge events
        always schedule because they're terminal and there's at most one
        per admission.
        """
        if event_type != "discharge":
            try:
                from shared.integration.sim_clock import get_sim_time as _now
                horizon_s = (sim_time - _now()).total_seconds()
                if horizon_s > EVENT_MAX_SCHEDULE_HORIZON_S:
                    return
            except Exception:  # noqa: BLE001
                # Sim clock unavailable — fall through and schedule normally.
                pass
        heapq.heappush(self.event_queue, (sim_time, _next_seq(), event_type, data))

    def _time_offset(self, base_time: datetime, event_time_str: Any) -> Optional[float]:
        """Seconds between *base_time* and a parsed event-time string.

        Returns ``None`` if the event time cannot be parsed.  Negative
        offsets are clamped to 0. The result is scaled by ``DEMO_LOS_SCALE``
        so demos can compress a multi-day MIMIC stay into a short wall window
        without changing the simulation clock speed (clock speed affects
        every event uniformly; LOS-scale specifically targets per-patient
        offsets so arrival cadence stays under operator control).
        """
        evt = _parse_time(event_time_str)
        if evt is None or base_time is None:
            return None
        raw = max(0.0, (evt - base_time).total_seconds())
        return raw * DEMO_LOS_SCALE

    # ------------------------------------------------------------------
    # WebSocket broadcast
    # ------------------------------------------------------------------

    async def _broadcast(self, message: Dict[str, Any]) -> None:
        """Send an event payload to all connected WebSocket listeners."""
        if not self.listeners:
            return
        text = json.dumps(message, default=str)
        dead: List[Any] = []
        for ws in self.listeners:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            try:
                self.listeners.remove(ws)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # State query
    # ------------------------------------------------------------------

    def get_state(self) -> Dict[str, Any]:
        """Return a snapshot of the current simulation state."""
        from datetime import timezone as _tz
        wall_now = datetime.now(_tz.utc)
        last_wall = self.last_fired_event_at_wall
        seconds_since_last = (
            (wall_now - last_wall).total_seconds() if last_wall is not None else None
        )
        return {
            "running": self.running,
            "sim_time": self.clock.now().isoformat(),
            "speed": self.clock.speed,
            "active_patients": len(self.active_patients),
            "queued_events": len(self.event_queue),
            "stats": dict(self.stats),
            "last_fired_event_at_wall": (
                last_wall.isoformat() if last_wall is not None else None
            ),
            "last_fired_event_at_sim": (
                self.last_fired_event_at_sim.isoformat()
                if self.last_fired_event_at_sim is not None else None
            ),
            "last_fired_event_type": self.last_fired_event_type,
            "seconds_since_last_event": seconds_since_last,
        }

    def get_active_patients(self) -> List[Dict[str, Any]]:
        """Return list of currently admitted patients."""
        return [
            {k: v for k, v in p.items() if k != "_id"}
            for p in self.active_patients.values()
        ]

    def get_department_census(self) -> Dict[str, int]:
        """Count active patients per department/careunit.

        Uses a single aggregation pipeline instead of N+1 ``find_one`` calls.
        Previously this looped over every active patient and issued a sorted
        ``find_one`` per patient — with a few hundred admissions that is
        hundreds of synchronous Mongo round-trips on the request path,
        which blocks the async event loop and causes the whole API
        (including endpoints unrelated to census) to time out under
        dashboard polling pressure.
        """
        census: Dict[str, int] = {}
        if not self.active_patients:
            return census
        pipeline = [
            {"$match": {"hadm_id": {"$in": list(self.active_patients.keys())}}},
            {"$sort": {"hadm_id": 1, "intime": -1}},
            {"$group": {
                "_id": "$hadm_id",
                "careunit": {"$first": "$careunit"},
            }},
            {"$group": {
                "_id": {"$ifNull": ["$careunit", "Unknown"]},
                "count": {"$sum": 1},
            }},
        ]
        try:
            for row in self.sim_db["transfers"].aggregate(pipeline):
                unit = row.get("_id") or "Unknown"
                census[unit] = census.get(unit, 0) + int(row.get("count", 0))
        except Exception:  # noqa: BLE001 — degrade gracefully
            return census
        return census
