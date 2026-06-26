"""Simple model registry: save / load models with JSON metadata sidecars."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import joblib


class ModelRegistry:
    """Persist trained models alongside their metrics and configuration.

    Each saved model produces two files under ``base_path``:
    - ``<name>.joblib``  -- the serialised model
    - ``<name>.meta.json`` -- metrics, config, timestamps
    """

    DEFAULT_DIR = "models"

    def __init__(self, base_path: Optional[str] = None) -> None:
        self.base_path = Path(base_path or os.getenv("MODEL_DIR", self.DEFAULT_DIR))
        self.base_path.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_model(
        self,
        model: Any,
        name: str,
        metrics: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
        path: Optional[str] = None,
    ) -> Path:
        """Serialise *model* and write a metadata sidecar.

        Parameters
        ----------
        model:
            Any object serialisable by ``joblib`` (sklearn estimators,
            numpy arrays, custom objects, etc.).
        name:
            Logical model name (used as the file stem).
        metrics:
            Evaluation metrics to record in the sidecar.
        config:
            Training configuration / hyper-parameters to record.
        path:
            Override directory (defaults to ``self.base_path``).

        Returns
        -------
        Path to the saved ``.joblib`` file.
        """
        target_dir = Path(path) if path else self.base_path
        target_dir.mkdir(parents=True, exist_ok=True)

        model_path = target_dir / f"{name}.joblib"
        meta_path = target_dir / f"{name}.meta.json"

        # Persist model — use torch.save for PyTorch-based models
        try:
            joblib.dump(model, model_path)
        except Exception:
            import torch
            torch_path = target_dir / f"{name}.pt"
            if hasattr(model, '_net') and model._net is not None:
                torch.save({
                    'state_dict': model._net.state_dict(),
                    'params': model.get_params(),
                }, torch_path)
                model_path = torch_path
            else:
                raise

        # Build metadata
        meta: Dict[str, Any] = {
            "name": name,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "model_file": model_path.name,
            "model_type": type(model).__qualname__,
        }
        if metrics:
            meta["metrics"] = metrics
        if config:
            meta["config"] = config

        meta_path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")

        return model_path

    def load_model(
        self,
        name: str,
        path: Optional[str] = None,
    ) -> Tuple[Any, Dict[str, Any]]:
        """Load a previously saved model and its metadata.

        Parameters
        ----------
        name:
            Logical model name (file stem).
        path:
            Override directory (defaults to ``self.base_path``).

        Returns
        -------
        ``(model, metadata_dict)``

        Raises
        ------
        FileNotFoundError
            If the model file does not exist.
        """
        target_dir = Path(path) if path else self.base_path

        model_path = target_dir / f"{name}.joblib"
        meta_path = target_dir / f"{name}.meta.json"

        if not model_path.exists():
            raise FileNotFoundError(f"Model file not found: {model_path}")

        model = joblib.load(model_path)

        metadata: Dict[str, Any] = {}
        if meta_path.exists():
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))

        return model, metadata

    def list_models(
        self,
        path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List all models stored in the registry directory.

        Parameters
        ----------
        path:
            Override directory (defaults to ``self.base_path``).

        Returns
        -------
        List of metadata dicts (one per model). Models without a sidecar
        are still listed with minimal info.
        """
        target_dir = Path(path) if path else self.base_path
        if not target_dir.exists():
            return []

        results: List[Dict[str, Any]] = []
        for model_file in sorted(target_dir.glob("*.joblib")):
            name = model_file.stem
            meta_path = target_dir / f"{name}.meta.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            else:
                meta = {
                    "name": name,
                    "model_file": model_file.name,
                    "saved_at": datetime.fromtimestamp(
                        model_file.stat().st_mtime, tz=timezone.utc
                    ).isoformat(),
                }
            results.append(meta)
        return results


# ---------------------------------------------------------------------------
# Integration 8 — runtime model registration with SimEngine
# ---------------------------------------------------------------------------

async def register_model_load(
    service_name: str,
    model_path: str,
    version: str,
    features_hash: Optional[str] = None,
    metrics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Advertise to the SimEngine that this service just loaded a model.

    Each ML service should call this in its startup hook. The payload is
    POSTed to ``/models/registry`` on SimEngine (port 8207) and aggregated
    at ``GET /models/registry`` for dashboards and drift monitoring.
    Failure is non-fatal — the call is best-effort.
    """
    try:
        from shared.integration.service_client import ServiceClient  # local to avoid cycle
        client = ServiceClient()
        return await client.data_ingestion.post("/models/registry", {
            "service_name": service_name,
            "model_path": str(model_path),
            "version": version,
            "features_hash": features_hash,
            "metrics": metrics or {},
        })
    except Exception as exc:  # noqa: BLE001 — best effort
        import logging
        logging.getLogger(__name__).warning("register_model_load_failed: %s", exc)
        return {"status": "error", "error": str(exc)}
