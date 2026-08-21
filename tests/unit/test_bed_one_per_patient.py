"""Unit tests for the one-bed-per-person invariant and capacity hysteresis.

Covers the 2026-08-21 audit findings:

* 14 subjects held 2-4 concurrent beds (21 of 129 occupied), which pinned
  HDU at a permanent 100% / black.
* Capacity alerts were re-posted on every /beds/summary poll and no alert
  was ever sent when a ward recovered to green.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app_08_bed_management.backend.app.main import (
    _alerts_to_send,
    _bed_held_by_subject,
    _dedupe_subject_beds,
    _pick_bed_to_keep,
)
from app_08_bed_management.backend.app.schemas import BedState


def _bed(bed_id, dept, *, patient=None, hadm=None, admitted=None, status=None):
    return BedState(
        bed_id=bed_id,
        department=dept,
        bed_type="general",
        category="general",
        status=status or ("occupied" if patient is not None else "available"),
        patient_id=patient,
        hadm_id=hadm,
        admission_time=admitted,
    )


def _registry(*beds):
    return {b.bed_id: b for b in beds}


T0 = datetime(2027, 1, 7, 6, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _bed_held_by_subject
# ---------------------------------------------------------------------------

def test_finds_bed_held_by_subject_across_id_types():
    beds = _registry(_bed("Medicine-001", "Medicine", patient=14344243))
    # bed.patient_id is an int, the request carries a string — both must match.
    assert _bed_held_by_subject(beds, "14344243").bed_id == "Medicine-001"
    assert _bed_held_by_subject(beds, 14344243).bed_id == "Medicine-001"


def test_no_bed_for_unknown_or_null_subject():
    beds = _registry(_bed("Medicine-001", "Medicine", patient=1))
    assert _bed_held_by_subject(beds, 999) is None
    assert _bed_held_by_subject(beds, None) is None


def test_available_beds_are_not_counted_as_held():
    beds = _registry(_bed("Medicine-001", "Medicine", status="available"))
    assert _bed_held_by_subject(beds, None) is None


# ---------------------------------------------------------------------------
# _pick_bed_to_keep
# ---------------------------------------------------------------------------

def test_keeps_earliest_admission():
    beds = _registry(
        _bed("Medicine-039", "Medicine", patient=1, admitted=T0 + timedelta(hours=5)),
        _bed("Medicine-013", "Medicine", patient=1, admitted=T0),
        _bed("Medicine-040", "Medicine", patient=1, admitted=T0 + timedelta(hours=9)),
    )
    assert _pick_bed_to_keep(beds, list(beds)) == "Medicine-013"


def test_discharge_lounge_bed_wins_over_an_earlier_ward_bed():
    # The patient has physically moved to the lounge. _lounge_reconciler
    # re-acquires lounge beds from the lounge service every 30s, so releasing
    # the lounge bed here would just be undone.
    beds = _registry(
        _bed("Medicine-013", "Medicine", patient=1, admitted=T0),
        _bed("Discharge_Lounge-002", "Discharge_Lounge", patient=1,
             admitted=T0 + timedelta(hours=20)),
    )
    assert _pick_bed_to_keep(beds, list(beds)) == "Discharge_Lounge-002"


def test_mixed_naive_and_aware_admission_times_do_not_raise():
    # admission_time is written by four paths; MIMIC_SIM supplies "...Z"
    # (aware) and the sim-clock fallback supplies naive values. Sorting the
    # raw datetimes together would raise TypeError.
    beds = _registry(
        _bed("ICU-011", "ICU", patient=1, admitted=T0 + timedelta(hours=3)),
        _bed("Medicine-014", "Medicine", patient=1,
             admitted=datetime(2027, 1, 7, 6, 0)),  # naive, same instant as T0
    )
    assert _pick_bed_to_keep(beds, list(beds)) == "Medicine-014"


def test_undated_beds_sort_last_and_ties_break_on_bed_id():
    beds = _registry(
        _bed("Surgery-027", "Surgery", patient=1),
        _bed("Surgery-001", "Surgery", patient=1),
    )
    assert _pick_bed_to_keep(beds, list(beds)) == "Surgery-001"


# ---------------------------------------------------------------------------
# _dedupe_subject_beds
# ---------------------------------------------------------------------------

def test_releases_every_surplus_bed_for_a_subject():
    beds = _registry(
        _bed("HDU-002", "HDU", patient=10456768, hadm="SIM-1-100",
             admitted=T0 + timedelta(hours=2)),
        _bed("HDU-003", "HDU", patient=10456768, hadm="SIM-1-200",
             admitted=T0 + timedelta(hours=4)),
        _bed("Cardiology-001", "Cardiology", patient=10456768, hadm="SIM-1-050",
             admitted=T0),
    )
    released = _dedupe_subject_beds(beds)

    assert sorted(b for _, b in released) == ["HDU-002", "HDU-003"]
    assert beds["Cardiology-001"].status == "occupied"
    for bed_id in ("HDU-002", "HDU-003"):
        assert beds[bed_id].status == "available"
        assert beds[bed_id].patient_id is None
        assert beds[bed_id].hadm_id is None
        assert beds[bed_id].admission_time is None


def test_dedupe_is_idempotent():
    beds = _registry(
        _bed("HDU-001", "HDU", patient=1, admitted=T0),
        _bed("HDU-002", "HDU", patient=1, admitted=T0 + timedelta(hours=1)),
    )
    assert len(_dedupe_subject_beds(beds)) == 1
    assert _dedupe_subject_beds(beds) == []


def test_dedupe_leaves_distinct_patients_alone():
    beds = _registry(
        _bed("HDU-001", "HDU", patient=1, admitted=T0),
        _bed("HDU-002", "HDU", patient=2, admitted=T0),
        _bed("HDU-003", "HDU", status="available"),
    )
    assert _dedupe_subject_beds(beds) == []
    assert beds["HDU-001"].status == "occupied"
    assert beds["HDU-002"].status == "occupied"


def test_dedupe_ignores_beds_with_no_patient_id():
    # A bed can be occupied with patient_id None (lounge acquisition when the
    # lounge service omits subject_id). Those must not be collapsed together.
    beds = _registry(
        _bed("Discharge_Lounge-001", "Discharge_Lounge", hadm="a", status="occupied"),
        _bed("Discharge_Lounge-002", "Discharge_Lounge", hadm="b", status="occupied"),
    )
    assert _dedupe_subject_beds(beds) == []


# ---------------------------------------------------------------------------
# _alerts_to_send
# ---------------------------------------------------------------------------

def _summary(dept, band):
    return SimpleNamespace(department=dept, alert_level=band)


def test_standing_band_is_not_resent_on_every_poll():
    gate = {}
    hdu = _summary("HDU", "black")
    assert [s.department for s in _alerts_to_send([hdu], gate)] == ["HDU"]
    # Same band a moment later — suppressed until the heartbeat elapses.
    assert _alerts_to_send([hdu], gate) == []
    assert _alerts_to_send([hdu], gate) == []


def test_heartbeat_resends_a_standing_band():
    gate = {}
    hdu = _summary("HDU", "black")
    _alerts_to_send([hdu], gate)
    gate["HDU"]["ts"] -= 31.0  # pretend the heartbeat window has passed
    assert [s.department for s in _alerts_to_send([hdu], gate)] == ["HDU"]


def test_escalation_sends_immediately_without_waiting_for_heartbeat():
    gate = {}
    _alerts_to_send([_summary("HDU", "amber")], gate)
    sent = _alerts_to_send([_summary("HDU", "black")], gate)
    assert [s.alert_level for s in sent] == ["black"]


def test_recovery_to_green_sends_exactly_one_clear():
    gate = {}
    _alerts_to_send([_summary("HDU", "black")], gate)
    # The clear is the only signal Hospital Ops gets that the bottleneck is
    # over — without it its bottleneck_detected gate stays latched.
    sent = _alerts_to_send([_summary("HDU", "green")], gate)
    assert [s.alert_level for s in sent] == ["green"]
    assert _alerts_to_send([_summary("HDU", "green")], gate) == []


def test_always_green_department_never_sends():
    gate = {}
    med = _summary("Medicine", "green")
    assert _alerts_to_send([med], gate) == []
    gate["Medicine"]["ts"] -= 3600.0
    assert _alerts_to_send([med], gate) == []


def test_green_then_amber_is_detected_as_a_change():
    gate = {}
    _alerts_to_send([_summary("Medicine", "green")], gate)
    sent = _alerts_to_send([_summary("Medicine", "amber")], gate)
    assert [s.alert_level for s in sent] == ["amber"]


def test_departments_are_gated_independently():
    gate = {}
    sent = _alerts_to_send(
        [_summary("HDU", "black"), _summary("ICU", "amber"), _summary("ED", "green")],
        gate,
    )
    assert sorted(s.department for s in sent) == ["HDU", "ICU"]
    assert _alerts_to_send(
        [_summary("HDU", "black"), _summary("ICU", "red"), _summary("ED", "green")],
        gate,
    )[0].department == "ICU"
