"""
Bed Management Model Definitions
=================================
1. **DischargeClassifier** -- XGBoost binary classifier for discharge_within_24h.
2. **LOSRegressor** -- XGBoost regressor for remaining_los_hours.
3. **CapacityForecaster** -- Statistical hourly census model by department.

All expose: ``fit()``, ``predict()``, ``predict_proba()`` (where applicable),
``get_params()``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("bed_management.model")


# ===================================================================
# 1.  Discharge Classifier (binary)
# ===================================================================
class DischargeClassifier:
    """XGBoost binary classifier predicting P(discharge within 24 h)."""

    def __init__(
        self,
        n_estimators: int = 300,
        max_depth: int = 6,
        learning_rate: float = 0.1,
        min_child_weight: int = 5,
        subsample: float = 0.8,
        colsample_bytree: float = 0.8,
        reg_alpha: float = 0.1,
        reg_lambda: float = 1.0,
        scale_pos_weight: Optional[float] = None,
        random_state: int = 42,
    ) -> None:
        self._scale_pos_weight = scale_pos_weight
        self.params = dict(
            n_estimators=n_estimators,
            max_depth=max_depth,
            learning_rate=learning_rate,
            min_child_weight=min_child_weight,
            subsample=subsample,
            colsample_bytree=colsample_bytree,
            reg_alpha=reg_alpha,
            reg_lambda=reg_lambda,
            random_state=random_state,
            objective="binary:logistic",
            eval_metric="logloss",
            use_label_encoder=False,
            tree_method="hist",
            device="cuda",
            n_jobs=-1,
            verbosity=1,
        )
        self._model = None
        self._feature_names: Optional[List[str]] = None

    def fit(
        self,
        X: pd.DataFrame | np.ndarray,
        y: np.ndarray,
        X_val: Optional[pd.DataFrame | np.ndarray] = None,
        y_val: Optional[np.ndarray] = None,
    ) -> "DischargeClassifier":
        from xgboost import XGBClassifier

        # Auto-compute class weight
        if self._scale_pos_weight is None:
            n_neg = int((y == 0).sum())
            n_pos = int((y == 1).sum())
            self.params["scale_pos_weight"] = n_neg / max(n_pos, 1)
            logger.info("Auto scale_pos_weight: %.2f", self.params["scale_pos_weight"])
        else:
            self.params["scale_pos_weight"] = self._scale_pos_weight

        if isinstance(X, pd.DataFrame):
            self._feature_names = X.columns.tolist()

        self._model = XGBClassifier(**self.params)
        eval_set = [(X, y)]
        if X_val is not None and y_val is not None:
            eval_set.append((X_val, y_val))

        self._model.fit(X, y, eval_set=eval_set, verbose=50)
        logger.info("DischargeClassifier training complete.")
        return self

    def predict(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        return self._model.predict(X)

    def predict_proba(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """Return P(discharge within 24h) — shape (n,)."""
        proba = self._model.predict_proba(X)
        return proba[:, 1] if proba.ndim == 2 else proba

    @property
    def feature_importances(self) -> Dict[str, float]:
        if self._model is None or self._feature_names is None:
            return {}
        imp = self._model.feature_importances_
        return {n: float(v) for n, v in zip(self._feature_names, imp)}

    def get_params(self) -> Dict[str, Any]:
        return {**self.params}


# ===================================================================
# 2.  LOS Regressor
# ===================================================================
class LOSRegressor:
    """XGBoost regressor predicting remaining LOS in hours."""

    def __init__(
        self,
        n_estimators: int = 300,
        max_depth: int = 6,
        learning_rate: float = 0.1,
        subsample: float = 0.8,
        colsample_bytree: float = 0.8,
        random_state: int = 42,
    ) -> None:
        self.params = dict(
            n_estimators=n_estimators,
            max_depth=max_depth,
            learning_rate=learning_rate,
            subsample=subsample,
            colsample_bytree=colsample_bytree,
            random_state=random_state,
            objective="reg:squarederror",
            eval_metric="rmse",
            tree_method="hist",
            device="cuda",
            n_jobs=-1,
            verbosity=1,
        )
        self._model = None
        self._feature_names: Optional[List[str]] = None

    def fit(
        self,
        X: pd.DataFrame | np.ndarray,
        y: np.ndarray,
        X_val: Optional[pd.DataFrame | np.ndarray] = None,
        y_val: Optional[np.ndarray] = None,
    ) -> "LOSRegressor":
        from xgboost import XGBRegressor

        if isinstance(X, pd.DataFrame):
            self._feature_names = X.columns.tolist()

        self._model = XGBRegressor(**self.params)
        eval_set = [(X, y)]
        if X_val is not None and y_val is not None:
            eval_set.append((X_val, y_val))

        self._model.fit(X, y, eval_set=eval_set, verbose=50)
        logger.info("LOSRegressor training complete.")
        return self

    def predict(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        preds = self._model.predict(X)
        return np.clip(preds, 0, 720)

    @property
    def feature_importances(self) -> Dict[str, float]:
        if self._model is None or self._feature_names is None:
            return {}
        imp = self._model.feature_importances_
        return {n: float(v) for n, v in zip(self._feature_names, imp)}

    def get_params(self) -> Dict[str, Any]:
        return {**self.params}


# ===================================================================
# 3.  Capacity Forecaster (Statistical)
# ===================================================================
class CapacityForecaster:
    """Statistical forecaster: predicts hourly census per department
    using historical (careunit, hour_of_day, day_of_week) averages."""

    def __init__(self) -> None:
        self._profiles: Optional[pd.DataFrame] = None

    def fit(self, capacity_df: pd.DataFrame) -> "CapacityForecaster":
        """Fit from capacity_hourly.parquet (columns: careunit, timestamp,
        census, hour_of_day, day_of_week)."""
        if capacity_df.empty:
            logger.warning("CapacityForecaster: empty input.")
            self._profiles = pd.DataFrame()
            return self

        self._profiles = (
            capacity_df
            .groupby(["careunit", "hour_of_day", "day_of_week"])["census"]
            .agg(["mean", "std", "count"])
            .reset_index()
        )
        self._profiles["std"] = self._profiles["std"].fillna(0)
        logger.info("CapacityForecaster fitted on %d department-hour-day combos.",
                     len(self._profiles))
        return self

    def predict(self, department: str, horizon_hours: int = 24,
                start_hour: int = 0, start_dow: int = 0) -> List[Dict]:
        """Predict census for each hour in the horizon."""
        if self._profiles is None or self._profiles.empty:
            return []
        dept_data = self._profiles[self._profiles["careunit"] == department]
        if dept_data.empty:
            return []

        forecasts = []
        for h in range(horizon_hours):
            hour = (start_hour + h) % 24
            dow = (start_dow + (start_hour + h) // 24) % 7
            row = dept_data[
                (dept_data["hour_of_day"] == hour) &
                (dept_data["day_of_week"] == dow)
            ]
            if not row.empty:
                mean_census = float(row["mean"].iloc[0])
                std_census = float(row["std"].iloc[0])
            else:
                mean_census = float(dept_data["mean"].mean())
                std_census = float(dept_data["std"].mean())

            forecasts.append({
                "horizon_hours": h + 1,
                "predicted_census": round(mean_census, 1),
                "lower_bound_90": round(max(0, mean_census - 1.645 * std_census), 1),
                "upper_bound_90": round(mean_census + 1.645 * std_census, 1),
            })
        return forecasts

    def get_params(self) -> Dict[str, Any]:
        n_depts = self._profiles["careunit"].nunique() if self._profiles is not None else 0
        return {"type": "statistical_hourly_profile", "n_departments": n_depts}
