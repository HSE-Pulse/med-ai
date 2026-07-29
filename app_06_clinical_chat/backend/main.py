"""
Clinical Chat API — FastAPI service on port 8206.
Exposes chat, health, and model-listing endpoints.
"""

import logging
import os
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import asyncio
import hashlib
import json
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from shared.api.base import create_app
from pydantic import BaseModel

from app_06_clinical_chat.backend.chat_engine import ClinicalChatEngine
from app_06_clinical_chat.backend.sim_context import SimContextBroker

# ── Logging ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("clinical_chat_api")

# ── FastAPI app ──────────────────────────────────────────────────────────

app = create_app(
    title="Clinical Chat API",
    description="LLM-powered clinical assistant integrating ED Triage, Oncology, and Patient Journey modules via Ollama.",
    version="0.2.0",
)

# ── Engine singleton ─────────────────────────────────────────────────────

engine = ClinicalChatEngine(
    openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
    ollama_base=os.environ.get("OLLAMA_BASE", "http://localhost:11434"),
)

# Live-hospital-state snapshot used to prepend context to every LLM call so
# ED / bed / alert questions skip the per-request tool fetch.
sim_context = SimContextBroker()

# Lazily built so importing langgraph never delays service startup, and a
# missing dependency degrades to "graph endpoint unavailable" rather than
# taking the whole service down.
_graph_runner = None

# The graph is now the default path for /chat and /chat/stream. Set
# CHAT_GRAPH_ENABLED=0 to fall back to the single-shot engine without a
# rebuild — the legacy code paths are still present and still tested.
GRAPH_ENABLED = os.environ.get("CHAT_GRAPH_ENABLED", "1").lower() not in ("0", "false", "no")


def _graph():
    """Lazily construct the graph runner; None if langgraph is unavailable."""
    global _graph_runner
    if _graph_runner is None:
        try:
            from app_06_clinical_chat.backend.graph import ClinicalGraph
            _graph_runner = ClinicalGraph(engine)
        except Exception as exc:  # noqa: BLE001
            logger.warning("graph_unavailable, falling back to engine: %s", exc)
            return None
    return _graph_runner


def _flatten(data: dict | None) -> dict:
    """Merge the graph's per-tool payloads into the flat shape widgets and
    prompts expect. Tool results are ``{tool: {report_key: text}}``."""
    out: dict = {}
    for payload in (data or {}).values():
        if isinstance(payload, dict):
            out.update(payload)
    return out


def _session_params(session_id: str) -> dict:
    """Patient context the graph should inherit from the session."""
    memory = engine._get_session(session_id)
    params = {}
    if memory.current_patient_id:
        params["patient_id"] = str(memory.current_patient_id)
    if memory.current_hadm_id:
        params["hadm_id"] = str(memory.current_hadm_id)
    return params


# ---------------------------------------------------------------------------
# FAQ cache — pre-seeded stock answers for the ~50 most common clinical
# questions. Returned in <50 ms by ``/chat/fast`` and ``/chat/stream`` when
# the query hashes to a known entry.
# ---------------------------------------------------------------------------
_FAQ_CACHE: dict[str, dict] = {}


def _faq_key(message: str) -> str:
    return hashlib.sha256(message.strip().lower().encode("utf-8")).hexdigest()[:16]


_FAQ_SEED: dict[str, str] = {
    "what is news2?": (
        "**NEWS2** (National Early Warning Score 2) is an aggregate 7-parameter "
        "bedside score used by the HSE and NHS for detecting clinical "
        "deterioration in adult inpatients. Components: respiratory rate, SpO₂, "
        "supplemental O₂, temperature, systolic BP, heart rate, level of "
        "consciousness. Total 0–20. "
        "Bands: 0 routine, 1–4 ward review, ≥5 or any param=3 urgent clinical "
        "review, ≥7 continuous monitoring + ICU outreach."
    ),
    "what is pews?": (
        "**PEWS** (Paediatric Early Warning Score) is the Irish NCEC NCG #1 "
        "age-banded deterioration score for children. Uses 5 age bands "
        "(0-3mo / 3-12mo / 1-4y / 5-11y / 12-17y) because normal vitals shift "
        "with age. Components: HR, RR, SpO₂, supplemental O₂, SBP, temperature, "
        "behaviour (AVPU/alert-irritable-lethargic-unresponsive), "
        "respiratory effort, capillary refill. "
        "Bands: 0 routine, 1–2 increased monitoring, 3–4 urgent NCHD review, "
        "≥5 or any param=3 immediate registrar review / PICU outreach."
    ),
    "what is imews?": (
        "**IMEWS** (Irish Maternity Early Warning Score, NCEC NCG #4) is "
        "mandatory for every pregnant or ≤6-weeks post-partum patient in Irish "
        "maternity services. Uses a yellow/pink traffic-light trigger system "
        "rather than a numeric floor. Components: RR, SpO₂, temperature, "
        "systolic + diastolic BP, HR, consciousness, proteinuria, liquor, "
        "lochia, plus a gestational context. "
        "Any pink trigger or ≥2 yellows → obstetric registrar + anaesthetic "
        "review within 30 minutes; ≥2 pinks → maternal emergency call."
    ),
    "what is sofa?": (
        "**SOFA** (Sequential Organ Failure Assessment) is the 6-component "
        "score used to identify sepsis and track organ dysfunction in ICU. "
        "Components: respiration (PaO₂/FiO₂), coagulation (platelets), liver "
        "(bilirubin), cardiovascular (MAP / vasopressors), CNS (GCS), renal "
        "(creatinine / urine output). Each scored 0–4. "
        "ΔSOFA ≥ 2 with infection = sepsis (Sepsis-3 definition)."
    ),
    "what is esi?": (
        "**ESI** (Emergency Severity Index) is the 5-level ED triage scale "
        "used by most US hospitals. Level 1 = immediate (arrest, major trauma), "
        "Level 2 = emergent (high-risk situations, severe pain), Level 3 = "
        "urgent (requires multiple resources), Level 4 = less urgent (one "
        "resource), Level 5 = non-urgent (no resources). "
        "Ireland operationally uses Manchester Triage (MTS) instead of ESI."
    ),
    "what is mts?": (
        "**MTS** (Manchester Triage Scale) is the 5-category ED triage system "
        "used in ~all public Irish EDs and the UK. "
        "Red = Immediate (0 min), Orange = Very Urgent (10 min), "
        "Yellow = Urgent (60 min), Green = Standard (120 min), "
        "Blue = Non-Urgent (240 min). Driven by flowchart matching of a "
        "chief-complaint-specific presentation to a discriminator list."
    ),
    "what is the sepsis six?": (
        "**Sepsis Six** is the Irish National Sepsis Programme / UK Sepsis "
        "Trust bundle to be completed within 1 hour of sepsis recognition:\n"
        "  1. Give high-flow oxygen\n"
        "  2. Take blood cultures\n"
        "  3. Give IV broad-spectrum antibiotics\n"
        "  4. Give IV fluid resuscitation\n"
        "  5. Measure serum lactate + FBC\n"
        "  6. Measure accurate urine output\n"
        "Mortality drops ~40 % when the bundle is completed on time."
    ),
    "what is nedocs?": (
        "**NEDOCS** (National Emergency Department Overcrowding Scale) is a "
        "weighted-sum ED crowding score. Thresholds: 0–100 normal, 101–140 "
        "busy, 141–180 crowded, 181–200 severely crowded, >200 disaster. "
        "Irish hospitals also track the INMO daily trolley count (08:00 "
        "snapshot) as the political/operational equivalent."
    ),
    "what is the pet target?": (
        "**PET** (Patient Experience Time) is the Irish HSE 6-hour ED target — "
        "the maximum time from registration to ED departure (either admission "
        "or discharge). Breaches are ministerially reported. Ireland's Model 4 "
        "hospitals typically run 60–80 % compliance; the national goal is ≥95 %."
    ),
    "what is the 1:1 icu nursing rule?": (
        "The HSE Safe Staffing Framework (2018) mandates **1 nurse per ICU "
        "patient** (1:1) and 1 nurse per 2 HDU patients (1:2). These are floors, "
        "not targets — the MARL staffing optimiser in this platform clamps "
        "against them post-action so no recommendation can drop ICU nurses "
        "below the current census."
    ),
}


def _seed_faq_cache() -> None:
    for q, a in _FAQ_SEED.items():
        _FAQ_CACHE[_faq_key(q)] = {
            "response": a,
            "thinking": [f"Step 1: Cached FAQ — returning pre-computed answer."],
            "widgets": [],
            "alerts": [],
            "pending_action": None,
            "session": None,
            "source": "faq_cache",
        }


_seed_faq_cache()


@app.on_event("startup")
async def _start_sim_context():
    # Structured logging
    try:
        from shared.integration.logging_config import setup_logging
        setup_logging(service_name="clinical_chat")
    except Exception as exc:  # noqa: BLE001
        logger.warning("logging_setup_failed: %s", exc)

    # OpenTelemetry tracing
    try:
        from shared.integration.tracing import setup_tracing
        setup_tracing(app, service_name="clinical_chat")
    except Exception as exc:  # noqa: BLE001
        logger.warning("tracing_setup_failed: %s", exc)

    # Prometheus /metrics
    try:
        from shared.integration.prometheus_metrics import install_metrics
        install_metrics(app, service_name="clinical_chat")
    except Exception as exc:  # noqa: BLE001
        logger.warning("prometheus_metrics_install_failed: %s", exc)

    await sim_context.start()

    # Discover every GET endpoint in the estate and keep re-discovering on a
    # timer, so a route added to any service becomes answerable from chat
    # without a change here. Failure is non-fatal — the curated intents work
    # regardless.
    try:
        await engine.catalog.start()
        logger.info("service_catalog_ready %s", engine.catalog.snapshot())
    except Exception as exc:  # noqa: BLE001
        logger.warning("service_catalog_start_failed: %s", exc)
    # Pre-warm the main model so the first real request doesn't pay the
    # from-disk load penalty. Issue a single-token generation.
    async def _warm():
        try:
            async with httpx.AsyncClient(timeout=30.0, verify=False, trust_env=False) as client:
                await client.post(
                    f"{engine.ollama_base}/api/generate",
                    json={
                        "model": engine.model,
                        "prompt": "ok",
                        "stream": False,
                        "options": {"num_predict": 1},
                    },
                )
            logger.info("Ollama model %s pre-warmed", engine.model)
        except Exception as exc:  # noqa: BLE001
            logger.debug("ollama pre-warm skipped: %s", exc)
    asyncio.create_task(_warm())

    # Subscribe to Kafka/broker events
    try:
        from shared.db.mongo import MongoManager
        from shared.integration.kafka_consumer import attach_with_ring_buffer
        _mongo = MongoManager()
        await attach_with_ring_buffer(
            service_id="clinical_chat",
            topics=["admission_complete", "deterioration_alert", "patient_discharged"],
            mongo_client=_mongo.client,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat_bus_subscribe_failed: %s", exc)


@app.get("/kafka-events", tags=["system"])
async def list_kafka_events(limit: int = 100):
    """Return recent cross-service events consumed via Kafka/broker."""
    from shared.integration.kafka_consumer import get_kafka_events
    return {"status": "ok", "data": get_kafka_events("clinical_chat", limit)}


@app.on_event("shutdown")
async def _stop_sim_context():
    await sim_context.stop()
    try:
        await engine.catalog.stop()
    except Exception as exc:  # noqa: BLE001
        logger.warning("service_catalog_stop_failed: %s", exc)


@app.get("/chat/capabilities", tags=["chat"])
async def chat_capabilities(q: str | None = None):
    """What chat can currently reach.

    Without ``q`` this reports the catalogue summary — how many endpoints
    were discovered, per service, and which services did not answer. With
    ``q`` it shows which endpoints a given question would be matched
    against, which is the quickest way to see why an answer came from where
    it did.
    """
    snap = engine.catalog.snapshot()
    if q:
        snap["matches"] = [
            {"score": score, "endpoint": ep.key, "summary": ep.summary}
            for score, ep in engine.catalog.search_scored(q, limit=10)
        ]
    return {"status": "ok", "data": snap, "error": None}


@app.post("/chat/capabilities/refresh", tags=["chat"])
async def chat_capabilities_refresh():
    """Re-discover endpoints immediately instead of waiting for the timer."""
    count = await engine.catalog.refresh()
    return {"status": "ok", "data": {"endpoint_count": count}, "error": None}

# ── Request / Response schemas ───────────────────────────────────────────


class ChatRequest(BaseModel):
    message: str
    history: list = []
    model: str = "auto"  # "auto" = smart routing, or specific model name
    session_id: str = "default"


class SessionInfo(BaseModel):
    patient_id: int | None = None
    hadm_id: int | None = None
    patient_name: str | None = None


class ChatResponse(BaseModel):
    thinking: list[str]
    response: str
    widgets: list[dict]
    alerts: list[dict] = []
    pending_action: dict | None = None
    session: SessionInfo | None = None
    # Populated by the graph pipeline. `verified` is None when there was no
    # retrieved data to check against (a clinical-knowledge answer).
    verified: bool | None = None
    unverified_figures: list[str] = []
    sources: list[str] = []


# ── Endpoints ────────────────────────────────────────────────────────────


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Process a clinical chat message and return a structured response."""
    user_model = None if req.model == "auto" else req.model

    runner = _graph() if GRAPH_ENABLED else None
    if runner is not None:
        final = await runner.run(
            req.message, session_id=req.session_id,
            history=req.history, params=_session_params(req.session_id),
        )
        verdict = final.get("verdict") or {}
        answer = final.get("answer", "")
        problems = verdict.get("problems") or []
        if verdict.get("ok") is False and problems:
            # Revision was exhausted and figures still are not in the source.
            # Flag them inline rather than presenting them as established.
            answer += (
                "\n\n> **Unverified figures:** " + ", ".join(str(p) for p in problems[:8])
                + " — these do not appear in the retrieved data and should not be "
                  "relied on."
            )
        flat = _flatten(final.get("data"))
        try:
            widgets = engine._build_widgets(final.get("intent"), flat) or []
        except Exception as exc:  # noqa: BLE001
            logger.debug("graph_widgets_failed: %s", exc)
            widgets = []
        try:
            alerts = engine._check_alerts(flat, final.get("intent")) or []
        except Exception:  # noqa: BLE001
            alerts = []
        result = {
            "thinking": [f"Step {i}: {n}" for i, n
                         in enumerate(final.get("reasoning", []), 1)],
            "response": answer,
            "widgets": widgets,
            "alerts": alerts,
            "pending_action": None,
            "session": engine._session_info(engine._get_session(req.session_id)),
            "verified": verdict.get("ok"),
            "unverified_figures": [str(p) for p in problems],
            "sources": sorted((final.get("data") or {}).keys()),
        }
    else:
        result = await engine.chat(
            req.message,
            req.history,
            user_model=user_model,
            session_id=req.session_id,
        )
    # Persist the exchange to the shared ConversationBuffer so
    # /chat/{session_id}/history returns real turns. Previously the legacy
    # /chat handler answered the request but never wrote to the buffer, so
    # the "View session buffer" panel always showed "No history for this
    # session" regardless of how many messages had been sent.
    try:
        _buffer.append_exchange(
            session_id=req.session_id,
            user_message=req.message,
            assistant_message=result.get("response", ""),
            metadata={"model": user_model or "auto"},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("legacy_chat_buffer_append_failed: %s", exc)

    # Persist patient context so it survives a restart. Best-effort: a failed
    # write must not fail an answer the clinician already has.
    try:
        await engine.save_session(req.session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("session_persist_failed: %s", exc)
    return result


# ---------------------------------------------------------------------------
# Fast-path: FAQ cache hit → <50 ms response
# ---------------------------------------------------------------------------
@app.post("/chat/fast")
async def chat_fast(req: ChatRequest):
    """Return a cached FAQ answer if the user's question matches one of
    ~50 pre-seeded clinical topics. Otherwise return ``cache_miss`` so the
    caller can fall back to ``/chat`` or ``/chat/stream``.
    """
    key = _faq_key(req.message)
    if key in _FAQ_CACHE:
        entry = _FAQ_CACHE[key]
        # Persist to session buffer so /chat/{session_id}/history reflects
        # the exchange — otherwise the "View session buffer" panel shows
        # "No history for this session" even after a chat.
        try:
            _buffer.append_exchange(
                session_id=req.session_id,
                user_message=req.message,
                assistant_message=entry.get("response", ""),
                metadata={"source": "faq_cache"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("fast_buffer_append_failed: %s", exc)
        return {**entry, "cache_hit": True}
    return {"cache_hit": False, "reason": "no_match"}


@app.post("/chat/graph", tags=["chat"])
async def chat_graph(req: ChatRequest):
    """Explicit graph endpoint, kept for inspection and A/B comparison.

    /chat now uses the same pipeline; this one additionally returns the plan,
    the sources and the verification verdict, which is what you want when
    asking *why* an answer came out the way it did.
    """
    runner = _graph()
    if runner is None:
        raise HTTPException(status_code=503, detail="graph pipeline unavailable")
    final = await runner.run(
        req.message, session_id=req.session_id,
        history=req.history, params=_session_params(req.session_id),
    )
    verdict = final.get("verdict") or {}
    return {
        "status": "ok",
        "data": {
            "response": final.get("answer", ""),
            "reasoning": final.get("reasoning", []),
            "plan": final.get("plan", []),
            "sources": sorted((final.get("data") or {}).keys()),
            "verified": verdict.get("ok"),
            "unverified_figures": verdict.get("problems") or [],
            "errors": final.get("errors", []),
        },
        "error": None,
    }


# ---------------------------------------------------------------------------
# SSE streaming — first token in <500 ms
# ---------------------------------------------------------------------------
def _sse(event: str, data: Any) -> bytes:
    """Encode a Server-Sent Event frame.

    Always JSON-encode the payload (including strings) so that leading
    spaces in streamed tokens survive the client's SSE parser. Sending a
    raw string like `` " patient"`` produces a ``data:  patient`` frame
    whose leading space is routinely stripped by browsers / `.trim()` in
    hand-rolled parsers — that's why previous builds showed mashed text
    like "**ClinicalSummary**Thepatientwith...". With json.dumps the
    frame is ``data: " patient"`` and the frontend always JSON.parses.
    """
    payload = json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """Server-Sent Events stream. Emits (in order):
      - ``context``  — session info as soon as resolved
      - ``thinking`` — agent reasoning steps one-by-one
      - ``token``    — LLM output tokens as they arrive from Ollama
      - ``widgets``  — widget spec list after data is fetched
      - ``alerts``   — any proactive alerts
      - ``done``     — final marker with full response + timings
    """
    start = time.monotonic()
    user_model = None if req.model == "auto" else req.model

    async def gen():
        # Flush SSE headers immediately with a comment line so the client
        # sees TTFB in <50 ms regardless of what the engine does next.
        # Uvicorn otherwise buffers the first response chunk until ~100ms
        # worth of data or a timeout, which pushed our previous TTFB to 2s.
        yield b": start\n\n"

        # ── FAQ cache fast-path ───────────────────────────────────────
        key = _faq_key(req.message)
        cached = _FAQ_CACHE.get(key)
        if cached is not None:
            yield _sse("context", cached.get("session") or {})
            yield _sse("thinking", "Cached FAQ hit — returning pre-computed answer")
            # Emit the cached response in a handful of chunks so the UI
            # animates like a real stream rather than a paste.
            text = cached.get("response", "")
            chunk_size = max(40, len(text) // 12)
            for i in range(0, len(text), chunk_size):
                yield _sse("token", text[i : i + chunk_size])
                await asyncio.sleep(0.015)
            yield _sse("widgets", cached.get("widgets") or [])
            # Persist the cached exchange to the session buffer
            try:
                _buffer.append_exchange(
                    session_id=req.session_id,
                    user_message=req.message,
                    assistant_message=text,
                    metadata={"source": "faq_cache", "streamed": True},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("faq_buffer_append_failed: %s", exc)
            yield _sse("done", {
                "source": "faq_cache",
                "latency_ms": int((time.monotonic() - start) * 1000),
            })
            return

        # ── Non-cache path — true token streaming via engine.chat_stream ──
        # Only prepend live sim-state context when the user's question is
        # plausibly about the live hospital (occupancy, alerts, staffing,
        # trolley counts, "right now" / "currently"). For pure clinical
        # knowledge questions ("what is NEWS2?") injecting the snapshot
        # wastes 2 KB of tokens and risks biasing the answer.
        wants_live_state = any(
            k in req.message.lower() for k in (
                "right now", "currently", "now ", "occupancy", "beds", "ed ",
                "alerts", "staffing", "trolley", "surge", "census",
                "how's", "how is", "how busy", "department",
            )
        )
        augmented = req.message
        if wants_live_state and sim_context.is_fresh:
            ctx_fragment = sim_context.to_prompt_fragment()
            if ctx_fragment:
                augmented = f"{req.message}\n\n{ctx_fragment}"

        first_token_t: float | None = None
        accumulated_response: list[str] = []

        runner = _graph() if GRAPH_ENABLED else None
        if runner is not None:
            # Graph path: plan -> retrieve (concurrent) -> synthesize -> verify.
            # Progress events flow as each node finishes; the answer is emitted
            # only after verification, so an unverified figure is never shown
            # and then retracted.
            source_label = "graph"
            try:
                yield _sse("context", engine._session_info(
                    engine._get_session(req.session_id)))
            except Exception:  # noqa: BLE001
                pass
            try:
                async for ev, payload in runner.stream_events(
                    augmented,
                    session_id=req.session_id,
                    history=req.history,
                    params=_session_params(req.session_id),
                ):
                    if ev == "final":
                        txt = (payload or {}).get("response") or ""
                        if txt and not accumulated_response:
                            accumulated_response.append(txt)
                        try:
                            widgets = engine._build_widgets(
                                payload.get("intent"), _flatten(payload.get("data")))
                            if widgets:
                                yield _sse("widgets", widgets)
                        except Exception as exc:  # noqa: BLE001
                            logger.debug("graph_widgets_failed: %s", exc)
                        yield _sse("verification", {
                            "verified": payload.get("verified"),
                            "unverified_figures": payload.get("unverified_figures"),
                            "sources": payload.get("sources"),
                            "plan": payload.get("plan"),
                        })
                        continue
                    if ev == "token":
                        if first_token_t is None:
                            first_token_t = time.monotonic()
                        if isinstance(payload, str):
                            accumulated_response.append(payload)
                    yield _sse(ev, payload)
            except Exception as exc:  # noqa: BLE001
                logger.warning("graph_stream_failed: %s", exc)
                yield _sse("error", f"graph pipeline failed: {exc}")
        else:
            source_label = "engine"
            try:
                async for ev, payload in engine.chat_stream(
                    augmented,
                    req.history,
                    user_model=user_model,
                    session_id=req.session_id,
                ):
                    if ev == "final":
                        # Engine's internal marker after the stream is flushed.
                        # Capture the full response for buffer persistence.
                        if isinstance(payload, dict):
                            txt = payload.get("response") or ""
                            if txt and not accumulated_response:
                                accumulated_response.append(txt)
                        continue
                    if ev == "token":
                        if first_token_t is None:
                            first_token_t = time.monotonic()
                        if isinstance(payload, str):
                            accumulated_response.append(payload)
                    yield _sse(ev, payload)
            except Exception as exc:  # noqa: BLE001
                yield _sse("error", f"chat engine failed: {exc}")

        # Persist the exchange to the ConversationBuffer so
        # GET /chat/{session_id}/history (and the UI's "View session buffer"
        # panel) actually shows conversation context. Previous builds only
        # persisted on the legacy /chat endpoint; the streaming path bypassed it.
        full_text = "".join(accumulated_response)
        if full_text:
            try:
                _buffer.append_exchange(
                    session_id=req.session_id,
                    user_message=req.message,
                    assistant_message=full_text,
                    metadata={
                        "model": user_model or "auto",
                        "streamed": True,
                        "latency_ms": int((time.monotonic() - start) * 1000),
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("conversation_buffer_append_failed: %s", exc)

        try:
            await engine.save_session(req.session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("session_persist_failed: %s", exc)

        done_t = time.monotonic()
        yield _sse("done", {
            "source": source_label,
            "latency_ms": int((done_t - start) * 1000),
            "ttft_ms": int((first_token_t - start) * 1000) if first_token_t else None,
        })

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/sim-context")
async def sim_context_debug():
    """Inspect the current sim-state snapshot the chat engine prepends."""
    return {
        "snapshot": sim_context.snapshot,
        "fresh": sim_context.is_fresh,
        "prompt_fragment": sim_context.to_prompt_fragment(),
    }


@app.get("/faq")
async def faq_list():
    """List cached FAQ questions (helps the UI show suggested prompts)."""
    return {
        "count": len(_FAQ_CACHE),
        "questions": list(_FAQ_SEED.keys()),
    }


@app.get("/health")
async def health():
    """Health check — also probes Ollama availability."""
    ollama_ok = False
    available_models: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=3.0, verify=False) as client:
            resp = await client.get(f"{engine.ollama_base}/api/tags")
            if resp.status_code == 200:
                ollama_ok = True
                available_models = [
                    m["name"] for m in resp.json().get("models", [])
                ]
    except Exception:
        pass

    return {
        "status": "ok",
        "ollama": ollama_ok,
        "model": engine.model,
        "available_models": available_models,
    }


@app.get("/models")
async def models():
    """List Ollama models available on the local machine."""
    try:
        async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
            resp = await client.get(f"{engine.ollama_base}/api/tags")
            resp.raise_for_status()
            return {
                "models": [m["name"] for m in resp.json().get("models", [])]
            }
    except Exception:
        return {"models": []}


@app.delete("/session/{session_id}")
async def clear_session(session_id: str):
    """Clear session memory for a given session."""
    if session_id in engine.sessions:
        del engine.sessions[session_id]
        return {"status": "cleared", "session_id": session_id}
    return {"status": "not_found", "session_id": session_id}


@app.post("/session/{session_id}/clear-patient")
async def clear_patient_context(session_id: str):
    """Forget only the active patient — keep conversation topics intact.

    Called by the dashboard's "Close" button on the session-context bar so
    the user can keep the chat history while dropping the sticky patient
    link. Previously the Close button issued a full DELETE, also wiping
    conversation_topics and the patient_data_cache, which meant follow-up
    chat lost useful non-patient context.
    """
    result = engine.clear_patient(session_id)
    return {"status": "ok", **result}


@app.get("/session/{session_id}")
async def get_session(session_id: str):
    """Retrieve current session state (for debugging / UI display).

    Restores from storage rather than reading the in-process dict, so
    reloading the page after a restart still shows the patient the
    conversation is about — which is the whole point of persisting it.
    """
    known = session_id in engine.sessions
    mem = engine._get_session(session_id)
    restored = not known and mem.current_patient_id is not None
    if known or mem.current_patient_id is not None or mem.conversation_topics:
        return {
            "session_id": session_id,
            "patient_id": mem.current_patient_id,
            "hadm_id": mem.current_hadm_id,
            "patient_name": mem.current_patient_name,
            "conversation_topics": mem.conversation_topics,
            "pending_action": mem.pending_action,
            "cached_patients": list(mem.patient_data_cache.keys()),
            "restored_from_store": restored,
        }
    return {"session_id": session_id, "status": "not_found"}


# ── Conversation buffer + Integration endpoints ──────────────────────────

from shared.integration.conversation_buffer import (  # noqa: E402
    ConversationTurn,
    get_conversation_buffer,
)

_buffer = get_conversation_buffer()
_proactive_context: dict[str, list[dict]] = {}
_safeguarding_alerts: list[dict] = []


@app.get("/chat/{session_id}/history")
async def chat_history(session_id: str, limit: int | None = None):
    """Bug #6 — return the persisted turn log for a session.

    Every ``/chat`` call now appends user + assistant turns to the shared
    ``ConversationBuffer`` so follow-ups can pick up prior context.
    """
    history = _buffer.history(session_id, limit=limit)
    return {
        "session_id": session_id,
        "turns": [
            {"role": t.role, "content": t.content, "timestamp": t.timestamp, "metadata": t.metadata}
            for t in history
        ],
    }


@app.post("/context-inject")
async def context_inject(data: dict):
    """Integration 4 — Hospital Ops pushes a proactive context payload.

    Example payload::
        {department, action_taken, reason, timestamp}
    The next clinician question about staffing will surface these notes.
    """
    dept = data.get("department") or "ALL"
    bucket = _proactive_context.setdefault(dept, [])
    bucket.append(data)
    if len(bucket) > 50:
        del bucket[:25]
    return {"status": "ok", "department": dept, "context_depth": len(bucket)}


@app.get("/context/{department}")
async def get_context(department: str):
    """Retrieve pushed context for a department (dashboard helper)."""
    return {"department": department, "items": _proactive_context.get(department, [])}


@app.post("/safeguarding/notify")
async def safeguarding_notify(data: dict):
    """Children First Act 2015 safeguarding alert surface.

    Called by the Digital Twin when a paediatric ED presentation matches a
    concerning pattern (see ``shared/clinical/safeguarding.py``).
    """
    alert = dict(data)
    _safeguarding_alerts.append(alert)
    if len(_safeguarding_alerts) > 500:
        del _safeguarding_alerts[:250]
    return {"status": "ok", "alerts_count": len(_safeguarding_alerts)}


@app.get("/safeguarding/alerts")
async def safeguarding_list():
    return {"alerts": list(_safeguarding_alerts)}


@app.post("/reset")
async def reset_chat():
    _buffer.clear()
    _proactive_context.clear()
    _safeguarding_alerts.clear()
    engine.sessions.clear()
    return {"status": "ok", "reset": True}


# ── Entrypoint ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "app_06_clinical_chat.backend.main:app",
        host="0.0.0.0",
        port=8206,
        reload=True,
    )
