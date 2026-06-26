"""
Clinical Chat Engine — orchestrates Ollama LLM with hospital module APIs
to provide intelligent clinical responses with widget specifications.

Agentic capabilities:
  1. Multi-step reasoning chains (follow-up for missing params)
  2. Tool-use loops (multi-API chaining)
  3. Session memory (patient context across messages)
  4. Proactive alerts (vital deterioration detection)
"""

import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import openai

# Add project root to path
PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app_06_clinical_chat.backend.intents import detect_intent

logger = logging.getLogger("clinical_chat")

# ── Widget type mapping by intent / data shape ──────────────────────────

INTENT_WIDGET_MAP: dict[str, str] = {
    "vitals": "vitals_chart",
    "risk_assessment": "risk_gauge",
    "lab_check": "lab_panel",
    "patient_lookup": "patient_summary",
    "triage": "triage_result",
    "medication_review": "medication_list",
    "pathway": "pathway",
    "cohort_stats": "cohort_stats",
    "note_analysis": "table",
    "sofa": "risk_gauge",
}

# Intents that require patient_id (only for data-fetching intents, not knowledge)
PATIENT_ID_INTENTS = {"patient_lookup", "vitals", "lab_check", "medication_review"}
# Intents that require both patient_id and hadm_id
HADM_ID_INTENTS = {"vitals", "lab_check", "medication_review"}
# Intents that NEVER need params (pure knowledge / LLM-only)
NO_PARAMS_INTENTS = {"general_clinical"}
# Intents that are legitimately ABOUT the current patient — prompt/context
# injection of patient identifiers is appropriate here. ``risk_assessment``,
# ``sofa``, ``triage`` (param-based), etc. either bring their own data in the
# message (triage) or target the current session's patient (risk, sofa).
PATIENT_CONTEXT_INTENTS = (
    PATIENT_ID_INTENTS
    | HADM_ID_INTENTS
    | {"risk_assessment", "sofa", "note_analysis", "pathway"}
)

MAX_TOOL_CALLS = 3

# ── System prompts ───────────────────────────────────────────────────────

_INTENT_PROMPT_BASE = (
    "You are a clinical AI assistant integrated with a hospital information system. "
    "Analyze the user's query and respond ONLY with valid JSON (no markdown, no extra text):\n"
    '{{"intent": "<one of: patient_lookup|triage|risk_assessment|pathway|lab_check|'
    'vitals|medication_review|cohort_stats|note_analysis|general_clinical>",\n'
    ' "params": {{"patient_id": "...", "hadm_id": "...", "vitals": {{...}}, "note_text": "..."}},\n'
    ' "reasoning": "I need to..."}}\n\n'
    "CRITICAL RULES:\n"
    "- Use 'general_clinical' for ALL knowledge/explanation questions like 'What is X?', 'Explain X', "
    "'How does X work?', 'Define X', 'Tell me about X', 'What are normal ranges?'. "
    "These do NOT need patient_id or any params.\n"
    "- Use 'triage' ONLY when the user provides actual vital sign values to triage.\n"
    "- Use 'vitals' ONLY when the user wants to FETCH stored vitals for a specific patient.\n"
    "- Use 'sofa' ONLY when the user wants to CALCULATE SOFA for a specific patient, not to learn about SOFA.\n"
    "- Only include params that are explicitly present in the user message. "
    "For patient IDs use the number the user mentioned. "
    "For vitals, extract numeric values only when the user gives them.\n"
    "{memory_context}"
)
# Pre-validate the template
INTENT_SYSTEM_PROMPT_TEMPLATE = _INTENT_PROMPT_BASE

RESPONSE_SYSTEM_PROMPT = (
    "You are a clinical AI assistant. Given data from the hospital system, provide a "
    "clear, concise clinical summary. Be specific with numbers and values from the data. "
    "Suggest follow-up actions when appropriate. Write in a professional medical tone."
)


# ── Session Memory ──────────────────────────────────────────────────────

@dataclass
class SessionMemory:
    """Persists patient context across messages within a session."""
    current_patient_id: int | None = None
    current_hadm_id: int | None = None
    current_patient_name: str | None = None
    patient_data_cache: dict = field(default_factory=dict)   # patient_id -> summary data
    conversation_topics: list[str] = field(default_factory=list)  # recent intents
    alert_watchlist: list[dict] = field(default_factory=list)  # patients being monitored
    pending_action: dict | None = None  # incomplete action awaiting user input


class ClinicalChatEngine:
    """Orchestrates Ollama LLM calls with hospital module API queries."""

    # Model routing: use the best model for each task.
    # Rule of thumb — if authoritative ML results already drive the answer
    # (triage, risk, SOFA, vitals, lab summaries), the LLM's job is to prose
    # the numbers. llama3.2:3b does this in 2-3 seconds vs deepseek-r1:8b's
    # 20-30 seconds of chain-of-thought. Reserve deepseek-r1 for open-ended
    # knowledge / pathway / differential-diagnosis reasoning.
    MODEL_ROUTING = {
        "intent_detection": "llama3.2:3b",        # fast (65 t/s) — just classify intent
        "clinical_summary": "llama3.2:3b",        # fast prose over ML output (default for structured intents)
        "clinical_response": "deepseek-r1:8b",    # best medical accuracy (93% MedQA) + CoT — open-ended reasoning
        "medical_qa": "deepseek-r1:8b",           # chain-of-thought reasoning for clinical questions
        "note_analysis": "MedAIBase/MedGemma1.5:4b-it",  # purpose-built for medical text
        "biomedical": "koesn/llama3-openbiollm-8b:q4_K_M",  # domain-specific biomedical
        "fast_fallback": "llama3.2:3b",            # when speed matters over accuracy
    }

    # Intent -> model task mapping. Structured intents (where the ML model
    # produces the authoritative numbers) use the fast summary model by
    # default; open-ended reasoning uses deepseek-r1.
    INTENT_MODEL_MAP = {
        "triage": "clinical_summary",
        "risk_assessment": "clinical_summary",
        "sofa": "clinical_summary",
        "patient_lookup": "clinical_summary",
        "vitals": "clinical_summary",
        "lab_check": "clinical_summary",
        "medication_review": "clinical_summary",
        "pathway": "clinical_response",            # multi-step reasoning still wants CoT
        "note_analysis": "note_analysis",
        "cohort_stats": "fast_fallback",
        "general_clinical": "medical_qa",
    }

    def __init__(
        self,
        ollama_base: str = "http://localhost:11434",
        model: str = "deepseek-r1:8b",
        openai_api_key: str | None = None,
    ):
        self.ollama_base = ollama_base
        self.model = model  # user-selected override (from frontend dropdown)
        self.api_endpoints = {
            "ed": os.environ.get("ED_TRIAGE_URL", "http://localhost:8201"),
            "oncology": os.environ.get("ONCOLOGY_AI_URL", "http://localhost:8204"),
            "journey": os.environ.get("PATIENT_JOURNEY_URL", "http://localhost:8205"),
        }
        # GPT for intent detection (fast, accurate, understands knowledge vs data questions)
        self.openai_client = None
        api_key = openai_api_key or os.environ.get("OPENAI_API_KEY", "")
        if api_key:
            try:
                # Fix SSL cert issue in some conda envs
                if "SSL_CERT_FILE" in os.environ and not os.path.exists(os.environ["SSL_CERT_FILE"]):
                    del os.environ["SSL_CERT_FILE"]
                self.openai_client = openai.OpenAI(api_key=api_key)
                logger.info("OpenAI GPT enabled for intent detection")
            except Exception as exc:
                logger.warning("Failed to initialize OpenAI client: %s", exc)
        # Session memory store: session_id -> SessionMemory
        self.sessions: dict[str, SessionMemory] = {}

    def _get_session(self, session_id: str) -> SessionMemory:
        """Get or create a session memory for the given session_id."""
        if session_id not in self.sessions:
            self.sessions[session_id] = SessionMemory()
        return self.sessions[session_id]

    def clear_patient(self, session_id: str) -> dict:
        """Forget only the active patient for this session.

        Zeros out ``current_patient_id`` / ``current_hadm_id`` /
        ``current_patient_name`` / ``patient_data_cache`` / ``pending_action``
        but preserves ``conversation_topics`` and the ConversationBuffer's
        chat history. Returned payload reports what was cleared so the UI
        can show a toast.
        """
        memory = self.sessions.get(session_id)
        if memory is None:
            return {"cleared": False, "reason": "no_session"}
        had_patient = memory.current_patient_id is not None
        had_cache = bool(memory.patient_data_cache)
        memory.current_patient_id = None
        memory.current_hadm_id = None
        memory.current_patient_name = None
        memory.patient_data_cache = {}
        memory.pending_action = None
        return {
            "cleared": True,
            "had_patient": had_patient,
            "had_cache": had_cache,
            "session_id": session_id,
        }

    def _select_model(self, task: str, user_model: str | None = None) -> str:
        """Select the best model for a task, with user override."""
        if user_model and user_model != "auto":
            return user_model
        return self.MODEL_ROUTING.get(task, self.model)

    # ── Public interface ─────────────────────────────────────────────────

    async def chat(
        self,
        message: str,
        history: list | None = None,
        user_model: str | None = None,
        session_id: str = "default",
    ) -> dict:
        """
        Process a user message through the full agentic pipeline:
        1. Check for pending action continuation
        2. Detect intent (via fast model or fallback regex)
        3. Resolve params from session memory
        4. Multi-step: if params still incomplete, return follow-up
        5. Tool-use loop: fetch data, chain if needed
        6. Proactive alerts: check fetched data for deterioration
        7. Generate clinical response
        8. Update session memory
        9. Attach widget specifications
        """
        history = history or []
        thinking: list[str] = []
        memory = self._get_session(session_id)

        # ── Step 0: Check for pending action continuation ────────────
        intent = None
        params = {}
        reasoning = ""

        if memory.pending_action and self._is_continuation(message, memory):
            pending = memory.pending_action
            intent = pending["intent"]
            params = pending.get("params", {})
            # Fill in the missing params from the new message
            self._fill_missing_from_message(message, params, pending.get("missing", []), memory)
            reasoning = f"Continuing pending '{intent}' action with new parameters."
            thinking.append(f"Step 1: Detected continuation of pending '{intent}' action.")
            memory.pending_action = None  # clear pending
        else:
            memory.pending_action = None  # clear stale pending

            # ── Pre-check: regex can reliably detect specific intents ──
            from app_06_clinical_chat.backend.intents import detect_intent as _regex_detect
            pre_check = _regex_detect(message)
            if pre_check["intent"] != "general_clinical":
                # Regex matched a specific intent — use it directly (fast & reliable)
                intent = pre_check["intent"]
                params = pre_check.get("params", {})
                reasoning = pre_check["reasoning"]
                thinking.append(f"Step 1: {reasoning}")
            elif pre_check["intent"] == "general_clinical":
                # Regex couldn't determine a specific intent — try LLM
                intent_model = self._select_model("intent_detection", user_model)
                thinking.append(f"Step 1: Identifying intent using {intent_model}...")

                old_model = self.model
                self.model = intent_model
                intent_result = await self._detect_intent(message, history, memory)
                self.model = old_model

                intent = intent_result.get("intent", "general_clinical")
                params = intent_result.get("params", {})
                reasoning = intent_result.get("reasoning", "")

        thinking.append(f"Step 2: Detected intent = '{intent}'. {reasoning}")

        # Track conversation topic
        memory.conversation_topics.append(intent)
        if len(memory.conversation_topics) > 20:
            memory.conversation_topics = memory.conversation_topics[-20:]

        # ── Step 2: Resolve params from session memory ───────────────
        self._resolve_params(intent, params, memory)
        thinking.append(f"Step 2b: Resolved params = {params}")

        # ── Step 3: Multi-step — check for missing required params ───
        missing = self._get_missing_params(intent, params)
        if missing:
            follow_up_msg = self._build_follow_up_message(intent, missing)
            memory.pending_action = {
                "intent": intent,
                "params": params,
                "missing": missing,
            }
            thinking.append(f"Step 3: Missing parameters {missing}. Asking user for clarification.")
            return {
                "thinking": thinking,
                "response": follow_up_msg,
                "widgets": [],
                "alerts": [],
                "pending_action": {"intent": intent, "missing": missing},
                "session": self._session_info(memory, intent),
            }

        # ── Step 4: Tool-use loop (multi-API chaining) ───────────────
        thinking.append("Step 3: Fetching relevant data from hospital systems...")

        all_data: dict = {}
        all_widgets: list[dict] = []
        all_errors: list[str] = []
        current_intent = intent

        for step in range(MAX_TOOL_CALLS):
            data, error = await self._fetch_data(current_intent, params)

            if error:
                all_errors.append(error)
                thinking.append(f"Step 3{'abcde'[step]} note: {error}")
            if data:
                all_data.update(data)
                all_widgets.extend(self._build_widgets(current_intent, data))

            # Update session memory from fetched data
            self._update_memory_from_data(current_intent, params, data, memory)

            # Check if follow-up fetch is needed
            next_intent = self._should_chain(current_intent, data, params, memory)
            if not next_intent:
                break
            current_intent = next_intent
            thinking.append(
                f"Step 3{'abcde'[step+1] if step+1 < 5 else 'x'}: "
                f"Chaining to '{next_intent}' for additional context..."
            )

        api_error = "; ".join(all_errors) if all_errors else None

        if all_data:
            thinking.append(
                f"Step 4: Received data ({_data_summary(all_data)}). Generating clinical summary..."
            )
        else:
            thinking.append("Step 4: No structured data retrieved. Generating response from clinical knowledge...")

        # ── Step 5: Proactive alerts ─────────────────────────────────
        alerts = self._check_alerts(all_data, intent)
        if alerts:
            thinking.append(f"Step 4a: Detected {len(alerts)} clinical alert(s)!")

        # ── Step 6: Generate response (best model for this intent) ───
        response_task = self.INTENT_MODEL_MAP.get(intent, "clinical_response")
        response_model = self._select_model(response_task, user_model)
        thinking.append(f"Step 4b: Using {response_model} for clinical reasoning...")

        old_model = self.model
        self.model = response_model
        response_text = await self._generate_response(
            message, intent, all_data, api_error, history, alerts, memory
        )
        self.model = old_model

        thinking.append("Step 5: Response ready.")

        return {
            "thinking": thinking,
            "response": response_text,
            "widgets": all_widgets,
            "alerts": alerts,
            "pending_action": None,
            "session": self._session_info(memory, intent),
        }

    # ── Session helpers ──────────────────────────────────────────────────

    @staticmethod
    def _session_info(memory: SessionMemory, intent: str | None = None) -> dict:
        """Return serializable session state for the response.

        When ``intent`` is provided and isn't patient-relevant, the patient
        fields are suppressed in the *response* so the dashboard's context
        bar doesn't keep advertising a patient the current question didn't
        actually touch. The server-side ``SessionMemory`` is unchanged —
        the user can still follow up with "How's their heart rate?" and the
        engine will re-resolve the current patient from memory.
        """
        patient_visible = intent is None or intent in PATIENT_CONTEXT_INTENTS
        return {
            "patient_id": memory.current_patient_id if patient_visible else None,
            "hadm_id": memory.current_hadm_id if patient_visible else None,
            "patient_name": memory.current_patient_name if patient_visible else None,
            "intent": intent,
            "patient_in_memory": bool(memory.current_patient_id),
        }

    # ── Multi-step reasoning helpers ─────────────────────────────────────

    def _is_continuation(self, message: str, memory: SessionMemory) -> bool:
        """Check if the message is a continuation of a pending action."""
        if not memory.pending_action:
            return False
        msg = message.strip().lower()
        missing = memory.pending_action.get("missing", [])
        # If the user sends just a number and we're missing patient_id or hadm_id
        if re.match(r"^\d{3,10}$", msg):
            if "patient_id" in missing or "hadm_id" in missing:
                return True
        # If there's a pending action and the message is short (likely a direct answer)
        if len(msg.split()) <= 5 and missing:
            return True
        return False

    def _fill_missing_from_message(
        self, message: str, params: dict, missing: list[str], memory: SessionMemory
    ):
        """Extract missing param values from a follow-up message."""
        msg = message.strip()
        number_match = re.search(r"\b(\d{3,10})\b", msg)
        if number_match:
            num_val = number_match.group(1)
            if "patient_id" in missing and "patient_id" not in params:
                params["patient_id"] = num_val
            elif "hadm_id" in missing and "hadm_id" not in params:
                params["hadm_id"] = num_val

    def _resolve_params(self, intent: str, params: dict, memory: SessionMemory):
        """Fill in missing params from session memory."""
        if intent in PATIENT_ID_INTENTS and not params.get("patient_id"):
            if memory.current_patient_id:
                params["patient_id"] = str(memory.current_patient_id)

        if intent in HADM_ID_INTENTS and not params.get("hadm_id"):
            if memory.current_hadm_id:
                params["hadm_id"] = str(memory.current_hadm_id)

    def _get_missing_params(self, intent: str, params: dict) -> list[str]:
        """Determine which required params are still missing for an intent."""
        # Knowledge intents and general questions never need params
        if intent in NO_PARAMS_INTENTS or intent == "sofa" or intent == "cohort_stats":
            return []
        missing = []
        if intent in PATIENT_ID_INTENTS and not params.get("patient_id"):
            missing.append("patient_id")
        if intent in HADM_ID_INTENTS and not params.get("hadm_id"):
            if params.get("patient_id") and not params.get("hadm_id"):
                missing.append("hadm_id")
        if intent == "triage" and not params.get("vitals"):
            missing.append("vitals")
        if intent == "note_analysis" and not params.get("note_text"):
            missing.append("note_text")
        return missing

    @staticmethod
    def _build_follow_up_message(intent: str, missing: list[str]) -> str:
        """Build a natural-language follow-up question for missing params."""
        parts = []
        if "patient_id" in missing:
            parts.append("Which patient are you asking about? Please provide a patient ID.")
        if "hadm_id" in missing:
            parts.append("Which admission should I look at? Please provide an admission (hadm) ID.")
        if "vitals" in missing:
            parts.append(
                "I need vital signs for triage. Please provide values such as HR, BP, SpO2, "
                "temperature, and respiratory rate."
            )
        if "note_text" in missing:
            parts.append("Please provide the clinical note text you'd like me to analyze.")

        prefix = {
            "vitals": "I'd like to check the vitals.",
            "lab_check": "I can look up lab results.",
            "medication_review": "I can review medications.",
            "patient_lookup": "I can look up that patient.",
            "triage": "I can perform an ED triage assessment.",
            "note_analysis": "I can analyze a clinical note.",
        }.get(intent, "I can help with that.")

        return f"{prefix} {' '.join(parts)}"

    # ── Tool-use loop: chaining logic ────────────────────────────────────

    def _should_chain(
        self, intent: str, data: dict | None, params: dict, memory: SessionMemory
    ) -> str | None:
        """
        Determine if the current fetch should chain into another API call.
        Returns the next intent to fetch, or None to stop.
        """
        if not data:
            return None

        # patient_lookup + has ICU stays -> chain to vitals
        if intent == "patient_lookup":
            summary = data.get("summary", {})
            admissions = summary.get("admissions", [])
            for adm in admissions:
                icu_stays = adm.get("icu_stays", [])
                if icu_stays:
                    # Use the first admission with ICU stays
                    hadm_id = adm.get("hadm_id")
                    if hadm_id and params.get("patient_id"):
                        params["hadm_id"] = str(hadm_id)
                        memory.current_hadm_id = int(hadm_id)
                        return "vitals"
            # Even without ICU stays, pick the first admission for context
            if admissions and not params.get("hadm_id"):
                first_hadm = admissions[0].get("hadm_id")
                if first_hadm:
                    params["hadm_id"] = str(first_hadm)
                    memory.current_hadm_id = int(first_hadm)

        # triage + ESI <= 2 (critical) + has patient_id -> chain to patient_lookup
        if intent == "triage":
            esi = data.get("esi_level") or data.get("predicted_esi") or data.get("acuity")
            try:
                esi_val = int(esi) if esi is not None else None
            except (ValueError, TypeError):
                esi_val = None
            if esi_val is not None and esi_val <= 2 and params.get("patient_id"):
                return "patient_lookup"

        # lab_check + critical flags -> chain to vitals
        if intent == "lab_check":
            panels = data.get("data", {}).get("panels", {})
            has_critical = False
            for panel_name, labs in panels.items():
                for lab_name, points in labs.items():
                    if not points:
                        continue
                    latest = points[-1] if isinstance(points, list) else points
                    flag = latest.get("flag", "") if isinstance(latest, dict) else ""
                    if "critical" in str(flag).lower():
                        has_critical = True
                        break
                if has_critical:
                    break
            if has_critical and params.get("patient_id") and params.get("hadm_id"):
                return "vitals"

        # risk_assessment -> chain to pathway (auto-suggest treatment)
        if intent == "risk_assessment":
            return "pathway"

        return None

    # ── Proactive alerts: vital deterioration detection ──────────────────

    def _check_alerts(self, data: dict, intent: str) -> list[dict]:
        """Scan fetched data for clinical deterioration and abnormal values."""
        alerts: list[dict] = []

        # Check vitals for critical ranges
        if "vitals" in str(data):
            vitals_data = data.get("data", {}).get("vitals", {})
            if not vitals_data and isinstance(data.get("vitals"), dict):
                vitals_data = data["vitals"]

            for vital_name, points in vitals_data.items() if isinstance(vitals_data, dict) else []:
                if not points or not isinstance(points, list):
                    continue
                latest_point = points[-1]
                latest = latest_point.get("value") if isinstance(latest_point, dict) else latest_point
                if latest is None:
                    continue

                try:
                    latest = float(latest)
                except (ValueError, TypeError):
                    continue

                if vital_name == "Heart Rate" and (latest < 40 or latest > 150):
                    alerts.append({
                        "severity": "critical", "type": "vital",
                        "message": f"Critical HR: {latest:.0f} bpm",
                        "vital": vital_name, "value": latest,
                    })
                if vital_name == "SpO2" and latest < 90:
                    alerts.append({
                        "severity": "critical", "type": "vital",
                        "message": f"Hypoxemia: SpO2 {latest:.0f}%",
                        "vital": vital_name, "value": latest,
                    })
                if vital_name == "SBP" and latest < 80:
                    alerts.append({
                        "severity": "critical", "type": "vital",
                        "message": f"Hypotension: SBP {latest:.0f} mmHg",
                        "vital": vital_name, "value": latest,
                    })
                if vital_name == "Temperature" and latest > 39.5:
                    alerts.append({
                        "severity": "warning", "type": "vital",
                        "message": f"High fever: {latest:.1f} C",
                        "vital": vital_name, "value": latest,
                    })

                # Trend detection: if last 5 values show consistent decline
                if len(points) >= 5:
                    recent_vals = []
                    for p in points[-5:]:
                        v = p.get("value") if isinstance(p, dict) else p
                        try:
                            recent_vals.append(float(v))
                        except (ValueError, TypeError):
                            break
                    if len(recent_vals) == 5:
                        if all(recent_vals[i] < recent_vals[i - 1] for i in range(1, len(recent_vals))):
                            alerts.append({
                                "severity": "warning", "type": "trend",
                                "message": f"{vital_name} declining: {recent_vals[0]:.1f} -> {recent_vals[-1]:.1f}",
                                "vital": vital_name,
                            })
                        if all(recent_vals[i] > recent_vals[i - 1] for i in range(1, len(recent_vals))):
                            alerts.append({
                                "severity": "warning", "type": "trend",
                                "message": f"{vital_name} rising: {recent_vals[0]:.1f} -> {recent_vals[-1]:.1f}",
                                "vital": vital_name,
                            })

        # Check labs for abnormal flags
        if "panels" in str(data):
            panels = data.get("data", {}).get("panels", {})
            for panel_name, labs in panels.items() if isinstance(panels, dict) else []:
                if not isinstance(labs, dict):
                    continue
                for lab_name, points in labs.items():
                    if not points or not isinstance(points, list):
                        continue
                    latest = points[-1]
                    if not isinstance(latest, dict):
                        continue
                    flag = latest.get("flag", "")
                    if flag in ("high", "critical_high", "low", "critical_low"):
                        severity = "critical" if "critical" in flag else "warning"
                        alerts.append({
                            "severity": severity, "type": "lab",
                            "message": f"Abnormal {lab_name}: {latest.get('value')} ({flag})",
                            "lab": lab_name,
                        })

        return alerts

    # ── Memory update from fetched data ──────────────────────────────────

    def _update_memory_from_data(
        self, intent: str, params: dict, data: dict | None, memory: SessionMemory
    ):
        """Update session memory with information from fetched data."""
        if not data:
            return

        pid = params.get("patient_id")
        if pid:
            try:
                memory.current_patient_id = int(pid)
            except (ValueError, TypeError):
                pass

        hadm = params.get("hadm_id")
        if hadm:
            try:
                memory.current_hadm_id = int(hadm)
            except (ValueError, TypeError):
                pass

        if intent == "patient_lookup":
            summary = data.get("summary", {})
            # Try to extract patient name
            patient_name = summary.get("patient_name") or summary.get("name")
            if patient_name:
                memory.current_patient_name = patient_name

            # Cache patient data
            if pid:
                memory.patient_data_cache[str(pid)] = summary

            # Auto-pick first hadm_id if not set
            admissions = summary.get("admissions", [])
            if admissions and not memory.current_hadm_id:
                first_hadm = admissions[0].get("hadm_id")
                if first_hadm:
                    try:
                        memory.current_hadm_id = int(first_hadm)
                    except (ValueError, TypeError):
                        pass

    # ── Intent detection ─────────────────────────────────────────────────

    async def _detect_intent(self, message: str, history: list, memory: SessionMemory) -> dict:
        """Detect intent: GPT (primary) → Ollama (fallback) → regex (last resort)."""

        memory_context = self._build_memory_context(memory)
        system_prompt = INTENT_SYSTEM_PROMPT_TEMPLATE.format(memory_context=memory_context)

        context_messages = []
        for h in history[-4:]:
            role = h.get("role", "user")
            context_messages.append({"role": role, "content": h.get("content", "")})

        messages = [
            {"role": "system", "content": system_prompt},
            *context_messages,
            {"role": "user", "content": message},
        ]

        # Try 1: GPT (fast, accurate, understands knowledge vs data)
        if self.openai_client:
            try:
                resp = self.openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=messages,
                    max_tokens=200,
                    temperature=0,
                )
                raw = resp.choices[0].message.content or ""
                parsed = self._parse_json_response(raw)
                if parsed and "intent" in parsed:
                    parsed["reasoning"] = f"(GPT) {parsed.get('reasoning', '')}"
                    return parsed
            except Exception as exc:
                logger.warning("GPT intent detection failed (%s), trying Ollama", exc)

        # Try 2: Ollama local LLM
        try:
            raw = await self._call_ollama(messages)
            parsed = self._parse_json_response(raw)
            if parsed and "intent" in parsed:
                return parsed
        except Exception as exc:
            logger.warning("Ollama intent detection failed (%s), using regex fallback", exc)

        # Try 3: Regex fallback
        return detect_intent(message)

    @staticmethod
    def _build_memory_context(memory: SessionMemory) -> str:
        """Build a context string from session memory for the intent prompt."""
        parts = []
        if memory.current_patient_id or memory.current_hadm_id:
            parts.append(
                f"Current patient context: patient_id={memory.current_patient_id}, "
                f"hadm_id={memory.current_hadm_id}"
            )
            if memory.current_patient_name:
                parts.append(f"Patient name: {memory.current_patient_name}")
            parts.append(
                'If the user refers to "the patient", "their", "this patient", '
                "use the current context."
            )
        if memory.conversation_topics:
            recent = memory.conversation_topics[-5:]
            parts.append(f"Recent conversation topics: {', '.join(recent)}")
        if not parts:
            return ""
        return "\n" + "\n".join(parts)

    # ── Data fetching ────────────────────────────────────────────────────

    async def _fetch_data(self, intent: str, params: dict) -> tuple[dict | None, str | None]:
        """
        Call the appropriate module API based on intent.
        Returns (data_dict | None, error_message | None).
        """
        try:
            if intent == "patient_lookup":
                return await self._fetch_patient_lookup(params)
            elif intent == "triage":
                return await self._fetch_triage(params)
            elif intent == "risk_assessment":
                return await self._fetch_risk_assessment(params)
            elif intent == "pathway":
                return await self._fetch_pathway(params)
            elif intent == "lab_check":
                return await self._fetch_labs(params)
            elif intent == "vitals":
                return await self._fetch_vitals(params)
            elif intent == "medication_review":
                return await self._fetch_medications(params)
            elif intent == "cohort_stats":
                return await self._fetch_cohort_stats()
            elif intent == "note_analysis":
                return await self._fetch_note_analysis(params)
            elif intent == "sofa":
                # If patient_id provided, look up patient; otherwise just answer from knowledge
                if params.get("patient_id"):
                    return await self._fetch_patient_lookup(params)
                return None, None  # answer from LLM knowledge
            else:
                return None, None  # general_clinical — no API call needed
        except httpx.ConnectError:
            return None, "Module API is not running. Answering from clinical knowledge only."
        except httpx.TimeoutException:
            return None, "Module API timed out. Answering from clinical knowledge only."
        except Exception as exc:
            logger.exception("API fetch error")
            return None, f"Error contacting module API: {exc}"

    async def _fetch_patient_lookup(self, params: dict) -> tuple[dict | None, str | None]:
        pid = params.get("patient_id")
        if not pid:
            return None, "No patient ID provided. Please specify a patient ID."
        base = self.api_endpoints["journey"]
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            summary_resp = await client.get(f"{base}/patient/{pid}/summary")
            summary_resp.raise_for_status()
            summary_json = summary_resp.json()
            data = {"summary": summary_json}
            # Fetch metrics for the most recent admission
            try:
                summary_data = summary_json.get("data", summary_json)
                admissions = summary_data.get("admissions", [])
                if admissions:
                    hadm_id = params.get("hadm_id") or admissions[-1].get("hadm_id")
                    if hadm_id:
                        metrics_resp = await client.get(f"{base}/patient/{pid}/admission/{hadm_id}/metrics")
                        metrics_resp.raise_for_status()
                        data["metrics"] = metrics_resp.json()
            except Exception:
                pass  # metrics optional
            return data, None

    async def _fetch_triage(self, params: dict) -> tuple[dict | None, str | None]:
        vitals = params.get("vitals", {})
        # Also check top-level params for vitals extracted by regex
        for k, v in params.items():
            if k not in ("patient_id", "hadm_id", "vitals", "note_text"):
                vitals[k] = v
        if not vitals:
            return None, "No vital signs provided for triage. Please include vitals (HR, BP, SpO2, etc.)."
        # Map common abbreviations to API field names
        key_map = {
            "hr": "heart_rate", "heart_rate": "heart_rate",
            "rr": "respiratory_rate", "respiratory_rate": "respiratory_rate",
            "spo2": "spo2", "o2sat": "spo2",
            "sbp": "sbp", "systolic": "sbp",
            "dbp": "dbp", "diastolic": "dbp",
            "temp": "temperature", "temperature": "temperature",
            "wbc": "wbc", "hemoglobin": "hemoglobin",
            "lactate": "lactate", "glucose": "glucose",
            "creatinine": "creatinine",
            "age": "age", "gender": "gender",
            "arrival_mode": "arrival_mode",
        }
        payload: dict = {}
        for k, v in vitals.items():
            mapped = key_map.get(k.lower().replace(" ", "_"))
            if mapped:
                payload[mapped] = v
        if not payload:
            return None, "Could not parse vital signs. Please include HR, SpO2, SBP, etc."
        # Defaults
        payload.setdefault("age", 50)
        payload.setdefault("gender", "M")
        payload.setdefault("arrival_mode", "EMERGENCY ROOM")
        base = self.api_endpoints["ed"]
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            resp = await client.post(f"{base}/predict", json=payload)
            resp.raise_for_status()
            return resp.json(), None

    async def _fetch_risk_assessment(self, params: dict) -> tuple[dict | None, str | None]:
        base = self.api_endpoints["oncology"]
        payload = {k: v for k, v in params.items() if k not in ("patient_id", "hadm_id")}
        if not payload:
            payload = {"patient_id": params.get("patient_id", "unknown")}
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            resp = await client.post(f"{base}/predict-risk", json=payload)
            resp.raise_for_status()
            return resp.json(), None

    async def _fetch_pathway(self, params: dict) -> tuple[dict | None, str | None]:
        base = self.api_endpoints["oncology"]
        payload = {k: v for k, v in params.items() if k not in ("hadm_id",)}
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            resp = await client.post(f"{base}/recommend-pathway", json=payload)
            resp.raise_for_status()
            return resp.json(), None

    async def _fetch_labs(self, params: dict) -> tuple[dict | None, str | None]:
        pid = params.get("patient_id")
        hadm = params.get("hadm_id")
        if not pid or not hadm:
            return None, "Patient ID and admission ID are required for lab lookup."
        base = self.api_endpoints["journey"]
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            resp = await client.get(f"{base}/patient/{pid}/admission/{hadm}/labs")
            resp.raise_for_status()
            return resp.json(), None

    async def _fetch_vitals(self, params: dict) -> tuple[dict | None, str | None]:
        pid = params.get("patient_id")
        hadm = params.get("hadm_id")
        if not pid or not hadm:
            return None, "Patient ID and admission ID are required for vitals lookup."
        base = self.api_endpoints["journey"]
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            resp = await client.get(f"{base}/patient/{pid}/admission/{hadm}/vitals")
            resp.raise_for_status()
            return resp.json(), None

    async def _fetch_medications(self, params: dict) -> tuple[dict | None, str | None]:
        pid = params.get("patient_id")
        hadm = params.get("hadm_id")
        if not pid or not hadm:
            return None, "Patient ID and admission ID are required for medication review."
        base = self.api_endpoints["journey"]
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            resp = await client.get(f"{base}/patient/{pid}/admission/{hadm}/medications")
            resp.raise_for_status()
            return resp.json(), None

    async def _fetch_cohort_stats(self) -> tuple[dict | None, str | None]:
        base = self.api_endpoints["oncology"]
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            resp = await client.get(f"{base}/cohort-stats")
            resp.raise_for_status()
            return resp.json(), None

    async def _fetch_note_analysis(self, params: dict) -> tuple[dict | None, str | None]:
        note_text = params.get("note_text", "")
        if not note_text:
            return None, "No clinical note text provided for analysis."
        base = self.api_endpoints["oncology"]
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            resp = await client.post(f"{base}/analyze-note", json={"text": note_text})
            resp.raise_for_status()
            return resp.json(), None

    # ── Response generation ──────────────────────────────────────────────

    async def _generate_response(
        self,
        message: str,
        intent: str,
        api_data: dict | None,
        api_error: str | None,
        history: list,
        alerts: list[dict] | None = None,
        memory: SessionMemory | None = None,
    ) -> str:
        """Generate a clinical response using Ollama with the fetched data."""
        data_section = ""
        if api_data:
            data_section = f"\n\nHospital system data:\n{json.dumps(api_data, indent=2, default=str)}"
        if api_error:
            data_section += f"\n\nNote: {api_error}"

        # Include alerts in prompt so the LLM can reference them
        alerts_section = ""
        if alerts:
            alerts_section = (
                "\n\nCLINICAL ALERTS DETECTED:\n"
                + json.dumps(alerts, indent=2, default=str)
                + "\nIMPORTANT: Mention these alerts prominently in your response."
            )

        # Include patient context ONLY when the intent is legitimately about
        # the current patient. Previously we always appended "Patient context:
        # ID=X" to every prompt — the LLM treated it as authoritative, which
        # caused general-knowledge queries like "What is NEWS2?" to confabulate
        # a clinical summary *for the last-looked-up patient*. Gating this on
        # PATIENT_CONTEXT_INTENTS fixes the leak.
        context_section = ""
        if memory and memory.current_patient_id and intent in PATIENT_CONTEXT_INTENTS:
            context_section = (
                f"\n\nPatient context: ID={memory.current_patient_id}"
                f"{f', Name={memory.current_patient_name}' if memory.current_patient_name else ''}"
                f"{f', Admission={memory.current_hadm_id}' if memory.current_hadm_id else ''}"
            )

        user_content = (
            f"User question: {message}"
            f"{data_section}{alerts_section}{context_section}"
            "\n\nRespond naturally. Reference specific values from the data."
        )

        context_messages = []
        for h in history[-4:]:
            role = h.get("role", "user")
            context_messages.append({"role": role, "content": h.get("content", "")})

        messages = [
            {"role": "system", "content": RESPONSE_SYSTEM_PROMPT},
            *context_messages,
            {"role": "user", "content": user_content},
        ]

        try:
            return await self._call_ollama(messages)
        except Exception as exc:
            logger.warning("Ollama response generation failed (%s), trying GPT fallback", exc)

        # Fallback: GPT for response generation
        if self.openai_client:
            try:
                resp = self.openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=messages,
                    max_tokens=1000,
                    temperature=0.3,
                )
                return resp.choices[0].message.content or ""
            except Exception as exc2:
                logger.warning("GPT response generation also failed (%s), using template", exc2)

        return self._template_response(intent, api_data, api_error, message, alerts)

    def _template_response(
        self,
        intent: str,
        api_data: dict | None,
        api_error: str | None,
        message: str,
        alerts: list[dict] | None = None,
    ) -> str:
        """Simple template-based response when Ollama is unavailable."""
        alert_text = ""
        if alerts:
            alert_lines = [f"  - [{a['severity'].upper()}] {a['message']}" for a in alerts]
            alert_text = "\n\n**Clinical Alerts:**\n" + "\n".join(alert_lines)

        if api_error and not api_data:
            return (
                f"I understood your request (intent: {intent}), but encountered an issue: "
                f"{api_error}\n\nPlease ensure the relevant service is running and try again."
                f"{alert_text}"
            )
        if api_data:
            summary = json.dumps(api_data, indent=2, default=str)
            if len(summary) > 2000:
                summary = summary[:2000] + "\n... (truncated)"
            return (
                f"Here is the data retrieved for your query (intent: {intent}):\n\n"
                f"```json\n{summary}\n```\n\n"
                "Note: The LLM service is currently unavailable, so this is a raw data view. "
                "Please review the data above for clinical details."
                f"{alert_text}"
            )
        return (
            "I'm currently unable to reach the LLM service for a detailed answer. "
            "Please try again shortly, or rephrase your question."
            f"{alert_text}"
        )

    # ── Widget construction ──────────────────────────────────────────────

    def _build_widgets(self, intent: str, api_data: dict | None) -> list[dict]:
        """Determine which widgets to include based on intent and data."""
        if not api_data:
            return []

        widgets: list[dict] = []
        widget_type = INTENT_WIDGET_MAP.get(intent)

        if widget_type:
            widgets.append({"type": widget_type, "data": api_data})

        # Add supplementary widgets based on data contents
        if isinstance(api_data, dict):
            if "metrics" in api_data and widget_type != "patient_summary":
                widgets.append({"type": "stats", "data": api_data["metrics"]})
            if "timeline" in api_data:
                widgets.append({"type": "timeline", "data": api_data["timeline"]})
            if "risk_score" in api_data or "risk" in api_data:
                if widget_type != "risk_gauge":
                    widgets.append({"type": "risk_gauge", "data": api_data})

        return widgets

    # ── Ollama communication ─────────────────────────────────────────────

    async def _call_ollama(self, messages: list[dict]) -> str:
        """Send a chat request to Ollama and return the assistant content."""
        async with httpx.AsyncClient(timeout=120.0, verify=False, trust_env=False) as client:
            resp = await client.post(
                f"{self.ollama_base}/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                },
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"]

    async def _call_ollama_stream(self, messages: list[dict]):
        """Call Ollama with ``stream: True`` and yield ``(kind, text)`` tuples.

        ``kind`` is either ``"reasoning"`` (chain-of-thought tokens from
        models like deepseek-r1) or ``"content"`` (final answer tokens).

        Deepseek-r1 streams its CoT in ``message.thinking`` *before* any
        ``message.content`` — naively forwarding only content gives the
        appearance of a 30-second silent stall. We yield both so the
        frontend can show the reasoning in a collapsible expander while
        the formal answer lands in the chat bubble.
        """
        try:
            async with httpx.AsyncClient(timeout=180.0, verify=False, trust_env=False) as client:
                async with client.stream(
                    "POST",
                    f"{self.ollama_base}/api/chat",
                    json={
                        "model": self.model,
                        "messages": messages,
                        "stream": True,
                        "options": {
                            # Cap CoT + answer at 512 tokens — ample for a
                            # clinical summary, prevents 3 000-token CoT spirals.
                            "num_predict": 512,
                        },
                    },
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        msg = obj.get("message") or {}
                        think = msg.get("thinking")
                        if think:
                            yield ("reasoning", think)
                        content = msg.get("content")
                        if content:
                            yield ("content", content)
                        if obj.get("done"):
                            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("_call_ollama_stream failed: %s", exc)
            return

    async def chat_stream(
        self,
        message: str,
        history: list | None = None,
        user_model: str | None = None,
        session_id: str = "default",
    ):
        """Same agentic pipeline as :meth:`chat`, but yields structured
        ``(event_type, payload)`` tuples as the work progresses — so the
        SSE endpoint can push tokens to the client in real time instead of
        waiting for the full response.

        Yield order:
            ("thinking", <str>)    — one per pipeline step, fast (<10ms each)
            ("context",  <dict>)   — session info after params are resolved
            ("pending_action", <dict>)  — if a follow-up is required (terminal)
            ("widgets",  <list>)   — widget spec list once tool data is in
            ("alerts",   <list>)   — proactive alerts (if any)
            ("token",    <str>)    — LLM output tokens streamed from Ollama
            ("final",    <dict>)   — full response dict at the end
        """
        history = history or []
        memory = self._get_session(session_id)
        thinking: list[str] = []

        # ── Step 0: pending-action continuation ───────────────────────
        intent = None
        params: dict = {}
        reasoning = ""

        if memory.pending_action and self._is_continuation(message, memory):
            pending = memory.pending_action
            intent = pending["intent"]
            params = pending.get("params", {})
            self._fill_missing_from_message(message, params, pending.get("missing", []), memory)
            reasoning = f"Continuing pending '{intent}' action with new parameters."
            thinking.append(f"Step 1: Detected continuation of pending '{intent}' action.")
            yield ("thinking", thinking[-1])
            memory.pending_action = None
        else:
            memory.pending_action = None
            # Regex first
            from app_06_clinical_chat.backend.intents import detect_intent as _regex_detect
            pre = _regex_detect(message)
            if pre["intent"] != "general_clinical":
                intent = pre["intent"]
                params = pre.get("params", {})
                reasoning = pre["reasoning"]
                thinking.append(f"Step 1: {reasoning}")
                yield ("thinking", thinking[-1])
            else:
                # LLM intent detection fallback
                intent_model = self._select_model("intent_detection", user_model)
                thinking.append(f"Step 1: Identifying intent using {intent_model}…")
                yield ("thinking", thinking[-1])
                old_model = self.model
                self.model = intent_model
                try:
                    intent_result = await self._detect_intent(message, history, memory)
                finally:
                    self.model = old_model
                intent = intent_result.get("intent", "general_clinical")
                params = intent_result.get("params", {})
                reasoning = intent_result.get("reasoning", "")

        thinking.append(f"Step 2: Detected intent = '{intent}'. {reasoning}")
        yield ("thinking", thinking[-1])

        memory.conversation_topics.append(intent)
        if len(memory.conversation_topics) > 20:
            memory.conversation_topics = memory.conversation_topics[-20:]

        # ── Step 2: resolve from memory ───────────────────────────────
        self._resolve_params(intent, params, memory)
        thinking.append(f"Step 2b: Resolved params = {params}")
        yield ("thinking", thinking[-1])

        # Emit session context early
        yield ("context", self._session_info(memory, intent))

        # ── Step 3: missing-params follow-up ──────────────────────────
        missing = self._get_missing_params(intent, params)
        if missing:
            follow_up_msg = self._build_follow_up_message(intent, missing)
            memory.pending_action = {"intent": intent, "params": params, "missing": missing}
            thinking.append(f"Step 3: Missing parameters {missing}. Asking user for clarification.")
            yield ("thinking", thinking[-1])
            yield ("pending_action", {"intent": intent, "missing": missing})
            yield ("token", follow_up_msg)
            yield ("final", {
                "thinking": thinking,
                "response": follow_up_msg,
                "widgets": [],
                "alerts": [],
                "pending_action": {"intent": intent, "missing": missing},
                "session": self._session_info(memory, intent),
            })
            return

        # ── Step 4: tool-use loop (concurrent-safe, same as chat()) ───
        thinking.append("Step 3: Fetching relevant data from hospital systems…")
        yield ("thinking", thinking[-1])

        all_data: dict = {}
        all_widgets: list[dict] = []
        all_errors: list[str] = []
        current_intent = intent

        for step in range(MAX_TOOL_CALLS):
            data, error = await self._fetch_data(current_intent, params)
            if error:
                all_errors.append(error)
                thinking.append(f"Step 3{'abcde'[step]} note: {error}")
                yield ("thinking", thinking[-1])
            if data:
                all_data.update(data)
                all_widgets.extend(self._build_widgets(current_intent, data))

            self._update_memory_from_data(current_intent, params, data, memory)

            next_intent = self._should_chain(current_intent, data, params, memory)
            if not next_intent:
                break
            current_intent = next_intent
            thinking.append(f"Step 3{'abcde'[step+1] if step+1 < 5 else 'x'}: Chaining to '{next_intent}'…")
            yield ("thinking", thinking[-1])

        api_error = "; ".join(all_errors) if all_errors else None

        if all_data:
            thinking.append(f"Step 4: Received data ({_data_summary(all_data)}). Generating clinical summary…")
        else:
            thinking.append("Step 4: No structured data. Generating response from clinical knowledge…")
        yield ("thinking", thinking[-1])

        # Widgets available now — emit so the dashboard can render them before
        # the LLM finishes speaking.
        if all_widgets:
            yield ("widgets", all_widgets)

        # ── Step 5: alerts ────────────────────────────────────────────
        alerts = self._check_alerts(all_data, intent)
        if alerts:
            thinking.append(f"Step 4a: Detected {len(alerts)} clinical alert(s)!")
            yield ("thinking", thinking[-1])
            yield ("alerts", alerts)

        # ── Step 6: stream the LLM response token-by-token ────────────
        response_task = self.INTENT_MODEL_MAP.get(intent, "clinical_response")
        response_model = self._select_model(response_task, user_model)
        thinking.append(f"Step 4b: Using {response_model} for clinical reasoning…")
        yield ("thinking", thinking[-1])

        # Build the prompt same as _generate_response
        data_section = f"\n\nHospital system data:\n{json.dumps(all_data, indent=2, default=str)}" if all_data else ""
        if api_error:
            data_section += f"\n\nNote: {api_error}"
        alerts_section = ""
        if alerts:
            alerts_section = (
                "\n\nCLINICAL ALERTS DETECTED:\n"
                + json.dumps(alerts, indent=2, default=str)
                + "\nIMPORTANT: Mention these alerts prominently in your response."
            )
        # Gate patient-context injection to patient-specific intents so
        # general-knowledge queries don't trigger hallucinated clinical
        # summaries about the current session's patient.
        context_section = ""
        if memory.current_patient_id and intent in PATIENT_CONTEXT_INTENTS:
            context_section = (
                f"\n\nPatient context: ID={memory.current_patient_id}"
                f"{f', Name={memory.current_patient_name}' if memory.current_patient_name else ''}"
                f"{f', Admission={memory.current_hadm_id}' if memory.current_hadm_id else ''}"
            )
        user_content = (
            f"User question: {message}"
            f"{data_section}{alerts_section}{context_section}"
            "\n\nRespond naturally. Reference specific values from the data."
        )
        context_messages = [
            {"role": h.get("role", "user"), "content": h.get("content", "")}
            for h in history[-4:]
        ]
        messages = [
            {"role": "system", "content": RESPONSE_SYSTEM_PROMPT},
            *context_messages,
            {"role": "user", "content": user_content},
        ]

        old_model = self.model
        self.model = response_model
        accumulated: list[str] = []
        try:
            async for kind, piece in self._call_ollama_stream(messages):
                if kind == "reasoning":
                    # CoT — route to the thinking expander, do NOT include in final text
                    yield ("reasoning", piece)
                else:
                    accumulated.append(piece)
                    yield ("token", piece)
        finally:
            self.model = old_model

        response_text = "".join(accumulated)
        thinking.append("Step 5: Response ready.")

        yield ("final", {
            "thinking": thinking,
            "response": response_text,
            "widgets": all_widgets,
            "alerts": alerts,
            "pending_action": None,
            "session": self._session_info(memory, intent),
        })

    # ── JSON parsing helpers ─────────────────────────────────────────────

    @staticmethod
    def _parse_json_response(raw: str) -> dict | None:
        """
        Parse JSON from an LLM response.  Handles markdown code fences and
        malformed output with a regex fallback.
        """
        # Strip markdown code fences
        cleaned = raw.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

        # Attempt direct parse
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Fallback: find the first {...} block
        match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        # Last resort: regex extraction of intent field
        intent_match = re.search(r'"intent"\s*:\s*"(\w+)"', raw)
        if intent_match:
            return {"intent": intent_match.group(1), "params": {}, "reasoning": "Extracted via regex fallback"}

        return None


# ── Helpers ──────────────────────────────────────────────────────────────

def _data_summary(data: Any) -> str:
    """Return a brief human-readable summary of an API response."""
    if isinstance(data, dict):
        keys = list(data.keys())
        return f"{len(keys)} fields: {', '.join(keys[:5])}"
    if isinstance(data, list):
        return f"list of {len(data)} items"
    return str(type(data).__name__)
