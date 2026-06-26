# Model Registry — Med AI Healthcare Platform

Complete inventory of all machine learning models across the monorepo, including features, outputs, architecture, hyperparameters, and performance metrics.

---

## Summary

| App | Model | Type | Task | Features | Best Metric |
|-----|-------|------|------|----------|-------------|
| ED Triage | TriageXGBoost | XGBoost | Acuity 5-class | 59 | AUROC 0.728 |
| ED Triage | TriageNN | Neural Net | Acuity 5-class | 59 | AUROC 0.690 |
| Sepsis ICU | SepsisLGBM | LightGBM | Sepsis binary | 127 (flat) | AUROC 0.994 |
| Sepsis ICU | SepsisLSTM | Bi-LSTM + Attention | Sepsis binary | 6×19 (seq) | AUROC 0.998 |
| Oncology AI | XGB Readmission | XGBoost | 30-day readmit | 16 | AUROC 0.734 |
| Oncology AI | XGB Mortality | XGBoost | In-hospital death | 16 | AUROC 0.897 |
| Oncology AI | Transformer Readmission | TabTransformer | 30-day readmit | 16 | AUROC 0.733 |
| Oncology AI | Transformer Mortality | TabTransformer | In-hospital death | 16 | AUROC 0.876 |
| Hospital Ops | MADDPG | Multi-Agent RL | Resource allocation | 12/agent | Continuous Q-value |
| Clinical Chat | Ollama LLMs | LLM ensemble | Clinical QA | Free text | N/A |

---

## 1. ED Triage (`app_01_ed_triage`)

### 1.1 TriageXGBoost (Served)

| Field | Value |
|-------|-------|
| **Type** | XGBoost multiclass classifier |
| **Task** | ESI-equivalent acuity scoring (1–5) |
| **Model path** | `models/ed_triage/ed_triage_xgb.joblib` |
| **Best model** | `models/ed_triage/ed_triage_best.joblib` |
| **Dataset** | 299K admissions from MIMIC-IV |
| **Output** | Predicted acuity class (1–5) + per-class probabilities |

#### Features (59)

| Group | Features |
|-------|----------|
| **Demographics** (2) | `age`, `gender_encoded` |
| **Vitals** (6) | `heart_rate`, `respiratory_rate`, `spo2`, `sbp`, `dbp`, `temperature` |
| **Labs** (10) | `wbc`, `hemoglobin`, `platelets`, `lactate`, `glucose`, `creatinine`, `bun`, `troponin`, `sodium`, `potassium` |
| **Vital missingness** (6) | `heart_rate_missing`, `respiratory_rate_missing`, `spo2_missing`, `sbp_missing`, `dbp_missing`, `temperature_missing` |
| **Lab missingness** (10) | `wbc_missing`, `hemoglobin_missing`, `platelets_missing`, `lactate_missing`, `glucose_missing`, `creatinine_missing`, `bun_missing`, `troponin_missing`, `sodium_missing`, `potassium_missing` |
| **Arrival mode** (5) | `arrival_emergency_room`, `arrival_physician_referral`, `arrival_transfer_from_hospital`, `arrival_walk_in_clinic_referral`, `arrival_ambulance` |
| **ICD category** (20) | `icd_Infectious`, `icd_Neoplasm`, `icd_Blood`, `icd_Endocrine`, `icd_Mental`, `icd_Nervous`, `icd_Eye/Ear`, `icd_Circulatory`, `icd_Respiratory`, `icd_Digestive`, `icd_Genitourinary`, `icd_Pregnancy`, `icd_Skin`, `icd_Musculoskeletal`, `icd_Congenital`, `icd_Symptoms`, `icd_Injury`, `icd_Health_Services`, `icd_Other`, `icd_Unknown` |

#### Hyperparameters

| Parameter | Value |
|-----------|-------|
| `n_estimators` | 300 |
| `max_depth` | 6 |
| `learning_rate` | 0.1 |
| `subsample` | 0.8 |
| `colsample_bytree` | 0.8 |
| `objective` | `multi:softprob` |
| `num_class` | 5 |
| `eval_metric` | `mlogloss` |
| `tree_method` | `hist` |

#### Performance (Test)

| Metric | Value |
|--------|-------|
| Accuracy | 0.665 |
| Weighted F1 | 0.653 |
| AUROC (OVR) | 0.728 |

---

### 1.2 TriageNN

| Field | Value |
|-------|-------|
| **Type** | PyTorch feed-forward neural network |
| **Task** | ESI-equivalent acuity scoring (1–5) |
| **Model path** | `models/ed_triage/ed_triage_nn.pt` |
| **Output** | Predicted acuity class (1–5) + softmax probabilities |

#### Features (59)

Same 59 features as TriageXGBoost above.

#### Architecture

```
Input (59) → Linear(256) → BatchNorm → ReLU → Dropout(0.3)
          → Linear(128) → BatchNorm → ReLU → Dropout(0.3)
          → Linear(64)  → BatchNorm → ReLU → Dropout(0.3)
          → Linear(5)   → Softmax
```

#### Hyperparameters

| Parameter | Value |
|-----------|-------|
| `hidden_dims` | [256, 128, 64] |
| `dropout` | 0.3 |
| `learning_rate` | 0.001 |
| `optimizer` | Adam |
| `batch_size` | 256 |
| `epochs` | 50 |
| `early_stopping_patience` | 7 |
| `loss` | CrossEntropyLoss (class-weighted) |

#### Performance (Val)

| Metric | Value |
|--------|-------|
| Accuracy | 0.570 |
| Weighted F1 | 0.577 |
| AUROC (OVR) | 0.690 |

---

## 2. Sepsis ICU (`app_02_sepsis_icu`)

### 2.1 SepsisLGBM (Tabular Baseline)

| Field | Value |
|-------|-------|
| **Type** | LightGBM binary classifier |
| **Task** | Early sepsis prediction 4–6h before clinical recognition |
| **Model path** | `models/sepsis_icu/sepsis_lgbm.pkl` |
| **Dataset** | 5K ICU stays, 329K sliding windows |
| **Output** | Probability of sepsis onset within 4 hours |

#### Features (127 flat)

The model flattens a 6-timestep × 19-feature sequence into 127 statistical features:

**Base timestep features (19 per step):**

| Group | Features |
|-------|----------|
| **Vitals** (7) | `HR`, `RR`, `SpO2`, `SBP`, `DBP`, `Temp`, `MBP` |
| **Labs** (5) | `WBC`, `Lactate`, `Creatinine`, `Platelets`, `Bilirubin` |
| **SOFA components** (6) | `SOFA_resp`, `SOFA_coag`, `SOFA_liver`, `SOFA_cardio`, `SOFA_renal`, `SOFA_total` |
| **Derived** (1) | `delta_SOFA` (change from baseline) |

**Flat aggregations (19 × 6 = 114):**

For each of the 19 timestep features, the following statistics are computed across the 6-step window:

- `mean`, `std`, `min`, `max`, `last_value`, `change_from_start`

**Static features (3):**

| Feature | Description |
|---------|-------------|
| `age` | Patient age at ICU admission |
| `gender_encoded` | 0 = Female, 1 = Male |
| `careunit_encoded` | ICU unit type (ordinal encoded) |

**Total: 114 + 10 (additional aggregates) + 3 static = 127**

#### Hyperparameters

| Parameter | Value |
|-----------|-------|
| `n_estimators` | 1000 |
| `learning_rate` | 0.05 |
| `max_depth` | 7 |
| `num_leaves` | 63 |
| `subsample` | 0.8 |
| `colsample_bytree` | 0.8 |
| `min_child_samples` | 20 |
| `reg_alpha` | 0.1 |
| `reg_lambda` | 1.0 |
| `objective` | `binary` |
| `metric` | `auc` |
| `scale_pos_weight` | auto (class imbalance) |

#### Performance (Test)

| Metric | Value |
|--------|-------|
| AUROC | 0.994 |
| AUPRC | 0.027 |
| F1 | 0.052 |
| Sensitivity @ 95% Spec | 1.000 |

---

### 2.2 SepsisLSTM (Best Model)

| Field | Value |
|-------|-------|
| **Type** | PyTorch Bidirectional LSTM with temporal attention |
| **Task** | Early sepsis prediction 4–6h before clinical recognition |
| **Model path** | `models/sepsis_icu/sepsis_lstm.pt` |
| **Output** | Probability of sepsis onset within 4 hours |

#### Features (6 × 19 sequence)

Input shape: `(batch_size, 6 timesteps, 19 features)`

Same 19 features per timestep as SepsisLGBM (7 vitals + 5 labs + 6 SOFA + 1 delta SOFA).

#### Architecture

```
Input (batch, 6, 19)
  → Bidirectional LSTM (hidden=64, layers=2, dropout=0.3)
  → Temporal Attention (128 → attention weights → context vector)
  → Linear(128 → 64) → ReLU → Dropout(0.3)
  → Linear(64 → 1) → Sigmoid
```

| Component | Detail |
|-----------|--------|
| LSTM | 2-layer bidirectional, hidden_dim=64 (128 after concat) |
| Attention | Learned temporal attention over all 6 timesteps |
| Classifier | 128 → 64 → 1 with dropout 0.3 |

#### Hyperparameters

| Parameter | Value |
|-----------|-------|
| `input_dim` | 19 |
| `hidden_dim` | 64 |
| `n_layers` | 2 |
| `bidirectional` | True |
| `dropout` | 0.3 |
| `learning_rate` | 0.001 |
| `optimizer` | Adam |
| `batch_size` | 256 |
| `epochs` | 50 |
| `early_stopping_patience` | 7 |
| `loss` | BCEWithLogitsLoss (pos_weight adjusted) |

#### Performance (Test)

| Metric | Value |
|--------|-------|
| AUROC | 0.998 |
| AUPRC | 0.093 |
| F1 | 0.179 |
| Accuracy | 0.997 |

#### Ensemble Strategy

Production inference uses a weighted ensemble:

| Model | Weight |
|-------|--------|
| SepsisLGBM | 0.40 |
| SepsisLSTM | 0.60 |

---

## 3. Hospital Ops (`app_03_hospital_ops`)

### 3.1 MADDPG (Multi-Agent RL)

| Field | Value |
|-------|-------|
| **Type** | Multi-Agent Deep Deterministic Policy Gradient |
| **Task** | Real-time hospital resource allocation and patient flow optimization |
| **Status** | Architecture defined, no pre-trained model |
| **Agents** | One per hospital department |

#### State Space (12 per agent)

| Feature | Description |
|---------|-------------|
| `queue_length` | Number of patients waiting |
| `current_patients` | Current patient census |
| `capacity` | Department bed capacity |
| `utilization` | Current utilization ratio (0–1) |
| `avg_wait_time` | Mean wait time in minutes |
| `staff_doctors` | Available doctors |
| `staff_nurses` | Available nurses |
| `staff_utilization` | Staff utilization ratio |
| `acuity_mean` | Mean patient acuity in department |
| `hour_of_day` | Normalized hour (0–1) |
| `day_of_week` | Normalized day (0–1) |
| `arrival_rate` | Current arrival intensity |

#### Action Space (4 continuous per agent)

| Action | Range | Description |
|--------|-------|-------------|
| `doctor_adjustment` | [-3, +3] | Change in doctor count |
| `nurse_adjustment` | [-5, +5] | Change in nurse count |
| `priority_weight` | [0, 1] | Patient priority weighting |
| `threshold` | [0, 1] | Transfer/divert threshold |

#### Architecture

**Actor Network (per agent):**
```
Local observation (12) → Linear(64) → ReLU → Linear(64) → ReLU → Linear(4) → Tanh
```

**Critic Network (centralized):**
```
Global state (12×N) + all actions (4×N) → Linear(128) → ReLU → Linear(64) → ReLU → Linear(1)
```

#### Hyperparameters

| Parameter | Value |
|-----------|-------|
| `actor_lr` | 0.001 |
| `critic_lr` | 0.001 |
| `gamma` | 0.99 |
| `tau` | 0.01 |
| `buffer_size` | 100,000 |
| `batch_size` | 256 |
| `noise_type` | Ornstein-Uhlenbeck |
| `training` | Curriculum learning |

#### Output

| Output | Description |
|--------|-------------|
| Actions | Continuous resource adjustments per department |
| Q-value | Expected future reward from critic |

---

## 4. Oncology AI (`app_04_oncology_ai`)

### 4.1 XGB Readmission 30-day (Served)

| Field | Value |
|-------|-------|
| **Type** | XGBoost binary classifier |
| **Task** | Predict 30-day hospital readmission for cancer patients |
| **Model path** | `models/oncology/xgb_readmission_30d/model.json` |
| **Scaler path** | `models/oncology/xgb_readmission_30d/scaler.joblib` |
| **Dataset** | 67K admissions, 29K patients |
| **Output** | Probability of readmission within 30 days |

#### Features (16)

| Feature | Type | Description |
|---------|------|-------------|
| `age` | float | Patient age at admission |
| `gender_encoded` | int | 0 = Female, 1 = Male |
| `stage_proxy` | int | Cancer stage proxy (derived from DRG/ICD) |
| `drg_mortality` | float | DRG-based mortality risk weight |
| `num_procedures` | int | Number of procedures during admission |
| `has_surgery` | bool (0/1) | Whether surgery was performed |
| `has_chemotherapy` | bool (0/1) | Whether chemotherapy was administered |
| `has_radiation` | bool (0/1) | Whether radiation therapy was given |
| `chemo_drug_count` | int | Number of distinct chemotherapy drugs |
| `num_prior_admissions` | int | Previous hospital admissions |
| `days_since_last_admission` | float | Days since most recent prior admission |
| `total_los_days` | float | Total length of stay in days |
| `num_comorbidities` | int | Count of comorbid conditions |
| `charlson_score` | int | Charlson Comorbidity Index score |
| `insurance_encoded` | int | Insurance type (ordinal encoded) |
| `time_to_first_procedure_days` | float | Days from admission to first procedure |

#### Hyperparameters

| Parameter | Value |
|-----------|-------|
| `n_estimators` | 200 |
| `max_depth` | 5 |
| `learning_rate` | 0.1 |
| `subsample` | 0.8 |
| `colsample_bytree` | 0.8 |
| `objective` | `binary:logistic` |
| `eval_metric` | `auc` |
| `scale_pos_weight` | auto |

#### Performance (Test)

| Metric | Value |
|--------|-------|
| AUROC | 0.734 |
| AUPRC | 0.549 |
| F1 | 0.511 |

---

### 4.2 XGB Hospital Mortality (Served)

| Field | Value |
|-------|-------|
| **Type** | XGBoost binary classifier |
| **Task** | Predict in-hospital mortality for cancer patients |
| **Model path** | `models/oncology/xgb_hospital_mortality/model.json` |
| **Scaler path** | `models/oncology/xgb_hospital_mortality/scaler.joblib` |
| **Output** | Probability of in-hospital death |

#### Features (16)

Same 16 features as XGB Readmission (see table above).

#### Hyperparameters

Same as XGB Readmission.

#### Performance (Test)

| Metric | Value |
|--------|-------|
| AUROC | 0.897 |
| AUPRC | 0.371 |
| F1 | 0.322 |

---

### 4.3 Transformer Readmission 30-day

| Field | Value |
|-------|-------|
| **Type** | PyTorch TabTransformer |
| **Task** | Predict 30-day hospital readmission for cancer patients |
| **Model path** | `models/oncology/transformer_readmission_30d/model.pt` |
| **Output** | Probability of readmission within 30 days |

#### Features (16)

Same 16 features as XGB models (see table above).

#### Architecture

```
Input (16 features, each 1-dim)
  → Feature projection: Linear(1 → 64) per feature
  → Positional embedding (16 learned positions)
  → TransformerEncoder (n_layers=2, n_heads=4, d_model=64, d_ff=128)
  → Mean pooling across feature tokens
  → Linear(64 → 32) → ReLU → Dropout(0.1)
  → Linear(32 → 1) → Sigmoid
```

#### Hyperparameters

| Parameter | Value |
|-----------|-------|
| `d_model` | 64 |
| `n_heads` | 4 |
| `n_layers` | 2 |
| `d_ff` | 128 |
| `dropout` | 0.1 |
| `learning_rate` | 0.0005 |
| `optimizer` | AdamW |
| `batch_size` | 128 |
| `epochs` | 100 |
| `early_stopping_patience` | 10 |
| `loss` | BCEWithLogitsLoss |
| `weight_decay` | 0.01 |

#### Performance (Test)

| Metric | Value |
|--------|-------|
| AUROC | 0.733 |
| AUPRC | 0.540 |
| F1 | 0.506 |

---

### 4.4 Transformer Hospital Mortality

| Field | Value |
|-------|-------|
| **Type** | PyTorch TabTransformer |
| **Task** | Predict in-hospital mortality for cancer patients |
| **Model path** | `models/oncology/transformer_hospital_mortality/model.pt` |
| **Output** | Probability of in-hospital death |

#### Features (16)

Same 16 features as other Oncology models.

#### Architecture

Same architecture as Transformer Readmission.

#### Hyperparameters

Same as Transformer Readmission.

#### Performance (Test)

| Metric | Value |
|--------|-------|
| AUROC | 0.876 |
| AUPRC | 0.237 |
| F1 | 0.268 |

---

## 5. Clinical Chat (`app_06_clinical_chat`)

### 5.1 Ollama LLM Ensemble

| Field | Value |
|-------|-------|
| **Type** | Local LLM routing via Ollama |
| **Task** | Clinical question answering, intent detection, note analysis |
| **API** | `http://localhost:11434` |
| **Input** | Free-text clinical queries |
| **Output** | Natural language clinical responses |

#### Model Routing

| Task | Model | Size | Purpose |
|------|-------|------|---------|
| Intent detection | `llama3.2:3b` | 2.0 GB | Fast intent classification (~65 tok/s) |
| Clinical response | `deepseek-r1:8b` | 5.2 GB | Chain-of-thought clinical reasoning (93% MedQA) |
| Medical QA | `deepseek-r1:8b` | 5.2 GB | General medical knowledge questions |
| Note analysis | `MedAIBase/MedGemma1.5:4b-it` | 7.8 GB | Medical text extraction and summarization |
| Biomedical | `koesn/llama3-openbiollm-8b` | 4.9 GB | Domain-specific biomedical reasoning |
| Fast fallback | `llama3.2:3b` | 2.0 GB | Speed-priority responses |

#### Parameters

| Parameter | Value |
|-----------|-------|
| `stream` | false |
| `timeout` | 120s |
| `temperature` (response) | 0.3 |
| `max_tokens` (intent) | 200 |
| `max_tokens` (response) | 1000 |

#### Fallback Chain

```
Regex intent detection (instant, always available)
  → Ollama LLM intent (if regex returns general_clinical)
    → GPT-4o-mini intent (if Ollama fails, requires OPENAI_API_KEY)
      → Regex fallback (if all LLMs fail)

Ollama response generation (primary)
  → GPT-4o-mini response (fallback)
    → Template response (last resort)
```

---

## 6. Apps Without ML Models

| App | Directory | Description |
|-----|-----------|-------------|
| **Patient Journey** | `app_05_patient_journey/` | Data exploration and timeline visualization — no predictive models |
| **Data Ingestion** | `app_07_data_ingestion/` | MIMIC-IV simulation engine — replays real patient data, no trained models |

---

## Model File Inventory

```
models/
├── ed_triage/
│   ├── ed_triage_xgb.joblib          # XGBoost (served via API)
│   ├── ed_triage_nn.pt               # Neural network
│   └── ed_triage_best.joblib         # Best model symlink (= XGBoost)
├── sepsis_icu/
│   ├── sepsis_lgbm.pkl               # LightGBM
│   └── sepsis_lstm.pt                # LSTM with attention
└── oncology/
    ├── xgb_readmission_30d/
    │   ├── model.json                # XGBoost readmission
    │   └── scaler.joblib             # Feature scaler
    ├── xgb_hospital_mortality/
    │   ├── model.json                # XGBoost mortality
    │   └── scaler.joblib             # Feature scaler
    ├── transformer_readmission_30d/
    │   └── model.pt                  # Transformer readmission
    └── transformer_hospital_mortality/
        └── model.pt                  # Transformer mortality
```

---

## Tech Stack

| Component | Version |
|-----------|---------|
| Python | 3.10 |
| PyTorch | 2.11.0+cu128 |
| XGBoost | 3.2 |
| LightGBM | 4.6 |
| scikit-learn | 1.7 |
| CUDA | 12.8 |
| GPU | NVIDIA RTX 4060 8 GB |
| Ollama | latest (6 models loaded) |
