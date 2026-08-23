"""
ED Flow Model Definitions
==========================
1. **DispositionClassifier** -- XGBoost multi-class (5 dispositions).
2. **EDLOSRegressor** -- XGBoost regressor for ED LOS in minutes.
3. **PETBreachClassifier** -- XGBoost binary for 6-hour PET breach.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("ed_flow.model")

DISPOSITION_LABELS = ["admit_to_inpatient", "discharge_home", "transfer", "expired", "lwbs"]
NUM_DISPOSITIONS = 5


class DispositionClassifier:
    """XGBoost multi-class classifier for ED disposition."""

    def __init__(self, n_estimators=300, max_depth=6, learning_rate=0.1,
                 subsample=0.8, colsample_bytree=0.8, random_state=42) -> None:
        self.params = dict(
            n_estimators=n_estimators, max_depth=max_depth,
            learning_rate=learning_rate, subsample=subsample,
            colsample_bytree=colsample_bytree, random_state=random_state,
            objective="multi:softprob", num_class=NUM_DISPOSITIONS,
            eval_metric="mlogloss", use_label_encoder=False,
            tree_method="hist", device="cuda", n_jobs=-1, verbosity=1,
        )
        self._model = None
        self._feature_names: Optional[List[str]] = None

    def fit(self, X, y, X_val=None, y_val=None) -> "DispositionClassifier":
        from xgboost import XGBClassifier
        if isinstance(X, pd.DataFrame):
            self._feature_names = X.columns.tolist()
        self._model = XGBClassifier(**self.params)
        eval_set = [(X, y)]
        if X_val is not None and y_val is not None:
            eval_set.append((X_val, y_val))
        self._model.fit(X, y, eval_set=eval_set, verbose=50)
        return self

    def predict(self, X) -> np.ndarray:
        return self._model.predict(X)

    def predict_proba(self, X) -> np.ndarray:
        return self._model.predict_proba(X)

    @property
    def feature_importances(self) -> Dict[str, float]:
        if self._model is None or self._feature_names is None:
            return {}
        return {n: float(v) for n, v in zip(self._feature_names, self._model.feature_importances_)}

    def get_params(self) -> Dict:
        return {**self.params}


class EDLOSRegressor:
    """XGBoost regressor for ED LOS in minutes."""

    def __init__(self, n_estimators=300, max_depth=6, learning_rate=0.1,
                 subsample=0.8, colsample_bytree=0.8, random_state=42) -> None:
        self.params = dict(
            n_estimators=n_estimators, max_depth=max_depth,
            learning_rate=learning_rate, subsample=subsample,
            colsample_bytree=colsample_bytree, random_state=random_state,
            objective="reg:squarederror", eval_metric="rmse",
            tree_method="hist", device="cuda", n_jobs=-1, verbosity=1,
        )
        self._model = None
        self._feature_names: Optional[List[str]] = None

    def fit(self, X, y, X_val=None, y_val=None) -> "EDLOSRegressor":
        from xgboost import XGBRegressor
        if isinstance(X, pd.DataFrame):
            self._feature_names = X.columns.tolist()
        self._model = XGBRegressor(**self.params)
        eval_set = [(X, y)]
        if X_val is not None and y_val is not None:
            eval_set.append((X_val, y_val))
        self._model.fit(X, y, eval_set=eval_set, verbose=50)
        return self

    def predict(self, X) -> np.ndarray:
        return np.clip(self._model.predict(X), 0, 1440)

    def get_params(self) -> Dict:
        return {**self.params}


class PETBreachClassifier:
    """XGBoost binary classifier for 6-hour PET breach."""

    def __init__(self, n_estimators=300, max_depth=6, learning_rate=0.1,
                 scale_pos_weight=None, subsample=0.8, colsample_bytree=0.8,
                 random_state=42) -> None:
        self._scale_pos_weight = scale_pos_weight
        self.params = dict(
            n_estimators=n_estimators, max_depth=max_depth,
            learning_rate=learning_rate, subsample=subsample,
            colsample_bytree=colsample_bytree, random_state=random_state,
            objective="binary:logistic", eval_metric="logloss",
            use_label_encoder=False, tree_method="hist",
            device="cuda", n_jobs=-1, verbosity=1,
        )
        self._model = None
        self._feature_names: Optional[List[str]] = None

    def fit(self, X, y, X_val=None, y_val=None) -> "PETBreachClassifier":
        from xgboost import XGBClassifier
        if self._scale_pos_weight is None:
            n_neg, n_pos = int((y == 0).sum()), int((y == 1).sum())
            self.params["scale_pos_weight"] = n_neg / max(n_pos, 1)
        else:
            self.params["scale_pos_weight"] = self._scale_pos_weight

        if isinstance(X, pd.DataFrame):
            self._feature_names = X.columns.tolist()
        self._model = XGBClassifier(**self.params)
        eval_set = [(X, y)]
        if X_val is not None and y_val is not None:
            eval_set.append((X_val, y_val))
        self._model.fit(X, y, eval_set=eval_set, verbose=50)
        return self

    def predict(self, X) -> np.ndarray:
        return self._model.predict(X)

    def predict_proba(self, X) -> np.ndarray:
        proba = self._model.predict_proba(X)
        return proba[:, 1] if proba.ndim == 2 else proba

    def get_params(self) -> Dict:
        return {**self.params}
