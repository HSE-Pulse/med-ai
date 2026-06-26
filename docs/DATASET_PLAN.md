# Dataset Construction Plan

## Overview

All datasets are derived from MongoDB (MIMIC, MIMIC_ICU, MIMIC_Clinical_Notes). Each app has a `build_dataset.py` script that extracts, transforms, labels, and splits data.

---

## App 01: ED Triage Dataset (BUILT)

### Source Query
```
admissions WHERE edregtime IS NOT NULL
  LEFT JOIN transfers WHERE careunit = "Emergency Department"
  LEFT JOIN patients ON subject_id
  LEFT JOIN drgcodes ON hadm_id
  LEFT JOIN diagnoses_icd ON hadm_id WHERE seq_num = 1
  LEFT JOIN labevents WHERE charttime BETWEEN edregtime AND edregtime + 2h
  LEFT JOIN chartevents WHERE charttime BETWEEN edregtime AND edregtime + 2h
```

### Actual Cohort Size
- **299,267 ED admissions** (admissions with edregtime != null)
- No further filtering applied beyond join completeness

### Features: 59 total (per ED visit)

| Feature | Source | Type | Handling Missing |
|---------|--------|------|------------------|
| age | patients.anchor_age | numeric | required (drop if missing) |
| gender | patients.gender | categorical (M/F) | required |
| heart_rate | chartevents 220045 | numeric | median impute, flag |
| respiratory_rate | chartevents 220210 | numeric | median impute, flag |
| spo2 | chartevents 220277 | numeric | median impute, flag |
| sbp | chartevents 220179 | numeric | median impute, flag |
| dbp | chartevents 220180 | numeric | median impute, flag |
| temperature | chartevents 223761 | numeric | median impute, flag |
| wbc | labevents 51301 | numeric | median impute, flag |
| hemoglobin | labevents 51222 | numeric | median impute, flag |
| platelet | labevents 51265 | numeric | median impute, flag |
| lactate | labevents 50813 | numeric | median impute, flag |
| glucose | labevents 50931 | numeric | median impute, flag |
| creatinine | labevents 50912 | numeric | median impute, flag |
| sodium | labevents 50983 | numeric | median impute, flag |
| potassium | labevents 50971 | numeric | median impute, flag |
| arrival_mode | admissions.admission_location | categorical | "UNKNOWN" |
| admission_type | admissions.admission_type | categorical | required |
| primary_dx_category | diagnoses_icd.icd_code[0:3] | categorical | "UNKNOWN" |
| drg_severity | drgcodes.drg_severity | ordinal (1-4) | median impute |
| hr_missing, rr_missing, ... | derived | binary (missingness flags) | 0 |

Total: 59 features including vitals, labs, ICD codes, arrival mode, and missingness flags.

### Labels

| Label | Derivation | Classes |
|-------|-----------|---------|
| acuity_level | Rule-based from admission_type + drg_severity + mortality | 1-5 (ESI equivalent) |
| disposition | discharge_location mapping | admit, discharge, transfer, expired |
| ed_los_hours | (edouttime - edregtime).total_seconds() / 3600 | continuous |

**Acuity derivation rules:**
- Level 1 (Resuscitation): hospital_expire_flag=1 OR ICU transfer within 4h
- Level 2 (Emergent): admission_type="EW EMER." AND drg_severity>=3
- Level 3 (Urgent): admission_type in ("URGENT", "DIRECT EMER.") OR drg_severity>=2
- Level 4 (Less Urgent): admission_type="EU OBSERVATION" OR drg_severity=1
- Level 5 (Non-urgent): admission_type in ("OBSERVATION ADMIT", "AMBULATORY OBSERVATION")

### Split Strategy
- Train: 70% (~209,000) | Validation: 15% (~44,000) | Test: 15% (~44,000)
- Stratified by acuity_level
- Temporal: train on earlier admissions, test on later (by admittime)

### Output (as built)
```
datasets/ed_triage/
  train.parquet    (~209,000 rows)
  val.parquet      (~44,000 rows)
  test.parquet     (~44,000 rows)
  metadata.json    (feature stats, class distributions)
```

---

## App 02: Sepsis & ICU Dataset (BUILT)

### Source Query
```
icustays (sampled 5,000 stays from 73,141 total)
  JOIN admissions ON hadm_id
  JOIN patients ON subject_id
  LEFT JOIN chartevents ON stay_id WHERE itemid IN (vital_signs)
  LEFT JOIN labevents ON hadm_id WHERE itemid IN (sepsis_labs)
  LEFT JOIN diagnoses_icd ON hadm_id WHERE icd_code IN (sepsis_codes)
  LEFT JOIN prescriptions ON hadm_id WHERE drug LIKE '%antibiotic%'
```

### Actual Cohort Size
- **5,000 ICU stays** sampled (from 73,141 total; chartevents has 314M rows so full extraction is impractical)
- **329,877 temporal windows** after windowing

### Features

**Time-series features (X_seq): 6 timesteps x 19 features per window**

| Feature | Source | itemid | Type |
|---------|--------|--------|------|
| heart_rate | chartevents | 220045 | numeric |
| respiratory_rate | chartevents | 220210 | numeric |
| spo2 | chartevents | 220277 | numeric |
| sbp | chartevents | 220179 | numeric |
| dbp | chartevents | 220180 | numeric |
| temperature | chartevents | 223761 | numeric |
| map | derived | (sbp + 2*dbp)/3 | numeric |
| wbc | labevents | 51301 | numeric |
| lactate | labevents | 50813 | numeric |
| creatinine | labevents | 50912 | numeric |
| platelets | labevents | 51265 | numeric |
| bilirubin | labevents | 50885 | numeric |
| bun | labevents | 51006 | numeric |
| inr | labevents | 51237 | numeric |
| sofa_resp | derived from SpO2 | - | ordinal 0-4 |
| sofa_coag | derived from Platelets | - | ordinal 0-4 |
| sofa_liver | derived from Bilirubin | - | ordinal 0-4 |
| sofa_cardio | derived from MAP | - | ordinal 0-4 |
| sofa_renal | derived from Creatinine | - | ordinal 0-4 |

**Flat statistical features (X_flat): 117 features** derived from time-series aggregation (mean, std, min, max, slope, etc.)

### Labels

| Label | Derivation |
|-------|-----------|
| sepsis_onset_4h | 1 if SOFA increases >=2 in next 4h AND sepsis ICD code present |

**Actual class balance: 114 positive windows out of 329,877 total (0.03% positive rate)**

### Split Strategy
- Patient-level split (no patient in both train and test)
- Train: ~230,000 windows | Validation: ~49,000 windows | Test: ~49,000 windows
- Stratified by sepsis_onset prevalence

### Output (as built)
```
datasets/sepsis_icu/
  train.npz        (X_seq: [N, 6, 19], X_flat: [N, 117], y: [N])  ~230K windows
  val.npz          ~49K windows
  test.npz         ~49K windows
  feature_names.json
  metadata.json
```

---

## App 03: Hospital Operations DES-MARL Dataset (PARTIALLY BUILT)

### Source Query
```
admissions (full 431K)
  JOIN transfers ON hadm_id ORDER BY intime
  LEFT JOIN services ON hadm_id
  LEFT JOIN icustays ON hadm_id
```

### Actual Data Extracted
- **1,560,641 transfers** from **431,088 admissions**
- patient_flows.parquet: **40.9 MB**

### Data Products

#### 1. Patient Flow Sequences (BUILT)
For each admission, extracted ordered list of department transitions:
```json
{
  "hadm_id": 12345,
  "admission_type": "EW EMER.",
  "pathway": [
    {"dept": "Emergency Department", "intime": "...", "outtime": "...", "los_hours": 4.2},
    {"dept": "Medicine", "intime": "...", "outtime": "...", "los_hours": 72.1},
    {"dept": "Discharge Lounge", "intime": "...", "outtime": "...", "los_hours": 1.5}
  ],
  "total_los_hours": 77.8
}
```

#### 2. Department Statistics (NOT FULLY BUILT - compute-heavy)
Per department, intended to compute:
- Mean/median/p95 LOS
- Hourly arrival rate (mean, variance)
- Peak occupancy
- Transfer-out probability matrix (dept A -> dept B)

#### 3. Arrival Patterns (PARTIALLY BUILT)
- MIMIC arrival profiles are embedded directly in the frontend (mimicArrivals.ts) rather than stored as separate dataset files
- These drive the client-side DES simulation

### Output (as built)
```
datasets/hospital_ops/
  patient_flows.parquet      (1,560,641 transfers from 431K admissions, 40.9MB)
```

**Not yet built:** dept_stats.json, arrival_patterns.parquet, transition_matrix.json, simulation_params.json (dept capacity and arrival pattern extraction is compute-heavy and pending)

---

## App 04: Oncology AI Dataset (BUILT)

### Source Query
```
diagnoses_icd WHERE icd_code LIKE 'C%' (ICD-10) OR icd_code BETWEEN '140' AND '239' (ICD-9)
  -> DISTINCT hadm_ids -> cancer_admissions
admissions WHERE hadm_id IN cancer_admissions
  JOIN patients ON subject_id
  LEFT JOIN procedures_icd ON hadm_id
  LEFT JOIN prescriptions ON hadm_id
  LEFT JOIN drgcodes ON hadm_id
  LEFT JOIN d_icd_diagnoses ON icd_code
  LEFT JOIN discharge (MIMIC_Clinical_Notes) ON hadm_id
```

### Actual Cohort Size
- **67,896 cancer admissions** from **29,549 unique patients**

### Features: 16 total (per admission)

| Feature | Source | Type |
|---------|--------|------|
| age | patients | numeric |
| gender | patients | categorical |
| cancer_type | primary cancer ICD category | categorical (lung, breast, colon, etc.) |
| cancer_icd_code | diagnoses_icd | categorical |
| stage_proxy | drgcodes.drg_severity | ordinal 1-4 |
| drg_mortality | drgcodes.drg_mortality | ordinal 1-4 |
| num_procedures | count of procedures_icd | numeric |
| has_surgery | any surgical procedure ICD | binary |
| has_chemotherapy | prescriptions matching chemo drugs | binary |
| has_radiation | procedure ICD for radiation | binary |
| num_prior_admissions | count of prior admissions for same patient | numeric |
| days_since_last_admission | derived | numeric |
| total_los_days | (dischtime - admittime) / 86400 | numeric |
| num_comorbidities | count of non-cancer ICD codes | numeric |
| charlson_score | derived from ICD codes | numeric |
| insurance | admissions.insurance | categorical |

### Labels: 4 targets

| Label | Derivation |
|-------|-----------|
| readmission_30d | 1 if same patient has another admission within 30 days of discharge |
| hospital_mortality | hospital_expire_flag |
| treatment_delay | time_to_first_procedure > median (binary) |
| long_stay | total_los > 75th percentile (binary) |

### Chemotherapy Drug Matching
Keywords in prescriptions.drug: cisplatin, carboplatin, paclitaxel, docetaxel, doxorubicin, cyclophosphamide, methotrexate, fluorouracil, 5-FU, gemcitabine, irinotecan, oxaliplatin, vincristine, etoposide, bleomycin, rituximab, trastuzumab, bevacizumab, pembrolizumab, nivolumab

### Split Strategy
- Patient-level split
- Stratified by cancer_type + readmission_30d
- Train: 70% (~47,000) | Validation: 15% (~9,900) | Test: 15% (~10,000)

### Clinical Notes Processing
For cancer patients with discharge summaries:
- Extract: diagnosis, treatment plan, follow-up recommendations
- Use for: treatment pathway feature augmentation, NLP-based risk factors
- Stored separately as notes.parquet with hadm_id, extracted_fields

### Output (as built)
```
datasets/oncology/
  train.parquet          (~47,000 rows)
  val.parquet            (~9,900 rows)
  test.parquet           (~10,000 rows)
  notes.parquet          (processed discharge summaries)
  treatments.parquet     (procedure/drug timeline per patient)
  metadata.json
```

---

## App 05: Patient Journey (NO SEPARATE DATASET)

The Patient Journey module does not require a pre-built dataset. It queries existing MIMIC collections (admissions, transfers, chartevents, labevents, prescriptions, patients) via live MongoDB queries at request time through its 5 engine modules (timeline.py, vitals.py, labs.py, medications.py, metrics.py). Patient lookup falls back to the admissions collection when the patients collection is unavailable.

No `build_dataset.py` script is needed for this module. All data access is handled by the FastAPI service on port 8205.

---

## Data Quality Checks (All Apps)

Each build_dataset.py must:
1. Print total records extracted per collection
2. Print missingness rates per feature (warn if >50%)
3. Print class distribution for all labels
4. Validate no PHI leakage (no names, MRNs, specific dates - only relative times)
5. Validate train/val/test have similar distributions
6. Save metadata.json with all statistics
