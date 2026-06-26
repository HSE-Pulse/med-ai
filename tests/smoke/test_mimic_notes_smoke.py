"""Smoke tests for the MIMIC-IV-Note flow against the running stack.

Each test does one HTTP call and pins one shape. Together they prove the
chain Scribe -> Patient Journey -> FHIR Gateway is live and serving the
demo patient (subject_id=16473192, hadm_id=21079163).

Skipped automatically when services aren't running.
"""

from __future__ import annotations

import httpx
import pytest

from .conftest import (
    DEMO_HADM,
    DEMO_SUBJECT,
    FHIR,
    PATIENT_JOURNEY,
    SCRIBE,
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,url", [
    ("scribe", SCRIBE),
    ("patient_journey", PATIENT_JOURNEY),
    ("fhir", FHIR),
])
def test_service_healthy(http, name, url):
    r = http.get(f"{url}/health")
    assert r.status_code == 200, f"{name} unhealthy: {r.status_code}"
    body = r.json()
    assert body.get("status") == "ok", f"{name} status: {body}"


# ---------------------------------------------------------------------------
# Scribe — the new persistence-backed endpoints
# ---------------------------------------------------------------------------

def test_scribe_notes_by_encounter_returns_notes_for_demo_admission(http):
    r = http.get(f"{SCRIBE}/notes/by-encounter/{DEMO_HADM}")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("data"), list), f"expected list, got: {type(body.get('data'))}"
    assert len(body["data"]) > 0, "no MIMIC notes ingested for demo patient — run a DT cascade"


def test_scribe_notes_carry_mimic_provenance(http):
    """Provenance is the whole point of the MIMIC ingestion: every note that
    came from MIMIC must be tagged so consumers (and audit) can tell."""
    r = http.get(f"{SCRIBE}/notes/by-encounter/{DEMO_HADM}")
    notes = r.json().get("data", [])
    mimic_notes = [n for n in notes if n.get("source") == "mimic_iv_note"]
    assert mimic_notes, "no notes have source=mimic_iv_note"
    n = mimic_notes[0]
    assert n.get("original_note_id"), "MIMIC note missing original_note_id"
    assert n.get("original_charttime"), "MIMIC note missing original_charttime"


def test_scribe_notes_by_patient_endpoint_works(http):
    r = http.get(f"{SCRIBE}/notes/by-patient/{DEMO_SUBJECT}")
    assert r.status_code == 200
    assert isinstance(r.json().get("data"), list)


def test_scribe_unknown_encounter_returns_empty_list_not_500(http):
    r = http.get(f"{SCRIBE}/notes/by-encounter/00000000")
    assert r.status_code == 200
    assert r.json().get("data") == []


# ---------------------------------------------------------------------------
# Patient Journey — clinical_note events show in unified timeline
# ---------------------------------------------------------------------------

def test_timeline_includes_clinical_note_events(http):
    """The bug being guarded: subscription was declared, no handler, events
    silently dropped, timeline empty of notes."""
    r = http.get(f"{PATIENT_JOURNEY}/patient/{DEMO_SUBJECT}/admission/{DEMO_HADM}/timeline")
    assert r.status_code == 200
    data = r.json().get("data") or {}
    events = data.get("events") or []
    note_events = [e for e in events if e.get("event_type") == "clinical_note"]
    assert note_events, "timeline has zero clinical_note events for demo patient"


def test_timeline_clinical_note_event_shape(http):
    """The event must validate against TimelineEventSchema (the missing
    source_table field was the second bug fixed in this chain)."""
    r = http.get(f"{PATIENT_JOURNEY}/patient/{DEMO_SUBJECT}/admission/{DEMO_HADM}/timeline")
    events = (r.json().get("data") or {}).get("events") or []
    notes = [e for e in events if e.get("event_type") == "clinical_note"]
    assert notes
    e = notes[0]
    assert e["category"] == "documentation"
    assert e["source_table"] == "scribe.notes"
    assert e["details"]["note_id"]
    assert e["details"]["note_type"]
    assert e["details"]["source"] in ("mimic_iv_note", "synthetic")


# ---------------------------------------------------------------------------
# FHIR Gateway — DocumentReference Bundle (the just-fixed shape parser)
# ---------------------------------------------------------------------------

def test_fhir_document_reference_returns_bundle_for_demo_admission(http):
    r = http.get(f"{FHIR}/fhir/DocumentReference",
                 params={"patient": f"Patient/{DEMO_SUBJECT}", "encounter": f"Encounter/{DEMO_HADM}"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "Bundle"
    assert body.get("total", 0) > 0, "DocumentReference bundle is empty (was the parser bug)"


def test_fhir_document_reference_uses_loinc_coding(http):
    r = http.get(f"{FHIR}/fhir/DocumentReference",
                 params={"patient": f"Patient/{DEMO_SUBJECT}", "encounter": f"Encounter/{DEMO_HADM}"})
    entries = r.json().get("entry") or []
    assert entries
    coding = entries[0]["resource"]["type"]["coding"][0]
    assert coding["system"] == "http://loinc.org"
    # Discharge summary is the most common type for this demo patient
    assert coding["code"] in {"18842-5", "11506-3", "11488-4", "34109-9", "11490-0"}


def test_fhir_document_reference_carries_mimic_source_extension(http):
    """The provenance extension is what tells an EHR consumer 'this is real
    MIMIC narrative replayed through the simulator'. Lock it down."""
    r = http.get(f"{FHIR}/fhir/DocumentReference",
                 params={"patient": f"Patient/{DEMO_SUBJECT}", "encounter": f"Encounter/{DEMO_HADM}"})
    entries = r.json().get("entry") or []
    assert entries
    extensions = entries[0]["resource"].get("extension", [])
    sources = [e["valueString"] for e in extensions if "note-source" in e["url"]]
    assert "mimic_iv_note" in sources, f"no mimic_iv_note source extension; got {sources}"


def test_fhir_document_reference_requires_encounter(http):
    """Endpoint contract: encounter is required (not optional)."""
    r = http.get(f"{FHIR}/fhir/DocumentReference",
                 params={"patient": f"Patient/{DEMO_SUBJECT}"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Cross-service consistency
# ---------------------------------------------------------------------------

def test_scribe_and_fhir_return_same_count_of_notes(http):
    """If they don't, the FHIR Gateway is filtering or losing notes silently."""
    scribe = http.get(f"{SCRIBE}/notes/by-encounter/{DEMO_HADM}").json().get("data", [])
    fhir_bundle = http.get(
        f"{FHIR}/fhir/DocumentReference",
        params={"patient": f"Patient/{DEMO_SUBJECT}", "encounter": f"Encounter/{DEMO_HADM}"},
    ).json()
    fhir_count = fhir_bundle.get("total", 0)
    # FHIR may add a synthetic discharge-summary stub if the encounter is
    # finished; allow that one extra.
    assert fhir_count in (len(scribe), len(scribe) + 1), (
        f"scribe={len(scribe)} fhir={fhir_count}"
    )


def test_scribe_and_journey_agree_on_note_ids(http):
    """Notes Scribe persisted should appear in patient_journey's timeline.
    Mismatch means the Kafka handler dropped events."""
    scribe_notes = http.get(f"{SCRIBE}/notes/by-encounter/{DEMO_HADM}").json().get("data", [])
    timeline = http.get(
        f"{PATIENT_JOURNEY}/patient/{DEMO_SUBJECT}/admission/{DEMO_HADM}/timeline"
    ).json()
    events = (timeline.get("data") or {}).get("events") or []
    journey_ids = {e["details"]["note_id"] for e in events
                   if e.get("event_type") == "clinical_note"}
    scribe_ids = {n["note_id"] for n in scribe_notes}
    # Journey may lag (eventual consistency on Kafka). Require at least
    # one overlap rather than full equality.
    assert scribe_ids & journey_ids, (
        f"no overlap: scribe={scribe_ids} journey={journey_ids}"
    )
