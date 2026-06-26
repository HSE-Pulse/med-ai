"""Shared training utilities to reduce boilerplate across train.py files.

Consolidates the common setup pattern (sys.path, logging, directory
resolution, feature/target splitting) used by app_08, app_09, app_10, app_14.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Set, Tuple

import pandas as pd


def setup_training_env(module_name: str, parents_depth: int = 4) -> logging.Logger:
    """Common training setup: project root on sys.path, logging, returns logger.

    Parameters
    ----------
    module_name : str
        Logger name (e.g. "bed_management.train").
    parents_depth : int
        How many levels up from the calling train.py to find the project root.
    """
    # Resolve project root from the caller's file location
    import inspect
    caller_file = inspect.stack()[1].filename
    project_root = str(Path(caller_file).resolve().parents[parents_depth - 1])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    return logging.getLogger(module_name)


def get_dirs(module_slug: str) -> Tuple[Path, Path]:
    """Return (DATASET_DIR, MODEL_DIR) from environment or defaults.

    Parameters
    ----------
    module_slug : str
        Module directory name under datasets/ and models/ (e.g. "bed_management").
    """
    base = Path(os.getenv("PROJECT_ROOT", "."))
    dataset_dir = Path(os.getenv("DATASET_DIR", str(base / "datasets" / module_slug)))
    model_dir = Path(os.getenv("MODEL_DIR", str(base / "models" / module_slug)))
    return dataset_dir, model_dir


def prepare_xy(
    df: pd.DataFrame,
    target: str,
    non_feature_cols: Set[str],
) -> Tuple[pd.DataFrame, pd.Series]:
    """Split DataFrame into features (X) and target (y).

    Drops non-feature columns and object-type columns automatically.

    Parameters
    ----------
    df : pd.DataFrame
        Full dataset.
    target : str
        Name of the target column.
    non_feature_cols : set
        Column names to exclude from features (IDs, timestamps, etc.).
    """
    drop_cols = non_feature_cols | {target}
    X = df.drop(columns=[c for c in drop_cols if c in df.columns])
    # Drop any remaining object columns
    X = X.select_dtypes(exclude=["object"])
    y = df[target]
    return X, y
