"""Pydantic schemas for AI Clinical Scribe API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Irish Clinical Note Templates
# ---------------------------------------------------------------------------

IRISH_NOTE_TYPES = [
    "consultant_letter", "admission_note", "discharge_summary",
    "ed_note", "ward_round", "progress_note", "referral_letter",
    "procedure_note", "nursing_assessment",
]

SOAP_SECTIONS = ["subjective", "objective", "assessment", "plan"]


# ---------------------------------------------------------------------------
# Request Schemas
# ---------------------------------------------------------------------------

class TranscribeRequest(BaseModel):
    """Request to transcribe audio to text."""
    audio_base64: Optional[str] = None  # Base64-encoded audio
    audio_url: Optional[str] = None     # URL to audio file
    language: str = "en"
    speaker_count: int = 2              # Expected number of speakers
    medical_context: Optional[str] = None  # Specialty hint for ASR


class GenerateNoteRequest(BaseModel):
    """Request to generate clinical note from transcript."""
    transcript: str
    patient_id: Optional[int] = None
    # Uplift: SIM-prefixed ids are strings; keep ints for real MIMIC ids
    hadm_id: Optional[Union[int, str]] = None
    note_type: str = "progress_note"
    specialty: Optional[str] = None
    encounter_type: str = "outpatient"  # outpatient, inpatient, ed, icu
    clinician_role: str = "consultant"  # consultant, registrar, sho, nurse
    include_icd_codes: bool = True
    include_ner: bool = True
    # Provenance for ingested notes (e.g. MIMIC-IV-Note replay through DT)
    source: Optional[str] = "synthetic"  # "synthetic" | "mimic_iv_note" | "transcribed"
    original_note_id: Optional[str] = None
    original_charttime: Optional[str] = None


class GenerateNoteFromTextRequest(BaseModel):
    """Generate note from free text (no audio)."""
    clinical_text: str
    patient_id: Optional[int] = None
    # Uplift: SIM-prefixed ids are strings; keep ints for real MIMIC ids
    hadm_id: Optional[Union[int, str]] = None
    note_type: str = "progress_note"
    specialty: Optional[str] = None
    # Provenance fields (passed through to the underlying note record)
    source: Optional[str] = "synthetic"
    original_note_id: Optional[str] = None
    original_charttime: Optional[str] = None


class CodeRequest(BaseModel):
    """Request for ICD-10-AM/ACHI coding from note text."""
    note_text: str
    note_type: str = "discharge_summary"
    max_codes: int = 10


class EntityExtractionRequest(BaseModel):
    """Request for clinical NER from text."""
    text: str
    entity_types: Optional[List[str]] = None  # Filter to specific types


class NoteApprovalRequest(BaseModel):
    """Clinician approval of generated note."""
    approved_by: str
    role: str
    edits_made: bool = False
    edit_summary: Optional[str] = None


# ---------------------------------------------------------------------------
# Response Schemas
# ---------------------------------------------------------------------------

class TranscriptSegment(BaseModel):
    """A segment of transcribed speech."""
    speaker: str = "unknown"  # doctor, patient, nurse, other
    start_time: float = 0.0
    end_time: float = 0.0
    text: str = ""
    confidence: float = 0.0


class Transcript(BaseModel):
    """Full transcript with speaker diarization."""
    segments: List[TranscriptSegment] = []
    full_text: str = ""
    duration_seconds: float = 0.0
    language: str = "en"
    word_error_rate_estimate: Optional[float] = None


class SOAPNote(BaseModel):
    """Structured SOAP clinical note."""
    subjective: str = ""
    objective: str = ""
    assessment: str = ""
    plan: str = ""


class ClinicalNote(BaseModel):
    """Generated clinical note."""
    note_id: str = ""
    note_type: str = "progress_note"
    patient_id: Optional[int] = None
    # Uplift: SIM-prefixed ids are strings; keep ints for real MIMIC ids
    hadm_id: Optional[Union[int, str]] = None
    generated_at: Optional[datetime] = None
    soap: Optional[SOAPNote] = None
    full_text: str = ""
    summary: str = ""
    specialty: Optional[str] = None
    clinician_role: str = "consultant"
    entities: Optional[Dict[str, Any]] = None
    icd_codes: Optional[List[Dict[str, Any]]] = None
    achi_codes: Optional[List[Dict[str, Any]]] = None
    quality_score: float = 0.0
    faithfulness_score: float = 0.0
    flags: List[str] = []  # Safety/quality flags
    status: str = "draft"  # draft, reviewed, approved
    model_used: str = ""
    # Provenance — only set when this note was derived from an upstream source
    # (e.g. ingested from MIMIC-IV-Note via the digital-twin cascade).
    source: Optional[str] = "synthetic"
    original_note_id: Optional[str] = None
    original_charttime: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None


class ClinicalEntity(BaseModel):
    """An extracted clinical entity."""
    text: str
    entity_type: str  # MEDICATION, DIAGNOSIS, PROCEDURE, etc.
    start_char: int = 0
    end_char: int = 0
    confidence: float = 0.0
    snomed_code: Optional[str] = None
    snomed_term: Optional[str] = None


class EntityExtractionResult(BaseModel):
    """NER extraction results."""
    entities: List[ClinicalEntity] = []
    medications: List[Dict[str, str]] = []
    diagnoses: List[Dict[str, str]] = []
    procedures: List[Dict[str, str]] = []
    allergies: List[Dict[str, str]] = []
    vitals: List[Dict[str, str]] = []
    symptoms: List[Dict[str, str]] = []


class ICDCodeSuggestion(BaseModel):
    """ICD-10-AM code suggestion."""
    code: str
    description: str
    confidence: float
    category: str = "diagnosis"  # diagnosis, procedure
    is_primary: bool = False


class CodingSuggestion(BaseModel):
    """Complete coding suggestion for a note."""
    icd10am_codes: List[ICDCodeSuggestion] = []
    achi_codes: List[ICDCodeSuggestion] = []
    drg_suggestion: Optional[str] = None
    coding_confidence: float = 0.0


class QualityMetrics(BaseModel):
    """Quality metrics for a generated note."""
    note_id: str
    faithfulness_score: float = 0.0
    completeness_score: float = 0.0
    coherence_score: float = 0.0
    clinical_accuracy: Optional[float] = None
    hallucination_flags: List[str] = []
    missing_elements: List[str] = []
    time_saved_estimate_minutes: float = 0.0
