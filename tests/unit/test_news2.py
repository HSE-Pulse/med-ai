"""Unit tests for NEWS2 scoring."""

from __future__ import annotations

import pytest

from shared.clinical.news2 import compute_news2


def test_all_normal_returns_zero():
    r = compute_news2(
        respiratory_rate=16,
        spo2=98,
        on_supplemental_o2=False,
        temperature_c=36.8,
        systolic_bp=120,
        heart_rate=70,
        consciousness="A",
    )
    assert r.total == 0
    assert r.risk_band == "none"
    assert not r.any_param_eq_3


def test_single_param_eq_3_escalates_band():
    # Low RR = 3 points
    r = compute_news2(
        respiratory_rate=7,
        spo2=98,
        on_supplemental_o2=False,
        temperature_c=36.8,
        systolic_bp=120,
        heart_rate=70,
        consciousness="A",
    )
    assert r.components["respiratory_rate"] == 3
    assert r.any_param_eq_3
    assert r.risk_band == "medium"


def test_high_total_triggers_high_band():
    r = compute_news2(
        respiratory_rate=25,       # 3
        spo2=91,                    # 3
        on_supplemental_o2=True,    # 2
        temperature_c=38.5,         # 1
        systolic_bp=95,             # 2
        heart_rate=120,             # 2
        consciousness="V",          # 3
    )
    assert r.total >= 7
    assert r.risk_band == "high"


def test_supplemental_o2_adds_two():
    r = compute_news2(
        respiratory_rate=16, spo2=98, on_supplemental_o2=True,
        temperature_c=36.8, systolic_bp=120, heart_rate=70,
        consciousness="A",
    )
    assert r.components["supplemental_o2"] == 2


def test_confusion_scores_three():
    r = compute_news2(
        respiratory_rate=16, spo2=98, on_supplemental_o2=False,
        temperature_c=36.8, systolic_bp=120, heart_rate=70,
        consciousness="Confusion",
    )
    assert r.components["consciousness"] == 3


def test_scale2_hypercapnic_target_on_oxygen_scores_above_target():
    # COPD patient with target SpO2 88-92%, on supplemental O2 at 94 → above target → scores
    r = compute_news2(
        respiratory_rate=16, spo2=94, on_supplemental_o2=True,
        scale2_hypercapnic_target=True,
        temperature_c=36.8, systolic_bp=120, heart_rate=70,
        consciousness="A",
    )
    # SpO2 is above the 88-92 target → non-zero on Scale 2
    assert r.components["spo2"] >= 1


def test_scale2_hypercapnic_target_in_range_scores_zero():
    # On air at 90 — squarely in the 88-92% target range → 0
    r = compute_news2(
        respiratory_rate=16, spo2=90, on_supplemental_o2=False,
        scale2_hypercapnic_target=True,
        temperature_c=36.8, systolic_bp=120, heart_rate=70,
        consciousness="A",
    )
    # Being in target range on air only scores via supplemental O2 flag
    assert r.components["spo2"] == 0


def test_missing_values_dont_crash():
    r = compute_news2()
    assert r.total == 0
    assert r.risk_band == "none"


def test_to_dict_is_serialisable():
    r = compute_news2(respiratory_rate=25)
    d = r.to_dict()
    assert d["total"] == r.total
    assert "components" in d
    assert "recommended_response" in d
