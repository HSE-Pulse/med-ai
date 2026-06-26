# Cancer Healthcare AI Platform - Architecture Overview

## Platform Summary

Five healthcare AI applications built on MIMIC-IV data (MongoDB), developed for evaluation with a partner hospital network.

| App | Name | Source Document Vertical | Primary Objective | Status |
|-----|------|--------------------------|-------------------|--------|
| 01 | ED Triage AI | Vertical 4: Emergency Department AI | ESI-equivalent acuity scoring + disposition prediction | Dataset built, models trained, API running |
| 02 | Sepsis & ICU Watch | Vertical 5: Sepsis & ICU Deterioration | Real-time sepsis onset prediction 4-6h before clinical recognition | Dataset built, models trained, API not running |
| 03 | Hospital Ops (DES-MARL) | Vertical 11: Hospital Operations Intelligence | Multi-agent staffing + patient flow optimization | Dataset partially built, simulation client-side, no MARL model |
| 04 | Oncology AI | Vertical 3: Oncology AI - Cancer Pathway | Cancer risk prediction + treatment pathway optimization | Dataset built, models trained, API running |
| 05 | Patient Journey | Cross-vertical | Patient timeline, vitals, labs, medications, care path with cohort comparison | No dataset (queries MIMIC live), API running |

---

## Data Architecture

### Source Databases (MongoDB)

```
MongoDB localhost:27017
  |
  +-- MIMIC (26 collections, ~260M+ documents)
  |     admissions          431,088    Core patient admissions
  |     transfers         1,890,730    Department movements
  |     labevents       118,057,948    Laboratory results
  |     prescriptions    15,399,811    Medications
  |     diagnoses_icd     4,752,265    ICD diagnosis codes
  |     procedures_icd      668,993    ICD procedure codes
  |     services            467,851    Service assignments
  |     drgcodes            603,645    DRG severity/mortality
  |     patients                215    Demographics
  |     d_labitems            1,623    Lab item definitions
  |     d_icd_diagnoses     109,775    ICD diagnosis lookup
  |     d_icd_procedures     85,257    ICD procedure lookup
  |     emar             26,743,071    Medication admin events
  |     pharmacy         13,568,015    Pharmacy orders
  |     poe              39,340,661    Provider order entries
  |     clinical_notes        1,203    Clinical notes (subset)
  |     omr               6,422,067    Outpatient medical records
  |
  +-- MIMIC_ICU (5 collections, ~332M+ documents)
  |     chartevents     314,035,266    Vital signs / observations
  |     icustays             73,141    ICU stay records
  |     d_items               4,014    Chart item definitions
  |     datetimeevents    7,117,467    Datetime observations
  |     ingredientevents 11,643,634    Infusion ingredients
  |
  +-- MIMIC_Clinical_Notes (1 collection)
        discharge           331,793    Discharge summaries
```

### Data Flow Pipeline

```
MongoDB (raw)
    |
    v
build_dataset.py (per app)
    |  - Cohort extraction
    |  - Feature engineering
    |  - Label derivation
    |  - Imputation
    |  - Train/Val/Test split
    v
datasets/ (parquet/npz)
    |
    v
train.py (per app)
    |  - Model training
    |  - Hyperparameter tuning
    |  - Evaluation
    v
models/ (serialized .joblib/.pt/.pkl + metadata .json)
    |
    v
FastAPI service (per app, where applicable)
    |  - /predict endpoints
    |  - /health, /model-info
    v
React Dashboard (unified, port 3000)
    |  - 7 pages + System Admin: Overview, ED Triage, Sepsis ICU, Hospital Ops,
    |    Oncology AI, Patient Journey, System Admin
    |  - Live API calls for ED Triage + Oncology + Patient Journey
    |  - Mock data + MIMIC profiles for Sepsis ICU + Hospital Ops
    v
Vite dev server with proxy to backend APIs
```

---

## Built Datasets

### App 01: ED Triage

| Metric | Value |
|--------|-------|
| Total cohort | 299,267 ED admissions |
| Features | 59 (vitals, labs, ICD, arrival mode, missingness flags) |
| Targets | acuity_level (1-5), disposition, ed_los_hours |
| Train split | ~209,000 rows |
| Val split | ~44,000 rows |
| Test split | ~44,000 rows |
| Format | parquet |

### App 02: Sepsis & ICU

| Metric | Value |
|--------|-------|
| ICU stays sampled | 5,000 |
| Total windows | 329,877 |
| Time-series shape (X_seq) | 6 timesteps x 19 features |
| Flat features (X_flat) | 117 statistical features |
| Positive rate | 114 positive (0.03%) |
| Train split | ~230,000 windows |
| Val split | ~49,000 windows |
| Test split | ~49,000 windows |
| Format | npz |

### App 03: Hospital Ops

| Metric | Value |
|--------|-------|
| Total transfers | 1,560,641 from 431,088 admissions |
| patient_flows.parquet | 40.9 MB |
| Dept capacity & arrival patterns | Not fully built (compute-heavy) |
| Format | parquet |

### App 04: Oncology AI

| Metric | Value |
|--------|-------|
| Cancer admissions | 67,896 (29,549 unique patients) |
| Features | 16 |
| Targets | readmission_30d, hospital_mortality, treatment_delay, long_stay |
| Train split | ~47,000 rows |
| Val split | ~9,900 rows |
| Test split | ~10,000 rows |
| Additional files | notes.parquet, treatments.parquet |
| Format | parquet |

---

## Application-to-Data Mapping

### App 01: ED Triage

| Data Source | Collection | Key Fields Used | Record Count |
|-------------|------------|-----------------|--------------|
| MIMIC | admissions | edregtime, edouttime, admission_type, hospital_expire_flag | 299,267 ED visits |
| MIMIC | transfers | careunit="Emergency Department", intime, outtime | ~625K ED transfers |
| MIMIC | labevents | itemid (WBC, Lactate, Glucose, Creatinine, Troponin), valuenum | Sampled per visit |
| MIMIC_ICU | chartevents | itemid (HR, RR, SpO2, SBP, DBP, Temp), valuenum | First vitals per visit |
| MIMIC | diagnoses_icd | icd_code (primary), seq_num=1 | Per admission |
| MIMIC | drgcodes | drg_severity | Per admission |
| MIMIC | patients | gender, anchor_age | Per patient |

**Label Construction:**
- Acuity (1-5): Derived from admission_type severity + DRG severity + disposition
- Disposition: discharge_location + hospital_expire_flag
- ED LOS: edouttime - edregtime (hours)

### App 02: Sepsis & ICU

| Data Source | Collection | Key Fields Used | Record Count |
|-------------|------------|-----------------|--------------|
| MIMIC_ICU | icustays | stay_id, intime, outtime, los, first_careunit | 5,000 stays (sampled) |
| MIMIC_ICU | chartevents | HR, RR, SpO2, SBP, DBP, Temp, GCS (hourly) | Sampled per stay |
| MIMIC | labevents | WBC, Lactate, Creatinine, Platelets, Bilirubin, BUN, INR | Sampled per stay |
| MIMIC | admissions | hospital_expire_flag | Per admission |
| MIMIC | diagnoses_icd | Sepsis codes (A40, A41, 995.91, 995.92) | Per admission |
| MIMIC | prescriptions | Antibiotics (drug field keyword matching) | Per admission |
| MIMIC | patients | gender, anchor_age | Per patient |

**Label Construction (Sepsis-3 Approximation):**
- SOFA score computed hourly from vitals + labs
- Sepsis onset = SOFA increase >= 2 AND sepsis ICD code present
- Prediction target: sepsis onset within next 4 hours
- Actual positive rate: 0.03% (114 of 329,877 windows)

### App 03: Hospital Operations DES-MARL

| Data Source | Collection | Key Fields Used | Record Count |
|-------------|------------|-----------------|--------------|
| MIMIC | admissions | admittime, dischtime, admission_type | 431,088 admissions |
| MIMIC | transfers | careunit, intime, outtime, eventtype | 1,560,641 transfers (used) |
| MIMIC_ICU | icustays | first_careunit, los | 73,141 stays |
| MIMIC | services | curr_service, transfertime | 467,851 service records |

**Simulation Calibration:**
- Arrival rates: MIMIC arrival profiles embedded in frontend (mimicArrivals.ts)
- Simulation: client-side DES with 15-min steps, 1x/2x/5x/10x speed controls
- No MARL model trained yet

### App 04: Oncology AI

| Data Source | Collection | Key Fields Used | Record Count |
|-------------|------------|-----------------|--------------|
| MIMIC | diagnoses_icd | Cancer ICD codes (C00-C99 ICD-10, 140-239 ICD-9) | 67,896 oncology admissions |
| MIMIC | procedures_icd | Treatment procedures with dates | Per admission |
| MIMIC | prescriptions | Chemotherapy drugs | Per admission |
| MIMIC | admissions | Readmission tracking, mortality | Per admission |
| MIMIC | drgcodes | drg_severity as stage proxy | Per admission |
| MIMIC_Clinical_Notes | discharge | Discharge summaries for NLP | Per admission |
| MIMIC | d_icd_diagnoses | Cancer type definitions | Lookup |

**Label Construction:**
- 30-day readmission: next admission within 30 days of discharge
- Hospital mortality: hospital_expire_flag
- Treatment delay: days from diagnosis admission to first procedure
- Long stay: total_los > 75th percentile

---

## Model Results

### App 01: ED Triage (Trained)

| Model | Format | F1 (Weighted) | AUROC | Notes |
|-------|--------|---------------|-------|-------|
| XGBoost multiclass | .joblib | **0.653** | **0.728** | Best overall, served via API |
| Neural Net (embeddings + dense) | .pt | 0.577 | 0.690 | Lower than XGBoost |

### App 02: Sepsis ICU (Trained)

| Model | Format | AUROC | Sensitivity@95%Spec | Notes |
|-------|--------|-------|---------------------|-------|
| LightGBM (flat features) | .pkl | 0.994 | 1.0 | Strong baseline |
| LSTM-Attention (temporal) | .pt | **0.998** | - | Best overall |

Note: Very high metrics reflect the extremely low positive rate (0.03%). Model performance should be validated with larger sepsis-positive cohorts.

### App 03: Hospital Ops (No Model)

No trained MARL model. The simulation runs client-side with MIMIC arrival profiles driving patient flow, with real-time DES and staff schedule generation.

### App 04: Oncology AI (Trained)

| Model | Task | Format | AUROC |
|-------|------|--------|-------|
| XGBoost | Readmission | .joblib | **0.734** |
| Transformer | Readmission | .pt | 0.733 |
| XGBoost | Mortality | .joblib | **0.897** |
| Transformer | Mortality | .pt | 0.876 |

---

## Technology Stack (Actual Versions)

| Layer | Technology | Version |
|-------|-----------|---------|
| Language | Python | 3.10 (conda env: mitiosis) |
| Data Store | MongoDB | 7.x |
| Dataset Cache | Parquet / NumPy | - |
| ML: Deep Learning | PyTorch | 2.11.0+cu128 |
| ML: Gradient Boosting | XGBoost | 3.2 |
| ML: Gradient Boosting | LightGBM | 4.6 |
| ML: Utilities | scikit-learn | 1.7 |
| ML: Data | pandas | 2.3 |
| API Layer | FastAPI | 0.135 |
| API Server | uvicorn | 0.42 |
| Database Driver | pymongo | 4.16 |
| Frontend Framework | React | 19.1 |
| Frontend Language | TypeScript | - |
| Frontend Build | Vite | 6.4 |
| Frontend CSS | Tailwind CSS | - |
| Frontend Charts | Recharts | - |
| GPU | NVIDIA RTX 4060 8GB | CUDA 12.8 |

---

## API Port Assignments

| App | API Port | Status | Dashboard |
|-----|----------|--------|-----------|
| 01 ED Triage | 8201 | Running (FastAPI + XGBoost) | Live API calls via Vite proxy |
| 02 Sepsis ICU | 8202 | Not running (model trained, no API serving) | Mock data in dashboard |
| 03 Hospital Ops | 8203 | Not running (simulation is client-side) | Client-side DES + MIMIC arrivals |
| 04 Oncology AI | 8204 | Running (FastAPI + XGBoost readmission/mortality + pathway engine) | Live API calls via Vite proxy |
| 05 Patient Journey | 8205 | Running (FastAPI + timeline/vitals/labs/medications/metrics engines) | Live API calls via Vite proxy |

Unified React dashboard on port 3000 (Vite dev server) with 7 pages + 1 admin: Overview, ED Triage, Sepsis ICU, Hospital Ops, Oncology AI, Patient Journey, System Admin.

---

## Dashboard Features

### ED Triage (port 3000, live via API 8201)
- Patient vital sign input and acuity prediction
- Disposition prediction

### Sepsis ICU (port 3000, mock data)
- ICU patient monitoring grid
- Risk visualization (uses mock data, not live API)

### Hospital Ops (port 3000, client-side simulation)
- Real-time DES simulation with 15-min steps
- Speed controls: 1x, 2x, 5x, 10x
- MIMIC arrival patterns driving patient flow (embedded in mimicArrivals.ts)
- 7-day staff schedule generation
- Charts updating live

### Oncology AI (port 3000, live via API 8204)
- Risk assessment with RiskGauge component (fixed SVG clipping with HTML text overlay)
- Treatment pathway with TimelineView (fixed crash: added all backend category names + fallback)
- Cohort analytics with live MIMIC stats
- Clinical note analyzer tab (NLP via /analyze-note endpoint)

### Patient Journey (port 3000, live via API 8205)
- PatientJourney.tsx page with 4 tabs: Timeline, Vitals, Labs, Care Path
- Timeline view of patient events across departments with timing
- Vitals charts with configurable vital sign selection
- Lab trends with reference ranges
- Medication Gantt chart (color-coded by drug category)
- Cross-patient cohort comparison table (up to 5 patients)
- Department flow visualization with timing

### System Admin (/system page)
- System Health Monitor: pings all 5 API services (ports 8201-8205)
- Model Performance Table: 8 models with AUROC/F1 metrics
- Service Configuration: ports, module paths, start commands
- Dataset Inventory: 4 datasets with record counts and sizes
- Technology Stack: versions grid

---

## Folder Structure

```
D:\project-demo\cancer\
|
+-- config.py                    # Global settings (pydantic-settings)
+-- pyproject.toml               # Monorepo dependencies
+-- README.md                    # Platform overview
|
+-- shared/                      # Reusable modules
|   +-- db/
|   |   +-- mongo.py             # MongoDB connection manager
|   |   +-- queries.py           # Pre-built aggregation pipelines
|   +-- ml/
|   |   +-- preprocessing.py     # Imputation, normalization, windowing
|   |   +-- evaluation.py        # Metrics, plots, reports
|   |   +-- registry.py          # Model save/load with metadata
|   +-- utils/
|   |   +-- logging.py           # Structured logging
|   +-- api/
|       +-- base.py              # FastAPI app factory
|
+-- datasets/                    # Generated datasets (gitignored)
|   +-- ed_triage/               # 299K rows, parquet
|   +-- sepsis_icu/              # 329K windows, npz
|   +-- hospital_ops/            # 1.56M transfers, parquet
|   +-- oncology/                # 67K admissions, parquet + notes + treatments
|
+-- app_01_ed_triage/
|   +-- backend/
|   |   +-- data/
|   |   |   +-- build_dataset.py
|   |   +-- models/
|   |   |   +-- triage_model.py
|   |   |   +-- train.py
|   |   +-- app/
|   |       +-- main.py
|   |       +-- schemas.py
|   +-- frontend/
|   |   +-- src/App.tsx
|   |   +-- package.json
|   +-- README.md
|
+-- app_02_sepsis_icu/
|   +-- backend/
|   |   +-- data/
|   |   |   +-- build_dataset.py
|   |   +-- models/
|   |   |   +-- sepsis_model.py
|   |   |   +-- train.py
|   |   +-- app/
|   |       +-- main.py
|   |       +-- schemas.py
|   +-- frontend/
|   |   +-- src/App.tsx
|   +-- README.md
|
+-- app_03_hospital_ops/
|   +-- backend/
|   |   +-- data/
|   |   |   +-- build_dataset.py
|   |   +-- simulation/
|   |   |   +-- environment.py
|   |   |   +-- des_engine.py
|   |   +-- models/
|   |   |   +-- marl_agent.py
|   |   |   +-- train.py
|   |   +-- app/
|   |       +-- main.py
|   |       +-- schemas.py
|   +-- frontend/
|   |   +-- src/App.tsx
|   +-- README.md
|
+-- app_04_oncology_ai/
|   +-- backend/
|   |   +-- data/
|   |   |   +-- build_dataset.py
|   |   +-- models/
|   |   |   +-- risk_model.py
|   |   |   +-- pathway_optimizer.py
|   |   |   +-- train.py
|   |   +-- app/
|   |       +-- main.py
|   |       +-- schemas.py
|   +-- frontend/
|   |   +-- src/App.tsx
|   +-- README.md
|
+-- app_05_patient_journey/
|   +-- backend/
|   |   +-- engine/
|   |   |   +-- timeline.py          # 618 lines - patient event timeline
|   |   |   +-- vitals.py            # 184 lines - vital signs retrieval
|   |   |   +-- labs.py              # 225 lines - lab results retrieval
|   |   |   +-- medications.py       # 187 lines - medication history
|   |   |   +-- metrics.py           # 141 lines - patient metrics
|   |   +-- app/
|   |       +-- main.py              # FastAPI on port 8205, 7 endpoints
|
+-- notebooks/                   # Exploration / EDA
|   +-- 01_data_audit.ipynb
|   +-- 02_ed_triage_eda.ipynb
|   +-- 03_sepsis_eda.ipynb
|   +-- 04_hospital_ops_eda.ipynb
|   +-- 05_oncology_eda.ipynb
|
+-- docs/
|   +-- ARCHITECTURE.md          # This file
|   +-- DATASET_PLAN.md          # Dataset construction details
|   +-- ROADMAP.md               # Execution phases
|
+-- scripts/
    +-- build_all_datasets.py    # Run all dataset builders
    +-- train_all_models.py      # Train all models
    +-- start_all_services.py    # Launch all APIs
```
