# Med AI - Healthcare AI Monorepo

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Research use only](https://img.shields.io/badge/use-research%20%26%20education%20only-red.svg)](DISCLAIMER.md)

Clinical machine-learning pipelines built on MIMIC-IV data, focused on oncology and acute-care prediction tasks. Developed for evaluation with a partner hospital network.

> ⚠️ **Research & educational use only — not a medical device.** See [DISCLAIMER.md](DISCLAIMER.md).
> 🔒 **No patient data is included.** The platform requires your own credentialed MIMIC-IV access — see [docs/DATA_ACCESS.md](docs/DATA_ACCESS.md).
> 🤖 **LLMs are pulled at runtime** under their own (mostly source-available) licenses — see [docs/MODELS.md](docs/MODELS.md).

## Current Status

| App | Dataset | Models | API | Dashboard |
|-----|---------|--------|-----|-----------|
| **ED Triage** | 299K admissions, 59 features | XGBoost F1=0.653, NN F1=0.577 | Running (port 8201) | Live via API |
| **Sepsis ICU** | 5K stays, 329K windows | LightGBM AUROC=0.994, LSTM AUROC=0.998 | Not running | Mock data |
| **Hospital Ops** | 1.56M transfers, 431K admissions | No MARL model yet | Not running | Client-side DES |
| **Oncology AI** | 67K admissions, 29K patients | XGBoost readmit=0.734, mortality=0.897 | Running (port 8204) | Live via API |
| **Patient Journey** | Uses existing MIMIC datasets | No ML models (data exploration) | Running (port 8205) | Live via API |

## Applications

| App | Directory | Description |
|-----|-----------|-------------|
| **ED Triage** | `app_01_ed_triage/` | ESI-equivalent acuity scoring (1-5) + disposition prediction |
| **Sepsis ICU** | `app_02_sepsis_icu/` | Early sepsis detection 4-6h before clinical recognition |
| **Hospital Ops** | `app_03_hospital_ops/` | DES simulation with MIMIC arrival patterns, staff scheduling |
| **Oncology AI** | `app_04_oncology_ai/` | Cancer readmission/mortality prediction + treatment pathway optimization |
| **Patient Journey** | `app_05_patient_journey/` | Patient timeline, vitals, labs, medications, care path with cohort comparison |

## Project Structure

```
med-ai/
├── shared/              # Shared utilities used by all apps
│   ├── db/              # MongoDB connection manager and query pipelines
│   ├── ml/              # Preprocessing, evaluation, model registry
│   ├── utils/           # Structured logging
│   └── api/             # FastAPI factory and base models
├── config.py            # Centralised settings (pydantic-settings)
├── datasets/            # Generated MIMIC-IV datasets (parquet/npz)
├── notebooks/           # Exploratory analysis
├── scripts/             # Data ingestion and admin scripts
├── docs/                # Documentation
└── pyproject.toml       # Monorepo dependencies
```

## Data

All applications read from a local MongoDB instance populated with MIMIC-IV tables:

- **MIMIC** database (26 collections, ~260M+ documents): admissions, transfers, labevents, services, diagnoses_icd, procedures_icd, prescriptions, drgcodes, patients, d_labitems, d_icd_diagnoses, d_icd_procedures, emar, pharmacy, poe, clinical_notes, omr
- **MIMIC_ICU** database (5 collections, ~332M+ documents): icustays, chartevents, d_items, datetimeevents, ingredientevents
- **MIMIC_Clinical_Notes** database (1 collection): discharge (331,793 summaries)

## Model Results

### ED Triage (best: XGBoost)
- **XGBoost**: Weighted F1 = 0.653, AUROC = 0.728 (served via API)
- **Neural Net**: Weighted F1 = 0.577, AUROC = 0.690

### Sepsis ICU (best: LSTM-Attention)
- **LightGBM**: AUROC = 0.994, Sensitivity@95%Spec = 1.0
- **LSTM-Attention**: AUROC = 0.998

### Oncology AI (best: XGBoost for both tasks)
- **XGBoost Readmission**: AUROC = 0.734
- **Transformer Readmission**: AUROC = 0.733
- **XGBoost Mortality**: AUROC = 0.897
- **Transformer Mortality**: AUROC = 0.876

### Hospital Ops
- No trained MARL model. Client-side DES simulation with MIMIC arrival profiles.

## Tech Stack

| Component | Version |
|-----------|---------|
| Python | 3.10 (conda env: mitiosis) |
| PyTorch | 2.11.0+cu128 |
| XGBoost | 3.2 |
| LightGBM | 4.6 |
| scikit-learn | 1.7 |
| FastAPI | 0.135 |
| uvicorn | 0.42 |
| pymongo | 4.16 |
| pandas | 2.3 |
| React | 19.1 |
| Vite | 6.4 |
| Tailwind CSS | latest |
| Recharts | latest |
| MongoDB | 7.x |
| GPU | NVIDIA RTX 4060 8GB, CUDA 12.8 |

## Quick Start

### Prerequisites

- Python 3.10 (conda env: `mitiosis`)
- Node.js 18+
- MongoDB 7.x running locally on default port
- NVIDIA GPU with CUDA 12.8 (optional, for LSTM models)

### 1. Clone and activate environment

```bash
git clone https://github.com/HSE-Pulse/med-ai.git
cd med-ai
conda activate mitiosis
```

### 2. Start all backend services

```bash
# Option A: Start apps 01-04 together
python -m scripts.start_all_services

# Start remaining services individually
uvicorn app_05_patient_journey.backend.api.main:app --host 0.0.0.0 --port 8205 --reload
uvicorn app_06_clinical_chat.backend.main:app --host 0.0.0.0 --port 8206 --reload
uvicorn app_07_data_ingestion.backend.api.main:app --host 0.0.0.0 --port 8207

# Option B: Start each service individually
uvicorn app_01_ed_triage.backend.app.main:app --host 0.0.0.0 --port 8201 --reload
uvicorn app_02_sepsis_icu.backend.app.main:app --host 0.0.0.0 --port 8202 --reload
uvicorn app_03_hospital_ops.backend.app.main:app --host 0.0.0.0 --port 8203 --reload
uvicorn app_04_oncology_ai.backend.app.main:app --host 0.0.0.0 --port 8204 --reload
uvicorn app_05_patient_journey.backend.api.main:app --host 0.0.0.0 --port 8205 --reload
uvicorn app_06_clinical_chat.backend.main:app --host 0.0.0.0 --port 8206 --reload
uvicorn app_07_data_ingestion.backend.api.main:app --host 0.0.0.0 --port 8207
```

### 3. Start the dashboard

```bash
cd dashboard
npm install
npm run dev
```

Open **http://localhost:3000** in your browser.

### API Ports

| App | Port | Docs |
|-----|------|------|
| ED Triage | 8201 | http://localhost:8201/docs |
| Sepsis ICU | 8202 | http://localhost:8202/docs |
| Hospital Ops | 8203 | http://localhost:8203/docs |
| Oncology AI | 8204 | http://localhost:8204/docs |
| Patient Journey | 8205 | http://localhost:8205/docs |
| Clinical Chat | 8206 | http://localhost:8206/docs |
| Data Ingestion | 8207 | http://localhost:8207/docs |
| Dashboard | 3000 | http://localhost:3000 |

### Dashboard (port 3000)

Unified React + Vite dashboard with 7 pages + 1 admin page:
- **Overview**: Live KPIs from APIs (299K ED, 67.9K cancer, 29.5K patients), ED board, Sepsis watch, Dept utilization, Oncology overview
- **ED Triage**: Live predictor wired to /predict API, feature importance chart, acuity pie, model metrics
- **Sepsis ICU**: Patient grid, detail view with vitals, interactive SOFA calculator, model performance cards
- **Hospital Ops**: Client-side DES simulation (15-min steps, 1x/2x/5x/10x speed, MIMIC arrival heatmap 8 depts x 24h, dept LOS comparison, 7-day staff schedules)
- **Oncology AI**: Risk assessment, treatment pathway, cohort analytics, clinical note analyzer (NLP via /analyze-note)
- **Patient Journey**: Timeline, vitals charts, lab trends, care path with medication Gantt chart and cross-patient cohort comparison (up to 5 patients)
- **System Admin** (/system): System health monitor (pings all 5 APIs), model performance table (8 models with AUROC/F1), service configuration, dataset inventory, technology stack

## Configuration

All settings are managed via environment variables (see `config.py`):

| Variable | Default | Description |
|----------|---------|-------------|
| `MONGO_URI` | `mongodb://localhost:27017/` | MongoDB connection string |
| `DATA_DIR` | `datasets/` | Path to data directory |
| `MODEL_DIR` | `models/` | Path to saved models |
| `LOG_LEVEL` | `INFO` | Logging level |
| `API_HOST` | `0.0.0.0` | API bind host |
| `API_PORT` | `8000` | API bind port |

## What's Pending

- Sepsis ICU API serving (port 8202)
- Hospital Ops: complete dept capacity/arrival pattern extraction, train MARL model
- Hospital Ops API serving (port 8203) or keep simulation client-side only
- Unified launcher script
- Docker Compose deployment
- Performance benchmarking

## Git Repository

https://github.com/HSE-Pulse/med-ai

## Documentation

- [Architecture Overview](docs/ARCHITECTURE.md)
- [Dataset Construction Plan](docs/DATASET_PLAN.md)
- [Execution Roadmap](docs/ROADMAP.md)
- [Data Access (MIMIC-IV / PhysioNet)](docs/DATA_ACCESS.md)
- [Models & Licenses](docs/MODELS.md)

## License

Licensed under the **Apache License 2.0** — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

Note that this applies to the **code** in this repository only. The MIMIC-IV
dataset (PhysioNet credentialed license) and the bundled LLMs (Llama, Gemma/
MedGemma, OpenBioLLM — source-available community licenses) are **not** covered
by this license and carry their own terms; see [docs/DATA_ACCESS.md](docs/DATA_ACCESS.md)
and [docs/MODELS.md](docs/MODELS.md).

## Disclaimer

This software is for **research and educational purposes only**. It is not a
medical device and must not be used for clinical decision-making. See
[DISCLAIMER.md](DISCLAIMER.md).
