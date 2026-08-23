"""Sepsis prediction models: LightGBM (tabular baseline) and LSTM with attention.

Both models expose a unified interface:
    - fit(X, y, X_val, y_val)
    - predict(X) -> binary predictions
    - predict_proba(X) -> probabilities
    - save(path) / load(path)
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ============================================================================
# LightGBM baseline -- operates on flattened statistical features
# ============================================================================

class SepsisLGBM:
    """Gradient-boosted tree model for sepsis prediction (tabular features)."""

    def __init__(
        self,
        n_estimators: int = 1000,
        learning_rate: float = 0.05,
        max_depth: int = 7,
        num_leaves: int = 63,
        subsample: float = 0.8,
        colsample_bytree: float = 0.8,
        min_child_samples: int = 50,
        reg_alpha: float = 0.1,
        reg_lambda: float = 1.0,
        scale_pos_weight: Optional[float] = None,
        random_state: int = 42,
    ) -> None:
        self.params = dict(
            n_estimators=n_estimators,
            learning_rate=learning_rate,
            max_depth=max_depth,
            num_leaves=num_leaves,
            subsample=subsample,
            colsample_bytree=colsample_bytree,
            min_child_samples=min_child_samples,
            reg_alpha=reg_alpha,
            reg_lambda=reg_lambda,
            scale_pos_weight=scale_pos_weight,
            random_state=random_state,
        )
        self.model = None
        self.threshold: float = 0.5

    def fit(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        X_val: Optional[np.ndarray] = None,
        y_val: Optional[np.ndarray] = None,
    ) -> "SepsisLGBM":
        import lightgbm as lgb

        # Auto-compute class weight if not provided
        if self.params["scale_pos_weight"] is None:
            n_neg = (y_train == 0).sum()
            n_pos = (y_train == 1).sum()
            self.params["scale_pos_weight"] = n_neg / max(n_pos, 1)
            logger.info("Auto scale_pos_weight: %.2f", self.params["scale_pos_weight"])

        self.model = lgb.LGBMClassifier(
            objective="binary",
            metric="auc",
            verbose=-1,
            **self.params,
        )

        fit_kwargs: dict = {}
        if X_val is not None and y_val is not None:
            fit_kwargs["eval_set"] = [(X_val, y_val)]
            fit_kwargs["callbacks"] = [
                lgb.early_stopping(stopping_rounds=50, verbose=True),
                lgb.log_evaluation(period=100),
            ]

        self.model.fit(X_train, y_train, **fit_kwargs)
        logger.info("LightGBM training complete. Best iteration: %s",
                     getattr(self.model, "best_iteration_", "N/A"))
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Return P(sepsis=1) for each sample."""
        if self.model is None:
            raise RuntimeError("Model not trained. Call fit() first.")
        return self.model.predict_proba(X)[:, 1]

    def predict(self, X: np.ndarray) -> np.ndarray:
        return (self.predict_proba(X) >= self.threshold).astype(int)

    def feature_importance(self) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("Model not trained.")
        return self.model.feature_importances_

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"model": self.model, "threshold": self.threshold, "params": self.params}, f)
        logger.info("LightGBM model saved to %s", path)

    @classmethod
    def load(cls, path: str | Path) -> "SepsisLGBM":
        with open(path, "rb") as f:
            data = pickle.load(f)
        obj = cls()
        obj.model = data["model"]
        obj.threshold = data.get("threshold", 0.5)
        obj.params = data.get("params", {})
        logger.info("LightGBM model loaded from %s", path)
        return obj


# ============================================================================
# LSTM with attention -- operates on sequential windows
# ============================================================================

class SepsisLSTM:
    """Bidirectional LSTM with temporal attention for sepsis prediction.

    Expects input shape (batch, seq_len, n_features).
    """

    def __init__(
        self,
        input_dim: int = 19,
        hidden_dim: int = 64,
        n_layers: int = 2,
        dropout: float = 0.3,
        lr: float = 1e-3,
        weight_decay: float = 1e-4,
        epochs: int = 50,
        batch_size: int = 256,
        patience: int = 8,
        device: Optional[str] = None,
    ) -> None:
        import torch

        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.n_layers = n_layers
        self.dropout = dropout
        self.lr = lr
        self.weight_decay = weight_decay
        self.epochs = epochs
        self.batch_size = batch_size
        self.patience = patience

        if device is None:
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)

        self.net: Optional[_LSTMAttentionNet] = None
        self.threshold: float = 0.5

    def _build_net(self) -> "_LSTMAttentionNet":
        net = _LSTMAttentionNet(
            input_dim=self.input_dim,
            hidden_dim=self.hidden_dim,
            n_layers=self.n_layers,
            dropout=self.dropout,
        ).to(self.device)
        return net

    def fit(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        X_val: Optional[np.ndarray] = None,
        y_val: Optional[np.ndarray] = None,
    ) -> "SepsisLSTM":
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset

        self.input_dim = X_train.shape[2]
        self.net = self._build_net()

        # Class-weighted BCE loss
        n_pos = (y_train == 1).sum()
        n_neg = (y_train == 0).sum()
        pos_weight = torch.tensor([n_neg / max(n_pos, 1)], dtype=torch.float32).to(self.device)

        criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
        optimizer = torch.optim.Adam(
            self.net.parameters(), lr=self.lr, weight_decay=self.weight_decay,
        )
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, mode="min", factor=0.5, patience=3,
        )

        # DataLoaders
        train_ds = TensorDataset(
            torch.from_numpy(X_train).float(),
            torch.from_numpy(y_train).float(),
        )
        train_loader = DataLoader(train_ds, batch_size=self.batch_size, shuffle=True, drop_last=False)

        val_loader = None
        if X_val is not None and y_val is not None:
            val_ds = TensorDataset(
                torch.from_numpy(X_val).float(),
                torch.from_numpy(y_val).float(),
            )
            val_loader = DataLoader(val_ds, batch_size=self.batch_size * 2, shuffle=False)

        best_val_loss = float("inf")
        best_state = None
        wait = 0

        for epoch in range(1, self.epochs + 1):
            # --- train ---
            self.net.train()
            train_loss = 0.0
            for xb, yb in train_loader:
                xb, yb = xb.to(self.device), yb.to(self.device)
                optimizer.zero_grad()
                logits = self.net(xb).squeeze(-1)
                loss = criterion(logits, yb)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.net.parameters(), max_norm=1.0)
                optimizer.step()
                train_loss += loss.item() * len(xb)
            train_loss /= len(train_ds)

            # --- validation ---
            val_loss = train_loss
            if val_loader is not None:
                self.net.eval()
                vl = 0.0
                with torch.no_grad():
                    for xb, yb in val_loader:
                        xb, yb = xb.to(self.device), yb.to(self.device)
                        logits = self.net(xb).squeeze(-1)
                        vl += criterion(logits, yb).item() * len(xb)
                val_loss = vl / len(val_ds)

            scheduler.step(val_loss)

            if epoch % 5 == 0 or epoch == 1:
                logger.info("Epoch %3d  train_loss=%.4f  val_loss=%.4f", epoch, train_loss, val_loss)

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                best_state = {k: v.cpu().clone() for k, v in self.net.state_dict().items()}
                wait = 0
            else:
                wait += 1
                if wait >= self.patience:
                    logger.info("Early stopping at epoch %d", epoch)
                    break

        if best_state is not None:
            self.net.load_state_dict(best_state)
            self.net.to(self.device)

        logger.info("LSTM training complete. Best val loss: %.4f", best_val_loss)
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Return P(sepsis=1) for each sample."""
        import torch
        from torch.utils.data import DataLoader, TensorDataset

        if self.net is None:
            raise RuntimeError("Model not trained. Call fit() first.")

        self.net.eval()
        ds = TensorDataset(torch.from_numpy(X).float())
        loader = DataLoader(ds, batch_size=self.batch_size * 2, shuffle=False)

        probs = []
        with torch.no_grad():
            for (xb,) in loader:
                xb = xb.to(self.device)
                logits = self.net(xb).squeeze(-1)
                probs.append(torch.sigmoid(logits).cpu().numpy())

        return np.concatenate(probs)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return (self.predict_proba(X) >= self.threshold).astype(int)

    def save(self, path: str | Path) -> None:
        import torch
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save({
            "state_dict": self.net.state_dict() if self.net else None,
            "input_dim": self.input_dim,
            "hidden_dim": self.hidden_dim,
            "n_layers": self.n_layers,
            "dropout": self.dropout,
            "threshold": self.threshold,
        }, path)
        logger.info("LSTM model saved to %s", path)

    @classmethod
    def load(cls, path: str | Path, device: Optional[str] = None) -> "SepsisLSTM":
        import torch
        data = torch.load(path, map_location="cpu", weights_only=False)
        obj = cls(
            input_dim=data["input_dim"],
            hidden_dim=data["hidden_dim"],
            n_layers=data["n_layers"],
            dropout=data["dropout"],
            device=device,
        )
        obj.threshold = data.get("threshold", 0.5)
        obj.net = obj._build_net()
        if data["state_dict"] is not None:
            obj.net.load_state_dict(data["state_dict"])
            obj.net.to(obj.device)
        logger.info("LSTM model loaded from %s", path)
        return obj


# ============================================================================
# PyTorch module: Bidirectional LSTM + Temporal Attention
# ============================================================================

import torch
import torch.nn as nn


class _TemporalAttention(nn.Module):
    """Additive attention over LSTM hidden states."""

    def __init__(self, hidden_dim: int) -> None:
        super().__init__()
        self.attn = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim),
            nn.Tanh(),
            nn.Linear(hidden_dim, 1, bias=False),
        )

    def forward(self, lstm_out: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Parameters
        ----------
        lstm_out : (batch, seq_len, hidden_dim)

        Returns
        -------
        context : (batch, hidden_dim)
        weights : (batch, seq_len)
        """
        scores = self.attn(lstm_out).squeeze(-1)       # (batch, seq_len)
        weights = torch.softmax(scores, dim=-1)         # (batch, seq_len)
        context = torch.bmm(weights.unsqueeze(1), lstm_out).squeeze(1)  # (batch, hidden_dim)
        return context, weights


class _LSTMAttentionNet(nn.Module):
    """Bidirectional LSTM followed by temporal attention and classification head."""

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int = 64,
        n_layers: int = 2,
        dropout: float = 0.3,
    ) -> None:
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_dim,
            hidden_size=hidden_dim,
            num_layers=n_layers,
            batch_first=True,
            bidirectional=True,
            dropout=dropout if n_layers > 1 else 0.0,
        )
        self.attention = _TemporalAttention(hidden_dim * 2)
        self.head = nn.Sequential(
            nn.LayerNorm(hidden_dim * 2),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim * 2, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Parameters
        ----------
        x : (batch, seq_len, input_dim)

        Returns
        -------
        logits : (batch, 1)
        """
        lstm_out, _ = self.lstm(x)                # (batch, seq_len, hidden_dim*2)
        context, self._attn_weights = self.attention(lstm_out)  # (batch, hidden_dim*2)
        logits = self.head(context)               # (batch, 1)
        return logits
