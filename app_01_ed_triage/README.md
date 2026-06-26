# App 01: Emergency Department AI / Triage Optimization

## Problem Statement

Only 23% of Indian hospitals have formal triage systems, with 8-12 hour average ED wait times in public hospitals. This application provides AI-driven ESI-equivalent acuity scoring to optimize emergency department patient prioritization.

## Architecture

```
app_01_ed_triage/
  backend/
    data/
      build_dataset.py      # MIMIC-IV MongoDB -> Parquet dataset builder
    models/
      triage_model.py       # TriageXGBoost + TriageNN model definitions
      train.py              # Training pipeline with evaluation metrics
    app/
      main.py               # FastAPI service (prediction + metadata endpoints)
      schemas.py            # Pydantic request/response schemas
  frontend/
    src/
      App.tsx               # React dashboard (Vite + TypeScript + Tailwind)
```

## Prediction Targets

| Target | Type | Classes |
|--------|------|---------|
| **acuity_level** | Multiclass (1-5) | 1=Resuscitation, 2=Emergent, 3=Urgent, 4=Less Urgent, 5=Non-urgent |
| **disposition** | Multiclass | admit_to_inpatient, discharge_home, transfer, expired |
| **ed_los_hours** | Regression | Continuous, clipped 0-72h |

## Data Pipeline

### Source Collections (MongoDB)

| Database | Collection | Fields Used |
|----------|-----------|-------------|
| MIMIC | admissions | edregtime, edouttime, admission_type, discharge_location, hospital_expire_flag |
| MIMIC | patients | gender, anchor_age, anchor_year |
| MIMIC_ICU | icustays | hadm_id -> stay_id mapping |
| MIMIC_ICU | chartevents | Vital signs (HR=220045, RR=220210, SpO2=220277, SBP=220179, DBP=220180, Temp=223761) |
| MIMIC | labevents | Labs (WBC=51301, Hgb=51222, Platelets=51265, Lactate=50813, Glucose=50931, Creatinine=50912, BUN=51006, Troponin=51003, Na=50983, K=50971) |
| MIMIC | diagnoses_icd | Primary ICD code -> clinical category |

### Feature Engineering

- **Demographics**: age (derived from anchor_age + year offset), gender (binary encoded)
- **Vitals**: first values within 2h of ED registration, median-imputed, clipped to physiological ranges
- **Labs**: first values within 2h, median-imputed, with missingness indicator flags
- **Arrival context**: one-hot encoded admission_location (ambulance, walk-in, transfer, etc.)
- **Clinical category**: ICD-9/10 code mapped to broad clinical buckets (Circulatory, Respiratory, Injury, etc.)
- **Missingness indicators**: binary flag for every imputed vital/lab feature

### Acuity Label Derivation

Acuity is derived from a combination of:
1. Admission type mapping (EW EMER. -> ESI 2, URGENT -> ESI 3, etc.)
2. Mortality upgrade (expired patients -> ESI 1)
3. Critical vital sign overrides (SpO2 < 90 -> ESI 1, HR > 130 -> ESI 2, SBP < 80 -> ESI 1, Lactate > 4 -> ESI 2)

## Models

### TriageXGBoost (Baseline)
- XGBoost multiclass classifier (`multi:softprob`)
- 300 trees, depth 6, learning rate 0.1
- L1/L2 regularization, column/row subsampling (0.8)
- Produces gain-based feature importance rankings

### TriageNN (Advanced)
- PyTorch feed-forward network (256 -> 128 -> 64 -> 5)
- BatchNorm + ReLU + Dropout (0.3) per hidden layer
- AdamW optimizer with ReduceLROnPlateau scheduler
- Class-weighted cross-entropy loss for imbalanced acuity distribution
- Early stopping on validation loss (patience=7)
- Gradient clipping (max_norm=1.0)

Both models expose a unified interface: `fit(X, y)`, `predict(X)`, `predict_proba(X)`

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/predict` | Single patient acuity prediction |
| POST | `/batch-predict` | Batch predictions (up to 500 patients) |
| GET | `/model-info` | Model metadata and performance metrics |
| GET | `/stats` | Dataset statistics and feature importance |
| GET | `/health` | Service health check |

### Example Request

```json
POST /predict
{
  "age": 65,
  "gender": "M",
  "heart_rate": 110,
  "respiratory_rate": 24,
  "spo2": 92,
  "sbp": 90,
  "dbp": 55,
  "temperature": 38.5,
  "wbc": 15.2,
  "hemoglobin": 10.1,
  "lactate": 3.2,
  "glucose": 180,
  "creatinine": 1.8,
  "arrival_mode": "AMBULANCE"
}
```

### Example Response

```json
{
  "status": "ok",
  "data": {
    "acuity_level": 2,
    "acuity_label": "Emergent",
    "acuity_color": "#F97316",
    "confidence": 0.72,
    "class_probabilities": {
      "ESI-1": 0.15, "ESI-2": 0.72, "ESI-3": 0.10, "ESI-4": 0.02, "ESI-5": 0.01
    },
    "disposition": "admit_to_inpatient",
    "ed_los_estimate_hours": 8.0,
    "risk_factors": [
      "Hypoxemia (SpO2 = 92%)",
      "Hypotension (SBP = 90 mmHg)",
      "Elevated lactate (3.2 mmol/L)",
      "Leukocytosis (WBC = 15.2 K/uL)",
      "Elevated creatinine (1.8 mg/dL)"
    ]
  }
}
```

## Quick Start

```bash
# From D:\project-demo\cancer\

# 1. Build dataset (requires MongoDB with MIMIC-IV data)
python -m app_01_ed_triage.backend.data.build_dataset

# 2. Train models
python -m app_01_ed_triage.backend.models.train

# 3. Start API server (falls back to rule-based if no model found)
uvicorn app_01_ed_triage.backend.app.main:app --host 0.0.0.0 --port 8001

# 4. Start frontend
cd app_01_ed_triage/frontend
npm install && npm run dev
# Dashboard at http://localhost:3001
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MONGO_URI` | `mongodb://localhost:27017/` | MongoDB connection string |
| `DATASET_DIR` | `D:/project-demo/cancer/datasets/ed_triage` | Parquet output directory |
| `MODEL_DIR` | `D:/project-demo/cancer/models/ed_triage` | Model save/load directory |

## Evaluation Metrics

- Accuracy
- Weighted F1 score (primary selection criterion)
- Per-class F1 scores (ESI 1-5)
- Weighted AUROC (one-vs-rest)
- Feature importance (XGBoost gain-based)

## Dependencies

**Backend**: fastapi, uvicorn, pymongo, pandas, numpy, scikit-learn, xgboost, torch, pyarrow, joblib

**Frontend**: react 18, recharts, tailwindcss 3, typescript 5, vite 5
