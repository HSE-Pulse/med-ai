# System Architecture & Enterprise Readiness Audit

**Date:** 2026-04-18
**Scope:** Full platform — 11 backend services, shared libraries, React dashboard, simulation engine, Digital Twin orchestrator

---

## System Overview

```
                        Dashboard (React + Vite + TypeScript + Tailwind)
                                    localhost:3000
                                        |
                                   Vite Proxy
                                        |
    +-------------------------------------------------------------------+
    |                    11 FastAPI Microservices                         |
    |                                                                    |
    |  +--------+ +--------+ +--------+ +--------+ +--------+           |
    |  |  8201  | |  8202  | |  8203  | |  8204  | |  8205  |           |
    |  |Triage  | |Sepsis  | |HospOps | | Onco   | |Journey |           |
    |  +--------+ +--------+ +--------+ +--------+ +--------+           |
    |                                                                    |
    |  +--------+ +--------+ +--------+ +--------+ +--------+ +--------+|
    |  |  8206  | |  8207  | |  8208  | |  8209  | |  8210  | |  8214  ||
    |  | Chat   | |SimEng  | |BedMgmt | |WaitLst | |Scribe  | |EDFlow  ||
    |  +--------+ +---+----+ +--------+ +--------+ +--------+ +--------+|
    +-----------------|----------------------------------------------+---+
                      |
          +-----------v-----------+         +------------------+
          |   Digital Twin        |-------->|   MongoDB        |
          |   Orchestrator        |         |   (MIMIC_SIM)    |
          |  (in-process, app_07) |         +------------------+
          +-----------------------+
```

### Service Registry

| Service | Port | Module | Purpose |
|---------|------|--------|---------|
| ED Triage | 8201 | app_01 | ESI acuity prediction (XGBoost) |
| Sepsis ICU | 8202 | app_02 | SOFA scoring, sepsis risk prediction |
| Hospital Ops | 8203 | app_03 | DES simulation + MARL staffing optimization |
| Oncology AI | 8204 | app_04 | Cancer readmission/mortality risk |
| Patient Journey | 8205 | app_05 | Timeline reconstruction from MIMIC data |
| Clinical Chat | 8206 | app_06 | LLM-powered clinical assistant (Ollama) |
| Simulation Engine | 8207 | app_07 | MIMIC-based hospital event simulation |
| Bed Management | 8208 | app_08 | Real-time bed tracking, discharge prediction |
| Waiting List | 8209 | app_09 | Priority scoring, NLP referral triage |
| Clinical Scribe | 8210 | app_10 | Auto-documentation, ICD-10 coding |
| ED Flow | 8214 | app_14 | PET compliance, flow optimization, NEDOCS |

### Data Flow Architecture

```
Simulation Engine (app_07)
    | MIMIC patient events (admission, vital, lab, transfer, discharge)
    v
Digital Twin Orchestrator (shared/integration/digital_twin.py)
    |
    +---> ED Triage (8201) -----> acuity + disposition
    +---> ED Flow (8214) -------> PET risk, LWBS risk, MTS category
    +---> Bed Management (8208) -> discharge prediction + bed allocation
    |         |
    |         +---> Hospital Ops (8203) <-- capacity alerts, census sync
    |         <--- staffing recommendations
    |
    +---> Oncology AI (8204) ---> cancer risk (conditional)
    +---> Clinical Scribe (8210) -> auto-documentation
    |
    v
EventBus (in-process pub/sub)
    Topics: bed_allocated, bed_released, discharge_predicted,
            capacity_alert, trolley_alert, pet_breach_risk,
            lwbs_risk, admission_predicted, patient_transferred,
            patient_discharged
```

### Shared Libraries

```
shared/
+-- api/base.py              FastAPI factory (create_app), BaseResponse, CORS, timing
+-- db/mongo.py              MongoManager (lazy connection, domain helpers)
+-- db/pipelines.py          Reusable MongoDB aggregation pipelines
+-- ml/registry.py           Model loading/saving (joblib/torch)
+-- ml/training_base.py      Training script boilerplate
+-- ml/preprocessing.py      Feature engineering utilities
+-- ml/evaluation.py         ROC, confusion matrix, calibration plots
+-- integration/event_bus.py  In-process pub/sub (15 topics)
+-- integration/service_client.py  HTTP service discovery + ModuleClient
+-- integration/digital_twin.py    Patient event orchestrator
+-- constants/mimic.py       Canonical MIMIC item ID mappings
+-- clinical/risk.py         SOFA, acuity, gender encoding, risk factors
+-- clinical/keywords.py     Medication/symptom term lists
+-- utils/datetime.py        MIMIC datetime parser
+-- utils/logging.py         Structured JSON logging (structlog)
```

---

## Enterprise Readiness Scorecard

| Dimension | Score | Details |
|-----------|-------|---------|
| **Domain Modeling** | 9/10 | Clinical schemas, MIMIC-IV data, Irish healthcare standards (PET, NEDOCS, MTS), ICD-10 coding |
| **API Design** | 8/10 | Consistent FastAPI + Pydantic contracts, BaseResponse envelope, OpenAPI auto-generated |
| **Code Quality** | 7/10 | Type hints throughout, ruff + mypy configured, shared utilities, docstrings |
| **Documentation** | 6/10 | Good README + ARCHITECTURE.md, missing runbooks/SLAs |
| **Observability** | 3/10 | structlog configured, request timing headers; no tracing, no centralized metrics |
| **Testing** | 0/10 | Zero test files across all 11 services, empty test directories, no CI |
| **Security** | 1/10 | CORS=`*`, no auth, no TLS, stack traces in error responses |
| **Infrastructure** | 1/10 | No Docker, no CI/CD, subprocess-based startup, hard-coded Windows paths |
| **Scalability** | 2/10 | Hard-coded localhost ports, in-memory state, in-process EventBus |
| **Resilience** | 3/10 | Graceful degradation on service failure; no retry/backoff, no circuit breaker |

**Overall: Research Prototype / POC** — not enterprise grade.

---

## Strengths

1. **Well-organized microservice architecture** — 11 services with consistent structure, shared utilities, clear separation of concerns
2. **Strong domain modeling** — MIMIC-IV clinical data, Irish healthcare standards (PET 6h target, NEDOCS crowding, MTS triage), ICD-10 coding
3. **Digital Twin orchestration** — Cascading pipeline (Triage -> Flow -> Beds -> Oncology -> Scribe) with runtime module enable/disable
4. **ML model integration** — XGBoost discharge prediction, acuity classification, risk scoring with rule-based fallbacks
5. **DES + MARL** — Discrete Event Simulation with Multi-Agent RL for staffing optimization (app_03)
6. **Cross-module integration** — Hospital Ops <-> Bed Management bidirectional data flow (census sync, capacity alerts, staffing recommendations)
7. **Simulation engine** — MIMIC patient replay with configurable speed (1x-100x), WebSocket streaming, department census
8. **Dashboard** — Comprehensive React UI with 13 pages, real-time polling, interactive charts, error handling

---

## Critical Gaps

### 1. Security (CRITICAL)

**No authentication or authorization on any endpoint.**

```python
# shared/api/base.py — ALL services inherit this
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # Allows ANY domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

- No JWT tokens, API keys, OAuth2, or RBAC
- Patient data (MIMIC demographics, triage scores, mortality predictions) exposed to unauthenticated requests
- Stack traces with internal file paths returned in 500 error responses
- No rate limiting — endpoints can be hammered without throttling
- No HTTPS/TLS — all traffic in plaintext
- No secrets management — MongoDB URI, API keys in environment variables without vault

**Healthcare compliance impact:** Violates HIPAA, GDPR, and Irish Data Protection Act requirements for PHI handling.

### 2. Testing (CRITICAL)

**Zero test files across the entire codebase.**

```
app_08_bed_management/tests/   # empty directory
app_09_waiting_list/tests/     # empty directory
app_10_clinical_scribe/tests/  # empty directory
app_14_ed_flow/tests/          # empty directory
```

- No unit tests, integration tests, or end-to-end tests
- No CI pipeline to run tests on commit
- pyproject.toml configures pytest but no tests exist
- No load testing or performance benchmarks
- No security scanning (OWASP, dependency audit)

### 3. Infrastructure (CRITICAL)

**No containerization or deployment automation.**

```python
# start_all.py — the entire deployment strategy
for mod, port in services:
    subprocess.Popen([sys.executable, '-m', 'uvicorn', mod, '--port', port])
time.sleep(999999)
```

- No Dockerfile, docker-compose.yml, or Kubernetes manifests
- No CI/CD pipelines (GitHub Actions, GitLab CI, Jenkins)
- Hard-coded Windows paths (`D:/project-demo/cancer/...`)
- No reverse proxy, load balancer, or API gateway
- No Infrastructure-as-Code (Terraform, CloudFormation)
- Single-machine deployment only

### 4. State Management (HIGH)

**All critical state is in-memory — lost on service restart.**

| Service | In-Memory State | Impact of Restart |
|---------|----------------|-------------------|
| Bed Management | 278 bed allocations, patient assignments | All beds appear empty |
| ED Flow | Patient tracking, PET breach timers | All patients lost |
| Hospital Ops | DES session, staffing recommendations | Simulation resets |
| Digital Twin | Patient contexts, pipeline results | All context lost |

- No persistent state layer (Redis, database-backed cache)
- Simulation reset now clears downstream modules (implemented), but unexpected restart loses everything
- In-process EventBus events not persisted (no replay on recovery)

### 5. Scalability (HIGH)

**Services cannot scale horizontally.**

```python
# shared/integration/service_client.py — hard-coded discovery
SERVICE_REGISTRY = {
    "ed_triage": "http://localhost:8201",
    "bed_management": "http://localhost:8208",
    # ... all localhost
}
```

- No service discovery (Consul, Eureka, K8s DNS)
- EventBus is in-process only — cannot span multiple instances
- No database connection pooling or tuning
- No MongoDB indexing strategy (relies on `_id` index only)
- No query pagination on large MIMIC collections (314M+ chartevents)

### 6. Observability (MEDIUM)

- Structured logging configured (structlog) but not centralized
- Request timing via `X-Process-Time-Ms` header (good)
- Only app_02 has Prometheus metrics (other 10 services have none)
- No distributed tracing (OpenTelemetry)
- No correlation IDs across service calls
- No alerting (PagerDuty, OpsGenie, etc.)

---

## Data Layer

### MongoDB

```python
# shared/db/mongo.py — connection management
class MongoManager:
    def __init__(self, uri=None):
        self._uri = uri or os.getenv("MONGO_URI", "mongodb://localhost:27017/")
        self._client = None  # lazy initialization
```

**Strengths:**
- Lazy connection initialization
- Context manager support
- Domain-specific query helpers (admissions, vitals, labs)
- Reusable aggregation pipelines (shared/db/pipelines.py)

**Gaps:**
- No connection pooling configuration (uses PyMongo defaults)
- No retry logic for transient failures
- No timeout configuration
- No SSL/TLS for database connections
- No authentication parameters
- No database migration system
- No backup/recovery procedures

### Collections Used

| Database | Collection | Size (typical) | Indexes |
|----------|-----------|----------------|---------|
| MIMIC_SIM | admissions | ~500 per sim run | `_id` only |
| MIMIC_SIM | transfers | ~2000 per sim run | `_id` only |
| MIMIC_SIM | chartevents | ~10000 per sim run | `_id` only |
| MIMIC_SIM | labevents | ~5000 per sim run | `_id` only |
| MIMIC_SIM | prescriptions | ~8000 per sim run | `_id` only |
| MIMIC_SIM | diagnoses_icd | ~4000 per sim run | `_id` only |
| MIMIC_SIM | procedures_icd | ~1000 per sim run | `_id` only |

**Missing indexes:** `hadm_id`, `subject_id`, `itemid`, `charttime` — queries scan full collections.

---

## Error Handling & Resilience

### Service-to-Service Communication

```python
# shared/integration/service_client.py
class ModuleClient:
    async def post(self, path, data):
        try:
            response = await self._client.post(url, json=data, timeout=10.0)
            response.raise_for_status()
            return response.json()
        except httpx.ConnectError:
            return {"status": "error", "error": f"{self.module_name} unavailable"}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
```

**What works:** Graceful degradation — returns error dict instead of crashing.

**What's missing:**
- No retry with exponential backoff
- No circuit breaker (dead services retried on every request)
- No bulkhead pattern (one slow service blocks callers)
- No timeout tuning per service (10s default for all)

### EventBus

```python
# shared/integration/event_bus.py
class EventBus:
    # In-process only — single Python process
    # Max 10,000 events in log (then FIFO eviction)
    # Async subscriber errors logged but swallowed
```

**Limitations:**
- Cannot cross process boundaries (HTTP notifications used as workaround)
- No delivery guarantees (at-most-once)
- No dead-letter queue
- Events lost on restart

---

## Frontend Architecture

### Technology Stack
- React 19 + TypeScript 5.8
- Vite build system
- Tailwind CSS 4 (via @tailwindcss/vite)
- Recharts for data visualization
- Lucide React for icons

### Dashboard Pages (13)

| Page | Route | Data Source |
|------|-------|------------|
| Overview | `/` | All modules via sim engine |
| ED Triage | `/ed-triage` | app_01 + sim ed-board |
| ED Flow | `/ed-flow` | app_14 |
| Sepsis & ICU | `/sepsis` | app_02 + sim icu-board |
| Hospital Ops | `/hospital-ops` | app_03 + sim census |
| Bed Management | `/bed-management` | app_08 |
| Oncology AI | `/oncology` | app_04 |
| Waiting List | `/waiting-list` | app_09 |
| Patient Journey | `/patient-journey` | app_05 + sim |
| Clinical Scribe | `/clinical-scribe` | app_10 |
| Simulation | `/simulation` | app_07 + Digital Twin |
| Clinical Chat | `/chat` | app_06 |
| System Admin | `/system` | All module health |

### Shared Frontend Libraries
- `lib/colors.ts` — Centralized color constants (acuity, SOFA, departments, MTS, crowding)
- `lib/chartConfig.ts` — Shared Recharts styling (grid, tooltip, axis)
- `lib/sofa.ts` — Client-side SOFA score calculation
- `lib/api.ts` — API client with response transformation
- `hooks/useSimPolling.ts` — Generic simulation data polling hook
- `components/StatCard.tsx` — Reusable KPI card component
- `components/SkeletonBlock.tsx` — Loading placeholder
- `components/VitalSignChart.tsx` — Vital sign trend chart

### Frontend Gaps
- No ESLint or Prettier configuration
- No frontend tests (no jest, vitest, or React Testing Library)
- No error boundary components
- No offline/PWA support

---

## ML Models

| Model | Module | Algorithm | Training Data |
|-------|--------|-----------|---------------|
| ED Acuity | app_01 | XGBoost | MIMIC-IV admissions + vitals + labs |
| Sepsis Risk | app_02 | XGBoost | MIMIC-IV ICU stays + SOFA scores |
| Readmission 30d | app_04 | XGBoost | MIMIC-IV oncology admissions |
| Mortality | app_04 | XGBoost | MIMIC-IV oncology outcomes |
| Discharge 24h | app_08 | XGBoost | MIMIC-IV LOS + discharge patterns |
| LOS Regressor | app_08 | XGBoost | MIMIC-IV admission duration |
| Capacity Forecast | app_08 | Custom | Department census time series |
| MARL Agent | app_03 | MADDPG | DES simulation episodes |

All models use `shared/ml/registry.py` for loading with JSON metadata sidecars. Rule-based fallbacks activate when ML models are unavailable.

---

## Path to Enterprise Grade

### Phase 1: Security & Compliance (Weeks 1-3)
- [ ] Add JWT authentication with role-based access (clinician, admin, readonly)
- [ ] Lock down CORS to specific origins
- [ ] Enable HTTPS/TLS on all services
- [ ] Remove stack traces from error responses in production
- [ ] Add rate limiting (per-IP and per-token)
- [ ] Implement secrets management (HashiCorp Vault or AWS Secrets Manager)
- [ ] Add audit logging for patient data access
- [ ] PII redaction in logs

### Phase 2: Infrastructure (Weeks 3-5)
- [ ] Dockerize all 11 services + dashboard
- [ ] Create docker-compose.yml for local development
- [ ] Set up CI/CD pipeline (GitHub Actions: lint, test, build, deploy)
- [ ] Add nginx reverse proxy with TLS termination
- [ ] Replace hard-coded paths with environment-based configuration
- [ ] Add health check probes (liveness + readiness)

### Phase 3: Testing (Weeks 5-8)
- [ ] Unit tests for all shared utilities (target: 90% coverage)
- [ ] Integration tests for Digital Twin pipeline
- [ ] API contract tests for all 11 services
- [ ] Frontend component tests (React Testing Library)
- [ ] Load testing (k6 or Locust) with SLA baselines
- [ ] Security scanning (Snyk, Trivy, OWASP ZAP)

### Phase 4: Resilience & State (Weeks 8-10)
- [ ] Replace in-process EventBus with Redis pub/sub
- [ ] Persist bed state and patient tracking in MongoDB
- [ ] Add retry with exponential backoff to ServiceClient
- [ ] Implement circuit breaker pattern (tenacity or pybreaker)
- [ ] Add MongoDB connection pooling and indexing
- [ ] Database backup and recovery procedures

### Phase 5: Observability (Weeks 10-12)
- [ ] Add OpenTelemetry distributed tracing across all services
- [ ] Prometheus metrics on all services (request count, latency, error rate)
- [ ] Grafana dashboards for operational monitoring
- [ ] Centralized logging (ELK stack or Loki)
- [ ] Alerting rules (PagerDuty/OpsGenie integration)
- [ ] SLI/SLO definitions and tracking

### Phase 6: Scalability (Weeks 12-16)
- [ ] Kubernetes deployment with Helm charts
- [ ] Service discovery (K8s DNS or Consul)
- [ ] Horizontal pod autoscaling based on CPU/request metrics
- [ ] MongoDB replica set for high availability
- [ ] CDN for dashboard static assets
- [ ] API gateway (Kong, Ambassador, or AWS API Gateway)

**Estimated total effort: 4-5 months** with dedicated DevOps + platform engineering team.

---

## Conclusion

The MedAI Platform demonstrates strong domain expertise in clinical AI, hospital simulation, and healthcare workflow modeling. The microservice architecture, shared library design, and Digital Twin orchestration pattern are architecturally sound. The ML pipeline (MIMIC data -> training -> inference with fallback) is well-implemented.

However, the system lacks the operational infrastructure, security controls, testing discipline, and resilience patterns required for production healthcare deployment. The current state is appropriate for research, demonstration, and stakeholder validation — but would need significant platform engineering investment before handling real patient data in a clinical setting.
