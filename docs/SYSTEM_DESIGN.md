# MedAI Platform — System Design & Integrated Data Flow

**Version:** 3.1
**Date:** 2026-04-23
**Services:** 18 backend microservices + React dashboard
**Database:** MongoDB (MIMIC-IV · MIMIC_ICU · MIMIC_Clinical_Notes · MIMIC_SIM)
**ML:** XGBoost · LightGBM · LSTM-Attention · Transformer · MADDPG · DES
**Deterioration:** NEWS2 (adult) · PEWS (paediatric, NCEC NCG #1) · IMEWS (maternity, NCEC NCG #4)

---

## 1. System Architecture

```
                     ┌──────────────────────────────────────────────────┐
                     │           Dashboard (React 19 + Vite 6)          │
                     │               localhost:3000                      │
                     │  16 pages · auto-polling · WebSocket · Recharts  │
                     └─────────────────────┬────────────────────────────┘
                                           │ Vite Proxy (/api/*)
          ┌────────────────────────────────┼────────────────────────────────┐
          │                                │                                │
          ▼                                ▼                                ▼
┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐
│ ED     │ │ Sepsis │ │ Hosp   │ │ Onco   │ │Patient │ │Clinical│ │ Data   │
│ Triage │ │  ICU   │ │  Ops   │ │   AI   │ │Journey │ │  Chat  │ │Ingest  │
│ :8201  │ │ :8202  │ │ :8203  │ │ :8204  │ │ :8205  │ │ :8206  │ │ :8207  │
└────────┘ └────────┘ └────────┘ └────────┘ └────────┘ └────────┘ └────────┘
┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐
│  Bed   │ │Waiting │ │Clinical│ │  ED    │ │Hospital│ │Trolley │ │  GDPR  │
│  Mgmt  │ │  List  │ │ Scribe │ │  Flow  │ │  ERP   │ │ Watch  │ │  :8217 │
│ :8208  │ │ :8209  │ │ :8210  │ │ :8214  │ │ :8215  │ │ :8216  │ │        │
└────────┘ └────────┘ └────────┘ └────────┘ └────────┘ └────────┘ └────────┘
┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐
│  XAI   │ │  FHIR  │ │Deterio-│ │Discharge│
│ :8218  │ │ :8219  │ │ration  │ │ Lounge  │
│        │ │        │ │ :8220  │ │ :8221   │
└────────┘ └────────┘ └────────┘ └─────────┘
          │
          │  Shared Integration Layer (in-process per service)
          ├── DigitalTwin (shared/integration/digital_twin.py)
          ├── EventBus   (shared/integration/event_bus.py)
          ├── CircuitBreaker (shared/integration/circuit_breaker.py)
          ├── ServiceClient  (shared/integration/service_client.py)
          └── SimClock   (shared/integration/sim_clock.py)
          │
          ▼
┌──────────────────────────────────────────────────────────────────┐
│                          MongoDB (3 databases)                    │
│  mimic           — 26 collections (admissions, labs, diagnoses…) │
│  mimic_icu       — 5 collections  (icustays, chartevents…)       │
│  mimic_notes     — 1 collection   (discharge summaries × 331K)   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. Service Registry

| Port | Module | Purpose | Key Endpoints |
|------|--------|---------|---------------|
| 8201 | ED Triage | ESI acuity prediction (XGBoost / NN) | `/predict`, `/batch-predict`, `/model-info`, `/stats` |
| 8202 | Sepsis ICU | Sepsis risk 4-6h ahead; SOFA monitoring | `/predict`, `/patient/{id}/timeline`, `/unit-overview`, `/ws/monitor` |
| 8203 | Hospital Ops | DES + MADDPG staffing optimization | `/simulate`, `/start`, `/step`, `/state`, `/ws/simulation` |
| 8204 | Oncology AI | Cancer readmission/mortality risk + pathway | `/predict`, `/pathway`, `/analyze-note` |
| 8205 | Patient Journey | Timeline, vitals, labs, meds, cohort compare | `/patient/{id}`, `/vitals`, `/labs`, `/medications`, `/journey`, `/cohort` |
| 8206 | Clinical Chat | LLM clinical assistant (Ollama / OpenAI) | `/chat`, `/models`, `/context` |
| 8207 | Data Ingestion | ETL pipeline, CSV/Parquet upload, schema | `/ingest`, `/status`, `/schema` |
| 8208 | Bed Management | Bed tracking with category taxonomy (isolation/paediatric/bariatric/stroke), allocation, forecast; idempotent notifications | `/beds`, `/beds/summary`, `/beds/categories`, `/allocate`, `/predict-discharge`, `/forecast/{dept}`, `/notify-discharge/{id}` (idempotent) |
| 8209 | Waiting List | Priority scoring, NLP triage, scheduling | `/queue`, `/predict-wait`, `/priority-rerank`, `/schedule` |
| 8210 | Clinical Scribe | Auto-transcription, NER, ICD coding | `/transcribe`, `/extract-entities`, `/templates` |
| 8214 | ED Flow | PET compliance, NEDOCS, bottleneck detection | `/flow`, `/bottlenecks`, `/delays` |
| 8215 | Hospital ERP | Master data (single source of truth) | `/departments`, `/staff`, `/schedule`, `/beds`, `/config` |
| 8216 | Trolley Watch | IoT bed/trolley movement tracking | `/trolleys`, `/movements`, `/availability` |
| 8217 | GDPR Compliance | Audit trail, SAR, erasure, breach, DPIA | `/gdpr/sar/{id}`, `/gdpr/purge/{id}`, `/gdpr/ropa`, `/gdpr/breach`, `/gdpr/dpia/{module}` |
| 8218 | XAI | SHAP/LIME explanations, feature importance | `/explain`, `/feature-importance`, `/decision-tree` |
| 8219 | FHIR Gateway | HL7 FHIR R4 national EHR interoperability (11 resource types) | `/fhir/CapabilityStatement`, `/fhir/Patient/{id}`, `/fhir/Encounter/{id}` + search, `/fhir/Observation`, `/fhir/Condition`, `/fhir/Procedure`, `/fhir/MedicationRequest`, `/fhir/DiagnosticReport`, `/fhir/DocumentReference`, `/fhir/AllergyIntolerance`, `/fhir/RiskAssessment`, `/fhir/CarePlan/{id}`, `/fhir/Bundle` |
| 8220 | Deterioration | NEWS2 / PEWS / IMEWS scoring with auto-routing, trended analysis, SBAR ack loop, durable persistence | `/deterioration/screen` (auto-route), `/deterioration/pews`, `/deterioration/imews`, `/deterioration/escalate`, `/deterioration/acknowledge`, `/deterioration/active-alerts`, `/deterioration/history/{id}`, `/deterioration/trend/{id}`, `/deterioration/escalations` |
| 8221 | Discharge Lounge | Discharge planning, follow-up, readmission prevention | `/patient/{id}/discharge-plan`, `/follow-up`, `/prescriptions` |

---

## 3. Master Data (ERP — Single Source of Truth)

### 3.1 Department Configuration

| Department | Type | Beds | Nurse:Patient | LOS Median |
|-----------|------|------|---------------|------------|
| ED | emergency | 30 | variable | 6.7h |
| MAU | assessment | 24 | 1:4 | 18h |
| AMAU | assessment | 16 | 1:4 | 12h |
| SAU | assessment | 12 | 1:6 | 10h |
| CDU | observation | 8 | 1:4 | 8h |
| Medicine | inpatient | 40 | 1:6 | 120h (5d) |
| Surgery | inpatient | 36 | 1:6 | 96h (4d) |
| Cardiology | inpatient | 20 | 1:4 | 72h (3d) |
| Respiratory | inpatient | 18 | 1:4 | 96h (4d) |
| Orthopaedics | inpatient | 24 | 1:6 | 84h (3.5d) |
| ICU | critical | 12 | **1:1** | 72h (3d) |
| HDU | high_dependency | 8 | **1:2** | 48h (2d) |
| Day_Ward | day_case | 20 | 1:5 | 6h |
| Discharge_Lounge | discharge | 10 | 1:10 | 2h |
| **TOTAL** | | **278** | | |

### 3.2 Staff per Shift (Irish 12-hour pattern)

| Department | Day Docs | Day Nurses | Night Docs | Night Nurses | Weekend Docs |
|-----------|----------|-----------|-----------|-------------|-------------|
| ED | 11 | 12 | 6 | 9 | 7 |
| Medicine | 8 | 8 | 3 | 7 | 5 |
| ICU | 4 | 13 | 2 | 11 | 3 |
| Surgery | 7 | 7 | 3 | 6 | 4 |

### 3.3 Shared Constants (`shared/constants/hospital.py`)

All modules import from here instead of defining their own:
- `DEPARTMENTS` — 14 Irish HSE department names
- `CAPACITIES` — bed counts per department (total 278)
- `STAFF_DEFAULTS` — doctors/nurses per department (day shift)
- `SERVICE_PARAMS` — log-normal LOS parameters for DES
- `LOS_PARAMS` — median/mean/p90 hours per department
- `MTS_CATEGORIES` — Manchester Triage Scale (5 levels)
- `PET_TARGET_HOURS` — 6 hours (Irish standard)
- `NEDOCS_THRESHOLDS` — normal/busy/crowded/severe
- `map_department()` — MIMIC → Irish name mapping (30+ entries)

---

## 4. Simulation Data Flow

### 4.1 Event Generation

```
MIMIC-IV Patient Pool (500 patients loaded)
    │
    ▼
SimClock (shared/integration/sim_clock.py — configurable 1x-100x speed)
    │
    ▼
PatientGenerator
    │ Poisson arrivals (~30 patients/sim-day)
    │ Pathway: Markov chain transitions between 14 departments
    ▼
HospitalEventEngine (priority queue)
    │
    ├── ARRIVAL event → admit patient, schedule vitals/labs/transfers
    ├── VITAL event → record to MongoDB, broadcast via EventBus
    ├── LAB event → record to MongoDB, broadcast via EventBus
    ├── TRANSFER event → move patient between departments
    ├── DISCHARGE event → finalize patient
    └── Each event → DigitalTwinOrchestrator.process_*()
```

### 4.2 Digital Twin Pipeline (per admission)

```
process_admission(patient)
    │
    ├─ Step 1: ED Triage (8201)
    │  POST /predict → acuity level (1-5), disposition, risk factors
    │
    ├─ Step 2: ED Flow (8214)
    │  POST /patients/{sid}/event {type:"triage"}
    │  → MTS category (weighted random from acuity)
    │  → PET breach risk, LWBS risk
    │
    ├─ Step 3: Bed Management (8208)
    │  POST /predict-discharge → probability, predicted time
    │  POST /allocate → assigns best bed (multi-objective scoring)
    │  POST /notify-bed-allocated → ED Flow (8214) updates to "boarding"
    │  POST /notify-discharge-prediction → Hospital Ops (8203)
    │
    ├─ Step 4: Oncology AI (8204) [conditional: if cancer ICD]
    │  POST /predict-risk → readmission/mortality risk
    │  → Discharge Lounge (8221) flagged if readmission risk > 0.7
    │
    ├─ Step 5: Deterioration (8220) [continuous]
    │  POST /deterioration/screen → NEWS2 score
    │  → Escalate if NEWS2 ≥ 5 (urgent) or ≥ 7 (continuous monitoring)
    │  → ICU transfer triggered via Digital Twin if NEWS2 ≥ 7
    │
    └─ Step 6: Clinical Scribe (8210)
       POST /generate-note → auto-documentation

Each module can be enabled/disabled at runtime via Digital Twin config.
```

### 4.3 Per-Event Flows

**Vital Sign:**
```
SimEngine → MongoDB → DigitalTwin.process_vital()
  → Deterioration (8220): POST /deterioration/screen (NEWS2 re-score)
    → If NEWS2 ≥ 5: Debouncer gates alert → EventBus "deterioration_alert"
  → ED Flow: POST /patients/{sid}/event {type:"treatment", vital, value}
  → Bed Mgmt: POST /predict-discharge (re-prediction with new vitals)
```

**Transfer:**
```
SimEngine → MongoDB → DigitalTwin.process_transfer()
  → ED Flow: POST /patients/{sid}/event {type:"transfer", to_department}
    If non-ED department → patient status = "discharged" from ED
  → Bed Mgmt: POST /notify-transfer
    Frees source bed, allocates target bed
    Maps MIMIC dept → Irish dept via map_department()
```

**Discharge:**
```
SimEngine → MongoDB → DigitalTwin.process_discharge()
  → Discharge Lounge (8221): POST /discharge-plan (generate care plan)
  → ED Flow: POST /patients/{sid}/event {type:"discharged"}
  → Bed Mgmt: POST /notify-discharge/{hadm_id}
    Frees bed (substring match for SIM-prefixed IDs)
  → EventBus: publish "patient_discharged"
  → GDPR (8217): Audit log written
```

---

## 5. Cross-Module Integration

### 5.1 Bed Management ↔ Hospital Ops Loop

```
Bed Management (8208)
    │
    ├── On /beds/summary poll:
    │   → POST /notify-census → Hospital Ops (8203)
    │     DES engine auto-steps, census synced
    │
    ├── On department occupancy ≥ 75%:
    │   → POST /notify-capacity-alert → Hospital Ops (8203)
    │     MARL agent makes staffing decision:
    │       1. Reset to ERP baseline
    │       2. Apply MARL action (clamped ±50% of baseline)
    │       3. Log action with timestamp + observation
    │
    ├── On /allocate:
    │   → Fetch staffing from Hospital Ops /staffing-recommendations
    │   → Score beds: +0.1 if well-staffed, -0.1 if understaffed
    │
    └── Discharge predictions forwarded:
        → Digital Twin → POST /notify-discharge-prediction → Hospital Ops
```

### 5.2 MARL Staffing Loop

```
Trained MADDPG Model (final_model.pt)
    │ Loaded at Hospital Ops startup
    ▼
On capacity alert (amber/red/black):
    │
    ├── Reset department to ERP baseline staffing
    ├── Build observation vector (12-dim: occupancy, wait, staffing ratio, ...)
    ├── MARL select_actions() → [doc_adj, nurse_adj, priority, threshold]
    ├── Clamp to ±50% of ERP baseline
    ├── Apply via DES engine apply_actions()
    ├── Log action: "MARL: +2 doctors, -1 nurse to ICU"
    └── Return staffing_after to Bed Management
```

### 5.3 Deterioration Cascade (NEWS2 / PEWS / IMEWS → Escalation)

The deterioration service routes a patient to the right early-warning score
based on age and pregnancy status, applies trended analysis and a
score-aware debouncer, and runs an SBAR-captured acknowledgement loop.

```
Deterioration Monitor (8220)
    │
    ├── POST /deterioration/screen (vitals input)
    │   │
    │   ├── age < 16y                → /deterioration/pews  (NCEC NCG #1)
    │   ├── pregnant or PP ≤ 42d     → /deterioration/imews (NCEC NCG #4)
    │   └── otherwise                → /deterioration/screen (NEWS2)
    │
    │   Each endpoint:
    │     • computes the score
    │     • persists snapshot to MongoDB (MIMIC_SIM.deterioration_*)
    │     • runs trend analysis over trailing 4 h (NEWS2 only)
    │     • consults ScoreAwareDebouncer(cooldown=300s, rise_threshold=1)
    │
    ├── Debouncer behaviour:
    │     • fires immediately on rising score (Δ ≥ 1)
    │     • suppresses repeats only when score stable/declining within 5 min
    │     • seeded from persisted state on startup — survives restart
    │
    ├── Escalation decision (per scoring system):
    │     • NEWS2  ≥ 5 or any param=3 or trend=rising  → escalate
    │     • PEWS   ≥ 3 or any param=3                  → escalate
    │     • IMEWS  any pink OR ≥ 2 yellow              → escalate
    │
    ├── Escalation side-effects (_escalate_internal):
    │     • Clinical Chat  → /context-inject (audit trail)
    │     • Bed Mgmt       → /escalate-bed-priority (NEWS2 ≥ 7, IMEWS ≥ 2 pink,
    │                         PEWS ≥ 5)
    │     • Hospital Ops   → /notify-capacity-alert (urgency red/amber)
    │     • persist escalation record with escalation_id (uuid4)
    │
    └── Acknowledgement loop — POST /deterioration/acknowledge
          • clinician identity (role: NCHD/Registrar/Consultant)
          • SBAR (situation/background/assessment/recommendation)
          • outcome: reviewed | escalated_further | cco_called | transferred_icu
          • time_to_ack_seconds computed and surfaced in /stats
```

### 5.4 Simulation Reset Flow

```
POST /reset (Data Ingestion 8207)
    │
    ├── Stop simulation engine
    ├── Clear MongoDB MIMIC_SIM collections
    ├── Clear in-memory state + metrics history
    ├── Reset Digital Twin (clear patient contexts)
    ├── POST /reset → ED Flow (8214) — clear ed_patients
    ├── POST /reset → Bed Management (8208) — reinitialize all beds
    ├── POST /reset → Deterioration (8220) — clear active alerts
    ├── POST /reset-integration → Hospital Ops (8203) — clear census/alerts/logs
    └── Reinitialize patient pool (500 patients)
```

---

## 6. Department Name Mapping

All simulation data originates from MIMIC-IV which uses ~30 different department names.
These are mapped to 14 Irish HSE departments at the **backend boundary**:

```
MIMIC Name                                    → Irish Name
──────────────────────────────────────────────────────────
"Emergency Department"                        → ED
"Emergency Department Observation"            → CDU
"Medical Intensive Care Unit (MICU)"          → ICU
"Surgical Intensive Care Unit (SICU)"         → ICU
"Cardiac Vascular Intensive Care Unit (CVICU)"→ ICU
"Trauma SICU (TSICU)"                        → ICU
"Coronary Care Unit (CCU)"                   → Cardiology
"Medicine/Cardiology"                        → Cardiology
"Medicine"                                   → Medicine
"Neurology"                                  → AMAU
"Surgery"                                    → Surgery
"Cardiac Surgery"                            → Surgery
"Vascular"                                   → Orthopaedics
"Med/Surg"                                   → SAU
"Neuro Intermediate"                         → HDU
"Discharge Lounge"                           → Discharge_Lounge
... (30+ mappings in shared/constants/hospital.py)
```

All downstream consumers (dashboard, Hospital Ops, Bed Management) receive
Irish names — no MIMIC names leak to the UI.

---

## 7. Dashboard Pages & Data Sources

| Page | API Endpoint(s) | Poll Interval | Key Data |
|------|-----------------|---------------|----------|
| **Overview** | `/sim/stats-dashboard`, `/sim/ed-board`, `/sim/icu-board`, `/sim/department-census` | 5s | KPIs, department distribution, patient lists |
| **ED Triage** | `/sim/ed-board` | 3s | All patients with acuity, vitals, wait times, Irish dept names |
| **ED Flow** | `/ed-flow/ed-state`, `/ed-flow/forecast/arrivals`, `/ed-flow/ed-state/bottlenecks`, `/ed-flow/recommendations` | 10s | PET compliance, NEDOCS, MTS distribution, forecasts |
| **Sepsis ICU** | `/sim/icu-board`, `/beds/beds/summary`, `/erp/staff/ICU` | 5s/10s | ICU+HDU patients (max 20), SOFA scores, bed pressure, ERP staffing |
| **Hospital Ops** | `/beds/beds/summary`, `/ops/staffing-recommendations`, `/sim/metrics-history`, `/sim/arrival-patterns`, `/ops/action-log` | 10-30s | Wait/throughput charts, MARL staffing, 7-day schedule, arrival heatmap, action log |
| **Bed Management** | `/beds/beds/summary`, `/beds/forecast/{dept}`, `/beds/predict-discharge` | Manual + refresh | Department occupancy, capacity forecast, discharge prediction |
| **Oncology** | `/sim/oncology-board`, `/onco/predict-risk` | 5s | Cancer patients, risk assessment |
| **Patient Journey** | `/sim/patient/{hadm}/journey`, `/journey/patient/{sid}/admission/{hadm}/*` | On demand | Timeline, vitals, labs, medications |
| **Waiting List** | `/waitlist/*` | On demand | Priority scoring, NLP referral triage |
| **Clinical Scribe** | `/scribe/*` | On demand | Note generation, ICD coding, NER |
| **Deterioration** | `/deterioration/active-alerts`, `/deterioration/history/{id}` | 5s | NEWS2 alerts, escalation status, score history |
| **GDPR Compliance** | `/gdpr/sar/{id}`, `/gdpr/ropa`, `/gdpr/dpia/{module}` | On demand | Subject access requests, audit records, DPIAs |
| **FHIR Gateway** | `/fhir/Patient/{id}`, `/fhir/Encounter/{id}`, `/fhir/Observation/{id}` | On demand | FHIR R4 resources for national EHR exchange |
| **Discharge Lounge** | `/patient/{id}/discharge-plan`, `/follow-up` | On demand | Discharge plans, readmission risk, follow-up bookings |
| **Hospital ERP** | `/erp/departments`, `/erp/staff`, `/erp/schedule`, `/erp/beds`, `/erp/config` | On mount | Master data (departments, staff rosters, bed config, schedules) |
| **System Admin** | `/sim/state`, `/sim/digital-twin/config`, `/sim/digital-twin/state` | 2s + WebSocket | Sim control, Digital Twin pipeline toggles, module health |

---

## 8. ML Models

| Model | Module | Algorithm | Input | Output | AUROC / F1 | Fallback |
|-------|--------|-----------|-------|--------|------------|----------|
| ED Acuity | app_01 | XGBoost | 50+ vitals + labs + demographics + missingness indicators | ESI 1-5, confidence, probabilities, ED LOS estimate | AUROC 0.728 · Wt-F1 0.653 | Rule-based acuity thresholds |
| ED Acuity (NN) | app_01 | PyTorch feed-forward | Same as above | ESI 1-5, probabilities | Wt-F1 0.577 | Falls back to XGBoost |
| Sepsis Risk | app_02 | LightGBM | 4-hourly time window: vitals + labs + SOFA components | Binary risk, alert level (RED/ORANGE/YELLOW/GREEN) | AUROC 0.994 | SOFA component functions |
| Sepsis Risk (TS) | app_02 | LSTM-Attention (PyTorch) | Same as above, sequential windows | Risk score + attention weights | AUROC 0.998 | LightGBM fallback |
| 30d Readmission | app_04 | XGBoost | 16 features: age, stage proxy, treatments, comorbidities, Charlson | Readmission probability | AUROC 0.734 | Weighted risk formula |
| Hospital Mortality | app_04 | XGBoost | Same feature set | Mortality probability | AUROC 0.897 | Weighted risk formula |
| Readmission (Transformer) | app_04 | PyTorch Transformer | Same feature set | Readmission probability | AUROC 0.733 | XGBoost fallback |
| Mortality (Transformer) | app_04 | PyTorch Transformer | Same feature set | Mortality probability | AUROC 0.876 | XGBoost fallback |
| Discharge 24h | app_08 | XGBoost | LOS + vitals + labs + barriers | Discharge probability, predicted time | — | Department LOS baseline |
| LOS Regressor | app_08 | XGBoost | Same as discharge | Remaining hours | — | LOS baseline |
| Capacity Forecast | app_08 | Rule-based | Current census + diurnal pattern | 4/8/12/24/48/72h forecast | — | Exponential convergence to 80% |
| MADDPG Staffing | app_03 | MADDPG (PyTorch) | 12-dim observation per dept | Staff adjustments (±50% ERP baseline) | — | Rule-based (+1 doc/+2 nurse) |
| NEWS2 Scoring | app_20 | Clinical rule (shared) | RR, SpO2, temp, SBP, HR, consciousness | Score 0-20 + risk band | — | Direct parameter scoring |
| MTS Classification | app_14 | Weighted random | Triage acuity (1-5) | MTS category (1-5) | — | Direct mapping |

---

## 9. Compliance & Regulatory Standards

### 9.1 Irish Healthcare Standards

| Standard | Implementation | Module |
|----------|---------------|--------|
| **PET 6-hour target** | `PET_TARGET_HOURS = 6` — tracked per ED patient | ED Flow (8214) |
| **Manchester Triage (MTS)** | 5-level triage with target response times | ED Flow, ED Triage |
| **NEDOCS crowding** | Thresholds: normal(100), busy(140), crowded(180), severe(200) | ED Flow (8214) |
| **NEWS2 deterioration** | Full iNEWS2 scoring + 4-band escalation protocol | Deterioration (8220) |
| **EWTD compliance** | 48h/week max working hours | Hospital ERP (8215) |
| **12-hour shifts** | Nursing 07:00-19:00/19:00-07:00, NCHD 08:00-20:00/20:00-08:00 | Hospital ERP, Hospital Ops |
| **Weekend reduction** | 70-80% of weekday staffing | Hospital ERP |
| **1:1 ICU nursing** | 12 nurses for 12 ICU beds on day shift | Hospital ERP |
| **Trolley tracking** | `TrolleyMetrics` class (INMO-compatible) | Trolley Watch (8216) |
| **HSE alert levels** | Green (<75%), Amber (75-85%), Red (85-95%), Black (≥95%) | Bed Management (8208) |

### 9.2 GDPR Compliance (app_17)

All services embed `PrivacyNotice` (GDPR Art. 13/14) via `shared/api/base.py`.

| Article | Implementation |
|---------|---------------|
| Art. 9(2)(h) — Special Category Health Data | Applied to all clinical endpoints; lawful basis documented |
| Art. 13/14 — Transparency | `PrivacyNotice` dataclass embedded in every service's `/docs` |
| Art. 15 — Subject Access Request | `GET /gdpr/sar/{patient_id}` exports all patient data |
| Art. 17 — Right to Erasure | `POST /gdpr/purge/{patient_id}` with tombstone audit record |
| Art. 30 — Record of Processing Activities | `GET /gdpr/ropa` — full ROPA per module |
| Art. 33 — Breach Notification | `POST /gdpr/breach` — 72h reporting workflow |
| Art. 35 — DPIA | `GET /gdpr/dpia/{module}` — risk assessment per AI module |

DPIA risk classifications: all prediction modules rated **HIGH** (Art. 9 health data). Mitigating measures: clinician override, explainability (XAI 8218), drift monitoring.

### 9.3 EU AI Act Compliance

`AIActInfo` dataclass (shared/api/base.py) embedded in prediction services:
- **Art. 13 — Transparency**: model type, training data source, limitations disclosed
- **Art. 14 — Human Oversight**: all decisions advisory; clinician override enforced
- **Art. 15 — Accuracy/Robustness**: AUROC metrics published at `/model-info`
- **High-risk classification**: ED Triage, Sepsis ICU, Oncology AI flagged as high-risk AI systems

### 9.4 HL7 FHIR R4 Gateway (app_19)

Endpoints conform to FHIR R4 specification for HSE national EHR interoperability:
- `GET /fhir/CapabilityStatement` — server capabilities manifest
- `GET /fhir/Patient/{id}` — FHIR Patient resource (pseudonymized in simulation mode)
- `GET /fhir/Encounter/{id}` — FHIR Encounter resource
- `GET /fhir/Observation/{id}` — Lab/vital FHIR Observations

Identifiers are pseudonymized (`SIM-<hash>`) in simulation mode to prevent accidental PHI exposure. Production mode requires `TLS_CERT_PATH` + `FERNET_KEY`.

---

## 10. Shared Libraries

```
shared/
├── api/
│   └── base.py              create_app() factory, BaseResponse, PrivacyNotice,
│                            AIActInfo, CORS, timing, security_check(), @require_role()
├── constants/
│   ├── mimic.py             MIMIC-IV vital/lab item ID mappings, risk color hex codes
│   └── hospital.py          Hospital master data + map_department() (single source)
├── clinical/
│   ├── risk.py              SOFA scoring, acuity calc, gender encoding, vital risk factors
│   ├── news2.py             NEWS2 (7 components, 4 risk bands) + compute_news2_trend()
│   │                        (slope over trailing window; rising/falling/stable classifier)
│   ├── pews.py              PEWS — age-banded paediatric (NCEC NCG #1); 5 age bands
│   │                        (0-3mo, 3-12mo, 1-4y, 5-11y, 12-17y) with behaviour,
│   │                        respiratory-effort, capillary-refill scoring
│   ├── imews.py             IMEWS — maternal (NCEC NCG #4); yellow/pink trigger system
│   │                        with proteinuria, liquor, lochia; gestational_context
│   ├── keywords.py          Medication/symptom/cancer term lists for NLP
│   ├── icd_names.py         ICD-9-CM & ICD-10 human-readable name lookup
│   ├── plain_language.py    Medical → patient-friendly term translation
│   ├── clinical_record.py   ClinicalRecord dataclass (aggregates all patient data)
│   └── safeguarding.py      Abuse/neglect/mental health crisis alert detection
├── db/
│   ├── mongo.py             MongoManager (lazy connection, 3 databases)
│   ├── queries.py           Domain-specific MongoDB queries
│   └── pipelines.py         Reusable aggregation pipelines
├── ml/
│   ├── registry.py          ModelRegistry (joblib/torch/pkl + JSON metadata sidecars)
│   ├── training_base.py     Abstract TrainerBase (fit, predict, evaluate, save)
│   ├── preprocessing.py     Feature scaling, imputation, missingness indicators
│   ├── evaluation.py        AUROC, F1, calibration curves, confusion matrices
│   └── explainability.py    SHAP feature importance, LIME explanations
├── integration/
│   ├── digital_twin.py      Patient event orchestrator (forwards Idempotency-Key
│   │                        on cross-service POSTs; _safe_call + health tracking)
│   ├── service_client.py    HTTP service discovery + client pool (18 services);
│   │                        post()/patch() accept optional idempotency_key
│   ├── circuit_breaker.py   CircuitBreaker (closed/open/half-open states)
│   ├── event_bus.py         In-process pub/sub (15 topics) + optional MongoDB
│   │                        persistence + replay_missed_events() for startup rehydrate
│   ├── idempotency.py       IdempotencyCache + idempotent_post() FastAPI dependency;
│   │                        install_idempotency_middleware() captures responses
│   ├── sim_clock.py         Simulation-mode clock (sync with system time on reset)
│   ├── debouncer.py         CensusDebouncer (time+payload digest) + GenericDebouncer
│   │                        + ScoreAwareDebouncer (fires on rise, suppresses on
│   │                        stable/decline — prevents NEWS2 5→7 suppression bug)
│   └── conversation_buffer.py  Sliding-window LLM chat history
└── utils/
    ├── datetime.py          MIMIC datetime parser, time-window helpers
    ├── logging.py           Structured JSON logging (structlog, correlation IDs)
    └── crypto.py            Fernet encryption for PHI, hash-based pseudonymization
```

---

## 11. Persistence & State

| Data | Storage | Survives Restart? |
|------|---------|-------------------|
| MIMIC patient pool | MongoDB `mimic` (read-only, 26 collections) | Yes |
| ICU chartevents | MongoDB `mimic_icu` (read-only, 5 collections) | Yes |
| Clinical notes | MongoDB `mimic_notes` (read-only, 331K summaries) | Yes |
| Simulation events | MongoDB `MIMIC_SIM` | Yes (until reset) |
| Bed allocations | In-memory (app_08) | No (reinitialized on start) |
| ED Flow patients | In-memory (app_14) | No (rebuilt from DT events) |
| Hospital Ops DES | In-memory (app_03) | No (auto-created on census sync) |
| NEWS2 active alerts | In-memory (app_20) | No (rebuilt from DT vital events) |
| Discharge plans | In-memory (app_21) | No |
| GDPR audit log | MongoDB `gdpr.audit_log` | Yes |
| MARL model | File (`models/hospital_ops/final_model.pt`) | Yes |
| Action log | In-memory + MongoDB `hospital_ops.action_log` | Partial |
| Metrics history | In-memory (Data Ingestion) | No (rebuilt as sim runs) |
| ERP master data | Static Python dicts (app_15) | Yes (code-defined) |
| ML models | Files (`models/*/`), loaded at startup via ModelRegistry | Yes |

---

## 12. Key Integration Patterns

### Pattern 1: HTTP Cross-Process Notification
EventBus is in-process only. Cross-service communication uses HTTP:
```python
# Bed Management notifies Hospital Ops
await client.hospital_ops.post("/notify-capacity-alert", {...})
# Digital Twin notifies Deterioration
await client.deterioration.post("/deterioration/screen", vitals_dict)
```

### Pattern 2: Shared Constants Import
All modules import from `shared/constants/hospital.py`:
```python
from shared.constants.hospital import DEPARTMENTS, CAPACITIES, map_department
```

### Pattern 3: Circuit Breaker (Graceful Degradation)
`CircuitBreaker` in `shared/integration/circuit_breaker.py` wraps every inter-service call:
- **Closed**: requests pass through normally
- **Open**: requests blocked after N failures — returns `{"status": "error", "circuit": "open"}`
- **Half-open**: one trial request; success closes, failure re-opens

```python
# ServiceClient wraps each module client with a CircuitBreaker
async def post(self, path, data):
    if self.breaker.is_open():
        return {"status": "error", "circuit": "open"}
    try:
        return await self._client.post(path, json=data)
    except Exception:
        self.breaker.record_failure()
```

### Pattern 4: ML Model Fallback
Every ML prediction has a rule-based fallback:
```python
if model is not None:
    prediction = model.predict(features)
else:
    prediction = rule_based_fallback(inputs)
```

### Pattern 5: MARL Staffing Clamping
MARL adjustments are bounded by ERP baseline:
```python
dept.staff.doctors = max(
    int(baseline * 0.5),        # floor: 50% of ERP
    min(int(baseline * 1.5),    # ceiling: 150% of ERP
        dept.staff.doctors + delta)
)
```

### Pattern 6: Score-Aware Alert Debouncing
`ScoreAwareDebouncer` in `shared/integration/debouncer.py` prevents alert fatigue
*without* masking deterioration. A pure time-only debouncer would suppress a
NEWS2 jump from 5 → 7 because the 5-minute cooldown hasn't elapsed — clinically
unsafe. The score-aware debouncer fires immediately on a rising score:
```python
deb = ScoreAwareDebouncer(cooldown_s=300, rise_threshold=1)
# First observation — fires
await deb.should_fire(hadm_id, score=5)   # → True
# 2 min later, stable — suppressed
await deb.should_fire(hadm_id, score=5)   # → False
# 2 min later, rising — fires immediately despite cooldown
await deb.should_fire(hadm_id, score=7)   # → True
```
On service restart, the debouncer is seeded from persisted state (`deb.record(...)`)
so the restart doesn't trigger an alert storm on the first post-restart observation.

### Pattern 7: Deployment Mode Guard
Security behaviour differs by deployment mode:
```python
# Simulation mode: JWT defaults to "researcher", checks emit warnings
# Production mode: TLS + Fernet key + MongoDB auth mandatory, startup fails otherwise
DEPLOYMENT_MODE = os.getenv("DEPLOYMENT_MODE", "simulation")
```

### Pattern 8: Idempotent Cross-Service Notifications
`IdempotencyCache` + `idempotent_post()` in `shared/integration/idempotency.py`
prevent duplicate side effects when a circuit breaker half-opens and retries,
or when Digital Twin re-dispatches on a transient failure. Callers send an
`Idempotency-Key` header; the receiving service deduplicates within a TTL
window (default 10 min).
```python
# Server side (e.g. Bed Management /notify-discharge)
from shared.integration.idempotency import IdempotencyCache, idempotent_post, install_idempotency_middleware
_cache = IdempotencyCache(ttl_seconds=600)
install_idempotency_middleware(app, _cache)

@app.post("/notify-discharge/{hadm_id}")
async def notify_discharge(hadm_id, _=Depends(idempotent_post(_cache))):
    ...

# Client side — Digital Twin discharge cascade
await self._safe_call(
    "bed_management", "POST", f"/notify-discharge/{hadm_id}", {},
    idempotency_key=f"discharge:{hadm_id}",
)
```
Concurrent duplicate requests with the same key collapse into a single
handler execution — the second caller awaits the first's response rather
than re-running the handler.

### Pattern 9: Trended Early-Warning Analysis
`compute_news2_trend()` in `shared/clinical/news2.py` computes a least-squares
slope over a trailing window of NEWS2 scores and classifies the trajectory.
This is clinically important because a NEWS2 of 5 rising from 2 an hour ago
is far more concerning than a static NEWS2 of 5.
```python
trend = compute_news2_trend(history, window_minutes=240)
# trend.trajectory ∈ {"rising", "falling", "stable", "insufficient_data"}
# trend.slope_per_hour (float), trend.is_clinically_rising (bool — slope > 1.0 or Δ ≥ 2)
```
The deterioration service uses `is_clinically_rising` as an additional
escalation trigger on top of raw-score thresholds.

### Pattern 10: Bed Taxonomy for Clinical Suitability
`BED_CATEGORIES` in `shared/constants/hospital.py` adds an orthogonal
clinical-suitability dimension to department type. HIQA expects ring-fenced
isolation-bed tracking, NCEC safety standards require explicit paediatric /
maternity beds, and the National Stroke Programme mandates thrombolysis-capable
identification.
```python
# Each bed carries a .category in addition to .bed_type
# {general, isolation, bariatric, paediatric, maternity,
#  stroke_thrombolysis, cardiac_monitored, observation,
#  critical, high_dependency, day_case}

# Allocation scoring hard-refuses clinically incompatible matches
if req.requires_isolation and category not in ISOLATION_COMPATIBLE:
    return 0.0  # infectious patient on general ward = hard NO
```
`GET /beds/categories` exposes per-category availability for the dashboard.

---

## 13. Deployment Modes

### Simulation Mode (default)
- `DEPLOYMENT_MODE` not set or set to `"simulation"`
- No authentication required; JWT role defaults to `"researcher"`
- Patient identifiers pseudonymized (`SIM-<hash>`) — no real PHI
- Security checks produce warnings only (non-blocking)
- Uses MIMIC-IV research data under PhysioNet credentialed access

### Production Mode
| Requirement | Environment Variable | Enforcement |
|-------------|---------------------|-------------|
| Mutual TLS | `TLS_CERT_PATH`, `TLS_KEY_PATH` | Startup fails if missing |
| Field-level encryption | `FERNET_KEY` | Startup fails if missing |
| MongoDB auth | `MONGO_URI` with `authSource=admin` | Startup fails if missing |
| RBAC | JWT `role` claim validated on protected routes | 403 if role mismatch |
| Audit logging | All data access written to `gdpr.audit_log` | Automatic |
