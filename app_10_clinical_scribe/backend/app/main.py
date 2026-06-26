"""
AI Clinical Scribe FastAPI Service
====================================
Ambient documentation, structured note generation, ICD-10-AM coding,
and clinical NER for Irish hospitals.

Port: 8210

Usage::
    uvicorn app_10_clinical_scribe.backend.app.main:app --host 0.0.0.0 --port 8210
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = str(Path(__file__).resolve().parents[4])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fastapi import FastAPI, HTTPException, Query

from shared.api.base import BaseResponse, create_app
from shared.clinical.keywords import MEDICATION_TERMS, SYMPTOM_TERMS
from shared.db.mongo import MongoManager
from shared.ml.registry import ModelRegistry
from shared.integration.event_bus import get_event_bus
from shared.integration.service_client import ServiceClient

MODEL_DIR = Path(os.getenv("MODEL_DIR", "./models/clinical_scribe"))

from app_10_clinical_scribe.backend.app.schemas import (
    ClinicalEntity,
    ClinicalNote,
    CodeRequest,
    CodingSuggestion,
    EntityExtractionRequest,
    EntityExtractionResult,
    GenerateNoteFromTextRequest,
    GenerateNoteRequest,
    ICDCodeSuggestion,
    NoteApprovalRequest,
    QualityMetrics,
    SOAPNote,
    TranscribeRequest,
    Transcript,
    TranscriptSegment,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("clinical_scribe.api")

state: Dict[str, Any] = {
    "mongo": None,
    "notes": {},  # note_id → ClinicalNote
    "service_client": None,
    "event_bus": None,
}


@asynccontextmanager
async def lifespan(application: FastAPI):
    # Observability — structured logs → Loki, tracing, metrics
    try:
        from shared.integration.logging_config import setup_logging
        setup_logging(service_name="clinical_scribe")
    except Exception as exc:  # noqa: BLE001
        logger.warning("logging_setup_failed: %s", exc)
    try:
        from shared.integration.tracing import setup_tracing
        setup_tracing(application, service_name="clinical_scribe")
    except Exception as exc:  # noqa: BLE001
        logger.warning("tracing_setup_failed: %s", exc)
    try:
        from shared.integration.prometheus_metrics import install_metrics
        install_metrics(application, service_name="clinical_scribe")
    except Exception as exc:  # noqa: BLE001
        logger.warning("prometheus_metrics_install_failed: %s", exc)

    # Anchor SimClock to data_ingestion's authoritative clock.
    try:
        from shared.integration.sim_clock import attach_remote_clock
        await attach_remote_clock()
    except Exception as exc:  # noqa: BLE001
        logger.warning("sim_clock_attach_remote_failed: %s", exc)

    state["mongo"] = MongoManager()
    state["service_client"] = ServiceClient()
    state["event_bus"] = get_event_bus()

    # Load ML models
    registry = ModelRegistry(base_path=str(MODEL_DIR))
    for name, key in [("scribe_icd_coder", "icd_model"),
                      ("scribe_ner_engine", "ner_engine"),
                      ("scribe_section_clf", "section_clf")]:
        try:
            state[key], meta = registry.load_model(name)
            logger.info("Loaded %s", name)
        except FileNotFoundError:
            logger.warning("No %s found; using keyword fallback.", name)

    # Subscribe to Kafka/broker events
    try:
        from shared.integration.kafka_consumer import attach_with_ring_buffer
        await attach_with_ring_buffer(
            service_id="clinical_scribe",
            topics=["admission_complete", "patient_discharged", "note_generated"],
            mongo_client=state["mongo"].client,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("scribe_bus_subscribe_failed: %s", exc)

    # Restore in-memory note cache from Mongo so /note/{id} keeps working
    # after a restart. The persistent collection is the source of truth for
    # /notes/by-encounter and /notes/by-patient regardless of cache state.
    try:
        col = state["mongo"].client["clinical_scribe"]["notes"]
        # Index for hot retrieval paths
        col.create_index([("hadm_id", 1), ("generated_at", -1)])
        col.create_index([("patient_id", 1), ("generated_at", -1)])
        col.create_index("note_id", unique=True)
        # Warm cache with last 1000 notes (keeps /note/{id} fast)
        cur = col.find({}, sort=[("generated_at", -1)], limit=1000)
        restored = 0
        for doc in cur:
            doc.pop("_id", None)
            nid = doc.get("note_id")
            if nid:
                state["notes"][nid] = doc
                restored += 1
        logger.info("Clinical Scribe restored %d notes from Mongo on startup", restored)
    except Exception as exc:  # noqa: BLE001
        logger.warning("scribe_restore_failed: %s", exc)

    logger.info("Clinical Scribe service ready")
    yield
    if state["mongo"]:
        state["mongo"].close()


def _persist_note(note_dict: Dict[str, Any]) -> None:
    """Upsert a note into the durable Mongo collection. Best-effort."""
    try:
        col = state["mongo"].client["clinical_scribe"]["notes"]
        # Mongo can't store native datetime if generated_at has tzinfo issues
        # Use replace_one with upsert keyed on note_id for idempotency
        col.replace_one({"note_id": note_dict.get("note_id")}, dict(note_dict), upsert=True)
    except Exception as exc:  # noqa: BLE001
        logger.debug("scribe_persist_failed: %s", exc)


app = create_app(
    title="AI Clinical Scribe",
    version="1.0.0",
    description=(
        "AI-powered clinical documentation for Irish hospitals. "
        "Ambient transcription, SOAP note generation, ICD-10-AM coding, "
        "and clinical NER. Targets 40% reduction in documentation time."
    ),
)
app.router.lifespan_context = lifespan


@app.get("/kafka-events", response_model=BaseResponse, tags=["system"])
async def list_kafka_events(limit: int = 100) -> BaseResponse:
    """Return recent cross-service events consumed via Kafka/broker."""
    from shared.integration.kafka_consumer import get_kafka_events
    return BaseResponse(data=get_kafka_events("clinical_scribe", limit))


# ---------------------------------------------------------------------------
# Transcription Endpoints
# ---------------------------------------------------------------------------
@app.post("/transcribe", response_model=BaseResponse, tags=["transcription"])
async def transcribe(req: TranscribeRequest) -> BaseResponse:
    """Transcribe audio to text with speaker diarization.

    Uses Whisper Large-v3 + pyannote for diarization.
    Falls back to basic transcription when diarization unavailable.
    """
    # Placeholder — Whisper integration in Phase 1
    transcript = Transcript(
        segments=[
            TranscriptSegment(
                speaker="doctor",
                text="[Whisper ASR model not yet loaded. Submit text via /generate-note/from-text]",
                confidence=0.0,
            )
        ],
        full_text="[ASR pending — use text input endpoint]",
        duration_seconds=0,
    )
    return BaseResponse(data=transcript.model_dump())


# ---------------------------------------------------------------------------
# Note Generation Endpoints
# ---------------------------------------------------------------------------
@app.post("/generate-note", response_model=BaseResponse, tags=["note-generation"])
async def generate_note(req: GenerateNoteRequest) -> BaseResponse:
    """Generate structured clinical note from transcript.

    Pipeline: transcript → context enrichment → LLM note generation →
    NER extraction → ICD coding → quality verification.
    """
    from shared.integration.sim_clock import get_sim_time as _sim_now
    note_id = str(uuid.uuid4())[:12]
    now = _sim_now()

    # Fetch patient context from other modules
    context = await _fetch_patient_context(req.patient_id, req.hadm_id)

    # Generate SOAP note (a commercial LLM API / Ollama in Phase 2)
    soap = _generate_soap_from_text(req.transcript, req.note_type, context)

    # Extract entities
    entities = _extract_entities(req.transcript) if req.include_ner else None

    # Generate ICD codes
    icd_codes = None
    if req.include_icd_codes:
        icd_codes = _suggest_icd_codes(soap.assessment + " " + soap.plan)

    # Quality check
    quality = _compute_quality_score(soap, req.transcript)

    note = ClinicalNote(
        note_id=note_id,
        note_type=req.note_type,
        patient_id=req.patient_id,
        hadm_id=req.hadm_id,
        generated_at=now,
        soap=soap,
        full_text=f"S: {soap.subjective}\n\nO: {soap.objective}\n\nA: {soap.assessment}\n\nP: {soap.plan}",
        summary=soap.assessment[:200] if soap.assessment else "",
        specialty=req.specialty,
        clinician_role=req.clinician_role,
        entities=entities,
        icd_codes=icd_codes,
        quality_score=quality,
        faithfulness_score=0.95,  # NLI check in Phase 2
        flags=[],
        status="draft",
        model_used="keyword_extraction_v1",
        source=getattr(req, "source", None) or "synthetic",
        original_note_id=getattr(req, "original_note_id", None),
        original_charttime=getattr(req, "original_charttime", None),
    )

    note_dump = note.model_dump(mode="json")
    state["notes"][note_id] = note_dump
    _persist_note(note_dump)

    bus = state.get("event_bus")
    if bus:
        await bus.publish("note_generated", {
            "note_id": note_id,
            "patient_id": req.patient_id,
            # hadm_id and charttime were missing from the payload — every
            # event_log entry showed hadm_id=None, breaking cross-module
            # correlation (e.g. patient_journey/timeline can't tie the note
            # back to its admission without it).
            "hadm_id": req.hadm_id,
            "charttime": now.isoformat(),
            "note_type": req.note_type,
        }, source_module="clinical_scribe")

    # Integration 7 — forward the completed note's summary to the ERP audit log.
    try:
        await _log_activity_to_erp(note.model_dump())
    except NameError:
        # Helper is declared lower in the module; safe to skip if not yet bound.
        pass

    return BaseResponse(data=note.model_dump())


@app.post("/generate-note/from-text", response_model=BaseResponse, tags=["note-generation"])
async def generate_note_from_text(req: GenerateNoteFromTextRequest) -> BaseResponse:
    """Generate note from free text input (no audio)."""
    note_req = GenerateNoteRequest(
        transcript=req.clinical_text,
        patient_id=req.patient_id,
        hadm_id=req.hadm_id,
        note_type=req.note_type,
        specialty=req.specialty,
        source=req.source or "synthetic",
        original_note_id=req.original_note_id,
        original_charttime=req.original_charttime,
    )
    return await generate_note(note_req)


@app.get("/notes/by-encounter/{hadm_id}", response_model=BaseResponse, tags=["note-generation"])
async def notes_by_encounter(hadm_id: str, limit: int = 50) -> BaseResponse:
    """Return all generated notes for a hospital admission, newest first.

    The id may be an int (real MIMIC) or a SIM-prefixed string. We try both
    so the same endpoint works for both populations.
    """
    col = state["mongo"].client["clinical_scribe"]["notes"]
    # Try as int (real MIMIC hadm_id), then as the raw string (SIM-...)
    candidates: List[Any] = [hadm_id]
    try:
        candidates.append(int(hadm_id))
    except (TypeError, ValueError):
        pass
    docs: List[Dict[str, Any]] = []
    seen: set = set()
    for v in candidates:
        for d in col.find({"hadm_id": v}, sort=[("generated_at", -1)], limit=limit):
            d.pop("_id", None)
            nid = d.get("note_id")
            if nid and nid not in seen:
                seen.add(nid)
                docs.append(d)
    return BaseResponse(data=docs)


@app.get("/notes/by-patient/{subject_id}", response_model=BaseResponse, tags=["note-generation"])
async def notes_by_patient(
    subject_id: str,
    limit: int = 50,
    note_type: Optional[str] = None,
) -> BaseResponse:
    """Return notes for a patient (across admissions), newest first."""
    col = state["mongo"].client["clinical_scribe"]["notes"]
    candidates: List[Any] = [subject_id]
    try:
        candidates.append(int(subject_id))
    except (TypeError, ValueError):
        pass
    query: Dict[str, Any] = {"patient_id": {"$in": candidates}}
    if note_type:
        query["note_type"] = note_type
    cur = col.find(query, sort=[("generated_at", -1)], limit=limit)
    docs = []
    for d in cur:
        d.pop("_id", None)
        docs.append(d)
    return BaseResponse(data=docs)


@app.get("/note/{note_id}", response_model=BaseResponse, tags=["note-generation"])
async def get_note(note_id: str) -> BaseResponse:
    """Retrieve a generated note by ID."""
    note = state["notes"].get(note_id)
    if not note:
        raise HTTPException(status_code=404, detail=f"Note {note_id} not found")
    return BaseResponse(data=note)


@app.post("/note/{note_id}/approve", response_model=BaseResponse, tags=["note-generation"])
async def approve_note(note_id: str, req: NoteApprovalRequest) -> BaseResponse:
    """Record clinician approval of a generated note (audit trail)."""
    note = state["notes"].get(note_id)
    if not note:
        raise HTTPException(status_code=404, detail=f"Note {note_id} not found")

    from shared.integration.sim_clock import get_sim_time as _sim_now
    note["status"] = "approved"
    note["approved_by"] = req.approved_by
    note["approved_at"] = _sim_now().isoformat()
    note["edits_made"] = req.edits_made
    _persist_note(note)

    bus = state.get("event_bus")
    if bus:
        await bus.publish("note_approved", {
            "note_id": note_id,
            "approved_by": req.approved_by,
        }, source_module="clinical_scribe")

    return BaseResponse(data=note)


# ---------------------------------------------------------------------------
# ICD Coding Endpoints
# ---------------------------------------------------------------------------
@app.post("/code", response_model=BaseResponse, tags=["coding"])
async def auto_code(req: CodeRequest) -> BaseResponse:
    """Generate ICD-10-AM and ACHI code suggestions from note text."""
    icd_codes = _suggest_icd_codes(req.note_text)
    return BaseResponse(data=CodingSuggestion(
        icd10am_codes=[ICDCodeSuggestion(**c) for c in (icd_codes or [])],
        coding_confidence=0.7,
    ).model_dump())


# ---------------------------------------------------------------------------
# Entity Extraction Endpoints
# ---------------------------------------------------------------------------
@app.post("/extract-entities", response_model=BaseResponse, tags=["ner"])
async def extract_entities(req: EntityExtractionRequest) -> BaseResponse:
    """Extract clinical entities from text using Bio-ClinicalBERT NER."""
    entities = _extract_entities(req.text)
    return BaseResponse(data=entities)


# ---------------------------------------------------------------------------
# Quality & Analytics
# ---------------------------------------------------------------------------
@app.get("/quality/{note_id}", response_model=BaseResponse, tags=["quality"])
async def get_quality(note_id: str) -> BaseResponse:
    """Return quality metrics for a generated note."""
    note = state["notes"].get(note_id)
    if not note:
        raise HTTPException(status_code=404, detail=f"Note {note_id} not found")

    metrics = QualityMetrics(
        note_id=note_id,
        faithfulness_score=note.get("faithfulness_score", 0),
        completeness_score=note.get("quality_score", 0),
        coherence_score=0.9,
        time_saved_estimate_minutes=8.0,
    )
    return BaseResponse(data=metrics.model_dump())


@app.get("/metrics/documentation-time", response_model=BaseResponse, tags=["quality"])
async def documentation_time_metrics() -> BaseResponse:
    """Return aggregate documentation time savings metrics."""
    notes = state["notes"]
    return BaseResponse(data={
        "total_notes_generated": len(notes),
        "approved_notes": sum(1 for n in notes.values() if n.get("status") == "approved"),
        "avg_quality_score": round(
            sum(n.get("quality_score", 0) for n in notes.values()) / max(len(notes), 1), 2
        ),
        "estimated_time_saved_hours": round(len(notes) * 8 / 60, 1),
    })


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
@app.get("/templates", response_model=BaseResponse, tags=["templates"])
async def list_templates() -> BaseResponse:
    """List available clinical note templates."""
    from app_10_clinical_scribe.backend.app.schemas import IRISH_NOTE_TYPES
    return BaseResponse(data=IRISH_NOTE_TYPES)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _fetch_patient_context(patient_id: Optional[int], hadm_id: Optional[int]) -> Dict:
    """Fetch patient context from other modules for note enrichment."""
    if not patient_id:
        return {}

    client = state.get("service_client")
    if not client:
        return {}

    context = {}
    try:
        summary = await client.patient_journey.get(f"/patient/{patient_id}/summary")
        if summary.get("status") == "ok":
            context["patient_summary"] = summary.get("data", {})
    except Exception:
        pass

    return context


def _generate_soap_from_text(text: str, note_type: str, context: Dict) -> SOAPNote:
    """Generate SOAP note from text (a commercial LLM API / Ollama in Phase 2)."""
    # Keyword-based section splitting (LLM replaces this in Phase 2)
    text_lower = text.lower()

    # Simple heuristic: split text into SOAP sections
    subjective = ""
    objective = ""
    assessment = ""
    plan = ""

    sentences = [s.strip() for s in text.replace("\n", ". ").split(".") if s.strip()]

    for sentence in sentences:
        s_lower = sentence.lower()
        if any(w in s_lower for w in ["complains", "reports", "feels", "pain", "history", "presenting"]):
            subjective += sentence + ". "
        elif any(w in s_lower for w in ["examination", "vitals", "bp", "heart rate", "temperature", "observed"]):
            objective += sentence + ". "
        elif any(w in s_lower for w in ["diagnosis", "likely", "consistent with", "assessment", "impression"]):
            assessment += sentence + ". "
        elif any(w in s_lower for w in ["plan", "prescribe", "refer", "follow", "review", "discharge"]):
            plan += sentence + ". "
        else:
            subjective += sentence + ". "

    return SOAPNote(
        subjective=subjective.strip() or "See transcript.",
        objective=objective.strip() or "Examination findings to be documented.",
        assessment=assessment.strip() or "Clinical assessment pending.",
        plan=plan.strip() or "Plan to be confirmed.",
    )


def _extract_entities(text: str) -> Dict[str, Any]:
    """Extract clinical entities using ML NER engine or keyword fallback."""
    ner_engine = state.get("ner_engine")
    if ner_engine is not None:
        try:
            return ner_engine.extract(text)
        except Exception:
            pass

    # Keyword fallback
    text_lower = text.lower()
    entities: Dict[str, Any] = {"medications": [], "diagnoses": [], "symptoms": [], "procedures": []}
    entities["medications"] = [{"drug": m, "source": "keyword"} for m in MEDICATION_TERMS if m in text_lower]
    entities["symptoms"] = [{"symptom": s, "source": "keyword"} for s in SYMPTOM_TERMS if s in text_lower]
    return entities


def _suggest_icd_codes(text: str) -> List[Dict[str, str]]:
    """Suggest ICD-10-AM codes using ML model or keyword fallback."""
    icd_model = state.get("icd_model")
    if icd_model is not None:
        try:
            predictions = icd_model.predict_top_k(text, k=10)
            return [
                {"code": code, "description": code, "confidence": round(prob, 3),
                 "category": "diagnosis", "is_primary": i == 0}
                for i, (code, prob) in enumerate(predictions) if prob > 0.1
            ]
        except Exception:
            pass

    # Keyword fallback
    text_lower = text.lower()
    suggestions = []
    code_map = {
        "chest pain": {"code": "R07.9", "description": "Chest pain, unspecified"},
        "pneumonia": {"code": "J18.9", "description": "Pneumonia, unspecified organism"},
        "heart failure": {"code": "I50.9", "description": "Heart failure, unspecified"},
        "diabetes": {"code": "E11.9", "description": "Type 2 diabetes mellitus"},
        "copd": {"code": "J44.1", "description": "COPD with acute exacerbation"},
        "sepsis": {"code": "A41.9", "description": "Sepsis, unspecified organism"},
        "hypertension": {"code": "I10", "description": "Essential hypertension"},
    }
    for keyword, code_info in code_map.items():
        if keyword in text_lower:
            suggestions.append({**code_info, "confidence": 0.7, "category": "diagnosis",
                                "is_primary": len(suggestions) == 0})
    return suggestions[:10]


def _compute_quality_score(soap: SOAPNote, source_text: str) -> float:
    """Compute note quality score (NLI faithfulness in Phase 2)."""
    score = 0.5
    if soap.subjective and len(soap.subjective) > 20:
        score += 0.15
    if soap.objective and len(soap.objective) > 20:
        score += 0.15
    if soap.assessment and len(soap.assessment) > 10:
        score += 0.1
    if soap.plan and len(soap.plan) > 10:
        score += 0.1
    return round(min(1.0, score), 2)


# ---------------------------------------------------------------------------
# Digital Twin integration (Rule 3 cascade + Integration 7)
# ---------------------------------------------------------------------------

_vital_buffer: Dict[str, List[Dict[str, Any]]] = {}


@app.post("/update-vitals", response_model=BaseResponse, tags=["integration"])
async def update_vitals(data: dict) -> BaseResponse:
    """Rule 3 — concurrent vital update to keep in-progress notes aligned.

    The Digital Twin's ``process_vital`` routes live vitals here with 2s
    timeout so any draft note for the admission picks up the latest values.
    """
    hadm_id = str(data.get("hadm_id", "unknown"))
    buf = _vital_buffer.setdefault(hadm_id, [])
    buf.append(dict(data))
    if len(buf) > 200:
        del buf[:100]

    # If we have an in-flight draft for this admission, attach the vital.
    for note in state.get("notes", {}).values():
        if str(note.get("hadm_id")) == hadm_id and note.get("status") == "draft":
            note.setdefault("live_vitals", []).append(dict(data))
    return BaseResponse(data={"buffered": len(buf), "hadm_id": hadm_id})


@app.post("/reset", response_model=BaseResponse, tags=["system"])
async def reset_scribe() -> BaseResponse:
    state["notes"] = {}
    _vital_buffer.clear()
    return BaseResponse(data={"reset": True})


async def _log_activity_to_erp(note: dict) -> None:
    """Integration 7 — forward a completed note's summary to the ERP audit log."""
    try:
        client = state.get("service_client")
        if client is None:
            return
        ts = note.get("generated_at")
        if hasattr(ts, "isoformat"):
            ts = ts.isoformat()
        await client.erp.post("/erp/activity-log", {
            "department": note.get("department"),
            "hadm_id": note.get("hadm_id"),
            "icd_codes": note.get("icd_codes", []),
            "note_type": note.get("note_type"),
            "timestamp": ts,
        })
    except Exception as exc:
        logger.warning("erp_activity_log_failed", extra={"error": str(exc)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8210)
