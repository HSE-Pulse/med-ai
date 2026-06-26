"""Smoke tests for the shared FastAPI factory — privacy notice, AI Act info, RBAC."""

from __future__ import annotations

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from shared.api.base import AIActInfo, PrivacyNotice, create_app, require_role


def _notice() -> PrivacyNotice:
    return PrivacyNotice(
        data_collected=["vitals"],
        legal_basis="GDPR Art. 9(2)(h) — healthcare",
        retention_period="24 months",
    )


def _ai_info() -> AIActInfo:
    return AIActInfo(
        risk_level="high",
        intended_purpose="acuity triage",
        known_limitations=["trained on MIMIC-IV — US cohort"],
        training_data_description="MIMIC-IV v2.2, 300k ED visits",
        validation_metrics={"auroc": 0.89},
    )


def test_factory_registers_health_endpoint(sim_mode_env):
    app = create_app(title="t", privacy_notice=_notice())
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "uptime_seconds" in data
    assert data["security"]["mode"] == "simulation"


def test_privacy_notice_registered(sim_mode_env):
    app = create_app(title="t", privacy_notice=_notice())
    client = TestClient(app)
    resp = client.get("/privacy-notice")
    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["legal_basis"].startswith("GDPR")
    assert "access (Art. 15)" in payload["subject_rights"]
    assert payload["service_title"] == "t"


def test_ai_act_info_registered(sim_mode_env):
    app = create_app(title="t", privacy_notice=_notice(), ai_act_info=_ai_info())
    client = TestClient(app)
    resp = client.get("/ai-act-info")
    assert resp.status_code == 200
    assert resp.json()["data"]["risk_level"] == "high"


def test_ai_act_info_not_registered_when_omitted(sim_mode_env):
    app = create_app(title="t", privacy_notice=_notice())
    client = TestClient(app)
    resp = client.get("/ai-act-info")
    assert resp.status_code == 404


def test_require_role_allows_default_researcher_in_sim_mode(sim_mode_env):
    app = create_app(title="t", privacy_notice=_notice())

    @app.get("/safe")
    @require_role("researcher", "clinician")
    async def safe(request: Request):
        return {"role": request.state.role}

    client = TestClient(app)
    resp = client.get("/safe")
    assert resp.status_code == 200
    assert resp.json()["role"] == "researcher"


def test_require_role_rejects_unknown_role(sim_mode_env):
    app = create_app(title="t", privacy_notice=_notice())

    @app.get("/admin-only")
    @require_role("admin")
    async def admin_only(request: Request):
        return {"ok": True}

    client = TestClient(app)
    # researcher default isn't admin -> 403
    resp = client.get("/admin-only")
    assert resp.status_code == 403


def test_security_check_production_missing_env_raises(monkeypatch):
    from shared.api.base import SecurityCheckError, security_check

    monkeypatch.setenv("DEPLOYMENT_MODE", "production")
    monkeypatch.delenv("TLS_CERT_PATH", raising=False)
    monkeypatch.delenv("TLS_KEY_PATH", raising=False)
    monkeypatch.delenv("FERNET_KEY", raising=False)
    with pytest.raises(SecurityCheckError):
        security_check()
