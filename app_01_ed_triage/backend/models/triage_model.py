"""
ED Triage Model Definitions
============================
Two model architectures for ESI-equivalent acuity prediction:

1. **TriageXGBoost** -- gradient-boosted tree baseline (fast, interpretable).
2. **TriageNN** -- PyTorch feed-forward network with embedding layers for
   categorical features and dense layers for continuous features.

Both expose a unified interface: ``fit(X, y)``, ``predict(X)``,
``predict_proba(X)``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("ed_triage.model")

# Number of ESI acuity levels
NUM_CLASSES = 5
ACUITY_LABELS = {
    1: "Resuscitation",
    2: "Emergent",
    3: "Urgent",
    4: "Less Urgent",
    5: "Non-urgent",
}


# ===================================================================
# 1.  XGBoost Baseline
# ===================================================================
class TriageXGBoost:
    """XGBoost multiclass classifier for ED acuity prediction.

    Uses ``multi:softprob`` objective to produce calibrated probabilities
    across 5 ESI-equivalent acuity levels.
    """

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
        random_state: int = 42,
        **kwargs: Any,
    ) -> None:
        from xgboost import XGBClassifier

        self.params = {
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "learning_rate": learning_rate,
            "min_child_weight": min_child_weight,
            "subsample": subsample,
            "colsample_bytree": colsample_bytree,
            "reg_alpha": reg_alpha,
            "reg_lambda": reg_lambda,
            "random_state": random_state,
            "objective": "multi:softprob",
            "num_class": NUM_CLASSES,
            "eval_metric": "mlogloss",
            "use_label_encoder": False,
            "tree_method": "hist",
            "device": "cuda",
            "n_jobs": -1,
            "verbosity": 1,
        }
        self.params.update(kwargs)
        self._model = XGBClassifier(**self.params)
        self._feature_names: Optional[List[str]] = None

    def fit(
        self,
        X: pd.DataFrame | np.ndarray,
        y: np.ndarray,
        X_val: Optional[pd.DataFrame | np.ndarray] = None,
        y_val: Optional[np.ndarray] = None,
    ) -> "TriageXGBoost":
        """Train the XGBoost model.

        Parameters
        ----------
        X, y:
            Training features and labels (labels should be 0-indexed).
        X_val, y_val:
            Optional validation set for early stopping.
        """
        if isinstance(X, pd.DataFrame):
            self._feature_names = X.columns.tolist()

        eval_set = [(X, y)]
        if X_val is not None and y_val is not None:
            eval_set.append((X_val, y_val))

        self._model.fit(
            X,
            y,
            eval_set=eval_set,
            verbose=50,
        )
        best_iter = getattr(self._model, "best_iteration", None)
        logger.info("XGBoost training complete. Best iteration: %s", best_iter)
        return self

    def predict(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """Return predicted class labels (0-indexed)."""
        return self._model.predict(X)

    def predict_proba(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """Return class probability matrix of shape (n_samples, NUM_CLASSES)."""
        return self._model.predict_proba(X)

    @property
    def feature_importances(self) -> Optional[Dict[str, float]]:
        """Return feature importance dict (gain-based)."""
        if self._feature_names is None:
            return None
        importances = self._model.feature_importances_
        return dict(zip(self._feature_names, importances.tolist()))

    def get_params(self) -> Dict[str, Any]:
        """Return model hyper-parameters."""
        return self.params.copy()


# ===================================================================
# 2.  PyTorch Neural Network (Advanced)
# ===================================================================
class TriageNN:
    """Feed-forward neural network with embedding layers for categoricals.

    Architecture:
        - Embedding layers for each categorical feature (ICD category, arrival mode)
        - BatchNorm + Dense layers for continuous features
        - Concatenation -> hidden layers -> softmax output

    Wraps PyTorch internals behind the same fit / predict / predict_proba API.
    """

    def __init__(
        self,
        numeric_dim: int = 18,
        categorical_dims: Optional[Dict[str, int]] = None,
        embedding_dim: int = 8,
        hidden_dims: Optional[List[int]] = None,
        dropout: float = 0.3,
        lr: float = 1e-3,
        weight_decay: float = 1e-4,
        epochs: int = 50,
        batch_size: int = 256,
        patience: int = 7,
        device: str = "auto",
    ) -> None:
        self.numeric_dim = numeric_dim
        self.categorical_dims = categorical_dims or {}
        self.embedding_dim = embedding_dim
        self.hidden_dims = hidden_dims or [256, 128, 64]
        self.dropout = dropout
        self.lr = lr
        self.weight_decay = weight_decay
        self.epochs = epochs
        self.batch_size = batch_size
        self.patience = patience
        if device == "auto":
            import torch
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device
        self._net: Optional[Any] = None
        self._feature_names: Optional[List[str]] = None
        self._numeric_cols: Optional[List[str]] = None
        self._cat_cols: Optional[List[str]] = None

    def _build_net(self) -> Any:
        """Build and return the PyTorch module."""
        import torch
        import torch.nn as nn

        total_emb_dim = sum(
            self.embedding_dim for _ in self.categorical_dims
        )
        input_dim = self.numeric_dim + total_emb_dim

        layers: list[nn.Module] = []
        prev_dim = input_dim
        for h_dim in self.hidden_dims:
            layers.extend([
                nn.Linear(prev_dim, h_dim),
                nn.BatchNorm1d(h_dim),
                nn.ReLU(),
                nn.Dropout(self.dropout),
            ])
            prev_dim = h_dim
        layers.append(nn.Linear(prev_dim, NUM_CLASSES))

        class _TriageNet(nn.Module):
            def __init__(self_net, cat_dims, emb_dim, dense_layers):
                super().__init__()
                self_net.embeddings = nn.ModuleDict({
                    name: nn.Embedding(num_classes, emb_dim)
                    for name, num_classes in cat_dims.items()
                })
                self_net.bn_input = nn.BatchNorm1d(input_dim - total_emb_dim)
                self_net.dense = nn.Sequential(*dense_layers)

            def forward(self_net, x_numeric, x_cat_dict):
                x_num = self_net.bn_input(x_numeric)
                emb_parts = [
                    self_net.embeddings[name](x_cat_dict[name])
                    for name in self_net.embeddings
                ]
                if emb_parts:
                    x = torch.cat([x_num] + emb_parts, dim=1)
                else:
                    x = x_num
                return self_net.dense(x)

        net = _TriageNet(self.categorical_dims, self.embedding_dim, layers)
        return net.to(self.device)

    def fit(
        self,
        X: pd.DataFrame | np.ndarray,
        y: np.ndarray,
        X_val: Optional[pd.DataFrame | np.ndarray] = None,
        y_val: Optional[np.ndarray] = None,
    ) -> "TriageNN":
        """Train the neural network with early stopping on validation loss."""
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset

        if isinstance(X, pd.DataFrame):
            self._feature_names = X.columns.tolist()
            X_np = X.values.astype(np.float32)
        else:
            X_np = X.astype(np.float32)

        self.numeric_dim = X_np.shape[1]
        # For simplicity, treat all columns as numeric (ICD/arrival are one-hot already)
        self.categorical_dims = {}
        self._net = self._build_net()

        # Tensors
        X_t = torch.tensor(X_np, dtype=torch.float32, device=self.device)
        y_t = torch.tensor(y.astype(np.int64), dtype=torch.long, device=self.device)

        dataset = TensorDataset(X_t, y_t)
        loader = DataLoader(dataset, batch_size=self.batch_size, shuffle=True)

        # Validation
        has_val = X_val is not None and y_val is not None
        if has_val:
            if isinstance(X_val, pd.DataFrame):
                X_val_np = X_val.values.astype(np.float32)
            else:
                X_val_np = X_val.astype(np.float32)
            X_val_t = torch.tensor(X_val_np, dtype=torch.float32, device=self.device)
            y_val_t = torch.tensor(y_val.astype(np.int64), dtype=torch.long, device=self.device)

        # Class weights for imbalanced data
        class_counts = np.bincount(y.astype(int), minlength=NUM_CLASSES).astype(float)
        class_counts[class_counts == 0] = 1.0
        weights = 1.0 / class_counts
        weights = weights / weights.sum() * NUM_CLASSES
        class_weights = torch.tensor(weights, dtype=torch.float32, device=self.device)

        criterion = nn.CrossEntropyLoss(weight=class_weights)
        optimizer = torch.optim.AdamW(
            self._net.parameters(), lr=self.lr, weight_decay=self.weight_decay
        )
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, mode="min", factor=0.5, patience=3
        )

        best_val_loss = float("inf")
        best_state = None
        epochs_no_improve = 0

        for epoch in range(1, self.epochs + 1):
            self._net.train()
            epoch_loss = 0.0
            n_batches = 0

            for x_batch, y_batch in loader:
                optimizer.zero_grad()
                logits = self._net(x_batch, {})
                loss = criterion(logits, y_batch)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self._net.parameters(), max_norm=1.0)
                optimizer.step()
                epoch_loss += loss.item()
                n_batches += 1

            avg_train_loss = epoch_loss / max(n_batches, 1)

            # Validation
            if has_val:
                self._net.eval()
                with torch.no_grad():
                    val_logits = self._net(X_val_t, {})
                    val_loss = criterion(val_logits, y_val_t).item()
                scheduler.step(val_loss)

                if val_loss < best_val_loss:
                    best_val_loss = val_loss
                    best_state = {k: v.cpu().clone() for k, v in self._net.state_dict().items()}
                    epochs_no_improve = 0
                else:
                    epochs_no_improve += 1

                if epoch % 5 == 0 or epoch == 1:
                    logger.info(
                        "  Epoch %3d/%d  train_loss=%.4f  val_loss=%.4f",
                        epoch, self.epochs, avg_train_loss, val_loss,
                    )

                if epochs_no_improve >= self.patience:
                    logger.info("  Early stopping at epoch %d", epoch)
                    break
            else:
                if epoch % 5 == 0 or epoch == 1:
                    logger.info(
                        "  Epoch %3d/%d  train_loss=%.4f",
                        epoch, self.epochs, avg_train_loss,
                    )

        # Restore best weights
        if best_state is not None:
            self._net.load_state_dict(best_state)

        self._net.eval()
        return self

    def predict(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """Return predicted class labels (0-indexed)."""
        proba = self.predict_proba(X)
        return np.argmax(proba, axis=1)

    def predict_proba(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """Return class probability matrix of shape (n_samples, NUM_CLASSES)."""
        import torch

        if self._net is None:
            raise RuntimeError("Model has not been trained. Call fit() first.")

        if isinstance(X, pd.DataFrame):
            X_np = X.values.astype(np.float32)
        else:
            X_np = X.astype(np.float32)

        self._net.eval()
        with torch.no_grad():
            X_t = torch.tensor(X_np, dtype=torch.float32, device=self.device)
            logits = self._net(X_t, {})
            proba = torch.softmax(logits, dim=1).cpu().numpy()
        return proba

    def get_params(self) -> Dict[str, Any]:
        """Return model hyper-parameters."""
        return {
            "numeric_dim": self.numeric_dim,
            "hidden_dims": self.hidden_dims,
            "dropout": self.dropout,
            "lr": self.lr,
            "weight_decay": self.weight_decay,
            "epochs": self.epochs,
            "batch_size": self.batch_size,
            "patience": self.patience,
        }
