# App 02: Sepsis & ICU Deterioration Prediction

Real-time sepsis onset prediction 4-6 hours before clinical recognition using MIMIC-IV ICU data.

## Clinical Context

- Sepsis mortality in Indian ICUs: 40-60% vs 15-25% in Western ICUs
- AI sepsis prediction achieving AUROC >0.90 in validation studies
- Target: predict sepsis onset 4-6 hours before clinical recognition using Sepsis-3 criteria

## Architecture

```
app_02_sepsis_icu/
├── backend/
│   ├── data/
│   │   └── build_dataset.py      # MongoDB extraction, SOFA scoring, windowing
│   ├── models/
│   │   ├── sepsis_model.py        # LightGBM + LSTM-Attention model definitions
│   │   ├── train.py               # Training pipeline with clinical evaluation metrics
│   │   └── saved/                 # Trained model artifacts
│   └── app/
│       ├── main.py                # FastAPI service (REST + WebSocket)
│       └── schemas.py             # Pydantic request/response models
├── frontend/
│   └── src/
│       ├── App.tsx                # React dashboard with monitoring grid
│       └── main.tsx               # Entry point
├── requirements.txt
└── README.md
```

## Data Sources (MongoDB)

| Collection | Database | Key Fields | Records |
|---|---|---|---|
| icustays | MIMIC_ICU | stay_id, hadm_id, los, intime, outtime | 73,141 |
| chartevents | MIMIC_ICU | stay_id, itemid, valuenum, charttime | 314M |
| labevents | MIMIC | hadm_id, itemid, valuenum, charttime | 118M |
| admissions | MIMIC | hadm_id, hospital_expire_flag | - |
| diagnoses_icd | MIMIC | hadm_id, icd_code, icd_version | - |
| patients | MIMIC | subject_id, gender, anchor_age | - |

### Vital Signs (chartevents itemids)
- HR=220045, RR=220210, SpO2=220277, SBP=220179, DBP=220180, Temp=223761, MBP=220181

### Lab Results (labevents itemids)
- WBC=51301, Lactate=50813, Creatinine=50912, Platelets=51265, Bilirubin=50885

## Sepsis-3 Labeling

1. **SOFA Score Computation** (approximated from available data):
   - Respiration: SpO2 proxy (SpO2<92=3, 92-96=1, >96=0)
   - Coagulation: Platelets (<20=4, <50=3, <100=2, <150=1)
   - Liver: Bilirubin (>12=4, 6-12=3, 2-6=2, 1.2-2=1)
   - Cardiovascular: MAP (<70=1)
   - Renal: Creatinine (>5=4, 3.5-5=3, 2-3.5=2, 1.2-2=1)

2. **Sepsis Onset Label**: SOFA increase >= 2 AND sepsis ICD code (A40/A41 ICD-10; 995.91/995.92/785.52 ICD-9)

3. **Windowing**: 6-hour lookback, predict onset in next 4 hours

## Models

### LightGBM (Baseline)
- Operates on flattened statistical features (mean, std, min, max, last, trend per variable)
- Fast inference suitable for real-time monitoring
- Class-weighted for imbalanced data

### LSTM with Temporal Attention (Advanced)
- Bidirectional LSTM processing raw 6-hour time series
- Additive attention mechanism highlights critical time steps
- LayerNorm + dropout regularization

### Ensemble
- Weighted combination: 40% LightGBM + 60% LSTM
- Falls back to heuristic scoring if no trained model available

## Feature Set (19 per timestep)

| Category | Features |
|---|---|
| Vitals | HR, RR, SpO2, SBP, DBP, Temp, MBP |
| Labs | WBC, Lactate, Creatinine, Platelets, Bilirubin |
| SOFA | resp, coag, liver, cardio, renal, total, delta |

Static: age, gender, careunit_encoded

## Evaluation Metrics

- **AUROC**: Discrimination across all thresholds
- **AUPRC**: Critical for imbalanced data (sepsis prevalence ~5-10%)
- **Sensitivity at 95% Specificity**: Clinical operating point
- **Early Prediction Lead Time**: Hours before clinical recognition

## API Endpoints

| Method | Path | Description |
|---|---|---|
| POST | /predict | Predict sepsis risk from vitals/labs time window |
| GET | /patient/{stay_id}/timeline | Historical risk timeline |
| GET | /unit-overview | Current risk status for all active patients |
| WS | /ws/monitor | Real-time monitoring stream |
| GET | /health | Service health check |
| GET | /metrics | Prometheus-compatible metrics |

## Quick Start

```bash
# From D:\project-demo\cancer\

# 1. Build dataset from MongoDB (samples 10K ICU stays - takes ~10-30 min)
python -m app_02_sepsis_icu.backend.data.build_dataset

# 2. Train models
python -m app_02_sepsis_icu.backend.models.train

# 3. Start API server
python -m uvicorn app_02_sepsis_icu.backend.app.main:app --host 0.0.0.0 --port 8202

# 4. Start frontend
cd app_02_sepsis_icu/frontend && npm install && npm run dev

# 5. Open API docs
# http://localhost:8202/docs
```

## Alert Levels

| Level | Risk Score | Action |
|---|---|---|
| Green | < 25% | Routine monitoring |
| Yellow | 25-50% | Increase monitoring frequency |
| Orange | 50-75% | Clinical review recommended |
| Red | >= 75% | Immediate clinical assessment |

## Important Notes

- Dataset builder samples 10K ICU stays to manage chartevents query volume (314M rows total)
- Sepsis labels are approximate (Sepsis-3 adapted to available MIMIC fields)
- Production deployment would require real-time vital sign streaming integration
- API gracefully degrades to heuristic scoring when no trained model is available
