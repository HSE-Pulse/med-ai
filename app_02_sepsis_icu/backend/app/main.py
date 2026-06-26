"""FastAPI service for Sepsis & ICU Deterioration Prediction.

Endpoints
---------
- POST /predict           : Predict sepsis risk from a vitals/labs time window
- GET  /patient/{stay_id}/timeline : Historical risk timeline
- GET  /unit-overview     : Current risk status for all active patients (demo)
- WS   /ws/monitor        : Real-time WebSocket monitoring stream
- GET  /health            : Service health check
- GET  /metrics           : Prometheus-compatible metrics
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import HTTPException, WebSocket, WebSocketDisconnect

from shared.api.base import create_app
from prometheus_client import Counter, Gauge, Histogram, generate_latest

from .schemas import (
    AlertLevel,
    ContributingFactor,
    HealthResponse,
    PatientSummary,
    PatientTimeline,
    PredictionRequest,
    SepsisPrediction,
    SOFABreakdown,
    TimelinePoint,
    UnitOverview,
)

# ---------------------------------------------------------------------------
# Allow imports from the shared and model packages
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")

# ---------------------------------------------------------------------------
# Model paths
# ---------------------------------------------------------------------------
MODEL_DIR = PROJECT_ROOT / "models" / "sepsis_icu"
LGBM_PATH = MODEL_DIR / "sepsis_lgbm.pkl"
LSTM_PATH = MODEL_DIR / "sepsis_lstm.pt"

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
REQUEST_COUNT = Counter("http_requests_total", "Total HTTP requests", ["method", "endpoint", "status"])
REQUEST_DURATION = Histogram("http_request_duration_seconds", "Request latency", ["endpoint"])
MODEL_LOADED = Gauge("model_loaded", "Whether the ML model is loaded (1=yes)")
INFERENCE_DURATION = Histogram("model_inference_duration_seconds", "Model inference latency")
PREDICTIONS_TOTAL = Counter("predictions_total", "Total sepsis predictions", ["alert_level"])

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_state: Dict[str, Any] = {
    "lgbm_model": None,
    "lstm_model": None,
    "model_available": False,
    "start_time": time.time(),
}


# ---------------------------------------------------------------------------
# SOFA computation helpers (mirror build_dataset.py logic)
# ---------------------------------------------------------------------------

from shared.clinical.risk import compute_sofa as _compute_sofa_dict


def compute_sofa(vitals: dict, labs: dict) -> SOFABreakdown:
    """Compute SOFA breakdown from the latest vitals and labs."""
    d = _compute_sofa_dict(vitals, labs)
    return SOFABreakdown(**d)


def risk_to_alert(risk: float) -> AlertLevel:
    if risk >= 0.75:
        return AlertLevel.RED
    if risk >= 0.50:
        return AlertLevel.ORANGE
    if risk >= 0.25:
        return AlertLevel.YELLOW
    return AlertLevel.GREEN


def build_contributing_factors(vitals: dict, labs: dict) -> List[ContributingFactor]:
    """Identify which parameters are abnormal and contributing to risk."""
    factors: List[ContributingFactor] = []

    checks = [
        ("heart_rate", vitals.get("heart_rate"), 60, 100, "Heart rate {v} bpm"),
        ("respiratory_rate", vitals.get("respiratory_rate"), 12, 20, "Respiratory rate {v}/min"),
        ("spo2", vitals.get("spo2"), 95, 100, "SpO2 {v}%"),
        ("sbp", vitals.get("sbp"), 90, 140, "Systolic BP {v} mmHg"),
        ("temperature", vitals.get("temperature"), 36.1, 38.0, "Temperature {v}C"),
        ("mbp", vitals.get("mbp"), 70, 105, "MAP {v} mmHg"),
        ("wbc", labs.get("wbc"), 4.0, 11.0, "WBC {v} K/uL"),
        ("lactate", labs.get("lactate"), 0.0, 2.0, "Lactate {v} mmol/L"),
        ("creatinine", labs.get("creatinine"), 0.6, 1.2, "Creatinine {v} mg/dL"),
        ("platelets", labs.get("platelets"), 150, 400, "Platelets {v} K/uL"),
        ("bilirubin", labs.get("bilirubin"), 0.1, 1.2, "Bilirubin {v} mg/dL"),
    ]

    for name, value, lo, hi, desc_template in checks:
        if value is None:
            continue
        if value < lo or value > hi:
            deviation = abs(value - lo) / max(hi - lo, 1) if value < lo else abs(value - hi) / max(hi - lo, 1)
            if deviation > 1.5:
                sev = AlertLevel.RED
            elif deviation > 0.75:
                sev = AlertLevel.ORANGE
            else:
                sev = AlertLevel.YELLOW
            factors.append(ContributingFactor(
                feature=name,
                value=value,
                description=desc_template.format(v=round(value, 1)),
                severity=sev,
            ))

    return factors


# ---------------------------------------------------------------------------
# Feature engineering for model input
# ---------------------------------------------------------------------------

FEATURE_ORDER = [
    "HR", "RR", "SpO2", "SBP", "DBP", "Temp", "MBP",
    "WBC", "Lactate", "Creatinine", "Platelets", "Bilirubin",
    "SOFA_resp", "SOFA_coag", "SOFA_liver", "SOFA_cardio", "SOFA_renal",
    "SOFA_total", "delta_SOFA",
]


def request_to_arrays(req: PredictionRequest) -> tuple[np.ndarray, np.ndarray]:
    """Convert a PredictionRequest to (X_seq, X_flat) numpy arrays.

    Returns arrays shaped for a single sample: X_seq (1, T, F), X_flat (1, F*6+3).
    """
    # Sort vitals and labs by timestamp
    vitals_sorted = sorted(req.vitals, key=lambda v: v.timestamp)
    labs_sorted = sorted(req.labs, key=lambda l: l.timestamp) if req.labs else []

    # Determine hourly grid (up to 6 hours back from the most recent vital)
    latest_ts = vitals_sorted[-1].timestamp
    n_hours = min(len(vitals_sorted), 6)
    grid_start = latest_ts - timedelta(hours=n_hours)

    # Create feature matrix for each timestep
    rows = []
    for h in range(n_hours):
        ts_start = grid_start + timedelta(hours=h)
        ts_end = ts_start + timedelta(hours=1)

        # Pick vitals in this hour (use last available)
        hour_vitals = [v for v in vitals_sorted if ts_start <= v.timestamp < ts_end]
        v = hour_vitals[-1] if hour_vitals else (vitals_sorted[-1] if vitals_sorted else None)

        # Pick labs closest to this hour
        hour_labs = [l for l in labs_sorted if ts_start <= l.timestamp < ts_end]
        l = hour_labs[-1] if hour_labs else (labs_sorted[-1] if labs_sorted else None)

        # Map to feature vector
        vd = {
            "spo2": getattr(v, "spo2", None) if v else None,
            "mbp": getattr(v, "mbp", None) if v else None,
        }
        ld = {
            "platelets": getattr(l, "platelets", None) if l else None,
            "bilirubin": getattr(l, "bilirubin", None) if l else None,
            "creatinine": getattr(l, "creatinine", None) if l else None,
        }
        sofa = compute_sofa(vd, ld)

        row = [
            getattr(v, "heart_rate", None) if v else None,
            getattr(v, "respiratory_rate", None) if v else None,
            getattr(v, "spo2", None) if v else None,
            getattr(v, "sbp", None) if v else None,
            getattr(v, "dbp", None) if v else None,
            getattr(v, "temperature", None) if v else None,
            getattr(v, "mbp", None) if v else None,
            getattr(l, "wbc", None) if l else None,
            getattr(l, "lactate", None) if l else None,
            getattr(l, "creatinine", None) if l else None,
            getattr(l, "platelets", None) if l else None,
            getattr(l, "bilirubin", None) if l else None,
            sofa.respiration,
            sofa.coagulation,
            sofa.liver,
            sofa.cardiovascular,
            sofa.renal,
            sofa.total,
            0.0,  # delta_SOFA placeholder
        ]
        rows.append([0.0 if x is None else float(x) for x in row])

    arr = np.array(rows, dtype=np.float32)

    # Compute delta_SOFA from baseline
    sofa_idx = FEATURE_ORDER.index("SOFA_total")
    if arr.shape[0] > 1:
        arr[:, -1] = arr[:, sofa_idx] - arr[0, sofa_idx]

    # Pad to 6 timesteps if fewer
    if arr.shape[0] < 6:
        pad = np.tile(arr[:1], (6 - arr.shape[0], 1))
        arr = np.vstack([pad, arr])

    X_seq = arr[np.newaxis, :, :]  # (1, 6, 19)

    # Flat features: statistical aggregations
    flat = []
    for col_i in range(arr.shape[1]):
        col = arr[:, col_i]
        flat.extend([
            float(np.mean(col)),
            float(np.std(col)),
            float(np.min(col)),
            float(np.max(col)),
            float(col[-1]),
            float(col[-1] - col[0]),
        ])
    # Static features
    gender_enc = 1 if req.gender == "M" else 0
    flat.extend([req.age, float(gender_enc), 0.0])  # careunit_encoded = 0 for now
    X_flat = np.array([flat], dtype=np.float32)

    return X_seq, X_flat


# ---------------------------------------------------------------------------
# Lifespan: load models at startup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Structured logging
    try:
        from shared.integration.logging_config import setup_logging
        setup_logging(service_name="sepsis_icu")
    except Exception as exc:  # noqa: BLE001
        logger.warning("logging_setup_failed: %s", exc)

    # OpenTelemetry tracing
    try:
        from shared.integration.tracing import setup_tracing
        setup_tracing(app, service_name="sepsis_icu")
    except Exception as exc:  # noqa: BLE001
        logger.warning("tracing_setup_failed: %s", exc)

    # Prometheus /metrics for Grafana scraping
    try:
        from shared.integration.prometheus_metrics import install_metrics
        install_metrics(app, service_name="sepsis_icu")
    except Exception as exc:  # noqa: BLE001
        logger.warning("prometheus_metrics_install_failed: %s", exc)

    # Anchor SimClock to data_ingestion's authoritative clock.
    try:
        from shared.integration.sim_clock import attach_remote_clock
        await attach_remote_clock()
    except Exception as exc:  # noqa: BLE001
        logger.warning("sim_clock_attach_remote_failed: %s", exc)

    logger.info("Loading models ...")
    try:
        if LGBM_PATH.exists():
            from ..models.sepsis_model import SepsisLGBM
            _state["lgbm_model"] = SepsisLGBM.load(LGBM_PATH)
            _state["model_available"] = True
            logger.info("LightGBM model loaded.")
    except Exception as e:
        logger.warning("Could not load LightGBM model: %s", e)

    try:
        if LSTM_PATH.exists():
            from ..models.sepsis_model import SepsisLSTM
            _state["lstm_model"] = SepsisLSTM.load(LSTM_PATH)
            _state["model_available"] = True
            logger.info("LSTM model loaded.")
    except Exception as e:
        logger.warning("Could not load LSTM model: %s", e)

    if not _state["model_available"]:
        logger.warning("No trained models found. Predictions will use heuristic fallback.")

    MODEL_LOADED.set(1 if _state["model_available"] else 0)

    # Subscribe to Kafka/broker events
    try:
        from shared.db.mongo import MongoManager
        from shared.integration.kafka_consumer import attach_with_ring_buffer
        _state["_mongo"] = MongoManager()
        await attach_with_ring_buffer(
            service_id="sepsis_icu",
            topics=["admission_complete", "patient_transferred", "patient_discharged"],
            mongo_client=_state["_mongo"].client,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("sepsis_bus_subscribe_failed: %s", exc)

    yield
    logger.info("Shutting down ...")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = create_app(
    title="Sepsis & ICU Deterioration Prediction",
    description="Real-time sepsis onset prediction 4-6 hours before clinical recognition",
    version="1.0.0",
)
app.router.lifespan_context = lifespan


@app.get("/kafka-events", tags=["system"])
async def list_kafka_events(limit: int = 100):
    """Return recent cross-service events consumed via Kafka/broker."""
    from shared.integration.kafka_consumer import get_kafka_events
    return {"status": "ok", "data": get_kafka_events("sepsis_icu", limit)}


# ---------------------------------------------------------------------------
# Heuristic fallback when no model is loaded
# ---------------------------------------------------------------------------

def heuristic_risk(sofa: SOFABreakdown, vitals: dict, labs: dict) -> float:
    """Simple rule-based risk score when no ML model is available."""
    score = sofa.total / 20.0  # normalized SOFA

    # Add penalty for abnormal vitals
    hr = vitals.get("heart_rate")
    if hr and (hr > 110 or hr < 50):
        score += 0.1
    rr = vitals.get("respiratory_rate")
    if rr and rr > 22:
        score += 0.08
    temp = vitals.get("temperature")
    if temp and (temp > 38.3 or temp < 36.0):
        score += 0.1
    spo2 = vitals.get("spo2")
    if spo2 and spo2 < 93:
        score += 0.12
    lactate = labs.get("lactate")
    if lactate and lactate > 2.0:
        score += 0.15

    return min(score, 1.0)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/predict", response_model=SepsisPrediction)
async def predict_sepsis(req: PredictionRequest):
    """Predict sepsis risk from a time window of vitals and labs."""
    start = time.time()

    # Extract latest readings for SOFA and contributing factors
    latest_vital = sorted(req.vitals, key=lambda v: v.timestamp)[-1]
    latest_lab = sorted(req.labs, key=lambda l: l.timestamp)[-1] if req.labs else None

    vitals_dict = {
        "heart_rate": latest_vital.heart_rate,
        "respiratory_rate": latest_vital.respiratory_rate,
        "spo2": latest_vital.spo2,
        "sbp": latest_vital.sbp,
        "dbp": latest_vital.dbp,
        "temperature": latest_vital.temperature,
        "mbp": latest_vital.mbp,
    }
    labs_dict = {
        "wbc": getattr(latest_lab, "wbc", None) if latest_lab else None,
        "lactate": getattr(latest_lab, "lactate", None) if latest_lab else None,
        "creatinine": getattr(latest_lab, "creatinine", None) if latest_lab else None,
        "platelets": getattr(latest_lab, "platelets", None) if latest_lab else None,
        "bilirubin": getattr(latest_lab, "bilirubin", None) if latest_lab else None,
    }

    sofa = compute_sofa(vitals_dict, labs_dict)
    factors = build_contributing_factors(vitals_dict, labs_dict)

    # Run model inference
    model_used = "heuristic"
    risk_score = heuristic_risk(sofa, vitals_dict, labs_dict)

    if _state["model_available"]:
        try:
            X_seq, X_flat = request_to_arrays(req)

            scores = []
            if _state["lgbm_model"] is not None:
                lgbm_prob = float(_state["lgbm_model"].predict_proba(X_flat)[0])
                scores.append(lgbm_prob)
                model_used = "lgbm"

            if _state["lstm_model"] is not None:
                lstm_prob = float(_state["lstm_model"].predict_proba(X_seq)[0])
                scores.append(lstm_prob)
                model_used = "lstm"

            if len(scores) == 2:
                risk_score = 0.4 * scores[0] + 0.6 * scores[1]  # weight LSTM higher
                model_used = "ensemble"
            elif scores:
                risk_score = scores[0]

        except Exception as e:
            logger.error("Model inference failed, using heuristic: %s", e)
            model_used = "heuristic"

    alert_level = risk_to_alert(risk_score)
    predicted_onset = (4.0 * (1 - risk_score)) if risk_score > 0.25 else None

    duration = time.time() - start
    INFERENCE_DURATION.observe(duration)
    PREDICTIONS_TOTAL.labels(alert_level=alert_level.value).inc()
    REQUEST_COUNT.labels(method="POST", endpoint="/predict", status=200).inc()
    REQUEST_DURATION.labels(endpoint="/predict").observe(duration)

    return SepsisPrediction(
        risk_score=round(risk_score, 4),
        alert_level=alert_level,
        sofa_score=sofa.total,
        sofa_components=sofa,
        predicted_onset_hours=round(predicted_onset, 1) if predicted_onset else None,
        contributing_factors=factors,
        model_used=model_used,
    )


@app.get("/patient/{stay_id}/timeline", response_model=PatientTimeline)
async def patient_timeline(stay_id: int):
    """Return historical risk timeline for a patient.

    In production this would query MongoDB for the stay's charted data and run
    the model over sliding windows. For demo purposes we generate realistic
    synthetic data.
    """
    REQUEST_COUNT.labels(method="GET", endpoint="/patient/timeline", status=200).inc()

    now = datetime.utcnow()
    admission_time = now - timedelta(hours=random.randint(12, 72))
    n_points = int((now - admission_time).total_seconds() / 3600)

    timeline: List[TimelinePoint] = []
    base_risk = random.uniform(0.05, 0.15)

    for h in range(n_points):
        ts = admission_time + timedelta(hours=h)
        # Simulate gradual risk increase with noise
        progress = h / max(n_points, 1)
        trend_risk = base_risk + 0.6 * (progress ** 2)
        noise = random.gauss(0, 0.03)
        risk = max(0.0, min(1.0, trend_risk + noise))

        # Simulate vitals
        hr = 75 + 25 * progress + random.gauss(0, 3)
        rr = 16 + 8 * progress + random.gauss(0, 1)
        spo2 = 98 - 6 * progress + random.gauss(0, 1)
        sbp = 125 - 30 * progress + random.gauss(0, 5)
        temp = 36.8 + 1.5 * progress + random.gauss(0, 0.2)
        lactate = 0.8 + 3 * progress + random.gauss(0, 0.2)

        sofa_score = int(min(20, max(0, risk * 15)))

        timeline.append(TimelinePoint(
            timestamp=ts,
            risk_score=round(risk, 4),
            alert_level=risk_to_alert(risk),
            sofa_score=sofa_score,
            heart_rate=round(hr, 1),
            respiratory_rate=round(rr, 1),
            spo2=round(max(80, spo2), 1),
            sbp=round(max(60, sbp), 1),
            temperature=round(temp, 1),
            lactate=round(max(0.1, lactate), 2),
        ))

    current_risk = timeline[-1].risk_score if timeline else 0.0
    peak = max(timeline, key=lambda t: t.risk_score) if timeline else None

    return PatientTimeline(
        stay_id=stay_id,
        careunit="MICU",
        admission_time=admission_time,
        timeline=timeline,
        current_alert=risk_to_alert(current_risk),
        peak_risk=peak.risk_score if peak else 0.0,
        peak_risk_time=peak.timestamp if peak else None,
    )


# MIMIC chartevents itemids written by the data-ingestion simulator
_VITAL_ITEMIDS = {
    220045: "heart_rate",
    220210: "respiratory_rate",
    220277: "spo2",
    220179: "sbp",
    220180: "dbp",
    223761: "temperature",
}

# Short labels for the bed column on the dashboard
_CAREUNIT_SHORT = {
    "Medical Intensive Care Unit (MICU)": "MICU",
    "Surgical Intensive Care Unit (SICU)": "SICU",
    "Coronary Care Unit (CCU)": "CCU",
    "Trauma SICU (TSICU)": "TSICU",
    "Cardiac Vascular Intensive Care Unit (CVICU)": "CVICU",
    "Medical/Surgical Intensive Care Unit (MICU/SICU)": "MICU",
    "Neuro Surgical Intensive Care Unit (Neuro SICU)": "NSICU",
}


@app.get("/unit-overview", response_model=UnitOverview)
async def unit_overview():
    """Return current risk status for all active ICU patients from the simulator.

    Sources:
      * MIMIC_SIM.admissions  — active hadm_ids (status='admitted')
      * MIMIC_SIM.transfers   — latest careunit per hadm_id (filtered to ICU)
      * MIMIC_SIM.chartevents — latest vitals per patient → NEWS2 → risk score
    """
    REQUEST_COUNT.labels(method="GET", endpoint="/unit-overview", status=200).inc()

    from shared.constants.hospital import map_department
    from shared.clinical.news2 import compute_news2
    from shared.db.mongo import MongoManager

    mongo = _state.get("_mongo") or MongoManager()
    sim_db = mongo.client["MIMIC_SIM"]

    now = datetime.utcnow()
    patients: List[PatientSummary] = []

    admitted = sim_db["admissions"].find(
        {"status": "admitted"},
        {"_id": 0, "hadm_id": 1, "subject_id": 1, "sim_admittime": 1},
    )

    for adm in admitted:
        hadm = adm.get("hadm_id")
        if not hadm:
            continue
        last_xfer = sim_db["transfers"].find_one(
            {"hadm_id": hadm},
            sort=[("intime", -1)],
        )
        careunit = (last_xfer or {}).get("careunit") or ""
        if map_department(careunit) != "ICU":
            continue

        # Latest reading per vital itemid
        latest: Dict[str, float] = {}
        cursor = sim_db["chartevents"].find(
            {"hadm_id": hadm, "itemid": {"$in": list(_VITAL_ITEMIDS.keys())}},
            sort=[("charttime", -1)],
            limit=60,
        )
        for ev in cursor:
            name = _VITAL_ITEMIDS.get(ev.get("itemid"))
            if name and name not in latest and ev.get("valuenum") is not None:
                latest[name] = float(ev["valuenum"])
            if len(latest) == len(_VITAL_ITEMIDS):
                break

        news2 = compute_news2(
            respiratory_rate=latest.get("respiratory_rate"),
            spo2=latest.get("spo2"),
            temperature_c=latest.get("temperature"),
            systolic_bp=latest.get("sbp"),
            heart_rate=latest.get("heart_rate"),
        )
        # NEWS2 0-20 → risk 0.0-1.0; 11+ → red, 7+ → orange, 3+ → yellow
        risk = min(1.0, news2.total / 15.0)

        # Hours in ICU since latest transfer (or admission)
        intime_raw = (last_xfer or {}).get("intime") or adm.get("sim_admittime")
        try:
            intime_dt = datetime.fromisoformat(str(intime_raw).replace("Z", ""))
            hours = max(0.0, (now - intime_dt).total_seconds() / 3600.0)
        except Exception:
            hours = 0.0

        cu_short = _CAREUNIT_SHORT.get(careunit, "ICU")

        patients.append(PatientSummary(
            stay_id=int(adm.get("subject_id") or 0),
            bed=f"{cu_short}-{len(patients)+1:02d}",
            age=0,
            gender="U",
            careunit=cu_short,
            hours_in_icu=round(hours, 1),
            current_risk=round(risk, 4),
            alert_level=risk_to_alert(risk),
            sofa_score=int(round(risk * 15)),
            trend="stable",
            last_updated=now,
        ))

    patients.sort(key=lambda p: p.current_risk, reverse=True)

    red = sum(1 for p in patients if p.alert_level == AlertLevel.RED)
    orange = sum(1 for p in patients if p.alert_level == AlertLevel.ORANGE)
    yellow = sum(1 for p in patients if p.alert_level == AlertLevel.YELLOW)

    return UnitOverview(
        unit_name="ICU",
        total_patients=len(patients),
        red_alerts=red,
        orange_alerts=orange,
        yellow_alerts=yellow,
        patients=patients,
    )


@app.websocket("/ws/monitor")
async def ws_monitor(websocket: WebSocket):
    """Real-time monitoring WebSocket.

    Streams patient risk updates every 5 seconds for demo purposes.
    In production this would be driven by live charted data.
    """
    await websocket.accept()
    logger.info("WebSocket client connected")

    try:
        while True:
            # Generate a simulated update
            now = datetime.utcnow()
            stay_id = random.randint(30000001, 30000020)
            risk = random.betavariate(2, 5)
            alert = risk_to_alert(risk)
            sofa = int(risk * 15)

            update = {
                "type": "risk_update",
                "timestamp": now.isoformat(),
                "stay_id": stay_id,
                "bed": f"MICU-{stay_id % 20 + 1:02d}",
                "risk_score": round(risk, 4),
                "alert_level": alert.value,
                "sofa_score": sofa,
                "vitals": {
                    "heart_rate": round(random.gauss(85, 15), 1),
                    "respiratory_rate": round(random.gauss(18, 4), 1),
                    "spo2": round(max(80, random.gauss(96, 3)), 1),
                    "sbp": round(random.gauss(115, 20), 1),
                    "temperature": round(random.gauss(37.2, 0.8), 1),
                },
            }

            await websocket.send_json(update)
            await asyncio.sleep(5)

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")


@app.get("/health", response_model=HealthResponse)
async def health_check():
    REQUEST_COUNT.labels(method="GET", endpoint="/health", status=200).inc()
    return HealthResponse(
        status="healthy",
        model_loaded=_state["model_available"],
        version="1.0.0",
        uptime_seconds=round(time.time() - _state["start_time"], 1),
    )


@app.get("/metrics")
async def metrics():
    """Prometheus-compatible metrics endpoint."""
    from fastapi.responses import Response
    return Response(content=generate_latest(), media_type="text/plain")


# ---------------------------------------------------------------------------
# Digital Twin integration — Bug #2 + Integrations 1/5 + Rule 3 cascade
# ---------------------------------------------------------------------------

_recent_screens: list = []
_admitted_patients: Dict[str, Dict[str, Any]] = {}


def _screen_to_alert_level(risk: float) -> str:
    """Translate a 0-1 risk into the platform-standard alert level."""
    if risk >= 0.75:
        return "RED"
    if risk >= 0.50:
        return "ORANGE"
    if risk >= 0.25:
        return "YELLOW"
    return "GREEN"


@app.post("/sepsis-screen")
async def sepsis_screen(payload: dict):
    """Integration 1 step 7 — quick sepsis screen from partial admission data.

    Accepts a smaller payload than ``/predict`` (vitals + labs + age/gender)
    and returns ``{alert_level, risk_score, recommended_action}``.
    Internally uses the heuristic scorer when the deep model is unavailable.
    """
    try:
        from .ensemble import heuristic_risk  # type: ignore
    except Exception:
        heuristic_risk = None  # type: ignore
    vitals = payload.get("vitals") or {}
    labs = payload.get("labs") or {}
    age = float(payload.get("age", 65))

    risk = 0.2
    if heuristic_risk is not None:
        try:
            risk = float(heuristic_risk(vitals=vitals, labs=labs, age=age))
        except Exception:
            pass
    else:
        # Fallback heuristic if ensemble module missing
        lactate = float(labs.get("lactate") or 0)
        wbc = float(labs.get("wbc") or 0)
        hr = float(vitals.get("heart_rate") or 80)
        temp = float(vitals.get("temperature") or 37)
        risk = min(1.0, 0.1 + (lactate / 10) + max(0, (hr - 90) / 100) + max(0, (temp - 38) / 5) + max(0, (wbc - 12) / 20))

    alert = _screen_to_alert_level(risk)
    response = {
        "hadm_id": payload.get("hadm_id"),
        "risk_score": round(risk, 3),
        "alert_level": alert,
        "sofa_total": max(0, int(risk * 10)),
        "care_unit": payload.get("care_unit"),
        "recommended_action": (
            "urgent clinical review" if alert in ("ORANGE", "RED")
            else "routine monitoring"
        ),
    }
    _recent_screens.append(response)
    if len(_recent_screens) > 1000:
        del _recent_screens[:500]
    return {"status": "ok", "data": response}


@app.post("/screen")
async def fast_screen(payload: dict):
    """Rule 3 cascade — lightweight re-screen on every new vital.

    Shares the same underlying scorer as ``/sepsis-screen`` and is wrapped
    in the Digital Twin's 2-second timeout. Intended for high-volume
    invocations from ``process_vital``.
    """
    return await sepsis_screen(payload)


@app.post("/admit-patient")
async def admit_patient(payload: dict):
    """Bug #2 / Rule 4 — accept a transfer handoff when destination is ICU/HDU."""
    hadm_id = str(payload.get("hadm_id", "unknown"))
    _admitted_patients[hadm_id] = dict(payload)
    # Immediate screen so dashboards surface the risk right away
    result = await sepsis_screen(payload)
    return {"status": "ok", "data": {"admitted": True, "initial_screen": result["data"]}}


@app.get("/sepsis/recent-screens")
async def list_recent_screens(limit: int = 50):
    return {"status": "ok", "data": _recent_screens[-limit:]}


@app.post("/reset")
async def reset_sepsis():
    _recent_screens.clear()
    _admitted_patients.clear()
    return {"status": "ok", "reset": True}
