"""
ED Triage FastAPI Service
=========================
Serves ESI-equivalent acuity predictions for emergency department patients.

Endpoints:
    POST /predict         -- single patient triage prediction
    POST /batch-predict   -- batch predictions (up to 500 patients)
    GET  /model-info      -- model metadata and performance metrics
    GET  /stats           -- dataset statistics and feature importance
    GET  /health          -- health check (from shared base)

Usage::

    uvicorn app_01_ed_triage.backend.app.main:app --host 0.0.0.0 --port 8001

Environment variables:
    MODEL_DIR    Path to saved models (default: ./models/ed_triage)
    DATASET_DIR  Path to Parquet splits (default: ./datasets/ed_triage)
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Ensure imports resolve
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_PROJECT_ROOT))

from shared.api.base import BaseResponse, create_app
from shared.clinical.risk import encode_gender, fahrenheit_to_celsius, identify_vital_risk_factors
from shared.ml.registry import ModelRegistry

from app_01_ed_triage.backend.app.schemas import (
    ACUITY_COLORS,
    ACUITY_LABELS,
    BatchTriageInput,
    BatchTriagePrediction,
    DatasetStatsResponse,
    ModelInfoResponse,
    TriageInput,
    TriagePrediction,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("ed_triage.api")

MODEL_DIR = Path(os.getenv("MODEL_DIR", "./models/ed_triage"))
DATASET_DIR = Path(os.getenv("DATASET_DIR", "./datasets/ed_triage"))

# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------
app = create_app(
    title="ED Triage AI",
    version="1.0.0",
    description="Emergency Department AI Triage -- ESI-equivalent acuity prediction",
)


# ---------------------------------------------------------------------------
# Startup: load model
# ---------------------------------------------------------------------------
_model: Optional[Any] = None
_model_meta: Dict[str, Any] = {}
_feature_names: List[str] = []
_dataset_stats: Dict[str, Any] = {}


@app.on_event("startup")
async def startup_load_model() -> None:
    """Load the best trained model and cache dataset stats at startup."""
    global _model, _model_meta, _feature_names, _dataset_stats

    # Structured logging
    try:
        from shared.integration.logging_config import setup_logging
        setup_logging(service_name="ed_triage")
    except Exception as exc:  # noqa: BLE001
        logger.warning("logging_setup_failed: %s", exc)

    # OpenTelemetry tracing
    try:
        from shared.integration.tracing import setup_tracing
        setup_tracing(app, service_name="ed_triage")
    except Exception as exc:  # noqa: BLE001
        logger.warning("tracing_setup_failed: %s", exc)

    # Prometheus /metrics for Grafana scraping
    try:
        from shared.integration.prometheus_metrics import install_metrics
        install_metrics(app, service_name="ed_triage")
    except Exception as exc:  # noqa: BLE001
        logger.warning("prometheus_metrics_install_failed: %s", exc)

    registry = ModelRegistry(base_path=str(MODEL_DIR))

    try:
        _model, _model_meta = registry.load_model("ed_triage_best")
        logger.info("Loaded model: ed_triage_best  metrics=%s", _model_meta.get("metrics"))
        # Extract feature names from the model's booster for exact column matching
        try:
            booster = _model._model.get_booster()
            if booster.feature_names:
                _feature_names = list(booster.feature_names)
                logger.info("Loaded %d feature names from model booster", len(_feature_names))
        except Exception:
            pass
    except FileNotFoundError:
        logger.warning(
            "No trained model found at %s. Predictions will use rule-based fallback.",
            MODEL_DIR,
        )
        _model = None

    # Load dataset stats
    try:
        train_path = DATASET_DIR / "train.parquet"
        if train_path.exists():
            train_df = pd.read_parquet(train_path)
            non_features = {"hadm_id", "acuity_level", "acuity_label", "disposition", "ed_los_hours"}
            # Exclude string/object columns that can't be used for numeric prediction
            obj_cols = set(train_df.select_dtypes(include=["object", "category"]).columns)
            _feature_names = [c for c in train_df.columns if c not in non_features and c not in obj_cols]

            dist = train_df["acuity_level"].value_counts().sort_index().to_dict()
            _dataset_stats = {
                "total_samples": len(train_df),
                "class_distribution": {
                    f"ESI-{k} ({ACUITY_LABELS.get(k, '?')})": int(v)
                    for k, v in dist.items()
                },
                "missing_rates": {
                    col: round(train_df[col].mean() * 100, 1)
                    for col in train_df.columns
                    if col.endswith("_missing")
                },
            }
            logger.info("Dataset stats loaded: %d training samples", len(train_df))
    except Exception as exc:
        logger.warning("Could not load dataset stats: %s", exc)

    # Subscribe to Kafka/broker events
    try:
        from shared.db.mongo import MongoManager
        from shared.integration.kafka_consumer import attach_with_ring_buffer
        _mongo = MongoManager()
        await attach_with_ring_buffer(
            service_id="ed_triage",
            topics=["admission_complete", "patient_transferred"],
            mongo_client=_mongo.client,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ed_triage_bus_subscribe_failed: %s", exc)


@app.get("/kafka-events", tags=["system"])
async def list_kafka_events(limit: int = 100):
    """Return recent cross-service events consumed via Kafka/broker."""
    from shared.integration.kafka_consumer import get_kafka_events
    return {"status": "ok", "data": get_kafka_events("ed_triage", limit)}


# ---------------------------------------------------------------------------
# Feature engineering for inference
# ---------------------------------------------------------------------------
# Median defaults (population-level from MIMIC-IV ED cohort)
_MEDIAN_DEFAULTS = {
    "heart_rate": 84.0,
    "respiratory_rate": 18.0,
    "spo2": 97.0,
    "sbp": 128.0,
    "dbp": 72.0,
    "temperature": 36.8,
    "wbc": 9.5,
    "hemoglobin": 12.5,
    "platelets": 220.0,
    "lactate": 1.5,
    "glucose": 120.0,
    "creatinine": 1.0,
    "bun": 18.0,
    "troponin": 0.02,
    "sodium": 140.0,
    "potassium": 4.1,
}


def _input_to_features(inp: TriageInput) -> Dict[str, float]:
    """Convert a TriageInput to the flat feature dict expected by the model."""
    features: Dict[str, float] = {}

    # Demographics
    features["age"] = inp.age
    features["gender_encoded"] = float(encode_gender(inp.gender))

    # Auto-convert Fahrenheit to Celsius if needed
    temp_c = fahrenheit_to_celsius(inp.temperature)

    # Vitals
    vital_fields = {
        "heart_rate": inp.heart_rate,
        "respiratory_rate": inp.respiratory_rate,
        "spo2": inp.spo2,
        "sbp": inp.sbp,
        "dbp": inp.dbp,
        "temperature": temp_c,
    }
    for name, val in vital_fields.items():
        if val is not None:
            features[name] = val
            features[f"{name}_missing"] = 0.0
        else:
            features[name] = _MEDIAN_DEFAULTS.get(name, 0.0)
            features[f"{name}_missing"] = 1.0

    # Labs
    lab_fields = {
        "wbc": inp.wbc,
        "hemoglobin": inp.hemoglobin,
        "lactate": inp.lactate,
        "glucose": inp.glucose,
        "creatinine": inp.creatinine,
    }
    # Labs not in the input schema but in the model
    extra_labs = {"platelets": None, "bun": None, "troponin": None, "sodium": None, "potassium": None}

    for name, val in {**lab_fields, **extra_labs}.items():
        if val is not None:
            features[name] = val
            features[f"{name}_missing"] = 0.0
        else:
            features[name] = _MEDIAN_DEFAULTS.get(name, 0.0)
            features[f"{name}_missing"] = 1.0

    # Arrival mode dummies
    arrival = (inp.arrival_mode or "UNKNOWN").upper()
    features["arrival_emergency_room"] = 1.0 if "EMERGENCY" in arrival or "ER" in arrival else 0.0
    features["arrival_physician_referral"] = 1.0 if "PHYSICIAN" in arrival else 0.0
    features["arrival_transfer_from_hospital"] = 1.0 if "TRANSFER" in arrival else 0.0
    features["arrival_walk_in_clinic_referral"] = 1.0 if "WALK" in arrival or "CLINIC" in arrival else 0.0
    features["arrival_ambulance"] = 1.0 if "AMBULANCE" in arrival else 0.0

    return features


def _identify_risk_factors(inp: TriageInput) -> List[str]:
    """Identify clinical risk factors from the input values."""
    return identify_vital_risk_factors(
        heart_rate=inp.heart_rate, respiratory_rate=inp.respiratory_rate,
        spo2=inp.spo2, sbp=inp.sbp, temperature=inp.temperature,
        lactate=inp.lactate, wbc=inp.wbc, creatinine=inp.creatinine,
        age=inp.age,
    )


def _estimate_disposition(acuity: int, risk_factors: List[str]) -> str:
    """Rule-based disposition estimate based on acuity and risk factors."""
    if acuity == 1:
        return "admit_to_inpatient"
    elif acuity == 2:
        return "admit_to_inpatient"
    elif acuity == 3:
        if len(risk_factors) >= 3:
            return "admit_to_inpatient"
        return "admit_to_inpatient"
    elif acuity == 4:
        return "discharge_home"
    else:
        return "discharge_home"


def _estimate_ed_los(acuity: int) -> float:
    """Rough ED LOS estimate by acuity level (hours)."""
    los_map = {1: 6.0, 2: 8.0, 3: 6.5, 4: 4.0, 5: 2.5}
    return los_map.get(acuity, 5.0)


def _rule_based_acuity(inp: TriageInput) -> int:
    """Fallback rule-based acuity when no model is available."""
    score = 3  # default Urgent

    if inp.spo2 is not None and inp.spo2 < 90:
        score = min(score, 1)
    if inp.sbp is not None and inp.sbp < 80:
        score = min(score, 1)
    if inp.heart_rate is not None and (inp.heart_rate > 130 or inp.heart_rate < 40):
        score = min(score, 2)
    if inp.lactate is not None and inp.lactate > 4.0:
        score = min(score, 2)
    if inp.temperature is not None and inp.temperature > 39.5:
        score = min(score, 2)

    # If all vitals normal, downgrade
    all_normal = True
    if inp.heart_rate is not None and not (60 <= inp.heart_rate <= 100):
        all_normal = False
    if inp.spo2 is not None and inp.spo2 < 95:
        all_normal = False
    if inp.sbp is not None and not (90 <= inp.sbp <= 160):
        all_normal = False
    if inp.respiratory_rate is not None and not (12 <= inp.respiratory_rate <= 20):
        all_normal = False

    if all_normal and inp.age < 50:
        score = max(score, 4)

    return score


# ---------------------------------------------------------------------------
# Shared prediction helper
# ---------------------------------------------------------------------------
def _predict_single(inp: TriageInput) -> dict:
    """Run triage prediction for a single patient input, returning a dict."""
    risk_factors = _identify_risk_factors(inp)

    if _model is not None:
        features = _input_to_features(inp)
        # Build DataFrame with matching feature columns
        if _feature_names:
            row = {col: features.get(col, 0.0) for col in _feature_names}
        else:
            row = features
        X = pd.DataFrame([row])

        proba = _model.predict_proba(X)[0]
        pred_class = int(np.argmax(proba))
        acuity = pred_class + 1  # back to 1-indexed
        confidence = float(proba[pred_class])
        class_probs = {
            f"ESI-{i+1}": round(float(proba[i]), 4) for i in range(len(proba))
        }
    else:
        # Fallback rule-based
        acuity = _rule_based_acuity(inp)
        confidence = 0.6
        class_probs = {f"ESI-{i+1}": 0.0 for i in range(5)}
        class_probs[f"ESI-{acuity}"] = confidence

    disposition = _estimate_disposition(acuity, risk_factors)
    ed_los = _estimate_ed_los(acuity)

    prediction = TriagePrediction(
        acuity_level=acuity,
        acuity_label=ACUITY_LABELS.get(acuity, "Unknown"),
        acuity_color=ACUITY_COLORS.get(acuity, "#6B7280"),
        confidence=round(confidence, 4),
        class_probabilities=class_probs,
        disposition=disposition,
        ed_los_estimate_hours=round(ed_los, 1),
        risk_factors=risk_factors,
    )
    return prediction.model_dump()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.post("/predict", response_model=BaseResponse, tags=["prediction"])
async def predict(inp: TriageInput) -> BaseResponse:
    """Predict ESI-equivalent acuity level for a single patient."""
    return BaseResponse(data=_predict_single(inp))


@app.post("/batch-predict", response_model=BaseResponse, tags=["prediction"])
async def batch_predict(batch: BatchTriageInput) -> BaseResponse:
    """Predict acuity levels for a batch of patients."""
    predictions = [_predict_single(inp) for inp in batch.patients]
    result = BatchTriagePrediction(
        predictions=[TriagePrediction(**p) for p in predictions],
        count=len(predictions),
    )
    return BaseResponse(data=result.model_dump())


@app.get("/model-info", response_model=BaseResponse, tags=["metadata"])
async def model_info() -> BaseResponse:
    """Return metadata and performance metrics for the loaded model."""
    if _model is None:
        return BaseResponse(
            data=ModelInfoResponse(
                model_name="rule_based_fallback",
                model_type="RuleBased",
                metrics={},
                class_labels={str(k): v for k, v in ACUITY_LABELS.items()},
            ).model_dump()
        )

    info = ModelInfoResponse(
        model_name=_model_meta.get("name", "ed_triage_best"),
        model_type=_model_meta.get("model_type", "Unknown"),
        version=_model_meta.get("saved_at", ""),
        metrics=_model_meta.get("metrics", {}),
        feature_count=len(_feature_names),
        feature_names=_feature_names[:50],  # limit for response size
        class_labels={str(k): v for k, v in ACUITY_LABELS.items()},
    )
    return BaseResponse(data=info.model_dump())


@app.get("/stats", response_model=BaseResponse, tags=["metadata"])
async def stats() -> BaseResponse:
    """Return dataset statistics and feature importance."""
    if not _dataset_stats:
        return BaseResponse(
            data=DatasetStatsResponse(
                total_samples=0,
                class_distribution={},
            ).model_dump()
        )

    # Feature importance from XGBoost if available
    fi: Dict[str, float] = {}
    if _model is not None and hasattr(_model, "feature_importances"):
        raw_fi = _model.feature_importances
        if raw_fi:
            fi = {k: round(v, 4) for k, v in sorted(raw_fi.items(), key=lambda x: -x[1])[:20]}

    result = DatasetStatsResponse(
        total_samples=_dataset_stats.get("total_samples", 0),
        class_distribution=_dataset_stats.get("class_distribution", {}),
        feature_importance=fi,
        missing_rates=_dataset_stats.get("missing_rates", {}),
    )
    return BaseResponse(data=result.model_dump())


# ---------------------------------------------------------------------------
# Entry point for direct execution
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
