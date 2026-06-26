"""SHAP / feature-importance helpers for EU AI Act Art. 13 transparency.

Every high-risk ML service (ED Triage 8201, Sepsis ICU 8202, Hospital Ops
MARL 8203, Oncology AI 8204, Bed Management 8208) must emit an explanation
alongside every prediction so the XAI audit service (port 8218) can store
and surface it to clinicians.

The helpers here degrade gracefully: if ``shap`` is not installed, they fall
back to permutation importance or the model's built-in ``feature_importances_``
— the XAI record is marked with ``method`` so auditors can tell what was used.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

try:
    import shap  # type: ignore
    _SHAP_AVAILABLE = True
except Exception:
    shap = None  # type: ignore
    _SHAP_AVAILABLE = False


def features_hash(feature_names: Sequence[str]) -> str:
    """Stable hash of the feature list — used by the model registry to detect drift."""
    joined = "\n".join(sorted(feature_names)).encode()
    return hashlib.sha256(joined).hexdigest()[:16]


def shap_values_for(
    model: Any,
    features: Dict[str, float] | np.ndarray,
    feature_names: Optional[Sequence[str]] = None,
    *,
    background: Optional[np.ndarray] = None,
    top_k: int = 10,
) -> Dict[str, Any]:
    """Return SHAP values for a single prediction, or a graceful fallback.

    Returns a dict with keys:
      - ``method``:        ``"shap_tree"`` | ``"shap_kernel"`` | ``"builtin"`` | ``"none"``
      - ``shap_values``:   list of {feature, value} (top_k)
      - ``base_value``:    expected model output (may be ``None``)
    """
    if isinstance(features, dict):
        if feature_names is None:
            feature_names = list(features.keys())
        x = np.array([[features.get(k, 0.0) for k in feature_names]], dtype=float)
    else:
        x = np.atleast_2d(features).astype(float)
        if feature_names is None:
            feature_names = [f"f{i}" for i in range(x.shape[1])]

    # Preferred: TreeExplainer for tree-based models
    if _SHAP_AVAILABLE:
        try:
            explainer = shap.TreeExplainer(model)
            sv = explainer.shap_values(x)
            if isinstance(sv, list):  # binary classifier returns a list
                sv = sv[1] if len(sv) > 1 else sv[0]
            sv = np.asarray(sv).ravel()
            return _format_shap("shap_tree", sv, feature_names, top_k, base=_safe_base(explainer))
        except Exception as exc:
            logger.debug("shap_tree_failed", exc_info=exc)

        # Kernel fallback if we have a background set
        try:
            if background is not None and hasattr(model, "predict_proba"):
                explainer = shap.KernelExplainer(model.predict_proba, background)
                sv = explainer.shap_values(x, nsamples=100)
                if isinstance(sv, list):
                    sv = sv[1] if len(sv) > 1 else sv[0]
                sv = np.asarray(sv).ravel()
                return _format_shap("shap_kernel", sv, feature_names, top_k, base=_safe_base(explainer))
        except Exception as exc:
            logger.debug("shap_kernel_failed", exc_info=exc)

    # Builtin importance fallback
    importances = getattr(model, "feature_importances_", None)
    if importances is not None:
        return _format_shap(
            "builtin",
            np.asarray(importances) * np.asarray(x.ravel()),
            feature_names,
            top_k,
            base=None,
        )

    return {"method": "none", "shap_values": [], "base_value": None}


def _safe_base(explainer: Any) -> Optional[float]:
    base = getattr(explainer, "expected_value", None)
    if base is None:
        return None
    try:
        if np.ndim(base) > 0:
            base = np.asarray(base).ravel()[-1]
        return float(base)
    except Exception:
        return None


def _format_shap(
    method: str,
    values: np.ndarray,
    names: Sequence[str],
    top_k: int,
    base: Optional[float],
) -> Dict[str, Any]:
    order = np.argsort(np.abs(values))[::-1][:top_k]
    return {
        "method": method,
        "shap_values": [
            {"feature": names[i], "value": float(values[i])}
            for i in order
        ],
        "base_value": base,
    }


def explanation_payload(
    *,
    module: str,
    model_version: str,
    input_features: Dict[str, Any],
    prediction: Any,
    confidence: Optional[float],
    shap_result: Dict[str, Any],
    prediction_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Shape the POST body for the XAI service ``/xai/log-decision`` endpoint."""
    return {
        "module": module,
        "model_version": model_version,
        "prediction_id": prediction_id,
        "input_features": input_features,
        "prediction": prediction,
        "confidence": confidence,
        "shap_method": shap_result.get("method"),
        "shap_values": shap_result.get("shap_values", []),
        "base_value": shap_result.get("base_value"),
    }


__all__ = ["shap_values_for", "features_hash", "explanation_payload"]
