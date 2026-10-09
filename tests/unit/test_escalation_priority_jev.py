"""Escalation priority ranking + JEV triage."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app_20_deterioration.backend.app import jev_triage
from app_20_deterioration.backend.app.main import _escalation_priority


def esc(total=3, band="low", red=False, repeats=1, age_min=0, eid="e1"):
    when = datetime.now(timezone.utc) - timedelta(minutes=age_min)
    return {
        "escalation_id": eid,
        "hadm_id": "SIM-1",
        "scoring_system": "news2",
        "escalated_at": when.isoformat(),
        "repeat_count": repeats,
        "score": {"total": total, "risk_band": band, "any_param_eq_3": red,
                  "components": {"systolic_bp": 3 if red else 1}},
    }


class TestPriority:
    def test_higher_risk_band_outranks_lower(self):
        assert _escalation_priority(esc(total=3, band="critical"))["priority"] > \
               _escalation_priority(esc(total=3, band="low"))["priority"]

    def test_red_flag_adds_weight(self):
        assert _escalation_priority(esc(red=True))["priority"] > \
               _escalation_priority(esc(red=False))["priority"]

    def test_repeat_escalations_raise_priority(self):
        assert _escalation_priority(esc(repeats=4))["priority"] > \
               _escalation_priority(esc(repeats=1))["priority"]

    def test_waiting_longer_raises_priority_anti_starvation(self):
        assert _escalation_priority(esc(age_min=55))["priority"] > \
               _escalation_priority(esc(age_min=0))["priority"]

    def test_score_is_bounded_and_banded(self):
        r = _escalation_priority(esc(total=20, band="critical", red=True, repeats=9, age_min=600))
        assert 0 <= r["priority"] <= 100
        assert r["priority_band"] in {"low", "moderate", "high", "critical"}

    def test_every_row_explains_itself(self):
        # an unexplained ranking is not usable at a bedside
        r = _escalation_priority(esc(red=True, repeats=3))
        assert r["priority_reasons"]
        assert any("red flag" in x for x in r["priority_reasons"])

    def test_ordering_is_clinical_not_chronological(self):
        """The bug this fixes: a NEWS2 12 arriving first sat under a NEWS2 3."""
        old_severe = _escalation_priority(esc(total=12, band="critical", red=True, age_min=90))
        new_mild = _escalation_priority(esc(total=3, band="low", age_min=0))
        assert old_severe["priority"] > new_mild["priority"]


class TestJevGating:
    def test_disabled_without_api_key(self, monkeypatch):
        monkeypatch.delenv("JEV_API_KEY", raising=False)
        assert jev_triage.enabled() is False

    def test_enabled_with_api_key(self, monkeypatch):
        monkeypatch.setenv("JEV_API_KEY", "k")
        assert jev_triage.enabled() is True

    @pytest.mark.asyncio
    async def test_returns_empty_without_key(self, monkeypatch):
        monkeypatch.delenv("JEV_API_KEY", raising=False)
        assert await jev_triage.triage([esc()]) == {}

    @pytest.mark.asyncio
    async def test_returns_empty_for_no_records(self, monkeypatch):
        monkeypatch.setenv("JEV_API_KEY", "k")
        assert await jev_triage.triage([]) == {}

    def test_suppress_is_not_an_available_action(self):
        """JEV advises, it never silences — nothing it returns can hide a row."""
        assert "suppress" not in jev_triage.ACTIONS
        assert set(jev_triage.ACTIONS) == {"review_now", "review_soon", "monitor", "routine"}

    def test_action_rank_orders_worst_first(self):
        r = jev_triage._ACTION_RANK
        assert r["review_now"] < r["review_soon"] < r["monitor"] < r["routine"]

    def test_state_carries_the_clinical_picture(self):
        st = jev_triage._state_for(esc(total=5, band="medium", red=True, repeats=3))
        assert st["total_score"] == 5
        assert st["risk_band"] == "medium"
        assert st["single_parameter_red_flag"] is True
        assert st["times_escalated_unacknowledged"] == 3
        assert "NEWS2" in st["guidance"]

    def test_questions_match_the_documented_contract(self):
        q = jev_triage._QUESTIONS
        assert q["action"]["type"] == "choice" and "criteria" in q["action"]
        assert q["urgency"]["type"] == "score"
        assert len(q["urgency"]["criteria"]) == 5          # score 0-4
        assert len(q["action"]["criteria"]) <= 255          # JEV cardinality limit


class TestJevResponseHandling:
    @pytest.mark.asyncio
    async def test_low_confidence_is_discarded(self, monkeypatch):
        monkeypatch.setenv("JEV_API_KEY", "k")
        monkeypatch.setattr(jev_triage, "MIN_CONFIDENCE", 0.55)

        class R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {"model": "jev-1.13.0", "answers": {
                    "action": {"type": "choice", "choice": "review_now", "confidence": 0.20},
                    "urgency": {"type": "score", "score": 4.0, "confidence": 0.9}}}

        class C:
            async def post(self, *a, **k): return R()
        assert await jev_triage._one(C(), "k", esc()) is None

    @pytest.mark.asyncio
    async def test_unknown_action_is_rejected(self, monkeypatch):
        monkeypatch.setenv("JEV_API_KEY", "k")

        class R:
            def raise_for_status(self): pass
            def json(self):
                return {"answers": {"action": {"choice": "delete_patient", "confidence": 0.99}}}

        class C:
            async def post(self, *a, **k): return R()
        assert await jev_triage._one(C(), "k", esc()) is None

    @pytest.mark.asyncio
    async def test_confident_answer_is_accepted(self, monkeypatch):
        monkeypatch.setenv("JEV_API_KEY", "k")

        class R:
            def raise_for_status(self): pass
            def json(self):
                return {"model": "jev-1.13.0", "answers": {
                    "action": {"type": "choice", "choice": "review_now", "confidence": 0.91,
                               "probabilities": {"review_now": 0.91}},
                    "urgency": {"type": "score", "score": 4.0, "confidence": 0.8}}}

        class C:
            async def post(self, *a, **k): return R()
        v = await jev_triage._one(C(), "k", esc())
        assert v["jev_action"] == "review_now"
        assert v["jev_confidence"] == 0.91
        assert v["jev_urgency"] == 4.0
        assert v["jev_model"] == "jev-1.13.0"

    @pytest.mark.asyncio
    async def test_transport_error_falls_back_silently(self, monkeypatch):
        monkeypatch.setenv("JEV_API_KEY", "k")

        class C:
            async def post(self, *a, **k): raise RuntimeError("connect timeout")
        assert await jev_triage._one(C(), "k", esc()) is None
