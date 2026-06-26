# Module 08: Real-Time Bed Management & Discharge Prediction
## System Design Document

---

## 1. Executive Summary

This module provides real-time hospital bed occupancy tracking, ML-powered discharge prediction, automated bed allocation optimization, and capacity forecasting for Irish hospitals. It directly addresses Ireland's trolley crisis (25,290+ trolley patients in 2 months) and aligns with the HSE's "Demand & Capacity Visualization Platform" priority.

**Port:** 8208
**Status:** New Module
**Integration:** Consumes from ED Triage (08201), Hospital Ops (8203), Patient Journey (8205), Sepsis ICU (8202). Publishes to Hospital Ops, Clinical Chat (8206), ED Flow (8214).

---

## 2. Market Research: Similar Products

### 2.1 Qventus (US) — Most Direct Competitor
- **What:** AI-powered hospital operations platform; flagship = inpatient discharge optimization
- **Technology:** Proprietary ML models integrated with EHR (Epic, Cerner); real-time data feeds
- **Results:** 20-35% reduction in excess patient days; up to 1-day LOS reduction; $5-15M annual savings per hospital
- **Pricing:** Enterprise SaaS (~$1-3M/year for large hospitals)
- **Strengths:** Deep EHR integration, proven ROI, strong clinical evidence
- **Weaknesses:** US-focused, expensive, no Irish/EU deployment, vendor lock-in
- **Gap vs. Our Module:** No open architecture; no FHIR-native design; no Irish hospital profiles

### 2.2 TeleTracking (US/UK)
- **What:** Real-time bed management and patient flow platform; Care Coordination Centres
- **Technology:** RFID/RTLS patient tracking, rule-based bed assignment, dashboard analytics
- **Results:** 1,000+ hospitals globally; Medway NHS Trust: electronic bed management + care coordination
- **Pricing:** Per-bed licensing model
- **Strengths:** Mature product, NHS presence, hardware integration (RTLS)
- **Weaknesses:** Rule-based (not ML), limited predictive capability, hardware dependency
- **Gap vs. Our Module:** No ML-based discharge prediction; no capacity forecasting

### 2.3 GE Healthcare Command Centre
- **What:** Hospital "nerve center" with real-time analytics; tile-based operations wall
- **Technology:** AI tiles for specific workflows (patient flow, OR, transport, bed management)
- **Results:** Johns Hopkins: 35% ED wait reduction, 78% improvement in sick patient access; Bradford NHS Trust (first UK deployment)
- **Pricing:** Enterprise (>$2M implementation + annual license)
- **Strengths:** Comprehensive visualization, proven at scale, AI tiles modular
- **Weaknesses:** Very expensive, heavy implementation, GE ecosystem dependency
- **Gap vs. Our Module:** Not FHIR-native; no open API; limited customization for Irish context

### 2.4 Epic Capacity Management (Rover, Capacity IQ)
- **What:** Built-in bed management within Epic EHR; real-time bed board, patient list management
- **Technology:** Native Epic platform; rules-based + some ML for discharge prediction
- **Results:** Available to all Epic customers; tight EHR integration
- **Strengths:** Seamless EHR integration, no additional vendor
- **Weaknesses:** Epic-only; limited ML sophistication; expensive Epic ecosystem
- **Gap vs. Our Module:** EHR-locked; not available to non-Epic hospitals

### 2.5 Oracle Health (Cerner) Capacity Management
- **What:** Bed management within Cerner Millennium; CareAware capacity management
- **Technology:** Rule-based bed assignment, dashboard reporting
- **Strengths:** Large installed base
- **Weaknesses:** Legacy architecture; limited AI/ML; Oracle transition uncertainty
- **Gap vs. Our Module:** No ML prediction; aging platform

---

## 3. SWOT Analysis

### Strengths
- **Integrated platform:** Only solution combining bed management with ED triage, sepsis, oncology, and patient journey in one system
- **Advanced ML:** Temporal Fusion Transformer + survival analysis vs. competitors' rule-based approaches
- **Open architecture:** FastAPI microservices, FHIR-native, EHR-agnostic
- **Irish-specific:** Designed for HSE metrics (trolley count, PET, ALOS), Irish department structures (MAU, AMAU, SAU, CDU)
- **Cost-effective:** 10-20x cheaper than Qventus/GE Command Centre
- **MIMIC-IV foundation:** 431K admissions, 1.56M transfers for model training

### Weaknesses
- **No Irish validation data yet:** Models trained on US MIMIC-IV data; need Irish hospital validation
- **No RTLS integration:** TeleTracking has hardware tracking; we're software-only
- **No EHR integration yet:** FHIR layer planned but not built
- **Single-site focus:** Not yet designed for multi-hospital health region coordination
- **New entrant:** No clinical evidence base or published validation studies
- **Small team:** Resource constraints vs. well-funded competitors

### Opportunities
- **EUR 263M HSE digital budget (2026):** Demand & Capacity Visualization is a funded priority
- **National EHR procurement:** FHIR-native design positions us for integration with any EHR vendor
- **No incumbent:** No AI bed management deployed in Irish public hospitals
- **Trolley crisis urgency:** Political pressure creates procurement urgency
- **HIHI pathway:** Health Innovation Hub Ireland provides structured pilot access
- **EU AI Act compliance:** Early compliance creates competitive moat vs. US vendors

### Threats
- **Qventus EU expansion:** Well-funded ($300M+) and could enter Irish market
- **EHR vendor bundling:** Epic/Oracle may enhance built-in capacity management with AI
- **GE Command Centre NHS expansion:** Could cross into Irish market from UK NHS
- **Data access barriers:** Hospitals may resist sharing real-time bed data
- **Regulatory delays:** EU AI Act compliance (August 2026) could slow deployment
- **Clinical adoption resistance:** Clinicians may distrust ML discharge predictions

---

## 4. Gap Identification

| Gap Area | Current Market State | Our Opportunity |
|----------|---------------------|-----------------|
| **ML discharge prediction** | Qventus (proprietary), others rule-based | Open-source TFT + DeepSurv approach |
| **Irish hospital profiles** | No vendor has Irish-specific department structures | MAU/AMAU/SAU/CDU modeling |
| **Real-time capacity forecasting** | GE Command Centre (tiles), limited ML | 4/8/12/24/48/72-hour forecasting with uncertainty |
| **Trolley tracking** | Manual INMO counts | Automated trolley wait prediction |
| **Cross-module intelligence** | All competitors are standalone | ED Triage → Bed needs; Sepsis → ICU demand |
| **FHIR-native** | Retrofit FHIR on legacy; Epic native only | Built from ground up on FHIR R4 |
| **Multi-site coordination** | TeleTracking (basic), GE (limited) | Health Region-level capacity balancing |
| **Discharge readiness scoring** | Qventus (best), others basic | Multi-signal fusion: vitals + labs + clinical progress |
| **Equity-aware allocation** | None address fairness | Fairness constraints in bed allocation optimization |

---

## 5. Peer-Reviewed Research & Algorithms

### 5.1 Key Papers

#### Discharge Prediction
1. **Rajkomar et al. (2018)** "Scalable and accurate deep learning with electronic health records" — *npj Digital Medicine*
   - EHR-wide deep learning; discharge prediction AUROC 0.86 at 24h before discharge
   - Architecture: LSTM + attention over longitudinal EHR sequences
   - DOI: 10.1038/s41746-018-0029-1

2. **Harutyunyan et al. (2019)** "Multitask learning and benchmarking with clinical time series" — *Scientific Data*
   - MIMIC-III benchmarks including LOS prediction, in-hospital mortality, phenotyping
   - Architecture: LSTM, channel-wise attention; LOS Kappa=0.40 (state-of-art on MIMIC)
   - DOI: 10.1038/s41597-019-0103-9

3. **Lim et al. (2021)** "Temporal Fusion Transformers for Interpretable Multi-horizon Time Series Forecasting" — *International Journal of Forecasting*
   - TFT architecture: multi-head attention + variable selection + quantile regression
   - State-of-art for multi-horizon forecasting with interpretability
   - DOI: 10.1016/j.ijforecast.2021.03.012

4. **Elbattah et al. (2022)** "Predicting hospital length of stay using machine learning on MIMIC-IV" — *IEEE ICHI*
   - XGBoost, Random Forest, neural nets on MIMIC-IV for LOS prediction
   - XGBoost: AUROC 0.82 for >7-day LOS prediction
   - Key features: diagnosis codes, admission type, age, prior admissions

5. **Wornow et al. (2023)** "EHRSHOT: An EHR Benchmark for Few-Shot Evaluation of Foundation Models" — *NeurIPS 2023 Datasets & Benchmarks*
   - Foundation model evaluation on EHR tasks including discharge prediction
   - CLMBR-t-base: AUROC 0.89 for long LOS prediction
   - DOI: 10.48550/arXiv.2307.02028

#### Bed Management & Capacity Forecasting
6. **Zhu et al. (2022)** "Hospital bed planning with discrete event simulation and optimization" — *Health Care Management Science*
   - DES + Integer Linear Programming for bed allocation
   - 15% reduction in bed shortages, 20% improvement in utilization
   - Key technique: Stochastic optimization with demand uncertainty

7. **Bachouch et al. (2023)** "Hospital bed management: A critical review and future research directions" — *Computers & Industrial Engineering*
   - Comprehensive survey of OR/ML methods for bed management
   - Key finding: Hybrid DES+ML outperforms pure ML or pure simulation
   - Recommended: Multi-objective optimization balancing throughput, equity, and cost

8. **Spaeder et al. (2023)** "Machine Learning Applied to Real-Time Hospital Census Prediction" — *Journal of Hospital Medicine*
   - XGBoost + ARIMA hybrid for 24h census prediction
   - MAE of 4.2 beds for 800-bed hospital (0.5% error)
   - Features: current census, day of week, scheduled admissions, historical patterns

#### Survival Analysis for Discharge
9. **Katzman et al. (2018)** "DeepSurv: Personalized treatment recommender system using a Cox proportional hazards deep neural network" — *BMC Medical Research Methodology*
   - Deep neural network extending Cox-PH; non-linear risk function
   - Concordance index 0.76 on clinical data
   - DOI: 10.1186/s12874-018-0482-1

10. **Lee et al. (2020)** "Dynamic-DeepHit: A Deep Learning Approach for Dynamic Survival Analysis" — *IEEE TPAMI*
    - Dynamic survival prediction with competing risks; handles time-varying covariates
    - Concordance index 0.81 on MIMIC-III discharge prediction
    - Key advantage: updates predictions as new data arrives (vitals, labs)
    - DOI: 10.1109/TPAMI.2020.3040625

### 5.2 Adopted Algorithms (State-of-the-Art)

#### Primary: Temporal Fusion Transformer (TFT) for Discharge Prediction
**Why chosen:** Best interpretable multi-horizon forecasting model; supports static covariates (demographics, diagnosis), known future inputs (scheduled procedures), and observed time-varying inputs (vitals, labs).

```
Architecture:
├── Variable Selection Networks (per input type)
│   ├── Static covariates: age, gender, admission_type, diagnosis, department
│   ├── Known future: scheduled procedures, planned discharges
│   └── Observed past: vitals, labs, medications, NEWS2 scores
├── Static Enrichment Layer (GRN)
├── Temporal Processing
│   ├── LSTM encoder (past observations)
│   └── LSTM decoder (future horizons: 4h, 8h, 12h, 24h, 48h, 72h)
├── Multi-Head Attention (interpretable: which past events matter)
└── Quantile Output (10th, 50th, 90th percentile discharge times)
```

**Performance targets:** AUROC > 0.85 for 24h discharge prediction; MAE < 4h for discharge time estimation.

#### Secondary: DeepSurv for Time-to-Discharge Survival Analysis
**Why chosen:** Handles censored data (patients still admitted); provides hazard ratios; clinically interpretable as "probability of discharge within next X hours."

```
Architecture:
├── Input: 32 clinical features (static + time-varying at current state)
├── Hidden layers: [128, 64, 32] with SELU activation + batch norm
├── Output: Log-risk (Cox partial likelihood loss)
└── Calibration: Isotonic regression post-hoc
```

#### Tertiary: XGBoost for Discharge Readiness Scoring
**Why chosen:** Fast inference for real-time scoring; high interpretability via SHAP; proven on MIMIC-IV; serves as fallback when temporal data insufficient.

```
Features (42):
├── Demographics: age, gender, admission_type, insurance
├── Current state: department, LOS_so_far, NEWS2_score, SOFA_score
├── Vitals trend: HR/RR/SpO2/SBP/DBP trend (improving/stable/worsening)
├── Labs trend: key labs trend + time since last abnormal
├── Treatment: active medications count, IV_status, oxygen_status
├── Clinical progress: days_since_last_procedure, diet_order_status
└── Context: day_of_week, hour_of_day, season, bed_type
```

#### Capacity Forecasting: ARIMA-XGBoost Hybrid
**Why chosen:** Captures both seasonal patterns (ARIMA) and complex feature interactions (XGBoost) for hospital census prediction.

```
Pipeline:
1. ARIMA(7,1,3) with 24h/168h seasonality → baseline forecast
2. XGBoost residual model with exogenous features:
   ├── Scheduled admissions (elective surgery list)
   ├── ED arrival forecast (from ED Flow module)
   ├── Current discharge predictions (from TFT model)
   ├── Day of week, month, public holidays
   └── Historical demand patterns
3. Ensemble: ARIMA baseline + XGBoost residual correction
4. Uncertainty: Conformal prediction intervals (90% coverage)
```

#### Bed Allocation: Multi-Objective Integer Programming
**Why chosen:** Optimal bed assignment considering multiple constraints simultaneously.

```
Objective: Minimize weighted sum of:
├── Patient wait time (trolley hours)
├── Clinical acuity mismatch (high-acuity in low-dependency bed)
├── Department preference violation (medical patient in surgical bed)
└── Transfer cost (distance, disruption)

Constraints:
├── Department capacity (hard)
├── Infection control isolation (hard)
├── Gender separation (hard where required)
├── Staff-to-patient ratios (hard)
├── Equipment requirements (hard)
└── Fairness: max wait time differential across acuity groups (soft)

Solver: Google OR-Tools CP-SAT for integer programming
         with rolling horizon (re-solve every 15 minutes)
```

---

## 6. System Architecture

### 6.1 High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    Module 08: Bed Management                      │
│                         Port 8208                                 │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────┐  │
│  │  Bed State       │  │ Discharge         │  │ Capacity       │  │
│  │  Engine          │  │ Prediction Engine │  │ Forecast Engine │  │
│  │                  │  │                   │  │                │  │
│  │ - Real-time      │  │ - TFT model       │  │ - ARIMA-XGB    │  │
│  │   census         │  │ - DeepSurv model  │  │   hybrid       │  │
│  │ - Bed board      │  │ - XGBoost scorer  │  │ - Conformal    │  │
│  │ - Trolley track  │  │ - Readiness score │  │   intervals    │  │
│  └────────┬─────────┘  └────────┬──────────┘  └───────┬────────┘  │
│           │                     │                      │          │
│  ┌────────v─────────────────────v──────────────────────v────────┐ │
│  │              Bed Allocation Optimizer                         │ │
│  │  Multi-Objective Integer Programming (OR-Tools CP-SAT)       │ │
│  │  Rolling horizon: re-solve every 15 minutes                  │ │
│  └──────────────────────────┬───────────────────────────────────┘ │
│                              │                                    │
│  ┌──────────────────────────v───────────────────────────────────┐ │
│  │              Event Bus Publisher                               │ │
│  │  Publishes: bed_allocated, discharge_predicted,               │ │
│  │  capacity_alert, trolley_alert                                │ │
│  └───────────────────────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────────┤
│  External Integrations (consumed):                                │
│  ├── ED Triage (8201): incoming patient acuity + disposition     │
│  ├── Sepsis ICU (8202): ICU demand predictions                   │
│  ├── Hospital Ops (8203): department census + simulation         │
│  ├── Patient Journey (8205): current patient state + vitals      │
│  └── ED Flow (8214): arrival forecasts + admission predictions   │
│                                                                   │
│  External Integrations (published to):                            │
│  ├── Hospital Ops (8203): real-time capacity data                │
│  ├── Clinical Chat (8206): bed availability queries              │
│  └── ED Flow (8214): bed availability for disposition planning   │
└──────────────────────────────────────────────────────────────────┘
```

### 6.2 Data Flow

```
MongoDB (MIMIC) ──────────────────────────────────┐
  admissions (431K)                                 │
  transfers (1.56M)                                 │
  chartevents (vitals)                              ├──► Dataset Builder
  labevents (labs)                                  │    (build_dataset.py)
  icustays (73K)                                    │
  services (467K)                                   │
  prescriptions (15M)                               │
                                                    │
                                                    ▼
                                          datasets/bed_management/
                                          ├── discharge_train.parquet
                                          ├── discharge_val.parquet
                                          ├── discharge_test.parquet
                                          ├── capacity_train.parquet
                                          └── metadata.json
                                                    │
                                                    ▼
                                          Training Pipeline
                                          ├── TFT (discharge prediction)
                                          ├── DeepSurv (time-to-discharge)
                                          ├── XGBoost (readiness score)
                                          └── ARIMA-XGB (capacity forecast)
                                                    │
                                                    ▼
                                          models/bed_management/
                                          ├── tft_discharge.pt
                                          ├── deepsurv_discharge.pt
                                          ├── xgb_readiness.joblib
                                          ├── arima_capacity.joblib
                                          └── *.meta.json

Real-Time Data Flow (Production):

ED Triage ──► [acuity, disposition] ──► Bed Allocation Optimizer
Sepsis ICU ──► [risk_score, ICU_need] ──► Capacity Forecast
Patient Journey ──► [vitals, labs] ──► Discharge Prediction
Hospital Ops ──► [dept_census] ──► Bed State Engine
ED Flow ──► [arrival_forecast] ──► Capacity Forecast
                                            │
                                            ▼
                                    Dashboard (Port 3000)
                                    ├── Real-time bed board
                                    ├── Discharge prediction cards
                                    ├── Capacity forecast charts
                                    ├── Trolley tracker
                                    └── Allocation recommendations
```

### 6.3 Database Schema Extensions

```javascript
// New MongoDB collection: bed_state (real-time)
{
  "bed_id": "MAU-101",
  "department": "Medical Assessment Unit",
  "bed_type": "general",           // general, monitored, isolation, trolley
  "status": "occupied",            // available, occupied, blocked, cleaning
  "patient_id": 12345,
  "hadm_id": 67890,
  "acuity": 3,
  "admission_time": ISODate(),
  "predicted_discharge": ISODate(),
  "discharge_confidence": 0.82,
  "discharge_readiness_score": 0.65,
  "last_updated": ISODate()
}

// New MongoDB collection: capacity_forecast
{
  "department": "Medical Assessment Unit",
  "forecast_time": ISODate(),
  "horizon_hours": 24,
  "predicted_census": 28,
  "census_lower_90": 24,
  "census_upper_90": 32,
  "capacity": 30,
  "predicted_occupancy": 0.93,
  "alert_level": "amber",         // green, amber, red, black
  "created_at": ISODate()
}

// New MongoDB collection: trolley_log
{
  "patient_id": 12345,
  "hadm_id": 67890,
  "trolley_start": ISODate(),
  "trolley_end": ISODate(),
  "trolley_hours": 8.5,
  "waiting_for_department": "Medicine",
  "acuity": 2,
  "ed_arrival_time": ISODate()
}
```

---

## 7. API Design

### 7.1 Endpoints

```
# ─── Real-Time Bed State ─────────────────────────────────
GET  /beds                           # All beds with current state
GET  /beds/{department}              # Beds in specific department
GET  /beds/summary                   # Department-level occupancy summary
POST /beds/{bed_id}/update           # Update bed status (admit/discharge/clean/block)

# ─── Discharge Prediction ────────────────────────────────
POST /predict-discharge              # Single patient discharge prediction
POST /batch-predict-discharge        # Batch predictions for department
GET  /discharge-board                # All patients with discharge predictions
GET  /discharge-board/{department}   # Department-specific discharge board

# ─── Capacity Forecasting ────────────────────────────────
GET  /forecast/{department}          # Capacity forecast for department
GET  /forecast/hospital              # Hospital-wide capacity forecast
GET  /forecast/trolley               # Predicted trolley count

# ─── Bed Allocation ──────────────────────────────────────
POST /allocate                       # Request optimal bed for patient
GET  /allocation-queue               # Patients waiting for bed allocation
POST /allocate/override              # Manual allocation override

# ─── Metrics & Analytics ─────────────────────────────────
GET  /metrics/trolley-hours          # Trolley hours (INMO-compatible)
GET  /metrics/occupancy-trend        # Historical occupancy trends
GET  /metrics/discharge-accuracy     # Model performance tracking
GET  /metrics/turnaround-time        # Bed turnaround (discharge→next-admit)

# ─── System ──────────────────────────────────────────────
GET  /health                         # Health check (from shared base)
GET  /model-info                     # Model metadata and metrics
```

### 7.2 Key Schemas

```python
class DischargePrediction(BaseModel):
    patient_id: int
    hadm_id: int
    department: str
    current_los_hours: float
    predicted_discharge_time: datetime
    discharge_probability_24h: float    # P(discharge within 24h)
    discharge_probability_48h: float
    discharge_readiness_score: float    # 0-1 composite score
    confidence_interval: tuple[datetime, datetime]  # 90% CI
    key_factors: list[str]             # SHAP-based top factors
    barriers_to_discharge: list[str]   # Identified blockers
    model_used: str                    # tft | deepsurv | xgboost

class BedAllocation(BaseModel):
    patient_id: int
    recommended_bed: str               # bed_id
    recommended_department: str
    priority_score: float
    wait_time_estimate_minutes: float
    alternative_beds: list[dict]       # ranked alternatives
    allocation_reason: str

class CapacityForecast(BaseModel):
    department: str
    forecasts: list[HorizonForecast]   # 4h, 8h, 12h, 24h, 48h, 72h
    current_census: int
    current_capacity: int
    alert_level: str                   # green/amber/red/black
    recommended_actions: list[str]

class HorizonForecast(BaseModel):
    horizon_hours: int
    predicted_census: float
    lower_bound_90: float
    upper_bound_90: float
    predicted_occupancy: float
    predicted_admissions: int
    predicted_discharges: int
```

---

## 8. Implementation Plan

### Phase 1: Dataset & Infrastructure (Week 1-2)

| Task | Description | Dependencies |
|------|-------------|--------------|
| 1.1 | Create `build_dataset.py` — extract discharge prediction cohort from MIMIC admissions + transfers + vitals + labs | MongoDB, shared/db |
| 1.2 | Feature engineering: current state features (42 features per patient) | 1.1 |
| 1.3 | Create temporal sequences for TFT (hourly snapshots per admission) | 1.1 |
| 1.4 | Build capacity time-series dataset (hourly census per department) | 1.1 |
| 1.5 | Train/val/test split (70/15/15, time-based for capacity) | 1.2, 1.3, 1.4 |

### Phase 2: Model Training (Week 2-4)

| Task | Description | Dependencies |
|------|-------------|--------------|
| 2.1 | Implement TFT model class in PyTorch | Phase 1 |
| 2.2 | Implement DeepSurv model class | Phase 1 |
| 2.3 | Implement XGBoost discharge readiness scorer | Phase 1 |
| 2.4 | Implement ARIMA-XGBoost hybrid for capacity forecasting | 1.4 |
| 2.5 | Train all models, hyperparameter tuning with Optuna | 2.1-2.4 |
| 2.6 | Evaluate: AUROC, MAE, concordance index, calibration | 2.5 |
| 2.7 | Save models via shared ModelRegistry | 2.6 |

### Phase 3: API & Engines (Week 3-5)

| Task | Description | Dependencies |
|------|-------------|--------------|
| 3.1 | Implement `BedStateEngine` — real-time bed tracking | MongoDB |
| 3.2 | Implement `DischargePredictionEngine` — model inference | Phase 2 |
| 3.3 | Implement `CapacityForecastEngine` — demand/capacity prediction | 2.4 |
| 3.4 | Implement `BedAllocationOptimizer` — OR-Tools CP-SAT solver | 3.1, 3.2 |
| 3.5 | Build FastAPI app with all endpoints | 3.1-3.4 |
| 3.6 | Integration endpoints: consume ED Triage, Patient Journey data | 3.5 |

### Phase 4: Integration & Dashboard (Week 5-6)

| Task | Description | Dependencies |
|------|-------------|--------------|
| 4.1 | Add Bed Management page to React dashboard | Phase 3 |
| 4.2 | Real-time bed board visualization (department grid) | 4.1 |
| 4.3 | Discharge prediction cards with confidence intervals | 4.1 |
| 4.4 | Capacity forecast charts (Recharts) | 4.1 |
| 4.5 | Trolley tracker widget | 4.1 |
| 4.6 | Integration with Clinical Chat for bed queries | 3.5 |
| 4.7 | Integration with ED Flow for arrival-to-bed pipeline | 3.5 |

---

## 9. Irish Hospital Customization

### Department Structure (Irish)
```python
IRISH_DEPARTMENTS = {
    "ED": {"capacity": 30, "type": "emergency"},
    "MAU": {"capacity": 24, "type": "assessment"},      # Medical Assessment Unit
    "AMAU": {"capacity": 16, "type": "assessment"},     # Acute Medical Assessment
    "SAU": {"capacity": 12, "type": "assessment"},      # Surgical Assessment Unit
    "CDU": {"capacity": 8, "type": "observation"},      # Clinical Decision Unit
    "Medicine": {"capacity": 40, "type": "inpatient"},
    "Surgery": {"capacity": 36, "type": "inpatient"},
    "Cardiology": {"capacity": 20, "type": "inpatient"},
    "Respiratory": {"capacity": 18, "type": "inpatient"},
    "Orthopaedics": {"capacity": 24, "type": "inpatient"},
    "ICU": {"capacity": 12, "type": "critical"},
    "HDU": {"capacity": 8, "type": "high_dependency"},
    "Day_Ward": {"capacity": 20, "type": "day_case"},
    "Discharge_Lounge": {"capacity": 10, "type": "discharge"},
}
```

### HSE Metrics
- **Trolley Count:** Compatible with INMO TrolleyGAR reporting
- **PET (Patient Experience Time):** 6-hour ED target tracking
- **ALOS (Average Length of Stay):** By department and diagnosis group
- **Delayed Discharges:** Categorized by reason (awaiting bed, social, rehab, home care)
- **Bed Occupancy Rate:** Target <85% (HSE standard)

---

## 10. File Structure

```
app_08_bed_management/
├── __init__.py
├── backend/
│   ├── __init__.py
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI application (port 8208)
│   │   └── schemas.py           # Pydantic request/response models
│   ├── models/
│   │   ├── __init__.py
│   │   ├── tft_discharge.py     # Temporal Fusion Transformer
│   │   ├── deepsurv.py          # DeepSurv survival model
│   │   ├── xgb_readiness.py     # XGBoost discharge readiness
│   │   └── capacity_forecast.py # ARIMA-XGBoost hybrid
│   ├── engines/
│   │   ├── __init__.py
│   │   ├── bed_state.py         # Real-time bed state tracking
│   │   ├── discharge_engine.py  # Discharge prediction orchestrator
│   │   ├── capacity_engine.py   # Capacity forecasting orchestrator
│   │   └── allocation.py        # Bed allocation optimizer (OR-Tools)
│   └── dataset/
│       ├── __init__.py
│       └── build_dataset.py     # MIMIC → training data pipeline
├── docs/
│   └── SYSTEM_DESIGN.md         # This document
└── tests/
    ├── __init__.py
    ├── test_discharge_model.py
    ├── test_capacity_forecast.py
    └── test_allocation.py
```
