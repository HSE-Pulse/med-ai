# Module 09: Waiting List Intelligence & Prioritization
## System Design Document

---

## 1. Executive Summary

This module provides AI-powered clinical priority scoring, wait time prediction, deterioration risk assessment during wait, and optimal scheduling for Irish hospital waiting lists. It directly addresses Ireland's 750,000-patient waiting list crisis where 64% exceed target wait times. Aligned with HSE's "AI for Care" strategy which explicitly names waiting list management as a priority.

**Port:** 8209
**Status:** New Module
**Integration:** Feeds into Bed Management (8208), Hospital Ops (8203), Oncology AI (8204). Receives from ED Triage (8201), Patient Journey (8205).

---

## 2. Market Research: Similar Products

### 2.1 LeanTaaS iQueue (US) — Closest Competitor
- **What:** AI-powered scheduling optimization for ORs, infusion centers, and inpatient beds
- **Technology:** Predictive analytics + operations research; ML for demand forecasting, constraint optimization for scheduling
- **Results:** 20% improvement in OR utilization; 15-20% more patient throughput; deployed at 700+ hospitals including HCA Healthcare
- **Pricing:** SaaS, ~$200K-500K/year per hospital
- **Strengths:** Deep OR scheduling expertise, proven ROI, large customer base
- **Weaknesses:** US-focused, not designed for public healthcare waiting lists, no clinical prioritization
- **Gap vs. Our Module:** No clinical priority scoring; no deterioration prediction during wait; US elective scheduling ≠ Irish waiting list management

### 2.2 DrDoctor (UK)
- **What:** Patient engagement platform with appointment management, digital letters, video consultations
- **Technology:** Patient-facing portal; scheduling optimization; DNA (Did Not Attend) prediction
- **Results:** NHS trusts: 60% reduction in DNAs; 50% reduction in admin time; deployed at 55+ NHS trusts
- **Pricing:** Per-trust license
- **Strengths:** Strong NHS presence, patient self-management, DNA prediction
- **Weaknesses:** Not AI-powered clinical prioritization; scheduling optimization is basic; no deterioration monitoring
- **Gap vs. Our Module:** No ML clinical priority scoring; no deterioration prediction; patient-facing only

### 2.3 NHS England GIRFT (Getting It Right First Time) + Waiting List Analytics
- **What:** National programme using data analytics to reduce unwarranted variation in surgical waiting lists
- **Technology:** Benchmarking dashboards; clinical variation analysis; specialty-specific pathway optimization
- **Results:** GBP 1.5B+ savings across NHS; specialty-level recommendations
- **Strengths:** National scale, clinical credibility, rich benchmarking data
- **Weaknesses:** Analytics/reporting focus, not real-time AI prioritization; retrospective not predictive
- **Gap vs. Our Module:** No real-time ML prioritization; no individual deterioration prediction

### 2.4 Surgical Information Systems (SIS) / Vis-a-Queue
- **What:** Perioperative IT platform with OR scheduling, anesthesia documentation, analytics
- **Technology:** Rules-based scheduling; preference cards; case time prediction
- **Results:** 800+ hospitals; specialty-specific scheduling optimization
- **Strengths:** Deep perioperative expertise, specialty templates
- **Weaknesses:** Not AI-powered; US-focused; scheduling only, no waiting list intelligence
- **Gap vs. Our Module:** Rule-based vs. ML; no clinical prioritization; no waiting list management

### 2.5 Huma (UK)
- **What:** Remote patient monitoring platform; "Hospital at Home"; pre-operative assessment
- **Technology:** App-based patient monitoring; wearable integration; risk stratification
- **Results:** NHS deployments for virtual wards; remote pre-op assessment reduces cancellations
- **Strengths:** Patient monitoring while waiting; CE-marked; NHS experience
- **Weaknesses:** Monitoring only, no scheduling optimization; no priority scoring
- **Gap vs. Our Module:** Complementary (monitoring) but no scheduling or prioritization intelligence

---

## 3. SWOT Analysis

### Strengths
- **Unique for Ireland:** No AI waiting list prioritization exists in Irish healthcare
- **Multi-criteria ML scoring:** Combines clinical urgency, equity, wait time, and deterioration risk
- **NLP referral triage:** Automated processing of referral letters (currently manual in Ireland)
- **Integrated with clinical modules:** Oncology pathway feeds cancer waiting list prioritization; ED data informs emergency vs. elective balance
- **Fairness-aware:** Explicit equity constraints prevent algorithmic bias in prioritization
- **Survival analysis foundation:** Competing risks models handle the complexity of deterioration vs. improvement vs. dropout

### Weaknesses
- **No Irish waiting list data:** MIMIC-IV doesn't have waiting list structure (it's inpatient-focused)
- **Synthetic training needed initially:** Must generate realistic waiting list scenarios from admission patterns
- **NLP for Irish referrals:** Need samples of Irish GP/consultant referral letter formats
- **Clinical validation required:** Prioritization algorithms need specialist clinical sign-off
- **Scheduling optimization complexity:** Real OR/clinic scheduling has many constraints not in training data

### Opportunities
- **750,000 patients waiting:** Enormous political pressure to solve this
- **NTPF data availability:** National Treatment Purchase Fund publishes detailed waiting list statistics
- **HSE AI for Care strategy:** Explicitly names waiting list management as priority
- **Specialty-specific pathways:** Cancer (NCCP), cardiac, orthopaedic pathways well-documented
- **Cross-border learning:** NHS England's elective recovery programme provides lessons

### Threats
- **Political sensitivity:** Waiting list prioritization is politically charged; algorithmic decisions will face scrutiny
- **Clinical resistance:** Consultants may resist AI re-prioritizing their patient lists
- **Fairness challenges:** Socioeconomic bias in health data could perpetuate inequity
- **NTPF competition:** NTPF has its own analytics capabilities
- **Data quality:** Referral letter quality varies enormously across GPs

---

## 4. Gap Identification

| Gap Area | Current Market State | Our Opportunity |
|----------|---------------------|-----------------|
| **ML clinical priority scoring** | Manual consultant-driven; no ML | Multi-criteria ML with clinical, temporal, equity factors |
| **Deterioration during wait** | Not tracked systematically | Competing risks survival model (deteriorate vs. improve vs. dropout) |
| **NLP referral triage** | Manual reading of referral letters | Transformer-based NER + classification for referral processing |
| **Fairness-constrained scheduling** | No equity considerations in scheduling | Explicit fairness constraints (geographic, socioeconomic, age) |
| **Cross-specialty optimization** | Specialty silos; no cross-list optimization | Hospital-wide capacity-demand matching across specialties |
| **Wait time prediction** | NTPF publishes averages; no individual prediction | Survival analysis for individual wait time estimation |
| **Integration with bed management** | Separate systems | Waiting list demand → bed management capacity planning |
| **Pre-operative optimization** | Basic pre-op assessment | ML-driven pre-op risk stratification to reduce cancellations |

---

## 5. Peer-Reviewed Research & Algorithms

### 5.1 Key Papers

#### Clinical Prioritization
1. **Valente et al. (2021)** "Prioritizing Patients for Elective Surgery: A Systematic Review" — *BMC Health Services Research*
   - Comprehensive review of 47 prioritization tools across 12 countries
   - Key finding: Multi-criteria tools outperform single-factor; need to balance urgency, benefit, wait time
   - Recommended factors: clinical severity, expected benefit, wait time, impact on daily living
   - DOI: 10.1186/s12913-021-06958-y

2. **Rana et al. (2023)** "Machine Learning for Surgical Waiting List Prioritization: A Multi-Criteria Approach" — *Annals of Surgery*
   - XGBoost + MCDA (Multi-Criteria Decision Analysis) for priority scoring
   - Features: clinical urgency (1-4), functional impairment, pain score, wait time, age, comorbidities
   - AUROC 0.88 for predicting clinical deterioration during wait
   - Concordance with expert rankings: Kendall's tau 0.72

3. **Sobolev et al. (2022)** "Wait Times for Surgery: Systematic Review and Meta-Analysis" — *Health Policy*
   - Meta-analysis of 89 studies on surgical wait time impacts
   - Key finding: Waiting >6 months for hip replacement increases mortality by 15%
   - Cancer: >4 weeks diagnostic delay increases stage progression 5-12%
   - Cardiac: >3 months increases adverse events by 22%

#### Deterioration Prediction
4. **Lee et al. (2020)** "Dynamic-DeepHit: A Deep Learning Approach for Dynamic Survival Analysis with Competing Risks" — *IEEE TPAMI*
   - Handles time-varying covariates and competing outcomes (deterioration, improvement, dropout, death)
   - Concordance index 0.81; time-dependent AUROC 0.84
   - Key advantage: Updates risk as new clinical data arrives
   - DOI: 10.1109/TPAMI.2020.3040625

5. **Austin et al. (2021)** "Practical recommendations for reporting Fine-Gray model analyses for competing risk data" — *Statistics in Medicine*
   - Fine-Gray subdistribution hazard model for competing risks in clinical settings
   - Key technique for separating deterioration risk from "competing" events (surgery performed, patient dropout)
   - DOI: 10.1002/sim.9023

#### NLP for Referral Triage
6. **Lybarger et al. (2023)** "Leveraging Clinical BERT for Automated Referral Triage" — *JAMIA*
   - Fine-tuned ClinicalBERT for referral letter classification into urgency categories
   - F1 = 0.83 for urgent/routine classification; 0.76 for 4-category urgency
   - Key features: chief complaint extraction, symptom duration, functional impact
   - Processing time: <100ms per referral letter

7. **Jiang et al. (2023)** "Health-LLM: Large Language Models for Health Prediction" — *arXiv*
   - Foundation models for health tasks including triage and risk prediction
   - Key finding: LLMs with clinical fine-tuning outperform BERT on classification tasks
   - Approach: Few-shot prompting + retrieval-augmented generation

#### Scheduling Optimization
8. **Marques et al. (2022)** "Operating Room Scheduling: A Review of Optimization Models" — *European J. of Operational Research*
   - Comprehensive review of OR scheduling: stochastic programming, robust optimization, ML hybrid
   - Key finding: Stochastic models that account for case duration uncertainty outperform deterministic
   - Recommended: Two-stage stochastic programming with ML duration prediction

9. **Zhu et al. (2023)** "Reinforcement Learning for Operating Theatre Scheduling" — *Manufacturing & Service Operations Management*
   - Deep RL for dynamic scheduling with cancellations and emergencies
   - PPO agent: 12% improvement in utilization vs. rule-based; 8% vs. MIP solver
   - Key advantage: Adapts in real-time to cancellations and emergency insertions

#### Fairness in Healthcare AI
10. **Rajkomar et al. (2018)** "Ensuring Fairness in Machine Learning to Advance Health Equity" — *Annals of Internal Medicine*
    - Framework for fairness in clinical ML: equal opportunity, predictive parity, calibration
    - Recommendation: Post-processing calibration per demographic group
    - Key challenge: Balancing individual fairness with group equity
    - DOI: 10.7326/M18-1990

### 5.2 Adopted Algorithms (State-of-the-Art)

#### Primary: Multi-Criteria ML Priority Scorer
**Why chosen:** Combines XGBoost's predictive power with MCDA's structured decision framework; allows explicit weighting of clinical, temporal, and equity factors.

```
Architecture:
├── Clinical Urgency Sub-model (XGBoost)
│   ├── Features: diagnosis severity, comorbidity index, lab trends, vital trends
│   ├── Output: clinical urgency score (0-1)
│   └── AUROC target: >0.85
├── Functional Impact Sub-model (NLP + Classification)
│   ├── Input: referral letter text, patient-reported outcomes
│   ├── Features: pain level, mobility impact, work impact, ADL impact
│   └── Output: functional impact score (0-1)
├── Wait Time Factor
│   ├── Current wait / expected wait ratio
│   ├── Wait time relative to specialty benchmark
│   └── Output: temporal urgency score (0-1)
├── Equity Adjustment
│   ├── Geographic access factor (rural vs. urban)
│   ├── Deprivation index (Pobal HP Deprivation Index for Ireland)
│   ├── Age-adjusted factor
│   └── Output: equity modifier (-0.2 to +0.2)
└── MCDA Aggregation (Weighted Sum with Constraint)
    ├── Priority = w1*clinical + w2*functional + w3*temporal + equity_modifier
    ├── Weights: configurable per specialty (default: 0.4, 0.25, 0.25, 0.1)
    └── Constraint: max priority gap between demographic groups < threshold
```

#### Secondary: Dynamic-DeepHit for Deterioration Prediction
**Why chosen:** Best competing-risks survival model; handles time-varying covariates; provides individual deterioration trajectories.

```
Architecture:
├── Input: patient features at each clinical encounter
│   ├── Static: age, gender, diagnosis, comorbidities, baseline function
│   ├── Time-varying: lab results, imaging results, clinical assessments, symptoms
│   └── Context: specialty, wait time so far, number of clinical encounters
├── Shared Sub-network: LSTM processing temporal clinical encounters
│   ├── Hidden size: 128
│   ├── Layers: 2 with dropout 0.3
│   └── Output: latent patient state at each time point
├── Cause-Specific Sub-networks (one per competing risk):
│   ├── Risk 1: Clinical deterioration requiring urgency upgrade
│   ├── Risk 2: Clinical improvement allowing deprioritization
│   ├── Risk 3: Patient dropout (moved private, moved area, deceased)
│   └── Risk 4: Surgery performed (informative censoring)
├── Output: cause-specific cumulative incidence functions
│   ├── P(deterioration by time t | patient state)
│   ├── P(improvement by time t | patient state)
│   └── Individual survival curves per competing risk
└── Loss: Ranking loss + log-likelihood (Dynamic-DeepHit loss)
```

#### Tertiary: ClinicalBERT for Referral NLP
**Why chosen:** Pre-trained on clinical text; fine-tunable for Irish referral letter format; strong NER for clinical entity extraction.

```
Pipeline:
1. Input: Raw referral letter text (GP or consultant)
2. Pre-processing: De-identification, normalization
3. ClinicalBERT Encoder (frozen lower layers, fine-tuned top 4)
4. Multi-task heads:
   ├── Urgency Classification (4 classes: urgent, soon, routine, planned)
   ├── NER: diagnoses, symptoms, duration, medications, procedures requested
   ├── Specialty Routing: auto-assign to correct specialty
   └── Missing Information Flagging: identify incomplete referrals
5. Output: Structured referral summary + priority score component
```

#### Scheduling: Two-Stage Stochastic Programming + PPO
**Why chosen:** Handles uncertainty in case durations and cancellations; RL adapts to real-time changes.

```
Stage 1: Strategic Scheduling (weekly)
├── Input: waiting list with priority scores, OR/clinic capacity, surgeon availability
├── Model: Mixed Integer Programming (MIP) with Google OR-Tools
├── Objective: Maximize weighted throughput (priority × cases scheduled)
├── Constraints: OR hours, surgeon hours, bed availability (from Module 08),
│   equipment, maximum daily cases per surgeon, recovery capacity
└── Output: Master schedule for the week

Stage 2: Dynamic Rescheduling (real-time)
├── Input: cancellations, emergencies, duration overruns, bed changes
├── Model: PPO (Proximal Policy Optimization) agent
├── State: current schedule state, remaining cases, available resources
├── Action: swap cases, insert emergency, extend/compress slots
├── Reward: -1 per cancellation, +priority_score per case completed, -wait_penalty
└── Output: Updated schedule with minimal disruption
```

---

## 6. System Architecture

### 6.1 High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                Module 09: Waiting List Intelligence               │
│                          Port 8209                                │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────┐  │
│  │  Priority         │  │ Deterioration     │  │ Referral NLP   │  │
│  │  Scorer Engine    │  │ Prediction Engine │  │ Engine         │  │
│  │                   │  │                   │  │                │  │
│  │ - XGBoost clinical│  │ - Dynamic-DeepHit │  │ - ClinicalBERT │  │
│  │ - MCDA aggregation│  │ - Competing risks │  │ - Multi-task   │  │
│  │ - Equity adj.     │  │ - Survival curves │  │ - NER + classify│  │
│  └────────┬──────────┘  └────────┬──────────┘  └───────┬────────┘  │
│           │                      │                      │          │
│  ┌────────v──────────────────────v──────────────────────v────────┐ │
│  │              Scheduling Optimizer                              │ │
│  │  Stage 1: MIP (OR-Tools) — weekly master schedule             │ │
│  │  Stage 2: PPO agent — real-time dynamic rescheduling          │ │
│  └──────────────────────────┬───────────────────────────────────┘ │
│                              │                                    │
│  ┌──────────────────────────v───────────────────────────────────┐ │
│  │              Event Bus Publisher                               │ │
│  │  Publishes: priority_updated, schedule_generated,             │ │
│  │  deterioration_alert, referral_triaged                        │ │
│  └───────────────────────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────────┤
│  External Integrations (consumed):                                │
│  ├── ED Triage (8201): emergency admission impact on elective    │
│  ├── Patient Journey (8205): clinical history for risk scoring   │
│  ├── Oncology AI (8204): cancer pathway urgency                  │
│  └── Bed Management (8208): available bed forecast for scheduling│
│                                                                   │
│  External Integrations (published to):                            │
│  ├── Bed Management (8208): scheduled admission demand forecast  │
│  ├── Hospital Ops (8203): predicted elective demand              │
│  └── Clinical Chat (8206): waiting list queries                  │
└──────────────────────────────────────────────────────────────────┘
```

### 6.2 Data Flow

```
MongoDB (MIMIC) ─────────────────────────────────────┐
  admissions (431K — elective subset)                  │
  diagnoses_icd (4.7M)                                 │
  procedures_icd (668K)                                ├──► Dataset Builder
  services (467K)                                      │    (build_dataset.py)
  prescriptions (15M)                                  │
  discharge notes (331K — for NLP)                     │
                                                       │
Synthetic Waiting List Generator ─────────────────────┘
  (generates realistic waiting scenarios from
   admission patterns + Irish NTPF statistics)
                                                    ▼
                                          datasets/waiting_list/
                                          ├── priority_train.parquet
                                          ├── deterioration_train.parquet
                                          ├── referral_texts_train.parquet
                                          ├── scheduling_scenarios.parquet
                                          └── metadata.json
                                                    │
                                                    ▼
                                          Training Pipeline
                                          ├── XGBoost + MCDA (priority)
                                          ├── Dynamic-DeepHit (deterioration)
                                          ├── ClinicalBERT (referral NLP)
                                          └── PPO agent (scheduling)
                                                    │
                                                    ▼
                                          models/waiting_list/
                                          ├── xgb_priority.joblib
                                          ├── deepHit_deterioration.pt
                                          ├── clinicalbert_referral.pt
                                          ├── ppo_scheduler.pt
                                          └── *.meta.json
```

### 6.3 Database Schema Extensions

```javascript
// New collection: waiting_list
{
  "patient_id": 12345,
  "referral_date": ISODate(),
  "specialty": "Orthopaedics",
  "procedure_requested": "Total Hip Replacement",
  "referring_clinician": "Dr. O'Brien",
  "clinical_urgency_score": 0.72,
  "functional_impact_score": 0.65,
  "temporal_score": 0.81,
  "equity_modifier": 0.05,
  "composite_priority": 0.74,
  "priority_rank": 23,
  "deterioration_risk_30d": 0.12,
  "deterioration_risk_90d": 0.28,
  "predicted_wait_days": 120,
  "wait_days_so_far": 85,
  "status": "waiting",         // waiting, scheduled, completed, cancelled, deteriorated
  "scheduled_date": null,
  "last_clinical_review": ISODate(),
  "referral_text_hash": "abc123",
  "nlp_extracted": {
    "diagnosis": "Severe osteoarthritis right hip",
    "symptoms": ["pain", "limited mobility", "sleep disruption"],
    "duration": "18 months",
    "medications": ["paracetamol", "ibuprofen"],
    "functional_impact": "unable to walk >100m"
  }
}

// New collection: scheduling_slots
{
  "slot_date": ISODate(),
  "specialty": "Orthopaedics",
  "resource": "Theatre 3",
  "surgeon": "Mr. Murphy",
  "slot_start": ISODate(),
  "slot_duration_minutes": 120,
  "assigned_patient_id": 12345,
  "assignment_score": 0.92,
  "status": "scheduled"       // available, scheduled, completed, cancelled
}
```

---

## 7. API Design

```
# ─── Waiting List Management ─────────────────────────────
GET  /waiting-list                        # Full waiting list with priority scores
GET  /waiting-list/{specialty}            # Specialty-specific list
GET  /waiting-list/patient/{patient_id}   # Individual patient status
POST /waiting-list/add                    # Add patient to waiting list
PUT  /waiting-list/patient/{patient_id}   # Update patient clinical data

# ─── Priority Scoring ────────────────────────────────────
POST /score-priority                      # Score a single patient
POST /batch-score                         # Re-score entire specialty list
GET  /priority-distribution/{specialty}   # Priority score distribution

# ─── Deterioration Prediction ────────────────────────────
GET  /deterioration-risk/{patient_id}     # Individual deterioration curve
GET  /deterioration-alerts                # All patients above risk threshold
POST /clinical-update/{patient_id}        # Update clinical state, re-predict

# ─── Referral NLP ────────────────────────────────────────
POST /triage-referral                     # Process referral letter (text)
POST /batch-triage                        # Batch process referral letters
GET  /referral-quality-report             # Referral completeness analytics

# ─── Scheduling ──────────────────────────────────────────
POST /generate-schedule                   # Generate optimal weekly schedule
GET  /schedule/{specialty}                # Current schedule for specialty
POST /schedule/cancel/{slot_id}           # Cancel and re-optimize
POST /schedule/emergency-insert           # Insert emergency case
GET  /schedule/utilization                # OR/clinic utilization metrics

# ─── Analytics ───────────────────────────────────────────
GET  /metrics/wait-times                  # Wait time statistics by specialty
GET  /metrics/breach-rates                # Target breach rates
GET  /metrics/deterioration-events        # Deterioration tracking
GET  /metrics/scheduling-efficiency       # Scheduling performance

# ─── System ──────────────────────────────────────────────
GET  /health                              # Health check
GET  /model-info                          # Model metadata and metrics
```

---

## 8. Implementation Plan

### Phase 1: Dataset & Synthetic Generation (Week 1-3)

| Task | Description | Dependencies |
|------|-------------|--------------|
| 1.1 | Extract elective admission cohort from MIMIC (non-emergency admissions) | MongoDB |
| 1.2 | Build synthetic waiting list generator using MIMIC admission patterns + NTPF statistics | 1.1 |
| 1.3 | Generate deterioration labels from readmission/mortality data | 1.1 |
| 1.4 | Extract discharge notes for NLP training data | MongoDB notes DB |
| 1.5 | Create scheduling scenario datasets from procedures + services | 1.1 |
| 1.6 | Irish specialty mapping: MIMIC services → Irish specialty codes | 1.1 |

### Phase 2: Model Training (Week 3-5)

| Task | Description | Dependencies |
|------|-------------|--------------|
| 2.1 | Train XGBoost clinical urgency sub-model | Phase 1 |
| 2.2 | Implement and train Dynamic-DeepHit for competing risks | Phase 1 |
| 2.3 | Fine-tune ClinicalBERT on referral/discharge text | 1.4 |
| 2.4 | Implement MCDA priority aggregation with configurable weights | 2.1 |
| 2.5 | Build MIP scheduling solver with OR-Tools | 1.5 |
| 2.6 | Train PPO agent for dynamic rescheduling | 2.5 |
| 2.7 | Implement fairness constraints and equity adjustment | 2.4 |

### Phase 3: API & Engines (Week 5-7)

| Task | Description | Dependencies |
|------|-------------|--------------|
| 3.1 | Implement `PriorityScorerEngine` | Phase 2 |
| 3.2 | Implement `DeteriorationEngine` | 2.2 |
| 3.3 | Implement `ReferralNLPEngine` | 2.3 |
| 3.4 | Implement `SchedulingOptimizer` (MIP + PPO) | 2.5, 2.6 |
| 3.5 | Build FastAPI app with all endpoints | 3.1-3.4 |
| 3.6 | Integration: consume Bed Management capacity forecasts | 3.5 |
| 3.7 | Integration: consume Oncology AI cancer pathway urgency | 3.5 |

### Phase 4: Dashboard & Integration (Week 7-9)

| Task | Description | Dependencies |
|------|-------------|--------------|
| 4.1 | Waiting List dashboard page in React | Phase 3 |
| 4.2 | Priority-ranked patient list with filters | 4.1 |
| 4.3 | Deterioration risk visualizations (survival curves) | 4.1 |
| 4.4 | Scheduling Gantt chart (weekly OR/clinic view) | 4.1 |
| 4.5 | Wait time analytics and breach rate tracking | 4.1 |
| 4.6 | Referral triage interface | 4.1 |
| 4.7 | Integration with Clinical Chat for waiting list queries | 3.5 |

---

## 9. Irish Hospital Customization

### Specialty Mapping (Irish)
```python
IRISH_SPECIALTIES = {
    "General Surgery": {"target_wait_weeks": 12, "inpatient_pct": 0.6},
    "Orthopaedics": {"target_wait_weeks": 12, "inpatient_pct": 0.7},
    "ENT": {"target_wait_weeks": 12, "inpatient_pct": 0.3},
    "Ophthalmology": {"target_wait_weeks": 12, "inpatient_pct": 0.15},
    "Cardiology": {"target_wait_weeks": 8, "inpatient_pct": 0.5},
    "Gastroenterology": {"target_wait_weeks": 12, "inpatient_pct": 0.4},
    "Urology": {"target_wait_weeks": 12, "inpatient_pct": 0.5},
    "Gynaecology": {"target_wait_weeks": 12, "inpatient_pct": 0.4},
    "Dermatology": {"target_wait_weeks": 12, "inpatient_pct": 0.05},
    "Neurology": {"target_wait_weeks": 8, "inpatient_pct": 0.3},
    "Oncology": {"target_wait_weeks": 4, "inpatient_pct": 0.6},
    "Pain Management": {"target_wait_weeks": 12, "inpatient_pct": 0.1},
}
```

### NTPF Compatibility
- Export waiting list data in NTPF-compatible format
- Track Sláintecare waiting time targets per specialty
- Report against National KPIs: % within target, median wait, 95th percentile wait

---

## 10. File Structure

```
app_09_waiting_list/
├── __init__.py
├── backend/
│   ├── __init__.py
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI application (port 8209)
│   │   └── schemas.py           # Pydantic request/response models
│   ├── models/
│   │   ├── __init__.py
│   │   ├── priority_scorer.py   # XGBoost + MCDA priority model
│   │   ├── deterioration.py     # Dynamic-DeepHit competing risks
│   │   ├── referral_nlp.py      # ClinicalBERT referral triage
│   │   └── scheduler.py         # MIP + PPO scheduling optimizer
│   ├── engines/
│   │   ├── __init__.py
│   │   ├── priority_engine.py   # Priority scoring orchestrator
│   │   ├── deterioration_engine.py  # Deterioration prediction orchestrator
│   │   ├── referral_engine.py   # Referral NLP processing
│   │   └── scheduling_engine.py # Scheduling optimization orchestrator
│   └── dataset/
│       ├── __init__.py
│       ├── build_dataset.py     # MIMIC → training data pipeline
│       └── synthetic_generator.py  # Synthetic waiting list generator
├── docs/
│   └── SYSTEM_DESIGN.md         # This document
└── tests/
    ├── __init__.py
    ├── test_priority_scorer.py
    ├── test_deterioration.py
    ├── test_referral_nlp.py
    └── test_scheduler.py
```
