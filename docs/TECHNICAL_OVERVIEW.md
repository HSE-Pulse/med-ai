# Med AI Healthcare Platform - Technical Overview

**Version:** 1.0.0
**Last Updated:** 2026-03-31
**Classification:** Internal Technical Documentation

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Architecture](#2-system-architecture)
3. [Technology Stack](#3-technology-stack)
4. [Data Pipeline](#4-data-pipeline)
5. [Application Modules](#5-application-modules)
    - 5.1 [App 01: ED Triage AI](#51-app-01-ed-triage-ai)
    - 5.2 [App 02: Sepsis & ICU Watch](#52-app-02-sepsis--icu-watch)
    - 5.3 [App 03: Hospital Operations Intelligence](#53-app-03-hospital-operations-intelligence)
    - 5.4 [App 04: Oncology AI](#54-app-04-oncology-ai)
    - 5.5 [App 05: Patient Journey](#55-app-05-patient-journey)
6. [Dashboard Architecture](#6-dashboard-architecture)
7. [ML Model Performance Summary](#7-ml-model-performance-summary)
8. [Deployment Architecture](#8-deployment-architecture)
9. [Security & Compliance](#9-security--compliance)
10. [Known Limitations & Future Work](#10-known-limitations--future-work)

---

## 1. Executive Summary

The Med AI Healthcare Platform is a production-oriented monorepo comprising five clinical
applications built on the MIMIC-IV critical-care database. The platform spans the
full clinical decision-support spectrum -- from emergency department triage and real-time sepsis
surveillance to hospital-wide operational optimization and oncology pathway intelligence. Each
application follows an identical layered architecture (MongoDB data extraction, Python ETL into
columnar storage, gradient-boosted and deep-learning model training, FastAPI inference services,
React/TypeScript visualization dashboards) enabling rapid iteration and consistent deployment.
The system is designed for eventual deployment at acute-care hospital settings, with all models
trained and validated exclusively on de-identified MIMIC-IV data from Beth Israel Deaconess
Medical Center.

---

## 2. System Architecture

### 2.1 High-Level Architecture Diagram

```
+===========================================================================+
|                         CANCER AI HEALTHCARE PLATFORM                      |
+===========================================================================+
|                                                                           |
|  +-------------------------------------------------------------------+   |
|  |                      PRESENTATION LAYER                            |   |
|  |                                                                    |   |
|  |   React 19.1 + TypeScript + Tailwind CSS 4.x                      |   |
|  |   Vite 6.4 Dev Server (port 3000)                                  |   |
|  |                                                                    |   |
|  |   +----------+ +----------+ +----------+ +----------+ +----------+|   |
|  |   | ED Triage| |Sepsis ICU| |Hosp. Ops | | Oncology | |Patient  ||   |
|  |   |  Page    | |  Page    | |  Page    | |  Page    | |Journey  ||   |
|  |   +----+-----+ +----+-----+ +----+-----+ +----+-----+ +----+----+|   |
|  |        |              |              |              |               |   |
|  |   Shared Components: RiskGauge, TimelineView, VitalSignChart,      |   |
|  |   AcuityBadge, StatCard, Layout                                    |   |
|  +----+----------+----------+----------+----------+-------------------+   |
|       |          |          |          |                                   |
|       | /api/ed  | /api/    | /api/ops | /api/onco                        |
|       |          | sepsis   |          |          Vite Proxy Routing       |
|       v          v          v          v                                   |
|  +-------------------------------------------------------------------+   |
|  |                        API LAYER (FastAPI)                         |   |
|  |                                                                    |   |
|  |   +----------+ +----------+ +----------+ +----------+ +----------+|   |
|  |   | :8201    | | :8202    | | :8203    | | :8204    | | :8205    ||   |
|  |   | ED API   | | Sepsis   | | Ops API  | | Onco API | | Patient  ||   |
|  |   |          | | API      | |          | |          | | Journey  ||   |
|  |   | /predict | | /predict | | /simulate| | /predict | | /timeline||   |
|  |   | /health  | | /health  | | /health  | | /health  | | /vitals  ||   |
|  |   | /model-  | | /model-  | | /model-  | | /model-  | | /labs    ||   |
|  |   |  info    | |  info    | |  info    | |  info    | | /meds    ||   |
|  |   +----+-----+ +----+-----+ +----+-----+ +----+-----+ +----+----+|   |
|  +--------+---------+----+----------+----+----------+-----+-----------+   |
|           |              |               |                |               |
|           v              v               v                v               |
|  +-------------------------------------------------------------------+   |
|  |                      ML / INFERENCE LAYER                          |   |
|  |                                                                    |   |
|  |   Serialized Models (.joblib, .pt) + Metadata (.json)              |   |
|  |                                                                    |   |
|  |   +---------------+  +------------------+  +------------------+   |   |
|  |   | XGBoost 3.2   |  | LightGBM 4.6    |  | PyTorch 2.11.0   |   |   |
|  |   | (ED, Oncology)|  | (Sepsis)         |  | +cu128           |   |   |
|  |   +---------------+  +------------------+  | (NN, LSTM-Attn,  |   |   |
|  |                                             |  Transformer)    |   |   |
|  |   scikit-learn (preprocessing, metrics)     +------------------+   |   |
|  +-------------------------------------------------------------------+   |
|           |              |               |                |               |
|           v              v               v                v               |
|  +-------------------------------------------------------------------+   |
|  |                       DATA LAYER                                   |   |
|  |                                                                    |   |
|  |   +-------------------+     +----------------------------------+  |   |
|  |   | MongoDB 7.x       |     | Local File System                |  |   |
|  |   | localhost:27017    |     |                                  |  |   |
|  |   |                   |     |  datasets/                       |  |   |
|  |   | - MIMIC           |---->|    ed_triage/*.parquet            |  |   |
|  |   | - MIMIC_ICU       | ETL |    sepsis_icu/*.npz              |  |   |
|  |   | - MIMIC_Clinical_ | --> |    hospital_ops/*.parquet         |  |   |
|  |   |   Notes           |     |    oncology/*.parquet             |  |   |
|  |   +-------------------+     +----------------------------------+  |   |
|  +-------------------------------------------------------------------+   |
+===========================================================================+
```

### 2.2 Component Interaction Flow

```
    User Browser
         |
         v
    Vite Dev Server (:3000)
         |
         +-- Static assets (React SPA bundle)
         |
         +-- Proxy rules:
              /api/ed/*       -->  localhost:8201/*
              /api/sepsis/*   -->  localhost:8202/*
              /api/ops/*      -->  localhost:8203/*
              /api/onco/*     -->  localhost:8204/*
              /api/journey/*  -->  localhost:8205/*
                                      |
                                      v
                              FastAPI Uvicorn workers
                                      |
                                      +-- Load serialized model from models/
                                      +-- (Optional) Query MongoDB for context
                                      +-- Return JSON prediction response
```

---

## 3. Technology Stack

### 3.1 Core Technologies

| Layer              | Technology              | Version    | Purpose                                      |
|--------------------|-------------------------|------------|----------------------------------------------|
| **Language**       | Python                  | 3.10       | All backend services, ETL, training           |
| **Language**       | TypeScript              | 5.x        | Dashboard frontend                            |
| **Deep Learning**  | PyTorch                 | 2.11.0+cu128 | Neural networks, LSTM, Transformer models  |
| **GPU Compute**    | CUDA                    | 12.8       | GPU-accelerated training and inference        |
| **Gradient Boost** | XGBoost                 | 3.2        | Tabular baselines (ED Triage, Oncology)       |
| **Gradient Boost** | LightGBM                | 4.6        | Temporal feature baselines (Sepsis)           |
| **API Framework**  | FastAPI                 | 0.135      | REST API with OpenAPI auto-docs               |
| **ASGI Server**    | Uvicorn                 | 0.34+      | Production ASGI server                        |
| **Frontend**       | React                   | 19.1       | Component-based UI framework                  |
| **Build Tool**     | Vite                    | 6.4        | Fast HMR, proxy routing, production builds    |
| **CSS Framework**  | Tailwind CSS            | 4.x        | Utility-first styling                         |
| **Database**       | MongoDB                 | 7.x        | Source-of-truth for MIMIC-IV data             |
| **Data Format**    | Apache Parquet / NumPy  | --         | Columnar storage for tabular/sequence data    |
| **Settings**       | pydantic-settings       | 2.1+       | Type-safe environment configuration           |
| **Logging**        | structlog               | 24.1+      | Structured JSON logging                       |

### 3.2 Hardware

| Component   | Specification                     |
|-------------|-----------------------------------|
| GPU         | NVIDIA GeForce RTX 4060 (8 GB VRAM) |
| CUDA Toolkit| 12.8                              |
| Platform    | Windows 11                        |

### 3.3 Python Environment

| Property        | Value                                        |
|-----------------|----------------------------------------------|
| Environment     | Conda                                        |
| Environment Name| `mitiosis`                                   |
| Python Version  | 3.10                                         |
| Package Manager | pip (via pyproject.toml) + conda for CUDA    |

### 3.4 Key Python Dependencies

```
pymongo        >= 4.6       MongoDB driver
fastapi        >= 0.110     Web framework
uvicorn        >= 0.27      ASGI server
pandas         >= 2.1       DataFrame operations
numpy          >= 1.26      Numerical computing
scikit-learn   >= 1.4       ML utilities, preprocessing, metrics
torch          >= 2.1       Deep learning (actual: 2.11.0+cu128)
xgboost        == 3.2       Gradient boosting (tabular)
lightgbm       == 4.6       Gradient boosting (temporal)
matplotlib     >= 3.8       Visualization
joblib         >= 1.3       Model serialization
httpx          >= 0.27      Async HTTP client
pydantic-settings >= 2.1    Configuration management
structlog      >= 24.1      Structured logging
imbalanced-learn >= 0.12    SMOTE and resampling (optional)
```

---

## 4. Data Pipeline

### 4.1 Source Databases

The platform ingests data from three MongoDB databases, all served from a local MongoDB 7.x
instance at `mongodb://localhost:27017/`.

```
MongoDB localhost:27017
  |
  +-- MIMIC                       26 collections       ~260M+ documents
  |     admissions                    431,088           Core patient admissions
  |     transfers                   1,890,730           Department movements
  |     labevents                 118,057,948           Laboratory results
  |     prescriptions              15,399,811           Medication orders
  |     diagnoses_icd               4,752,265           ICD diagnosis codes
  |     procedures_icd                668,993           ICD procedure codes
  |     services                      467,851           Service assignments
  |     drgcodes                      603,645           DRG severity/mortality
  |     patients                          215           Demographics
  |     d_labitems                      1,623           Lab item definitions
  |     d_icd_diagnoses               109,775           ICD diagnosis lookup
  |     d_icd_procedures               85,257           ICD procedure lookup
  |     emar                       26,743,071           Medication administration
  |     pharmacy                   13,568,015           Pharmacy orders
  |     poe                        39,340,661           Provider order entries
  |     omr                         6,422,067           Outpatient medical records
  |
  +-- MIMIC_ICU                    5 collections       ~332M+ documents
  |     chartevents               314,035,266           Vital signs / observations
  |     icustays                       73,141           ICU stay records
  |     d_items                         4,014           Chart item definitions
  |     datetimeevents              7,117,467           Datetime observations
  |     ingredientevents           11,643,634           Infusion ingredients
  |
  +-- MIMIC_Clinical_Notes         1 collection
        discharge                     331,793           Discharge summaries
```

**Total raw records:** approximately 592 million documents across 32 collections.

### 4.2 ETL Pipeline Architecture

```
+-------------------+       +--------------------+       +-------------------+
|   MongoDB Raw     |       |  Python ETL        |       |  Training-Ready   |
|   Collections     | ----> |  (build_dataset.py)|-----> |  Datasets         |
+-------------------+       +--------------------+       +-------------------+
                                    |
                                    |  Per-app pipeline:
                                    |
                                    +-- 1. Cohort Selection
                                    |      Filter admissions by criteria
                                    |      (e.g., ED visits, ICU stays,
                                    |       cancer ICD codes)
                                    |
                                    +-- 2. Feature Extraction
                                    |      Join across collections
                                    |      Aggregate labs, vitals, meds
                                    |      Compute derived features
                                    |
                                    +-- 3. Label Derivation
                                    |      Construct prediction targets
                                    |      (acuity, sepsis onset,
                                    |       readmission, mortality)
                                    |
                                    +-- 4. Preprocessing
                                    |      Imputation (median/forward-fill)
                                    |      Normalization (StandardScaler)
                                    |      Encoding (one-hot, ordinal)
                                    |
                                    +-- 5. Split & Serialize
                                           Train (70%) / Val (15%) / Test (15%)
                                           Output: .parquet or .npz
```

### 4.3 Data Flow by Format

| Stage              | Format                | Location                        |
|--------------------|-----------------------|---------------------------------|
| Raw source         | BSON (MongoDB)        | `mongodb://localhost:27017/`    |
| Extracted features | Pandas DataFrame      | In-memory during ETL            |
| Training dataset   | Parquet (tabular)     | `datasets/<app>/*.parquet`      |
| Sequence dataset   | NumPy NPZ (temporal)  | `datasets/<app>/*.npz`          |
| Trained models     | Joblib / PyTorch .pt  | `models/<app>/`                 |
| Model metadata     | JSON                  | `models/<app>/*.json`           |

### 4.4 Orchestration Scripts

| Script                          | Purpose                                   |
|---------------------------------|-------------------------------------------|
| `scripts/build_all_datasets.py` | Execute all four app dataset builders      |
| `scripts/train_all_models.py`   | Train all models across all applications   |
| `scripts/start_all_services.py` | Launch all four FastAPI services            |

---

## 5. Application Modules

### 5.1 App 01: ED Triage AI

**Port:** 8201
**Directory:** `app_01_ed_triage/`

#### 5.1.1 Clinical Purpose

Automated Emergency Department triage classification that predicts ESI-equivalent acuity levels
(1-5) and patient disposition (admit, discharge, ICU, expire) from initial presentation data.
The system aims to provide decision support for triage nurses by estimating severity from vital
signs, chief complaint proxies, and early laboratory values available within the first minutes of
an ED encounter.

#### 5.1.2 Data Sources

| Collection         | Database  | Key Fields                                         | Records Used     |
|--------------------|-----------|----------------------------------------------------|------------------|
| `admissions`       | MIMIC     | edregtime, edouttime, admission_type, discharge_location, hospital_expire_flag | ~149K ED visits |
| `transfers`        | MIMIC     | careunit="Emergency Department", intime, outtime   | ~625K ED transfers |
| `labevents`        | MIMIC     | WBC, Lactate, Glucose, Creatinine, Troponin        | Sampled per visit |
| `chartevents`      | MIMIC_ICU | HR, RR, SpO2, SBP, DBP, Temperature                | First vitals per visit |
| `diagnoses_icd`    | MIMIC     | Primary ICD code (seq_num=1)                       | Per admission     |
| `drgcodes`         | MIMIC     | drg_severity                                       | Per admission     |
| `patients`         | MIMIC     | gender, anchor_age                                 | Per patient       |

#### 5.1.3 Feature Engineering

- **Vital signs:** First recorded HR, RR, SpO2, SBP, DBP, Temperature within ED visit window
- **Laboratory:** First WBC, Lactate, Glucose, Creatinine, Troponin values
- **Demographics:** Age (from anchor_age), gender (binary encoded)
- **Temporal:** Hour of arrival, day of week (cyclical encoding)
- **Clinical context:** Admission type (emergency/urgent/elective), prior ED visit count
- **Imputation:** Median imputation for missing labs; vitals forward-filled from closest prior reading
- **Normalization:** StandardScaler fit on training partition only

**Label Construction:**
- **Acuity (1-5):** Composite score derived from admission_type severity + DRG severity index + disposition outcome
- **Disposition:** Categorical target from discharge_location + hospital_expire_flag
- **ED LOS:** Continuous target computed as `edouttime - edregtime` in hours

#### 5.1.4 Model Architectures

**Baseline: XGBoost Multiclass Classifier**

```
Algorithm:        XGBoost (gbtree booster)
Objective:        multi:softprob (5-class)
Num classes:      5 (ESI acuity levels)
Max depth:        6
Learning rate:    0.1
Num estimators:   300
Subsample:        0.8
Colsample bytree: 0.8
Min child weight: 5
Eval metric:      mlogloss
Early stopping:   20 rounds
```

**Advanced: Neural Network with Embeddings**

```
Architecture:     Feedforward NN with categorical embeddings
Input:            Continuous features (normalized) + embedded categoricals
Hidden layers:    [256, 128, 64] with BatchNorm + ReLU + Dropout(0.3)
Output:           5-class softmax
Optimizer:        AdamW (lr=1e-3, weight_decay=1e-4)
Scheduler:        CosineAnnealingLR
Loss:             CrossEntropyLoss (class-weighted)
Epochs:           50 (early stopping patience=10)
Batch size:       512
```

#### 5.1.5 Performance Metrics (Test Set)

| Model    | Accuracy | Weighted F1 | Macro AUROC |
|----------|----------|-------------|-------------|
| XGBoost  | 0.665    | 0.653       | 0.728       |
| Neural Net | 0.570  | 0.577       | 0.690       |

The XGBoost baseline outperforms the neural network on all metrics, which is consistent with
the relatively small feature space and tabular data structure. The 5-class acuity problem is
inherently difficult due to subjective label boundaries between adjacent ESI levels.

#### 5.1.6 API Endpoints

| Method | Endpoint       | Description                                  |
|--------|----------------|----------------------------------------------|
| POST   | `/predict`     | Predict acuity level and disposition          |
| GET    | `/health`      | Service health check                         |
| GET    | `/model-info`  | Return model metadata and feature schema     |

---

### 5.2 App 02: Sepsis & ICU Watch

**Port:** 8202
**Directory:** `app_02_sepsis_icu/`

#### 5.2.1 Clinical Purpose

Real-time sepsis onset prediction for ICU patients, targeting early detection 4-6 hours before
clinical recognition. The system continuously evaluates patient risk from time-series vital signs
and laboratory values, computing an hourly sepsis probability score. This enables proactive
intervention with antibiotics and fluid resuscitation before hemodynamic collapse.

#### 5.2.2 Data Sources

| Collection         | Database  | Key Fields                                         | Records Used     |
|--------------------|-----------|----------------------------------------------------|------------------|
| `icustays`         | MIMIC_ICU | stay_id, intime, outtime, los, first_careunit      | 73,141 stays     |
| `chartevents`      | MIMIC_ICU | HR, RR, SpO2, SBP, DBP, Temp, GCS (hourly)        | ~314M events     |
| `labevents`        | MIMIC     | WBC, Lactate, Creatinine, Platelets, Bilirubin, BUN, INR | Sampled per stay |
| `admissions`       | MIMIC     | hospital_expire_flag                               | Per admission     |
| `diagnoses_icd`    | MIMIC     | Sepsis ICD codes (A40, A41, 995.91, 995.92)       | Per admission     |
| `prescriptions`    | MIMIC     | Antibiotic drugs (keyword matching)                | Per admission     |
| `patients`         | MIMIC     | gender, anchor_age                                 | Per patient       |

#### 5.2.3 Feature Engineering

- **Vital signs (hourly):** HR, RR, SpO2, SBP, DBP, Temperature, GCS -- resampled to 1-hour bins
- **Laboratory (irregular):** WBC, Lactate, Creatinine, Platelets, Bilirubin, BUN, INR -- forward-filled to hourly resolution
- **SOFA sub-scores:** Computed hourly from vitals + labs (respiratory, coagulation, liver, cardiovascular, CNS, renal)
- **Trend features:** 6-hour rolling mean, rolling std, delta (current - 6h ago) for each vital/lab
- **Static features:** Age, gender, admission type, first ICU careunit
- **Sequence construction:** Fixed-length windows of 24 hourly observations (for LSTM)
- **Imputation:** Forward-fill with median fallback; missingness indicators as additional features

**Label Construction (Sepsis-3 Approximation):**
- SOFA score computed hourly from vital signs and laboratory values
- Sepsis onset defined as: SOFA increase >= 2 points AND sepsis ICD code present in discharge diagnoses
- Prediction target: binary -- sepsis onset within the next 4 hours from the current observation time
- Negative samples: all hourly windows without impending sepsis onset

#### 5.2.4 Model Architectures

**Baseline: LightGBM Binary Classifier**

```
Algorithm:        LightGBM (GBDT)
Objective:        binary (log loss)
Num leaves:       63
Max depth:        -1 (unlimited)
Learning rate:    0.05
Num estimators:   500
Feature fraction: 0.8
Bagging fraction: 0.8
Min child samples: 20
Scale pos weight: auto (class imbalance correction)
Early stopping:   30 rounds
```

**Advanced: LSTM with Attention**

```
Architecture:     Bidirectional LSTM + Temporal Attention
Input:            24-step hourly sequences x N features
LSTM layers:      2 (hidden_size=128, bidirectional)
Attention:        Scaled dot-product over LSTM hidden states
FC head:          [256, 64] with LayerNorm + GELU + Dropout(0.3)
Output:           Sigmoid (binary probability)
Optimizer:        AdamW (lr=5e-4, weight_decay=1e-4)
Scheduler:        ReduceLROnPlateau (patience=5, factor=0.5)
Loss:             BCEWithLogitsLoss (pos_weight adjusted)
Epochs:           30 (early stopping patience=7)
Batch size:       256
Gradient clipping: max_norm=1.0
```

#### 5.2.5 Performance Metrics (Test Set)

| Model          | AUROC | Sensitivity | Specificity | Sensitivity @95% Spec |
|----------------|-------|-------------|-------------|----------------------|
| LightGBM       | 0.994 | --          | --          | 1.000                |
| LSTM-Attention | 0.998 | 0.882       | 0.997       | --                   |

Both models achieve exceptional discrimination performance. The near-perfect AUROC values reflect
the strong signal in SOFA component features combined with the structured nature of the Sepsis-3
definition. The LightGBM model achieves perfect sensitivity at the 95% specificity threshold,
indicating that the aggregated trend features capture the sepsis onset signal effectively. The
LSTM-Attention model marginally improves overall AUROC by leveraging raw temporal patterns in the
hourly sequence data.

#### 5.2.6 API Endpoints

| Method | Endpoint       | Description                                   |
|--------|----------------|-----------------------------------------------|
| POST   | `/predict`     | Predict sepsis probability from vitals/labs    |
| GET    | `/health`      | Service health check                          |
| GET    | `/model-info`  | Return model metadata and feature requirements|

---

### 5.3 App 03: Hospital Operations Intelligence

**Port:** 8203
**Directory:** `app_03_hospital_ops/`

#### 5.3.1 Clinical Purpose

Hospital-wide operational optimization through discrete-event simulation (DES) calibrated from
MIMIC-IV patient flow data. The system models patient arrivals, department transitions, and
discharge patterns to enable prospective analysis of staffing levels, bed utilization, and
bottleneck identification. The long-term design includes a multi-agent reinforcement learning
(MARL) layer for autonomous staffing recommendations, though this component is not yet trained.

#### 5.3.2 Data Sources

| Collection    | Database  | Key Fields                                  | Records Used        |
|---------------|-----------|---------------------------------------------|---------------------|
| `admissions`  | MIMIC     | admittime, dischtime, admission_type        | 431,088 admissions  |
| `transfers`   | MIMIC     | careunit, intime, outtime, eventtype        | 1,890,730 transfers |
| `icustays`    | MIMIC_ICU | first_careunit, los                         | 73,141 stays        |
| `services`    | MIMIC     | curr_service, transfertime                  | 467,851 records     |

#### 5.3.3 Simulation Architecture

The Hospital Ops module implements a discrete-event simulation engine (`des_engine.py`) coupled
with a Gymnasium-compatible environment wrapper (`environment.py`) for future MARL integration.

**Simulation Parameters:**

| Parameter              | Value                                    |
|------------------------|------------------------------------------|
| Time step              | 15 minutes                               |
| Simulation horizon     | 7 days (672 steps)                       |
| Departments modeled    | 8                                        |
| Arrival process        | Poisson, modulated by MIMIC intensity    |
| Discharge process      | Proportional to census / mean LOS        |

**MIMIC-Derived Arrival Profiles:**

Each of the 8 modeled departments has two distribution vectors derived from historical MIMIC-IV
admission patterns:

- **Hourly profile:** 24-point vector representing relative arrival intensity by hour of day
  (0.02 at 04:00 to 0.08 at 11:00, etc.)
- **Daily profile:** 7-point vector representing day-of-week modulation
  (Monday peak at 0.16, weekend trough at 0.12, etc.)

The instantaneous arrival rate for department `d` at simulation time `t` is computed as:

```
lambda_d(t) = base_rate_d * hourly_profile_d[hour(t)] * daily_profile_d[weekday(t)]
arrivals_d(t) ~ Poisson(lambda_d(t) * dt)
```

**Real Length-of-Stay Data (MIMIC-IV Derived):**

| Department      | Median LOS (hours) | Distribution        |
|-----------------|---------------------|---------------------|
| Emergency Dept  | 6.7                 | Log-normal          |
| ICU             | 36.1                | Log-normal          |
| Medicine        | 42.9                | Log-normal          |
| Oncology        | 55.3                | Log-normal          |

**Patient Flow Model:**

1. **Arrivals:** Poisson process with time-varying intensity from MIMIC profiles
2. **Department assignment:** Probabilistic routing based on observed MIMIC transfer patterns
3. **Length of stay:** Sampled from department-specific log-normal distributions fitted to MIMIC data
4. **Discharge:** Patients discharge when their sampled LOS expires; census-proportional discharge
   rate provides additional flow control
5. **Transfers:** Inter-department transfers follow a Markov chain with transition probabilities
   estimated from the `transfers` collection

**Staff Scheduling:**

Staff schedules are generated retrospectively after a 7-day simulation run completes:

1. Simulation runs for 672 steps, recording department census at each step
2. Utilization history is aggregated into 4-hour shift blocks (6 blocks per day, 42 per week)
3. For each department and shift block, the required staff count is computed from the utilization
   ratio (census / capacity) using configurable staffing ratios
4. The resulting schedule provides a baseline staffing plan grounded in MIMIC-realistic demand

#### 5.3.4 Current State and Model Status

The DES simulation engine is fully operational and produces MIMIC-calibrated patient flow
projections. The MARL (Multi-Agent Deep Deterministic Policy Gradient / MADDPG) training pipeline
is implemented but **not yet trained**. Consequently, there are no ML model performance metrics
for this application at this time. The simulation outputs are deterministic given a random seed
and serve as the training environment for future RL agent development.

#### 5.3.5 API Endpoints

| Method | Endpoint       | Description                                       |
|--------|----------------|---------------------------------------------------|
| POST   | `/simulate`    | Run simulation with given parameters               |
| GET    | `/health`      | Service health check                              |
| GET    | `/model-info`  | Return simulation configuration and parameters    |

---

### 5.4 App 04: Oncology AI

**Port:** 8204
**Directory:** `app_04_oncology_ai/`

#### 5.4.1 Clinical Purpose

Cancer pathway intelligence providing two core predictive capabilities: (1) 30-day hospital
readmission prediction for oncology patients, and (2) in-hospital mortality risk estimation.
These models support discharge planning, resource allocation, and early identification of
high-risk patients who may benefit from enhanced post-discharge follow-up or palliative care
referral.

#### 5.4.2 Data Sources

| Collection            | Database             | Key Fields                              | Records Used         |
|-----------------------|----------------------|-----------------------------------------|----------------------|
| `diagnoses_icd`       | MIMIC                | Cancer ICD codes (C00-C99, 140-239)     | ~35K oncology admissions |
| `procedures_icd`      | MIMIC                | Treatment procedure codes with dates    | Per admission         |
| `prescriptions`       | MIMIC                | Chemotherapy drug orders                | Per admission         |
| `admissions`          | MIMIC                | Readmission tracking, mortality flag    | Per admission         |
| `drgcodes`            | MIMIC                | drg_severity as cancer stage proxy      | Per admission         |
| `discharge`           | MIMIC_Clinical_Notes | Discharge summary text for NLP          | ~331K notes           |
| `d_icd_diagnoses`     | MIMIC                | Cancer type code definitions            | Lookup table          |

#### 5.4.3 Feature Engineering

- **Cancer type:** ICD code grouping into major cancer categories (lung, breast, colorectal, etc.)
- **Stage proxy:** DRG severity index (1-4) as surrogate for TNM staging
- **Treatment history:** Count and type of procedures, chemotherapy drug classes
- **Comorbidity burden:** Elixhauser comorbidity index computed from co-occurring ICD codes
- **Prior utilization:** Number of prior admissions, prior ED visits, days since last discharge
- **Demographics:** Age, gender
- **Admission context:** Admission type, insurance category
- **Temporal features:** Season of admission, day of week
- **Text features (Transformer only):** TF-IDF or learned embeddings from discharge summaries

**Label Construction:**
- **30-day readmission:** Binary -- next admission within 30 calendar days of discharge
- **Hospital mortality:** Binary -- `hospital_expire_flag = 1`

#### 5.4.4 Model Architectures

**Baseline: XGBoost Binary Classifiers (one per target)**

```
Algorithm:        XGBoost (gbtree booster)
Objective:        binary:logistic
Max depth:        5
Learning rate:    0.05
Num estimators:   400
Subsample:        0.8
Colsample bytree: 0.7
Min child weight: 10
Scale pos weight: auto
Eval metric:      auc
Early stopping:   25 rounds
```

**Advanced: Transformer on Treatment Sequences**

```
Architecture:     Encoder-only Transformer
Input:            Tokenized treatment sequences (procedures + drugs + diagnoses)
                  + continuous features via linear projection
Embedding dim:    128
Num heads:        4
Num layers:       3
FFN dim:          256
Dropout:          0.2
Pooling:          [CLS] token representation
FC head:          [128, 64] with LayerNorm + GELU + Dropout(0.3)
Output:           Sigmoid (binary probability, separate heads for readmission/mortality)
Optimizer:        AdamW (lr=3e-4, weight_decay=1e-3)
Scheduler:        CosineAnnealingWarmRestarts (T_0=10)
Loss:             BCEWithLogitsLoss (pos_weight adjusted)
Epochs:           40 (early stopping patience=8)
Batch size:       128
```

#### 5.4.5 Performance Metrics (Test Set)

**30-Day Readmission Prediction:**

| Model       | AUROC | F1 Score | Precision | Recall |
|-------------|-------|----------|-----------|--------|
| XGBoost     | 0.734 | 0.511    | --        | --     |
| Transformer | 0.733 | 0.506    | --        | --     |

**Hospital Mortality Prediction:**

| Model       | AUROC | F1 Score | Precision | Recall |
|-------------|-------|----------|-----------|--------|
| XGBoost     | 0.897 | 0.322    | --        | --     |
| Transformer | 0.876 | 0.268    | --        | --     |

The mortality prediction task achieves strong AUROC discrimination (0.897) but exhibits low F1
scores, reflecting the severe class imbalance inherent in hospital mortality data (low
prevalence of in-hospital death). The XGBoost baseline matches or marginally outperforms the
Transformer on both tasks, suggesting that the tabular feature representation captures the
dominant predictive signals and that the sequential treatment representation provides limited
additional benefit with current data volumes.

#### 5.4.6 API Endpoints

| Method | Endpoint       | Description                                        |
|--------|----------------|----------------------------------------------------|
| POST   | `/predict`     | Predict readmission and mortality risk              |
| GET    | `/health`      | Service health check                               |
| GET    | `/model-info`  | Return model metadata and feature requirements     |

---

### 5.5 App 05: Patient Journey

**Port:** 8205
**Directory:** `app_05_patient_journey/`

#### 5.5.1 Clinical Purpose

Longitudinal patient data exploration providing a unified view of a patient's hospital journey
including timeline of events, vital sign trends, laboratory results, medication history, and
department transitions. Supports cross-patient cohort comparison (up to 5 patients) for clinical
research and quality improvement.

#### 5.5.2 Backend Architecture

The Patient Journey module uses 5 engine modules that query MIMIC data live (no pre-built dataset):

| Engine Module | Lines | Purpose |
|--------------|-------|---------|
| `timeline.py` | 618 | Patient event timeline with department flow and timing |
| `vitals.py` | 184 | Vital signs retrieval and trending |
| `labs.py` | 225 | Laboratory results with reference ranges |
| `medications.py` | 187 | Medication history for Gantt chart visualization |
| `metrics.py` | 141 | Aggregate patient metrics and summary statistics |

#### 5.5.3 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/timeline/{subject_id}` | Patient event timeline |
| GET | `/vitals/{subject_id}` | Vital sign history |
| GET | `/labs/{subject_id}` | Laboratory results |
| GET | `/medications/{subject_id}` | Medication history |
| GET | `/metrics/{subject_id}` | Patient summary metrics |
| GET | `/patients` | Patient list / search |
| GET | `/health` | Service health check |

#### 5.5.4 Frontend

PatientJourney.tsx page with 4 tabs:
- **Timeline**: Department flow visualization with event timing
- **Vitals**: Configurable vital sign charts
- **Labs**: Lab result trends with reference ranges
- **Care Path**: Medication Gantt chart (color-coded by drug category) + cross-patient cohort comparison table (up to 5 patients)

#### 5.5.5 Data Sources

Uses existing MIMIC collections (admissions, transfers, chartevents, labevents, prescriptions, patients) via live MongoDB queries. No separate dataset build required. Patient lookup falls back to admissions collection when patients collection is unavailable.

---

## 6. Dashboard Architecture

### 6.1 Overview

The platform provides a unified React single-page application (SPA) that serves as the clinical
decision-support interface for all five applications. The dashboard is built with React 19.1,
TypeScript, Tailwind CSS, and Vite 6.4, and communicates with backend services exclusively
through RESTful API calls routed via the Vite development server proxy.

### 6.2 Directory Structure

```
dashboard/
  +-- index.html                   Entry point
  +-- vite.config.ts               Proxy routing configuration
  +-- package.json                 Dependencies
  +-- tsconfig.json                TypeScript configuration
  +-- src/
  |   +-- main.tsx                 Application bootstrap
  |   +-- App.tsx                  Root component with routing
  |   +-- index.css                Global styles (Tailwind directives)
  |   +-- lib/                     Utility functions, API clients
  |   +-- pages/
  |   |   +-- Overview.tsx         Platform landing / summary page
  |   |   +-- EdTriage.tsx         ED Triage clinical interface
  |   |   +-- SepsisIcu.tsx        Sepsis monitoring interface
  |   |   +-- HospitalOps.tsx      Operations simulation dashboard
  |   |   +-- Oncology.tsx         Oncology risk assessment interface
  |   |   +-- PatientJourney.tsx   Patient journey explorer (4 tabs)
  |   |   +-- SystemAdmin.tsx      System health, models, config (/system)
  |   +-- components/
  |       +-- Layout.tsx           Shared navigation shell and sidebar
  |       +-- RiskGauge.tsx        Circular gauge for risk probability display
  |       +-- TimelineView.tsx     Horizontal timeline for patient events
  |       +-- VitalSignChart.tsx   Real-time vital sign charting component
  |       +-- AcuityBadge.tsx      Color-coded ESI acuity level badge
  |       +-- StatCard.tsx         Summary statistic card with trend indicator
  +-- public/                      Static assets
  +-- dist/                        Production build output
```

### 6.3 Proxy Routing Configuration

The Vite development server proxies API requests to the appropriate backend service based on
URL prefix. This eliminates CORS concerns during development and mirrors the production
reverse-proxy topology.

```typescript
// vite.config.ts
server: {
  port: 3000,
  proxy: {
    "/api/ed":     { target: "http://localhost:8201", rewrite: strip("/api/ed") },
    "/api/sepsis": { target: "http://localhost:8202", rewrite: strip("/api/sepsis") },
    "/api/ops":    { target: "http://localhost:8203", rewrite: strip("/api/ops") },
    "/api/onco":   { target: "http://localhost:8204", rewrite: strip("/api/onco") },
    "/api/journey":{ target: "http://localhost:8205", rewrite: strip("/api/journey") },
  }
}
```

| Frontend Route | Proxy Prefix    | Backend Target      | Backend Port |
|----------------|-----------------|---------------------|--------------|
| ED Triage      | `/api/ed`       | ED Triage API       | 8201         |
| Sepsis ICU     | `/api/sepsis`   | Sepsis API          | 8202         |
| Hospital Ops   | `/api/ops`      | Ops API             | 8203         |
| Oncology       | `/api/onco`     | Oncology API        | 8204         |
| Patient Journey| `/api/journey`  | Patient Journey API | 8205         |

### 6.4 Shared Component Library

| Component          | Purpose                                                        | Used By            |
|--------------------|----------------------------------------------------------------|--------------------|
| `Layout`           | Application shell with sidebar navigation and header            | All pages          |
| `RiskGauge`        | Circular SVG gauge displaying 0-100% risk probability           | Sepsis, Oncology   |
| `TimelineView`     | Horizontal scrollable timeline showing patient events           | ED Triage, Sepsis  |
| `VitalSignChart`   | Line chart for real-time vital sign trending (Recharts-based)   | ED Triage, Sepsis  |
| `AcuityBadge`      | Color-coded badge (ESI 1=red to ESI 5=blue) for triage level   | ED Triage          |
| `StatCard`         | KPI card with value, label, trend arrow, and optional sparkline | All pages          |

### 6.5 Hospital Ops Real-Time Simulation Engine

The Hospital Ops page (`HospitalOps.tsx`) features a real-time simulation visualization that
renders the DES engine output as an animated operational dashboard. Key features include:

- **Department census heatmap:** 8 departments displayed with color-coded occupancy (green < 70%,
  yellow 70-85%, red > 85%)
- **Patient flow Sankey diagram:** Visual representation of inter-department transfers
- **Arrival rate chart:** Live plot of arrivals per department overlaid with MIMIC profile curves
- **Staff utilization timeline:** 4-hour shift blocks showing computed staffing recommendations
- **Simulation controls:** Start, pause, step-forward, speed multiplier (1x/2x/5x/10x), seed input

The simulation runs on the backend (port 8203) and streams state updates to the frontend,
which renders the 672-step, 7-day simulation as an animated progression through simulated time.

---

## 7. ML Model Performance Summary

### 7.1 Consolidated Results Table

| Application     | Model          | Task                  | AUROC | F1    | Accuracy | Sensitivity | Specificity | Notes                       |
|-----------------|----------------|-----------------------|-------|-------|----------|-------------|-------------|-----------------------------|
| ED Triage       | XGBoost        | 5-class acuity        | 0.728 | 0.653 | 0.665    | --          | --          | Macro AUROC, Weighted F1    |
| ED Triage       | Neural Net     | 5-class acuity        | 0.690 | 0.577 | 0.570    | --          | --          | Macro AUROC, Weighted F1    |
| Sepsis ICU      | LightGBM       | Sepsis onset (binary) | 0.994 | --    | --       | 1.000*      | 0.950*      | *At 95% specificity threshold|
| Sepsis ICU      | LSTM-Attention | Sepsis onset (binary) | 0.998 | --    | --       | 0.882       | 0.997       | Default threshold            |
| Oncology        | XGBoost        | 30-day readmission    | 0.734 | 0.511 | --       | --          | --          | Binary classification        |
| Oncology        | Transformer    | 30-day readmission    | 0.733 | 0.506 | --       | --          | --          | Binary classification        |
| Oncology        | XGBoost        | Hospital mortality    | 0.897 | 0.322 | --       | --          | --          | Class-imbalanced target      |
| Oncology        | Transformer    | Hospital mortality    | 0.876 | 0.268 | --       | --          | --          | Class-imbalanced target      |
| Hospital Ops    | DES Simulation | Operational KPIs      | --    | --    | --       | --          | --          | MARL not yet trained         |

### 7.2 Key Observations

1. **Sepsis detection achieves near-perfect discrimination.** Both LightGBM (AUROC 0.994) and
   LSTM-Attention (AUROC 0.998) demonstrate that the Sepsis-3 criteria can be predicted with
   high fidelity from SOFA-component features. This is expected given that the label is partially
   derived from the same feature space.

2. **Gradient-boosted trees match or exceed deep learning on tabular data.** Across all three
   applications with trained models, XGBoost/LightGBM baselines perform comparably to neural
   architectures (NN, LSTM, Transformer), consistent with established literature on tabular
   data modeling.

3. **Mortality prediction shows high AUROC but low F1.** The 0.897 AUROC for oncology mortality
   combined with 0.322 F1 indicates strong ranking ability but poor calibration at the default
   threshold, driven by severe class imbalance. Threshold optimization and calibration (Platt
   scaling, isotonic regression) are recommended next steps.

4. **ED Triage faces inherent label ambiguity.** The 0.665 accuracy on 5-class acuity reflects
   the subjective nature of ESI-equivalent labeling derived from proxy variables rather than
   ground-truth triage assessments.

---

## 8. Deployment Architecture

### 8.1 Service Topology

```
+------------------------------------------------------------------+
|                    Development Workstation                         |
|                    Windows 11 / RTX 4060 8GB                      |
|                                                                   |
|   +--------------------+                                          |
|   | Conda Environment  |                                          |
|   | Name: mitiosis     |                                          |
|   | Python: 3.10       |                                          |
|   | CUDA: 12.8         |                                          |
|   +----+---------------+                                          |
|        |                                                          |
|   +----v---------------+  +------------------+  +--------------+  |
|   | FastAPI Services   |  | Vite Dev Server  |  | MongoDB 7.x  |  |
|   |                    |  |                  |  |              |  |
|   | :8201 ED Triage    |  | :3000 Dashboard  |  | :27017       |  |
|   | :8202 Sepsis ICU   |  | (SPA + proxy)    |  | MIMIC        |  |
|   | :8203 Hospital Ops |  | 7 pages +        |  | MIMIC_ICU    |  |
|   | :8204 Oncology AI  |  | System Admin     |  | MIMIC_Clin.. |  |
|   | :8205 Pat. Journey |  |                  |  |              |  |
|   +--------------------+  +------------------+  +--------------+  |
|                                                                   |
+------------------------------------------------------------------+
```

### 8.2 Port Assignments

| Service             | Port  | Protocol | Process                       |
|---------------------|-------|----------|-------------------------------|
| MongoDB             | 27017 | TCP      | mongod                        |
| ED Triage API       | 8201  | HTTP     | uvicorn (FastAPI)             |
| Sepsis ICU API      | 8202  | HTTP     | uvicorn (FastAPI)             |
| Hospital Ops API    | 8203  | HTTP     | uvicorn (FastAPI)             |
| Oncology AI API     | 8204  | HTTP     | uvicorn (FastAPI)             |
| Patient Journey API | 8205  | HTTP     | uvicorn (FastAPI)             |
| Dashboard (dev)     | 3000  | HTTP     | Vite dev server               |

### 8.3 Environment Setup

```bash
# Activate the conda environment
conda activate mitiosis

# Install Python dependencies
pip install -e ".[dev,imbalance]"

# Build datasets (requires MongoDB running with MIMIC-IV loaded)
python scripts/build_all_datasets.py

# Train all models
python scripts/train_all_models.py

# Launch all backend services
python scripts/start_all_services.py

# In a separate terminal, start the dashboard
cd dashboard && npm install && npm run dev
```

### 8.4 Configuration

All application settings are centralized in `config.py` using `pydantic-settings`:

| Setting      | Default Value                      | Env Var Override |
|--------------|------------------------------------|------------------|
| `MONGO_URI`  | `mongodb://localhost:27017/`       | `MONGO_URI`      |
| `DATA_DIR`   | `D:/project-demo/cancer/datasets`  | `DATA_DIR`       |
| `MODEL_DIR`  | `D:/project-demo/cancer/models`    | `MODEL_DIR`      |
| `LOG_LEVEL`  | `INFO`                             | `LOG_LEVEL`      |
| `API_HOST`   | `0.0.0.0`                          | `API_HOST`       |
| `API_PORT`   | `8000`                             | `API_PORT`       |

Settings can be overridden via environment variables or a `.env` file in the project root.

---

## 9. Security & Compliance

### 9.1 MIMIC-IV Data Handling

MIMIC-IV is a de-identified clinical database released under a Data Use Agreement (DUA) by
PhysioNet / MIT Laboratory for Computational Physiology. The following safeguards are in place:

| Control                        | Implementation                                            |
|--------------------------------|-----------------------------------------------------------|
| Data access                    | MIMIC-IV data stored in local MongoDB only                |
| No PHI in version control      | All dataset directories are `.gitignore`d                 |
| No model artifacts in git      | All trained model files are `.gitignore`d                 |
| No credentials in source       | MongoDB connection string uses localhost (no auth)        |
| DUA compliance                 | MIMIC-IV credentialing required for data access           |

### 9.2 .gitignore Coverage

The following paths are excluded from version control to prevent accidental data exposure:

```
# Datasets (MIMIC-IV derived)
datasets/

# Trained model artifacts
models/

# Environment files
.env
*.env

# Python caches
__pycache__/
*.pyc

# Node modules
node_modules/
dist/

# Jupyter checkpoints
.ipynb_checkpoints/
```

### 9.3 Network Exposure

In development mode, all services bind to `localhost` or `0.0.0.0` without authentication or
TLS. Before any clinical deployment, the following must be implemented:

- TLS termination (HTTPS) at a reverse proxy layer
- JWT or OAuth2 authentication on all API endpoints
- Role-based access control (RBAC) for clinical vs. administrative users
- Audit logging of all prediction requests and responses
- Input validation and rate limiting
- HIPAA-compliant infrastructure (encrypted storage, access logging, BAA with cloud provider)

---

## 10. Known Limitations & Future Work

### 10.1 Known Limitations

| Area                | Limitation                                                                  |
|---------------------|-----------------------------------------------------------------------------|
| **Data provenance** | All models trained on MIMIC-IV (Beth Israel Deaconess, Boston) -- may not generalize to other populations, geographies, or care settings |
| **Label quality**   | ED acuity labels are proxy-derived (not ground-truth ESI scores), limiting triage model ceiling |
| **Sepsis label leakage** | Sepsis-3 labels partially overlap with input features (SOFA components), inflating apparent discrimination |
| **Mortality F1**    | Low F1 on mortality prediction due to class imbalance; threshold tuning and calibration needed |
| **Hospital Ops RL** | MARL agents (MADDPG) are architected but not yet trained; simulation is deterministic rule-based only |
| **Single GPU**      | RTX 4060 8GB VRAM limits batch sizes and model sizes for Transformer-based architectures |
| **No external validation** | No held-out external dataset for prospective evaluation |
| **No real-time inference** | APIs perform batch inference; true streaming vital-sign integration not yet implemented |
| **No NLP pipeline** | Discharge note embeddings for Oncology are TF-IDF only; clinical BERT integration is planned but not complete |

### 10.2 Future Work

| Priority | Item                                                                         | Target     |
|----------|------------------------------------------------------------------------------|------------|
| P0       | Train MARL (MADDPG) agents on Hospital Ops DES environment                  | Next phase |
| P0       | Threshold optimization and Platt scaling for mortality model                 | Next phase |
| P1       | Clinical BERT / Med-PaLM embeddings for Oncology discharge notes            | Next phase |
| P1       | WebSocket-based real-time vital sign streaming for Sepsis ICU dashboard      | Next phase |
| P1       | External validation on eICU Collaborative Research Database                  | Next phase |
| P2       | SHAP / LIME explainability layer for all prediction endpoints                | Planned    |
| P2       | Federated learning support for multi-site deployment                         | Planned    |
| P2       | A/B testing framework for model version comparison                           | Planned    |
| P3       | FHIR R4 integration for EHR interoperability                                 | Roadmap    |
| P3       | MLflow experiment tracking and model registry                                | Roadmap    |
| P3       | Kubernetes deployment manifests with Helm charts                             | Roadmap    |
| P3       | CI/CD pipeline (GitHub Actions) with automated model validation gates        | Roadmap    |

---

## Appendix A: Repository Structure

```
D:\project-demo\cancer\
  |
  +-- config.py                        Centralized pydantic-settings configuration
  +-- pyproject.toml                   Monorepo dependencies and tool configuration
  +-- README.md                        Platform overview
  |
  +-- shared/                          Reusable cross-application modules
  |   +-- __init__.py
  |   +-- db/                          MongoDB connection and query utilities
  |   +-- ml/                          Preprocessing, evaluation, model registry
  |   +-- utils/                       Structured logging, helpers
  |   +-- api/                         FastAPI app factory and base routes
  |
  +-- datasets/                        Generated datasets (gitignored)
  |   +-- ed_triage/
  |   +-- sepsis_icu/
  |   +-- hospital_ops/
  |   +-- oncology/
  |
  +-- models/                          Trained model artifacts (gitignored)
  |
  +-- app_01_ed_triage/                ED Triage AI application
  |   +-- backend/
  |   |   +-- data/                    Dataset builder
  |   |   +-- models/                  Model definitions and training
  |   |   +-- app/                     FastAPI service
  |   +-- frontend/                    Per-app frontend (optional)
  |
  +-- app_02_sepsis_icu/               Sepsis & ICU Watch application
  |   +-- backend/
  |   |   +-- data/
  |   |   +-- models/
  |   |   +-- app/
  |   +-- frontend/
  |
  +-- app_03_hospital_ops/             Hospital Operations Intelligence
  |   +-- backend/
  |   |   +-- data/
  |   |   +-- models/
  |   |   +-- simulation/              DES engine and Gymnasium environment
  |   |   +-- app/
  |   +-- frontend/
  |
  +-- app_04_oncology_ai/              Oncology AI application
  |   +-- backend/
  |   |   +-- data/
  |   |   +-- models/
  |   |   +-- app/
  |   +-- frontend/
  |
  +-- app_05_patient_journey/          Patient Journey application
  |   +-- backend/
  |   |   +-- engine/                  5 engine modules (timeline, vitals, labs, medications, metrics)
  |   |   +-- app/                     FastAPI service (port 8205, 7 endpoints)
  |
  +-- dashboard/                       Unified React SPA dashboard
  |   +-- src/
  |   |   +-- pages/                   7 page components + System Admin
  |   |   +-- components/              Shared UI components
  |   |   +-- lib/                     API clients and utilities
  |   +-- vite.config.ts               Proxy routing to backend services
  |
  +-- notebooks/                       Jupyter exploration notebooks
  +-- scripts/                         Orchestration scripts
  +-- docs/                            Technical documentation
```

---

## Appendix B: MongoDB Collection Quick Reference

| Database             | Collection          | Document Count  | Primary Use Case            |
|----------------------|---------------------|-----------------|-----------------------------|
| MIMIC                | admissions          | 431,088         | All apps: cohort base       |
| MIMIC                | transfers           | 1,890,730       | ED Triage, Hospital Ops     |
| MIMIC                | labevents           | 118,057,948     | ED Triage, Sepsis           |
| MIMIC                | prescriptions       | 15,399,811      | Sepsis, Oncology            |
| MIMIC                | diagnoses_icd       | 4,752,265       | All apps: label derivation  |
| MIMIC                | procedures_icd      | 668,993         | Oncology                    |
| MIMIC                | services            | 467,851         | Hospital Ops                |
| MIMIC                | drgcodes            | 603,645         | ED Triage, Oncology         |
| MIMIC                | patients            | 215             | All apps: demographics      |
| MIMIC                | d_labitems          | 1,623           | Lookup: lab definitions     |
| MIMIC                | d_icd_diagnoses     | 109,775         | Lookup: ICD definitions     |
| MIMIC                | d_icd_procedures    | 85,257          | Lookup: procedure codes     |
| MIMIC                | emar                | 26,743,071      | Medication administration   |
| MIMIC                | pharmacy            | 13,568,015      | Pharmacy orders             |
| MIMIC                | poe                 | 39,340,661      | Provider orders             |
| MIMIC                | omr                 | 6,422,067       | Outpatient records          |
| MIMIC_ICU            | chartevents         | 314,035,266     | ED Triage, Sepsis           |
| MIMIC_ICU            | icustays            | 73,141          | Sepsis, Hospital Ops        |
| MIMIC_ICU            | d_items             | 4,014           | Lookup: chart items         |
| MIMIC_ICU            | datetimeevents      | 7,117,467       | Temporal observations       |
| MIMIC_ICU            | ingredientevents    | 11,643,634      | Infusion data               |
| MIMIC_Clinical_Notes | discharge           | 331,793         | Oncology: NLP features      |

---

*Document generated for the Med AI Healthcare Platform. For questions or updates, refer to
the project repository at `D:\project-demo\cancer\`.*
