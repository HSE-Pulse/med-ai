"""Unit tests for the HIQA clinical record wrapper."""

from __future__ import annotations

import pytest

from shared.clinical.clinical_record import is_paediatric, update, wrap


def test_wrap_adds_provenance_and_quality():
    record = {"hadm_id": "1", "acuity": 3}
    wrapped = wrap(record, source_system="sim", operator_id="op-1")
    assert wrapped["provenance"]["source_system"] == "sim"
    assert wrapped["provenance"]["operator_id"] == "op-1"
    assert wrapped["data_quality"]["is_complete"] is True
    assert wrapped["version_history"][0]["version"] == 1


def test_wrap_detects_missing_required_fields():
    record = {"hadm_id": "1"}
    wrapped = wrap(
        record,
        source_system="sim",
        required_fields=["hadm_id", "subject_id", "department"],
    )
    assert wrapped["data_quality"]["is_complete"] is False
    assert set(wrapped["data_quality"]["has_missing_fields"]) == {"subject_id", "department"}


def test_wrap_preserves_existing_version_history():
    record = {"version_history": [{"version": 5, "timestamp": "x", "operator_id": "y"}]}
    wrapped = wrap(record, source_system="sim")
    assert wrapped["version_history"][-1]["version"] == 5


def test_update_appends_version_and_tracks_changes():
    rec = wrap({"acuity": 3}, source_system="sim")
    updated = update(rec, {"acuity": 2}, operator_id="op-2", reason="retriage")
    history = updated["version_history"]
    assert history[-1]["version"] == 2
    assert history[-1]["operator_id"] == "op-2"
    assert "acuity" in history[-1]["changed_fields"]
    assert history[-1]["reason"] == "retriage"


def test_update_applies_field_changes():
    rec = wrap({"acuity": 3}, source_system="sim")
    updated = update(rec, {"acuity": 1})
    assert updated["acuity"] == 1


def test_wrap_does_not_mutate_input():
    record = {"hadm_id": "1"}
    wrap(record, source_system="sim")
    assert "provenance" not in record
    assert "version_history" not in record


def test_safeguarding_flag_set_from_wrap():
    wrapped = wrap({"age": 12}, source_system="sim", safeguarding_flag=True)
    assert wrapped["safeguarding_flag"] is True


def test_is_paediatric():
    assert is_paediatric(10)
    assert is_paediatric(17.9)
    assert not is_paediatric(18)
    assert not is_paediatric(None)
    assert not is_paediatric("not a number")
