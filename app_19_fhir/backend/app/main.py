"""National EHR Gateway (HL7 FHIR R4) — port 8219.

Exposes FHIR R4 resources built on top of the Patient Journey module and
MIMIC_SIM. In simulation mode identifiers are pseudonymised as
``SIM-<hash>`` so no real MIMIC subject_ids ever leave the service.

Resource coverage (expansion v2):
  - Patient                (GET)
  - Encounter              (GET, search by patient)
  - Observation            (GET Bundle — vitals + labs)
  - Condition              (GET Bundle from diagnoses_icd)
  - Procedure              (GET Bundle from procedures_icd)
  - MedicationRequest      (GET Bundle from prescriptions / POST for inbound orders)
  - DiagnosticReport       (GET Bundle from lab panels)
  - DocumentReference      (GET Bundle from discharge summaries / scribe notes)
  - AllergyIntolerance     (GET Bundle — stub since MIMIC lacks structured allergies)
  - RiskAssessment         (GET — reads from Oncology AI + Deterioration)
  - CarePlan               (GET — reads discharge plan from app_21)
  - Bundle                 (POST — accepts transaction bundles in-memory)
  - CapabilityStatement    (GET)

IHE/SMART-on-FHIR auth is out of scope for the simulation gateway; production
deployment will require SMART launch + PIX/PDQ for Irish IHI resolution.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fastapi import FastAPI, HTTPException, Query

from shared.api.base import BaseResponse, PrivacyNotice, create_app
from shared.integration.service_client import ServiceClient

logger = logging.getLogger("fhir_gateway")


PRIVACY = PrivacyNotice(
    data_collected=[
        "patient_resources", "encounter_resources", "observation_resources",
        "condition_resources", "procedure_resources", "medication_resources",
        "diagnostic_report_resources", "document_reference_resources",
        "risk_assessment_resources", "care_plan_resources",
    ],
    legal_basis="GDPR Art. 9(2)(h) (healthcare); Health Identifiers Act 2014; HSE eHealth interoperability",
    retention_period="aligned with source systems (Patient Journey)",
)


def _pseudonymise(identifier: Any) -> str:
    if not identifier:
        return "SIM-UNKNOWN"
    h = hashlib.sha256(str(identifier).encode()).hexdigest()[:12]
    return f"SIM-{h}"


def _is_simulation() -> bool:
    return os.environ.get("DEPLOYMENT_MODE", "simulation").lower() != "production"


def _patient_ref(subject_id: Any) -> Dict[str, str]:
    return {"reference": f"Patient/{_pseudonymise(subject_id)}"}


_state: Dict[str, Any] = {"client": None, "inbox": [], "medication_requests": []}


@asynccontextmanager
async def lifespan(application: FastAPI):
    # Observability
    import logging as _logging
    _log = _logging.getLogger("fhir")
    try:
        from shared.integration.logging_config import setup_logging
        setup_logging(service_name="fhir")
    except Exception as exc:  # noqa: BLE001
        _log.warning("logging_setup_failed: %s", exc)
    try:
        from shared.integration.tracing import setup_tracing
        setup_tracing(application, service_name="fhir")
    except Exception as exc:  # noqa: BLE001
        _log.warning("tracing_setup_failed: %s", exc)
    try:
        from shared.integration.prometheus_metrics import install_metrics
        install_metrics(application, service_name="fhir")
    except Exception as exc:  # noqa: BLE001
        _log.warning("prometheus_metrics_install_failed: %s", exc)

    _state["client"] = ServiceClient()
    # Subscribe to Kafka — admissions generate Patient+Encounter FHIR
    # resources; discharges finalize them; notes drive DocumentReference.
    try:
        from shared.db.mongo import MongoManager
        from shared.integration.kafka_consumer import attach_with_ring_buffer
        _state["_mongo"] = MongoManager()
        await attach_with_ring_buffer(
            service_id="fhir",
            topics=["admission_complete", "patient_discharged", "patient_transferred", "note_generated"],
            mongo_client=_state["_mongo"].client,
        )
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger("fhir").warning("fhir_bus_subscribe_failed: %s", exc)
    yield


app = create_app(
    title="National EHR Gateway (FHIR R4)",
    version="2.0.0",
    description=(
        "HL7 FHIR R4 resource surface for HSE National Shared Care Record. "
        "Expanded resource set: Patient, Encounter, Observation, Condition, "
        "Procedure, MedicationRequest, DiagnosticReport, DocumentReference, "
        "AllergyIntolerance, RiskAssessment, CarePlan."
    ),
    privacy_notice=PRIVACY,
)
app.router.lifespan_context = lifespan


@app.get("/kafka-events", response_model=BaseResponse, tags=["system"])
async def list_kafka_events(limit: int = 100) -> BaseResponse:
    """Return recent cross-service events consumed via Kafka/broker."""
    from shared.integration.kafka_consumer import get_kafka_events
    return BaseResponse(data=get_kafka_events("fhir", limit))


def _client() -> ServiceClient:
    return _state.get("client") or ServiceClient()


# ---------------------------------------------------------------------------
# CapabilityStatement
# ---------------------------------------------------------------------------
@app.get("/fhir/CapabilityStatement")
async def capability_statement():
    resources = [
        {"type": "Patient", "interaction": [{"code": "read"}]},
        {"type": "Encounter", "interaction": [{"code": "read"}, {"code": "search-type"}]},
        {"type": "Observation", "interaction": [{"code": "search-type"}]},
        {"type": "Condition", "interaction": [{"code": "search-type"}]},
        {"type": "Procedure", "interaction": [{"code": "search-type"}]},
        {"type": "MedicationRequest", "interaction": [{"code": "search-type"}, {"code": "create"}]},
        {"type": "DiagnosticReport", "interaction": [{"code": "search-type"}]},
        {"type": "DocumentReference", "interaction": [{"code": "search-type"}]},
        {"type": "AllergyIntolerance", "interaction": [{"code": "read"}, {"code": "search-type"}]},
        {"type": "RiskAssessment", "interaction": [{"code": "search-type"}]},
        {"type": "CarePlan", "interaction": [{"code": "read"}]},
        {"type": "Bundle", "interaction": [{"code": "create"}]},
    ]
    return {
        "resourceType": "CapabilityStatement",
        "status": "active",
        "kind": "instance",
        "fhirVersion": "4.0.1",
        "format": ["application/fhir+json"],
        "rest": [{"mode": "server", "resource": resources}],
    }


# ---------------------------------------------------------------------------
# Patient Journey fetchers
# ---------------------------------------------------------------------------
async def _fetch_summary(subject_id: str) -> Dict[str, Any]:
    res = await _client().patient_journey.get(f"/patient/{subject_id}/summary")
    return res.get("data") or {}


async def _fetch_timeline(subject_id: str, hadm_id: str) -> Dict[str, Any]:
    res = await _client().patient_journey.get(
        f"/patient/{subject_id}/admission/{hadm_id}/timeline"
    )
    return res.get("data") or {}


async def _fetch_medications(subject_id: str, hadm_id: str) -> List[Dict[str, Any]]:
    res = await _client().patient_journey.get(
        f"/patient/{subject_id}/admission/{hadm_id}/medications"
    )
    data = res.get("data") or {}
    return data.get("medications") or []


async def _fetch_labs(subject_id: str, hadm_id: str) -> Dict[str, Any]:
    res = await _client().patient_journey.get(
        f"/patient/{subject_id}/admission/{hadm_id}/labs"
    )
    return res.get("data") or {}


async def _fetch_journey(subject_id: str, hadm_id: str) -> Dict[str, Any]:
    res = await _client().patient_journey.get(
        f"/patient/{subject_id}/admission/{hadm_id}/journey"
    )
    return res.get("data") or {}


# ---------------------------------------------------------------------------
# Resource builders
# ---------------------------------------------------------------------------
def _bundle(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "total": len(entries),
        "entry": [{"resource": e} for e in entries],
    }


def _patient_resource(summary: Dict[str, Any]) -> Dict[str, Any]:
    subject_id = summary.get("subject_id", "unknown")
    pseudonym = _pseudonymise(subject_id) if _is_simulation() else str(subject_id)
    return {
        "resourceType": "Patient",
        "id": pseudonym,
        "identifier": [{"system": "urn:oid:2.16.840.1.113883.2.2.4.2", "value": pseudonym}],
        "gender": (summary.get("gender") or "unknown").lower(),
        "birthDate": summary.get("dob"),
        "extension": [{
            "url": "http://hl7.org/fhir/StructureDefinition/patient-citizenship",
            "valueString": "IE" if not _is_simulation() else "SIM",
        }],
    }


def _encounter_resource(hadm: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "resourceType": "Encounter",
        "id": str(hadm.get("hadm_id")),
        "status": "finished" if hadm.get("dischtime") else "in-progress",
        "class": {"code": "IMP", "display": "inpatient encounter"},
        "subject": _patient_ref(hadm.get("subject_id")),
        "period": {"start": hadm.get("admittime"), "end": hadm.get("dischtime")},
        "serviceProvider": {"display": hadm.get("careunit", "")},
    }


def _observation_resource(event: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "resourceType": "Observation",
        "id": str(event.get("event_id") or abs(hash(str(event)))),
        "status": "final",
        "category": [{"coding": [{"code": event.get("event_type", "vital-signs")}]}],
        "code": {"coding": [{"code": event.get("label", "unknown"),
                             "display": event.get("label", "")}]},
        "valueQuantity": {"value": event.get("value"), "unit": event.get("unit", "")},
        "subject": _patient_ref(event.get("subject_id")),
        "effectiveDateTime": event.get("timestamp"),
    }


def _condition_resource(dx: Dict[str, Any], subject_id: Any) -> Dict[str, Any]:
    code = str(dx.get("icd_code") or dx.get("code") or "")
    system = "http://hl7.org/fhir/sid/icd-10" if code and code[0].isalpha() else "http://hl7.org/fhir/sid/icd-9-cm"
    return {
        "resourceType": "Condition",
        "id": f"cond-{code}-{dx.get('seq_num', 0)}",
        "clinicalStatus": {"coding": [{"code": "active"}]},
        "verificationStatus": {"coding": [{"code": "confirmed"}]},
        "code": {
            "coding": [{"system": system, "code": code, "display": dx.get("long_title") or dx.get("label") or code}],
        },
        "subject": _patient_ref(subject_id),
        "recordedDate": dx.get("timestamp") or dx.get("recorded"),
    }


def _procedure_resource(proc: Dict[str, Any], subject_id: Any) -> Dict[str, Any]:
    code = str(proc.get("icd_code") or proc.get("code") or "")
    system = "http://hl7.org/fhir/sid/icd-10-pcs" if code and code[0].isalpha() else "http://hl7.org/fhir/sid/icd-9-cm"
    return {
        "resourceType": "Procedure",
        "id": f"proc-{code}-{proc.get('seq_num', 0)}",
        "status": "completed",
        "code": {
            "coding": [{"system": system, "code": code, "display": proc.get("long_title") or proc.get("label") or code}],
        },
        "subject": _patient_ref(subject_id),
        "performedDateTime": proc.get("timestamp") or proc.get("performed"),
    }


def _medication_request_resource(med: Dict[str, Any], subject_id: Any) -> Dict[str, Any]:
    return {
        "resourceType": "MedicationRequest",
        "id": f"medreq-{abs(hash(str(med)))}",
        "status": "active" if not med.get("stopped") else "stopped",
        "intent": "order",
        "medicationCodeableConcept": {
            "coding": [{"display": med.get("drug") or med.get("medication") or "unknown"}],
            "text": med.get("drug") or med.get("medication") or "unknown",
        },
        "subject": _patient_ref(subject_id),
        "authoredOn": med.get("starttime") or med.get("start"),
        "dosageInstruction": [{
            "text": med.get("dose_val_rx", ""),
            "route": {"text": med.get("route", "")},
        }],
    }


def _diagnostic_report_resource(panel: str, tests: List[Dict[str, Any]], subject_id: Any) -> Dict[str, Any]:
    # Construct contained Observation references from individual test results
    observations = []
    for i, t in enumerate(tests):
        observations.append({
            "resourceType": "Observation",
            "id": f"obs-{panel}-{i}",
            "status": "final",
            "code": {"coding": [{"display": t.get("label", "")}]},
            "valueQuantity": {"value": t.get("value"), "unit": t.get("unit", "")},
            "effectiveDateTime": t.get("timestamp"),
        })
    return {
        "resourceType": "DiagnosticReport",
        "id": f"report-{panel}",
        "status": "final",
        "category": [{"coding": [{"code": "LAB", "display": "Laboratory"}]}],
        "code": {"coding": [{"display": panel}], "text": panel},
        "subject": _patient_ref(subject_id),
        "contained": observations,
        "result": [{"reference": f"#obs-{panel}-{i}"} for i in range(len(tests))],
    }


# LOINC codes for common Irish/MIMIC clinical note types.
_NOTE_TYPE_LOINC = {
    "discharge_summary": ("18842-5", "Discharge summary"),
    "discharge-summary": ("18842-5", "Discharge summary"),
    "admission_note": ("11490-0", "Physician Discharge summary"),
    "progress_note": ("11506-3", "Progress note"),
    "consultant_letter": ("11488-4", "Consult note"),
    "ed_note": ("34111-5", "Emergency department note"),
    "ward_round": ("11506-3", "Progress note"),
    "referral_letter": ("57133-1", "Referral note"),
    "procedure_note": ("28570-0", "Procedure note"),
    "nursing_assessment": ("34746-8", "Nurse Note"),
}


def _document_reference_resource(doc: Dict[str, Any], subject_id: Any, hadm_id: Any) -> Dict[str, Any]:
    note_type = (doc.get("note_type") or "discharge-summary").lower()
    loinc_code, loinc_display = _NOTE_TYPE_LOINC.get(note_type, ("34109-9", "Note"))
    note_id = doc.get("note_id") or f"{abs(hash(str(doc)))}"
    when = (
        doc.get("original_charttime")
        or doc.get("charttime")
        or doc.get("generated_at")
        or doc.get("created_at")
    )
    pretty_type = note_type.replace("_", " ").title()
    title = doc.get("title") or (
        f"{pretty_type} ({when})" if when else pretty_type
    )
    return {
        "resourceType": "DocumentReference",
        "id": f"doc-{note_id}",
        "status": "current",
        "docStatus": "final" if doc.get("status") == "approved" else "preliminary",
        "type": {
            "coding": [{
                "system": "http://loinc.org",
                "code": loinc_code,
                "display": loinc_display,
            }],
            "text": note_type.replace("_", " "),
        },
        "category": [{
            "coding": [{
                "system": "http://hl7.org/fhir/us/core/CodeSystem/us-core-documentreference-category",
                "code": "clinical-note",
                "display": "Clinical Note",
            }],
        }],
        "subject": _patient_ref(subject_id),
        "date": when,
        "context": {
            "encounter": [{"reference": f"Encounter/{hadm_id}"}],
            "sourcePatientInfo": _patient_ref(subject_id),
        },
        "content": [{
            "attachment": {
                "contentType": "text/plain",
                "data": None,  # omit raw PHI in simulation
                "title": title,
            },
        }],
        "extension": [{
            "url": "https://medai.platform/extensions/note-source",
            "valueString": doc.get("source") or "synthetic",
        }] + ([{
            "url": "https://medai.platform/extensions/original-note-id",
            "valueString": doc["original_note_id"],
        }] if doc.get("original_note_id") else []),
    }


def _risk_assessment_resource(
    risk_source: str,
    risk_value: float,
    rationale: str,
    subject_id: Any,
    hadm_id: Any,
) -> Dict[str, Any]:
    return {
        "resourceType": "RiskAssessment",
        "id": f"risk-{risk_source}-{hadm_id}",
        "status": "final",
        "subject": _patient_ref(subject_id),
        "encounter": {"reference": f"Encounter/{hadm_id}"},
        "method": {"text": risk_source},
        "prediction": [{
            "outcome": {"text": risk_source},
            "probabilityDecimal": round(float(risk_value or 0), 3),
            "rationale": rationale,
        }],
    }


def _care_plan_resource(plan: Dict[str, Any], subject_id: Any, hadm_id: Any) -> Dict[str, Any]:
    activities = []
    for item in plan.get("activities", []) or []:
        activities.append({"detail": {"description": str(item)}})
    return {
        "resourceType": "CarePlan",
        "id": f"careplan-{hadm_id}",
        "status": plan.get("status", "active"),
        "intent": "plan",
        "subject": _patient_ref(subject_id),
        "encounter": {"reference": f"Encounter/{hadm_id}"},
        "title": plan.get("title", "Discharge plan"),
        "description": plan.get("description", ""),
        "activity": activities,
    }


# ---------------------------------------------------------------------------
# Resource endpoints
# ---------------------------------------------------------------------------
@app.get("/fhir/Patient/{patient_id}")
async def fhir_patient(patient_id: str):
    summary = await _fetch_summary(patient_id)
    if not summary:
        raise HTTPException(status_code=404, detail="patient not found")
    return _patient_resource(summary)


@app.get("/fhir/Encounter/{hadm_id}")
async def fhir_encounter(hadm_id: str, subject_id: Optional[str] = None):
    if subject_id is None:
        raise HTTPException(status_code=400, detail="subject_id query parameter required")
    timeline = await _fetch_timeline(subject_id, hadm_id)
    header = timeline.get("admission") or {"hadm_id": hadm_id, "subject_id": subject_id}
    return _encounter_resource(header)


@app.get("/fhir/Encounter")
async def fhir_encounter_search(
    patient: str = Query(..., description="Patient/{id} or subject_id"),
):
    # Support ``?patient=Patient/SIM-abc`` form
    subject_id = patient.split("/")[-1]
    summary = await _fetch_summary(subject_id)
    if not summary:
        return _bundle([])
    return _bundle([
        _encounter_resource({**adm, "subject_id": subject_id})
        for adm in summary.get("admissions", [])
    ])


@app.get("/fhir/Observation")
async def fhir_observation_search(
    patient: str = Query(...),
    encounter: Optional[str] = Query(None),
    category: Optional[str] = Query(None, description="vital-signs | laboratory"),
):
    subject_id = patient.split("/")[-1]
    hadm_id = encounter.split("/")[-1] if encounter else None
    if hadm_id is None:
        raise HTTPException(status_code=400, detail="encounter required for Observation search")
    timeline = await _fetch_timeline(subject_id, hadm_id)
    events = timeline.get("events") or []
    wanted_types = {"vital", "lab"}
    if category == "vital-signs":
        wanted_types = {"vital"}
    elif category == "laboratory":
        wanted_types = {"lab"}
    return _bundle([
        _observation_resource({**e, "subject_id": subject_id})
        for e in events if e.get("event_type") in wanted_types
    ])


@app.get("/fhir/Observation/{hadm_id}")
async def fhir_observations_by_encounter(hadm_id: str, subject_id: Optional[str] = None):
    # Legacy endpoint retained
    if subject_id is None:
        raise HTTPException(status_code=400, detail="subject_id query parameter required")
    return await fhir_observation_search(
        patient=f"Patient/{subject_id}",
        encounter=f"Encounter/{hadm_id}",
    )


@app.get("/fhir/Condition")
async def fhir_condition_search(patient: str = Query(...), encounter: Optional[str] = Query(None)):
    subject_id = patient.split("/")[-1]
    if encounter:
        hadm_id = encounter.split("/")[-1]
        journey = await _fetch_journey(subject_id, hadm_id)
        diagnoses = journey.get("diagnoses") or []
    else:
        # All conditions across all admissions via timeline-join would be expensive;
        # return conditions from most recent admission.
        summary = await _fetch_summary(subject_id)
        admissions = summary.get("admissions") or []
        if not admissions:
            return _bundle([])
        hadm_id = str(admissions[-1].get("hadm_id"))
        journey = await _fetch_journey(subject_id, hadm_id)
        diagnoses = journey.get("diagnoses") or []
    return _bundle([_condition_resource(d, subject_id) for d in diagnoses])


@app.get("/fhir/Procedure")
async def fhir_procedure_search(patient: str = Query(...), encounter: Optional[str] = Query(None)):
    subject_id = patient.split("/")[-1]
    if encounter:
        hadm_id = encounter.split("/")[-1]
    else:
        summary = await _fetch_summary(subject_id)
        admissions = summary.get("admissions") or []
        if not admissions:
            return _bundle([])
        hadm_id = str(admissions[-1].get("hadm_id"))
    journey = await _fetch_journey(subject_id, hadm_id)
    procedures = journey.get("procedures") or []
    return _bundle([_procedure_resource(p, subject_id) for p in procedures])


@app.get("/fhir/MedicationRequest")
async def fhir_medication_request_search(
    patient: str = Query(...),
    encounter: str = Query(...),
):
    subject_id = patient.split("/")[-1]
    hadm_id = encounter.split("/")[-1]
    meds = await _fetch_medications(subject_id, hadm_id)
    return _bundle([_medication_request_resource(m, subject_id) for m in meds])


@app.get("/fhir/DiagnosticReport")
async def fhir_diagnostic_report_search(
    patient: str = Query(...),
    encounter: str = Query(...),
):
    subject_id = patient.split("/")[-1]
    hadm_id = encounter.split("/")[-1]
    labs = await _fetch_labs(subject_id, hadm_id)
    panels = labs.get("panels") or {}
    reports = []
    for panel_name, panel_data in panels.items():
        tests = panel_data.get("tests") or panel_data.get("results") or []
        if tests:
            reports.append(_diagnostic_report_resource(panel_name, tests, subject_id))
    return _bundle(reports)


@app.get("/fhir/DocumentReference")
async def fhir_document_reference_search(
    patient: str = Query(...),
    encounter: Optional[str] = Query(None),
):
    subject_id = patient.split("/")[-1]
    if encounter is None:
        raise HTTPException(status_code=400, detail="encounter required")
    hadm_id = encounter.split("/")[-1]
    # Discharge summary exists if encounter is finished; clinical-scribe notes
    # are held by app_10 — best-effort fetch.
    docs: List[Dict[str, Any]] = []
    try:
        notes_res = await _client()._get_client("clinical_scribe").get(
            f"/notes/by-encounter/{hadm_id}"
        )
        data = notes_res.get("data") if isinstance(notes_res, dict) else None
        if isinstance(data, list):
            docs = data
        elif isinstance(data, dict):
            docs = data.get("notes") or []
    except Exception as exc:  # noqa: BLE001
        logger.debug("scribe_notes_unavailable: %s", exc)
    # Also expose a synthetic discharge summary reference if encounter is finished
    timeline = await _fetch_timeline(subject_id, hadm_id)
    hadm = timeline.get("admission") or {}
    if hadm.get("dischtime"):
        docs.append({
            "note_type": "discharge-summary",
            "title": "Discharge Summary",
            "charttime": hadm.get("dischtime"),
        })
    return _bundle([_document_reference_resource(d, subject_id, hadm_id) for d in docs])


@app.get("/fhir/AllergyIntolerance/{patient_id}")
async def fhir_allergy(patient_id: str):
    # MIMIC-IV has no structured allergy table — return empty Bundle.
    return _bundle([])


@app.get("/fhir/AllergyIntolerance")
async def fhir_allergy_search(patient: str = Query(...)):
    return _bundle([])


@app.get("/fhir/RiskAssessment")
async def fhir_risk_assessment_search(
    patient: str = Query(...),
    encounter: str = Query(...),
):
    subject_id = patient.split("/")[-1]
    hadm_id = encounter.split("/")[-1]
    assessments: List[Dict[str, Any]] = []

    # Oncology risk — readmission + mortality
    try:
        onco_res = await _client().oncology_ai.get(f"/risk/{hadm_id}")
        onco = onco_res.get("data") or {}
        if onco:
            assessments.append(_risk_assessment_resource(
                "oncology-readmission-30d",
                onco.get("readmission_30d_risk", 0),
                "XGBoost model trained on MIMIC-IV oncology cohort (AUROC 0.734)",
                subject_id, hadm_id,
            ))
            assessments.append(_risk_assessment_resource(
                "oncology-mortality",
                onco.get("mortality_risk", 0),
                "XGBoost model trained on MIMIC-IV oncology cohort (AUROC 0.897)",
                subject_id, hadm_id,
            ))
    except Exception as exc:  # noqa: BLE001
        logger.debug("onco_risk_unavailable: %s", exc)

    # Deterioration — latest NEWS2/PEWS/IMEWS
    try:
        det_res = await _client()._get_client("deterioration").get(
            f"/deterioration/history/{hadm_id}"
        )
        history = det_res.get("data") or []
        if history:
            latest = history[-1]
            score = (latest.get("score") or {}).get("total", 0)
            system = latest.get("scoring_system", "news2").upper()
            assessments.append(_risk_assessment_resource(
                f"{system}-score",
                min(1.0, score / 15.0),
                f"{system}={score} — {(latest.get('score') or {}).get('recommended_response', '')}",
                subject_id, hadm_id,
            ))
    except Exception as exc:  # noqa: BLE001
        logger.debug("det_risk_unavailable: %s", exc)

    return _bundle(assessments)


@app.get("/fhir/CarePlan/{hadm_id}")
async def fhir_care_plan(hadm_id: str, subject_id: Optional[str] = None):
    if subject_id is None:
        raise HTTPException(status_code=400, detail="subject_id required")
    plan: Dict[str, Any] = {}
    try:
        plan_res = await _client()._get_client("discharge_lounge").get(
            f"/patient/{hadm_id}/discharge-plan"
        )
        plan = plan_res.get("data") or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("discharge_plan_unavailable: %s", exc)
    return _care_plan_resource(plan, subject_id, hadm_id)


# ---------------------------------------------------------------------------
# Write endpoints (POST)
# ---------------------------------------------------------------------------
@app.post("/fhir/Bundle")
async def post_bundle(bundle: dict):
    """Accept a FHIR transaction Bundle — in simulation mode stored in memory."""
    entries = bundle.get("entry", [])
    _state["inbox"].append(bundle)
    return {
        "status": "ok",
        "resourceType": "Bundle",
        "type": "transaction-response",
        "entry": [{"response": {"status": "201 Created"}} for _ in entries],
    }


@app.post("/fhir/MedicationRequest")
async def post_medication_request(payload: dict):
    _state["medication_requests"].append(payload)
    return {
        "status": "ok",
        "resourceType": "MedicationRequest",
        "id": str(abs(hash(str(payload)))),
    }


@app.post("/reset")
async def reset_fhir():
    _state["inbox"] = []
    _state["medication_requests"] = []
    return {"status": "ok", "reset": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8219)
