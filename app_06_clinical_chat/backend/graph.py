"""LangGraph orchestration for clinical chat.

Why this exists
---------------
The original pipeline was a straight line: classify the question with one
regex pass, call exactly one data fetcher, hand the result to the model,
return whatever it said. Three weaknesses followed from that shape, and all
three had already produced wrong answers in production:

1. **One question, one source.** "How does ED crowding compare with the
   national trolley figure?" needs two fetches. The old chain could only
   express a fixed hand-coded hop (patient_lookup -> vitals), so anything
   else returned half an answer.

2. **Reasoning was invisible and unchecked.** Chain-of-thought lived inside
   the model's own monologue. When it decided INMO meant "Indian National
   Medical Organisation" nothing could catch it, because nothing else could
   see the reasoning. Here the plan is a first-class object: it is produced,
   logged, executed and can be revised.

3. **No verification.** Fabricated figures were fought with ever-longer
   prompt instructions ("do not invent", "do not recompute", "do not
   reformat the year"). That is a losing game against a 8B model. A verify
   node instead checks the numbers in the answer against the numbers in the
   retrieved data and sends the answer back for revision when they do not
   match — a check, not a plea.

Shape of the graph
------------------
    classify -> plan -> retrieve -> assess -.-> synthesize -> verify -.-> END
                  ^                          |                        |
                  '------- (re-plan) --------'      (revise) ---------'

Both loops are bounded (PLAN_ROUNDS, REVISE_ROUNDS) so a confused model
cannot spin. Every node is pure-ish: it takes state and returns a partial
state update, which is what makes the whole thing inspectable.

The curated fetchers from ClinicalChatEngine are reused as the tool surface
rather than reimplemented — they pre-render their data as flat text, which
is what stopped the response model misreading nested JSON.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Annotated, Any, Dict, List, Optional, TypedDict

logger = logging.getLogger(__name__)

KNOWLEDGE_INTENTS = {"general_clinical"}

# Deterministic floor. The planner is a 3B model and occasionally returns an
# empty or unparseable plan for a question that plainly needs data — observed
# live: "how many patients are in ED" came back with no steps, so the answer
# was written from clinical knowledge with no census at all. That is precisely
# the failure this pipeline exists to remove, so a classified data intent
# always retrieves something. The planner may add to this; it may not reduce
# it to nothing.
INTENT_FLOOR: Dict[str, str] = {
    "hospital_status": "hospital_status",
    "trolley_watch": "trolley_watch",
    "sofa": "sofa_cohort",
    "patient_lookup": "patient_lookup",
    "vitals": "vitals",
    "lab_check": "labs",
    "medication_review": "medications",
    "cohort_stats": "api_search",
    "triage": "api_search",
    "risk_assessment": "api_search",
    "pathway": "api_search",
    "note_analysis": "api_search",
}

# Markers that turn a knowledge-shaped question into a data request.
_LIVE_MARKERS = re.compile(
    r"\b(today|now|current(ly)?|latest|right\s+now|this\s+hospital|our\s+)\b"
    r"|how\s+many|\bcensus\b|trolley|\binmo\b",
    re.I,
)


def _wants_live(message: str) -> bool:
    return bool(_LIVE_MARKERS.search(message or ""))


PLAN_ROUNDS = 2       # how many times we may go back and fetch more
REVISE_ROUNDS = 1     # how many times a failed verification may rewrite


def _merge_dict(left: dict, right: dict) -> dict:
    out = dict(left or {})
    out.update(right or {})
    return out


def _extend(left: list, right: list) -> list:
    return list(left or []) + list(right or [])


class ChatState(TypedDict, total=False):
    # inputs
    message: str
    session_id: str
    history: List[dict]
    params: Dict[str, Any]
    # working
    intent: Optional[str]
    plan: List[dict]
    data: Annotated[Dict[str, Any], _merge_dict]
    errors: Annotated[List[str], _extend]
    reasoning: Annotated[List[str], _extend]
    plan_rounds: int
    revise_rounds: int
    # outputs
    answer: str
    verdict: Optional[dict]


# Tools the planner may choose. Kept deliberately small and described in the
# vocabulary a clinician would use, because the planner is a 3B model.
TOOLS: Dict[str, Dict[str, str]] = {
    "hospital_status": {
        "desc": "Live census of THIS hospital: patients per department, ED "
                "count, bed occupancy, NEDOCS crowding. Use for 'how many "
                "patients are in X', 'how busy', bed availability.",
        "args": "",
    },
    "trolley_watch": {
        "desc": "National HSE TrolleyGAR / INMO Trolley Watch figures: "
                "trolleys nationally and per health region, surge capacity, "
                "delayed transfers. This is PUBLISHED NATIONAL data, not this "
                "hospital's own census.",
        "args": "",
    },
    "sofa_cohort": {
        "desc": "SOFA organ-dysfunction score for every admitted patient, "
                "ranked. Use for 'sickest patient', 'highest SOFA', "
                "'who is most at risk'. Optional department filter.",
        "args": "department (optional, e.g. ICU)",
    },
    "patient_lookup": {
        "desc": "Demographics and admission history for one patient.",
        "args": "patient_id (required)",
        "needs": "patient_id",
    },
    "vitals": {
        "desc": "Current observations (HR, BP, RR, SpO2, temperature) for one "
                "patient.",
        "args": "patient_id (required)",
        "needs": "patient_id",
    },
    "labs": {
        "desc": "Laboratory results for one patient's admission.",
        "args": "patient_id, hadm_id",
        "needs": "patient_id",
    },
    "medications": {
        "desc": "Medications for one patient's admission.",
        "args": "patient_id, hadm_id",
        "needs": "patient_id",
    },
    "api_search": {
        "desc": "Anything else the hospital systems track — waiting lists, "
                "discharge lounge, ED flow, bed management, FHIR, ERP, "
                "deterioration alerts. Searches all 143 live endpoints. Use "
                "this when no other tool fits.",
        "args": "query (a short phrase describing what to look up)",
    },
}


class ClinicalGraph:
    """Builds and runs the graph. Holds a reference to the existing engine
    so the curated fetchers, model routing and endpoint catalogue are shared
    rather than duplicated."""

    def __init__(self, engine):
        self.engine = engine
        self._graph = None

    # ── model helpers ────────────────────────────────────────────────
    async def _ask(self, prompt: str, task: str = "intent_detection") -> str:
        """One-shot model call at a chosen tier, restoring the caller's model."""
        eng = self.engine
        prev = eng.model
        eng.model = eng.MODEL_ROUTING.get(task, eng.model)
        try:
            return await eng._call_ollama([{"role": "user", "content": prompt}]) or ""
        finally:
            eng.model = prev

    @staticmethod
    def _json_block(raw: str) -> Optional[Any]:
        """Pull the first JSON object/array out of a model reply.

        Reasoning models wrap output in <think> blocks and prose fences; the
        planner is asked for bare JSON but we cannot rely on that.
        """
        if not raw:
            return None
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.S | re.I)
        for pattern in (r"\[.*\]", r"\{.*\}"):
            m = re.search(pattern, cleaned, re.S)
            if not m:
                continue
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                continue
        return None

    # ── nodes ────────────────────────────────────────────────────────
    async def classify(self, state: ChatState) -> ChatState:
        """Regex intent detection — fast, deterministic, already well tested.

        The graph keeps it as a hint for the planner rather than as the sole
        decision, which is the change: a wrong classification is no longer
        fatal because the planner can still choose a different tool.
        """
        from app_06_clinical_chat.backend.intents import detect_intent

        det = detect_intent(state["message"])
        params = dict(state.get("params") or {})
        params.update(det.get("params") or {})
        return {
            "intent": det.get("intent"),
            "params": params,
            "reasoning": [f"Classified as '{det.get('intent')}' "
                          f"({det.get('reasoning', '')[:90]})"],
        }

    async def plan(self, state: ChatState) -> ChatState:
        """Decide which data is needed, as an explicit ordered list.

        This is the chain-of-thought made external. It is written to the
        state, streamed to the UI and logged, so a bad plan is visible
        before it becomes a bad answer.
        """
        known = {k: v for k, v in (state.get("params") or {}).items() if v not in (None, "")}

        # A pure knowledge question needs no hospital data. Asked "what is
        # sepsis" the 3B planner cheerfully scheduled sofa_cohort,
        # patient_lookup and an api_search that returned sepsis screening
        # records — three wasted calls, and irrelevant data in the prompt of
        # an answer that should come from clinical knowledge. The classifier
        # already knows; trust it rather than paying a model call to
        # re-litigate it.
        if state.get("intent") in KNOWLEDGE_INTENTS and not _wants_live(state["message"]):
            return {
                "plan": [],
                "reasoning": ["Plan: clinical-knowledge question — no hospital "
                              "data needed, skipping retrieval."],
                "plan_rounds": int(state.get("plan_rounds", 0)) + 1,
            }

        already = sorted(state.get("data", {}).keys())
        # Don't offer tools whose required identifier we don't have. Offering
        # patient_lookup with no patient_id invites a plan step that can only
        # fail, and the planner does take the bait.
        offered = {
            name: spec for name, spec in TOOLS.items()
            if not (spec.get("needs") and not known.get(spec["needs"]))
        }
        tools = "\n".join(
            f"- {name}: {spec['desc']}"
            + (f" ARGS: {spec['args']}" if spec["args"] else "")
            for name, spec in offered.items()
        )
        prompt = (
            "You plan which hospital data to fetch to answer a question. "
            "You do NOT answer it.\n\n"
            f"Question: {state['message']}\n"
            f"Rule-based intent guess: {state.get('intent')}\n"
            f"Known values: {json.dumps(known, default=str)}\n"
            + (f"Already retrieved: {already}\n" if already else "")
            + f"\nTools:\n{tools}\n\n"
            "Reply with ONLY a JSON array, at most 3 steps, no prose:\n"
            '[{"tool": "<name>", "args": {}, "why": "<short reason>"}]\n'
            "Return [] if the question is general medical knowledge needing no "
            "hospital data. Never invent a patient_id.\n"
            "If the question asks about TWO different things, emit one step for "
            "each — you may use api_search more than once with different "
            "queries."
        )
        raw = await self._ask(prompt)
        steps = self._json_block(raw)
        if not isinstance(steps, list):
            steps = []
        # Keep only steps naming a real tool — a hallucinated tool name must
        # not reach the dispatcher.
        clean = [
            s for s in steps
            if isinstance(s, dict) and s.get("tool") in offered
        ][:3]
        # Only single-shot tools are deduped against what we already hold.
        # api_search is parameterised by query, so a compound question
        # ("discharge lounge status AND waiting list size") legitimately needs
        # it more than once — deduping by tool name alone silently dropped
        # half of such a question.
        already_tools = {a.split("::", 1)[0] for a in already}
        clean = [
            s for s in clean
            if s["tool"] == "api_search" or s["tool"] not in already_tools
        ]

        floor_used = False
        if not clean:
            floor = INTENT_FLOOR.get(state.get("intent") or "")
            if floor and floor in offered and floor not in already_tools:
                args = ({"query": state["message"]} if floor == "api_search"
                        else ({"department": known["department"]}
                              if floor == "sofa_cohort" and known.get("department")
                              else {}))
                clean = [{"tool": floor, "args": args,
                          "why": f"default source for '{state.get('intent')}'"}]
                floor_used = True

        note = (
            ("Plan: " + "; ".join(f"{s['tool']} ({s.get('why', '')[:50]})" for s in clean)
             + (" [planner returned nothing; used the default source for this "
                "intent]" if floor_used else ""))
            if clean else
            "Plan: no hospital data needed — answering from clinical knowledge."
        )
        return {
            "plan": clean,
            "reasoning": [note],
            "plan_rounds": int(state.get("plan_rounds", 0)) + 1,
        }

    async def _run_step(self, step: dict, params: dict, message: str):
        """Execute one plan step. Returns (key, data, error)."""
        eng = self.engine
        tool = step["tool"]
        args = step.get("args") if isinstance(step.get("args"), dict) else {}
        key = tool
        try:
            if tool == "hospital_status":
                d, e = await eng._fetch_hospital_status()
            elif tool == "trolley_watch":
                d, e = await eng._fetch_trolley_watch()
            elif tool == "sofa_cohort":
                dept = args.get("department") or params.get("department")
                d, e = await eng._fetch_sofa_cohort(dept)
            elif tool == "patient_lookup":
                pid = args.get("patient_id") or params.get("patient_id")
                if not pid:
                    d, e = None, "A patient ID is needed and none was given."
                else:
                    d, e = await eng._fetch_patient_lookup({"patient_id": pid})
            elif tool == "vitals":
                pid = args.get("patient_id") or params.get("patient_id")
                d, e = await eng._fetch_vitals(
                    {"patient_id": pid, "hadm_id": params.get("hadm_id")})
            elif tool == "labs":
                d, e = await eng._fetch_labs({**params, **args})
            elif tool == "medications":
                d, e = await eng._fetch_medications({**params, **args})
            elif tool == "api_search":
                query = args.get("query") or message
                key = f"api_search::{query[:40]}"
                d, e = await eng._fetch_via_catalog(query, params, min_score=1)
            else:
                d, e = None, f"Unknown tool {tool}."
        except Exception as exc:  # noqa: BLE001
            logger.warning("graph_tool_failed tool=%s: %s", tool, exc)
            d, e = None, f"{tool} failed ({type(exc).__name__})."
        return key, d, e

    async def retrieve(self, state: ChatState) -> ChatState:
        """Execute the plan.

        Steps run CONCURRENTLY. They are independent lookups against
        different services, so running them in sequence just added their
        latencies together — the main cost of putting a planner in front of
        the fetchers. One failure never abandons the others: a partial answer
        with an explicit gap beats no answer.
        """
        params = dict(state.get("params") or {})
        steps = state.get("plan", [])
        if not steps:
            return {"reasoning": ["Retrieved nothing."]}

        results = await asyncio.gather(
            *[self._run_step(s, params, state["message"]) for s in steps],
            return_exceptions=True,
        )

        data: Dict[str, Any] = {}
        errors: List[str] = []
        notes: List[str] = []
        for step, res in zip(steps, results):
            if isinstance(res, BaseException):
                errors.append(f"{step['tool']} failed ({type(res).__name__}).")
                notes.append(f"{step['tool']}: crashed")
                continue
            key, d, e = res
            if d:
                data[key] = d
                notes.append(f"{step['tool']}: retrieved")
            if e:
                errors.append(e)
                notes.append(f"{step['tool']}: {e[:80]}")

        self._remember(state, data, params)

        return {
            "data": data,
            "errors": errors,
            "reasoning": [("Retrieved -> " + "; ".join(notes)) if notes
                          else "Retrieved nothing."],
        }

    def _remember(self, state: ChatState, data: dict, params: dict) -> None:
        """Write what we learned back into session memory.

        The single-shot engine did this in _update_memory_from_data, and the
        graph originally did not — so switching /chat over quietly broke the
        follow-up question. "Show me patient 15554295" then "what are his
        vitals" lost the patient between turns, because nothing recorded who
        "his" referred to. The engine's own updater is reused rather than
        reimplemented, so both pipelines learn the same things from the same
        payloads.
        """
        session_id = state.get("session_id") or "default"
        try:
            memory = self.engine._get_session(session_id)
        except Exception:  # noqa: BLE001
            return

        # Identifiers the classifier or the plan resolved.
        for key, attr in (("patient_id", "current_patient_id"),
                          ("hadm_id", "current_hadm_id")):
            val = params.get(key)
            if not val:
                continue
            if attr == "current_patient_id":
                try:
                    memory.current_patient_id = int(val)
                except (TypeError, ValueError):
                    pass
            else:
                memory.current_hadm_id = str(val).strip()

        # Anything the payloads themselves reveal — patient name, the current
        # admission picked out of the summary, and so on.
        tool_to_intent = {
            "patient_lookup": "patient_lookup", "vitals": "vitals",
            "labs": "lab_check", "medications": "medication_review",
        }
        for tool, payload in (data or {}).items():
            intent = tool_to_intent.get(tool.split("::", 1)[0])
            if not intent or not isinstance(payload, dict):
                continue
            try:
                self.engine._update_memory_from_data(intent, params, payload, memory)
            except Exception as exc:  # noqa: BLE001
                logger.debug("graph_memory_update_failed tool=%s: %s", tool, exc)

        topics = memory.conversation_topics
        if state.get("intent") and (not topics or topics[-1] != state["intent"]):
            topics.append(state["intent"])
            del topics[:-20]

    async def assess(self, state: ChatState) -> ChatState:
        """Cheap sufficiency check. Deliberately not a model call: if a plan
        ran and produced nothing, that is a fact, not a judgement."""
        got = bool(state.get("data"))
        rounds = int(state.get("plan_rounds", 0))
        if got or rounds >= PLAN_ROUNDS or not state.get("plan"):
            return {}
        return {"reasoning": [f"No data from round {rounds}; re-planning."]}

    async def synthesize(self, state: ChatState) -> ChatState:
        """Write the answer, strictly from what was retrieved."""
        eng = self.engine
        from app_06_clinical_chat.backend.chat_engine import (
            RESPONSE_SYSTEM_PROMPT, NO_LIVE_DATA_INSTRUCTION, _render_data,
        )

        data = state.get("data") or {}
        flat: Dict[str, Any] = {}
        for payload in data.values():
            if isinstance(payload, dict):
                flat.update(payload)
        section = f"\n\nHospital system data:\n{_render_data(flat)}" if flat else ""
        if state.get("errors"):
            section += "\n\nNote: " + "; ".join(state["errors"][:3])
        if not flat and state.get("plan"):
            section += NO_LIVE_DATA_INSTRUCTION
        prior = state.get("verdict") or {}
        if prior.get("problems"):
            section += (
                "\n\nYour previous answer was rejected because these figures do "
                "not appear in the data above: "
                + ", ".join(str(p) for p in prior["problems"][:8])
                + ". Rewrite using only values present in the data."
            )

        messages = [
            {"role": "system", "content": RESPONSE_SYSTEM_PROMPT},
            *[{"role": h.get("role", "user"), "content": h.get("content", "")}
              for h in (state.get("history") or [])[-4:]],
            {"role": "user",
             "content": f"User question: {state['message']}{section}\n\n"
                        "Respond naturally. Reference specific values from the data."},
        ]
        prev = eng.model
        eng.model = eng.MODEL_ROUTING.get("clinical_response", eng.model)
        try:
            answer = await eng._call_ollama(messages) or ""
        finally:
            eng.model = prev
        return {"answer": answer,
                "reasoning": [f"Drafted answer ({len(answer)} chars)."]}

    async def verify(self, state: ChatState) -> ChatState:
        """Check every number in the answer against the retrieved data.

        This replaces prompt-begging with an actual test. It is intentionally
        arithmetic-free and conservative: only numbers that appear nowhere in
        the source are flagged, so a legitimate restatement always passes and
        the loop cannot thrash.
        """
        answer = state.get("answer") or ""
        data = state.get("data") or {}

        # Nothing was retrieved, so there is nothing to check against. A
        # clinical-knowledge answer legitimately cites figures that appear in
        # no hospital record — "SOFA >= 2", "30-day mortality", "1:4 nurse
        # ratio". Checking those against an empty source flagged every one of
        # them and burned two rewrite rounds producing a worse answer each
        # time. The verifier's question is "does this match the source", which
        # is only meaningful when there is a source.
        if not data:
            return {"verdict": {"ok": True, "problems": [], "scope": "knowledge"},
                    "reasoning": ["No retrieved data — answered from clinical "
                                  "knowledge; figure verification not applicable."]}

        haystack = json.dumps(data, default=str)
        source_nums = set(re.findall(r"\d+(?:\.\d+)?", haystack))

        problems: List[str] = []
        for num in set(re.findall(r"\d+(?:\.\d+)?", answer)):
            if num in source_nums:
                continue
            # Tolerate formatting: 1,682 -> 1682, 8.0 -> 8, percentages the
            # model derived from two source numbers are NOT tolerated.
            if num.rstrip("0").rstrip(".") in {s.rstrip("0").rstrip(".") for s in source_nums}:
                continue
            if len(num) <= 1:          # list markers, "1." etc.
                continue
            problems.append(num)

        ok = not problems
        note = ("Verified: every figure appears in the retrieved data."
                if ok else
                f"Verification failed — not in source: {', '.join(sorted(problems)[:8])}")
        return {"verdict": {"ok": ok, "problems": problems},
                "reasoning": [note],
                "revise_rounds": int(state.get("revise_rounds", 0)) + (0 if ok else 1)}

    # ── edges ────────────────────────────────────────────────────────
    @staticmethod
    def _after_assess(state: ChatState) -> str:
        if state.get("data"):
            return "synthesize"
        if int(state.get("plan_rounds", 0)) >= PLAN_ROUNDS or not state.get("plan"):
            return "synthesize"     # answer honestly from knowledge / say why not
        return "plan"

    @staticmethod
    def _after_verify(state: ChatState) -> str:
        verdict = state.get("verdict") or {}
        if verdict.get("ok"):
            return "__end__"
        if int(state.get("revise_rounds", 0)) > REVISE_ROUNDS:
            # Give up rewriting rather than loop; the caller surfaces the
            # unverified figures so the reader is warned rather than misled.
            return "__end__"
        return "synthesize"

    # ── build / run ──────────────────────────────────────────────────
    def build_retrieval(self):
        """Compile just classify -> plan -> retrieve -> assess.

        Streaming needs the retrieval half on its own so it can hand the
        synthesis step to a token-streaming call instead of a node. Both
        graphs are compiled from the same node functions, so there is one
        implementation of planning and retrieval, not two.
        """
        if getattr(self, "_retrieval_graph", None) is not None:
            return self._retrieval_graph
        from langgraph.graph import StateGraph, START, END

        g = StateGraph(ChatState)
        g.add_node("classify", self.classify)
        g.add_node("plan", self.plan)
        g.add_node("retrieve", self.retrieve)
        g.add_node("assess", self.assess)
        g.add_edge(START, "classify")
        g.add_edge("classify", "plan")
        g.add_edge("plan", "retrieve")
        g.add_edge("retrieve", "assess")
        g.add_conditional_edges("assess", self._after_assess,
                                {"plan": "plan", "synthesize": END})
        self._retrieval_graph = g.compile()
        return self._retrieval_graph

    def build(self):
        if self._graph is not None:
            return self._graph
        from langgraph.graph import StateGraph, START, END

        g = StateGraph(ChatState)
        g.add_node("classify", self.classify)
        g.add_node("plan", self.plan)
        g.add_node("retrieve", self.retrieve)
        g.add_node("assess", self.assess)
        g.add_node("synthesize", self.synthesize)
        g.add_node("verify", self.verify)

        g.add_edge(START, "classify")
        g.add_edge("classify", "plan")
        g.add_edge("plan", "retrieve")
        g.add_edge("retrieve", "assess")
        g.add_conditional_edges("assess", self._after_assess,
                                {"plan": "plan", "synthesize": "synthesize"})
        g.add_edge("synthesize", "verify")
        g.add_conditional_edges("verify", self._after_verify,
                                {"synthesize": "synthesize", "__end__": END})

        self._graph = g.compile()
        return self._graph

    async def run(self, message: str, session_id: str = "default",
                  history: Optional[list] = None,
                  params: Optional[dict] = None) -> ChatState:
        graph = self.build()
        return await graph.ainvoke({
            "message": message,
            "session_id": session_id,
            "history": history or [],
            "params": params or {},
            "data": {},
            "errors": [],
            "reasoning": [],
            "plan_rounds": 0,
            "revise_rounds": 0,
        })

    async def stream_events(self, message: str, session_id: str = "default",
                            history: Optional[list] = None,
                            params: Optional[dict] = None):
        """Drive the pipeline, emitting the SSE vocabulary the dashboard
        speaks: ``thinking`` / ``sources`` / ``token`` / ``verification``.

        Answer tokens stream live, as they generate. Verification then runs on
        the completed text; if a figure is not present in the retrieved data
        the reader gets an explicit correction block rather than a silent
        rewrite. Holding the answer back until verified was the obvious
        alternative and it was measurably worse: time-to-first-token went from
        about two seconds to twenty-five, because synthesis is the slow part.
        A visible correction beats a blank screen, and it is also more honest
        than quietly replacing text the reader never saw.
        """
        eng = self.engine
        from app_06_clinical_chat.backend.chat_engine import (
            RESPONSE_SYSTEM_PROMPT, NO_LIVE_DATA_INSTRUCTION, _render_data,
        )

        init: ChatState = {
            "message": message, "session_id": session_id,
            "history": history or [], "params": params or {},
            "data": {}, "errors": [], "reasoning": [],
            "plan_rounds": 0, "revise_rounds": 0,
        }
        state: ChatState = dict(init)
        emitted = 0

        async for chunk in self.build_retrieval().astream(init):
            for node, update in chunk.items():
                if not isinstance(update, dict):
                    continue
                for key, val in update.items():
                    if key == "data":
                        state["data"] = {**(state.get("data") or {}), **val}
                    elif key in ("errors", "reasoning"):
                        state[key] = (state.get(key) or []) + list(val)
                    else:
                        state[key] = val
                for note in update.get("reasoning") or []:
                    emitted += 1
                    yield "thinking", f"Step {emitted}: {note}"
                if node == "retrieve" and update.get("data"):
                    yield "sources", sorted(update["data"].keys())

        # ── synthesis, streamed ──────────────────────────────────────
        data = state.get("data") or {}
        flat: Dict[str, Any] = {}
        for payload in data.values():
            if isinstance(payload, dict):
                flat.update(payload)
        section = f"\n\nHospital system data:\n{_render_data(flat)}" if flat else ""
        if state.get("errors"):
            section += "\n\nNote: " + "; ".join(state["errors"][:3])
        if not flat and state.get("plan"):
            section += NO_LIVE_DATA_INSTRUCTION

        messages = [
            {"role": "system", "content": RESPONSE_SYSTEM_PROMPT},
            *[{"role": h.get("role", "user"), "content": h.get("content", "")}
              for h in (history or [])[-4:]],
            {"role": "user",
             "content": f"User question: {message}{section}\n\n"
                        "Respond naturally. Reference specific values from the data."},
        ]
        emitted += 1
        yield "thinking", f"Step {emitted}: Generating answer…"

        parts: List[str] = []
        prev = eng.model
        eng.model = eng.MODEL_ROUTING.get("clinical_response", eng.model)
        try:
            async for kind, text in eng._call_ollama_stream(messages):
                if kind == "reasoning":
                    yield "reasoning", text
                elif kind == "content":
                    parts.append(text)
                    yield "token", text
        finally:
            eng.model = prev
        answer = "".join(parts)
        state["answer"] = answer

        # ── verification on the finished text ────────────────────────
        verdict_update = await self.verify(state)
        verdict = verdict_update.get("verdict") or {}
        for note in verdict_update.get("reasoning") or []:
            emitted += 1
            yield "thinking", f"Step {emitted}: {note}"

        problems = verdict.get("problems") or []
        if verdict.get("ok") is False and problems:
            correction = (
                "\n\n> ⚠️ **Unverified figures:** "
                + ", ".join(str(p) for p in problems[:8])
                + " — these do not appear in the retrieved hospital data and "
                  "should not be relied on."
            )
            answer += correction
            yield "token", correction

        yield "final", {
            "response": answer,
            "reasoning": state.get("reasoning", []),
            "plan": state.get("plan", []),
            "sources": sorted(data.keys()),
            "verified": verdict.get("ok"),
            "unverified_figures": [str(p) for p in problems],
            "errors": state.get("errors", []),
            "data": data,
            "intent": state.get("intent"),
        }

    async def stream(self, message: str, session_id: str = "default",
                     history: Optional[list] = None,
                     params: Optional[dict] = None):
        """Yield (node_name, partial_state) as each node completes, so the UI
        can show the plan and the verification instead of a spinner."""
        graph = self.build()
        async for chunk in graph.astream({
            "message": message,
            "session_id": session_id,
            "history": history or [],
            "params": params or {},
            "data": {},
            "errors": [],
            "reasoning": [],
            "plan_rounds": 0,
            "revise_rounds": 0,
        }):
            for node, update in chunk.items():
                yield node, update
