"""Unit tests for PEWS (Paediatric Early Warning Score)."""

from __future__ import annotations

from shared.clinical.pews import compute_pews


def test_infant_all_normal_returns_zero():
    # 2-month-old with normal infant vitals
    r = compute_pews(
        age_months=2,
        heart_rate=140,
        respiratory_rate=45,
        spo2=98,
        on_supplemental_o2=False,
        systolic_bp=80,
        temperature_c=36.8,
        behaviour="Alert",
    )
    assert r.age_band == "0-3mo"
    assert r.total == 0
    assert r.risk_band == "none"


def test_toddler_tachycardia_scores():
    # 2-year-old with HR 180 (tachycardia vs 90-150 normal)
    r = compute_pews(
        age_months=24,
        heart_rate=180,
        respiratory_rate=28,
        spo2=98,
        systolic_bp=100,
        temperature_c=36.8,
        behaviour="Alert",
    )
    assert r.age_band == "1-4y"
    assert r.components["heart_rate"] >= 2
    assert r.total >= 2


def test_severe_bradypnoea_flags_high_risk():
    # 8-year-old with RR of 5 (severe, well below 16 lower bound × 0.6)
    r = compute_pews(
        age_months=96,
        heart_rate=90,
        respiratory_rate=5,
        spo2=96,
        systolic_bp=100,
        temperature_c=36.8,
        behaviour="Lethargic",
    )
    assert r.components["respiratory_rate"] == 3
    assert r.any_param_eq_3
    # 3 (RR) + 2 (behaviour L) = at least 5 → high
    assert r.risk_band == "high"


def test_hypoxia_severe_scores_three():
    r = compute_pews(
        age_months=36,
        heart_rate=130,
        respiratory_rate=30,
        spo2=88,
        systolic_bp=95,
        temperature_c=37.0,
        behaviour="Alert",
    )
    assert r.components["spo2"] == 3
    assert r.any_param_eq_3


def test_supplemental_oxygen_adds_one_point():
    r = compute_pews(
        age_months=60,
        heart_rate=100,
        respiratory_rate=22,
        spo2=97,
        on_supplemental_o2=True,
        systolic_bp=100,
        temperature_c=36.7,
        behaviour="Alert",
    )
    assert r.components["supplemental_o2"] == 1


def test_unresponsive_behaviour_scores_three():
    r = compute_pews(
        age_months=48,
        heart_rate=110,
        respiratory_rate=25,
        spo2=98,
        systolic_bp=100,
        temperature_c=36.8,
        behaviour="Unresponsive",
    )
    assert r.components["behaviour"] == 3


def test_capillary_refill_slow_flags():
    r = compute_pews(
        age_months=30,
        heart_rate=120,
        respiratory_rate=28,
        spo2=96,
        systolic_bp=95,
        temperature_c=36.8,
        behaviour="Alert",
        capillary_refill_s=4,
    )
    assert r.components["capillary_refill"] == 3


def test_severe_respiratory_effort_scores_three():
    r = compute_pews(
        age_months=12,
        heart_rate=150,
        respiratory_rate=40,
        spo2=94,
        systolic_bp=85,
        temperature_c=37.0,
        behaviour="Irritable",
        respiratory_effort="severe",
    )
    assert r.components["respiratory_effort"] == 3


def test_age_band_selection():
    assert compute_pews(age_months=1, heart_rate=140).age_band == "0-3mo"
    assert compute_pews(age_months=6, heart_rate=140).age_band == "3-12mo"
    assert compute_pews(age_months=36, heart_rate=120).age_band == "1-4y"
    assert compute_pews(age_months=96, heart_rate=100).age_band == "5-11y"
    assert compute_pews(age_months=180, heart_rate=80).age_band == "12-17y"


def test_missing_values_dont_crash():
    r = compute_pews(age_months=24)
    assert r.total == 0
    assert r.risk_band == "none"


def test_to_dict_is_serialisable():
    r = compute_pews(age_months=24, heart_rate=180)
    d = r.to_dict()
    assert "total" in d
    assert "components" in d
    assert "age_band" in d
    assert "recommended_response" in d


def test_hypotension_scores_heavily():
    # 5-year-old with SBP 60 (severe, below 85 × 0.8 = 68)
    r = compute_pews(
        age_months=60,
        heart_rate=120,
        respiratory_rate=22,
        spo2=96,
        systolic_bp=60,
        temperature_c=36.8,
    )
    assert r.components["systolic_bp"] == 3
    assert r.any_param_eq_3
