# Clinical Chat — Sub-Second Latency Implementation Plan

**Current baseline** (measured 2026-04-23):

| Endpoint | Latency | Notes |
|---|---|---|
| `GET /health` | ~220 ms | probes Ollama at 11434 |
| `POST /chat` — "What is NEWS2?" | **66 000 ms** (66 s) | full deepseek-r1:8b CoT on CPU |

Root cause: the user waits for **the entire** LLM response to complete before
seeing a single byte, and `deepseek-r1:8b` (8-billion parameters) runs at
~2-5 tokens/sec on CPU, generating 300-500 tokens of chain-of-thought + answer.

---

## Target

| Signal | Baseline | Phase 1 | Phase 2 | Phase 3 |
|---|---:|---:|---:|---:|
| Time-to-first-byte (TTFB) | 66 s | **<500 ms** | <200 ms | <100 ms |
| Time-to-first-token | 66 s | **<500 ms** | <200 ms | <100 ms |
| FAQ cache hit | 66 s | <50 ms | <50 ms | <50 ms |
| Fast-path (llama3.2:3b) full response | 66 s | 20-30 s | **<3 s** | <1 s |
| Complex clinical query full response | 66 s | 30-60 s (streamed) | 15-30 s (streamed) | 2-8 s (GPU) |

"Sub-second" is only realistic for:
1. Cache hits on FAQ / stock knowledge
2. Perceived latency (first-token streaming) for any query
3. Full answer sub-second requires **GPU inference** or a much smaller model

---

## Phase 1 — Perceived sub-second via streaming (deliver this sprint)

### 1.1 SSE endpoint `/chat/stream` on port 8206

- Flip Ollama from `stream: False` to `stream: True`.
- New FastAPI handler yields Server-Sent Events:
  - `event: thinking` — agent reasoning steps, one per line, as they happen
  - `event: context` — session metadata (patient_id, hadm_id) as soon as resolved
  - `event: token` — LLM tokens as Ollama emits them
  - `event: widgets` — widget specs after data is fetched
  - `event: alerts` — proactive alerts
  - `event: done` — final marker

TTFB = 1× FastAPI hop + 1× intent-detection (regex, ~1 ms) + first Ollama token (~200-400 ms). **Realistic: 300-500 ms.**

### 1.2 FAQ fast-path cache `/chat/fast`

- In-memory dict of ~50 common clinical questions pre-answered by the LLM.
- Seeded from a stock list (NEWS2, SOFA, ESI, IMEWS, PEWS, Sepsis Six, NEDOCS, …).
- Re-use last session's response for identical question via content hash.
- Return in <50 ms with a `source: "cache"` marker.

### 1.3 Dashboard switch to EventSource

- Replace the `fetch().then(json)` in `ClinicalChat.tsx` with an `EventSource`.
- Append tokens to the assistant bubble as they arrive; show pulsing dot in the header.
- Widgets render when the `widgets` event fires (late in the stream); they are NOT blocking text display.
- Graceful fallback to legacy `/chat` if EventSource isn't available.

### 1.4 Model pre-warm at service startup

- Issue a 1-token warm-up call to each advertised model at service startup so the first real request doesn't pay the Ollama load-from-disk penalty (~3-5 s for 8B model).

---

## Phase 2 — Live simulation context streaming

### 2.1 SimContextBroker — in-process snapshot of hospital state

New `app_06_clinical_chat/backend/sim_context.py`:

- Background task polls every 5 s:
  - `/beds/summary` (all 14 departments)
  - `/ops/staffing-recommendations`
  - `/sim/stats-dashboard`
  - `/deterioration/active-alerts`
  - `/sim/digital-twin/health`
- Compresses into a 2 KB snapshot that fits in the LLM system prompt.
- Every `/chat` and `/chat/stream` call prepends this snapshot — no need for
  per-query tool fetch for "how's the ED right now?", "what's ICU occupancy?"
  style questions.

Result: ED-status / bed-status / alert-status queries skip the 500-2000 ms
tool-fetch phase and go straight to the LLM. When combined with streaming,
TTFB drops to <300 ms.

### 2.2 WebSocket live event feed

New `WS /chat/events` endpoint:

- Subscribes to `MIMIC_SIM.event_log` via MongoDB change streams.
- Pushes new admission / transfer / discharge / deterioration events to the
  dashboard in real-time.
- Chat UI shows a "live" badge and pre-loads context for upcoming questions.

### 2.3 Parallel tool-use

- Current: intent → fetch data → LLM (serial, 500 ms + N seconds).
- Fix: start LLM draft with "fetching data…" **while** tool calls fly in parallel via `asyncio.gather`.
- Fold tool results into the stream as an `event: tool` update.

---

## Phase 3 — True sub-second full-response

### 3.1 Model routing: llama3.2:3b first, deepseek-r1:8b escalation

- llama3.2:3b: 65 tokens/sec on CPU → **full 200-token response in 3 s**.
- Use 3b for: NEWS2/PEWS/IMEWS explanations, stats queries, sim-state questions.
- Escalate to deepseek-r1:8b only for: pathway reasoning, treatment planning,
  differential diagnosis, complex multi-factor risk.
- Router uses intent + query-complexity heuristic.

### 3.2 Speculative decoding

- Ollama supports `num_predict` and draft-model speculative decoding.
- 3b draft + 8b verifier gives 2-3x speedup on accepted tokens.
- Config: `{"options": {"draft_model": "llama3.2:3b", "num_draft": 8}}`.

### 3.3 KV-cache warming

- Keep "system prompt + sim context" in Ollama's context window between
  requests (same `session_id`). Ollama preserves KV cache for the same model
  within a session window — the second request only re-encodes the user's new
  message + new context delta.

### 3.4 GPU path

- If NVIDIA GPU ≥8 GB available, Ollama offloads all layers automatically.
- Expected speedup: 10-20x for 8b model → ~1-2 s full response.
- No code change; just the deployment config (`OLLAMA_NUM_GPU=999`).

---

## Phase 4 — Advanced agentic

### 4.1 MCP (Model Context Protocol) integration

- Wrap the 18 hospital services as MCP tools.
- LLM chooses which tools to call; FastAPI Chat service becomes an MCP client.
- Enables native function-calling with schema validation and streaming tool
  results in a single protocol.

### 4.2 RAG over MIMIC_SIM

- ChromaDB / Qdrant collection of event-log summaries (admissions,
  escalations, action-log entries) with OpenAI-compatible embeddings.
- Retrieval top-5 relevant events augments every LLM prompt.
- Answers questions like "what did MARL decide last time ICU went to 100%?"
  with direct audit-log citations.

### 4.3 Guardrails + citation enforcement

- Every LLM response must include citations to:
  - HSE NCG guidelines (loaded as RAG corpus)
  - Patient-specific data used (with hadm_id references)
  - Digital Twin module outputs consulted
- Rejection of hallucinated guideline citations via post-hoc validation.

---

## Concrete delivery order (this sprint)

1. Implement SSE `/chat/stream` endpoint — backend
2. Implement FAQ cache `/chat/fast` — backend
3. Model pre-warm on startup — backend
4. SimContextBroker with 5-s snapshot loop — backend
5. Dashboard switches to EventSource — frontend
6. Inline "live" banner with sim event count — frontend
7. Parallel tool-use via `asyncio.gather` — backend
8. Model router (3b fast-path) — backend
9. Metrics: add Prometheus histogram `chat_ttfb_seconds`, `chat_ttft_seconds`,
   `chat_total_seconds`, `chat_tokens_per_second`

Everything else is Phase 3/4 and requires either GPU hardware or larger
architectural commitments (MCP, vector DB).
