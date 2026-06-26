# Execution Roadmap

## Phase 1: Data Audit & Schema Discovery -- COMPLETED

### Tasks
- [x] Verify MongoDB connectivity and collection sizes
- [x] Sample 10 documents from each key collection, validate field names and types
- [x] Check edregtime non-null count (result: 299,267 ED admissions)
- [x] Check ICU stays with chartevents coverage
- [x] Identify cancer ICD codes in diagnoses_icd (C00-C99, 140-239)
- [x] Profile vital sign completeness in chartevents for key itemids
- [x] Profile lab completeness for sepsis-relevant labs
- [x] Document any schema discrepancies

### Deliverables
- `notebooks/01_data_audit.ipynb` with full schema validation
- Updated DATASET_PLAN.md with actual counts

---

## Phase 2: Dataset Construction -- COMPLETED (3 of 4 fully built)

### Execution Order & Results

1. **App 01 ED Triage** -- DONE
   - 299,267 ED admissions, 59 features, 3 targets
   - Train 209K / Val 44K / Test 44K (parquet)

2. **App 04 Oncology** -- DONE
   - 67,896 cancer admissions (29,549 patients), 16 features, 4 targets
   - Train 47K / Val 9.9K / Test 10K (parquet) + notes.parquet + treatments.parquet

3. **App 03 Hospital Ops** -- PARTIALLY DONE
   - 1,560,641 transfers from 431,088 admissions -> patient_flows.parquet (40.9MB)
   - Dept capacity and arrival patterns not fully extracted (compute-heavy)
   - MIMIC arrival profiles embedded directly in frontend (mimicArrivals.ts)

4. **App 02 Sepsis ICU** -- DONE
   - 5,000 ICU stays (sampled from 73,141), 329,877 windows
   - X_seq: 6x19 time series, X_flat: 117 flat features, 114 positive (0.03%)
   - Train 230K / Val 49K / Test 49K (npz)

---

## Phase 3: Baseline Models -- COMPLETED

### Models Trained (all on NVIDIA RTX 4060 8GB, CUDA 12.8)

| App | Model | Result | Target Met? |
|-----|-------|--------|-------------|
| 01 ED Triage | XGBoost multiclass (acuity) | F1=0.653, AUROC=0.728 | Yes (target: F1>0.60) |
| 02 Sepsis ICU | LightGBM binary (sepsis onset) | AUROC=0.994, sens@95spec=1.0 | Yes (target: AUROC>0.75) |
| 03 Hospital Ops | Rule-based scheduling baseline | N/A (no trained model) | Simulation runs client-side |
| 04 Oncology | XGBoost readmission | AUROC=0.734 | Yes (target: AUROC>0.65) |
| 04 Oncology | XGBoost mortality | AUROC=0.897 | Yes |

---

## Phase 4: APIs & Dashboards -- COMPLETED (partially)

### API Status

| App | Port | Status |
|-----|------|--------|
| 01 ED Triage | 8201 | Running (FastAPI + loaded XGBoost model) |
| 02 Sepsis ICU | 8202 | NOT running (model trained but no API serving yet) |
| 03 Hospital Ops | 8203 | NOT running (simulation is client-side only) |
| 04 Oncology AI | 8204 | Running (FastAPI + XGBoost readmission/mortality + pathway engine) |
| 05 Patient Journey | 8205 | Running (FastAPI + timeline/vitals/labs/medications/metrics engines) |

### Unified Dashboard (React + Vite, port 3000) -- BUILT

7 pages + 1 admin: Overview, ED Triage, Sepsis ICU, Hospital Ops, Oncology AI, Patient Journey, System Admin

| Page | Data Source | Key Components |
|------|-------------|----------------|
| Overview | Live KPIs from APIs | 299K ED, 67.9K cancer, 29.5K patients; ED board, Sepsis watch, Dept utilization, Oncology overview |
| ED Triage | Live API calls to port 8201 via Vite proxy | Live predictor wired to /predict, feature importance chart, acuity pie, model metrics |
| Sepsis ICU | Mock data (no live API) | Patient grid, detail view with vitals, interactive SOFA calculator (5 components + presets), model performance cards |
| Hospital Ops | Client-side DES simulation + MIMIC arrival profiles (mimicArrivals.ts) | Real-time DES with 15-min steps, 1x/2x/5x/10x speed, MIMIC arrival heatmap (8 depts x 24h), dept LOS comparison, 7-day staff schedule, live charts |
| Oncology AI | Live API calls to port 8204 via Vite proxy | RiskGauge, TimelineView, cohort analytics, clinical note analyzer (NLP via /analyze-note) |
| Patient Journey | Live API calls to port 8205 via Vite proxy | Timeline, vitals charts, lab trends, care path, medication Gantt chart, cross-patient cohort comparison (up to 5) |
| System Admin | Pings all 5 APIs | System health monitor, model performance table (8 models), service config, dataset inventory, tech stack |

---

## Phase 5: Advanced Models -- COMPLETED (3 of 4)

| App | Model | Architecture | Result |
|-----|-------|-------------|--------|
| 01 ED Triage | TriageNN | Embedding + Dense + Softmax (.pt) | F1=0.577, AUROC=0.690 (below XGBoost) |
| 02 Sepsis ICU | LSTM-Attention | LSTM + Attention + Dense (.pt) | AUROC=0.998 (best, above LightGBM) |
| 03 Hospital Ops | MADDPG | Not trained | Pending |
| 04 Oncology | Transformer (readmission) | Treatment sequence transformer (.pt) | AUROC=0.733 (on par with XGBoost 0.734) |
| 04 Oncology | Transformer (mortality) | Treatment sequence transformer (.pt) | AUROC=0.876 (below XGBoost 0.897) |

### DES-MARL Training Schedule (App 03) -- NOT STARTED
- Stage 1: Single department (ED only) - 500 episodes
- Stage 2: 3 departments (ED, Medicine, ICU) - 1,000 episodes
- Stage 3: 5 departments - 2,000 episodes
- Stage 4: All 8 departments - 5,000 episodes
- Stage 5: Full complexity with stochastic arrivals - 10,000 episodes

---

## Phase 6: Integration & Demo Packaging -- PARTIALLY COMPLETED

### Completed
- [x] Unified React dashboard with cross-app navigation (7 pages + System Admin on port 3000)
- [x] Vite proxy routing to backend APIs (ED Triage, Oncology, Patient Journey)
- [x] Client-side DES simulation for Hospital Ops with MIMIC arrival data
- [x] Mock data fallback for Sepsis ICU page
- [x] Patient Journey module: backend (5 engine modules, 7 API endpoints on port 8205) + frontend (PatientJourney.tsx with 4 tabs)
- [x] System Admin page: health monitor, model table, service config, dataset inventory, tech stack
- [x] 10 new dashboard widgets (see Phase 7 below)

### Pending
- [ ] Unified launcher script (start_all_services.py)
- [ ] Sepsis ICU API serving (port 8202)
- [ ] Hospital Ops API serving (port 8203) or decision to keep client-side only
- [ ] Hospital Ops: complete dept capacity and arrival pattern extraction
- [ ] Hospital Ops: train MARL model
- [ ] Performance benchmarking (latency per endpoint)
- [ ] Docker Compose for containerized deployment
- [ ] Demo data seeding (pre-computed predictions for demo mode)

---

## Phase 7: Dashboard Widgets & Patient Journey -- COMPLETED

### New Module: Patient Journey (app_05_patient_journey)
- [x] Backend: 5 engine modules (timeline.py 618L, vitals.py 184L, labs.py 225L, medications.py 187L, metrics.py 141L)
- [x] API: FastAPI on port 8205 with 7 endpoints serving real MIMIC data
- [x] Frontend: PatientJourney.tsx page with 4 tabs (Timeline, Vitals, Labs, Care Path)
- [x] Medication Gantt chart (color-coded by drug category)
- [x] Cross-patient cohort comparison (up to 5 patients)
- [x] Department flow with timing

### 10 New Dashboard Widgets -- ALL COMPLETED
1. [x] Overview: Live KPIs from APIs (299K ED, 67.9K cancer, 29.5K patients)
2. [x] ED Triage: Live predictor wired to /predict API + feature importance chart + acuity pie + model metrics
3. [x] Oncology: Clinical note analyzer tab (NLP via /analyze-note)
4. [x] Overview: System health monitor (moved to /system page)
5. [x] Overview: Model performance table (moved to /system page)
6. [x] Patient Journey: Medication Gantt chart (color-coded by drug category)
7. [x] Hospital Ops: MIMIC arrival heatmap (8 depts x 24h)
8. [x] Sepsis ICU: Interactive SOFA calculator (5 components + presets)
9. [x] Hospital Ops: Department LOS comparison bar chart
10. [x] Patient Journey: Cross-patient cohort comparison table

### Bug Fixes Applied
- [x] ED Triage /predict: fixed feature_names mismatch (excluded string columns from training features)
- [x] ED Triage frontend: fixed API envelope unwrapping (acuity_level -> esi_level, confidence 0-1 -> 0-100%)
- [x] Sepsis detail view: fixed React hooks ordering (moved useState before conditional return)
- [x] Patient Journey: fixed vitals empty (VITAL_CONFIG keys matched to API response names)
- [x] Patient Journey: fixed useState -> useEffect for all data fetching
- [x] Patient Journey: fixed medication schema (dose_val_rx accepts Any type)
- [x] Patient Journey: fixed patient lookup (falls back to admissions when patients collection missing)
- [x] Oncology: fixed RiskGauge SVG clipping (HTML text overlay with background knockout)
- [x] Oncology: fixed TimelineView crash (added all backend category names + fallback)

---

## Assumptions

1. MongoDB is running locally on port 27017 with MIMIC, MIMIC_ICU, MIMIC_Clinical_Notes databases
2. Python 3.10 with conda env "mitiosis", CUDA 12.8 on RTX 4060 8GB
3. Node.js for frontend builds
4. All data is de-identified MIMIC-IV (no PHI)
5. chartevents queries are limited to 5,000 stays (from 73,141 total) to avoid memory/time issues on 314M rows
6. Cancer ICD codes follow both ICD-9 (140-239) and ICD-10 (C00-C99) conventions present in MIMIC-IV
7. Sepsis labels are approximate (Sepsis-3 criteria adapted to available data fields)
8. DES-MARL simulation parameters are calibrated from MIMIC data but would need recalibration for the partner hospital deployment

---

## Missing Information Needed

1. **the partner hospital-specific data schemas:** When real the partner hospital data becomes available, adapter layers will need mapping
2. **ESI ground truth:** MIMIC-IV does not include ESI triage scores; our acuity labels are derived approximations
3. **Antibiotic list:** Complete list of antibiotic drug names for sepsis definition matching against prescriptions.drug
4. **Cancer staging data:** MIMIC-IV lacks explicit TNM staging; DRG severity is a proxy
5. **Real-time vital sign streaming protocol:** For production sepsis monitoring, need HL7/FHIR integration specs
6. **the partner hospital department structure:** Simulation departments may differ from MIMIC's structure
