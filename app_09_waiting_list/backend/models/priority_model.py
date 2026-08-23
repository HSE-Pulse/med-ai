"""
Waiting List Model Definitions
===============================
1. **PriorityClassifier** -- XGBoost 3-class (urgent/soon/routine).
2. **AdverseOutcomePredictor** -- XGBoost binary for adverse outcome.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("waiting_list.model")

URGENCY_LABELS = {0: "urgent", 1: "soon", 2: "routine"}


class PriorityClassifier:
    """XGBoost 3-class urgency classifier."""

    def __init__(self, n_estimators=200, max_depth=5, learning_rate=0.1,
                 subsample=0.8, colsample_bytree=0.8, random_state=42) -> None:
        self.params = dict(
            n_estimators=n_estimators, max_depth=max_depth,
            learning_rate=learning_rate, subsample=subsample,
            colsample_bytree=colsample_bytree, random_state=random_state,
            objective="multi:softprob", num_class=3, eval_metric="mlogloss",
            use_label_encoder=False, tree_method="hist", device="cuda",
            n_jobs=-1, verbosity=1,
        )
        self._model = None
        self._feature_names: Optional[List[str]] = None

    def fit(self, X, y, X_val=None, y_val=None) -> "PriorityClassifier":
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


class AdverseOutcomePredictor:
    """XGBoost binary classifier for adverse outcome."""

    def __init__(self, n_estimators=200, max_depth=5, learning_rate=0.1,
                 scale_pos_weight=None, subsample=0.8, colsample_bytree=0.8,
                 random_state=42) -> None:
        self._scale_pos_weight = scale_pos_weight
        self.params = dict(
            n_estimators=n_estimators, max_depth=max_depth,
            learning_rate=learning_rate, subsample=subsample,
            colsample_bytree=colsample_bytree, random_state=random_state,
            objective="binary:logistic", eval_metric="logloss",
            use_label_encoder=False, tree_method="hist", device="cuda",
            n_jobs=-1, verbosity=1,
        )
        self._model = None
        self._feature_names: Optional[List[str]] = None

    def fit(self, X, y, X_val=None, y_val=None) -> "AdverseOutcomePredictor":
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
