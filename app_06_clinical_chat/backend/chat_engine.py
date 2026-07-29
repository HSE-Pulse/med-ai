"""
Clinical Chat Engine — orchestrates Ollama LLM with hospital module APIs
to provide intelligent clinical responses with widget specifications.

Agentic capabilities:
  1. Multi-step reasoning chains (follow-up for missing params)
  2. Tool-use loops (multi-API chaining)
  3. Session memory (patient context across messages)
  4. Proactive alerts (vital deterioration detection)
"""

import asyncio
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import openai

# Add project root to path
PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app_06_clinical_chat.backend.intents import detect_intent
from app_06_clinical_chat.backend.service_catalog import ServiceCatalog

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
    "Suggest follow-up actions when appropriate. Write in a professional medical tone.\n\n"
    "Any number describing the CURRENT state of this hospital or its patients — counts, "
    "censuses, waits, occupancy — must come from the 'Hospital system data' block. Never "
    "estimate one, illustrate with a made-up one, or carry one over from an earlier "
    "message. If a live figure the question needs is absent, say plainly that it is "
    "unavailable — that is a correct answer, an invented number is not.\n"
    "This restricts live figures ONLY. When a question asks for clinical knowledge — "
    "what a condition is, how it is managed, what a score measures, normal ranges, "
    "guidelines — answer it fully from established medical knowledge, whether or not "
    "any data block is present. Do not refuse a knowledge question for lack of data, "
    "and do not preface such an answer with remarks about the hospital data.\n"
    "Glossary — expand these acronyms only as given, never guess:\n"
    "INMO = Irish Nurses and Midwives Organisation, which publishes Trolley Watch.\n"
    "TrolleyGAR = the HSE Special Delivery Unit's daily count of admitted patients "
    "waiting on trolleys.\n"
    "NEDOCS = National Emergency Department Overcrowding Scale (a crowding score; it "
    "is NOT an early warning score).\n"
    "PET = Patient Experience Time, the 6-hour ED target.\n"
    "DToC = delayed transfers of care."
)

def _wants_cohort_ranking(params: dict) -> bool:
    """A SOFA question with no patient is a cohort question."""
    return not params.get("patient_id")


def _pick_admission(admissions):
    """Choose the admission a follow-up question is about.

    Prefer the one flagged active; otherwise the most recent by admittime.
    The previous code took admissions[0] on the assumption it is the current
    stay. That happens to hold for the replay, but nothing guarantees it —
    and picking the wrong element means answering "what are his vitals" from
    a discharged admission years earlier, which reads as current.
    """
    if not admissions:
        return None
    active = [a for a in admissions if a.get("is_active")]
    if active:
        return active[0]
    dated = [a for a in admissions if a.get("admittime")]
    if dated:
        return max(dated, key=lambda a: str(a.get("admittime")))
    return admissions[0]


def _unwrap(payload):
    """Return the useful body of a service response.

    Services answer with a ``{"status", "data", "error"}`` envelope, and
    _fetch_patient_lookup stores that envelope verbatim under "summary".
    Callers reading ``summary["admissions"]`` therefore always got None: the
    patient's name was never cached and the current admission was never
    picked up, which is why a follow-up question about a patient stalled
    asking for an admission id the system had already fetched. Unwrapping
    here rather than in the fetcher keeps the payload handed to the model
    unchanged.
    """
    if isinstance(payload, dict) and "data" in payload and (
        "status" in payload or "error" in payload
    ):
        inner = payload.get("data")
        if isinstance(inner, (dict, list)):
            return inner
    return payload


def _render_data(data: dict) -> str:
    """Serialise fetched data for the prompt.

    Values under a ``*_report`` key are already laid out as plain text by the
    fetcher; JSON-encoding them would bury the table under escaped newlines
    and quotes, which is precisely the shape the response model misreads.
    Those pass through verbatim; everything else is JSON as before.
    """
    pre = {k: v for k, v in data.items() if k.endswith("_report") and isinstance(v, str)}
    rest = {k: v for k, v in data.items() if k not in pre}
    parts = list(pre.values())
    if rest:
        parts.append(json.dumps(rest, indent=2, default=str))
    return "\n\n".join(parts)


NO_LIVE_DATA_INSTRUCTION = (
    "\n\nNo live data was returned for this question. State that the current figures "
    "could not be retrieved and say what you would need. Do NOT supply example, "
    "approximate or remembered numbers — no patient counts, no trolley counts, no "
    "admission figures of any kind."
)


# ── Session Memory ──────────────────────────────────────────────────────

SESSION_COLL = "chat_sessions"
SESSION_TTL_DAYS = int(os.environ.get("CHAT_SESSION_TTL_DAYS", "7"))
# The cached patient summaries are the bulky part of a session and are
# rebuildable from patient_journey, so only the active patient's entry is
# persisted. Without a bound, a long-lived session grows the document until
# the write fails and nothing is saved at all.
SESSION_CACHE_MAX_BYTES = 64_000


class SessionStore:
    """Mongo-backed persistence for :class:`SessionMemory`.

    Session state used to live only in ``ClinicalChatEngine.sessions``, a
    process-local dict. Every restart — a deploy, a crash, a config change —
    silently forgot which patient the clinician was discussing, so the next
    "what are his vitals" asked them to identify the patient again. That is
    the same defect class that emptied the waiting list on restart, and the
    fix is the same: write it down.

    Degrades to memory-only if Mongo is unreachable; losing durability is
    survivable, refusing to answer is not.
    """

    def __init__(self):
        self._coll = None
        self._tried = False

    def _collection(self):
        if self._tried:
            return self._coll
        self._tried = True
        try:
            from shared.db.mongo import MongoManager
            mgr = MongoManager()
            coll = mgr.client["MIMIC_SIM"][SESSION_COLL]
            # Mongo expires documents itself, so abandoned sessions do not
            # accumulate and no sweeper job is needed.
            try:
                coll.create_index("updated_at", expireAfterSeconds=SESSION_TTL_DAYS * 86400)
            except Exception as exc:  # noqa: BLE001
                logger.debug("session_ttl_index_skipped: %s", exc)
            self._coll = coll
            logger.info("session_store_ready coll=%s ttl_days=%d",
                        SESSION_COLL, SESSION_TTL_DAYS)
        except Exception as exc:  # noqa: BLE001
            logger.warning("session_store_unavailable, sessions are in-memory "
                           "only: %s", exc)
            self._coll = None
        return self._coll

    # ── load ─────────────────────────────────────────────────────────
    def load(self, session_id: str):
        """Read one session. Called only on a cache miss, so a single
        indexed _id lookup — cheap enough to do inline."""
        coll = self._collection()
        if coll is None:
            return None
        try:
            doc = coll.find_one({"_id": session_id}, {"_id": 0, "updated_at": 0})
        except Exception as exc:  # noqa: BLE001
            logger.warning("session_load_failed id=%s: %s", session_id, exc)
            return None
        if not doc:
            return None
        try:
            mem = SessionMemory(
                current_patient_id=doc.get("current_patient_id"),
                current_hadm_id=doc.get("current_hadm_id"),
                current_patient_name=doc.get("current_patient_name"),
                patient_data_cache=doc.get("patient_data_cache") or {},
                conversation_topics=doc.get("conversation_topics") or [],
                alert_watchlist=doc.get("alert_watchlist") or [],
                pending_action=doc.get("pending_action"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("session_decode_failed id=%s: %s", session_id, exc)
            return None
        logger.info("session_restored id=%s patient=%s hadm=%s",
                    session_id, mem.current_patient_id, mem.current_hadm_id)
        return mem

    # ── save ─────────────────────────────────────────────────────────
    def _to_doc(self, memory) -> dict:
        cache = memory.patient_data_cache or {}
        pid = str(memory.current_patient_id) if memory.current_patient_id else None
        trimmed = {}
        if pid and pid in cache:
            entry = cache[pid]
            try:
                if len(json.dumps(entry, default=str)) <= SESSION_CACHE_MAX_BYTES:
                    trimmed = {pid: entry}
            except (TypeError, ValueError):
                trimmed = {}
        return {
            "current_patient_id": memory.current_patient_id,
            "current_hadm_id": memory.current_hadm_id,
            "current_patient_name": memory.current_patient_name,
            "patient_data_cache": trimmed,
            "conversation_topics": (memory.conversation_topics or [])[-20:],
            "alert_watchlist": (memory.alert_watchlist or [])[-20:],
            "pending_action": memory.pending_action,
            "updated_at": datetime.now(timezone.utc),
        }

    def save(self, session_id: str, memory) -> bool:
        coll = self._collection()
        if coll is None:
            return False
        try:
            coll.replace_one({"_id": session_id},
                             {"_id": session_id, **self._to_doc(memory)},
                             upsert=True)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("session_save_failed id=%s: %s", session_id, exc)
            return False

    def delete(self, session_id: str) -> bool:
        coll = self._collection()
        if coll is None:
            return False
        try:
            coll.delete_one({"_id": session_id})
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("session_delete_failed id=%s: %s", session_id, exc)
            return False


@dataclass
class SessionMemory:
    """Persists patient context across messages within a session."""
    current_patient_id: int | None = None
    # Stored as a STRING. Simulated admissions carry ids like
    # "SIM-27705504-1791905712"; this field used to be an int and every
    # assignment went through int(), which threw ValueError into a bare
    # `except: pass`. The result was that a simulated admission could never
    # populate it, so "what are his vital signs" always stalled asking the
    # user for an admission id the system already knew. Every consumer
    # already stringifies this value.
    current_hadm_id: str | None = None
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
            "trolley": os.environ.get("TROLLEY_WATCH_URL", "http://localhost:8216"),
            "sim": os.environ.get("DATA_INGESTION_URL", "http://localhost:8207"),
            "beds": os.environ.get("BED_MANAGEMENT_URL", "http://localhost:8208"),
            "ed_flow": os.environ.get("ED_FLOW_URL", "http://localhost:8214"),
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
        # Session memory store: session_id -> SessionMemory. Backed by Mongo
        # so patient context survives a restart; this dict is the hot cache.
        self.sessions: dict[str, SessionMemory] = {}
        self.session_store = SessionStore()

        # Live endpoint catalogue. Curated intents above still win — they
        # pre-render their data and are more reliable — but anything they
        # don't cover can now be reached generically, and the catalogue
        # re-discovers itself on a timer so new routes appear without a
        # code change here.
        self.catalog = ServiceCatalog()

    def _get_session(self, session_id: str) -> SessionMemory:
        """Get, restore, or create the memory for a session.

        A miss falls through to Mongo before creating a blank one, so a
        conversation picks up its patient context after a restart instead of
        asking the clinician to identify the patient again. The read is a
        single indexed _id lookup and happens once per session, so it is done
        inline; saves are the frequent operation and those go to a thread.
        """
        if session_id not in self.sessions:
            restored = self.session_store.load(session_id)
            self.sessions[session_id] = restored or SessionMemory()
        return self.sessions[session_id]

    async def save_session(self, session_id: str) -> bool:
        """Persist a session. Called at the end of a turn.

        PyMongo is synchronous; writing inline would stall the event loop for
        every other request in flight — the same starvation that wedged the
        simulation engine earlier in this project.
        """
        memory = self.sessions.get(session_id)
        if memory is None:
            return False
        try:
            return await asyncio.to_thread(
                self.session_store.save, session_id, memory)
        except Exception as exc:  # noqa: BLE001
            logger.warning("session_save_dispatch_failed id=%s: %s", session_id, exc)
            return False

    def clear_patient(self, session_id: str) -> dict:
        """Forget only the active patient for this session.

        Zeros out ``current_patient_id`` / ``current_hadm_id`` /
        ``current_patient_name`` / ``patient_data_cache`` / ``pending_action``
        but preserves ``conversation_topics`` and the ConversationBuffer's
        chat history. Returned payload reports what was cleared so the UI
        can show a toast.
        """
        # Restore before clearing. Reading self.sessions directly reported
        # "no_session" after a restart while Mongo still held the patient, so
        # "forget this patient" left the record in place and the next message
        # picked the patient straight back up.
        memory = self._get_session(session_id)
        had_patient = memory.current_patient_id is not None
        had_cache = bool(memory.patient_data_cache)
        memory.current_patient_id = None
        memory.current_hadm_id = None
        memory.current_patient_name = None
        memory.patient_data_cache = {}
        memory.pending_action = None
        # Persist immediately — a clear that survives only until the next
        # restart is not a clear. Synchronous because this is rare and
        # user-initiated, and the caller must know it actually happened.
        persisted = self.session_store.save(session_id, memory)
        return {
            "cleared": True,
            "had_patient": had_patient,
            "had_cache": had_cache,
            "persisted": persisted,
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

        # ── Catalogue fallback ────────────────────────────────────────
        # Curated intents cover the common ground and pre-render their data.
        # Anything they miss is attempted against the live endpoint catalogue
        # so the whole estate is reachable, not just the hand-wired services.
        # A knowledge question needs a much stronger match before it is
        # diverted to an API call.
        if not all_data:
            try:
                cat_data, cat_err = await self._fetch_via_catalog(
                    message, params, min_score=6 if intent in NO_PARAMS_INTENTS else 1,
                )
                if cat_data:
                    all_data.update(cat_data)
                elif cat_err:
                    all_errors.append(cat_err)
            except Exception as exc:  # noqa: BLE001
                logger.warning("catalog_fallback_failed: %s", exc)

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
            summary = _unwrap(data.get("summary", {})) or {}
            admissions = summary.get("admissions", [])
            for adm in admissions:
                icu_stays = adm.get("icu_stays", [])
                if icu_stays:
                    # Use the first admission with ICU stays
                    hadm_id = adm.get("hadm_id")
                    if hadm_id and params.get("patient_id"):
                        params["hadm_id"] = str(hadm_id)
                        memory.current_hadm_id = str(hadm_id).strip()
                        return "vitals"
            # Even without ICU stays, carry the current admission for context
            if admissions and not params.get("hadm_id"):
                chosen = _pick_admission(admissions) or {}
                first_hadm = chosen.get("hadm_id")
                if first_hadm:
                    params["hadm_id"] = str(first_hadm)
                    memory.current_hadm_id = str(first_hadm).strip()

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
                memory.current_hadm_id = str(hadm).strip()
            except (ValueError, TypeError):
                pass

        if intent == "patient_lookup":
            summary = _unwrap(data.get("summary", {})) or {}
            # Try to extract patient name
            patient_name = summary.get("patient_name") or summary.get("name")
            if patient_name:
                memory.current_patient_name = patient_name

            # Cache patient data
            if pid:
                memory.patient_data_cache[str(pid)] = summary

            # Auto-pick the current admission if not already set
            admissions = summary.get("admissions", [])
            if admissions and not memory.current_hadm_id:
                first_hadm = (_pick_admission(admissions) or {}).get("hadm_id")
                if first_hadm:
                    try:
                        memory.current_hadm_id = str(first_hadm).strip()
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
            elif intent == "trolley_watch":
                return await self._fetch_trolley_watch()
            elif intent == "hospital_status":
                return await self._fetch_hospital_status()
            elif intent == "sofa":
                # With a patient, look that patient up. Without one, the
                # question is almost always comparative ("who has the highest
                # SOFA in ICU"), which needs a scored cohort — previously this
                # fetched nothing and the model refused.
                if params.get("patient_id"):
                    return await self._fetch_patient_lookup(params)
                if _wants_cohort_ranking(params):
                    return await self._fetch_sofa_cohort(params.get("department"))
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

    async def _live_admission(self, client, pid) -> dict | None:
        """The simulator's current admission for a patient, if any.

        The journey summary reports the MIMIC admission id (27705504); the
        simulator replays it under its own string id
        ("SIM-27705504-1792172734") and stores live observations against
        that. A patient can hold more than one admission marked "admitted",
        so the most recent by sim_admittime wins.
        """
        try:
            body = (await client.get(
                f"{self.api_endpoints['sim']}/active-patients?limit=500"
            )).json()
        except Exception:  # noqa: BLE001
            return None
        # /active-patients answers {"count", "patients"} with no envelope,
        # unlike most services here. Accept the bare list, the enveloped
        # list, and the {"patients": [...]} form rather than assuming one.
        rows = body
        if isinstance(rows, dict):
            rows = rows.get("patients") or rows.get("data") or rows
        if isinstance(rows, dict):
            rows = rows.get("patients") or rows
        if not isinstance(rows, list):
            return None
        mine = [r for r in rows if str(r.get("subject_id")) == str(pid)]
        if not mine:
            return None
        return max(mine, key=lambda r: str(r.get("sim_admittime") or ""))

    async def _fetch_vitals(self, params: dict) -> tuple[dict | None, str | None]:
        """Current observations for a patient.

        Prefers the LIVE simulated values. The journey service's vitals route
        reads historical MIMIC chartevents joined through ICU stay_ids, so it
        returns {} for any admission without an ICU stay and cannot accept a
        simulated admission id at all (the path parameter is typed int). For a
        patient who is currently admitted, the answer to "what are his vitals"
        is the simulator's live observation set, not an empty historical one.
        """
        pid = params.get("patient_id")
        hadm = params.get("hadm_id")
        if not pid:
            return None, "A patient ID is required for a vitals lookup."

        async with httpx.AsyncClient(timeout=20.0, verify=False, trust_env=False) as client:
            live = await self._live_admission(client, pid)
            if live:
                sim_hadm = live.get("hadm_id")
                try:
                    twin = (await client.get(
                        f"{self.api_endpoints['sim']}/digital-twin/patient/{sim_hadm}"
                    )).json()
                    ctx = (twin.get("data") or {}).get("context") or {}
                except Exception:  # noqa: BLE001
                    ctx = {}
                vitals = ctx.get("vitals") or {}
                if vitals:
                    labels = {
                        "heart_rate": ("Heart rate", "bpm"),
                        "sbp": ("Systolic BP", "mmHg"),
                        "dbp": ("Diastolic BP", "mmHg"),
                        "respiratory_rate": ("Respiratory rate", "breaths/min"),
                        "spo2": ("SpO2", "%"),
                        "temperature": ("Temperature", "degC"),
                    }
                    lines = [
                        f"CURRENT OBSERVATIONS for patient {pid} "
                        f"(live, admission {sim_hadm}, currently in "
                        f"{ctx.get('current_department') or ctx.get('department') or 'unknown'}):",
                    ]
                    for key, (label, unit) in labels.items():
                        if vitals.get(key) is not None:
                            lines.append(f"- {label}: {vitals[key]} {unit}")
                    for key, val in vitals.items():
                        if key not in labels and val is not None:
                            lines.append(f"- {key}: {val}")
                    lines.append(
                        "These are the patient's current observations. Report them "
                        "exactly; do not convert units or add values not listed."
                    )
                    return {"vitals_report": "\n".join(lines)}, None

            # Not currently admitted (or the twin had nothing) — fall back to
            # the historical record, which needs the MIMIC admission id.
            if not hadm:
                return None, (
                    f"Patient {pid} is not currently admitted and no admission ID was "
                    "given, so there are no observations to report."
                )
            journey = self.api_endpoints["journey"]
            resp = await client.get(f"{journey}/patient/{pid}/admission/{hadm}/vitals")
            resp.raise_for_status()
            body = _unwrap(resp.json())
            if not (body or {}).get("vitals"):
                return None, (
                    f"No recorded vital signs for patient {pid} admission {hadm}. "
                    "The historical record only holds observations for ICU stays. "
                    "Do not substitute values."
                )
            return body, None

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

    async def _fetch_via_catalog(self, message: str, params: dict,
                                 min_score: int = 0
                                 ) -> tuple[dict | None, str | None]:
        """Answer from any GET endpoint in the estate.

        Curated intents cover the common questions and pre-render their data.
        This is the long tail: the model is shown the best-matching endpoints
        from the live catalogue, picks one, and the result is fetched and
        handed back. Only endpoints present in the catalogue can be called,
        and the catalogue holds GET operations exclusively, so this cannot
        mutate anything.
        """
        if not self.catalog.endpoints:
            try:
                await self.catalog.refresh()
            except Exception:  # noqa: BLE001
                return None, "The endpoint catalogue is unavailable."

        ranked = self.catalog.search_scored(message, limit=12)
        if not ranked or ranked[0][0] < min_score:
            # Below the bar this is a knowledge question that merely shares
            # vocabulary with a service name ("what is sepsis" matches the
            # sepsis_icu routes). Answering it from an endpoint would be worse
            # than answering it from clinical knowledge.
            return None, None
        candidates = [ep for _, ep in ranked]

        listing = "\n".join(f"{i}. {ep.signature()}" for i, ep in enumerate(candidates, 1))
        known = {k: v for k, v in params.items() if v not in (None, "")}
        pick_prompt = (
            "You are selecting ONE read-only API endpoint to answer a question about a "
            "hospital system.\n\n"
            f"Question: {message}\n\n"
            f"Known values you may use for parameters: {json.dumps(known, default=str)}\n\n"
            f"Endpoints:\n{listing}\n\n"
            "Reply with ONLY a JSON object, no prose, no markdown fence:\n"
            '{"choice": <number>, "path_params": {}, "query_params": {}}\n'
            "Use choice 0 if none of them can answer the question. Only supply a "
            "parameter if you know its value from the question or the known values — "
            "never invent an identifier."
        )
        # Endpoint selection is a classification task, not a reasoning one.
        # The default response model emits chain-of-thought, which buries the
        # JSON; the fast model answers directly.
        prev_model = self.model
        self.model = self.MODEL_ROUTING.get("intent_detection", self.model)
        try:
            raw = await self._call_ollama([{"role": "user", "content": pick_prompt}])
        finally:
            self.model = prev_model
        if not raw:
            return None, None
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return None, None
        try:
            plan = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None, None

        try:
            idx = int(plan.get("choice", 0))
        except (TypeError, ValueError):
            return None, None
        if idx < 1 or idx > len(candidates):
            return None, None
        ep = candidates[idx - 1]

        # Fill the path template. A missing path parameter is fatal — guessing
        # an identifier would fabricate a patient.
        supplied = {str(k): v for k, v in (plan.get("path_params") or {}).items()}
        for name, val in list(known.items()):
            supplied.setdefault(name, val)
        url_path = ep.path
        for pp in ep.path_params:
            val = supplied.get(pp["name"])
            if val in (None, ""):
                return None, (
                    f"Answering that needs a {pp['name']} and none was given. "
                    "Ask the user for it rather than guessing."
                )
            url_path = url_path.replace("{" + pp["name"] + "}", str(val))

        allowed = {q["name"] for q in ep.query_params}
        query = {k: v for k, v in (plan.get("query_params") or {}).items()
                 if k in allowed and v not in (None, "")}

        try:
            async with httpx.AsyncClient(timeout=25.0, verify=False, trust_env=False) as client:
                resp = await client.get(f"{ep.base_url}{url_path}", params=query)
            if resp.status_code >= 400:
                return None, (
                    f"{ep.key} returned HTTP {resp.status_code}. Report that the data "
                    "could not be retrieved; do not substitute figures."
                )
            payload = _unwrap(resp.json())
        except Exception as exc:  # noqa: BLE001
            return None, f"{ep.key} could not be reached ({type(exc).__name__})."

        rendered = json.dumps(payload, indent=2, default=str)
        truncated = len(rendered) > 6000
        if truncated:
            rendered = rendered[:6000] + "\n… (truncated)"
        report = (
            f"Result of {ep.key}"
            + (f" with {json.dumps(query)}" if query else "")
            + (f" — {ep.summary}" if ep.summary else "")
            + ".\n\n" + rendered
            + ("\n\nNOTE: this response was truncated; say so if you summarise it."
               if truncated else "")
            + "\n\nAnswer the question from these values only. Do not add figures that "
              "are not present here."
        )
        return {"api_result_report": report}, None

    async def _fetch_sofa_cohort(self, department: str | None = None
                                 ) -> tuple[dict | None, str | None]:
        """Score every currently-admitted patient and rank them by SOFA.

        There is no SOFA endpoint anywhere in the estate — the score is
        computed here from each patient's live observations and labs using
        the same shared.clinical.risk implementation the rest of the platform
        uses, so chat cannot drift from the other consumers.
        """
        from shared.clinical.risk import compute_sofa

        sim = self.api_endpoints["sim"]
        async with httpx.AsyncClient(timeout=30.0, verify=False, trust_env=False) as client:
            try:
                body = (await client.get(f"{sim}/active-patients?limit=500")).json()
            except Exception as exc:  # noqa: BLE001
                return None, f"Could not read the active patient list ({type(exc).__name__})."
            rows = body
            if isinstance(rows, dict):
                rows = rows.get("patients") or rows.get("data") or []
            if not isinstance(rows, list) or not rows:
                return None, "No patients are currently admitted, so there is nothing to rank."

            sem = asyncio.Semaphore(8)

            async def score(row):
                async with sem:
                    try:
                        twin = (await client.get(
                            f"{sim}/digital-twin/patient/{row.get('hadm_id')}")).json()
                        ctx = (twin.get("data") or {}).get("context") or {}
                    except Exception:  # noqa: BLE001
                        return None
                vitals = dict(ctx.get("vitals") or {})
                labs = ctx.get("labs") or {}
                if not vitals and not labs:
                    return None
                # SOFA's cardiovascular component needs mean arterial pressure;
                # the twin publishes systolic and diastolic. MAP = (SBP+2*DBP)/3
                # is the standard derivation, not an invented value.
                if vitals.get("mbp") is None and vitals.get("sbp") and vitals.get("dbp"):
                    try:
                        vitals["mbp"] = round(
                            (float(vitals["sbp"]) + 2 * float(vitals["dbp"])) / 3, 1)
                    except (TypeError, ValueError):
                        pass
                parts = compute_sofa(vitals, labs)
                return {
                    "subject_id": ctx.get("subject_id") or row.get("subject_id"),
                    "hadm_id": row.get("hadm_id"),
                    "department": ctx.get("current_department") or ctx.get("department"),
                    "parts": parts,
                    "total": parts.get("total", 0),
                }

            scored = [r for r in await asyncio.gather(*[score(r) for r in rows]) if r]

        if not scored:
            return None, "No live observations are available, so SOFA cannot be computed."

        pool = scored
        scope = "the hospital"
        if department:
            pool = [r for r in scored if str(r.get("department", "")).upper()
                    == department.upper()]
            scope = department
            if not pool:
                return None, (
                    f"No patients are currently in {department}, so there is no SOFA "
                    f"ranking for it. Do not report a patient from another department."
                )
        pool.sort(key=lambda r: -r["total"])
        top = pool[0]

        lines = [
            f"SOFA SCORES for every patient currently in {scope} "
            f"({len(pool)} patient(s)), computed from live observations and labs.",
            "",
            f"HIGHEST: patient {top['subject_id']} in {top['department']} with a total "
            f"SOFA of {top['total']}.",
            "",
            "Full ranking, worst first:",
        ]
        for rank, r in enumerate(pool[:10], 1):
            p = r["parts"]
            lines.append(
                f"{rank}. Patient {r['subject_id']} ({r['department']}): total {r['total']} "
                f"= respiration {p['respiration']}, coagulation {p['coagulation']}, "
                f"liver {p['liver']}, cardiovascular {p['cardiovascular']}, "
                f"renal {p['renal']}."
            )
        lines += [
            "",
            "SOFA components are 0-4 each; the total runs 0-24 and a higher score means "
            "more organ dysfunction. This build scores five systems (respiration, "
            "coagulation, liver, cardiovascular, renal) — the neurological component "
            "needs a GCS, which is not recorded here, so totals are conservative and "
            "you must say so. Report the ranking exactly as listed; do not recompute.",
        ]
        return {"sofa_cohort_report": "\n".join(lines)}, None

    async def _fetch_hospital_status(self) -> tuple[dict | None, str | None]:
        """Live census of THIS hospital — ED, ICU, wards, beds, crowding.

        Answers "how many patients are in ED", which previously routed to the
        oncology cohort endpoint and 404'd. Three sources are queried and
        cross-checked rather than trusted individually: the simulation census
        (:8207), the bed register (:8208) and the ED board (:8214). They are
        independent, so a disagreement is worth surfacing rather than hiding
        behind whichever one answered first.

        Pre-rendered as sentences for the same reason as the trolley report —
        the response model misreads nested JSON.
        """
        base_sim, base_beds, base_ed = (
            self.api_endpoints["sim"],
            self.api_endpoints["beds"],
            self.api_endpoints["ed_flow"],
        )
        stats: dict = {}
        beds: list = []
        ed: dict = {}
        failures: list[str] = []
        async with httpx.AsyncClient(timeout=20.0, verify=False, trust_env=False) as client:
            for label, coro in (
                ("census", client.get(f"{base_sim}/stats-dashboard")),
                ("bed register", client.get(f"{base_beds}/beds/summary")),
                ("ED board", client.get(f"{base_ed}/ed-state")),
            ):
                try:
                    body = (await coro).json()
                    payload = body.get("data", body)
                    if label == "census":
                        stats = payload or {}
                    elif label == "bed register":
                        beds = payload or []
                    else:
                        ed = payload or {}
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"{label} unavailable ({type(exc).__name__})")

        if not stats and not beds and not ed:
            return None, (
                "Live hospital census could not be retrieved from any source "
                f"({'; '.join(failures)}). Do not estimate patient numbers."
            )

        def g(d, k, default=None):
            v = (d or {}).get(k)
            return default if v is None else v

        ed_bed = next((b for b in beds if str(b.get("department")).upper() == "ED"), {})
        ed_counts = {
            "simulation census": g(stats, "ed_count"),
            "bed register": ed_bed.get("occupied"),
            "ED board": g(ed, "total_patients"),
        }
        reported = [v for v in ed_counts.values() if v is not None]
        agree = len(set(reported)) <= 1

        total_cap = sum((b.get("capacity") or 0) for b in beds)
        total_occ = sum((b.get("occupied") or 0) for b in beds)
        total_free = sum((b.get("available") or 0) for b in beds)

        lines = [
            f"LIVE HOSPITAL CENSUS (this hospital's own simulation, NOT the national "
            f"trolley figures). Simulation time: {g(stats, 'sim_time', 'unknown')}.",
            "",
        ]
        if reported:
            lines.append(
                f"PATIENTS CURRENTLY IN ED: {reported[0]}."
                + ("" if agree else
                   "  WARNING — sources disagree: "
                   + ", ".join(f"{k} says {v}" for k, v in ed_counts.items() if v is not None)
                   + ". Report this disagreement; do not pick one silently.")
            )
        if ed_bed:
            lines.append(
                f"ED beds: {ed_bed.get('occupied')} occupied of {ed_bed.get('capacity')} "
                f"({ed_bed.get('available')} available); alert level "
                f"{ed_bed.get('alert_level')}."
            )
        if ed:
            lines.append(
                f"ED board detail: {g(ed, 'waiting_count', 0)} waiting, "
                f"{g(ed, 'in_treatment_count', 0)} in treatment, "
                f"{g(ed, 'boarding_count', 0)} boarding; resus "
                f"{g(ed, 'resus_occupied', 0)}/{g(ed, 'resus_capacity', 0)}. "
                f"Average wait {g(ed, 'avg_wait_minutes', 0)} min, longest "
                f"{g(ed, 'longest_wait_minutes', 0)} min. NEDOCS "
                f"{g(ed, 'nedocs_score', 0)} ({g(ed, 'crowding_level', 'unknown')})."
            )
        lines += [
            "",
            # icu_count is deliberately NOT quoted here. It is a separate field
            # from the same service as department_distribution and the two
            # disagree (9 vs 7), which produced a report saying "9 in ICU" three
            # lines above a table saying ICU holds 7. The department table below
            # is the single source for per-department numbers.
            f"WHOLE HOSPITAL: {g(stats, 'total_active', 'unknown')} patients currently "
            f"admitted. {g(stats, 'total_discharged', 'unknown')} discharged to date. "
            f"Per-department numbers are in the table below — use only those.",
            # Pre-computed because the model otherwise sums the per-department
            # capacities itself and gets it wrong — it reported "5 beds out of
            # 1,682" for a hospital with 458 beds and 34 patients.
            (f"HOSPITAL-WIDE BEDS: {total_occ} occupied of {total_cap} "
             f"({total_free} free), across {len(beds)} departments. "
             f"Occupancy {round(100 * total_occ / total_cap, 1)}%."
             if total_cap else "HOSPITAL-WIDE BEDS: bed register unavailable."),
        ]
        # ONE reconciled per-department table, not two independent ones. The
        # simulation census and the bed register are separate services sampled
        # microseconds apart, so a patient mid-transfer shows in one and not
        # the other. Printing both as separate lists produced answers that
        # contradicted themselves inside a single paragraph ("HDU (4)" then
        # "HDU 2/8 occupied"). Where they differ, the difference is stated.
        dept = g(stats, "department_distribution") or {}
        bed_by_dept = {str(b.get("department")): b for b in beds}
        all_depts = sorted(set(dept) | set(bed_by_dept))
        if all_depts:
            lines.append("")
            lines.append("BY DEPARTMENT (patients — bed occupancy — free beds):")
            disagreements = []
            for name in all_depts:
                census_n = dept.get(name)
                bed = bed_by_dept.get(name, {})
                occ, cap = bed.get("occupied"), bed.get("capacity")
                avail = bed.get("available")
                if census_n is not None and occ is not None and census_n != occ:
                    disagreements.append(f"{name} ({census_n} vs {occ})")
                    count_txt = (
                        f"{min(census_n, occ)}-{max(census_n, occ)} patients "
                        f"(census says {census_n}, bed register says {occ})"
                    )
                else:
                    shown = census_n if census_n is not None else occ
                    count_txt = (
                        "unknown patient count" if shown is None
                        else f"{shown} patient{'' if shown == 1 else 's'}"
                    )
                if cap is not None:
                    lines.append(
                        f"- {name}: {count_txt}; {occ} of {cap} beds occupied, "
                        f"{avail} free."
                    )
                else:
                    lines.append(f"- {name}: {count_txt}.")
            if disagreements:
                lines.append(
                    "Note: the simulation census and the bed register disagree slightly "
                    f"for {', '.join(disagreements)}. These are independent services "
                    "sampled a moment apart, so a patient mid-transfer appears in one "
                    "and not the other. Give the range and say the two systems differ "
                    "by one or two — do not silently pick a single figure."
                )

        if failures:
            lines.append("")
            lines.append("Note: " + "; ".join(failures) + ".")
        lines += [
            "",
            "Report these figures exactly as given. A count of 0 means that department "
            "is genuinely empty right now — say so plainly; do not treat 0 as missing "
            "data and do not substitute a number of your own. Only call a department "
            "empty if its count is exactly 0; a department holding even one patient is "
            "occupied. Write numbers as digits, not words. Do NOT add up the "
            "per-department figures yourself — every total you need is already given "
            "above; a computed total will be wrong.",
        ]
        return {"hospital_census_report": "\n".join(lines)}, None

    async def _fetch_trolley_watch(self) -> tuple[dict | None, str | None]:
        """Live HSE TrolleyGAR figures — national, all six zones, worst sites.

        Returns ONE pre-rendered table rather than nested JSON. The 8B
        response model reliably garbles multi-level JSON: handed the zone
        objects it reported West & North West as 147 trolleys (actually 21) and
        labelled a zone subtotal row as a hospital. Every number here is
        already computed and laid out, so the model's only job is to quote
        it — there is no arithmetic left to get wrong.
        """
        base = self.api_endpoints["trolley"]
        async with httpx.AsyncClient(timeout=20.0, verify=False, trust_env=False) as client:
            latest = (await client.get(f"{base}/trolley/hse/latest")).json().get("data", {})
            if not latest.get("available"):
                return None, (
                    "No reconciled HSE TrolleyGAR report is available yet. "
                    "Do not estimate or invent trolley figures."
                )
            zones = (await client.get(f"{base}/trolley/hse/zones")).json().get("data", {})

        nat = latest.get("national") or {}
        zone_rows = zones.get("zones", []) if zones.get("available") else []

        def n(v):
            return "n/a" if v is None else str(v)

        # Rows are written as sentences, not a fixed-width table. The response
        # model misaligns columns — given a padded table it read the ED figure
        # as the zone total and dropped the ward count. It also rewrote the
        # report year 2026 as 2025 and then declared its own live data stale,
        # so today's date and the report's age are stated outright.
        today = datetime.now(timezone.utc).date()
        rep_date = str(latest.get("report_date") or "")
        try:
            age = (today - datetime.strptime(rep_date, "%Y-%m-%d").date()).days
        except ValueError:
            age = None
        if age == 0:
            freshness = f"This IS today's report ({rep_date}) — it is current, not historical."
        elif age is not None:
            freshness = (
                f"This report is dated {rep_date}, {age} day(s) before today. It is the "
                f"most recent one the HSE has published."
            )
        else:
            freshness = f"Report date {rep_date}."

        lines = [
            f"Today's date is {today.isoformat()}. The current year is {today.year}. "
            f"Write every date in exactly the YYYY-MM-DD form given below — do not "
            f"reformat it into words or another order, and do not change the year. "
            f"(A previous answer turned {today.isoformat()} into '01 July 2026'.)",
            f"HSE TrolleyGAR / INMO Trolley Watch, 08:00 count, "
            f"{latest.get('hospital_count')} hospitals reporting. {freshness}",
            "",
            f"NATIONAL TOTAL: {n(nat.get('total_trolleys'))} patients waiting on trolleys "
            f"= {n(nat.get('ed_trolleys'))} in EDs + {n(nat.get('ward_trolleys'))} on wards. "
            f"Surge capacity: {n(nat.get('surge_capacity'))}. "
            f"Delayed transfers of care: {n(nat.get('delayed_transfers'))}.",
            "",
            "BY HEALTH REGION, worst first (these six totals sum to the national "
            "total). The list is already ranked — the first row is the highest:",
        ]
        ranked = sorted(zone_rows, key=lambda r: -(r.get("total_trolleys") or 0))
        for z in ranked:
            lines.append(
                f"- {z.get('zone')}: {n(z.get('total_trolleys'))} total trolleys "
                f"= {n(z.get('ed_trolleys'))} ED + {n(z.get('ward_trolleys'))} ward; "
                f"surge capacity {n(z.get('surge_capacity'))}; "
                f"delayed transfers {n(z.get('delayed_transfers'))}."
            )

        if ranked:
            # Stated outright because the model otherwise narrates its own
            # ranking wrongly — it wrote "Mid West highest (36), followed by
            # Dublin and South East (44)" while displaying a correct table.
            hi, lo = ranked[0], ranked[-1]
            lines.append(
                f"Worst-affected region: {hi.get('zone')} with "
                f"{n(hi.get('total_trolleys'))}. Least-affected: {lo.get('zone')} with "
                f"{n(lo.get('total_trolleys'))}."
            )

        hospitals = [h for z in zone_rows for h in (z.get("hospitals") or [])]
        sites_affected = len([h for h in hospitals if (h.get("total_trolleys") or 0) > 0])
        worst = sorted(
            (h for h in hospitals if (h.get("total_trolleys") or 0) > 0),
            key=lambda h: -(h.get("total_trolleys") or 0),
        )[:10]
        if worst:
            lines += ["",
                      f"WORST-AFFECTED HOSPITALS — top {len(worst)} individual sites "
                      f"(not regions); {sites_affected} sites reported trolleys in total:"]
            for rank, h in enumerate(worst, 1):
                lines.append(
                    f"{rank}. {h.get('hospital')} ({h.get('zone')}): "
                    f"{n(h.get('total_trolleys'))} total "
                    f"= {n(h.get('ed_trolleys'))} ED + {n(h.get('ward_trolleys'))} ward."
                )
        lines += [
            "",
            "Source: HSE Special Delivery Unit TrolleyGAR, published daily.",
            "Reproduce these figures exactly as given; do not recompute, reorder or "
            "adjust them, and if you list hospitals keep them in rank order. When you "
            "name a hospital or region, give its ED and ward numbers separately as "
            "written above — never compress a site to 'all ED' or 'all ward' shorthand, "
            "which has produced wrong figures (UH Limerick is 6 ED + 30 ward, not "
            "'36, all ED'). Report surge capacity as a plain count — do not describe "
            "those beds as 'available' or 'free'; the report does not say whether they "
            "are occupied.",
        ]

        return {"hse_trolley_report": "\n".join(lines)}, None

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
            data_section = f"\n\nHospital system data:\n{_render_data(api_data)}"
        if api_error:
            data_section += f"\n\nNote: {api_error}"
        if not api_data and intent not in NO_PARAMS_INTENTS:
            data_section += NO_LIVE_DATA_INSTRUCTION

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
                            # num_predict caps CoT *and* answer together. At 512
                            # a reasoning model (deepseek-r1) spends nearly all
                            # of it thinking and the reply dies mid-sentence —
                            # the "answers stuck in between" symptom. Budget for
                            # both: the CoT is streamed separately as `thinking`
                            # so a longer cap costs the reader nothing.
                            "num_predict": int(os.environ.get("CHAT_NUM_PREDICT", "2048")),
                        },
                    },
                ) as resp:
                    resp.raise_for_status()
                    answer_chars = 0
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
                            answer_chars += len(content)
                            yield ("content", content)
                        if obj.get("done"):
                            # Never end on a silent stump. If the model hit the
                            # token ceiling, say so rather than leaving a
                            # sentence hanging — a truncated clinical answer
                            # reads as a complete one.
                            if obj.get("done_reason") == "length":
                                if answer_chars == 0:
                                    yield ("content",
                                           "I ran out of response budget while reasoning and "
                                           "could not produce an answer. Please ask again, or "
                                           "narrow the question.")
                                else:
                                    yield ("content",
                                           "\n\n_[response truncated at the token limit]_")
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

        # ── Catalogue fallback ────────────────────────────────────────
        # Curated intents cover the common ground and pre-render their data.
        # Anything they miss is attempted against the live endpoint catalogue
        # so the whole estate is reachable, not just the hand-wired services.
        # A knowledge question needs a much stronger match before it is
        # diverted to an API call.
        if not all_data:
            try:
                cat_data, cat_err = await self._fetch_via_catalog(
                    message, params, min_score=6 if intent in NO_PARAMS_INTENTS else 1,
                )
                if cat_data:
                    all_data.update(cat_data)
                elif cat_err:
                    all_errors.append(cat_err)
            except Exception as exc:  # noqa: BLE001
                logger.warning("catalog_fallback_failed: %s", exc)

        api_error = "; ".join(all_errors) if all_errors else None

        if all_data:
            thinking.append(f"Step 4: Received data ({_data_summary(all_data)}). Generating clinical summary…")
        else:
            thinking.append("Step 4: No structured data. Generating response from clinical knowledge…")
            if intent not in NO_PARAMS_INTENTS:
                # A data intent that returned nothing must not be answered from
                # the model's imagination. Asked for today's INMO snapshot with
                # no data, it previously produced "42 new patients admitted
                # today" — a fabricated figure in a clinical tool.
                thinking.append(
                    "Step 4b: Data request with no data — answering without invented figures."
                )
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
        data_section = f"\n\nHospital system data:\n{_render_data(all_data)}" if all_data else ""
        if api_error:
            data_section += f"\n\nNote: {api_error}"
        if not all_data and intent not in NO_PARAMS_INTENTS:
            data_section += NO_LIVE_DATA_INSTRUCTION
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
