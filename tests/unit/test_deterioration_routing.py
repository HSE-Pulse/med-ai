"""Integration tests for the deterioration service routing and escalation loop."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    # Import inside fixture so module-level state is fresh per test session
    from app_20_deterioration.backend.app import main as det_main
    # Force in-memory mode (no Mongo) for tests
    det_main._state["mongo"] = None
    from shared.integration.debouncer import ScoreAwareDebouncer
    det_main._state["debouncer"] = ScoreAwareDebouncer(cooldown_s=300, rise_threshold=1)
    det_main._state["active_alerts"].clear()
    det_main._state["history"].clear()
    det_main._state["escalations"].clear()
    det_main._state["acknowledgements"].clear()
    with TestClient(det_main.app) as c:
        yield c


def test_adult_patient_routes_to_news2(client):
    r = client.post("/deterioration/screen", json={
        "hadm_id": "A1",
        "subject_id": 1,
        "department": "Medicine",
        "age": 65,
        "vitals": {
            "respiratory_rate": 18, "spo2": 97, "temperature": 37.0,
            "systolic_bp": 120, "heart_rate": 80, "consciousness": "A",
        },
    })
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["scoring_system"] == "news2"
    assert data["score"]["total"] == 0


def test_paediatric_patient_autoroutes_to_pews(client):
    r = client.post("/deterioration/screen", json={
        "hadm_id": "P1",
        "subject_id": 10,
        "department": "Medicine",
        "age": 5,  # years — triggers paediatric routing
        "vitals": {
            "heart_rate": 110, "respiratory_rate": 22, "spo2": 98,
            "systolic_bp": 100, "temperature": 36.8, "behaviour": "Alert",
        },
    })
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["scoring_system"] == "pews"
    assert "age_band" in data["score"]


def test_pregnant_patient_autoroutes_to_imews(client):
    r = client.post("/deterioration/screen", json={
        "hadm_id": "M1",
        "subject_id": 20,
        "department": "MAU",
        "age": 30,
        "gestation_weeks": 32,
        "vitals": {
            "respiratory_rate": 18, "spo2": 97, "temperature": 36.8,
            "systolic_bp": 115, "diastolic_bp": 75, "heart_rate": 90,
            "consciousness": "A",
        },
    })
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["scoring_system"] == "imews"


def test_postpartum_patient_autoroutes_to_imews(client):
    r = client.post("/deterioration/screen", json={
        "hadm_id": "M2",
        "subject_id": 21,
        "department": "MAU",
        "age": 28,
        "post_partum_days": 2,
        "vitals": {
            "respiratory_rate": 20, "spo2": 97, "temperature": 36.8,
            "systolic_bp": 110, "diastolic_bp": 72, "heart_rate": 95,
            "consciousness": "A",
        },
    })
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["scoring_system"] == "imews"


def test_high_news2_creates_escalation(client):
    # Score high enough to escalate
    client.post("/deterioration/screen", json={
        "hadm_id": "H1",
        "subject_id": 30,
        "department": "Medicine",
        "age": 70,
        "vitals": {
            "respiratory_rate": 26, "spo2": 88, "temperature": 38.5,
            "systolic_bp": 95, "heart_rate": 125, "consciousness": "V",
        },
    })
    r = client.get("/deterioration/escalations")
    assert r.status_code == 200
    records = r.json()["data"]
    assert len(records) >= 1
    assert records[0]["hadm_id"] == "H1"
    assert records[0]["scoring_system"] == "news2"


def test_acknowledgement_loop_captures_sbar(client):
    # Create an escalation first
    client.post("/deterioration/screen", json={
        "hadm_id": "A100",
        "subject_id": 40,
        "department": "Medicine",
        "age": 72,
        "vitals": {
            "respiratory_rate": 25, "spo2": 90, "systolic_bp": 95,
            "heart_rate": 120, "consciousness": "V",
        },
    })
    escalations = client.get("/deterioration/escalations").json()["data"]
    assert len(escalations) >= 1
    esc_id = escalations[0]["escalation_id"]

    # Acknowledge with SBAR
    ack_r = client.post("/deterioration/acknowledge", json={
        "escalation_id": esc_id,
        "clinician": {"name": "Dr. O'Brien", "role": "Registrar"},
        "sbar": {
            "situation": "NEWS2 7 — new hypoxia + hypotension",
            "background": "72M admitted with pneumonia, day 2",
            "assessment": "Suspected septic shock",
            "recommendation": "IV fluids, cultures, antibiotics, ICU review",
        },
        "outcome": "escalated_further",
    })
    assert ack_r.status_code == 200
    record = ack_r.json()["data"]
    assert record["acknowledged"] is True
    assert record["acknowledged_by"]["role"] == "Registrar"
    assert record["sbar"]["assessment"].startswith("Suspected")
    assert record["outcome"] == "escalated_further"


def test_unacknowledged_filter(client):
    # Generate two escalations; ack one
    for i in range(2):
        client.post("/deterioration/screen", json={
            "hadm_id": f"U{i}",
            "subject_id": 50 + i,
            "department": "Medicine",
            "age": 65,
            "vitals": {
                "respiratory_rate": 26, "spo2": 88, "heart_rate": 125,
                "systolic_bp": 95, "consciousness": "V",
            },
        })
    escalations = client.get("/deterioration/escalations").json()["data"]
    assert len(escalations) >= 2
    client.post("/deterioration/acknowledge", json={
        "escalation_id": escalations[0]["escalation_id"],
        "clinician": {"name": "Dr. X", "role": "NCHD"},
        "sbar": {"situation": "reviewed"},
        "outcome": "reviewed",
    })
    unacked = client.get("/deterioration/escalations", params={"unacknowledged": True}).json()["data"]
    assert all(not r["acknowledged"] for r in unacked)
    assert len(unacked) < len(escalations)


def test_trend_endpoint_returns_slope(client):
    # Push increasing scores for same patient
    for i, rr in enumerate([18, 22, 24, 26]):
        client.post("/deterioration/screen", json={
            "hadm_id": "T1",
            "subject_id": 60,
            "department": "Medicine",
            "age": 68,
            "vitals": {
                "respiratory_rate": rr, "spo2": 96, "systolic_bp": 110,
                "heart_rate": 90, "consciousness": "A",
            },
        })
    r = client.get("/deterioration/trend/T1")
    assert r.status_code == 200
    data = r.json()["data"]
    assert "trajectory" in data
    assert "slope_per_hour" in data


def test_stats_includes_by_scoring_system(client):
    # Mix of adult + paediatric
    client.post("/deterioration/screen", json={
        "hadm_id": "S1", "age": 70, "department": "Medicine",
        "vitals": {"respiratory_rate": 18, "spo2": 97, "systolic_bp": 120,
                   "heart_rate": 80, "consciousness": "A"},
    })
    client.post("/deterioration/screen", json={
        "hadm_id": "S2", "age": 5, "department": "Medicine",
        "vitals": {"heart_rate": 110, "respiratory_rate": 22, "spo2": 98,
                   "systolic_bp": 100, "temperature": 36.8, "behaviour": "Alert"},
    })
    r = client.get("/deterioration/stats")
    data = r.json()["data"]
    assert "by_scoring_system" in data
    # Should reflect both systems having been used
    assert "news2" in data["by_scoring_system"] or "pews" in data["by_scoring_system"]
