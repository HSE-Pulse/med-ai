"""Oncology risk prediction models.

Baseline: XGBoost for 30-day readmission prediction.
Advanced: Transformer on treatment sequence features.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

import numpy as np

try:
    import xgboost as xgb
except ImportError:
    xgb = None

try:
    import torch
    import torch.nn as nn
except ImportError:
    torch = None


FEATURE_COLS = [
    "age", "gender_encoded", "stage_proxy", "drg_mortality",
    "num_procedures", "has_surgery", "has_chemotherapy", "has_radiation",
    "chemo_drug_count", "num_prior_admissions", "days_since_last_admission",
    "total_los_days", "num_comorbidities", "charlson_score",
    "insurance_encoded", "time_to_first_procedure_days",
]


class OncologyRiskXGB:
    """XGBoost model for cancer patient outcome prediction."""

    def __init__(self, target: str = "readmission_30d", n_estimators: int = 200):
        self.target = target
        self.n_estimators = n_estimators
        self.model = None
        self.feature_names = FEATURE_COLS

    def fit(self, X: np.ndarray, y: np.ndarray) -> "OncologyRiskXGB":
        if xgb is None:
            raise ImportError("xgboost required")

        pos_weight = (y == 0).sum() / max((y == 1).sum(), 1)
        self.model = xgb.XGBClassifier(
            n_estimators=self.n_estimators,
            max_depth=5,
            learning_rate=0.1,
            scale_pos_weight=float(pos_weight),
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=0.1,
            reg_lambda=1.0,
            eval_metric="logloss",
            use_label_encoder=False,
            random_state=42,
        )
        self.model.fit(X, y)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("Model not trained")
        return self.model.predict(X)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("Model not trained")
        return self.model.predict_proba(X)[:, 1]

    def feature_importance(self) -> dict[str, float]:
        if self.model is None:
            return {}
        imp = self.model.feature_importances_
        return {name: float(imp[i]) for i, name in enumerate(self.feature_names) if i < len(imp)}

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self.model.save_model(str(path / "model.json"))
        meta = {"target": self.target, "feature_names": self.feature_names}
        (path / "meta.json").write_text(json.dumps(meta, indent=2))

    def load(self, path: Path) -> "OncologyRiskXGB":
        if xgb is None:
            raise ImportError("xgboost required")
        self.model = xgb.XGBClassifier()
        self.model.load_model(str(path / "model.json"))
        meta_file = path / "meta.json"
        if meta_file.exists():
            meta = json.loads(meta_file.read_text())
            self.target = meta.get("target", self.target)
            self.feature_names = meta.get("feature_names", self.feature_names)
        return self


class OncologyTransformer:
    """Simplified transformer model for treatment sequence outcome prediction.

    Uses a small transformer encoder on tabular features treated as a sequence
    of (feature_name, value) pairs with positional encoding.
    """

    def __init__(self, n_features: int = 16, d_model: int = 64, n_heads: int = 4,
                 n_layers: int = 2, dropout: float = 0.2, lr: float = 1e-3,
                 epochs: int = 50, batch_size: int = 256):
        self.n_features = n_features
        self.d_model = d_model
        self.n_heads = n_heads
        self.n_layers = n_layers
        self.dropout = dropout
        self.lr = lr
        self.epochs = epochs
        self.batch_size = batch_size
        self.net = None
        self.device = "cpu"

    def _build_net(self):
        if torch is None:
            raise ImportError("torch required")

        class TabTransformer(nn.Module):
            def __init__(self, n_feat, d_model, n_heads, n_layers, dropout):
                super().__init__()
                self.input_proj = nn.Linear(1, d_model)
                self.pos_embed = nn.Embedding(n_feat, d_model)
                encoder_layer = nn.TransformerEncoderLayer(
                    d_model=d_model, nhead=n_heads, dim_feedforward=d_model * 2,
                    dropout=dropout, batch_first=True,
                )
                self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)
                self.head = nn.Sequential(
                    nn.Linear(d_model, d_model // 2), nn.ReLU(), nn.Dropout(dropout),
                    nn.Linear(d_model // 2, 1),
                )

            def forward(self, x):
                # x: [B, n_feat]
                B, F = x.shape
                x = x.unsqueeze(-1)  # [B, F, 1]
                x = self.input_proj(x)  # [B, F, d_model]
                pos = torch.arange(F, device=x.device).unsqueeze(0).expand(B, -1)
                x = x + self.pos_embed(pos)
                x = self.encoder(x)  # [B, F, d_model]
                x = x.mean(dim=1)  # [B, d_model] - global average pooling
                return self.head(x).squeeze(-1)

        self.net = TabTransformer(self.n_features, self.d_model, self.n_heads,
                                   self.n_layers, self.dropout)
        return self.net

    def fit(self, X: np.ndarray, y: np.ndarray) -> "OncologyTransformer":
        if torch is None:
            raise ImportError("torch required")

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self._build_net()
        self.net.to(self.device)

        X_t = torch.FloatTensor(X).to(self.device)
        y_t = torch.FloatTensor(y).to(self.device)

        pos_weight = torch.tensor([(y == 0).sum() / max((y == 1).sum(), 1)]).to(self.device)
        criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
        optimizer = torch.optim.AdamW(self.net.parameters(), lr=self.lr, weight_decay=1e-4)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=self.epochs)

        self.net.train()
        for epoch in range(self.epochs):
            perm = torch.randperm(len(X_t))
            total_loss = 0
            n_batches = 0
            for i in range(0, len(X_t), self.batch_size):
                idx = perm[i:i + self.batch_size]
                logits = self.net(X_t[idx])
                loss = criterion(logits, y_t[idx])
                optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.net.parameters(), 1.0)
                optimizer.step()
                total_loss += loss.item()
                n_batches += 1
            scheduler.step()
            if (epoch + 1) % 10 == 0:
                print(f"  Epoch {epoch+1}/{self.epochs} loss={total_loss/n_batches:.4f}")

        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if self.net is None:
            raise RuntimeError("Model not trained")
        self.net.eval()
        with torch.no_grad():
            X_t = torch.FloatTensor(X).to(self.device)
            logits = self.net(X_t)
            return torch.sigmoid(logits).cpu().numpy()

    def predict(self, X: np.ndarray) -> np.ndarray:
        return (self.predict_proba(X) >= 0.5).astype(int)

    def save(self, path: Path) -> None:
        if self.net is None:
            return
        path.mkdir(parents=True, exist_ok=True)
        torch.save(self.net.state_dict(), str(path / "model.pt"))
        meta = {"n_features": self.n_features, "d_model": self.d_model,
                "n_heads": self.n_heads, "n_layers": self.n_layers}
        (path / "meta.json").write_text(json.dumps(meta, indent=2))

    def load(self, path: Path) -> "OncologyTransformer":
        if torch is None:
            raise ImportError("torch required")
        meta = json.loads((path / "meta.json").read_text())
        self.n_features = meta["n_features"]
        self.d_model = meta["d_model"]
        self.n_heads = meta["n_heads"]
        self.n_layers = meta["n_layers"]
        self._build_net()
        self.net.load_state_dict(torch.load(str(path / "model.pt"), weights_only=True))
        self.net.eval()
        return self
