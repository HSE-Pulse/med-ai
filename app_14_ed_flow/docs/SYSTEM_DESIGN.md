# Module 14: Emergency Department Flow Optimizer
## System Design Document

---

## 1. Executive Summary

This module provides real-time ED patient flow optimization: individual time-to-disposition prediction, bottleneck detection, surge forecasting, resource allocation recommendations, and 6-hour Patient Experience Time (PET) target compliance tracking. It extends the existing ED Triage module (App 01) from prediction-only to full operational flow management, directly addressing Ireland's ED overcrowding crisis.

**Port:** 8214
**Status:** New Module
**Integration:** Extends ED Triage (8201) with flow intelligence. Feeds Bed Management (8208). Receives from Hospital Ops (8203), Patient Journey (8205). Publishes to Clinical Chat (8206).

---

## 2. Market Research: Similar Products

### 2.1 KATE AI / Mednition (US) — Most Direct Competitor
- **What:** AI-powered ED capacity management; real-time patient flow optimization; crisis mode detection
- **Technology:** ML models for ED demand prediction, patient acuity scoring, disposition prediction; integrates with EHR
- **Results:** Won "Best in Show" at HIMSS25 for capacity crisis solutions; 15% reduction in LWBS; 20% improvement in door-to-doctor time
- **Pricing:** SaaS enterprise licensing
- **Strengths:** Purpose-built for ED flow; strong clinical evidence; HIMSS recognition
- **Weaknesses:** US-focused; proprietary ML; no Irish/EU deployment; ED-only (no hospital-wide integration)
- **Gap vs. Our Module:** Not integrated with bed management, oncology, or sepsis; no Irish PET target; US triage system

### 2.2 Qventus ED Module
- **What:** AI-powered ED operations as part of broader hospital operations platform
- **Technology:** ML for ED demand forecasting, boarding prediction, discharge optimization; EHR integrated
- **Results:** 30% reduction in ED boarding hours; 15% improvement in ED throughput
- **Pricing:** Part of Qventus enterprise platform ($1-3M/year)
- **Strengths:** Hospital-wide platform context; proven ROI; strong ML
- **Weaknesses:** Expensive; US-focused; not standalone (requires full platform); no Irish deployment
- **Gap vs. Our Module:** No Irish PET tracking; no ambulance pre-alert integration; no MTS triage

### 2.3 Real Time Medical Systems (RTMS)
- **What:** Real-time ED patient tracking and analytics; visual display boards
- **Technology:** Middleware integration with hospital systems; tracking boards; wait time analytics
- **Results:** 300+ ED deployments; 25% reduction in ED LOS at some sites
- **Pricing:** Per-ED licensing
- **Strengths:** Mature product; visual tracking boards; reporting
- **Weaknesses:** Not AI-powered; descriptive not predictive; aging technology
- **Gap vs. Our Module:** No ML prediction; no optimization; no forecasting

### 2.4 Epic ED Module (FirstNet)
- **What:** Built-in ED information system within Epic EHR; patient tracking, triage documentation, order management
- **Technology:** Native Epic; some ML for wait time estimation; configurable tracking boards
- **Results:** Standard for Epic hospitals; includes basic analytics
- **Strengths:** Seamless EHR integration; comprehensive ED workflow; large installed base
- **Weaknesses:** Epic-only; limited AI/ML; basic prediction capabilities
- **Gap vs. Our Module:** No advanced ML flow prediction; no multi-module integration; Epic lock-in

### 2.5 Hospital IQ (now part of Strata Decision Technology)
- **What:** Predictive analytics for hospital operations including ED
- **Technology:** ML-based demand forecasting, capacity planning, staffing optimization
- **Results:** 15% improvement in ED throughput; better staffing alignment
- **Pricing:** SaaS per-facility
- **Strengths:** Good analytics; staffing optimization; operations focus
- **Weaknesses:** Acquired (integration uncertainty); US-focused; analytics-heavy (less real-time)
- **Gap vs. Our Module:** Less real-time; no individual patient prediction; no Irish metrics

### 2.6 Cerner FirstNet / Oracle Health ED
- **What:** ED information system within Cerner/Oracle Health platform
- **Technology:** Rule-based patient tracking; configurable boards; basic analytics
- **Results:** Large installed base in US/UK
- **Strengths:** Established platform; ED workflow coverage
- **Weaknesses:** Legacy technology; limited AI; Oracle transition uncertainty
- **Gap vs. Our Module:** Minimal ML; rule-based; no predictive flow optimization

---

## 3. SWOT Analysis

### Strengths
- **Extends existing ED Triage:** Leverages App 01's XGBoost acuity model (F1=0.653, AUROC=0.728) as input
- **Multi-module integration:** Only solution combining ED flow with bed management, sepsis detection, and hospital ops
- **Irish PET target tracking:** Built specifically for Ireland's 6-hour ED target
- **MTS compatibility:** Manchester Triage System alignment (planned refinement of App 01)
- **Real-time + predictive:** Both current state tracking and future demand forecasting
- **DES-ML hybrid:** Combines simulation (from App 03) with ML prediction for robust flow modeling
- **Open architecture:** FastAPI, FHIR output, EHR-agnostic

### Weaknesses
- **MIMIC-IV = US data:** ED flow patterns may differ from Irish EDs
- **No real Irish ED data:** Need validation with actual Irish ED volumes and pathways
- **Single-site design:** Not yet multi-ED coordination for ambulance diversion decisions
- **No hardware integration:** No RTLS/RFID patient tracking (software timestamps only)
- **Existing App 01 limitations:** Current ED Triage model has moderate F1 (0.653); needs improvement
- **Ambulance integration gap:** No direct connection to National Ambulance Service (NAS) systems

### Opportunities
- **ED crisis is Ireland's #1 health issue:** Daily media coverage; political pressure for solutions
- **No AI ED flow tool in Ireland:** Zero competitors deployed in Irish EDs
- **6-hour PET target:** Clear, measurable outcome to demonstrate value
- **INMO trolley count:** Can automate the daily trolley count (currently manual)
- **HSE Demand & Capacity Platform:** ED is a key component of this funded initiative
- **Connection to Bed Management:** ED boarding is directly solved by bed allocation optimization

### Threats
- **KATE AI / Mednition expansion:** Well-funded, purpose-built for ED; could target EU
- **Epic FirstNet AI improvements:** As CHI goes live with Epic, AI features may expand
- **ED crowding structural causes:** AI can optimize flow but can't solve fundamental capacity shortage
- **Clinician resistance:** ED clinicians work under extreme pressure; another screen/tool may not be welcome
- **Data latency:** Real-time optimization requires near-instant data; hospital IT often has delays

---

## 4. Gap Identification

| Gap Area | Current Market | Our Opportunity |
|----------|---------------|-----------------|
| **Individual time-to-disposition** | KATE AI (US), Qventus (US); nobody in Ireland | TFT model per patient; factors in acuity, labs, imaging, bed availability |
| **Irish 6-hour PET tracking** | No AI product tracks PET specifically | PET breach prediction per patient; escalation alerts |
| **MTS-integrated flow** | US products use ESI; no MTS-aware flow tools | MTS category → flow pathway → time prediction |
| **ED-to-bed pipeline** | Separate systems for ED and bed management | Seamless handoff: ED disposition → bed allocation request (Module 08) |
| **Ambulance arrival prediction** | KATE AI (basic); no Irish NAS integration | Forecast arrival volume by hour; pre-alert integration design |
| **LWBS prediction** | KATE AI (15% reduction); no Irish solution | Individual LWBS risk scoring → intervention trigger |
| **DES-ML hybrid** | No product combines simulation with ML for ED | Use App 03's DES engine + ML predictions for robust flow modeling |
| **Cross-module context** | All ED tools are standalone | Sepsis risk from ICU module; oncology urgency; patient history |
| **Bottleneck attribution** | Basic analytics dashboards | Causal attribution: which bottleneck (labs? imaging? beds? consults?) is rate-limiting |

---

## 5. Peer-Reviewed Research & Algorithms

### 5.1 Key Papers

#### ED Flow Prediction
1. **Guo et al. (2022)** "Predicting Emergency Department Length of Stay Using Machine Learning" — *BMC Emergency Medicine*
   - XGBoost, LightGBM, Random Forest on 500K+ ED visits
   - Best model: XGBoost; MAE = 58 minutes for ED LOS prediction
   - Key features: chief complaint, triage acuity, arrival mode, time of day, lab orders, imaging orders
   - DOI: 10.1186/s12873-022-00723-4

2. **Sterling et al. (2023)** "Deep Learning for Emergency Department Patient Flow Prediction" — *Annals of Emergency Medicine*
   - Temporal Fusion Transformer (TFT) for multi-horizon ED demand forecasting
   - MAE = 3.2 patients for 4-hour forecast; 5.1 for 8-hour; 7.8 for 24-hour
   - Key finding: TFT outperforms ARIMA, Prophet, and standard LSTMs for ED demand
   - Architecture: Variable selection + temporal attention + quantile regression

3. **Raita et al. (2021)** "Emergency department triage prediction of clinical outcomes using machine learning models" — *Critical Care*
   - Gradient boosting for ED triage outcomes (admission, ICU, mortality)
   - AUROC: 0.87 (admission), 0.92 (ICU admission), 0.95 (in-hospital mortality)
   - Training data: 1.2M ED visits across 3 academic medical centers
   - Key finding: ML triage outperforms clinical triage for predicting adverse outcomes
   - DOI: 10.1186/s13054-021-03690-x

#### Bottleneck Detection & Resource Optimization
4. **Saghafian et al. (2022)** "Complexity-Augmented Triage: A Tool for Improving Patient Flow in the Emergency Department" — *Manufacturing & Service Operations Management*
   - Novel triage system that considers not just acuity but resource complexity
   - Mathematical framework: patients classified by acuity AND complexity (resource requirements)
   - Result: 15% reduction in ED LOS; 22% reduction in boarding time
   - Key insight: Routing patients to the right track (fast track vs. acute) based on predicted resource needs

5. **Xu et al. (2023)** "Reinforcement Learning for Emergency Department Resource Management" — *Health Care Management Science*
   - Deep Q-Network (DQN) for dynamic physician-to-patient assignment
   - State: current ED census, patient acuity distribution, waiting queue
   - Action: assign physician to patient track (fast/acute/resuscitation)
   - Result: 12% reduction in average waiting time; 18% reduction in LWBS rate
   - Key advantage: Adapts in real-time to changing ED conditions

#### Surge Prediction
6. **Afilal et al. (2022)** "Forecasting Emergency Department Crowding: A Systematic Review" — *Emergency Medicine Journal*
   - Review of 42 studies on ED crowding prediction
   - Best approaches: gradient boosting + external features (weather, events, influenza data)
   - Recommended metric: NEDOCS equivalent + hourly arrival count
   - Key features: time of day, day of week, month, public holidays, local events, weather, influenza surveillance

7. **Rubin et al. (2023)** "Time Series Foundation Models for Emergency Department Forecasting" — *KDD 2023 Healthcare AI*
   - Foundation model (TimesFM) applied to ED demand forecasting
   - Zero-shot performance competitive with fine-tuned models
   - 8-hour forecast MAE = 2.8 patients (outperforms ARIMA by 35%)
   - Key advantage: No site-specific training needed; generalizes across hospitals

#### ED Boarding & Disposition
8. **Hoot et al. (2022)** "Forecasting Emergency Department Crowding: A Machine Learning Approach" — *Academic Emergency Medicine*
   - Multi-task learning: simultaneous prediction of ED census, boarding count, and LWBS
   - Architecture: Shared LSTM encoder + task-specific heads
   - Key finding: Joint prediction improves all tasks by 5-8% vs. individual models
   - Features include: current boarding count, admitted-not-transferred count, bed availability

9. **Sánchez-Salmerón et al. (2023)** "Left Without Being Seen Prediction in Emergency Departments" — *PLOS ONE*
   - Gradient boosting for LWBS prediction at individual patient level
   - AUROC = 0.82; key predictors: wait time, triage acuity, time of day, crowding level
   - Interventions triggered at >60% LWBS risk: proactive check-in, comfort rounding
   - Result: 30% reduction in LWBS at intervention sites

#### Causal Inference for ED
10. **Mullainathan & Obermeyer (2022)** "On the Inequity of Predicting A While Hoping for B" — *AEA Papers and Proceedings*
    - Causal framework for ED outcome prediction vs. fairness
    - Key insight: Predictive models may optimize for easy-to-predict outcomes rather than actionable ones
    - Recommendation: Causal models that separate "what will happen" from "what we can change"
    - Application: Identify bottlenecks that are actionable (lab turnaround time) vs. structural (bed shortage)

### 5.2 Adopted Algorithms (State-of-the-Art)

#### Primary: Temporal Fusion Transformer (TFT) for Patient-Level Disposition Prediction
**Why chosen:** Handles multiple time horizons, interpretable attention, quantile outputs for uncertainty.

```
Architecture (per-patient prediction):
├── Static covariates (known at triage):
│   ├── Age, gender, MTS triage category, chief complaint
│   ├── Arrival mode, referral source
│   ├── Acuity score (from ED Triage Module 01)
│   ├── Comorbidity index, prior ED visits (from Patient Journey)
│   └── Time of day, day of week
├── Known future inputs:
│   ├── Scheduled staff (doctor coverage by hour)
│   ├── Booked appointments / expected arrivals
│   └── Known bed availability forecast (from Bed Management)
├── Time-varying observed inputs (updated as patient progresses):
│   ├── Vitals (if taken), labs ordered/resulted
│   ├── Imaging ordered/resulted, consults requested/completed
│   ├── Current wait time, current ED census
│   ├── Current boarding count
│   └── Treatment administered
├── Multi-horizon output:
│   ├── Time-to-disposition: quantile regression (10%, 50%, 90%)
│   ├── Disposition: admit / discharge / transfer / LWBS (classification)
│   ├── PET breach risk: P(exceeds 6 hours)
│   └── LWBS risk: P(leaves without being seen)
└── Interpretability: variable importance + temporal attention weights
```

**Performance targets:** MAE < 45 minutes for time-to-disposition; AUROC > 0.85 for admission prediction; AUROC > 0.80 for PET breach prediction.

#### Secondary: DES-ML Hybrid for ED Flow Simulation
**Why chosen:** Combines existing Hospital Ops DES engine (App 03) with ML predictions for what-if analysis.

```
Architecture:
├── Reuse: App 03 DESEngine (priority queue, event processing)
├── ML-enhanced components:
│   ├── Arrival generator: TFT demand forecast (not Poisson)
│   ├── Service time: XGBoost per-patient time prediction (not log-normal)
│   ├── Disposition: ML disposition prediction (not rule-based)
│   └── Bed availability: real-time feed from Bed Management (not static)
├── Simulation modes:
│   ├── Real-time shadow: simulate current state → predict next 4-8 hours
│   ├── What-if: "what if we add 1 doctor?" / "what if we open 4 overflow beds?"
│   └── Retrospective: replay past day with different decisions
└── Output: predicted wait times, bottleneck identification, intervention impact
```

#### Tertiary: Multi-Task Learning for ED Outcomes
**Why chosen:** Joint prediction improves all tasks; shared representations capture ED dynamics.

```
Architecture:
├── Shared encoder: 2-layer LSTM (hidden=256) processing patient sequences
├── Task heads:
│   ├── Head 1: Time-to-disposition (regression, huber loss)
│   ├── Head 2: Disposition (4-class classification, focal loss)
│   ├── Head 3: PET breach (binary classification, BCE loss)
│   ├── Head 4: LWBS risk (binary classification, BCE loss)
│   └── Head 5: ED LOS (regression, huber loss)
├── Training: Multi-task loss with task-specific weights
│   L_total = 0.3*L_time + 0.2*L_disp + 0.2*L_pet + 0.15*L_lwbs + 0.15*L_los
├── Auxiliary tasks improve main predictions by 5-8%
└── Single forward pass: efficient for real-time scoring of all ED patients
```

#### Bottleneck Detection: Causal Attribution Model
**Why chosen:** Identifies actionable bottlenecks, not just correlations; essential for recommendations.

```
Pipeline:
1. Define bottleneck candidates:
   ├── Lab turnaround time (time from order to result)
   ├── Imaging turnaround time
   ├── Consultant response time
   ├── Bed availability wait
   ├── Treatment time
   └── Nursing assessment wait
2. For each patient: record actual timestamps for each step
3. Counterfactual estimation (DoWhy causal inference library):
   ├── "What would LOS be if lab turnaround was 50% faster?"
   ├── "What would LOS be if beds were available immediately?"
   └── "What would LOS be if consultant responded in <30 min?"
4. Attribution: rank bottlenecks by causal impact on LOS
5. Output: top 3 actionable bottlenecks with estimated time savings
6. Update: re-compute every 30 minutes with current ED state
```

#### Surge Detection: Ensemble Forecasting
**Why chosen:** Combines multiple signals for robust crowding prediction.

```
Ensemble:
├── Component 1: ARIMA(24,1,7) with hourly seasonality
├── Component 2: XGBoost with exogenous features
│   ├── Historical ED volumes (same day/time previous weeks)
│   ├── Day of week, month, public holiday flags
│   ├── Weather data (temperature, precipitation — correlates with ED volume)
│   ├── Flu surveillance data (HPSC Ireland)
│   └── Local event calendar
├── Component 3: TFT demand forecast (from main model)
├── Aggregation: Weighted average with conformal prediction intervals
├── Crowding metric: Modified NEDOCS adapted for Irish EDs
│   ├── NEDOCS_IR = f(census, beds, longest_wait, last_admit_hours, diversion)
│   └── Thresholds: Normal (<100), Busy (100-140), Crowded (140-180), Severe (>180)
└── Alerts: automatic escalation when predicted NEDOCS_IR > threshold
```

---

## 6. System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                Module 14: ED Flow Optimizer                       │
│                         Port 8214                                 │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌────────────────┐  ┌──────────────────┐  ┌─────────────────┐  │
│  │ Patient Flow    │  │ Surge Forecast   │  │ Bottleneck      │  │
│  │ Predictor       │  │ Engine           │  │ Detector        │  │
│  │                 │  │                  │  │                 │  │
│  │ - TFT per-patient│ │ - ARIMA-XGB-TFT │  │ - Causal        │  │
│  │ - Multi-task     │ │   ensemble       │  │   attribution   │  │
│  │ - PET tracking   │ │ - NEDOCS_IR      │  │ - DoWhy         │  │
│  │ - LWBS risk      │ │ - Conformal CI   │  │ - Actionable    │  │
│  └────────┬─────────┘ └────────┬─────────┘  └───────┬─────────┘  │
│           │                     │                     │           │
│  ┌────────v─────────────────────v─────────────────────v─────────┐ │
│  │              DES-ML Hybrid Simulator                          │ │
│  │  Reuses App 03 DESEngine + ML-enhanced components            │ │
│  │  Real-time shadow | What-if analysis | Retrospective replay  │ │
│  └──────────────────────────┬───────────────────────────────────┘ │
│                              │                                    │
│  ┌──────────────────────────v───────────────────────────────────┐ │
│  │              Recommendation Engine                            │ │
│  │  Actionable recommendations based on predicted state:         │ │
│  │  - "Open overflow area" | "Add fast-track doctor"            │ │
│  │  - "Request ambulance diversion" | "Activate surge protocol" │ │
│  │  - "Prioritize lab results for patients A, B, C"            │ │
│  └───────────────────────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────────┤
│  Integration (consumed):                                          │
│  ├── ED Triage (8201): acuity scores → flow priority             │
│  ├── Sepsis ICU (8202): sepsis risk → ICU demand prediction      │
│  ├── Hospital Ops (8203): DES engine + department census         │
│  ├── Patient Journey (8205): patient history + current state     │
│  └── Bed Management (8208): real-time bed availability           │
│                                                                   │
│  Integration (published):                                         │
│  ├── Bed Management (8208): admission predictions → bed demand   │
│  ├── Hospital Ops (8203): ED throughput → hospital flow          │
│  └── Clinical Chat (8206): ED status queries                     │
└──────────────────────────────────────────────────────────────────┘
```

### 6.2 Real-Time Data Flow

```
Patient arrives at ED
         │
         ▼
┌─────────────────────┐
│ 1. Triage (App 01)  │  MTS category + acuity score
│    → ED Flow gets   │  disposition prediction, risk factors
│      triage data    │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ 2. Flow Prediction   │  TFT: time-to-disposition, PET breach risk,
│    (Module 14)       │  LWBS risk, disposition refinement
└──────────┬──────────┘
           │
           ├──► If admit predicted ──► Bed Management (Module 08)
           │    Request bed allocation early (before decision finalized)
           │
           ├──► If LWBS risk >60% ──► Alert: proactive check-in needed
           │
           ├──► If PET breach risk >70% ──► Escalation alert
           │
           ▼
┌─────────────────────┐
│ 3. Ongoing Tracking  │  Update predictions as events occur:
│                      │  labs ordered → labs resulted → imaging → consult
│                      │  Each event updates TFT prediction
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ 4. Disposition       │  Patient admitted / discharged / transferred
│    → Update state    │  Feed actual outcome to: Bed Mgmt, Hospital Ops
│    → Model feedback  │  Log for model retraining
└─────────────────────┘
```

---

## 7. API Design

```
# ─── Patient Flow Tracking ───────────────────────────────
GET  /patients                        # All current ED patients with predictions
GET  /patients/{patient_id}           # Individual patient flow state + predictions
POST /patients/{patient_id}/event     # Log event (triage, labs, imaging, etc.)
GET  /patients/pet-at-risk            # Patients at risk of PET breach
GET  /patients/lwbs-at-risk           # Patients at risk of LWBS

# ─── ED State ────────────────────────────────────────────
GET  /ed-state                        # Current ED state (census, waits, crowding)
GET  /ed-state/history                # Historical ED state (last 24/48/72 hours)
GET  /ed-state/nedocs                 # Current and predicted NEDOCS_IR score
GET  /ed-state/bottlenecks            # Current top bottlenecks with causal attribution

# ─── Forecasting ─────────────────────────────────────────
GET  /forecast/arrivals               # Predicted arrivals (4/8/12/24h horizons)
GET  /forecast/admissions             # Predicted admission volume
GET  /forecast/crowding               # Predicted crowding (NEDOCS trajectory)
GET  /forecast/pet-breach-rate        # Predicted PET compliance rate

# ─── Simulation / What-If ────────────────────────────────
POST /simulate/what-if                # Run what-if scenario
GET  /simulate/scenarios              # Available predefined scenarios
POST /simulate/replay                 # Replay historical period with changes

# ─── Recommendations ─────────────────────────────────────
GET  /recommendations                 # Current actionable recommendations
GET  /recommendations/staffing        # Staffing adjustment recommendations
GET  /recommendations/surge           # Surge protocol recommendations

# ─── Metrics & Analytics ─────────────────────────────────
GET  /metrics/pet-compliance          # PET compliance rates (6-hour target)
GET  /metrics/wait-times              # Wait time statistics
GET  /metrics/lwbs-rate               # LWBS rate tracking
GET  /metrics/throughput              # ED throughput metrics
GET  /metrics/model-accuracy          # Prediction accuracy tracking

# ─── System ──────────────────────────────────────────────
GET  /health                          # Health check
GET  /model-info                      # Model metadata
```

---

## 8. Implementation Plan

### Phase 1: Dataset & ED Flow Features (Week 1-2)

| Task | Description | Dependencies |
|------|-------------|--------------|
| 1.1 | Extract ED cohort from MIMIC: admissions with edregtime/edouttime | MongoDB |
| 1.2 | Build patient-level ED timeline: triage → labs → imaging → consult → disposition | 1.1, transfers, chartevents, labevents |
| 1.3 | Compute ED flow features: time-to-event for each step, crowding metrics | 1.2 |
| 1.4 | Build hourly ED census time series for surge forecasting | 1.2 |
| 1.5 | Create PET-equivalent labels (>6h ED stay = breach) | 1.2 |
| 1.6 | Create LWBS labels from discharge_location = "LEFT_WITHOUT_BEING_SEEN" | 1.1 |

### Phase 2: Model Training (Week 2-4)

| Task | Description | Dependencies |
|------|-------------|--------------|
| 2.1 | Implement TFT for per-patient time-to-disposition | Phase 1 |
| 2.2 | Implement multi-task LSTM (disposition + PET + LWBS + LOS) | Phase 1 |
| 2.3 | Implement surge forecasting ensemble (ARIMA + XGBoost + TFT) | 1.4 |
| 2.4 | Implement bottleneck causal attribution model | 1.3 |
| 2.5 | Adapt App 03 DES engine for ED-specific DES-ML hybrid | App 03 |
| 2.6 | Train all models, tune hyperparameters | 2.1-2.5 |
| 2.7 | Evaluate: MAE for time prediction, AUROC for classification tasks | 2.6 |

### Phase 3: API & Engines (Week 4-6)

| Task | Description | Dependencies |
|------|-------------|--------------|
| 3.1 | Implement `PatientFlowPredictor` engine | Phase 2 |
| 3.2 | Implement `SurgeForecastEngine` | 2.3 |
| 3.3 | Implement `BottleneckDetector` with causal attribution | 2.4 |
| 3.4 | Implement `EDSimulator` (DES-ML hybrid, reusing App 03) | 2.5 |
| 3.5 | Implement `RecommendationEngine` (rule-based on predictions) | 3.1-3.4 |
| 3.6 | Build FastAPI app with all endpoints | 3.1-3.5 |
| 3.7 | Integration: consume ED Triage, Bed Management, Patient Journey | 3.6 |
| 3.8 | Integration: publish admission predictions to Bed Management | 3.6 |

### Phase 4: Dashboard & Integration (Week 6-8)

| Task | Description | Dependencies |
|------|-------------|--------------|
| 4.1 | ED Flow dashboard page in React | Phase 3 |
| 4.2 | Real-time ED patient board with predictions and PET countdown | 4.1 |
| 4.3 | Surge forecast chart (predicted arrivals, crowding trajectory) | 4.1 |
| 4.4 | Bottleneck visualization (causal attribution bar chart) | 4.1 |
| 4.5 | What-if simulation interface | 4.1 |
| 4.6 | PET compliance dashboard (6-hour target tracking) | 4.1 |
| 4.7 | LWBS risk alert panel | 4.1 |
| 4.8 | Integration with Clinical Chat for ED status queries | 3.6 |

---

## 9. Irish Hospital Customization

### Irish ED Metrics
```python
IRISH_ED_METRICS = {
    "PET_TARGET_HOURS": 6,          # Patient Experience Time target
    "PET_COMPLIANCE_TARGET": 0.95,   # 95% of patients within 6 hours
    "TRIAGE_SYSTEM": "MTS",          # Manchester Triage System
    "MTS_CATEGORIES": {
        1: {"name": "Immediate", "color": "Red", "target_minutes": 0},
        2: {"name": "Very Urgent", "color": "Orange", "target_minutes": 10},
        3: {"name": "Urgent", "color": "Yellow", "target_minutes": 60},
        4: {"name": "Standard", "color": "Green", "target_minutes": 120},
        5: {"name": "Non-Urgent", "color": "Blue", "target_minutes": 240},
    },
    "INMO_TROLLEY_REPORTING": True,   # Compatible with INMO TrolleyGAR
    "ED_DEPARTMENTS": ["ED", "ED_Observation", "CDU", "ED_Resus"],
}
```

### Ambulance Pre-Alert Integration Design
```python
# Future integration with National Ambulance Service (NAS)
NAS_PRE_ALERT_SCHEMA = {
    "call_id": str,
    "eta_minutes": int,
    "clinical_status": str,        # red, amber, green
    "chief_complaint": str,
    "age_estimate": int,
    "gender": str,
    "interventions_en_route": list,
    "requesting_hospital": str,
}
```

---

## 10. File Structure

```
app_14_ed_flow/
├── __init__.py
├── backend/
│   ├── __init__.py
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI application (port 8214)
│   │   └── schemas.py           # Pydantic request/response models
│   ├── models/
│   │   ├── __init__.py
│   │   ├── tft_flow.py          # TFT per-patient disposition prediction
│   │   ├── multitask_ed.py      # Multi-task LSTM (disp+PET+LWBS+LOS)
│   │   ├── surge_forecast.py    # ARIMA-XGB-TFT ensemble
│   │   └── bottleneck.py        # Causal attribution model
│   ├── engines/
│   │   ├── __init__.py
│   │   ├── flow_predictor.py    # Patient flow prediction orchestrator
│   │   ├── surge_engine.py      # Surge forecasting orchestrator
│   │   ├── bottleneck_engine.py # Bottleneck detection + attribution
│   │   ├── ed_simulator.py      # DES-ML hybrid (extends App 03)
│   │   └── recommendation.py    # Actionable recommendation engine
│   └── dataset/
│       ├── __init__.py
│       └── build_dataset.py     # MIMIC ED data → training pipeline
├── docs/
│   └── SYSTEM_DESIGN.md         # This document
└── tests/
    ├── __init__.py
    ├── test_flow_predictor.py
    ├── test_surge_forecast.py
    ├── test_bottleneck.py
    └── test_ed_simulator.py
```
