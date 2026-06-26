# App 04: Oncology AI - Cancer Pathway Optimization

## Objective
Predict cancer patient outcomes (30-day readmission, mortality) and recommend evidence-based treatment pathways for major cancer types using MIMIC-IV oncology cohort data.

## Components
- **Risk Prediction**: XGBoost + Transformer models for readmission and mortality
- **Pathway Optimizer**: Rule-based treatment sequencing with patient-factor adjustments
- **Note Analysis**: Keyword extraction from discharge summaries for cancer indicators

## Supported Cancer Types
Lung, Breast, Colon, Colorectal, Prostate, Leukemia (Myeloid), Non-Hodgkin Lymphoma, Multiple Myeloma + generic fallback

## Quick Start
```bash
# Build oncology cohort dataset
python -m app_04_oncology_ai.backend.data.build_dataset

# Train risk models
python -m app_04_oncology_ai.backend.models.train

# Start API
python -m uvicorn app_04_oncology_ai.backend.app.main:app --port 8204
```

## API: http://localhost:8204/docs
