"""Unit tests for the MIMIC-IV-Note ingestion pipeline.

Covers the pure-function side of the chain that lifts a real MIMIC discharge
summary out of Mongo and lands it in the FHIR DocumentReference resource.

No Mongo, no Kafka, no HTTP. Each test exercises one helper.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Digital Twin helpers — MIMIC -> Scribe vocabulary mapping + specialty
# ---------------------------------------------------------------------------

class TestScribeNoteTypeMapping:
    """`_scribe_note_type_for` translates MIMIC note codes to Scribe's vocab."""

    def test_ds_maps_to_discharge_summary(self):
        from shared.integration.digital_twin import _scribe_note_type_for
        assert _scribe_note_type_for("DS") == "discharge_summary"

    def test_lowercase_ds_still_maps(self):
        from shared.integration.digital_twin import _scribe_note_type_for
        assert _scribe_note_type_for("ds") == "discharge_summary"

    def test_rr_maps_to_procedure_note(self):
        from shared.integration.digital_twin import _scribe_note_type_for
        assert _scribe_note_type_for("RR") == "procedure_note"

    def test_unknown_code_falls_back_to_admission_note(self):
        from shared.integration.digital_twin import _scribe_note_type_for
        assert _scribe_note_type_for("XYZ") == "admission_note"

    def test_none_falls_back_to_admission_note(self):
        from shared.integration.digital_twin import _scribe_note_type_for
        assert _scribe_note_type_for(None) == "admission_note"


class TestSpecialtyInference:
    """`_infer_specialty` reads the first diagnosis's ICD prefix."""

    def test_cardiology_from_i_prefix(self):
        from shared.integration.digital_twin import _infer_specialty
        assert _infer_specialty([{"icd_code": "I21.9"}]) == "cardiology"

    def test_oncology_from_c_prefix(self):
        from shared.integration.digital_twin import _infer_specialty
        assert _infer_specialty([{"icd_code": "C34.10"}]) == "oncology"

    def test_unknown_prefix_returns_none(self):
        from shared.integration.digital_twin import _infer_specialty
        assert _infer_specialty([{"icd_code": "Z99.99"}]) is None

    def test_empty_list_returns_none(self):
        from shared.integration.digital_twin import _infer_specialty
        assert _infer_specialty([]) is None

    def test_none_returns_none(self):
        from shared.integration.digital_twin import _infer_specialty
        assert _infer_specialty(None) is None

    def test_missing_icd_code_field(self):
        from shared.integration.digital_twin import _infer_specialty
        assert _infer_specialty([{"description": "no code here"}]) is None


# ---------------------------------------------------------------------------
# MIMIC accessor — int coercion + bad-input handling
# ---------------------------------------------------------------------------

def _stub_mongo(monkeypatch, find_result=None):
    """Stub MongoManager.mimic_notes via monkeypatch (auto-restored).

    Returns (manager, mock_collection). Avoids leaking the class-level
    property override into other tests in the session.
    """
    from shared.db.mongo import MongoManager

    mock_collection = MagicMock()
    mock_collection.find.return_value = find_result or []
    mock_db = {"discharge": mock_collection}
    monkeypatch.setattr(MongoManager, "mimic_notes", property(lambda self: mock_db))
    mgr = MongoManager.__new__(MongoManager)  # bypass __init__ — no real Mongo
    return mgr, mock_collection


class TestNotesForAdmission:
    """`MongoManager.notes_for_admission` coerces hadm_id to int and returns []
    for inputs that can't be coerced. The actual Mongo lookup is mocked."""

    def test_returns_empty_for_none(self, monkeypatch):
        mgr, _ = _stub_mongo(monkeypatch)
        assert mgr.notes_for_admission(None) == []

    def test_returns_empty_for_non_numeric_string(self, monkeypatch):
        mgr, _ = _stub_mongo(monkeypatch)
        assert mgr.notes_for_admission("SIM-foo-bar") == []

    def test_coerces_string_digits_to_int(self, monkeypatch):
        mgr, coll = _stub_mongo(monkeypatch, find_result=[{"note_id": "n1"}])
        result = mgr.notes_for_admission("21079163")
        assert result == [{"note_id": "n1"}]
        # Mongo query must use int, not str
        assert coll.find.call_args.args[0] == {"hadm_id": 21079163}

    def test_passes_int_unchanged(self, monkeypatch):
        mgr, coll = _stub_mongo(monkeypatch, find_result=[])
        mgr.notes_for_admission(21079163)
        assert coll.find.call_args.args[0]["hadm_id"] == 21079163


class TestNotesForPatient:
    """Same int-coercion contract on subject_id."""

    def test_empty_for_none(self, monkeypatch):
        mgr, _ = _stub_mongo(monkeypatch)
        assert mgr.notes_for_patient(None) == []

    def test_filters_by_subject_id_as_int(self, monkeypatch):
        mgr, coll = _stub_mongo(monkeypatch, find_result=[{"note_id": "n1"}])
        mgr.notes_for_patient("16473192")
        assert coll.find.call_args.args[0] == {"subject_id": 16473192}


# ---------------------------------------------------------------------------
# FHIR DocumentReference resource builder
# ---------------------------------------------------------------------------

class TestDocumentReferenceResource:
    """`_document_reference_resource` translates a Scribe note dict into a
    FHIR R4 DocumentReference. Tests pin the LOINC mapping + provenance
    extensions so the contract with EHRs is locked down."""

    def _build(self, **overrides):
        from app_19_fhir.backend.app.main import _document_reference_resource
        doc = {
            "note_id": "abc-123",
            "note_type": "discharge_summary",
            "status": "approved",
            "source": "mimic_iv_note",
            "original_note_id": "16473192-DS-12",
            "original_charttime": "2163-06-12 00:00:00",
        }
        doc.update(overrides)
        return _document_reference_resource(doc, subject_id="16473192", hadm_id="21079163")

    def test_resource_type_is_document_reference(self):
        assert self._build()["resourceType"] == "DocumentReference"

    def test_id_is_derived_from_note_id_not_a_random_hash(self):
        # Stable id is needed so consumers can de-dup across polls
        assert self._build()["id"] == "doc-abc-123"

    def test_discharge_summary_gets_loinc_18842_5(self):
        coding = self._build()["type"]["coding"][0]
        assert coding["system"] == "http://loinc.org"
        assert coding["code"] == "18842-5"
        assert coding["display"] == "Discharge summary"

    def test_unknown_note_type_falls_back_to_loinc_34109_9(self):
        coding = self._build(note_type="unknown_type")["type"]["coding"][0]
        assert coding["code"] == "34109-9"

    def test_progress_note_maps_to_11506_3(self):
        coding = self._build(note_type="progress_note")["type"]["coding"][0]
        assert coding["code"] == "11506-3"

    def test_approved_status_marks_doc_status_final(self):
        assert self._build(status="approved")["docStatus"] == "final"

    def test_draft_status_marks_doc_status_preliminary(self):
        assert self._build(status="draft")["docStatus"] == "preliminary"

    def test_subject_reference_is_pseudonymised_in_simulation_mode(self):
        # FHIR Gateway pseudonymises subject_ids in simulation mode
        # (deterministic SHA256[:12]) so PHI never leaves the boundary
        import hashlib

        expected = "SIM-" + hashlib.sha256(b"16473192").hexdigest()[:12]
        ref = self._build()["subject"]["reference"]
        assert ref == f"Patient/{expected}", f"got {ref}"

    def test_encounter_reference_uses_passed_hadm_id(self):
        assert self._build()["context"]["encounter"][0]["reference"] == "Encounter/21079163"

    def test_date_prefers_original_charttime_over_generated_at(self):
        # MIMIC provenance: the real clinical timestamp is original_charttime,
        # not when the simulator regenerated the note
        r = self._build(
            original_charttime="2163-06-12 00:00:00",
            generated_at="2026-04-26 10:00:00",
        )
        assert r["date"] == "2163-06-12 00:00:00"

    def test_date_falls_back_to_generated_at(self):
        r = self._build(original_charttime=None, generated_at="2026-04-26 10:00:00")
        assert r["date"] == "2026-04-26 10:00:00"

    def test_mimic_source_extension_included(self):
        ext = self._build()["extension"]
        sources = [e["valueString"] for e in ext if "note-source" in e["url"]]
        assert sources == ["mimic_iv_note"]

    def test_original_note_id_extension_included_when_present(self):
        ext = self._build()["extension"]
        originals = [e["valueString"] for e in ext if "original-note-id" in e["url"]]
        assert originals == ["16473192-DS-12"]

    def test_original_note_id_extension_omitted_when_absent(self):
        ext = self._build(original_note_id=None)["extension"]
        originals = [e for e in ext if "original-note-id" in e["url"]]
        assert originals == []

    def test_synthetic_source_marked_as_such(self):
        ext = self._build(source="synthetic")["extension"]
        sources = [e["valueString"] for e in ext if "note-source" in e["url"]]
        assert sources == ["synthetic"]

    def test_clinical_note_category_set(self):
        cat = self._build()["category"][0]["coding"][0]
        assert cat["code"] == "clinical-note"

    def test_title_includes_note_type_and_date(self):
        title = self._build()["content"][0]["attachment"]["title"]
        assert "Discharge Summary" in title
        assert "2163-06-12" in title


# ---------------------------------------------------------------------------
# Scribe schema — provenance fields are accepted on the request models
# ---------------------------------------------------------------------------

class TestScribeProvenanceSchema:
    """Pydantic guarantees: the new provenance fields validate cleanly and
    default to the synthetic-source values when omitted."""

    def test_generate_from_text_accepts_mimic_provenance(self):
        from app_10_clinical_scribe.backend.app.schemas import GenerateNoteFromTextRequest
        req = GenerateNoteFromTextRequest(
            clinical_text="any text",
            patient_id=16473192,
            hadm_id=21079163,
            source="mimic_iv_note",
            original_note_id="16473192-DS-12",
            original_charttime="2163-06-12 00:00:00",
        )
        assert req.source == "mimic_iv_note"
        assert req.original_note_id == "16473192-DS-12"

    def test_source_defaults_to_synthetic(self):
        from app_10_clinical_scribe.backend.app.schemas import GenerateNoteFromTextRequest
        req = GenerateNoteFromTextRequest(clinical_text="x")
        assert req.source == "synthetic"
        assert req.original_note_id is None

    def test_clinical_note_persists_provenance_round_trip(self):
        from app_10_clinical_scribe.backend.app.schemas import ClinicalNote
        note = ClinicalNote(
            note_id="abc",
            source="mimic_iv_note",
            original_note_id="x",
            original_charttime="2163-06-12",
        )
        dumped = note.model_dump()
        assert dumped["source"] == "mimic_iv_note"
        assert dumped["original_note_id"] == "x"

    def test_hadm_id_accepts_str_for_sim_patients(self):
        # SIM-prefixed hadm_ids are strings; real MIMIC hadm_ids are ints.
        # Both must validate.
        from app_10_clinical_scribe.backend.app.schemas import GenerateNoteFromTextRequest
        sim = GenerateNoteFromTextRequest(clinical_text="x", hadm_id="SIM-abc-1")
        real = GenerateNoteFromTextRequest(clinical_text="x", hadm_id=21079163)
        assert sim.hadm_id == "SIM-abc-1"
        assert real.hadm_id == 21079163
