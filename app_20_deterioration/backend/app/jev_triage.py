"""JEV (TypeSafe System One) triage for the escalation queue.

Why a model here at all: the deterministic priority score ranks by what the
NEWS2 snapshot already states. It cannot weigh context that is obvious to a
clinician — a score of 4 that has been bouncing for six hours on a stable
post-op patient is not the same as a 4 climbing on a fresh admission, even
though the numbers match.

Why JEV specifically rather than an LLM: this is a typed decision, not prose.
We need a bounded action from a fixed set plus a calibrated probability, in
the time a queue render can afford. JEV returns exactly that — the action is
type-guaranteed to be one of the keys we declared, so there is no "model
invented an action" branch to defend against, and the confidence is
calibrated rather than a number the model felt like emitting.

Safety posture: JEV ADVISES, it never silences. The worst action it can take
is to rank something lower. `suppress` is deliberately absent from the choice
set — an alert that fired still appears in the queue. Anything the model is
not confident about falls back to the deterministic score, as does any error,
timeout, or missing API key. The service is fully functional with JEV off.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

API_URL = os.environ.get("JEV_API_URL", "https://api.typesafe.ai/v1/systemone")
MODEL = os.environ.get("JEV_MODEL", "jev-latest")
TIMEOUT_S = float(os.environ.get("JEV_TIMEOUT_SECONDS", "2.0"))
MAX_CONCURRENCY = int(os.environ.get("JEV_MAX_CONCURRENCY", "8"))
MIN_CONFIDENCE = float(os.environ.get("JEV_MIN_CONFIDENCE", "0.55"))

# Ordered worst->best so the index doubles as a rank.
ACTIONS = {
    "review_now": "Needs a clinician at the bedside now: the picture is "
                  "deteriorating or already at an urgent-response threshold.",
    "review_soon": "Warrants review within the hour, but is not an emergency.",
    "monitor": "Keep observing on the current schedule; no review needed yet.",
    "routine": "Expected variation for this patient; no action beyond routine "
               "observations.",
}
_ACTION_RANK = {k: i for i, k in enumerate(ACTIONS)}


def enabled() -> bool:
    return bool(os.environ.get("JEV_API_KEY"))


def _state_for(rec: Dict[str, Any]) -> Dict[str, Any]:
    """The clinical picture, as plain data. No prompt engineering — System One
    takes unstructured state in and returns typed decisions out."""
    score = rec.get("score") or {}
    comps = score.get("components") or {}
    raised = {k: v for k, v in comps.items() if v}
    return {
        "scoring_system": rec.get("scoring_system", "news2"),
        "total_score": score.get("total"),
        "risk_band": score.get("risk_band"),
        "single_parameter_red_flag": bool(score.get("any_param_eq_3") or score.get("any_pink")),
        "raised_parameters": raised or "none — score spread across parameters",
        "recommended_response": score.get("recommended_response"),
        "department": rec.get("department"),
        "times_escalated_unacknowledged": rec.get("repeat_count", 1),
        "minutes_waiting_unacknowledged": rec.get("age_minutes", 0),
        "guidance": ("RCP NEWS2: total 0 routine; 1-4 ward-based monitoring; "
                     "5-6 or any single parameter scoring 3 urgent review; "
                     "7+ emergency response."),
    }


_QUESTIONS = {
    "action": {
        "type": "choice",
        "instructions": ("Given this early-warning-score snapshot, what should "
                         "happen next for this patient? Judge the clinical "
                         "picture as a whole, not the headline number alone."),
        "criteria": ACTIONS,
    },
    "urgency": {
        "type": "score",
        "instructions": "How urgent is clinician attention for this patient?",
        "criteria": [
            "Not urgent — routine observations are sufficient",
            "Low — review at the next scheduled round",
            "Moderate — review within the hour",
            "High — review within 30 minutes",
            "Critical — immediate bedside attendance",
        ],
    },
}


async def _one(client: httpx.AsyncClient, key: str, rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        r = await client.post(
            API_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": MODEL, "state": _state_for(rec), "questions": _QUESTIONS},
            timeout=TIMEOUT_S,
        )
        r.raise_for_status()
        body = r.json()
    except Exception as exc:  # noqa: BLE001 — triage must never break the queue
        logger.warning("jev triage failed for %s: %s", rec.get("escalation_id"), exc)
        return None

    ans = (body.get("answers") or {})
    act = ans.get("action") or {}
    urg = ans.get("urgency") or {}
    choice = act.get("choice")
    conf = float(act.get("confidence") or 0.0)
    if choice not in ACTIONS:
        # Type safety is the model's guarantee, but we are the ones who get
        # paged if it ever slips — so verify rather than assume.
        logger.warning("jev returned unknown action %r; ignoring", choice)
        return None
    if conf < MIN_CONFIDENCE:
        logger.info("jev low confidence %.2f for %s; deferring to heuristic",
                    conf, rec.get("escalation_id"))
        return None
    return {
        "jev_action": choice,
        "jev_confidence": round(conf, 3),
        "jev_action_rank": _ACTION_RANK[choice],
        "jev_urgency": urg.get("score"),
        "jev_urgency_confidence": round(float(urg.get("confidence") or 0.0), 3),
        "jev_probabilities": act.get("probabilities") or {},
        "jev_model": body.get("model"),
    }


async def triage(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Return {escalation_id: verdict} for whatever JEV could answer.

    Absent, unconfident or failed answers are simply omitted — the caller
    keeps its deterministic score for those rows.
    """
    key = os.environ.get("JEV_API_KEY")
    if not key or not records:
        return {}
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def guarded(client, rec):
        async with sem:
            return rec.get("escalation_id"), await _one(client, key, rec)

    async with httpx.AsyncClient() as client:
        pairs = await asyncio.gather(*(guarded(client, r) for r in records),
                                     return_exceptions=True)
    out: Dict[str, Dict[str, Any]] = {}
    for p in pairs:
        if isinstance(p, Exception) or not p:
            continue
        eid, verdict = p
        if eid and verdict:
            out[eid] = verdict
    return out
