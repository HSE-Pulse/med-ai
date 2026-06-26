"""Unit tests for IMEWS (Irish Maternity Early Warning Score)."""

from __future__ import annotations

from shared.clinical.imews import compute_imews


def test_normal_pregnancy_vitals_score_zero():
    r = compute_imews(
        respiratory_rate=16,
        spo2=98,
        temperature_c=36.8,
        systolic_bp=110,
        diastolic_bp=70,
        heart_rate=85,
        consciousness="Alert",
        gestation_weeks=24,
    )
    assert r.total == 0
    assert r.pink_triggers == 0
    assert r.risk_band == "none"
    assert r.gestational_context == "second_trimester"


def test_severe_hypertension_scores_pink():
    # Pre-eclampsia: SBP 165 + DBP 115 + proteinuria 3+
    r = compute_imews(
        respiratory_rate=18,
        spo2=97,
        temperature_c=36.8,
        systolic_bp=165,
        diastolic_bp=115,
        heart_rate=90,
        consciousness="Alert",
        proteinuria="3+",
        gestation_weeks=34,
    )
    assert r.components["systolic_bp"] == 3
    assert r.components["diastolic_bp"] == 3
    assert r.components["proteinuria"] == 3
    assert r.pink_triggers >= 3
    assert r.risk_band == "critical"


def test_severe_hypotension_flags_high():
    # Haemorrhage scenario: SBP 85, HR 125
    r = compute_imews(
        respiratory_rate=22,
        spo2=96,
        temperature_c=36.5,
        systolic_bp=85,
        diastolic_bp=55,
        heart_rate=125,
        consciousness="Alert",
        post_partum_days=0,
        lochia="heavy",
    )
    assert r.components["systolic_bp"] == 3
    assert r.components["heart_rate"] == 3
    assert r.components["lochia"] == 3
    assert r.pink_triggers >= 3
    assert r.risk_band == "critical"
    assert r.gestational_context == "immediate_postpartum"


def test_single_yellow_triggers_medium():
    # Mild tachycardia only
    r = compute_imews(
        respiratory_rate=18,
        spo2=98,
        temperature_c=36.8,
        systolic_bp=115,
        diastolic_bp=72,
        heart_rate=105,
        consciousness="Alert",
        gestation_weeks=30,
    )
    assert r.components["heart_rate"] == 1
    assert r.yellow_triggers == 1
    assert r.pink_triggers == 0
    assert r.risk_band == "medium"


def test_two_yellows_escalate_to_high():
    r = compute_imews(
        respiratory_rate=22,  # yellow
        spo2=98,
        temperature_c=37.7,  # yellow
        systolic_bp=115,
        diastolic_bp=80,
        heart_rate=85,
        consciousness="Alert",
        gestation_weeks=32,
    )
    assert r.yellow_triggers >= 2
    assert r.risk_band == "high"


def test_altered_consciousness_always_pink():
    r = compute_imews(
        respiratory_rate=18,
        spo2=97,
        temperature_c=36.8,
        systolic_bp=115,
        diastolic_bp=75,
        heart_rate=90,
        consciousness="Voice",
        gestation_weeks=36,
    )
    assert r.components["consciousness"] == 3
    assert r.any_pink
    assert r.risk_band == "high"


def test_sepsis_temperature_scores_pink():
    r = compute_imews(
        respiratory_rate=24,
        spo2=97,
        temperature_c=38.2,
        systolic_bp=105,
        diastolic_bp=65,
        heart_rate=110,
        consciousness="Alert",
        gestation_weeks=32,
    )
    assert r.components["temperature"] == 3


def test_gestational_context_categories():
    # Cover the main gestational buckets
    assert compute_imews(gestation_weeks=6).gestational_context == "first_trimester"
    assert compute_imews(gestation_weeks=20).gestational_context == "second_trimester"
    assert compute_imews(gestation_weeks=33).gestational_context == "third_trimester_preterm"
    assert compute_imews(gestation_weeks=39).gestational_context == "term"
    assert compute_imews(post_partum_days=0).gestational_context == "immediate_postpartum"
    assert compute_imews(post_partum_days=14).gestational_context == "postpartum_6wk"


def test_heavy_lochia_postpartum_scores_pink():
    r = compute_imews(
        respiratory_rate=18,
        spo2=97,
        temperature_c=36.8,
        systolic_bp=110,
        diastolic_bp=70,
        heart_rate=90,
        consciousness="Alert",
        post_partum_days=1,
        lochia="heavy",
    )
    assert r.components["lochia"] == 3


def test_to_dict_is_serialisable():
    r = compute_imews(
        systolic_bp=85,
        heart_rate=125,
        post_partum_days=0,
    )
    d = r.to_dict()
    assert "pink_triggers" in d
    assert "yellow_triggers" in d
    assert "gestational_context" in d
    assert "recommended_response" in d


def test_missing_values_dont_crash():
    r = compute_imews(gestation_weeks=20)
    assert r.total == 0
    assert r.risk_band == "none"
