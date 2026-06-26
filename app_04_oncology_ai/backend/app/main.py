"""FastAPI service for Oncology AI - risk prediction and pathway optimization."""
import json
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException

from shared.api.base import create_app
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent.parent.parent
MODEL_DIR = ROOT / "models" / "oncology"
DATASET_DIR = ROOT / "datasets" / "oncology"

# Global state
state = {
    "readmission_model": None,
    "mortality_model": None,
    "pathway_engine": None,
    "metadata": None,
    "start_time": time.time(),
}

FEATURE_COLS = [
    "age", "gender_encoded", "stage_proxy", "drg_mortality",
    "num_procedures", "has_surgery", "has_chemotherapy", "has_radiation",
    "chemo_drug_count", "num_prior_admissions", "days_since_last_admission",
    "total_los_days", "num_comorbidities", "charlson_score",
    "insurance_encoded", "time_to_first_procedure_days",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load models and data at startup."""
    # Observability — JSON logging → Loki, OTel tracing, Prometheus /metrics
    try:
        from shared.integration.logging_config import setup_logging
        setup_logging(service_name="oncology_ai")
    except Exception as exc:  # noqa: BLE001
        print(f"logging_setup_failed: {exc}")
    try:
        from shared.integration.tracing import setup_tracing
        setup_tracing(app, service_name="oncology_ai")
    except Exception as exc:  # noqa: BLE001
        print(f"tracing_setup_failed: {exc}")
    try:
        from shared.integration.prometheus_metrics import install_metrics
        install_metrics(app, service_name="oncology_ai")
    except Exception as exc:  # noqa: BLE001
        print(f"prometheus_metrics_install_failed: {exc}")

    # Load pathway engine
    from app_04_oncology_ai.backend.models.pathway_optimizer import TreatmentPathwayEngine
    state["pathway_engine"] = TreatmentPathwayEngine()

    # Load XGBoost models
    try:
        from app_04_oncology_ai.backend.models.risk_model import OncologyRiskXGB
        readm_path = MODEL_DIR / "xgb_readmission_30d"
        if readm_path.exists():
            state["readmission_model"] = OncologyRiskXGB().load(readm_path)
            print("Loaded readmission model")
        mort_path = MODEL_DIR / "xgb_hospital_mortality"
        if mort_path.exists():
            state["mortality_model"] = OncologyRiskXGB().load(mort_path)
            print("Loaded mortality model")
    except Exception as e:
        print(f"Warning: Could not load risk models: {e}")

    # Load metadata
    meta_path = DATASET_DIR / "metadata.json"
    if meta_path.exists():
        state["metadata"] = json.loads(meta_path.read_text())

    # Subscribe to Kafka/broker events
    try:
        from shared.db.mongo import MongoManager
        from shared.integration.kafka_consumer import attach_with_ring_buffer
        state["_mongo"] = MongoManager()
        await attach_with_ring_buffer(
            service_id="oncology_ai",
            topics=["admission_complete", "patient_discharged"],
            mongo_client=state["_mongo"].client,
        )
    except Exception as exc:
        print(f"Warning: oncology bus subscribe failed: {exc}")

    yield

app = create_app(title="Oncology AI", version="1.0.0")
app.router.lifespan_context = lifespan


@app.get("/kafka-events", tags=["system"])
async def list_kafka_events(limit: int = 100):
    """Return recent cross-service events consumed via Kafka/broker."""
    from shared.integration.kafka_consumer import get_kafka_events
    return {"status": "ok", "data": get_kafka_events("oncology_ai", limit)}


# --- Schemas ---
class PatientInput(BaseModel):
    age: int = 65
    gender: str = "M"
    cancer_type: str = "Lung"
    stage_proxy: int = 2
    drg_mortality: int = 1
    num_procedures: int = 0
    has_surgery: int = 0
    has_chemotherapy: int = 0
    has_radiation: int = 0
    chemo_drug_count: int = 0
    num_prior_admissions: int = 0
    days_since_last_admission: float = 0
    total_los_days: float = 5
    num_comorbidities: int = 0
    charlson_score: int = 0
    insurance: str = "Other"
    time_to_first_procedure_days: float | None = None


class PathwayInput(BaseModel):
    cancer_type: str = "Lung"
    age: int = 65
    stage_proxy: int = 2
    charlson_score: int = 0
    has_prior_chemo: bool = False
    has_prior_surgery: bool = False
    has_prior_radiation: bool = False


class NoteInput(BaseModel):
    text: str


# --- Helper ---
INSURANCE_ENCODING = {"Medicaid": 0, "Medicare": 1, "Other": 2}


def patient_to_features(p: PatientInput) -> np.ndarray:
    """Convert patient input to feature vector."""
    gender_enc = encode_gender(p.gender)
    insurance_enc = INSURANCE_ENCODING.get(p.insurance, INSURANCE_ENCODING["Other"])
    ttp = p.time_to_first_procedure_days if p.time_to_first_procedure_days is not None else 5.0
    features = [
        p.age, gender_enc, p.stage_proxy, p.drg_mortality,
        p.num_procedures, p.has_surgery, p.has_chemotherapy, p.has_radiation,
        p.chemo_drug_count, p.num_prior_admissions, p.days_since_last_admission,
        p.total_los_days, p.num_comorbidities, p.charlson_score,
        insurance_enc, ttp,
    ]
    return np.array([features], dtype=np.float32)


from shared.clinical.risk import get_risk_level, encode_gender


def identify_risk_factors(p: PatientInput) -> list[str]:
    factors = []
    if p.age >= 75:
        factors.append(f"Advanced age ({p.age} years)")
    if p.stage_proxy >= 3:
        factors.append(f"Advanced stage disease (proxy={p.stage_proxy})")
    if p.charlson_score >= 3:
        factors.append(f"High comorbidity burden (Charlson={p.charlson_score})")
    if p.num_prior_admissions >= 3:
        factors.append(f"Multiple prior admissions ({p.num_prior_admissions})")
    if p.has_chemotherapy and p.chemo_drug_count >= 3:
        factors.append(f"Multi-agent chemotherapy ({p.chemo_drug_count} drugs)")
    if p.total_los_days > 14:
        factors.append(f"Prolonged hospital stay ({p.total_los_days:.0f} days)")
    if p.time_to_first_procedure_days and p.time_to_first_procedure_days > 30:
        factors.append(f"Treatment delay ({p.time_to_first_procedure_days:.0f} days)")
    return factors


# --- Endpoints ---

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "uptime_seconds": int(time.time() - state["start_time"]),
        "models_loaded": {
            "readmission": state["readmission_model"] is not None,
            "mortality": state["mortality_model"] is not None,
            "pathway_engine": state["pathway_engine"] is not None,
        },
    }


@app.post("/predict-risk")
async def predict_risk(patient: PatientInput):
    """Predict 30-day readmission and mortality risk."""
    X = patient_to_features(patient)

    # Readmission risk
    if state["readmission_model"] is not None:
        readm_risk = float(state["readmission_model"].predict_proba(X)[0])
    else:
        # Heuristic fallback
        readm_risk = min(1.0, (
            0.1 + 0.05 * patient.num_prior_admissions +
            0.03 * patient.charlson_score +
            0.02 * patient.stage_proxy +
            0.01 * max(0, patient.age - 65)
        ))

    # Mortality risk
    if state["mortality_model"] is not None:
        mort_risk = float(state["mortality_model"].predict_proba(X)[0])
    else:
        mort_risk = min(1.0, (
            0.05 + 0.05 * patient.stage_proxy +
            0.03 * patient.charlson_score +
            0.02 * max(0, patient.age - 70)
        ))

    combined = 0.6 * readm_risk + 0.4 * mort_risk
    level, color = get_risk_level(combined)
    factors = identify_risk_factors(patient)

    recommendations = []
    if readm_risk > 0.3:
        recommendations.append("Enhanced discharge planning with close follow-up")
    if mort_risk > 0.3:
        recommendations.append("Palliative care consultation recommended")
    if patient.charlson_score >= 3:
        recommendations.append("Multidisciplinary team review for comorbidity management")
    if patient.stage_proxy >= 3:
        recommendations.append("Expedite treatment initiation")
    if not recommendations:
        recommendations.append("Standard follow-up per treatment protocol")

    return {
        "status": "ok",
        "data": {
            "readmission_30d_risk": round(readm_risk, 3),
            "mortality_risk": round(mort_risk, 3),
            "combined_risk": round(combined, 3),
            "risk_level": level,
            "risk_color": color,
            "contributing_factors": factors,
            "recommendations": recommendations,
        },
    }


@app.post("/recommend-pathway")
async def recommend_pathway(body: PathwayInput):
    """Get treatment pathway recommendation for a cancer patient."""
    if state["pathway_engine"] is None:
        raise HTTPException(500, "Pathway engine not loaded")

    result = state["pathway_engine"].recommend_pathway(
        cancer_type=body.cancer_type,
        age=body.age,
        stage_proxy=body.stage_proxy,
        charlson_score=body.charlson_score,
        has_prior_chemo=body.has_prior_chemo,
        has_prior_surgery=body.has_prior_surgery,
        has_prior_radiation=body.has_prior_radiation,
    )
    return {"status": "ok", "data": result}


@app.get("/cohort-stats")
async def cohort_stats():
    """Return oncology cohort statistics."""
    if state["metadata"] is None:
        raise HTTPException(404, "Metadata not found. Run build_dataset.py first.")
    return {"status": "ok", "data": state["metadata"]}


@app.post("/analyze-note")
async def analyze_note(body: NoteInput):
    """Extract cancer-relevant information from clinical note text."""
    text = body.text.lower()

    # Simple keyword extraction
    cancer_terms = ["cancer", "carcinoma", "adenocarcinoma", "squamous", "lymphoma",
                    "leukemia", "melanoma", "sarcoma", "myeloma", "tumor", "tumour",
                    "neoplasm", "metastasis", "metastatic", "malignant"]
    treatment_terms = ["chemotherapy", "radiation", "surgery", "resection", "biopsy",
                       "transplant", "immunotherapy", "targeted therapy", "hormone therapy"]
    risk_terms = ["readmission", "recurrence", "progression", "complication",
                  "infection", "neutropenia", "thrombocytopenia", "renal failure"]

    cancer_mentions = [t for t in cancer_terms if t in text]
    treatment_mentions = [t for t in treatment_terms if t in text]
    risk_indicators = [t for t in risk_terms if t in text]

    # Extract drug mentions
    drug_pattern = r'\b(?:' + '|'.join([
        "cisplatin", "carboplatin", "paclitaxel", "doxorubicin", "fluorouracil",
        "gemcitabine", "rituximab", "pembrolizumab", "nivolumab", "methotrexate",
    ]) + r')\b'
    medication_mentions = list(set(re.findall(drug_pattern, text)))

    summary_parts = []
    if cancer_mentions:
        summary_parts.append(f"Cancer-related terms found: {', '.join(cancer_mentions[:5])}")
    if treatment_mentions:
        summary_parts.append(f"Treatments mentioned: {', '.join(treatment_mentions[:5])}")
    if risk_indicators:
        summary_parts.append(f"Risk indicators: {', '.join(risk_indicators[:5])}")
    summary = ". ".join(summary_parts) if summary_parts else "No significant oncology findings in note."

    return {
        "status": "ok",
        "data": {
            "cancer_mentions": cancer_mentions,
            "treatment_mentions": treatment_mentions,
            "medication_mentions": medication_mentions,
            "risk_indicators": risk_indicators,
            "summary": summary,
        },
    }


@app.get("/patient/{subject_id}/timeline")
async def patient_timeline(subject_id: int):
    """Get treatment timeline for a patient from cached data."""
    # Load from dataset
    patients_path = DATASET_DIR / "train.parquet"
    if not patients_path.exists():
        raise HTTPException(404, "Dataset not found")

    df = pd.read_parquet(patients_path)
    patient_rows = df[df["subject_id"] == subject_id]
    if patient_rows.empty:
        raise HTTPException(404, f"Patient {subject_id} not found")

    admissions = []
    for _, row in patient_rows.iterrows():
        admissions.append({
            "hadm_id": int(row["hadm_id"]),
            "admittime": str(row.get("admittime", "")),
            "dischtime": str(row.get("dischtime", "")),
            "los_days": float(row.get("total_los_days", 0)),
            "cancer_type": row.get("cancer_type", "Unknown"),
            "has_surgery": int(row.get("has_surgery", 0)),
            "has_chemotherapy": int(row.get("has_chemotherapy", 0)),
            "has_radiation": int(row.get("has_radiation", 0)),
            "mortality": int(row.get("hospital_mortality", 0)),
        })

    return {
        "status": "ok",
        "data": {
            "subject_id": subject_id,
            "num_admissions": len(admissions),
            "admissions": admissions,
        },
    }
