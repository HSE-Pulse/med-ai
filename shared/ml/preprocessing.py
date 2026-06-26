"""Data preprocessing utilities for clinical ML pipelines."""

from __future__ import annotations

from typing import List, Literal, Optional, Tuple, Union

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder


def time_series_impute(
    df: pd.DataFrame,
    method: Literal["forward_fill", "backward_fill", "linear", "mean"] = "forward_fill",
) -> pd.DataFrame:
    """Impute missing values in a time-series DataFrame.

    Parameters
    ----------
    df:
        Input DataFrame (numeric columns are imputed; non-numeric left as-is).
    method:
        ``"forward_fill"`` (default), ``"backward_fill"``, ``"linear"``
        interpolation, or column ``"mean"`` fill.

    Returns
    -------
    DataFrame with missing values filled.
    """
    df = df.copy()
    if method == "forward_fill":
        df = df.ffill()
        # Fill any remaining leading NaNs with backward fill
        df = df.bfill()
    elif method == "backward_fill":
        df = df.bfill()
        df = df.ffill()
    elif method == "linear":
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        df[numeric_cols] = df[numeric_cols].interpolate(method="linear", limit_direction="both")
    elif method == "mean":
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        df[numeric_cols] = df[numeric_cols].fillna(df[numeric_cols].mean())
    else:
        raise ValueError(f"Unknown imputation method: {method!r}")
    return df


def normalize_vitals(
    df: pd.DataFrame,
    vital_cols: List[str],
) -> pd.DataFrame:
    """Z-score normalise the specified vital-sign columns in place.

    Parameters
    ----------
    df:
        Input DataFrame.
    vital_cols:
        Column names to normalise (e.g. ``["HR", "SBP", "SpO2"]``).

    Returns
    -------
    A copy of *df* with the requested columns z-score normalised.
    """
    df = df.copy()
    for col in vital_cols:
        if col not in df.columns:
            continue
        mean = df[col].mean()
        std = df[col].std()
        if std == 0 or pd.isna(std):
            df[col] = 0.0
        else:
            df[col] = (df[col] - mean) / std
    return df


def create_windows(
    df: pd.DataFrame,
    window_size: int,
    step: int = 1,
) -> np.ndarray:
    """Create sliding windows over a DataFrame's numeric values.

    Parameters
    ----------
    df:
        Input DataFrame (uses all numeric columns).
    window_size:
        Number of rows per window.
    step:
        Stride between successive windows.

    Returns
    -------
    3-D NumPy array of shape ``(n_windows, window_size, n_features)``.
    """
    values = df.select_dtypes(include=[np.number]).values
    n_rows, n_features = values.shape
    if n_rows < window_size:
        raise ValueError(
            f"DataFrame has {n_rows} rows but window_size={window_size}"
        )
    windows = []
    for start in range(0, n_rows - window_size + 1, step):
        windows.append(values[start : start + window_size])
    return np.array(windows)


def encode_categorical(
    df: pd.DataFrame,
    cols: List[str],
) -> Tuple[pd.DataFrame, dict[str, LabelEncoder]]:
    """Label-encode the specified categorical columns.

    Parameters
    ----------
    df:
        Input DataFrame.
    cols:
        Columns to encode.

    Returns
    -------
    A tuple of (encoded DataFrame copy, dict mapping column name to its
    fitted ``LabelEncoder``).
    """
    df = df.copy()
    encoders: dict[str, LabelEncoder] = {}
    for col in cols:
        if col not in df.columns:
            continue
        le = LabelEncoder()
        # Treat NaN as a string category so the encoder doesn't crash
        mask = df[col].isna()
        df[col] = df[col].fillna("__MISSING__")
        df[col] = le.fit_transform(df[col].astype(str))
        encoders[col] = le
    return df, encoders


def handle_class_imbalance(
    X: Union[np.ndarray, pd.DataFrame],
    y: Union[np.ndarray, pd.Series],
    method: Literal["smote", "undersample"] = "smote",
    random_state: int = 42,
) -> Tuple[np.ndarray, np.ndarray]:
    """Re-balance classes via SMOTE or random under-sampling.

    Parameters
    ----------
    X:
        Feature matrix.
    y:
        Target vector.
    method:
        ``"smote"`` (default) uses ``imblearn.over_sampling.SMOTE``;
        ``"undersample"`` uses ``imblearn.under_sampling.RandomUnderSampler``.
    random_state:
        Reproducibility seed.

    Returns
    -------
    Resampled ``(X, y)`` as NumPy arrays.
    """
    if isinstance(X, pd.DataFrame):
        X = X.values
    if isinstance(y, pd.Series):
        y = y.values

    if method == "smote":
        try:
            from imblearn.over_sampling import SMOTE
        except ImportError as exc:
            raise ImportError(
                "imbalanced-learn is required for SMOTE. "
                "Install it with: pip install imbalanced-learn"
            ) from exc
        sampler = SMOTE(random_state=random_state)
    elif method == "undersample":
        try:
            from imblearn.under_sampling import RandomUnderSampler
        except ImportError as exc:
            raise ImportError(
                "imbalanced-learn is required for undersampling. "
                "Install it with: pip install imbalanced-learn"
            ) from exc
        sampler = RandomUnderSampler(random_state=random_state)
    else:
        raise ValueError(f"Unknown method: {method!r}")

    X_res, y_res = sampler.fit_resample(X, y)
    return X_res, y_res


def train_val_test_split(
    df: pd.DataFrame,
    test_size: float = 0.15,
    val_size: float = 0.15,
    stratify_col: Optional[str] = None,
    random_state: int = 42,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split a DataFrame into train / validation / test sets.

    Parameters
    ----------
    df:
        Input DataFrame.
    test_size:
        Fraction held out for test.
    val_size:
        Fraction (of the *original* data) held out for validation.
    stratify_col:
        Optional column name used for stratified splitting.
    random_state:
        Reproducibility seed.

    Returns
    -------
    ``(train_df, val_df, test_df)``
    """
    stratify = df[stratify_col] if stratify_col else None

    # First split: separate test set
    remaining, test_df = train_test_split(
        df,
        test_size=test_size,
        stratify=stratify,
        random_state=random_state,
    )

    # Second split: separate validation from remaining
    # Adjust val_size relative to remaining data
    relative_val = val_size / (1.0 - test_size)
    stratify_remaining = remaining[stratify_col] if stratify_col else None

    train_df, val_df = train_test_split(
        remaining,
        test_size=relative_val,
        stratify=stratify_remaining,
        random_state=random_state,
    )

    return (
        train_df.reset_index(drop=True),
        val_df.reset_index(drop=True),
        test_df.reset_index(drop=True),
    )
