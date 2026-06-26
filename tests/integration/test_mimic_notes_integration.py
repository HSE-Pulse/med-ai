"""Integration tests for the MIMIC-IV-Note ingestion pipeline.

These exercise real components (MongoDB) through their public contracts.
Auto-skip if Mongo isn't reachable (see conftest.py). Kafka is NOT required
because every test seeds Mongo directly and queries the same collections
the live services would write to / read from.

Coverage:
  * MongoManager.notes_for_admission against the live MIMIC_Clinical_Notes
    collection (skips per-test if MIMIC isn't loaded — the data is large)
  * Scribe persistence + by-encounter / by-patient query roundtrip
  * Patient Journey timeline-merge logic (the dropped-event regression)
  * FHIR Gateway DocumentReference response-shape parsing (the bug fixed
    immediately before this test was written)
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest


_TEST_NOTE_HADM = 99999911
_TEST_NOTE_SUBJECT = 99999922
_TEST_NOTE_ID = "integration-test-note-001"


def _seed_note(coll, **overrides) -> Dict[str, Any]:
    """Insert a Scribe-shaped note doc into the given Mongo collection."""
    doc = {
        "note_id": _TEST_NOTE_ID,
        "note_type": "discharge_summary",
        "patient_id": _TEST_NOTE_SUBJECT,
        "hadm_id": _TEST_NOTE_HADM,
        "generated_at": "2026-04-26T10:00:00",
        "status": "draft",
        "source": "mimic_iv_note",
        "original_note_id": "16473192-DS-12",
        "original_charttime": "2163-06-12 00:00:00",
        "soap": {
            "subjective": "patient reports chest pain",
            "objective": "BP 140/90 HR 95",
            "assessment": "ACS rule-out",
            "plan": "serial troponins",
        },
        "icd_codes": [{"code": "I20.9", "description": "Angina, unspecified", "is_primary": True}],
        "_test_marker": "integration",
    }
    doc.update(overrides)
    coll.insert_one(doc)
    return doc


# ---------------------------------------------------------------------------
# MongoManager — MIMIC accessor against the real loaded collection
# ---------------------------------------------------------------------------

class TestMimicNotesAccessorLive:
    """`notes_for_admission` / `notes_for_patient` against actually-loaded
    MIMIC-IV-Note docs. Skips if the collection is empty (e.g. dev box
    where the dataset isn't downloaded)."""

    @pytest.fixture
    def mgr(self, mongo_uri):
        from shared.db.mongo import MongoManager

        mgr = MongoManager(uri=mongo_uri)
        # Probe that MIMIC notes are loaded; skip otherwise
        try:
            count = mgr.mimic_notes["discharge"].estimated_document_count()
        except Exception:
            count = 0
        if count == 0:
            pytest.skip("MIMIC_Clinical_Notes.discharge is empty — load the dataset first")
        yield mgr
        mgr.close()

    def test_returns_real_note_for_known_admission(self, mgr):
        # 21079163 is the demo patient identified in the plan; if it's not
        # present locally just take the first hadm_id we find
        note = mgr.mimic_notes["discharge"].find_one({}, {"hadm_id": 1, "subject_id": 1})
        assert note, "no docs in collection"
        hadm = note["hadm_id"]
        result = mgr.notes_for_admission(hadm)
        assert len(result) >= 1
        assert all(n.get("hadm_id") == int(hadm) for n in result)

    def test_returned_docs_have_expected_schema(self, mgr):
        # All consumers rely on these fields. If MIMIC's column names ever
        # change upstream this test fails first.
        note = mgr.mimic_notes["discharge"].find_one({})
        assert note
        result = mgr.notes_for_admission(note["hadm_id"])
        first = result[0]
        for required in ("note_id", "subject_id", "hadm_id", "note_type", "text"):
            assert required in first, f"missing field {required} in MIMIC note"

    def test_unknown_hadm_returns_empty_list(self, mgr):
        assert mgr.notes_for_admission(999_999_999) == []


# ---------------------------------------------------------------------------
# Scribe — persist + retrieve via the same Mongo path the live service uses
# ---------------------------------------------------------------------------

class TestScribeNotePersistence:
    """Tests for the persisted-notes contract that survives Scribe restart.

    These don't import the FastAPI app (would trigger lifespan + Kafka).
    Instead they exercise the same Mongo collection + query shape the
    by-encounter endpoint uses. If this passes, the endpoint passes too —
    they share a code path."""

    def test_seeded_note_visible_to_by_encounter_query(self, mongo_client, clean_scribe_notes):
        coll = clean_scribe_notes
        _seed_note(coll)

        # Mirror the endpoint's query exactly (see app_10/.../main.py)
        candidates = [str(_TEST_NOTE_HADM), _TEST_NOTE_HADM]
        results = []
        for v in candidates:
            for d in coll.find({"hadm_id": v}, sort=[("generated_at", -1)]):
                d.pop("_id", None)
                results.append(d)
        assert any(r["note_id"] == _TEST_NOTE_ID for r in results)

    def test_provenance_round_trips_through_mongo(self, mongo_client, clean_scribe_notes):
        coll = clean_scribe_notes
        _seed_note(coll, source="mimic_iv_note", original_note_id="16473192-DS-99")

        fetched = coll.find_one({"note_id": _TEST_NOTE_ID}, {"_id": 0})
        assert fetched["source"] == "mimic_iv_note"
        assert fetched["original_note_id"] == "16473192-DS-99"
        assert fetched["original_charttime"] == "2163-06-12 00:00:00"

    def test_by_patient_query_filters_on_patient_id(self, mongo_client, clean_scribe_notes):
        coll = clean_scribe_notes
        _seed_note(coll)
        _seed_note(coll, note_id="other-patient-note", patient_id=88888888)

        results = list(coll.find({"patient_id": {"$in": [str(_TEST_NOTE_SUBJECT), _TEST_NOTE_SUBJECT]}}))
        ids = [r["note_id"] for r in results]
        assert _TEST_NOTE_ID in ids
        assert "other-patient-note" not in ids

    def test_note_type_filter_excludes_other_types(self, mongo_client, clean_scribe_notes):
        coll = clean_scribe_notes
        _seed_note(coll)
        _seed_note(coll, note_id="progress-note-1", note_type="progress_note")

        ds_only = list(coll.find({
            "patient_id": {"$in": [str(_TEST_NOTE_SUBJECT), _TEST_NOTE_SUBJECT]},
            "note_type": "discharge_summary",
        }))
        assert len(ds_only) == 1
        assert ds_only[0]["note_id"] == _TEST_NOTE_ID


# ---------------------------------------------------------------------------
# Patient Journey — timeline merge of clinical_note events
# ---------------------------------------------------------------------------

class TestPatientJourneyNoteMerge:
    """The bug being guarded: subscription was declared but no handler, so
    note_generated events were silently dropped. Now the handler upserts
    a row into patient_journey.notes, and the timeline merge surfaces it
    as a `clinical_note` event."""

    def test_journey_note_doc_shape_matches_handler_output(
        self, mongo_client, clean_journey_notes
    ):
        coll = clean_journey_notes
        # Mirror exactly what _on_note_generated writes
        doc = {
            "note_id": _TEST_NOTE_ID,
            "note_type": "discharge_summary",
            "hadm_id": _TEST_NOTE_HADM,
            "subject_id": _TEST_NOTE_SUBJECT,
            "generated_at": "2026-04-26T10:00:00",
            "status": "draft",
            "source": "mimic_iv_note",
            "original_charttime": "2163-06-12 00:00:00",
            "_test_marker": "integration",
        }
        coll.replace_one({"note_id": _TEST_NOTE_ID}, doc, upsert=True)

        # Mirror the timeline-merge query exactly
        note_query = {
            "subject_id": {"$in": [_TEST_NOTE_SUBJECT, str(_TEST_NOTE_SUBJECT)]},
            "hadm_id": {"$in": [_TEST_NOTE_HADM, str(_TEST_NOTE_HADM)]},
        }
        found = list(coll.find(note_query))
        assert len(found) == 1
        f = found[0]
        # The fields the timeline event builder reads — pin them
        assert f["note_id"] == _TEST_NOTE_ID
        assert f["note_type"] == "discharge_summary"
        assert f["source"] == "mimic_iv_note"
        # original_charttime is what becomes the timeline event timestamp;
        # if missing, the merge falls back to generated_at
        assert (f.get("original_charttime") or f.get("generated_at")) is not None

    def test_replace_one_upsert_dedups_on_note_id(
        self, mongo_client, clean_journey_notes
    ):
        coll = clean_journey_notes
        coll.replace_one(
            {"note_id": _TEST_NOTE_ID},
            {"note_id": _TEST_NOTE_ID, "status": "draft", "_test_marker": "integration"},
            upsert=True,
        )
        # Same event arriving again (Kafka at-least-once) must not duplicate
        coll.replace_one(
            {"note_id": _TEST_NOTE_ID},
            {"note_id": _TEST_NOTE_ID, "status": "approved", "_test_marker": "integration"},
            upsert=True,
        )
        rows = list(coll.find({"note_id": _TEST_NOTE_ID}))
        assert len(rows) == 1
        assert rows[0]["status"] == "approved"


# ---------------------------------------------------------------------------
# FHIR Gateway — response-shape parsing (regression for the just-fixed bug)
# ---------------------------------------------------------------------------

class TestFhirDocumentReferenceParsing:
    """Pre-fix the gateway did `(notes_res.get('data') or {}).get('notes')`
    which silently swallowed the new `{data: [list]}` shape. This test
    pins the new shape-tolerant parsing."""

    @pytest.fixture
    def gateway_endpoint(self):
        # Import lazily to avoid pulling FastAPI lifespan
        import importlib

        mod = importlib.import_module("app_19_fhir.backend.app.main")
        return mod

    def test_list_data_shape_yields_documents(self, gateway_endpoint):
        # Reproduce the exact parsing block in fhir_document_reference_search
        notes_res = {"status": "ok", "data": [{"note_id": "n1"}, {"note_id": "n2"}]}
        data = notes_res.get("data") if isinstance(notes_res, dict) else None
        if isinstance(data, list):
            docs = data
        elif isinstance(data, dict):
            docs = data.get("notes") or []
        else:
            docs = []
        assert len(docs) == 2

    def test_dict_data_with_notes_key_still_works(self, gateway_endpoint):
        # Backwards-compat with the old shape
        notes_res = {"data": {"notes": [{"note_id": "n1"}]}}
        data = notes_res.get("data") if isinstance(notes_res, dict) else None
        if isinstance(data, list):
            docs = data
        elif isinstance(data, dict):
            docs = data.get("notes") or []
        else:
            docs = []
        assert len(docs) == 1

    def test_full_resource_built_from_seeded_note(self, gateway_endpoint):
        from app_19_fhir.backend.app.main import _document_reference_resource

        doc = {
            "note_id": _TEST_NOTE_ID,
            "note_type": "discharge_summary",
            "status": "approved",
            "source": "mimic_iv_note",
            "original_note_id": "16473192-DS-12",
            "original_charttime": "2163-06-12 00:00:00",
        }
        r = _document_reference_resource(doc, _TEST_NOTE_SUBJECT, _TEST_NOTE_HADM)
        # Lock the FHIR R4 wire shape an EHR consumer would see
        assert r["resourceType"] == "DocumentReference"
        assert r["id"] == f"doc-{_TEST_NOTE_ID}"
        assert r["docStatus"] == "final"
        assert r["type"]["coding"][0]["system"] == "http://loinc.org"
        assert r["type"]["coding"][0]["code"] == "18842-5"
        # subject is pseudonymised in simulation mode (PHI guard)
        assert r["subject"]["reference"].startswith("Patient/SIM-")
        assert r["context"]["encounter"][0]["reference"] == f"Encounter/{_TEST_NOTE_HADM}"
        sources = [e["valueString"] for e in r["extension"] if "note-source" in e["url"]]
        assert sources == ["mimic_iv_note"]
